from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Dict, List

from src.config import PROJECT_ROOT

logger = logging.getLogger(__name__)


_SECTION_ORDER = {
    "product": 10,
    "tools": 20,
    "system": 30,
}

_DEFAULT_CATALOG: List[Dict[str, Any]] = [
    {
        "id": "cap.board",
        "label": "产品看板",
        "route": "board",
        "icon": "dashboard",
        "section": "product",
        "order": 10,
        "status": "active",
        "renderer": {"module": "./pages/board.js", "export": "renderBoard"},
    },
    {
        "id": "cap.campaigns",
        "label": "营销活动",
        "route": "campaigns",
        "icon": "campaign",
        "section": "product",
        "order": 20,
        "status": "active",
        "renderer": {"module": "./pages/campaign-view.js", "export": "renderCampaignView"},
    },
    {
        "id": "cap.platform",
        "label": "平台连接",
        "route": "platform",
        "icon": "hub",
        "section": "product",
        "order": 30,
        "status": "active",
        "renderer": {"module": "./pages/platform-connect.js", "export": "renderPlatformConnect"},
    },
    {
        "id": "cap.briefing",
        "label": "每日简报",
        "route": "briefing",
        "icon": "event",
        "section": "tools",
        "order": 10,
        "status": "active",
        "renderer": {"module": "./pages/daily-briefing.js", "export": "renderDailyBriefing"},
    },
    {
        "id": "cap.knowledge",
        "label": "智能洞察",
        "route": "knowledge",
        "icon": "psychology",
        "section": "tools",
        "order": 20,
        "status": "active",
        "renderer": {"module": "./pages/knowledge-browser.js", "export": "renderKnowledgeBrowser"},
    },
    {
        "id": "cap.history",
        "label": "对话历史",
        "route": "history",
        "icon": "history",
        "section": "tools",
        "order": 30,
        "status": "active",
        "renderer": {"module": "./pages/conversation-history.js", "export": "renderConversationHistory"},
    },
    {
        "id": "cap.dashboard",
        "label": "系统看板",
        "route": "dashboard",
        "icon": "insights",
        "section": "tools",
        "order": 40,
        "status": "active",
        "renderer": {"module": "./pages/dashboard.js", "export": "renderDashboard"},
    },
    {
        "id": "cap.packages",
        "label": "通用化管理",
        "route": "packages",
        "icon": "deployed_code",
        "section": "tools",
        "order": 45,
        "status": "active",
        "renderer": {"module": "./pages/packages-center.js", "export": "renderPackagesCenter"},
    },
    {
        "id": "cap.agents",
        "label": "AI 团队介绍",
        "route": "agents",
        "icon": "smart_toy",
        "section": "tools",
        "order": 50,
        "status": "active",
        "renderer": {"module": "./pages/agents-showcase.js", "export": "renderAgentsShowcase"},
    },
    {
        "id": "cap.alerts",
        "label": "预警中心",
        "route": "alerts",
        "icon": "notifications",
        "section": "tools",
        "order": 60,
        "status": "active",
        "renderer": {"module": "./pages/alerts-center.js", "export": "renderAlertsCenter"},
    },
]


