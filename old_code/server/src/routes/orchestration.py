"""通用编排预览路由：外层领域识别 + 内层岗位编排。"""

from __future__ import annotations

from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from src.core.domain_router import classify_domain, load_domain_catalog
from src.core.intent import analyze_intent
from src.core.role_router import build_runtime_role_context, suggest_roles
from src.routes.auth import get_current_user
from src.services.package_runtime import build_package_catalog, build_role_capability_matrix

router = APIRouter(prefix="/api/orchestration", tags=["orchestration"])

_FALLBACK_ROLE_LABELS = {
    "ops": "运营策略",
    "data": "数据分析",
    "service": "客户服务",
    "design": "视觉设计",
    "accounting": "财务分析",
    "engineering": "技术架构",
    "web": "增长与SEO",
    "creative": "内容创意",
}


class OrchestrationPreviewRequest(BaseModel):
    message: str = Field(..., description="用户目标描述")
    hired_roles: List[str] = Field(default_factory=list, description="用户手工指定的岗位组合")
    domain_hint: str = Field(default="", description="可选领域提示")
    max_support_roles: int = Field(default=3, ge=1, le=6)
    include_candidates: bool = Field(default=True)


def _normalize_role_name(value: str, role_ctx: Dict[str, Any], *, allow_default: bool = False) -> str:
    raw = str(value or "").strip().lower()
    runtime_roles = {
        str(x).strip().lower()
        for x in (role_ctx.get("runtime_roles") or [])
        if str(x).strip()
    }
    default_role = str(role_ctx.get("default_role") or "ops").strip().lower() or "ops"
    alias_map = role_ctx.get("alias_map") if isinstance(role_ctx.get("alias_map"), dict) else {}

    if not raw:
        return default_role if allow_default else ""
    if raw in runtime_roles:
        return raw

    mapped = str(alias_map.get(raw) or "").strip().lower()
    if mapped and mapped in runtime_roles:
        return mapped

    return default_role if allow_default else ""


def _build_role_package_map(context: Dict[str, Any]) -> Dict[str, Dict[str, str]]:
    raw_map = context.get("role_packages")
    if not isinstance(raw_map, dict):
        return {}

    role_packages: Dict[str, Dict[str, str]] = {}
    for runtime_role, candidates in raw_map.items():
        if not isinstance(candidates, list) or not candidates:
            continue
        first = candidates[0] if isinstance(candidates[0], dict) else {}
        role_packages[str(runtime_role)] = {
            "package_id": str(first.get("id") or ""),
            "package_name": str(first.get("name") or runtime_role),
        }
    return role_packages


def _build_skill_package_map(alias_map: Dict[str, str]) -> Dict[str, List[Dict[str, str]]]:
    catalog = build_package_catalog(include_disabled=False)
    mapping: Dict[str, List[Dict[str, str]]] = {}

    for section in catalog.get("sections") or []:
        if section.get("type") != "skill":
            continue
        for item in section.get("items") or []:
            skill_item = {
                "id": str(item.get("id") or ""),
                "name": str(item.get("name") or ""),
                "status": str(item.get("status") or "active"),
            }
            provided_roles = item.get("provided_roles") if isinstance(item.get("provided_roles"), list) else []
            normalized_roles = [
                str(alias_map.get(str(x).strip().lower()) or str(x).strip().lower())
                for x in provided_roles
                if str(x).strip()
            ]
            normalized_roles = [x for x in normalized_roles if x]

            if not normalized_roles:
                sid = skill_item.get("id") or ""
                if sid.startswith("builtin."):
                    role = sid.removeprefix("builtin.").split(".", 1)[0].strip().lower()
                    role = str(alias_map.get(role) or role)
                    if role:
                        normalized_roles = [role]

            for role in normalized_roles:
                mapping.setdefault(role, []).append(skill_item)

    return mapping


