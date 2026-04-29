"""Kernel路由 — 工作流模块/Workspace内核配置/Pipeline执行。"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.core.role_router import build_runtime_role_context, normalize_runtime_role, suggest_roles
from src.routes.auth import get_current_user
from src.services.package_runtime import PackageValidationError, set_package_status

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None

router = APIRouter(prefix="/api/kernel", tags=["kernel"])
logger = logging.getLogger(__name__)

PackageType = Literal["domain", "role", "skill", "capability"]

_ALLOWED_PACKAGE_TYPES: set[str] = {"domain", "role", "skill", "capability"}
_ALLOWED_FIX_ACTIONS: set[str] = {
    "activate_package",
    "patch_manifest",
    "create_role_package",
    "create_skill_package",
    "create_capability_manifest",
}
_ALLOWED_PATCH_FIELDS: Dict[str, set[str]] = {
    "domain": {
        "status",
        "enabled",
        "keywords",
        "preferred_roles",
        "prompt_profile",
        "description",
    },
    "role": {
        "status",
        "enabled",
        "runtime_role",
        "priority",
        "domains",
        "keywords",
        "preferred_actions",
        "required_capabilities",
        "optional_capabilities",
        "prompt_hints",
        "execution_mandate",
    },
    "skill": {
        "status",
        "enabled",
        "provided_roles",
        "provided_capabilities",
        "core",
        "dependencies",
        "compatibility",
        "prompt_hints",
        "execution_mandate",
    },
    "capability": {
        "status",
        "enabled",
        "label",
        "icon",
        "section",
        "order",
        "requires",
        "description",
        "renderer",
    },
}

# 内置工作流模块清单
_BUILT_IN_MODULES = [
    {
        "name": "intent_analysis",
        "display_name": "意图分析",
        "description": "基于规则的意图识别，0 LLM调用",
        "type": "analysis",
        "enabled": True,
    },
    {
        "name": "feature_selection",
        "display_name": "特性选择",
        "description": "预算式特性选择器，每条消息最多注入3个特性",
        "type": "selection",
        "enabled": True,
    },
    {
        "name": "prompt_builder",
        "display_name": "Prompt构建",
        "description": "精简Prompt构建，控制在2000字符以内",
        "type": "build",
        "enabled": True,
    },
    {
        "name": "llm_client",
        "display_name": "LLM客户端",
        "description": "OpenAI兼容流式客户端，每条消息1次LLM调用",
        "type": "inference",
        "enabled": True,
    },
    {
        "name": "quality_checker",
        "display_name": "质量检查",
        "description": "5维度基于规则的质量评估，无LLM调用",
        "type": "quality",
        "enabled": True,
    },
    {
        "name": "trust_scorer",
        "display_name": "信任评分",
        "description": "4级信任评分 + DB持久化",
        "type": "trust",
        "enabled": True,
    },
    {
        "name": "agent_memory",
        "display_name": "Agent记忆",
        "description": "学习捕获 + 去重",
        "type": "memory",
        "enabled": True,
    },
    {
        "name": "alerts",
        "display_name": "告警系统",
        "description": "6类告警 + 去重 + cooldown",
        "type": "alerting",
        "enabled": True,
    },
    {
        "name": "background_tasks",
        "display_name": "后台任务",
        "description": "SSE响应后异步执行quality/trust/learning/alert",
        "type": "background",
        "enabled": True,
    },
]



class KernelRuntimeProfileRequest(BaseModel):
    message: str = Field(default="", description="用户目标描述（可选）")
    runtime_roles: List[str] = Field(default_factory=list, description="期望参与编排的runtime角色")
    domain_hint: str = Field(default="", description="可选领域提示")
    max_roles: int = Field(default=4, ge=1, le=12)


def _resolve_runtime_role(value: str, *, alias_map: Dict[str, str], available_role_set: set[str]) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    mapped = str(alias_map.get(raw) or raw).strip().lower()
    if mapped in available_role_set:
        return mapped
    return ""


def _normalize_runtime_roles(raw_roles: List[str]) -> Dict[str, Any]:
    role_ctx = build_runtime_role_context()
    available_roles = [
        str(x).strip().lower()
        for x in (role_ctx.get("runtime_roles") or [])
        if str(x).strip()
    ]
    if not available_roles:
        available_roles = ["ops"]

    default_role = str(role_ctx.get("default_role") or "ops").strip().lower() or "ops"
    if default_role not in available_roles:
        default_role = available_roles[0]

    alias_map = role_ctx.get("alias_map") if isinstance(role_ctx.get("alias_map"), dict) else {}
    available_role_set = set(available_roles)

    normalized: List[str] = []
    for role in raw_roles:
        resolved = _resolve_runtime_role(role, alias_map=alias_map, available_role_set=available_role_set)
        if resolved and resolved not in normalized:
            normalized.append(resolved)

    if not normalized:
        normalized = [default_role]

    return {
        "runtime_roles": normalized,
        "available_roles": available_roles,
        "default_role": default_role,
        "alias_map": alias_map,
        "role_ctx": role_ctx,
    }




def _safe_string_list(value: Any) -> List[str]:
    if isinstance(value, list):
        raw_items = value
    elif isinstance(value, str):
        raw_items = [x.strip() for x in value.split(",")]
    else:
        raw_items = []

    normalized: List[str] = []
    seen: set[str] = set()
    for item in raw_items:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized


def _sections_index(catalog: Dict[str, Any]) -> Dict[str, List[Dict[str, Any]]]:
    index: Dict[str, List[Dict[str, Any]]] = {}
    for section in catalog.get("sections") or []:
        section_type = str(section.get("type") or "").strip().lower()
        if not section_type:
            continue
        items = section.get("items") if isinstance(section.get("items"), list) else []
        index[section_type] = items
    return index


def _build_generalization_snapshot() -> Dict[str, Any]:
    from src.config import PROJECT_ROOT
    from src.services.capability_catalog import build_catalog_payload
    from src.services.package_runtime import build_package_catalog
    from src.skills.registry import get_registry

    role_ctx = build_runtime_role_context()
    runtime_roles = [
        str(x).strip().lower()
        for x in (role_ctx.get("runtime_roles") or [])
        if str(x).strip()
    ]
    if not runtime_roles:
        runtime_roles = ["ops"]

    role_packages = role_ctx.get("role_packages") if isinstance(role_ctx.get("role_packages"), dict) else {}

    registry = get_registry()
    role_skill_matrix = registry.build_role_skill_matrix(runtime_roles)

    catalog = build_package_catalog(include_disabled=True)
    sections = _sections_index(catalog)
    role_items = sections.get("role", [])
    skill_items = sections.get("skill", [])
    capability_items = sections.get("capability", [])

    package_counts: Dict[str, Any] = {}
    for section in catalog.get("sections") or []:
        section_type = str(section.get("type") or "")
        if not section_type:
            continue
        package_counts[section_type] = {
            "count": int(section.get("count") or 0),
            "active": int(section.get("active_count") or 0),
            "disabled": int(section.get("disabled_count") or 0),
        }

    capability_catalog = build_catalog_payload()
    capability_routes = [
        str(item.get("route") or "")
        for item in (capability_catalog.get("catalog") or [])
        if str(item.get("route") or "").strip()
    ]

    role_rows: List[Dict[str, Any]] = []
    for row in role_skill_matrix.get("roles") or []:
        runtime_role = str(row.get("runtime_role") or "")
        packages = role_packages.get(runtime_role) if isinstance(role_packages.get(runtime_role), list) else []
        role_rows.append(
            {
                "runtime_role": runtime_role,
                "role_package_count": len(packages),
                "skill_count": int(row.get("skill_count") or 0),
                "skills": row.get("skills") or [],
                "hot_pluggable": len(packages) > 0 and int(row.get("skill_count") or 0) > 0,
            }
        )

    uncovered_roles = [
        str(x).strip().lower()
        for x in (role_skill_matrix.get("uncovered_roles") or [])
        if str(x).strip()
    ]

    runtime_role_count = max(1, len(runtime_roles))
    covered_role_count = runtime_role_count - len(uncovered_roles)
    role_coverage = covered_role_count / runtime_role_count

    package_health_values: List[float] = []
    for package_type in ("domain", "role", "skill", "capability"):
        stat = package_counts.get(package_type) if isinstance(package_counts.get(package_type), dict) else {}
        total = int(stat.get("count") or 0)
        active = int(stat.get("active") or 0)
        package_health_values.append((active / total) if total > 0 else 0.0)

    package_health = sum(package_health_values) / len(package_health_values) if package_health_values else 0.0
    capability_ratio = min(1.0, len(capability_routes) / 10.0)

    maturity_score = round(
        (role_coverage * 0.5 + package_health * 0.3 + capability_ratio * 0.2) * 100,
        1,
    )

    role_manifest_by_runtime: Dict[str, List[Dict[str, Any]]] = {}
    for item in role_items:
        runtime_role = str(item.get("runtime_role") or "").strip().lower()
        if not runtime_role:
            continue
        role_manifest_by_runtime.setdefault(runtime_role, []).append(item)

    recommendations: List[Dict[str, Any]] = []
    autofix_actions: List[Dict[str, Any]] = []

    if uncovered_roles:
        missing_rows: List[Dict[str, Any]] = []
        for runtime_role in uncovered_roles:
            role_packages_for_runtime = role_manifest_by_runtime.get(runtime_role, [])
            role_required_capabilities: List[str] = []
            for role_pkg in role_packages_for_runtime:
                role_required_capabilities.extend(_safe_string_list(role_pkg.get("required_capabilities")))
            required_cap_set = {x for x in role_required_capabilities if x}

            role_manifest_paths = [
                str(pkg.get("manifest_path") or "")
                for pkg in role_packages_for_runtime
                if str(pkg.get("manifest_path") or "").strip()
            ]

            fixes: List[Dict[str, Any]] = []

            disabled_role_skills = [
                skill
                for skill in skill_items
                if runtime_role in [x.lower() for x in _safe_string_list(skill.get("provided_roles"))]
                and str(skill.get("status") or "disabled") != "active"
            ]
            for skill in disabled_role_skills[:3]:
                fix = {
                    "action": "activate_package",
                    "package_type": "skill",
                    "package_id": str(skill.get("id") or ""),
                    "manifest_path": str(skill.get("manifest_path") or ""),
                    "reason": f"runtime_role={runtime_role} 无可用技能，优先激活已声明 provided_roles 的技能包。",
                }
                fixes.append(fix)
                autofix_actions.append(fix)

            bridge_skills = []
            for skill in skill_items:
                provided_caps = {x for x in _safe_string_list(skill.get("provided_capabilities")) if x}
                if not required_cap_set.intersection(provided_caps):
                    continue
                current_roles = [x.lower() for x in _safe_string_list(skill.get("provided_roles"))]
                if runtime_role not in current_roles:
                    bridge_skills.append(skill)

            for skill in bridge_skills[:3]:
                current_roles = [x.lower() for x in _safe_string_list(skill.get("provided_roles"))]
                patched_roles = list(dict.fromkeys(current_roles + [runtime_role]))
                fix = {
                    "action": "patch_manifest",
                    "package_type": "skill",
                    "package_id": str(skill.get("id") or ""),
                    "manifest_path": str(skill.get("manifest_path") or ""),
                    "suggested_patch": {"provided_roles": patched_roles},
                    "reason": f"技能包能力可覆盖岗位需求，但未声明 provided_roles 包含 {runtime_role}。",
                }
                fixes.append(fix)
                autofix_actions.append(fix)

            if not fixes:
                suggested_role_dir = str(runtime_role or "generic").replace("_", "-")
                suggested_manifest_path = (
                    PROJECT_ROOT / "packages" / "skills" / suggested_role_dir / "manifest.yaml"
                ).resolve()
                fix = {
                    "action": "create_skill_package",
                    "package_type": "skill",
                    "runtime_role": runtime_role,
                    "suggested_manifest_path": str(suggested_manifest_path),
                    "suggested_manifest": {
                        "id": f"builtin.{suggested_role_dir}",
                        "name": f"Builtin {runtime_role} Skill Pack",
                        "version": "1.0.0",
                        "status": "active",
                        "entrypoint": {
                            "module": f"src.skills.{suggested_role_dir.replace('-', '_')}",
                            "attr": "ALL_SKILLS",
                        },
                        "provided_roles": [runtime_role],
                        "provided_capabilities": sorted(required_cap_set),
                    },
                    "reason": "当前岗位缺少可绑定技能包，建议补充独立 skill manifest 以支持热插拔。",
                }
                fixes.append(fix)
                autofix_actions.append(fix)

            missing_rows.append(
                {
                    "runtime_role": runtime_role,
                    "required_capabilities": sorted(required_cap_set),
                    "role_manifest_paths": role_manifest_paths,
                    "fixes": fixes,
                }
            )

        recommendations.append(
            {
                "code": "missing_role_skill_binding",
                "severity": "high",
                "message": "存在岗位未绑定可执行技能，建议补充 skill manifest 的 provided_roles 或新增技能包。",
                "roles": uncovered_roles,
                "missing_bindings": missing_rows,
            }
        )

    role_without_package = [
        str(row.get("runtime_role") or "")
        for row in role_rows
        if int(row.get("role_package_count") or 0) == 0
    ]
    if role_without_package:
        fixes: List[Dict[str, Any]] = []
        for runtime_role in role_without_package:
            disabled_candidates = [
                item
                for item in role_items
                if str(item.get("runtime_role") or "").strip().lower() == runtime_role
                and str(item.get("status") or "disabled") != "active"
            ]

            if disabled_candidates:
                target = disabled_candidates[0]
                fix = {
                    "action": "activate_package",
                    "package_type": "role",
                    "package_id": str(target.get("id") or ""),
                    "manifest_path": str(target.get("manifest_path") or ""),
                    "reason": f"runtime_role={runtime_role} 已存在岗位包但处于停用状态。",
                }
                fixes.append(fix)
                autofix_actions.append(fix)
                continue

            suggested_manifest_path = (
                PROJECT_ROOT / "packages" / "roles" / f"{runtime_role}-core" / "manifest.yaml"
            ).resolve()
            fix = {
                "action": "create_role_package",
                "package_type": "role",
                "runtime_role": runtime_role,
                "suggested_manifest_path": str(suggested_manifest_path),
                "suggested_manifest": {
                    "id": f"role.{runtime_role}-core",
                    "name": f"{runtime_role} Core Role",
                    "version": "1.0.0",
                    "status": "active",
                    "runtime_role": runtime_role,
                    "priority": 100,
                    "domains": ["domain.general"],
                    "keywords": [runtime_role],
                    "preferred_actions": ["execute", "analyze"],
                },
                "reason": f"runtime_role={runtime_role} 缺少岗位包定义。",
            }
            fixes.append(fix)
            autofix_actions.append(fix)

        recommendations.append(
            {
                "code": "missing_role_package",
                "severity": "medium",
                "message": "存在运行时岗位没有激活的岗位包，建议补齐 role package manifest。",
                "roles": role_without_package,
                "fixes": fixes,
            }
        )

    disabled_summary = {
        k: int(v.get("disabled") or 0)
        for k, v in package_counts.items()
        if isinstance(v, dict) and int(v.get("disabled") or 0) > 0
    }
    if disabled_summary:
        disabled_packages: List[Dict[str, Any]] = []
        for package_type, items in sections.items():
            for item in items:
                if str(item.get("status") or "disabled") == "active":
                    continue
                disabled_packages.append(
                    {
                        "package_type": package_type,
                        "package_id": str(item.get("id") or ""),
                        "manifest_path": str(item.get("manifest_path") or ""),
                    }
                )

        quick_fixes = []
        for row in disabled_packages[:8]:
            fix = {
                "action": "activate_package",
                "package_type": row["package_type"],
                "package_id": row["package_id"],
                "manifest_path": row["manifest_path"],
                "reason": "该包已下线，若当前场景需要可重新启用。",
            }
            quick_fixes.append(fix)
            autofix_actions.append(fix)

        recommendations.append(
            {
                "code": "disabled_packages_present",
                "severity": "low",
                "message": "检测到已下线包，可根据业务场景选择激活或保留下线状态。",
                "details": disabled_summary,
                "packages": disabled_packages[:12],
                "fixes": quick_fixes,
            }
        )

    if len(capability_routes) < 5:
        baseline_capabilities: Dict[str, Dict[str, Any]] = {
            "cap.board": {
                "route": "board",
                "renderer": {"module": "./pages/board.js", "export": "renderBoard"},
            },
            "cap.platform": {
                "route": "platform",
                "renderer": {"module": "./pages/platform-connect.js", "export": "renderPlatformConnect"},
            },
            "cap.packages": {
                "route": "packages",
                "renderer": {"module": "./pages/packages-center.js", "export": "renderPackagesCenter"},
            },
            "cap.alerts": {
                "route": "alerts",
                "renderer": {"module": "./pages/alerts-center.js", "export": "renderAlertsCenter"},
            },
            "cap.dashboard": {
                "route": "dashboard",
                "renderer": {"module": "./pages/dashboard.js", "export": "renderDashboard"},
            },
        }
        existing_cap_ids = {
            str(item.get("id") or "")
            for item in capability_items
            if str(item.get("id") or "").strip()
        }
        missing_baseline = sorted([x for x in baseline_capabilities.keys() if x not in existing_cap_ids])

        fixes: List[Dict[str, Any]] = []
        for cap_id in missing_baseline:
            item = baseline_capabilities[cap_id]
            route = str(item.get("route") or "")
            manifest_path = (PROJECT_ROOT.parent / "client" / "src" / "capabilities" / route / "manifest.json").resolve()
            fix = {
                "action": "create_capability_manifest",
                "package_type": "capability",
                "package_id": cap_id,
                "suggested_manifest_path": str(manifest_path),
                "suggested_manifest": {
                    "id": cap_id,
                    "label": route,
                    "route": route,
                    "icon": "apps",
                    "section": "tools",
                    "order": 999,
                    "status": "active",
                    "renderer": item.get("renderer") or _default_renderer_for_route(route),
                    "requires": {"skills": [], "roles": [], "feature_flags": []},
                },
                "reason": "能力目录过稀疏，补齐基础 capability 以保证跨行业可见入口。",
            }
            fixes.append(fix)
            autofix_actions.append(fix)

        recommendations.append(
            {
                "code": "capability_catalog_sparse",
                "severity": "medium",
                "message": "前端能力目录偏少，建议补齐 capability 包，增强跨行业场景可见功能。",
                "capability_count": len(capability_routes),
                "missing_baseline_capabilities": missing_baseline,
                "fixes": fixes,
            }
        )

    deduped_actions: List[Dict[str, Any]] = []
    seen_action_keys: set[str] = set()
    for action in autofix_actions:
        action_type = str(action.get("action") or "")
        package_type = str(action.get("package_type") or "")
        package_id = str(action.get("package_id") or "")
        manifest_path = str(action.get("manifest_path") or action.get("suggested_manifest_path") or "")
        key = f"{action_type}|{package_type}|{package_id}|{manifest_path}"
        if key in seen_action_keys:
            continue
        seen_action_keys.add(key)
        deduped_actions.append(action)

    ready = len(uncovered_roles) == 0 and len(capability_routes) > 0

    return {
        "ready": ready,
        "maturity_score": maturity_score,
        "default_role": str(role_ctx.get("default_role") or "ops"),
        "runtime_role_count": len(runtime_roles),
        "runtime_roles": runtime_roles,
        "covered_role_count": covered_role_count,
        "uncovered_roles": uncovered_roles,
        "role_coverage": round(role_coverage, 4),
        "package_health": round(package_health, 4),
        "capability_ratio": round(capability_ratio, 4),
        "role_skill_matrix": role_rows,
        "package_counts": package_counts,
        "capability_routes": capability_routes,
        "capability_count": len(capability_routes),
        "recommendations": recommendations,
        "autofix_actions": deduped_actions,
    }


class KernelFixActionRequest(BaseModel):
    action: str = Field(default="", description="修复动作类型")
    package_type: str = Field(default="", description="包类型")
    package_id: str = Field(default="", description="包ID")
    runtime_role: str = Field(default="", description="运行时岗位")
    manifest_path: str = Field(default="", description="当前 manifest 路径")
    suggested_manifest_path: str = Field(default="", description="建议创建路径")
    suggested_patch: Dict[str, Any] = Field(default_factory=dict, description="manifest patch")
    suggested_manifest: Dict[str, Any] = Field(default_factory=dict, description="manifest 模板")


class KernelFixApplyRequest(BaseModel):
    actions: List[KernelFixActionRequest] = Field(default_factory=list)
    dry_run: bool = Field(default=False, description="仅验证，不落盘")


def _to_model_dict(model: Any) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        data = model.model_dump()  # pydantic v2
    elif hasattr(model, "dict"):
        data = model.dict()  # pydantic v1
    else:
        data = {}
    return data if isinstance(data, dict) else {}


def _normalize_package_type(value: Any) -> Optional[PackageType]:
    raw = str(value or "").strip().lower()
    if not raw:
        return None

    mapping: Dict[str, PackageType] = {
        "domain": "domain",
        "domains": "domain",
        "role": "role",
        "roles": "role",
        "skill": "skill",
        "skills": "skill",
        "capability": "capability",
        "capabilities": "capability",
    }
    normalized = mapping.get(raw)
    if normalized in _ALLOWED_PACKAGE_TYPES:
        return normalized
    return None


def _allowed_manifest_roots() -> List[Path]:
    from src.config import PROJECT_ROOT

    return [
        (PROJECT_ROOT / "packages").resolve(),
        (PROJECT_ROOT.parent / "client" / "src" / "capabilities").resolve(),
    ]


def _is_path_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except Exception:
        return False


def _resolve_safe_manifest_path(raw_path: str) -> Path:
    if not str(raw_path or "").strip():
        raise ValueError("manifest_path 不能为空")

    resolved = Path(str(raw_path)).expanduser().resolve()
    if resolved.suffix.lower() not in {".yaml", ".yml", ".json"}:
        raise ValueError(f"manifest 扩展名不受支持: {resolved.suffix}")

    for root in _allowed_manifest_roots():
        if _is_path_within(resolved, root):
            return resolved
    raise ValueError(f"manifest_path 不在白名单目录内: {resolved}")


def _read_manifest_file(path: Path) -> Dict[str, Any]:
    raw = path.read_text(encoding="utf-8")

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    if yaml is None:
        raise ValueError("manifest 不是 JSON 且当前环境缺少 PyYAML")

    parsed_yaml = yaml.safe_load(raw)
    if not isinstance(parsed_yaml, dict):
        raise ValueError("manifest 内容必须是对象")
    return parsed_yaml


def _write_manifest_file(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(data, ensure_ascii=False, indent=2)
    path.write_text(body + "\n", encoding="utf-8")


def _route_to_pascal(route: str) -> str:
    parts = [seg for seg in str(route or "").replace("_", "-").split("-") if seg]
    if not parts:
        return "Page"
    return "".join(seg[:1].upper() + seg[1:] for seg in parts)


def _default_renderer_for_route(route: str) -> Dict[str, str]:
    route_key = str(route or "").strip().lower()
    mapping: Dict[str, Dict[str, str]] = {
        "board": {"module": "./pages/board.js", "export": "renderBoard"},
        "product": {"module": "./pages/product-detail.js", "export": "renderProductDetail"},
        "workspace": {"module": "./pages/product-workspace.js", "export": "renderWorkspace"},
        "campaigns": {"module": "./pages/campaign-view.js", "export": "renderCampaignView"},
        "knowledge": {"module": "./pages/knowledge-browser.js", "export": "renderKnowledgeBrowser"},
        "briefing": {"module": "./pages/daily-briefing.js", "export": "renderDailyBriefing"},
        "history": {"module": "./pages/conversation-history.js", "export": "renderConversationHistory"},
        "dashboard": {"module": "./pages/dashboard.js", "export": "renderDashboard"},
        "agents": {"module": "./pages/agents-showcase.js", "export": "renderAgentsShowcase"},
        "platform": {"module": "./pages/platform-connect.js", "export": "renderPlatformConnect"},
        "alerts": {"module": "./pages/alerts-center.js", "export": "renderAlertsCenter"},
        "packages": {"module": "./pages/packages-center.js", "export": "renderPackagesCenter"},
    }
    if route_key in mapping:
        return dict(mapping[route_key])

    return {
        "module": f"./pages/{route_key}.js",
        "export": f"render{_route_to_pascal(route_key)}",
    }


def _default_manifest_for_action(action_name: str, action: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(action.get("suggested_manifest"), dict) and action.get("suggested_manifest"):
        return dict(action["suggested_manifest"])

    runtime_role = str(action.get("runtime_role") or "").strip().lower()
    package_id = str(action.get("package_id") or "").strip()

    if action_name == "create_role_package":
        role_token = runtime_role or package_id.replace("role.", "") or "generic"
        role_slug = role_token.replace("_", "-")
        return {
            "id": package_id or f"role.{role_slug}-core",
            "name": f"{role_token} Core Role",
            "version": "1.0.0",
            "status": "active",
            "runtime_role": role_token,
            "priority": 100,
            "domains": ["domain.general"],
            "keywords": [role_token],
            "preferred_actions": ["execute", "analyze"],
            "required_capabilities": [],
            "optional_capabilities": [],
        }

    if action_name == "create_skill_package":
        role_token = runtime_role or package_id.replace("builtin.", "") or "generic"
        role_slug = role_token.replace("_", "-")
        return {
            "id": package_id or f"builtin.{role_slug}",
            "name": f"Builtin {role_token} Skill Pack",
            "version": "1.0.0",
            "status": "active",
            "entrypoint": {
                "module": f"src.skills.{role_slug.replace('-', '_')}",
                "attr": "ALL_SKILLS",
            },
            "provided_roles": [role_token],
            "provided_capabilities": [],
            "dependencies": {},
            "compatibility": {},
        }

    if action_name == "create_capability_manifest":
        cap_id = package_id.strip() or "cap.generic"
        route = cap_id.split(".", 1)[1] if cap_id.startswith("cap.") else cap_id
        renderer = _default_renderer_for_route(route)
        return {
            "id": cap_id,
            "label": route,
            "route": route,
            "icon": "apps",
            "section": "tools",
            "order": 999,
            "status": "active",
            "renderer": renderer,
            "requires": {"skills": [], "roles": [], "feature_flags": []},
        }

    return {}


def _lookup_manifest_path(package_type: PackageType, package_id: str) -> str:
    from src.services.package_runtime import build_package_catalog

    catalog = build_package_catalog(include_disabled=True)
    for section in catalog.get("sections") or []:
        if str(section.get("type") or "") != package_type:
            continue
        for item in section.get("items") or []:
            if str(item.get("id") or "") == package_id:
                return str(item.get("manifest_path") or "")
    return ""


def _resolve_create_manifest_path(action_name: str, action: Dict[str, Any], manifest: Dict[str, Any]) -> Path:
    raw_path = str(action.get("suggested_manifest_path") or action.get("manifest_path") or "").strip()
    if raw_path:
        return _resolve_safe_manifest_path(raw_path)

    from src.config import PROJECT_ROOT

    if action_name == "create_role_package":
        runtime_role = str(action.get("runtime_role") or manifest.get("runtime_role") or "generic").strip().lower()
        slug = runtime_role.replace("_", "-")
        return _resolve_safe_manifest_path(str((PROJECT_ROOT / "packages" / "roles" / f"{slug}-core" / "manifest.yaml").resolve()))

    if action_name == "create_skill_package":
        runtime_role = str(action.get("runtime_role") or "").strip().lower()
        if not runtime_role:
            role_from_id = str(manifest.get("id") or "").replace("builtin.", "").strip().lower()
            runtime_role = role_from_id or "generic"
        slug = runtime_role.replace("_", "-")
        return _resolve_safe_manifest_path(str((PROJECT_ROOT / "packages" / "skills" / slug / "manifest.yaml").resolve()))

    if action_name == "create_capability_manifest":
        route = str(manifest.get("route") or "generic").strip().lower()
        return _resolve_safe_manifest_path(
            str((PROJECT_ROOT.parent / "client" / "src" / "capabilities" / route / "manifest.json").resolve())
        )

    raise ValueError(f"unsupported create action: {action_name}")


async def _log_kernel_fix_apply(
    *,
    action_payload: Dict[str, Any],
    result_payload: Dict[str, Any],
    changed_by: Optional[int],
) -> None:
    from src.database import get_db

    try:
        db = await get_db()
        await db.execute(
            """
            INSERT INTO kernel_fix_apply_logs
                (action_type, package_type, package_id, status, reason, payload, result, changed_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (
                str(action_payload.get("action") or ""),
                str(action_payload.get("package_type") or ""),
                str(action_payload.get("package_id") or ""),
                str(result_payload.get("status") or "unknown"),
                str(result_payload.get("reason") or "")[:300],
                json.dumps(action_payload, ensure_ascii=False),
                json.dumps(result_payload, ensure_ascii=False),
                changed_by,
            ),
        )
        await db.commit()
    except Exception as exc:
        logger.warning("kernel fix apply log failed: %s", exc)