def _capability_manifest_root() -> Path:
    env_root = os.getenv("CAPABILITY_MANIFEST_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser().resolve()
    # repo/client/src/capabilities
    return (PROJECT_ROOT.parent / "client" / "src" / "capabilities").resolve()


def _manifest_paths(root: Path) -> List[Path]:
    if not root.exists():
        return []
    return sorted({p.resolve() for p in root.rglob("manifest.json")})


def _read_json(path: Path) -> Dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Capability manifest read failed %s: %s", path, exc)
        return None

    if not isinstance(data, dict):
        logger.warning("Capability manifest should be object: %s", path)
        return None
    return data


def _normalize_requires(data: Dict[str, Any]) -> Dict[str, List[str]]:
    req = data.get("requires")
    if not isinstance(req, dict):
        return {"skills": [], "roles": [], "feature_flags": []}

    normalized: Dict[str, List[str]] = {}
    for key in ("skills", "roles", "feature_flags"):
        raw_value = req.get(key, [])
        if isinstance(raw_value, list):
            normalized[key] = [str(x).strip() for x in raw_value if str(x).strip()]
        else:
            normalized[key] = []
    return normalized


def _normalize_renderer(data: Dict[str, Any]) -> Dict[str, str]:
    renderer = data.get("renderer")
    if not isinstance(renderer, dict):
        return {}

    module = str(renderer.get("module") or "").strip()
    export_name = str(renderer.get("export") or "").strip()

    # 仅允许前端 pages 目录内模块，防止 manifest 注入任意 import。
    if not module.startswith("./pages/") or ".." in module or not module.endswith(".js"):
        return {}

    if export_name and not re.match(r"^[A-Za-z_$][A-Za-z0-9_$]*$", export_name):
        return {}

    payload: Dict[str, str] = {"module": module}
    if export_name:
        payload["export"] = export_name
    return payload


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
    return dict(mapping.get(route_key) or {})


def _normalize_manifest(data: Dict[str, Any], path: Path) -> Dict[str, Any] | None:
    route = str(data.get("route", "")).strip()
    label = str(data.get("label", "")).strip()
    if not route or not label:
        logger.warning("Capability manifest missing route/label: %s", path)
        return None

    cap_id = str(data.get("id") or f"cap.{route}").strip()
    section = str(data.get("section") or "tools").strip().lower()
    if section not in _SECTION_ORDER:
        section = "tools"

    try:
        order = int(data.get("order", 999))
    except Exception:
        order = 999

    status = str(data.get("status") or "active").strip().lower()
    if status in {"disabled", "inactive", "off"}:
        return None

    normalized = {
        "id": cap_id,
        "label": label,
        "route": route,
        "icon": str(data.get("icon") or "apps").strip() or "apps",
        "section": section,
        "order": order,
        "status": "active",
        "description": str(data.get("description") or "").strip(),
        "requires": _normalize_requires(data),
        "source": str(path),
    }

    renderer = _normalize_renderer(data)
    if not renderer:
        renderer = _default_renderer_for_route(route)
    if renderer:
        normalized["renderer"] = renderer

    return normalized


def _dedupe_and_sort(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: set[str] = set()
    deduped: List[Dict[str, Any]] = []

    for item in items:
        key = f"{item.get('id', '')}::{item.get('route', '')}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    deduped.sort(
        key=lambda x: (
            _SECTION_ORDER.get(str(x.get("section", "tools")), 99),
            int(x.get("order", 999)),
            str(x.get("label", "")),
        )
    )
    return deduped


def load_capability_catalog() -> List[Dict[str, Any]]:
    root = _capability_manifest_root()
    normalized: List[Dict[str, Any]] = []

    for path in _manifest_paths(root):
        data = _read_json(path)
        if not data:
            continue
        item = _normalize_manifest(data, path)
        if item:
            normalized.append(item)

    if not normalized:
        # fallback for startup safety
        normalized = [dict(x) for x in _DEFAULT_CATALOG]

    return _dedupe_and_sort(normalized)


def build_catalog_payload() -> Dict[str, Any]:
    catalog = load_capability_catalog()
    body_text = json.dumps(catalog, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    revision = hashlib.sha1(body_text.encode("utf-8")).hexdigest()[:12]

    sections: Dict[str, List[Dict[str, Any]]] = {"product": [], "tools": [], "system": []}
    for item in catalog:
        sections.setdefault(item["section"], []).append(item)

    return {
        "catalog": catalog,
        "sections": [
            {"id": section_id, "items": sections.get(section_id, [])}
            for section_id in ("product", "tools", "system")
            if sections.get(section_id)
        ],
        "count": len(catalog),
        "revision": revision,
        "source_root": str(_capability_manifest_root()),
    }
