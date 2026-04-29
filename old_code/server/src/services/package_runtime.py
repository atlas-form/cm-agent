from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
import platform
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from src.config import PROJECT_ROOT
from src.core.rbac import require_workspace_permission
from src.database import get_db

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None

logger = logging.getLogger(__name__)

PackageType = Literal["domain", "role", "skill", "capability"]
_PACKAGE_TYPES: tuple[PackageType, ...] = ("domain", "role", "skill", "capability")
_APP_VERSION = "4.0.0"
_CURRENT_OS = {
    "windows": "windows",
    "linux": "linux",
    "darwin": "macos",
}.get(platform.system().strip().lower(), "linux")

_PACKAGE_TYPE_ALIASES: Dict[str, PackageType] = {
    "domain": "domain",
    "domains": "domain",
    "role": "role",
    "roles": "role",
    "skill": "skill",
    "skills": "skill",
    "capability": "capability",
    "capabilities": "capability",
}


class PackageValidationError(ValueError):
    """激活前校验失败。"""

    def __init__(
        self,
        package_type: PackageType,
        package_id: str,
        errors: List[str],
        warnings: Optional[List[str]] = None,
    ) -> None:
        self.package_type = package_type
        self.package_id = package_id
        self.errors = list(errors)
        self.warnings = list(warnings or [])
        super().__init__(f"package activation blocked: {package_type}:{package_id}")

    def as_dict(self) -> Dict[str, Any]:
        return {
            "package_type": self.package_type,
            "package_id": self.package_id,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def _root_for(package_type: PackageType) -> Path:
    server_root = PROJECT_ROOT
    if package_type == "capability":
        return (server_root.parent / "client" / "src" / "capabilities").resolve()
    return (server_root / "packages" / f"{package_type}s").resolve()


def _manifest_paths(package_type: PackageType) -> List[Path]:
    root = _root_for(package_type)
    if not root.exists():
        return []

    if package_type == "capability":
        paths = list(root.rglob("manifest.json"))
    else:
        paths = list(root.rglob("manifest.yaml")) + list(root.rglob("manifest.yml")) + list(root.rglob("manifest.json"))

    return sorted({p.resolve() for p in paths})


def _read_manifest(path: Path) -> Optional[Dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Read manifest failed: %s (%s)", path, exc)
        return None

    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except Exception:
        pass

    if yaml is None:
        logger.warning("Manifest is not JSON and PyYAML unavailable: %s", path)
        return None

    try:
        data = yaml.safe_load(raw)
    except Exception as exc:
        logger.warning("YAML parse failed: %s (%s)", path, exc)
        return None

    if not isinstance(data, dict):
        logger.warning("Manifest should be mapping: %s", path)
        return None
    return data


def _write_manifest(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(data, ensure_ascii=False, indent=2)
    path.write_text(body + "\n", encoding="utf-8")


def _normalize_status(value: Any, enabled: Any = True) -> str:
    if enabled in {False, 0, "0", "false", "False", "off", "OFF"}:
        return "disabled"

    status = str(value or "active").strip().lower()
    if status in {"active", "enabled", "on", "true", "1"}:
        return "active"
    return "disabled"


def _normalize_string_list(value: Any) -> List[str]:
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, str):
        raw_items = [x.strip() for x in value.split(",")]
    else:
        raw_items = []

    result: List[str] = []
    seen: set[str] = set()
    for item in raw_items:
        s = str(item or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        result.append(s)
    return result


_SKILL_ROLE_ALIASES: Dict[str, str] = {
    "ops": "ops",
    "data": "data",
    "data_analysis": "data",
    "analysis": "data",
    "customer": "service",
    "customer_service": "service",
    "service": "service",
    "creative": "creative",
    "accounting": "accounting",
    "finance": "accounting",
    "engineering": "engineering",
    "web": "web",
    "seo": "web",
    "design": "design",
    # 兼容现有核心技能命名，不直接映射到业务岗位
    "coordination": "coordination",
    "search": "search",
}


def _runtime_role_aliases() -> Dict[str, str]:
    alias_map: Dict[str, str] = dict(_SKILL_ROLE_ALIASES)

    try:
        from src.core.role_router import build_runtime_role_context

        role_ctx = build_runtime_role_context()
        runtime_roles = [
            str(x).strip().lower()
            for x in (role_ctx.get("runtime_roles") or [])
            if str(x).strip()
        ]
        role_packages = role_ctx.get("role_packages") if isinstance(role_ctx.get("role_packages"), dict) else {}

        for runtime_role in runtime_roles:
            alias_map[runtime_role] = runtime_role
            alias_map[runtime_role.replace("-", "_")] = runtime_role

        for runtime_role, candidates in role_packages.items():
            runtime = str(runtime_role or "").strip().lower()
            if not runtime:
                continue

            if not isinstance(candidates, list):
                continue

            for pkg in candidates:
                if not isinstance(pkg, dict):
                    continue

                role_id = str(pkg.get("id") or "").strip().lower()
                role_name = str(pkg.get("name") or "").strip().lower()

                if role_id:
                    alias_map[role_id] = runtime
                    if role_id.startswith("role."):
                        alias_map[role_id.removeprefix("role.")] = runtime
                    if "." in role_id:
                        alias_map[role_id.split(".")[-1]] = runtime

                if role_name:
                    alias_map[role_name] = runtime
                    for token in re.split(r"[\s._/-]+", role_name):
                        token = str(token).strip().lower()
                        if token:
                            alias_map[token] = runtime
    except Exception:
        pass

    return alias_map


def _infer_runtime_roles_from_skill_id(skill_id: str) -> List[str]:
    sid = str(skill_id or "").strip().lower()
    if not sid:
        return []

    base = sid.removeprefix("builtin.")
    alias_map = _runtime_role_aliases()

    token_candidates: List[str] = [base]
    token_candidates.extend(re.split(r"[._/-]+", base))

    roles: List[str] = []
    for token in token_candidates:
        key = str(token or "").strip().lower()
        if not key:
            continue
        mapped = alias_map.get(key)
        if mapped and mapped not in roles:
            roles.append(mapped)

    return roles


def _build_package_item(package_type: PackageType, path: Path, data: Dict[str, Any]) -> Dict[str, Any]:
    pkg_id = str(data.get("id") or f"{package_type}.{path.parent.name}").strip()
    name = str(data.get("name") or data.get("label") or pkg_id).strip()
    version = str(data.get("version") or "0.1.0").strip()
    status = _normalize_status(data.get("status", "active"), data.get("enabled", True))

    item: Dict[str, Any] = {
        "type": package_type,
        "id": pkg_id,
        "name": name,
        "version": version,
        "status": status,
        "manifest_path": str(path),
        "dependencies": data.get("dependencies") if isinstance(data.get("dependencies"), dict) else {},
        "compatibility": data.get("compatibility") if isinstance(data.get("compatibility"), dict) else {},
    }

    if package_type == "skill":
        ep = data.get("entrypoint") if isinstance(data.get("entrypoint"), dict) else {}
        item["entrypoint"] = {
            "module": str(ep.get("module") or "").strip(),
            "attr": str(ep.get("attr") or "ALL_SKILLS").strip() or "ALL_SKILLS",
        }
        provided_roles = _normalize_string_list(data.get("provided_roles"))
        if not provided_roles:
            provided_roles = _infer_runtime_roles_from_skill_id(pkg_id)
        item["provided_roles"] = provided_roles
        item["provided_capabilities"] = _normalize_string_list(data.get("provided_capabilities"))
        item["core"] = bool(data.get("core", False))

    if package_type == "capability":
        item["route"] = str(data.get("route") or "").strip()
        item["label"] = str(data.get("label") or name).strip()
        item["section"] = str(data.get("section") or "tools").strip().lower() or "tools"
        try:
            item["order"] = int(data.get("order", 999))
        except Exception:
            item["order"] = 999

    if package_type == "domain":
        kws = data.get("keywords", [])
        item["keywords"] = [str(x).strip() for x in kws if str(x).strip()] if isinstance(kws, list) else []
        item["preferred_roles"] = [
            str(x).strip() for x in data.get("preferred_roles", []) if str(x).strip()
        ] if isinstance(data.get("preferred_roles"), list) else []
        item["prompt_profile"] = str(data.get("prompt_profile") or "").strip()

    if package_type == "role":
        item["runtime_role"] = str(data.get("runtime_role") or "ops").strip() or "ops"
        kws = data.get("keywords", [])
        item["keywords"] = [str(x).strip() for x in kws if str(x).strip()] if isinstance(kws, list) else []
        item["domains"] = [
            str(x).strip() for x in data.get("domains", []) if str(x).strip()
        ] if isinstance(data.get("domains"), list) else []
        item["required_capabilities"] = _normalize_string_list(data.get("required_capabilities"))
        item["optional_capabilities"] = _normalize_string_list(data.get("optional_capabilities"))

    return item


def _scan_packages(package_type: PackageType, include_disabled: bool = False) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for path in _manifest_paths(package_type):
        data = _read_manifest(path)
        if not data:
            continue
        item = _build_package_item(package_type, path, data)
        if not include_disabled and item["status"] != "active":
            continue
        items.append(item)

    if package_type == "capability":
        items.sort(key=lambda x: (str(x.get("section", "tools")), int(x.get("order", 999)), str(x.get("id", ""))))
    else:
        items.sort(key=lambda x: str(x.get("id", "")))
    return items


def build_package_catalog(include_disabled: bool = False) -> Dict[str, Any]:
    all_items: List[Dict[str, Any]] = []
    sections: List[Dict[str, Any]] = []

    for package_type in _PACKAGE_TYPES:
        items = _scan_packages(package_type, include_disabled=include_disabled)
        all_items.extend(items)

        active_count = sum(1 for x in items if x.get("status") == "active")
        disabled_count = sum(1 for x in items if x.get("status") != "active")
        sections.append(
            {
                "type": package_type,
                "items": items,
                "count": len(items),
                "active_count": active_count,
                "disabled_count": disabled_count,
            }
        )

    body = json.dumps(all_items, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    revision = hashlib.sha1(body.encode("utf-8")).hexdigest()[:12]

    return {
        "revision": revision,
        "count": len(all_items),
        "sections": sections,
        "types": list(_PACKAGE_TYPES),
    }


def _find_package_manifest(package_type: PackageType, package_id: str) -> tuple[Path, Dict[str, Any], Dict[str, Any]]:
    package_id = package_id.strip()
    if not package_id:
        raise KeyError("package_id is empty")

    for path in _manifest_paths(package_type):
        data = _read_manifest(path)
        if not data:
            continue
        item = _build_package_item(package_type, path, data)
        if str(item.get("id")) == package_id:
            return path, data, item

    raise KeyError(f"package '{package_type}:{package_id}' not found")


def _normalize_package_type(value: Any) -> Optional[PackageType]:
    key = str(value or "").strip().lower().replace("_", "-").replace(" ", "")
    key = key.replace("-", "")
    if not key:
        return None

    compact_map = {
        "domain": "domain",
        "domains": "domain",
        "role": "role",
        "roles": "role",
        "skill": "skill",
        "skills": "skill",
        "capability": "capability",
        "capabilities": "capability",
    }
    mapped = compact_map.get(key)
    if mapped in _PACKAGE_TYPES:
        return mapped  # type: ignore[return-value]
    return None


def _is_likely_package_id(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if ":" in text or "/" in text:
        return True
    if text.count(".") >= 1:
        return True
    if text.startswith(("cap.", "role.", "domain.", "builtin.")):
        return True
    return False


def _catalog_index(include_disabled: bool = True) -> Dict[tuple[PackageType, str], Dict[str, Any]]:
    index: Dict[tuple[PackageType, str], Dict[str, Any]] = {}
    for package_type in _PACKAGE_TYPES:
        for item in _scan_packages(package_type, include_disabled=include_disabled):
            pid = str(item.get("id") or "").strip()
            if pid:
                index[(package_type, pid)] = item
    return index


def _parse_dependency_token(token: str, default_type: Optional[PackageType]) -> tuple[Optional[PackageType], str]:
    raw = str(token or "").strip()
    if not raw:
        return default_type, ""

    for sep in (":", "/"):
        if sep in raw:
            left, right = raw.split(sep, 1)
            dep_type = _normalize_package_type(left)
            if dep_type:
                return dep_type, right.strip()

    return default_type, raw


def _iter_dependency_items(manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    deps = manifest.get("dependencies")
    if not isinstance(deps, dict):
        return []

    parsed: List[Dict[str, Any]] = []

    def add_item(dep_type: Optional[PackageType], dep_id: str, required: bool, source: str) -> None:
        dep_id = str(dep_id or "").strip()
        if not dep_type or not dep_id:
            return
        parsed.append(
            {
                "type": dep_type,
                "id": dep_id,
                "required": bool(required),
                "source": source,
            }
        )

    # 显式 packages / required_packages / optional_packages
    for key in ("packages", "required_packages", "optional_packages"):
        value = deps.get(key)
        if isinstance(value, list):
            default_required = key != "optional_packages"
            for entry in value:
                if isinstance(entry, str):
                    dep_type, dep_id = _parse_dependency_token(entry, None)
                    add_item(dep_type, dep_id, default_required, f"{key}:{entry}")
                elif isinstance(entry, dict):
                    dep_type = _normalize_package_type(entry.get("type") or entry.get("package_type"))
                    dep_id = str(entry.get("id") or entry.get("package_id") or "").strip()
                    required = bool(entry.get("required", default_required))
                    add_item(dep_type, dep_id, required, f"{key}:{dep_type}:{dep_id}")

    # 兼容 domain/role/skill/capability（单复数）结构
    for key, value in deps.items():
        dep_type = _PACKAGE_TYPE_ALIASES.get(str(key or "").strip().lower())
        if dep_type is None:
            continue
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, str):
                    _, dep_id = _parse_dependency_token(entry, dep_type)
                    add_item(dep_type, dep_id, True, f"{key}:{entry}")
        elif isinstance(value, str):
            _, dep_id = _parse_dependency_token(value, dep_type)
            add_item(dep_type, dep_id, True, f"{key}:{value}")

    return parsed


def _resolve_dependency(
    dep_type: PackageType,
    dep_id: str,
    index: Dict[tuple[PackageType, str], Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    dep_id = dep_id.strip()
    direct = index.get((dep_type, dep_id))
    if direct:
        return direct

    if ":" in dep_id:
        _, right = dep_id.split(":", 1)
        right = right.strip()
        direct = index.get((dep_type, right))
        if direct:
            return direct

    # 回退匹配：允许传短ID（例如 cap.alerts / alerts）
    for (pkg_type, pkg_id), item in index.items():
        if pkg_type != dep_type:
            continue
        if pkg_id == dep_id:
            return item
        if dep_id and pkg_id.endswith(f".{dep_id}"):
            return item

    return None


def _parse_version_tuple(value: str) -> tuple[int, ...]:
    nums = [int(x) for x in re.findall(r"\d+", str(value or ""))]
    if not nums:
        return (0,)
    return tuple(nums[:3])


def _compare_tuple(a: tuple[int, ...], b: tuple[int, ...]) -> int:
    max_len = max(len(a), len(b))
    a2 = list(a) + [0] * (max_len - len(a))
    b2 = list(b) + [0] * (max_len - len(b))
    if a2 > b2:
        return 1
    if a2 < b2:
        return -1
    return 0


def _satisfy_version_spec(current: str, spec: str) -> bool:
    current_tuple = _parse_version_tuple(current)
    clauses = [c.strip() for c in str(spec or "").split(",") if c.strip()]
    if not clauses:
        return True

    for clause in clauses:
        match = re.match(r"^(>=|<=|==|=|>|<)?\s*([0-9]+(?:\.[0-9]+){0,2})$", clause)
        if not match:
            raise ValueError(f"invalid version clause: {clause}")
        op = match.group(1) or "=="
        target = _parse_version_tuple(match.group(2))
        comp = _compare_tuple(current_tuple, target)

        if op in {"=", "=="} and comp != 0:
            return False
        if op == ">=" and comp < 0:
            return False
        if op == ">" and comp <= 0:
            return False
        if op == "<=" and comp > 0:
            return False
        if op == "<" and comp >= 0:
            return False

    return True


def _normalize_os_token(value: str) -> str:
    token = str(value or "").strip().lower()
    mapping = {
        "win": "windows",
        "win32": "windows",
        "windows": "windows",
        "darwin": "macos",
        "mac": "macos",
        "macos": "macos",
        "linux": "linux",
    }
    return mapping.get(token, token)


def _validate_compatibility(item: Dict[str, Any], errors: List[str], warnings: List[str]) -> None:
    compatibility = item.get("compatibility")
    if not isinstance(compatibility, dict):
        return

    requires = compatibility.get("requires") if isinstance(compatibility.get("requires"), dict) else {}
    excludes = compatibility.get("excludes") if isinstance(compatibility.get("excludes"), dict) else {}

    os_allow = compatibility.get("os")
    if os_allow is None:
        os_allow = requires.get("os")

    if isinstance(os_allow, str):
        allow_set = {_normalize_os_token(x) for x in os_allow.split(",") if str(x).strip()}
    elif isinstance(os_allow, list):
        allow_set = {_normalize_os_token(str(x)) for x in os_allow if str(x).strip()}
    else:
        allow_set = set()

    if allow_set and _CURRENT_OS not in allow_set:
        errors.append(f"当前系统 '{_CURRENT_OS}' 不在兼容列表 {sorted(allow_set)}")

    os_block = compatibility.get("disallow_os")
    if os_block is None:
        os_block = excludes.get("os")

    if isinstance(os_block, str):
        blocked = {_normalize_os_token(x) for x in os_block.split(",") if str(x).strip()}
    elif isinstance(os_block, list):
        blocked = {_normalize_os_token(str(x)) for x in os_block if str(x).strip()}
    else:
        blocked = set()

    if blocked and _CURRENT_OS in blocked:
        errors.append(f"当前系统 '{_CURRENT_OS}' 被兼容策略禁用")

    python_spec = (
        compatibility.get("python")
        or compatibility.get("python_version")
        or requires.get("python")
        or compatibility.get("min_python")
        or ""
    )
    python_spec = str(python_spec).strip()
    if python_spec:
        current_py = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        try:
            if not _satisfy_version_spec(current_py, python_spec):
                errors.append(f"当前 Python {current_py} 不满足要求 {python_spec}")
        except ValueError:
            warnings.append(f"Python 版本约束解析失败: {python_spec}")

    app_spec = (
        compatibility.get("app")
        or compatibility.get("app_version")
        or requires.get("app")
        or compatibility.get("min_app_version")
        or ""
    )
    app_spec = str(app_spec).strip()
    if app_spec:
        try:
            if not _satisfy_version_spec(_APP_VERSION, app_spec):
                errors.append(f"当前应用版本 {_APP_VERSION} 不满足要求 {app_spec}")
        except ValueError:
            warnings.append(f"应用版本约束解析失败: {app_spec}")


def validate_package_activation(package_type: PackageType, package_id: str) -> Dict[str, Any]:
    if package_type not in _PACKAGE_TYPES:
        raise ValueError(f"unsupported package_type: {package_type}")

    path, manifest, item = _find_package_manifest(package_type, package_id)

    errors: List[str] = []
    warnings: List[str] = []
    resolved_dependencies: List[Dict[str, Any]] = []

    index = _catalog_index(include_disabled=True)
    dependency_items = _iter_dependency_items(manifest)

    for dep in dependency_items:
        dep_type = dep["type"]
        dep_id = str(dep["id"]).strip()
        required = bool(dep.get("required", True))
        source = str(dep.get("source") or "dependency")

        matched = _resolve_dependency(dep_type, dep_id, index)
        if not matched:
            # 软依赖（例如 role->skill 的函数级名称）给 warning，不阻塞
            if required and _is_likely_package_id(dep_id):
                errors.append(f"缺少依赖包: {dep_type}:{dep_id} (from {source})")
            else:
                warnings.append(f"依赖未映射到包注册表，按软依赖处理: {dep_type}:{dep_id} (from {source})")
            continue

        dep_status = str(matched.get("status") or "disabled")
        resolved_dependencies.append(
            {
                "type": dep_type,
                "id": str(matched.get("id") or dep_id),
                "name": str(matched.get("name") or dep_id),
                "status": dep_status,
                "required": required,
            }
        )

        if required and dep_status != "active":
            errors.append(f"依赖包未激活: {dep_type}:{dep_id} (current={dep_status})")

    _validate_compatibility(item, errors, warnings)

    return {
        "ok": len(errors) == 0,
        "package": {
            "type": package_type,
            "id": item.get("id"),
            "name": item.get("name"),
            "version": item.get("version"),
            "status": item.get("status"),
            "manifest_path": str(path),
        },
        "errors": errors,
        "warnings": warnings,
        "resolved_dependencies": resolved_dependencies,
        "dependency_count": len(dependency_items),
    }


async def _persist_package_state(
    package_type: PackageType,
    package_id: str,
    name: str,
    version: str,
    status: str,
    manifest_path: str,
    changed_by: Optional[int],
    reason: str,
    before_status: str,
) -> None:
    db = await get_db()

    await db.execute(
        """
        INSERT INTO package_registry
            (package_type, package_id, package_name, version, status, manifest_path, updated_by, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(package_type, package_id) DO UPDATE SET
            package_name=excluded.package_name,
            version=excluded.version,
            status=excluded.status,
            manifest_path=excluded.manifest_path,
            updated_by=excluded.updated_by,
            updated_at=CURRENT_TIMESTAMP
        """,
        (package_type, package_id, name, version, status, manifest_path, changed_by),
    )

    await db.execute(
        """
        INSERT INTO package_state_history
            (package_type, package_id, from_status, to_status, reason, changed_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (package_type, package_id, before_status, status, reason[:300], changed_by),
    )
    await db.commit()


async def set_package_status(
    package_type: PackageType,
    package_id: str,
    target_status: Literal["active", "disabled"],
    *,
    changed_by: Optional[int] = None,
    reason: str = "",
) -> Dict[str, Any]:
    if package_type not in _PACKAGE_TYPES:
        raise ValueError(f"unsupported package_type: {package_type}")
    if target_status not in {"active", "disabled"}:
        raise ValueError(f"unsupported target_status: {target_status}")

    if target_status == "active":
        validation = validate_package_activation(package_type, package_id)
        if not validation.get("ok"):
            raise PackageValidationError(
                package_type=package_type,
                package_id=package_id,
                errors=[str(x) for x in validation.get("errors") or []],
                warnings=[str(x) for x in validation.get("warnings") or []],
            )

    path, data, item = _find_package_manifest(package_type, package_id)

    before_status = str(item.get("status") or "active")
    data["status"] = target_status
    data["enabled"] = target_status == "active"
    _write_manifest(path, data)

    _, _, updated = _find_package_manifest(package_type, package_id)

    await _persist_package_state(
        package_type=package_type,
        package_id=package_id,
        name=str(updated.get("name") or package_id),
        version=str(updated.get("version") or "0.1.0"),
        status=str(updated.get("status") or target_status),
        manifest_path=str(path),
        changed_by=changed_by,
        reason=reason or f"set_{target_status}",
        before_status=before_status,
    )

    if package_type == "skill":
        try:
            from src.skills.registry import get_registry

            get_registry().reload()
        except Exception as exc:
            logger.warning("Skill registry reload failed after package switch: %s", exc)

    return updated


async def rollback_package_status(
    package_type: PackageType,
    package_id: str,
    *,
    changed_by: Optional[int] = None,
    reason: str = "rollback",
) -> Dict[str, Any]:
    if package_type not in _PACKAGE_TYPES:
        raise ValueError(f"unsupported package_type: {package_type}")

    db = await get_db()
    row = await db.execute_fetchone(
        """
        SELECT id, from_status, to_status
        FROM package_state_history
        WHERE package_type = ? AND package_id = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (package_type, package_id),
    )
    if not row:
        raise KeyError(f"no history for package '{package_type}:{package_id}'")

    target = str(row["from_status"] or "active").strip().lower()
    if target not in {"active", "disabled"}:
        target = "active"

    return await set_package_status(
        package_type,
        package_id,
        target,  # type: ignore[arg-type]
        changed_by=changed_by,
        reason=f"{reason}:history#{row['id']}",
    )


async def get_package_history(package_type: PackageType, package_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    if package_type not in _PACKAGE_TYPES:
        raise ValueError(f"unsupported package_type: {package_type}")

    db = await get_db()
    rows = await db.execute_fetchall(
        """
        SELECT id, package_type, package_id, from_status, to_status, reason, changed_by, created_at
        FROM package_state_history
        WHERE package_type = ? AND package_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (package_type, package_id, max(1, min(limit, 200))),
    )
    return [dict(r) for r in rows]


class RunPackageLockDriftError(RuntimeError):
    """执行编排启动前，检测到包版本/状态漂移。"""

    def __init__(self, lock_state: Dict[str, Any]) -> None:
        self.lock_state = lock_state
        run_id = lock_state.get("run_id")
        super().__init__(f"run package lock drift detected: run_id={run_id}")

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.lock_state)


def _stable_lock_revision(entries: List[Dict[str, Any]]) -> str:
    stable_body = json.dumps(
        [
            {
                "package_type": str(x.get("package_type") or ""),
                "package_id": str(x.get("package_id") or ""),
                "locked_version": str(x.get("locked_version") or ""),
                "locked_status": str(x.get("locked_status") or ""),
                "required": 1 if bool(x.get("required", True)) else 0,
            }
            for x in sorted(entries, key=lambda i: (str(i.get("package_type")), str(i.get("package_id"))))
        ],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha1(stable_body.encode("utf-8")).hexdigest()[:12]


def _runtime_role_package_index(include_disabled: bool = True) -> Dict[str, List[Dict[str, Any]]]:
    mapping: Dict[str, List[Dict[str, Any]]] = {}
    for item in _scan_packages("role", include_disabled=include_disabled):
        runtime_role = str(item.get("runtime_role") or "").strip()
        if not runtime_role:
            continue
        mapping.setdefault(runtime_role, []).append(item)

    for runtime_role, candidates in mapping.items():
        candidates.sort(key=lambda x: (0 if str(x.get("status") or "") == "active" else 1, str(x.get("id") or "")))
        mapping[runtime_role] = candidates

    return mapping


def _effective_role_package(runtime_role: str) -> Optional[Dict[str, Any]]:
    candidates = _runtime_role_package_index(include_disabled=True).get(runtime_role, [])
    if not candidates:
        return None
    for item in candidates:
        if str(item.get("status") or "") == "active":
            return item
    return candidates[0]


def _skill_supports_runtime_roles(skill_item: Dict[str, Any], runtime_roles: List[str]) -> bool:
    provided = _normalize_string_list(skill_item.get("provided_roles"))
    if not provided:
        provided = _infer_runtime_roles_from_skill_id(str(skill_item.get("id") or ""))
    return any(role in provided for role in runtime_roles)


def _capability_matches(skill_item: Dict[str, Any], capability_id: str) -> bool:
    provided_caps = _normalize_string_list(skill_item.get("provided_capabilities"))
    return capability_id in provided_caps


def build_role_capability_matrix(runtime_roles: List[str], include_disabled: bool = True) -> Dict[str, Any]:
    normalized_roles: List[str] = []
    for role in runtime_roles:
        r = str(role or "").strip()
        if r and r not in normalized_roles:
            normalized_roles.append(r)

    skill_items = _scan_packages("skill", include_disabled=include_disabled)
    rows: List[Dict[str, Any]] = []
    required_total = 0
    required_satisfied = 0

    for runtime_role in normalized_roles:
        role_pkg = _effective_role_package(runtime_role)
        if not role_pkg:
            rows.append(
                {
                    "runtime_role": runtime_role,
                    "ready": False,
                    "coverage": 0.0,
                    "missing_reason": "role package not found",
                    "role_package": {},
                    "required_capabilities": [],
                    "optional_capabilities": [],
                    "role_skills": [],
                }
            )
            continue

        role_skills: List[Dict[str, Any]] = []
        for skill in skill_items:
            if _skill_supports_runtime_roles(skill, [runtime_role]):
                role_skills.append(
                    {
                        "id": str(skill.get("id") or ""),
                        "name": str(skill.get("name") or ""),
                        "status": str(skill.get("status") or "disabled"),
                    }
                )

        role_skills.sort(key=lambda x: (0 if x.get("status") == "active" else 1, x.get("id", "")))

        required_caps = _normalize_string_list(role_pkg.get("required_capabilities"))
        optional_caps = _normalize_string_list(role_pkg.get("optional_capabilities"))

        req_rows: List[Dict[str, Any]] = []
        for cap in required_caps:
            matched_skills = [
                {
                    "id": str(skill.get("id") or ""),
                    "name": str(skill.get("name") or ""),
                    "status": str(skill.get("status") or "disabled"),
                }
                for skill in skill_items
                if _capability_matches(skill, cap)
            ]
            matched_active = [s for s in matched_skills if s.get("status") == "active"]
            satisfied = len(matched_active) > 0
            required_total += 1
            if satisfied:
                required_satisfied += 1
            req_rows.append(
                {
                    "id": cap,
                    "satisfied": satisfied,
                    "matched_skills": matched_skills,
                    "active_skills": matched_active,
                }
            )

        opt_rows: List[Dict[str, Any]] = []
        for cap in optional_caps:
            matched_skills = [
                {
                    "id": str(skill.get("id") or ""),
                    "name": str(skill.get("name") or ""),
                    "status": str(skill.get("status") or "disabled"),
                }
                for skill in skill_items
                if _capability_matches(skill, cap)
            ]
            matched_active = [s for s in matched_skills if s.get("status") == "active"]
            opt_rows.append(
                {
                    "id": cap,
                    "satisfied": len(matched_active) > 0,
                    "matched_skills": matched_skills,
                    "active_skills": matched_active,
                }
            )

        ready = bool(str(role_pkg.get("status") or "") == "active") and all(x.get("satisfied") for x in req_rows)
        coverage = 1.0 if not req_rows else sum(1 for x in req_rows if x.get("satisfied")) / len(req_rows)

        rows.append(
            {
                "runtime_role": runtime_role,
                "ready": ready,
                "coverage": round(coverage, 3),
                "role_package": {
                    "id": str(role_pkg.get("id") or ""),
                    "name": str(role_pkg.get("name") or ""),
                    "status": str(role_pkg.get("status") or "disabled"),
                    "version": str(role_pkg.get("version") or "0.1.0"),
                },
                "required_capabilities": req_rows,
                "optional_capabilities": opt_rows,
                "role_skills": role_skills,
            }
        )

    summary = {
        "required_total": required_total,
        "required_satisfied": required_satisfied,
        "required_coverage": round((required_satisfied / required_total), 3) if required_total else 1.0,
        "ready_roles": [x.get("runtime_role") for x in rows if x.get("ready")],
        "unready_roles": [x.get("runtime_role") for x in rows if not x.get("ready")],
    }

    revision = hashlib.sha1(
        json.dumps({"roles": rows, "summary": summary}, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]

    return {
        "runtime_roles": normalized_roles,
        "roles": rows,
        "summary": summary,
        "revision": revision,
    }


def build_execution_package_lock(
    *,
    goal: str,
    runtime_roles: List[str],
    domain_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    生成执行编排的包版本锁快照。

    锁定范围：
    - 领域包（domain）
    - 编排涉及的岗位包（role，按 runtime_role 映射）
    - 当前激活的技能包（skill baseline）
    """
    from src.core.domain_router import classify_domain

    normalized_roles: List[str] = []
    for role in runtime_roles:
        r = str(role or "").strip()
        if r and r not in normalized_roles:
            normalized_roles.append(r)

    if not domain_id:
        try:
            domain_match = classify_domain(goal)
            domain_id = str(domain_match.domain_id or "domain.general")
        except Exception:
            domain_id = "domain.general"

    catalog_index = _catalog_index(include_disabled=True)
    role_index = _runtime_role_package_index(include_disabled=True)

    entries: List[Dict[str, Any]] = []
    issues: List[str] = []
    seen: set[str] = set()

    def add_entry(item: Dict[str, Any], *, required: bool, source: str, notes: str = "") -> None:
        package_type = str(item.get("type") or "").strip()
        package_id = str(item.get("id") or "").strip()
        if not package_type or not package_id:
            return

        key = f"{package_type}:{package_id}"
        if key in seen:
            return
        seen.add(key)

        status = str(item.get("status") or "disabled").strip().lower()
        effective_notes = notes
        if required and status != "active":
            effective_notes = (effective_notes + "; required package currently not active").strip("; ")
            issues.append(f"required package not active: {package_type}:{package_id} (status={status})")

        entries.append(
            {
                "package_type": package_type,
                "package_id": package_id,
                "package_name": str(item.get("name") or package_id),
                "locked_version": str(item.get("version") or "0.1.0"),
                "locked_status": status,
                "required": 1 if required else 0,
                "lock_source": source,
                "manifest_path": str(item.get("manifest_path") or ""),
                "notes": effective_notes,
            }
        )

    # 1) domain lock
    normalized_domain_id = str(domain_id or "domain.general").strip() or "domain.general"
    domain_item = catalog_index.get(("domain", normalized_domain_id))
    if not domain_item:
        domain_item = catalog_index.get(("domain", "domain.general"))
        issues.append(f"domain package not found: {normalized_domain_id}; fallback to domain.general")
    if domain_item:
        add_entry(domain_item, required=True, source="domain_router")

    # 2) role lock（仅锁编排涉及角色）
    missing_roles: List[str] = []
    for runtime_role in normalized_roles:
        candidates = role_index.get(runtime_role, [])
        if not candidates:
            missing_roles.append(runtime_role)
            continue
        add_entry(candidates[0], required=True, source=f"runtime_role:{runtime_role}")

    if missing_roles:
        issues.append("runtime role package missing: " + ", ".join(missing_roles))

    # 3) skill baseline lock（按角色/能力精准锁定，避免无关技能变更影响运行）
    role_cap_matrix = build_role_capability_matrix(normalized_roles, include_disabled=True)
    required_caps: set[str] = set()
    for row in role_cap_matrix.get("roles", []):
        for cap in row.get("required_capabilities", []):
            cap_id = str(cap.get("id") or "").strip()
            if cap_id:
                required_caps.add(cap_id)

    selected_skill_items: List[Dict[str, Any]] = []
    for skill_item in _scan_packages("skill", include_disabled=True):
        sid = str(skill_item.get("id") or "").strip()
        provided_caps = set(_normalize_string_list(skill_item.get("provided_capabilities")))

        is_global_core = bool(skill_item.get("core", False))
        supports_roles = _skill_supports_runtime_roles(skill_item, normalized_roles)
        supports_required_caps = bool(required_caps and provided_caps.intersection(required_caps))

        if is_global_core or supports_roles or supports_required_caps:
            selected_skill_items.append(skill_item)

    if not selected_skill_items:
        selected_skill_items = [x for x in _scan_packages("skill", include_disabled=True) if str(x.get("status") or "") == "active"]
        issues.append("skill baseline fallback to all active packs")

    for skill_item in selected_skill_items:
        source_parts: List[str] = []
        if _skill_supports_runtime_roles(skill_item, normalized_roles):
            source_parts.append("role_matched")
        if set(_normalize_string_list(skill_item.get("provided_capabilities"))).intersection(required_caps):
            source_parts.append("capability_matched")
        if bool(skill_item.get("core", False)):
            source_parts.append("global_core")

        add_entry(
            skill_item,
            required=True,
            source="skill_pack_baseline:" + ("+".join(source_parts) if source_parts else "selected"),
        )

    revision = _stable_lock_revision(entries)

    return {
        "revision": revision,
        "domain_id": normalized_domain_id,
        "runtime_roles": normalized_roles,
        "entries": entries,
        "count": len(entries),
        "issues": issues,
    }


async def save_run_package_lock(
    *,
    run_id: int,
    workspace_id: int,
    user_id: int,
    goal: str,
    runtime_roles: List[str],
    domain_id: Optional[str] = None,
    db: Any = None,
) -> Dict[str, Any]:
    """为指定 run 持久化包版本锁。"""
    owns_db = db is None
    conn = db or await get_db()

    lock_data = build_execution_package_lock(
        goal=goal,
        runtime_roles=runtime_roles,
        domain_id=domain_id,
    )

    await conn.execute("DELETE FROM workspace_run_package_locks WHERE run_id = ?", (run_id,))

    for entry in lock_data["entries"]:
        await conn.execute(
            """
            INSERT INTO workspace_run_package_locks
            (run_id, workspace_id, user_id, package_type, package_id, locked_version, locked_status, required, lock_source, manifest_path, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                workspace_id,
                user_id,
                str(entry.get("package_type") or ""),
                str(entry.get("package_id") or ""),
                str(entry.get("locked_version") or "0.1.0"),
                str(entry.get("locked_status") or "active"),
                int(entry.get("required", 1)),
                str(entry.get("lock_source") or ""),
                str(entry.get("manifest_path") or ""),
                str(entry.get("notes") or ""),
            ),
        )

    if owns_db:
        await conn.commit()

    return {
        "run_id": run_id,
        "workspace_id": workspace_id,
        "revision": lock_data["revision"],
        "domain_id": lock_data["domain_id"],
        "runtime_roles": lock_data["runtime_roles"],
        "count": lock_data["count"],
        "issues": lock_data["issues"],
    }


async def get_run_package_lock(
    *,
    run_id: int,
    workspace_id: int,
    user_id: int,
    db: Any = None,
) -> Dict[str, Any]:
    """读取执行编排的包版本锁。"""
    conn = db or await get_db()

    await require_workspace_permission(
        workspace_id=workspace_id,
        user_id=user_id,
        permission="read",
        allow_global_admin=True,
        db=conn,
    )
    run_row = await conn.execute_fetchone(
        """
        SELECT id, goal, status, source, user_id
        FROM workspace_flow_runs
        WHERE id = ? AND workspace_id = ?
        """,
        (run_id, workspace_id),
    )
    if not run_row:
        raise KeyError(f"run not found: {run_id}")
    run_item = dict(run_row)

    rows = await conn.execute_fetchall(
        """
        SELECT id, package_type, package_id, locked_version, locked_status, required,
               lock_source, manifest_path, notes, created_at
        FROM workspace_run_package_locks
        WHERE run_id = ?
        ORDER BY id ASC
        """,
        (run_id,),
    )

    entries: List[Dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["required"] = bool(item.get("required", 1))
        entries.append(item)

    return {
        "run_id": run_id,
        "workspace_id": workspace_id,
        "goal": str(run_item.get("goal") or ""),
        "run_status": str(run_item.get("status") or "planned"),
        "source": str(run_item.get("source") or "manual"),
        "count": len(entries),
        "revision": _stable_lock_revision(entries),
        "entries": entries,
    }


_DRIFT_SEVERITY_ORDER: Dict[str, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
    "info": 0,
}

_DRIFT_SEVERITY_SCORE: Dict[str, int] = {
    "critical": 100,
    "high": 60,
    "medium": 30,
    "low": 10,
    "info": 0,
}


def _assess_drift_risk(
    *,
    package_type: str,
    kind: str,
    required: bool,
    lock_source: str,
) -> Dict[str, Any]:
    package_type = str(package_type or "").strip().lower()
    kind = str(kind or "").strip().lower()
    lock_source = str(lock_source or "").strip().lower()

    is_global_core = "global_core" in lock_source

    severity = "low"
    blocking = False
    rationale = "minor drift"

    if kind == "missing":
        rationale = "locked package missing from current catalog"
        if package_type in {"domain", "role"}:
            severity = "critical"
            blocking = True
        elif package_type == "skill":
            severity = "high" if (required or is_global_core) else "medium"
            blocking = bool(required or is_global_core)
        elif package_type == "capability":
            severity = "low"
            blocking = False

    elif kind == "status_changed":
        rationale = "locked package status changed"
        if package_type in {"domain", "role"}:
            severity = "critical"
            blocking = True
        elif package_type == "skill":
            severity = "high" if (required or is_global_core) else "medium"
            blocking = bool(required or is_global_core)
        elif package_type == "capability":
            severity = "medium" if required else "low"
            blocking = False

    elif kind == "version_changed":
        rationale = "locked package version changed"
        if package_type in {"domain", "role"}:
            severity = "high"
            blocking = True
        elif package_type == "skill":
            severity = "high" if is_global_core else "medium"
            blocking = bool(is_global_core and required)
        elif package_type == "capability":
            severity = "low"
            blocking = False

    recommended_action = (
        "rollback package or relock run before execute"
        if severity in {"critical", "high"}
        else "review drift; may continue with warn policy"
    )

    return {
        "severity": severity,
        "severity_score": _DRIFT_SEVERITY_SCORE.get(severity, 0),
        "blocking": blocking,
        "rationale": rationale,
        "recommended_action": recommended_action,
    }


async def check_run_package_lock_drift(
    *,
    run_id: int,
    workspace_id: int,
    user_id: int,
    db: Any = None,
) -> Dict[str, Any]:
    """校验当前包状态是否与 run 创建时的锁定快照一致。"""
    lock_state = await get_run_package_lock(
        run_id=run_id,
        workspace_id=workspace_id,
        user_id=user_id,
        db=db,
    )

    current_index = _catalog_index(include_disabled=True)
    drifts: List[Dict[str, Any]] = []

    for entry in lock_state.get("entries", []):
        package_type = _normalize_package_type(entry.get("package_type"))
        package_id = str(entry.get("package_id") or "").strip()
        if not package_type or not package_id:
            continue

        required = bool(entry.get("required", True))
        locked_version = str(entry.get("locked_version") or "0.1.0")
        locked_status = str(entry.get("locked_status") or "active")
        lock_source = str(entry.get("lock_source") or "")
        matched = _resolve_dependency(package_type, package_id, current_index)

        if not matched:
            risk = _assess_drift_risk(
                package_type=package_type,
                kind="missing",
                required=required,
                lock_source=lock_source,
            )
            drifts.append(
                {
                    "package_type": package_type,
                    "package_id": package_id,
                    "kind": "missing",
                    "required": required,
                    "lock_source": lock_source,
                    "message": "locked package is missing from current catalog",
                    **risk,
                }
            )
            continue

        current_version = str(matched.get("version") or "0.1.0")
        current_status = str(matched.get("status") or "disabled")

        if locked_version != current_version:
            risk = _assess_drift_risk(
                package_type=package_type,
                kind="version_changed",
                required=required,
                lock_source=lock_source,
            )
            drifts.append(
                {
                    "package_type": package_type,
                    "package_id": package_id,
                    "kind": "version_changed",
                    "required": required,
                    "lock_source": lock_source,
                    "locked_version": locked_version,
                    "current_version": current_version,
                    "message": "locked package version drift detected",
                    **risk,
                }
            )

        if required and locked_status == "active" and current_status != "active":
            risk = _assess_drift_risk(
                package_type=package_type,
                kind="status_changed",
                required=required,
                lock_source=lock_source,
            )
            drifts.append(
                {
                    "package_type": package_type,
                    "package_id": package_id,
                    "kind": "status_changed",
                    "required": required,
                    "lock_source": lock_source,
                    "locked_status": locked_status,
                    "current_status": current_status,
                    "message": "required locked package is no longer active",
                    **risk,
                }
            )

    blocking_count = sum(1 for d in drifts if d.get("blocking"))

    severity_counts: Dict[str, int] = {key: 0 for key in _DRIFT_SEVERITY_ORDER.keys()}
    max_severity = "info"
    risk_score_total = 0

    for drift in drifts:
        severity = str(drift.get("severity") or "low")
        if severity not in severity_counts:
            severity = "low"
        severity_counts[severity] += 1
        risk_score_total += int(drift.get("severity_score") or 0)

        if _DRIFT_SEVERITY_ORDER.get(severity, 0) > _DRIFT_SEVERITY_ORDER.get(max_severity, 0):
            max_severity = severity

    return {
        **lock_state,
        "ok": blocking_count == 0,
        "drifts": drifts,
        "drift_count": len(drifts),
        "blocking_count": blocking_count,
        "blocking_drifts": [d for d in drifts if d.get("blocking")],
        "severity_summary": severity_counts,
        "max_severity": max_severity,
        "risk_score": risk_score_total,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