async def _apply_kernel_fix_action(
    action_payload: Dict[str, Any],
    *,
    changed_by: Optional[int],
    dry_run: bool,
) -> Dict[str, Any]:
    action_name = str(action_payload.get("action") or "").strip()
    package_type = _normalize_package_type(action_payload.get("package_type"))
    package_id = str(action_payload.get("package_id") or "").strip()

    if action_name not in _ALLOWED_FIX_ACTIONS:
        return {
            "status": "error",
            "action": action_name,
            "package_type": str(action_payload.get("package_type") or ""),
            "package_id": package_id,
            "reason": f"unsupported action: {action_name}",
        }

    if action_name == "activate_package":
        if not package_type or not package_id:
            return {
                "status": "error",
                "action": action_name,
                "package_type": str(action_payload.get("package_type") or ""),
                "package_id": package_id,
                "reason": "activate_package 缺少 package_type 或 package_id",
            }

        if dry_run:
            return {
                "status": "applied",
                "action": action_name,
                "package_type": package_type,
                "package_id": package_id,
                "reason": "dry_run: activation validated",
            }

        try:
            updated = await set_package_status(
                package_type,
                package_id,
                "active",
                changed_by=changed_by,
                reason="kernel_generalization_autofix",
            )
            return {
                "status": "applied",
                "action": action_name,
                "package_type": package_type,
                "package_id": package_id,
                "manifest_path": str(updated.get("manifest_path") or action_payload.get("manifest_path") or ""),
                "reason": "package activated",
            }
        except PackageValidationError as exc:
            return {
                "status": "error",
                "action": action_name,
                "package_type": package_type,
                "package_id": package_id,
                "reason": "activation blocked by validation",
                "validation": exc.as_dict(),
            }
        except KeyError as exc:
            return {
                "status": "error",
                "action": action_name,
                "package_type": package_type,
                "package_id": package_id,
                "reason": str(exc),
            }
        except Exception as exc:
            return {
                "status": "error",
                "action": action_name,
                "package_type": package_type,
                "package_id": package_id,
                "reason": f"activation failed: {exc}",
            }

    if action_name == "patch_manifest":
        if not package_type:
            return {
                "status": "error",
                "action": action_name,
                "package_type": "",
                "package_id": package_id,
                "reason": "patch_manifest 缺少 package_type",
            }

        patch = action_payload.get("suggested_patch")
        if not isinstance(patch, dict) or not patch:
            return {
                "status": "error",
                "action": action_name,
                "package_type": package_type,
                "package_id": package_id,
                "reason": "suggested_patch 不能为空",
            }

        allowed_fields = _ALLOWED_PATCH_FIELDS.get(package_type, set())
        invalid_fields = sorted([key for key in patch.keys() if key not in allowed_fields])
        if invalid_fields:
            return {
                "status": "error",
                "action": action_name,
                "package_type": package_type,
                "package_id": package_id,
                "reason": "patch 字段不在白名单内",
                "invalid_fields": invalid_fields,
            }

        raw_manifest_path = str(action_payload.get("manifest_path") or "").strip()
        if not raw_manifest_path and package_id:
            raw_manifest_path = _lookup_manifest_path(package_type, package_id)

        if not raw_manifest_path:
            return {
                "status": "error",
                "action": action_name,
                "package_type": package_type,
                "package_id": package_id,
                "reason": "无法定位 manifest_path",
            }

        try:
            manifest_path = _resolve_safe_manifest_path(raw_manifest_path)
        except Exception as exc:
            return {
                "status": "error",
                "action": action_name,
                "package_type": package_type,
                "package_id": package_id,
                "manifest_path": raw_manifest_path,
                "reason": f"unsafe_manifest_path: {exc}",
            }

        if not manifest_path.exists():
            return {
                "status": "error",
                "action": action_name,
                "package_type": package_type,
                "package_id": package_id,
                "manifest_path": str(manifest_path),
                "reason": "manifest 文件不存在",
            }

        try:
            manifest_data = _read_manifest_file(manifest_path)
            merged_manifest = dict(manifest_data)
            for key, value in patch.items():
                merged_manifest[key] = value

            if not dry_run:
                _write_manifest_file(manifest_path, merged_manifest)
                if package_type == "skill":
                    from src.skills.registry import get_registry

                    get_registry().reload()

            return {
                "status": "applied",
                "action": action_name,
                "package_type": package_type,
                "package_id": package_id or str(merged_manifest.get("id") or ""),
                "manifest_path": str(manifest_path),
                "reason": "manifest patched" + (" (dry_run)" if dry_run else ""),
                "updated_fields": sorted(list(patch.keys())),
            }
        except Exception as exc:
            return {
                "status": "error",
                "action": action_name,
                "package_type": package_type,
                "package_id": package_id,
                "manifest_path": str(manifest_path),
                "reason": f"patch failed: {exc}",
            }

    # create_* actions
    expected_type_by_action: Dict[str, PackageType] = {
        "create_role_package": "role",
        "create_skill_package": "skill",
        "create_capability_manifest": "capability",
    }
    target_type = expected_type_by_action[action_name]

    if package_type and package_type != target_type:
        return {
            "status": "error",
            "action": action_name,
            "package_type": package_type,
            "package_id": package_id,
            "reason": f"action 与 package_type 不匹配: {action_name} -> {target_type}",
        }

    try:
        manifest = _default_manifest_for_action(action_name, action_payload)
        if not manifest:
            return {
                "status": "error",
                "action": action_name,
                "package_type": target_type,
                "package_id": package_id,
                "reason": "缺少 suggested_manifest，且默认模板生成失败",
            }

        final_package_id = str(manifest.get("id") or package_id).strip()
        manifest_path = _resolve_create_manifest_path(action_name, action_payload, manifest)

        if manifest_path.exists():
            return {
                "status": "skipped",
                "action": action_name,
                "package_type": target_type,
                "package_id": final_package_id,
                "manifest_path": str(manifest_path),
                "reason": "manifest 已存在，跳过创建",
            }

        if not dry_run:
            _write_manifest_file(manifest_path, manifest)
            if target_type == "skill":
                from src.skills.registry import get_registry

                get_registry().reload()

        return {
            "status": "applied",
            "action": action_name,
            "package_type": target_type,
            "package_id": final_package_id,
            "manifest_path": str(manifest_path),
            "reason": "manifest created" + (" (dry_run)" if dry_run else ""),
        }
    except Exception as exc:
        return {
            "status": "error",
            "action": action_name,
            "package_type": target_type,
            "package_id": package_id,
            "reason": f"create failed: {exc}",
        }