@router.post("/preview")
async def orchestration_preview(
    body: OrchestrationPreviewRequest,
    user: dict = Depends(get_current_user),
):
    message = body.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="message不能为空")

    # 外层领域识别
    domain_match = classify_domain(message)
    if body.domain_hint.strip():
        hinted = body.domain_hint.strip()
        known_domains = {x.get("id") for x in load_domain_catalog()}
        if hinted in known_domains:
            domain_match.domain_id = hinted

    role_ctx = build_runtime_role_context()
    alias_map = role_ctx.get("alias_map") if isinstance(role_ctx.get("alias_map"), dict) else {}
    role_labels = role_ctx.get("label_map") if isinstance(role_ctx.get("label_map"), dict) else {}
    for role, label in _FALLBACK_ROLE_LABELS.items():
        role_labels.setdefault(role, label)

    default_role = str(role_ctx.get("default_role") or "ops")

    # 内层岗位意图与建议
    intent = analyze_intent(message)
    suggested_roles_raw = suggest_roles(message, domain_match.domain_id, max_roles=body.max_support_roles + 1)

    suggested_roles: List[str] = []
    for role in suggested_roles_raw:
        norm = _normalize_role_name(role, role_ctx)
        if norm and norm not in suggested_roles:
            suggested_roles.append(norm)

    hired_roles: List[str] = []
    for role in body.hired_roles:
        norm = _normalize_role_name(role, role_ctx)
        if norm and norm not in hired_roles:
            hired_roles.append(norm)

    # primary/support 合并策略：主岗位优先意图，再吸收用户雇佣和router建议
    primary_role = _normalize_role_name(intent.primary_role, role_ctx)
    if not primary_role and suggested_roles:
        primary_role = suggested_roles[0]
    if not primary_role:
        primary_role = default_role

    support_roles: List[str] = []
    for role in intent.support_roles + hired_roles + suggested_roles:
        norm = _normalize_role_name(role, role_ctx)
        if not norm or norm == primary_role:
            continue
        if norm not in support_roles:
            support_roles.append(norm)
    support_roles = support_roles[: body.max_support_roles]

    role_pack_map = _build_role_package_map(role_ctx)
    skill_pack_map = _build_skill_package_map(alias_map=alias_map)

    orchestration_roles = [primary_role] + support_roles
    capability_match = build_role_capability_matrix(orchestration_roles, include_disabled=True)

    # 图结构：input -> domain -> primary -> supports -> output
    nodes = [
        {"id": "input", "kind": "input", "label": "用户目标"},
        {
            "id": f"domain:{domain_match.domain_id}",
            "kind": "domain",
            "label": domain_match.domain_id,
            "confidence": domain_match.confidence,
        },
        {
            "id": f"role:{primary_role}",
            "kind": "role",
            "label": str(role_labels.get(primary_role) or primary_role),
            "is_primary": True,
            "package": role_pack_map.get(primary_role, {}),
            "skills": skill_pack_map.get(primary_role, []),
        },
        {"id": "output", "kind": "output", "label": "执行闭环"},
    ]

    edges = [
        {"from": "input", "to": f"domain:{domain_match.domain_id}", "label": "领域判定"},
        {"from": f"domain:{domain_match.domain_id}", "to": f"role:{primary_role}", "label": "主岗位路由"},
    ]

    for role in support_roles:
        node_id = f"role:{role}"
        nodes.append(
            {
                "id": node_id,
                "kind": "role",
                "label": str(role_labels.get(role) or role),
                "is_primary": False,
                "package": role_pack_map.get(role, {}),
                "skills": skill_pack_map.get(role, []),
            }
        )
        edges.append({"from": f"role:{primary_role}", "to": node_id, "label": "并行协作"})
        edges.append({"from": node_id, "to": "output", "label": "结果回传"})

    edges.append({"from": f"role:{primary_role}", "to": "output", "label": "汇总决策"})

    stages = [
        {
            "name": "Domain Routing",
            "description": "外层识别行业领域",
            "domain_id": domain_match.domain_id,
            "matched_keywords": domain_match.matched_keywords,
        },
        {
            "name": "Role Orchestration",
            "description": "内层角色编排与任务分发",
            "primary_role": primary_role,
            "support_roles": support_roles,
            "action": intent.action,
            "tier": intent.tier,
        },
        {
            "name": "Execution Loop",
            "description": "多角色执行结果回流并形成闭环",
            "expected_output": "可执行方案 + 风险提示 + 下一步动作",
        },
    ]

    payload = {
        "message": message,
        "domain": {
            "id": domain_match.domain_id,
            "confidence": domain_match.confidence,
            "matched_keywords": domain_match.matched_keywords,
            "candidates": domain_match.candidates if body.include_candidates else [],
        },
        "routing": {
            "primary_role": primary_role,
            "support_roles": support_roles,
            "hired_roles": hired_roles,
            "suggested_roles": suggested_roles,
            "action": intent.action,
            "tier": intent.tier,
            "confidence": intent.confidence,
        },
        "graph": {
            "nodes": nodes,
            "edges": edges,
            "stages": stages,
        },
        "capability_match": capability_match,
    }

    return payload
