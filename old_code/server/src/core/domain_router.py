"""领域路由器：外层识别用户问题所属行业域。"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from src.config import PROJECT_ROOT

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None

logger = logging.getLogger(__name__)


@dataclass
class DomainMatch:
    domain_id: str
    confidence: float
    matched_keywords: List[str]
    candidates: List[Dict[str, Any]]


def _domain_root() -> Path:
    return (PROJECT_ROOT / "packages" / "domains").resolve()


def _manifest_paths() -> List[Path]:
    root = _domain_root()
    if not root.exists():
        return []
    paths = list(root.rglob("manifest.yaml")) + list(root.rglob("manifest.yml")) + list(root.rglob("manifest.json"))
    return sorted({p.resolve() for p in paths})


def _read_manifest(path: Path) -> Dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Read domain manifest failed: %s (%s)", path, exc)
        return None

    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    if yaml is None:
        return None

    try:
        parsed = yaml.safe_load(raw)
    except Exception as exc:
        logger.warning("Parse domain manifest failed: %s (%s)", path, exc)
        return None

    if isinstance(parsed, dict):
        return parsed
    return None


def load_domain_catalog() -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for path in _manifest_paths():
        data = _read_manifest(path)
        if not data:
            continue

        status = str(data.get("status") or "active").strip().lower()
        enabled = bool(data.get("enabled", True))
        if not enabled or status in {"disabled", "inactive", "off"}:
            continue

        domain_id = str(data.get("id") or f"domain.{path.parent.name}").strip()
        name = str(data.get("name") or domain_id).strip()
        keywords = data.get("keywords") if isinstance(data.get("keywords"), list) else []
        preferred_roles = data.get("preferred_roles") if isinstance(data.get("preferred_roles"), list) else []

        try:
            priority = int(data.get("priority", 100))
        except Exception:
            priority = 100

        items.append(
            {
                "id": domain_id,
                "name": name,
                "keywords": [str(x).strip() for x in keywords if str(x).strip()],
                "preferred_roles": [str(x).strip() for x in preferred_roles if str(x).strip()],
                "prompt_profile": str(data.get("prompt_profile") or "").strip(),
                "priority": priority,
                "manifest_path": str(path),
            }
        )

    items.sort(key=lambda x: (int(x.get("priority", 100)), str(x.get("id", ""))))
    return items


def classify_domain(message: str, top_k: int = 3) -> DomainMatch:
    text = (message or "").lower()
    catalog = load_domain_catalog()

    if not catalog:
        return DomainMatch(
            domain_id="domain.general",
            confidence=0.3,
            matched_keywords=[],
            candidates=[{"id": "domain.general", "name": "通用", "score": 0.0}],
        )

    scored: List[Dict[str, Any]] = []
    for item in catalog:
        keywords = item.get("keywords", [])
        matched = [kw for kw in keywords if kw.lower() in text]
        score = float(len(matched))
        scored.append(
            {
                "id": item["id"],
                "name": item.get("name", item["id"]),
                "score": score,
                "matched_keywords": matched,
                "preferred_roles": item.get("preferred_roles", []),
            }
        )

    scored.sort(key=lambda x: (x["score"], x["id"]), reverse=True)
    best = scored[0]

    # 无命中时优先 general 域
    if best["score"] <= 0:
        for item in scored:
            if str(item["id"]).endswith("general"):
                best = item
                break

    total = sum(float(x["score"]) for x in scored) or 1.0
    confidence = round(min(max(float(best["score"]) / total, 0.2), 0.98), 2)

    candidates = [
        {
            "id": item["id"],
            "name": item["name"],
            "score": round(float(item["score"]), 3),
        }
        for item in scored[: max(1, top_k)]
    ]

    return DomainMatch(
        domain_id=str(best["id"]),
        confidence=confidence,
        matched_keywords=list(best.get("matched_keywords") or []),
        candidates=candidates,
    )