@router.get("/runtime-roles")
async def list_runtime_roles(user: dict = Depends(get_current_user)):
    """列出当前内核识别到的可用runtime角色（来自role包）。"""
    role_ctx = build_runtime_role_context()

    available_roles = [
        str(x).strip().lower()
        for x in (role_ctx.get("runtime_roles") or [])
        if str(x).strip()
    ]
    default_role = str(role_ctx.get("default_role") or "ops").strip().lower() or "ops"

    label_map = role_ctx.get("label_map") if isinstance(role_ctx.get("label_map"), dict) else {}
    role_packages = role_ctx.get("role_packages") if isinstance(role_ctx.get("role_packages"), dict) else {}

    roles: List[Dict[str, Any]] = []
    for runtime_role in available_roles:
        candidates = role_packages.get(runtime_role) if isinstance(role_packages.get(runtime_role), list) else []
        first = candidates[0] if candidates else {}

        domains: List[str] = []
        keywords: List[str] = []
        package_ids: List[str] = []

        for pkg in candidates:
            if not isinstance(pkg, dict):
                continue
            pid = str(pkg.get("id") or "").strip()
            if pid and pid not in package_ids:
                package_ids.append(pid)

            for domain in pkg.get("domains") if isinstance(pkg.get("domains"), list) else []:
                d = str(domain).strip()
                if d and d not in domains:
                    domains.append(d)

            for keyword in pkg.get("keywords") if isinstance(pkg.get("keywords"), list) else []:
                k = str(keyword).strip()
                if k and k not in keywords:
                    keywords.append(k)

        roles.append(
            {
                "runtime_role": runtime_role,
                "id": str(first.get("id") or ""),
                "name": str(label_map.get(runtime_role) or first.get("name") or runtime_role),
                "is_default": runtime_role == default_role,
                "package_count": len(candidates),
                "package_ids": package_ids,
                "domains": domains,
                "keywords": keywords,
            }
        )

    return {
        "roles": roles,
        "count": len(roles),
        "default_role": default_role,
    }


