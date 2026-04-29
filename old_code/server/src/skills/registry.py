from __future__ import annotations

import importlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .base import SkillBase

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None

logger = logging.getLogger(__name__)


@dataclass
class SkillPackManifest:
    pack_id: str
    name: str
    version: str
    module: str
    attr: str
    status: str
    manifest_path: Path
    provided_roles: List[str]


class SkillRegistry:
    """Singleton registry for all skills."""

    def __init__(self) -> None:
        self._skills: Dict[str, SkillBase] = {}
        self._skill_sources: Dict[str, str] = {}
        self._skill_packs: List[Dict[str, Any]] = []
        self._skill_role_hints: Dict[str, List[str]] = {}
        self._initialized: bool = False

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            self._init_all_skills()
            self._initialized = True

    def _init_all_skills(self) -> None:
        """Load skill packs from manifests first; fallback to legacy hard-coded modules."""
        loaded_count = self._init_from_manifests()
        if loaded_count > 0:
            logger.info("Loaded %d skills from manifest packs", loaded_count)
            return

        logger.warning("Skill manifest loading skipped/empty, fallback to legacy hard-coded skill imports")
        self._init_legacy_builtin_skills()

    def _manifest_root(self) -> Path:
        env_value = os.getenv("SKILL_MANIFEST_ROOT", "").strip()
        if env_value:
            return Path(env_value).expanduser().resolve()
        # repo/server/packages/skills
        return (Path(__file__).resolve().parents[2] / "packages" / "skills").resolve()

    def _iter_manifest_paths(self) -> List[Path]:
        root = self._manifest_root()
        if not root.exists():
            return []
        paths = list(root.rglob("manifest.yaml")) + list(root.rglob("manifest.yml"))
        return sorted({p.resolve() for p in paths})

    def _read_manifest_data(self, path: Path) -> Optional[Dict[str, Any]]:
        try:
            raw = path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning("Failed to read skill manifest %s: %s", path, exc)
            return None

        # JSON is valid YAML; this makes manifest loading dependency-light.
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                return data
        except Exception:
            pass

        if yaml is None:
            logger.warning(
                "Skill manifest %s is not JSON and PyYAML is unavailable; skipped",
                path,
            )
            return None

        try:
            parsed = yaml.safe_load(raw)
            if isinstance(parsed, dict):
                return parsed
            logger.warning("Skill manifest %s parsed but not a mapping", path)
        except Exception as exc:
            logger.warning("Failed to parse skill manifest %s: %s", path, exc)
        return None

    def _normalize_provided_roles(self, value: Any) -> List[str]:
        if not isinstance(value, list):
            return []

        role_tokens = [str(x).strip().lower() for x in value if str(x).strip()]
        if not role_tokens:
            return []

        normalized: List[str] = []
        seen: set[str] = set()

        try:
            from src.core.role_router import build_runtime_role_context, normalize_runtime_role

            role_ctx = build_runtime_role_context()
            for token in role_tokens:
                mapped = normalize_runtime_role(token, context=role_ctx)
                if mapped and mapped not in seen:
                    seen.add(mapped)
                    normalized.append(mapped)
        except Exception:
            for token in role_tokens:
                if token not in seen:
                    seen.add(token)
                    normalized.append(token)

        return normalized

    def _parse_manifest(self, path: Path) -> Optional[SkillPackManifest]:
        data = self._read_manifest_data(path)
        if not data:
            return None

        status = str(data.get("status", "active")).strip().lower()
        enabled = bool(data.get("enabled", True))
        if not enabled or status in {"disabled", "inactive", "off"}:
            return None

        entrypoint = data.get("entrypoint") or {}
        if not isinstance(entrypoint, dict):
            logger.warning("Skill manifest %s missing valid entrypoint mapping", path)
            return None

        module = str(entrypoint.get("module", "")).strip()
        attr = str(entrypoint.get("attr", "ALL_SKILLS")).strip() or "ALL_SKILLS"
        if not module:
            logger.warning("Skill manifest %s missing entrypoint.module", path)
            return None

        pack_id = str(data.get("id") or path.parent.name).strip()
        name = str(data.get("name") or pack_id).strip()
        version = str(data.get("version") or "0.1.0").strip()
        provided_roles = self._normalize_provided_roles(data.get("provided_roles"))
        return SkillPackManifest(
            pack_id=pack_id,
            name=name,
            version=version,
            module=module,
            attr=attr,
            status=status,
            manifest_path=path,
            provided_roles=provided_roles,
        )

    def _normalize_exported_skills(self, exported: Any, manifest: SkillPackManifest) -> List[SkillBase]:
        if callable(exported):
            try:
                exported = exported()
            except Exception as exc:
                logger.warning("Skill pack %s callable export failed: %s", manifest.pack_id, exc)
                return []

        if not isinstance(exported, (list, tuple)):
            logger.warning(
                "Skill pack %s export '%s' should be list/tuple, got %s",
                manifest.pack_id,
                manifest.attr,
                type(exported).__name__,
            )
            return []

        normalized: List[SkillBase] = []
        for idx, item in enumerate(exported):
            if isinstance(item, SkillBase):
                normalized.append(item)
                continue

            if isinstance(item, type) and issubclass(item, SkillBase):
                try:
                    normalized.append(item())
                except Exception as exc:
                    logger.warning(
                        "Skill pack %s failed to instantiate skill class %s at index %d: %s",
                        manifest.pack_id,
                        item.__name__,
                        idx,
                        exc,
                    )
                continue

            logger.warning(
                "Skill pack %s exported invalid skill item at index %d (%s)",
                manifest.pack_id,
                idx,
                type(item).__name__,
            )

        return normalized

    def _load_skills_from_manifest(self, manifest: SkillPackManifest) -> List[SkillBase]:
        try:
            module = importlib.import_module(manifest.module)
        except Exception as exc:
            logger.warning("Skill pack %s module import failed (%s): %s", manifest.pack_id, manifest.module, exc)
            return []

        if not hasattr(module, manifest.attr):
            logger.warning(
                "Skill pack %s module %s missing export '%s'",
                manifest.pack_id,
                manifest.module,
                manifest.attr,
            )
            return []

        exported = getattr(module, manifest.attr)
        return self._normalize_exported_skills(exported, manifest)

    def _init_from_manifests(self) -> int:
        loaded_total = 0
        manifests = self._iter_manifest_paths()
        if not manifests:
            return 0

        for path in manifests:
            manifest = self._parse_manifest(path)
            if not manifest:
                continue

            skills = self._load_skills_from_manifest(manifest)
            if not skills:
                continue

            for skill in skills:
                self.register(skill, source_pack=manifest.pack_id)
                if manifest.provided_roles:
                    self._skill_role_hints[skill.name] = list(manifest.provided_roles)

            self._skill_packs.append(
                {
                    "id": manifest.pack_id,
                    "name": manifest.name,
                    "version": manifest.version,
                    "status": manifest.status,
                    "module": manifest.module,
                    "attr": manifest.attr,
                    "manifest_path": str(manifest.manifest_path),
                    "skill_count": len(skills),
                    "provided_roles": list(manifest.provided_roles),
                }
            )
            loaded_total += len(skills)

        return loaded_total

    def _iter_legacy_skill_lists(self) -> Iterable[List[SkillBase]]:
        from .ops import ALL_SKILLS as ops_skills
        from .data_analysis import ALL_SKILLS as data_skills
        from .customer_service import ALL_SKILLS as service_skills
        from .design import ALL_SKILLS as design_skills
        from .accounting import ALL_SKILLS as accounting_skills
        from .engineering import ALL_SKILLS as engineering_skills
        from .web import ALL_SKILLS as web_skills
        from .creative import ALL_SKILLS as creative_skills
        from .coordination import ALL_SKILLS as coordination_skills
        from .search import ALL_SKILLS as search_skills

        return (
            ops_skills,
            data_skills,
            service_skills,
            design_skills,
            accounting_skills,
            engineering_skills,
            web_skills,
            creative_skills,
            coordination_skills,
            search_skills,
        )

    def _init_legacy_builtin_skills(self) -> None:
        for skill_list in self._iter_legacy_skill_lists():
            for skill in skill_list:
                self.register(skill, source_pack="legacy_builtin")

    def register(self, skill: SkillBase, source_pack: str = "unknown") -> None:
        existing = self._skills.get(skill.name)
        if existing is not None and existing is not skill:
            logger.warning(
                "Skill name collision '%s': %s -> %s",
                skill.name,
                self._skill_sources.get(skill.name, "unknown"),
                source_pack,
            )

        self._skills[skill.name] = skill
        self._skill_sources[skill.name] = source_pack

    def list_all(self) -> List[SkillBase]:
        self._ensure_initialized()
        return list(self._skills.values())
    def reload(self) -> Dict[str, Any]:
        """Force reload all skills from manifests/fallback without restarting server."""
        self._skills.clear()
        self._skill_sources.clear()
        self._skill_packs.clear()
        self._skill_role_hints.clear()
        self._initialized = False
        self._ensure_initialized()
        return {
            "skill_count": len(self._skills),
            "pack_count": len(self._skill_packs),
        }

    def list_skill_packs(self) -> List[Dict[str, Any]]:
        self._ensure_initialized()
        return list(self._skill_packs)

    def get_skill_source(self, skill_name: str) -> str:
        self._ensure_initialized()
        return self._skill_sources.get(skill_name, "unknown")

    def get_tools_for_role(self, role: str) -> List[Dict[str, Any]]:
        """Return tool_spec dicts for *role* + coordination + search tools (globally available)."""
        self._ensure_initialized()

        role_key = str(role or "").strip().lower() or "ops"
        try:
            from src.core.role_router import build_runtime_role_context

            ctx = build_runtime_role_context()
            alias_map = ctx.get("alias_map") if isinstance(ctx.get("alias_map"), dict) else {}
            mapped = str(alias_map.get(role_key) or "").strip().lower()
            if mapped:
                role_key = mapped
        except Exception:
            pass

        return [
            s.to_tool_spec()
            for s in self._skills.values()
            if (
                s.category == role_key
                or role_key in (self._skill_role_hints.get(s.name) or [])
                or s.category == "coordination"
                or s.category == "search"
            )
        ]

    def build_role_skill_matrix(self, runtime_roles: Optional[List[str]] = None) -> Dict[str, Any]:
        """Build role -> skill coverage matrix for generalized kernel diagnostics."""
        self._ensure_initialized()

        roles: List[str] = []
        if isinstance(runtime_roles, list):
            roles = [str(x).strip().lower() for x in runtime_roles if str(x).strip()]

        if not roles:
            role_set: set[str] = set()
            for skill in self._skills.values():
                if skill.category and skill.category not in {"coordination", "search"}:
                    role_set.add(str(skill.category).strip().lower())
                for hinted in (self._skill_role_hints.get(skill.name) or []):
                    role_set.add(str(hinted).strip().lower())
            roles = sorted(role_set)

        rows: List[Dict[str, Any]] = []
        uncovered: List[str] = []

        for role in roles:
            role_skills: List[str] = []
            for skill in self._skills.values():
                hinted_roles = self._skill_role_hints.get(skill.name) or []
                if str(skill.category).strip().lower() == role or role in hinted_roles:
                    role_skills.append(skill.name)

            role_skills = sorted({x for x in role_skills if x})
            if not role_skills:
                uncovered.append(role)

            rows.append(
                {
                    "runtime_role": role,
                    "skill_count": len(role_skills),
                    "skills": role_skills,
                }
            )

        return {
            "roles": rows,
            "uncovered_roles": uncovered,
            "covered_count": len(rows) - len(uncovered),
            "total": len(rows),
        }

    async def execute(
        self,
        name: str,
        args: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        self._ensure_initialized()
        skill = self._skills.get(name)
        if skill is None:
            return {"error": f"Skill '{name}' not found"}

        from src.services.skill_runtime_adapter import execute_skill_runtime

        return await execute_skill_runtime(
            skill,
            skill_name=name,
            args=args,
            context=context,
        )


_registry: Optional[SkillRegistry] = None


def get_registry() -> SkillRegistry:
    """Lazy-init singleton accessor."""
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
    return _registry