@router.post("/runtime-profile")
async def get_kernel_runtime_profile(
    body: KernelRuntimeProfileRequest,
    user: dict = Depends(get_current_user),
):
    """生成内核通用运行时画像（领域 + 角色 + 能力匹配 + 包锁预览）。"""
    from src.core.domain_router import classify_domain, load_domain_catalog
    from src.services.package_runtime import build_execution_package_lock, build_role_capability_matrix

    message = body.message.strip()

    role_norm = _normalize_runtime_roles(body.runtime_roles)
    normalized_roles = role_norm["runtime_roles"]
    alias_map = role_norm["alias_map"]

    # 若用户未指定角色，按message建议
    if (not body.runtime_roles) and message:
        domain_guess = classify_domain(message)
        suggested = suggest_roles(message, domain_guess.domain_id, max_roles=body.max_roles)
        resolved: List[str] = []
        available_role_set = set(role_norm.get("available_roles") or [])
        for role in suggested:
            mapped = _resolve_runtime_role(str(role), alias_map=alias_map, available_role_set=available_role_set)
            if mapped and mapped not in resolved:
                resolved.append(mapped)
        if resolved:
            normalized_roles = resolved[: body.max_roles]

    if not normalized_roles:
        normalized_roles = [str(role_norm.get("default_role") or "ops")]

    # 二次保护：统一归一化，避免上下文切换时出现脏值
    role_ctx = role_norm.get("role_ctx") if isinstance(role_norm.get("role_ctx"), dict) else build_runtime_role_context()
    stable_roles: List[str] = []
    for role in normalized_roles:
        norm = normalize_runtime_role(role, context=role_ctx)
        if norm not in stable_roles:
            stable_roles.append(norm)
    normalized_roles = stable_roles or [str(role_norm.get("default_role") or "ops")]

    # 领域识别/覆盖
    if body.domain_hint.strip():
        domain_id = body.domain_hint.strip()
        known_domains = {x.get("id") for x in load_domain_catalog()}
        if domain_id not in known_domains and message:
            domain_id = classify_domain(message).domain_id
        elif domain_id not in known_domains:
            domain_id = "domain.general"
    else:
        domain_id = classify_domain(message).domain_id if message else "domain.general"

    capability_match = build_role_capability_matrix(normalized_roles, include_disabled=True)
    package_lock_preview = build_execution_package_lock(
        goal=message or "kernel_runtime_profile",
        runtime_roles=normalized_roles,
        domain_id=domain_id,
    )

    return {
        "message": message,
        "domain_id": domain_id,
        "runtime_roles": normalized_roles,
        "capability_match": capability_match,
        "package_lock_preview": package_lock_preview,
    }


@router.get("/generalization-status")
async def get_kernel_generalization_status(user: dict = Depends(get_current_user)):
    """内核通用化体检：岗位/技能/能力包覆盖与热插拔就绪度。"""
    return _build_generalization_snapshot()


@router.get("/generalization-fixes")
async def get_kernel_generalization_fixes(user: dict = Depends(get_current_user)):
    """返回通用化体检的可执行修复动作清单。"""
    snapshot = _build_generalization_snapshot()
    return {
        "ready": bool(snapshot.get("ready")),
        "maturity_score": float(snapshot.get("maturity_score") or 0.0),
        "recommendation_count": len(snapshot.get("recommendations") or []),
        "autofix_action_count": len(snapshot.get("autofix_actions") or []),
        "recommendations": snapshot.get("recommendations") or [],
        "autofix_actions": snapshot.get("autofix_actions") or [],
    }



@router.post("/generalization-fixes/apply")
async def apply_kernel_generalization_fixes(
    body: KernelFixApplyRequest,
    user: dict = Depends(get_current_user),
):
    """执行通用化体检修复动作（含白名单校验与审计日志）。"""
    actions = body.actions or []
    if not actions:
        raise HTTPException(status_code=400, detail="actions不能为空")

    changed_by = int(user.get("id") or 0) if str(user.get("id") or "").strip().isdigit() else user.get("id")
    dry_run = bool(body.dry_run)

    results: List[Dict[str, Any]] = []
    for index, action_model in enumerate(actions):
        action_payload = _to_model_dict(action_model)
        result = await _apply_kernel_fix_action(
            action_payload,
            changed_by=changed_by,
            dry_run=dry_run,
        )
        result["index"] = index
        results.append(result)

        await _log_kernel_fix_apply(
            action_payload=action_payload,
            result_payload=result,
            changed_by=changed_by,
        )

    applied_count = sum(1 for row in results if str(row.get("status") or "") == "applied")
    skipped_count = sum(1 for row in results if str(row.get("status") or "") == "skipped")
    error_count = sum(1 for row in results if str(row.get("status") or "") == "error")

    return {
        "ok": error_count == 0,
        "dry_run": dry_run,
        "count": len(results),
        "applied_count": applied_count,
        "skipped_count": skipped_count,
        "error_count": error_count,
        "results": results,
    }


@router.get("/modules")
async def list_kernel_modules(user: dict = Depends(get_current_user)):
    """列出可用的工作流模块。"""
    # 尝试从pipeline获取实际状态
    modules = list(_BUILT_IN_MODULES)
    try:
        from src.config import (
            ENABLE_QUALITY_CHECK, ENABLE_TRUST_SCORING, ENABLE_LEARNING,
            ENABLE_ALERTS, ENABLE_METRICS,
        )
        flag_map = {
            "quality_checker": ENABLE_QUALITY_CHECK,
            "trust_scorer": ENABLE_TRUST_SCORING,
            "agent_memory": ENABLE_LEARNING,
            "alerts": ENABLE_ALERTS,
        }
        for module in modules:
            if module["name"] in flag_map:
                module["enabled"] = flag_map[module["name"]]
    except Exception:
        pass
    return {"modules": modules}


@router.get("/workspace")
async def get_workspace_kernel_config(user: dict = Depends(get_current_user)):
    """获取Workspace内核配置（pipeline参数）。"""
    config = {
        "max_prompt_chars": 2000,
        "max_feature_injections": 3,
        "max_history_messages": 10,
        "llm_calls_per_message": 1,
        "background_tasks_enabled": True,
        "stream_enabled": True,
        "quality_check_enabled": True,
        "trust_scoring_enabled": True,
        "learning_enabled": True,
        "alerts_enabled": True,
    }

    try:
        from src.config import (
            ENABLE_QUALITY_CHECK, ENABLE_TRUST_SCORING, ENABLE_LEARNING,
            ENABLE_ALERTS, ENABLE_TOOL_USE, ENABLE_WORKSPACE_ENGINE,
            ENABLE_AUTOPILOT, MAX_PROMPT_CHARS, MAX_HISTORY_MESSAGES, FEATURE_BUDGET_MAX,
        )
        config["quality_check_enabled"] = ENABLE_QUALITY_CHECK
        config["trust_scoring_enabled"] = ENABLE_TRUST_SCORING
        config["learning_enabled"] = ENABLE_LEARNING
        config["alerts_enabled"] = ENABLE_ALERTS
        config["tool_use_enabled"] = ENABLE_TOOL_USE
        config["workspace_engine_enabled"] = ENABLE_WORKSPACE_ENGINE
        config["autopilot_enabled"] = ENABLE_AUTOPILOT
        config["max_prompt_chars"] = MAX_PROMPT_CHARS
        config["max_history_messages"] = MAX_HISTORY_MESSAGES
        config["max_feature_injections"] = FEATURE_BUDGET_MAX
    except Exception:
        pass

    return {"kernel_config": config}


@router.post("/execute")
async def execute_kernel_pipeline(
    body: dict,
    user: dict = Depends(get_current_user),
):
    """直接执行Kernel Pipeline（高级API）。

    body参数:
    - message: str — 输入消息（必填）
    - role: str — Agent角色（可选）
    - conversation_id: str — 会话ID（可选）
    - product_id: int — 产品上下文（可选）
    - workspace_id: int — 工作区上下文（可选）
    - stream: bool — 是否流式（默认false）
    """
    message = body.get("message", "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message不能为空")

    role = body.get("role")
    product_id = body.get("product_id")
    workspace_id = body.get("workspace_id")

    import uuid as _uuid
    conversation_id = body.get("conversation_id") or str(_uuid.uuid4())

    from src.core.chat_pipeline import chat_simple
    result = await chat_simple(
        message=message,
        user_id=user["id"],
        role=role,
        conversation_id=conversation_id,
        product_id=product_id,
        workspace_id=workspace_id,
    )

    return {
        "conversation_id": conversation_id,
        "reply": result.get("reply", ""),
        "role": result.get("role", ""),
        "skill_used": result.get("skill_used"),
        "metadata": result.get("metadata", {}),
    }
