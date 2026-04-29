"""岗位路由器：在领域上下文中推荐主岗位与支持岗位。"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config import PROJECT_ROOT

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None

logger = logging.getLogger(__name__)


_WEAK_ROLE_KEYWORD_WEIGHTS: Dict[str, Dict[str, float]] = {
    "data": {
        "分析": 0.35,
        "analysis": 0.35,
        "analyze": 0.35,
        "诊断": 0.45,
        "评估": 0.45,
        "复盘": 0.45,
        "趋势": 0.45,
    }
}

_MARKETING_HINT_KEYWORDS: List[str] = [
    "营销", "营销方式", "营销策略", "增长策略", "增长方案", "增长计划",
    "获客", "拉新", "促活", "留存", "复购", "活动策划", "渠道策略", "渠道组合",
    "推广", "投放", "投流", "达人", "种草", "内容矩阵", "活动复盘", "活动机制",
    "创意方向", "素材", "起量", "破圈", "转化链路", "转化路径", "流量获取",
    "campaign", "growth", "acquisition", "activation", "retention marketing", "media mix",
    "go-to-market", "gtm", "creative strategy",
]

_DATA_EVIDENCE_KEYWORDS: List[str] = [
    "sql", "报表", "漏斗", "同比", "环比", "统计", "归因",
    "实验", "ab测试", "a/b", "显著性", "置信", "看板", "仪表板", "口径",
    "cohort", "retention", "attribution", "dashboard", "metric", "significance",
    "join", "table", "query", "warehouse",
]

_DATA_MEDIUM_EVIDENCE_KEYWORDS: List[str] = [
    "指标", "roi", "roas", "gmv", "uv", "pv", "ctr", "cvr", "cac", "ltv",
    "点击率", "转化率", "留存率", "客单价", "复购率", "曝光", "消耗", "预算", "人群包",
]

_DATA_WEAK_ANALYSIS_KEYWORDS: List[str] = [
    "分析", "analysis", "analyze", "诊断", "评估", "复盘", "趋势",
]


_STRATEGY_DELIVERY_HINT_KEYWORDS: List[str] = [
    "方案", "策略", "计划", "执行", "落地", "动作", "步骤", "排期", "节奏",
    "优先级", "打法", "拆解", "推进", "路径", "playbook", "roadmap",
]


_GROWTH_GOAL_KEYWORDS: List[str] = [
    "提升", "优化", "增长", "拉新", "促活", "复购", "转化", "转化率", "点击率",
    "roi", "roas", "gmv", "客单价", "投放", "投流", "活动", "渠道", "起量",
]


_DATA_DIAGNOSTIC_HINT_KEYWORDS: List[str] = [
    "原因", "根因", "归因", "诊断", "排查", "下滑", "波动", "异常", "拆解",
    "显著性", "置信", "验证", "口径", "sql", "报表", "漏斗", "看板", "dashboard",
]



_DESIGN_CREATIVE_HINT_KEYWORDS: List[str] = [
    "设计", "视觉", "文案", "素材", "主图", "详情页", "海报", "版式", "排版",
    "钩子", "脚本", "封面", "创意", "品牌风格", "色值", "字体",
]


_EXPERIMENT_HINT_KEYWORDS: List[str] = [
    "a/b", "ab测试", "ab 实验", "实验", "测试", "对照组", "测试组",
]


_DATA_STRONG_CONTEXT_HINT_KEYWORDS: List[str] = [
    "sql", "报表", "漏斗", "看板", "仪表板", "口径", "归因", "显著性", "置信", "dashboard",
]

_OPS_EXECUTION_HINT_KEYWORDS: List[str] = [
    "执行", "排期", "节奏", "落地", "推进", "里程碑", "跨团队", "协作安排",
    "大促", "预热", "返场", "上线计划", "执行计划", "跨部门", "周会", "协同", "协同机制",
]


_HINT_TOKEN_STOPWORDS: set[str] = {
    "role", "skill", "skills", "capability", "capabilities", "domain", "general",
    "analysis", "analyze", "plan", "query", "execute", "optimize", "create",
    "strategy", "workflow", "process", "model", "modeling", "monitor", "review",
    "handler", "calc", "ops", "data", "service", "creative", "design", "web",
    "engineering", "finance", "accounting", "方案", "分析", "执行", "优化", "查询", "创建", "策略", "流程",
}


_ROLE_SEMANTIC_HINT_KEYWORDS: Dict[str, List[str]] = {
    "ops": [
        "执行", "排期", "里程碑", "跨团队", "跨部门", "协同", "协作", "大促", "预热", "返场",
        "execution", "roadmap", "milestone", "coordination", "checklist", "playbook",
    ],
    "data": [
        "sql", "cohort", "retention", "funnel", "attribution", "dashboard", "metric",
        "显著性", "归因", "漏斗", "看板", "统计",
    ],
    "design": [
        "视觉", "排版", "版式", "主图", "详情页",
        "design", "redesign", "typography", "wireframe", "layout", "visual", "ui", "ux",
    ],
    "web": [
        "seo", "organic", "ranking", "indexing", "keyword", "serp", "搜索曝光", "收录", "自然流量",
    ],
    "service": [
        "客服", "投诉", "退款", "差评", "话术", "nps", "csat", "sentiment", "after-sales", "support",
    ],
    "accounting": [
        "现金流", "利润", "毛利", "净利", "预算", "成本",
        "cashflow", "profit", "margin", "budget", "pnl", "unit economics",
    ],
    "creative": [
        "文案", "脚本", "选题", "种草", "直播", "短视频",
        "copywriting", "storyboard", "hook", "content idea", "creative",
    ],
    "engineering": [
        "接口", "报错", "故障", "性能", "稳定性",
        "api", "error", "bug", "latency", "deployment", "integration", "crash",
    ],
}


def _extract_hint_tokens(values: List[str]) -> List[str]:
    tokens: List[str] = []
    seen: set[str] = set()

    for raw in values:
        text = str(raw or "").strip().lower()
        if not text:
            continue

        for cjk in re.findall(r"[一-鿿]{2,}", text):
            if cjk not in _HINT_TOKEN_STOPWORDS and cjk not in seen:
                seen.add(cjk)
                tokens.append(cjk)

        parts = [part for part in re.split(r"[^a-z0-9]+", text) if part]
        for part in parts:
            if len(part) < 3 or part in _HINT_TOKEN_STOPWORDS:
                continue
            if part not in seen:
                seen.add(part)
                tokens.append(part)

        if len(parts) >= 2:
            compact = "".join(parts)
            if len(compact) >= 6 and compact not in _HINT_TOKEN_STOPWORDS and compact not in seen:
                seen.add(compact)
                tokens.append(compact)

    return tokens


def _hint_token_hits(values: List[str], text: str) -> float:
    text_lower = str(text or "").lower()
    if not text_lower:
        return 0.0

    tokens = _extract_hint_tokens(values)
    if not tokens:
        return 0.0

    compact_text = re.sub(r"[^a-z0-9一-鿿]+", "", text_lower)

    score = 0.0
    hit_count = 0
    for token in tokens:
        matched = False
        if re.search(r"[一-鿿]", token):
            matched = token in text_lower
        else:
            if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text_lower):
                matched = True
            elif len(token) >= 6 and token in compact_text:
                matched = True

        if not matched:
            continue

        hit_count += 1
        score += 0.55 if len(token) <= 4 else 0.72
        if hit_count >= 5:
            break

    return min(score, 2.8)


def _role_semantic_hits(runtime_role: str, text: str) -> int:
    role_key = _normalize_runtime_role(runtime_role)
    text_lower = str(text or "").lower()
    if not role_key or not text_lower:
        return 0

    hints = _ROLE_SEMANTIC_HINT_KEYWORDS.get(role_key, [])
    if not hints:
        return 0

    compact_text = re.sub(r"[^a-z0-9一-鿿]+", "", text_lower)
    hits = 0
    for raw in hints:
        token = str(raw or "").strip().lower()
        if not token:
            continue

        matched = False
        if re.search(r"[一-鿿]", token):
            matched = token in text_lower
        elif " " in token or "-" in token:
            matched = token in text_lower or token.replace(" ", "").replace("-", "") in compact_text
        else:
            matched = bool(re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text_lower))
            if not matched and len(token) >= 6:
                matched = token in compact_text

        if not matched:
            continue

        hits += 1
        if hits >= 4:
            break
    return hits


def _role_identity_hits(role: Dict[str, Any], text: str) -> float:
    text_lower = str(text or "").lower()
    if not text_lower:
        return 0.0

    candidates: List[str] = []

    runtime_role = str(role.get("runtime_role") or "").strip().lower()
    if runtime_role:
        candidates.append(runtime_role)

    role_id = str(role.get("id") or "").strip().lower()
    if role_id:
        candidates.extend([part for part in re.split(r"[^a-z0-9]+", role_id) if len(part) >= 3 and part != "role"])

    role_name = str(role.get("name") or "").strip().lower()
    if role_name:
        candidates.extend([part for part in re.split(r"[^a-z0-9一-鿿]+", role_name) if len(part) >= 2])

    seen: set[str] = set()
    score = 0.0
    hit_count = 0
    for token in candidates:
        token_key = str(token or "").strip().lower()
        if not token_key or token_key in seen:
            continue
        seen.add(token_key)

        matched = False
        if re.search(r"[一-鿿]", token_key):
            matched = token_key in text_lower
        else:
            if re.search(rf"(?<![a-z0-9]){re.escape(token_key)}(?![a-z0-9])", text_lower):
                matched = True

        if not matched:
            continue

        hit_count += 1
        score += 0.75
        if hit_count >= 2:
            break

    return min(score, 1.5)


# 运行时角色包可能阶段性缺失（例如仅装了 6 个岗位包）。
# 为保证调度覆盖面，缺失岗位使用内建画像兜底；若同 runtime_role 的包已存在，优先使用包定义。
_BUILTIN_RUNTIME_ROLE_FALLBACKS: Dict[str, Dict[str, Any]] = {
    "ops": {
        "id": "role.ops-fallback",
        "name": "运营专家",
        "runtime_role": "ops",
        "keywords": ["运营", "增长", "策略", "活动", "投放", "推广", "转化", "复盘", "营销"],
        "domains": ["domain.general", "domain.ecommerce", "domain.education"],
        "preferred_actions": ["plan", "optimize", "execute"],
        "prompt_hints": [],
        "skill_preferences": [],
        "execution_mandate": "",
        "priority": 999,
    },
    "data": {
        "id": "role.data-fallback",
        "name": "数据分析师",
        "runtime_role": "data",
        "keywords": ["数据", "分析", "报表", "指标", "漏斗", "归因", "sql", "统计", "实验", "ROI"],
        "domains": ["domain.general", "domain.ecommerce", "domain.education"],
        "preferred_actions": ["analyze", "query"],
        "prompt_hints": [],
        "skill_preferences": [],
        "execution_mandate": "",
        "priority": 999,
    },
    "service": {
        "id": "role.service-fallback",
        "name": "客服专家",
        "runtime_role": "service",
        "keywords": ["客服", "售后", "投诉", "差评", "退款", "满意度", "NPS", "话术"],
        "domains": ["domain.general", "domain.ecommerce"],
        "preferred_actions": ["analyze", "optimize", "execute"],
        "prompt_hints": [],
        "skill_preferences": [],
        "execution_mandate": "",
        "priority": 999,
    },
    "design": {
        "id": "role.design-fallback",
        "name": "设计师",
        "runtime_role": "design",
        "keywords": ["设计", "视觉", "主图", "详情页", "排版", "版式", "UI", "交互", "海报"],
        "domains": ["domain.general", "domain.ecommerce"],
        "preferred_actions": ["create", "optimize"],
        "prompt_hints": [],
        "skill_preferences": [],
        "execution_mandate": "",
        "priority": 999,
    },
    "accounting": {
        "id": "role.accounting-fallback",
        "name": "财务分析师",
        "runtime_role": "accounting",
        "keywords": ["财务", "成本", "利润", "预算", "现金流", "毛利", "净利", "ROI"],
        "domains": ["domain.general", "domain.ecommerce", "domain.education"],
        "preferred_actions": ["analyze", "plan"],
        "prompt_hints": [],
        "skill_preferences": [],
        "execution_mandate": "",
        "priority": 999,
    },
    "engineering": {
        "id": "role.engineering-fallback",
        "name": "技术工程师",
        "runtime_role": "engineering",
        "keywords": ["技术", "架构", "接口", "故障", "报错", "性能", "稳定性", "系统"],
        "domains": ["domain.general", "domain.ecommerce", "domain.education"],
        "preferred_actions": ["execute", "optimize", "analyze"],
        "prompt_hints": [],
        "skill_preferences": [],
        "execution_mandate": "",
        "priority": 999,
    },
    "web": {
        "id": "role.web-fallback",
        "name": "SEO专家",
        "runtime_role": "web",
        "keywords": ["seo", "关键词", "搜索", "自然流量", "收录", "排名", "曝光", "站内搜索"],
        "domains": ["domain.general", "domain.ecommerce", "domain.education"],
        "preferred_actions": ["plan", "optimize", "analyze"],
        "prompt_hints": [],
        "skill_preferences": [],
        "execution_mandate": "",
        "priority": 999,
    },
    "creative": {
        "id": "role.creative-fallback",
        "name": "内容创作者",
        "runtime_role": "creative",
        "keywords": ["文案", "创意", "脚本", "内容", "直播", "短视频", "选题", "种草"],
        "domains": ["domain.general", "domain.ecommerce", "domain.education"],
        "preferred_actions": ["create", "plan", "optimize"],
        "prompt_hints": [],
        "skill_preferences": [],
        "execution_mandate": "",
        "priority": 999,
    },
}


def _ensure_runtime_role_fallbacks(roles: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    existing_runtime = {
        _normalize_runtime_role(item.get("runtime_role"))
        for item in roles
        if isinstance(item, dict)
    }

    for runtime_role, profile in _BUILTIN_RUNTIME_ROLE_FALLBACKS.items():
        role_key = _normalize_runtime_role(runtime_role)
        if not role_key or role_key in existing_runtime:
            continue
        fallback = {
            "id": str(profile.get("id") or f"role.{role_key}-fallback"),
            "name": str(profile.get("name") or role_key),
            "runtime_role": role_key,
            "keywords": [str(x).strip() for x in (profile.get("keywords") or []) if str(x).strip()],
            "domains": [str(x).strip() for x in (profile.get("domains") or []) if str(x).strip()],
            "preferred_actions": [str(x).strip() for x in (profile.get("preferred_actions") or []) if str(x).strip()],
            "prompt_hints": [str(x).strip() for x in (profile.get("prompt_hints") or []) if str(x).strip()],
            "skill_preferences": [str(x).strip() for x in (profile.get("skill_preferences") or []) if str(x).strip()],
            "capability_hints": [str(x).strip() for x in (profile.get("capability_hints") or []) if str(x).strip()],
            "execution_mandate": str(profile.get("execution_mandate") or "").strip(),
            "priority": int(profile.get("priority", 999)),
        }
        roles.append(fallback)
        existing_runtime.add(role_key)

    return roles


_LEGACY_RUNTIME_ROLE_ALIASES: Dict[str, str] = {
    "operations": "ops",
    "operation": "ops",
    "运营": "ops",
    "运营专家": "ops",
    "data_analysis": "data",
    "analysis": "data",
    "数据分析": "data",
    "数据分析师": "data",
    "customer": "service",
    "customer_service": "service",
    "service_agent": "service",
    "客服": "service",
    "客服专家": "service",
    "finance": "accounting",
    "financial": "accounting",
    "财务": "accounting",
    "财务分析师": "accounting",
    "engineer": "engineering",
    "engineering_agent": "engineering",
    "技术": "engineering",
    "技术工程师": "engineering",
    "creative_agent": "creative",
    "内容": "creative",
    "内容创作者": "creative",
    "design": "design",
    "设计": "design",
    "设计师": "design",
    "seo": "web",
    "seo专家": "web",
    "web_agent": "web",
    "内容创作": "creative",
}


def _normalize_runtime_role(value: Any) -> str:
    return str(value or "").strip().lower()


def _role_root() -> Path:
    return (PROJECT_ROOT / "packages" / "roles").resolve()


def _manifest_paths() -> List[Path]:
    root = _role_root()
    if not root.exists():
        return []
    paths = list(root.rglob("manifest.yaml")) + list(root.rglob("manifest.yml")) + list(root.rglob("manifest.json"))
    return sorted({p.resolve() for p in paths})


def _read_manifest(path: Path) -> Dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Read role manifest failed: %s (%s)", path, exc)
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
        logger.warning("Parse role manifest failed: %s (%s)", path, exc)
        return None

    if isinstance(parsed, dict):
        return parsed
    return None


def load_role_catalog() -> List[Dict[str, Any]]:
    roles: List[Dict[str, Any]] = []

    for path in _manifest_paths():
        data = _read_manifest(path)
        if not data:
            continue

        status = str(data.get("status") or "active").strip().lower()
        enabled = bool(data.get("enabled", True))
        if not enabled or status in {"disabled", "inactive", "off"}:
            continue

        role_id = str(data.get("id") or f"role.{path.parent.name}").strip()
        runtime_role = _normalize_runtime_role(data.get("runtime_role")) or "ops"

        keywords = data.get("keywords") if isinstance(data.get("keywords"), list) else []
        domains = data.get("domains") if isinstance(data.get("domains"), list) else []
        preferred_actions = data.get("preferred_actions") if isinstance(data.get("preferred_actions"), list) else []
        prompt_hints = data.get("prompt_hints") if isinstance(data.get("prompt_hints"), list) else []

        explicit_skill_preferences = data.get("skill_preferences") if isinstance(data.get("skill_preferences"), list) else []
        dependencies = data.get("dependencies") if isinstance(data.get("dependencies"), dict) else {}
        dependency_skills = dependencies.get("skills") if isinstance(dependencies.get("skills"), list) else []

        skill_preferences_raw = [
            *[str(x).strip() for x in explicit_skill_preferences if str(x).strip()],
            *[str(x).strip() for x in dependency_skills if str(x).strip()],
        ]
        skill_preferences: List[str] = []
        seen_skill_preferences: set[str] = set()
        for item in skill_preferences_raw:
            token = str(item or "").strip()
            if not token or token in seen_skill_preferences:
                continue
            seen_skill_preferences.add(token)
            skill_preferences.append(token)

        required_capabilities = data.get("required_capabilities") if isinstance(data.get("required_capabilities"), list) else []
        optional_capabilities = data.get("optional_capabilities") if isinstance(data.get("optional_capabilities"), list) else []
        capability_hints_raw = [
            *[str(x).strip() for x in required_capabilities if str(x).strip()],
            *[str(x).strip() for x in optional_capabilities if str(x).strip()],
        ]
        capability_hints: List[str] = []
        seen_capability_hints: set[str] = set()
        for item in capability_hints_raw:
            token = str(item or "").strip()
            if not token or token in seen_capability_hints:
                continue
            seen_capability_hints.add(token)
            capability_hints.append(token)

        execution_mandate = str(data.get("execution_mandate") or "").strip()

        try:
            priority = int(data.get("priority", 100))
        except Exception:
            priority = 100

        roles.append(
            {
                "id": role_id,
                "name": str(data.get("name") or role_id).strip(),
                "runtime_role": runtime_role,
                "keywords": [str(x).strip() for x in keywords if str(x).strip()],
                "domains": [str(x).strip() for x in domains if str(x).strip()],
                "preferred_actions": [str(x).strip() for x in preferred_actions if str(x).strip()],
                "prompt_hints": [str(x).strip() for x in prompt_hints if str(x).strip()],
                "skill_preferences": [str(x).strip() for x in skill_preferences if str(x).strip()],
                "capability_hints": [str(x).strip() for x in capability_hints if str(x).strip()],
                "execution_mandate": execution_mandate,
                "priority": priority,
            }
        )

    roles = _ensure_runtime_role_fallbacks(roles)
    roles.sort(key=lambda x: (int(x.get("priority", 100)), str(x.get("id", ""))))
    return roles


def build_runtime_role_context(*, extra_aliases: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    roles = load_role_catalog()
    runtime_roles: List[str] = []
    alias_map: Dict[str, str] = {}
    label_map: Dict[str, str] = {}
    role_packages: Dict[str, List[Dict[str, Any]]] = {}

    for role in roles:
        runtime_role = _normalize_runtime_role(role.get("runtime_role"))
        if not runtime_role:
            continue

        if runtime_role not in runtime_roles:
            runtime_roles.append(runtime_role)
        alias_map[runtime_role] = runtime_role

        role_id = str(role.get("id") or "").strip()
        role_name = str(role.get("name") or runtime_role).strip()
        role_priority = int(role.get("priority", 100))

        if role_name and runtime_role not in label_map:
            label_map[runtime_role] = role_name

        if role_name:
            alias_map[role_name.lower()] = runtime_role

        role_id_lower = role_id.lower()
        if role_id_lower:
            alias_map[role_id_lower] = runtime_role
            if role_id_lower.startswith("role."):
                alias_map[role_id_lower.removeprefix("role.")] = runtime_role
            if "." in role_id_lower:
                alias_map[role_id_lower.split(".")[-1]] = runtime_role

        role_packages.setdefault(runtime_role, []).append(
            {
                "id": role_id,
                "name": role_name or runtime_role,
                "priority": role_priority,
                "domains": role.get("domains") if isinstance(role.get("domains"), list) else [],
                "keywords": role.get("keywords") if isinstance(role.get("keywords"), list) else [],
                "preferred_actions": role.get("preferred_actions") if isinstance(role.get("preferred_actions"), list) else [],
                "prompt_hints": role.get("prompt_hints") if isinstance(role.get("prompt_hints"), list) else [],
                "skill_preferences": role.get("skill_preferences") if isinstance(role.get("skill_preferences"), list) else [],
                "capability_hints": role.get("capability_hints") if isinstance(role.get("capability_hints"), list) else [],
                "execution_mandate": str(role.get("execution_mandate") or "").strip(),
            }
        )

    if not runtime_roles:
        runtime_roles = ["ops"]

    default_role = "ops" if "ops" in runtime_roles else runtime_roles[0]
    runtime_set = set(runtime_roles)

    for key, value in _LEGACY_RUNTIME_ROLE_ALIASES.items():
        raw = str(key or "").strip().lower()
        mapped = _normalize_runtime_role(value)
        if raw and mapped:
            alias_map[raw] = mapped

    if isinstance(extra_aliases, dict):
        for key, value in extra_aliases.items():
            raw = str(key or "").strip().lower()
            mapped = _normalize_runtime_role(value)
            if raw and mapped:
                alias_map[raw] = mapped

    for key, value in list(alias_map.items()):
        normalized = _normalize_runtime_role(value)
        alias_map[key] = normalized if normalized in runtime_set else default_role

    for runtime_role in runtime_roles:
        packages = role_packages.get(runtime_role, [])
        packages.sort(key=lambda x: (int(x.get("priority", 100)), str(x.get("id") or "")))
        role_packages[runtime_role] = packages

        if runtime_role not in label_map:
            top = packages[0] if packages else {}
            label_map[runtime_role] = str(top.get("name") or runtime_role)

    return {
        "runtime_roles": runtime_roles,
        "default_role": default_role,
        "alias_map": alias_map,
        "label_map": label_map,
        "role_packages": role_packages,
        "catalog": roles,
    }


def normalize_runtime_role(value: Optional[str], *, context: Optional[Dict[str, Any]] = None) -> str:
    ctx = context or build_runtime_role_context()
    default_role = str(ctx.get("default_role") or "ops")
    runtime_roles = [str(x).strip().lower() for x in (ctx.get("runtime_roles") or []) if str(x).strip()]
    if not runtime_roles:
        runtime_roles = [default_role]
    runtime_set = set(runtime_roles)
    alias_map = ctx.get("alias_map") if isinstance(ctx.get("alias_map"), dict) else {}

    raw = str(value or "").strip().lower()
    if not raw:
        return default_role if default_role in runtime_set else runtime_roles[0]
    if raw in runtime_set:
        return raw
    mapped = _normalize_runtime_role(alias_map.get(raw))
    if mapped in runtime_set:
        return mapped
    return default_role if default_role in runtime_set else runtime_roles[0]


_ACTION_SIGNAL_KEYWORDS: Dict[str, List[str]] = {
    "analyze": ["分析", "诊断", "排查", "归因", "why", "analysis"],
    "create": ["写", "生成", "创作", "脚本", "文案", "create"],
    "optimize": ["优化", "提升", "改善", "调优", "optimize"],
    "plan": ["计划", "方案", "规划", "排期", "plan"],
    "execute": ["执行", "落地", "上线", "处理", "execute"],
    "query": ["查询", "查看", "多少", "是什么", "query"],
}


def _action_signal_hit_count(action: str, message: str) -> int:
    action_key = str(action or "").strip().lower()
    text = str(message or "").strip().lower()
    if not action_key or not text:
        return 0
    keywords = _ACTION_SIGNAL_KEYWORDS.get(action_key, [])
    if not keywords:
        return 0
    return sum(1 for kw in keywords if kw in text)


def _infer_action_signal(message: str) -> str:
    text = str(message or "").strip().lower()
    if not text:
        return ""

    best_action = ""
    best_score = 0
    for action, keywords in _ACTION_SIGNAL_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text)
        if score > best_score:
            best_score = score
            best_action = action
    return best_action


def _keyword_weighted_hits(runtime_role: str, keywords: List[str], text: str) -> float:
    if not text:
        return 0.0

    role_key = _normalize_runtime_role(runtime_role)
    weight_map = _WEAK_ROLE_KEYWORD_WEIGHTS.get(role_key, {})

    score = 0.0
    for kw in keywords:
        token = str(kw or "").strip()
        if not token:
            continue
        token_lower = token.lower()
        if token_lower in text:
            score += float(weight_map.get(token_lower, 1.0))
    return score


def suggest_roles(message: str, domain_id: str, max_roles: int = 3, action: str = "") -> List[str]:
    text = (message or "").lower()
    roles = load_role_catalog()
    if not roles:
        return []

    score_by_runtime: Dict[str, float] = {}
    signal_strength_by_runtime: Dict[str, float] = {}
    available_runtime_roles: set[str] = set()
    explicit_action_key = str(action or "").strip().lower()
    inferred_action_key = _infer_action_signal(text)
    action_key = explicit_action_key or inferred_action_key
    action_signal_hits = _action_signal_hit_count(action_key, text) if action_key else 0
    if explicit_action_key and action_signal_hits == 0 and inferred_action_key and inferred_action_key != explicit_action_key:
        inferred_hits = _action_signal_hit_count(inferred_action_key, text)
        if inferred_hits > action_signal_hits:
            action_key = inferred_action_key
            action_signal_hits = inferred_hits
    action_has_evidence = action_signal_hits > 0

    for role in roles:
        runtime_role = _normalize_runtime_role(role.get("runtime_role")) or "ops"
        available_runtime_roles.add(runtime_role)
        keywords = role.get("keywords") or []
        domains = role.get("domains") or []
        preferred_actions = [str(x).strip().lower() for x in (role.get("preferred_actions") or []) if str(x).strip()]

        keyword_hits = _keyword_weighted_hits(runtime_role, keywords, text)

        hint_values: List[str] = [
            *[str(x).strip() for x in (role.get("skill_preferences") or []) if str(x).strip()],
            *[str(x).strip() for x in (role.get("capability_hints") or []) if str(x).strip()],
            *[str(x).strip() for x in (role.get("prompt_hints") or []) if str(x).strip()],
        ]
        hint_hits = _hint_token_hits(hint_values, text)
        identity_hits = _role_identity_hits(role, text)
        semantic_hits = _role_semantic_hits(runtime_role, text)

        domain_bonus = 1.0 if domain_id in domains or "domain.general" in domains else 0.0

        action_bonus = 0.0
        if action_has_evidence and action_key and action_key in preferred_actions:
            action_bonus += 0.9
        elif action_has_evidence and action_key:
            # 柔性动作兼容，避免只有关键词触发时岗位序颠倒。
            if action_key in {"plan", "optimize", "execute"} and runtime_role == "ops":
                action_bonus += 0.35
            elif action_key in {"analyze", "query"} and runtime_role == "data":
                action_bonus += 0.35
            elif action_key in {"create"} and runtime_role in {"creative", "design", "web"}:
                action_bonus += 0.35

        score = (
            float(keyword_hits) * 1.8
            + float(hint_hits) * 1.15
            + float(identity_hits) * 0.85
            + float(semantic_hits) * 0.72
            + domain_bonus
            + action_bonus
        )

        signal_strength = (
            float(keyword_hits)
            + float(hint_hits) * 0.65
            + float(identity_hits) * 0.5
            + float(semantic_hits) * 0.28
            + action_bonus * 0.2
        )

        prev = score_by_runtime.get(runtime_role, 0.0)
        if score > prev:
            score_by_runtime[runtime_role] = score
            signal_strength_by_runtime[runtime_role] = signal_strength
        elif runtime_role not in signal_strength_by_runtime:
            signal_strength_by_runtime[runtime_role] = signal_strength

    action_prior_map: Dict[str, List[tuple[str, float]]] = {
        "analyze": [("data", 0.85), ("accounting", 0.35)],
        "query": [("data", 0.95), ("accounting", 0.25)],
        "plan": [("ops", 0.75), ("creative", 0.25)],
        "optimize": [("ops", 0.75), ("service", 0.25), ("design", 0.25)],
        "execute": [("ops", 0.82), ("engineering", 0.28)],
        "create": [("design", 0.65), ("creative", 0.65), ("web", 0.28)],
    }
    if action_has_evidence and action_key:
        for runtime_role, boost in action_prior_map.get(action_key, []):
            if runtime_role not in available_runtime_roles:
                continue
            score_by_runtime[runtime_role] = score_by_runtime.get(runtime_role, 0.0) + float(boost)
            signal_strength_by_runtime[runtime_role] = signal_strength_by_runtime.get(runtime_role, 0.0) + 0.22

    marketing_hits = sum(1 for kw in _MARKETING_HINT_KEYWORDS if kw in text)
    data_strong_hits = sum(1 for kw in _DATA_EVIDENCE_KEYWORDS if kw in text)
    data_medium_hits = sum(1 for kw in _DATA_MEDIUM_EVIDENCE_KEYWORDS if kw in text)
    data_weak_hits = sum(1 for kw in _DATA_WEAK_ANALYSIS_KEYWORDS if kw in text)
    data_evidence_hits = data_strong_hits * 2 + data_medium_hits
    has_strong_data_evidence = data_strong_hits >= 1 or data_medium_hits >= 2
    ops_execution_hits = sum(1 for kw in _OPS_EXECUTION_HINT_KEYWORDS if kw in text)
    strategy_delivery_hits = sum(1 for kw in _STRATEGY_DELIVERY_HINT_KEYWORDS if kw in text)
    growth_goal_hits = sum(1 for kw in _GROWTH_GOAL_KEYWORDS if kw in text)
    data_diagnostic_hits = sum(1 for kw in _DATA_DIAGNOSTIC_HINT_KEYWORDS if kw in text)
    design_creative_hits = sum(1 for kw in _DESIGN_CREATIVE_HINT_KEYWORDS if kw in text)
    experiment_hits = sum(1 for kw in _EXPERIMENT_HINT_KEYWORDS if kw in text)
    data_strong_context_hits = sum(1 for kw in _DATA_STRONG_CONTEXT_HINT_KEYWORDS if kw in text)

    domain_key = str(domain_id or "").strip().lower()
    marketing_favored_domain = domain_key in {"", "domain.general", "domain.ecommerce"}
    marketing_action = action_key in {"plan", "optimize", "execute", "create"}
    analysis_action = action_key in {"analyze", "query"}
    has_growth_strategy_pattern = bool(
        re.search(r"(怎么|如何).{0,8}(提升|优化|增长|做)", text)
        or re.search(r"(提升|优化|增长).{0,8}(方案|策略|计划|动作|步骤|落地)", text)
    )
    business_strategy_focus = bool(
        strategy_delivery_hits > 0
        or marketing_action
        or has_growth_strategy_pattern
    )
    growth_strategy_focus = bool(
        business_strategy_focus
        and (
            marketing_hits > 0
            or growth_goal_hits > 0
            or any(
                token in text
                for token in ("投放", "投流", "渠道", "活动", "拉新", "促活", "复购", "增长", "转化")
            )
            or (data_medium_hits >= 1 and has_growth_strategy_pattern)
        )
    )
    strong_data_diagnostic = bool(
        has_strong_data_evidence
        and (
            data_diagnostic_hits >= 2
            or (analysis_action and data_diagnostic_hits >= 1 and data_medium_hits >= 1)
            or (data_strong_hits >= 2 and data_strong_context_hits >= 1)
        )
    )
    design_creative_experiment_focus = bool(
        experiment_hits >= 1
        and design_creative_hits >= 2
        and data_diagnostic_hits <= 1
        and not (analysis_action and data_strong_context_hits >= 2)
    )
    prefer_ops_primary = bool(growth_strategy_focus and not strong_data_diagnostic)
    prefer_data_primary = bool(
        strong_data_diagnostic
        and (
            analysis_action
            or data_diagnostic_hits >= 2
            or (data_strong_hits >= 2 and data_strong_context_hits >= 1)
        )
    )

    if design_creative_experiment_focus:
        if "design" in score_by_runtime:
            design_boost = 1.05 + min(design_creative_hits, 4) * 0.18
            score_by_runtime["design"] = score_by_runtime.get("design", 0.0) + design_boost
            signal_strength_by_runtime["design"] = signal_strength_by_runtime.get("design", 0.0) + 0.72
        if "creative" in score_by_runtime:
            creative_boost = 0.86 + min(design_creative_hits, 4) * 0.14
            score_by_runtime["creative"] = score_by_runtime.get("creative", 0.0) + creative_boost
            signal_strength_by_runtime["creative"] = signal_strength_by_runtime.get("creative", 0.0) + 0.58

        if not strong_data_diagnostic and "data" in score_by_runtime:
            data_penalty = 1.10 + min(experiment_hits, 2) * 0.25
            score_by_runtime["data"] = max(0.0, score_by_runtime.get("data", 0.0) - data_penalty)
            signal_strength_by_runtime["data"] = max(0.0, signal_strength_by_runtime.get("data", 0.0) - 0.42)

        if "design" in score_by_runtime and "data" in score_by_runtime:
            design_score = float(score_by_runtime.get("design") or 0.0)
            data_score = float(score_by_runtime.get("data") or 0.0)
            if data_score >= design_score:
                score_by_runtime["design"] = data_score + 0.08

    if marketing_hits > 0 or prefer_ops_primary:
        if marketing_favored_domain:
            signal_hits_for_ops = max(marketing_hits, growth_goal_hits)
            ops_boost = 0.60 + min(signal_hits_for_ops, 4) * 0.22
            if marketing_action or business_strategy_focus:
                ops_boost += 0.42
            if prefer_ops_primary and marketing_hits == 0:
                ops_boost += 0.35
            elif analysis_action and not strong_data_diagnostic:
                ops_boost += 0.12
            ops_signal_boost = 0.66 + (0.18 if business_strategy_focus else 0.0)
            data_penalty_without_evidence = 1.15 if (marketing_action or prefer_ops_primary) else 0.75
        else:
            # 非电商域（例如教育）里，增长诉求也可能是数据分析语境，保持更弱先验。
            ops_boost = 0.25 if (marketing_hits >= 2 or prefer_ops_primary) else 0.0
            ops_signal_boost = 0.16 if ops_boost > 0.0 else 0.0
            data_penalty_without_evidence = 0.35 if (marketing_action or prefer_ops_primary) else 0.0

        if ops_boost > 0.0:
            score_by_runtime["ops"] = score_by_runtime.get("ops", 0.0) + ops_boost
            signal_strength_by_runtime["ops"] = signal_strength_by_runtime.get("ops", 0.0) + ops_signal_boost

        if (
            not strong_data_diagnostic
            and data_penalty_without_evidence > 0.0
            and "data" in score_by_runtime
        ):
            score_by_runtime["data"] = max(
                0.0,
                score_by_runtime["data"] - data_penalty_without_evidence,
            )
            signal_strength_by_runtime["data"] = max(
                0.0,
                signal_strength_by_runtime.get("data", 0.0) - 0.35,
            )

        if (
            "ops" in score_by_runtime
            and "data" in score_by_runtime
            and prefer_ops_primary
            and not strong_data_diagnostic
        ):
            ops_score = float(score_by_runtime.get("ops") or 0.0)
            data_score = float(score_by_runtime.get("data") or 0.0)
            if data_score >= ops_score:
                score_by_runtime["ops"] = data_score + 0.12

    if ops_execution_hits > 0:
        ops_exec_boost = 0.75 + min(ops_execution_hits, 3) * 0.18
        score_by_runtime["ops"] = score_by_runtime.get("ops", 0.0) + ops_exec_boost
        signal_strength_by_runtime["ops"] = signal_strength_by_runtime.get("ops", 0.0) + 0.55

    if has_strong_data_evidence and "data" in score_by_runtime:
        data_boost = 0.95 + min(data_strong_hits, 2) * 0.35 + min(data_medium_hits, 4) * 0.18
        if strong_data_diagnostic:
            data_boost += 0.45 + min(data_diagnostic_hits, 3) * 0.18
        elif design_creative_experiment_focus:
            # “设计/文案/素材 + A/B”场景不应仅因实验词误判为数据主导。
            data_boost = min(data_boost, 0.36 + min(data_medium_hits, 2) * 0.12)

        data_signal_boost = 0.95 + (0.25 if strong_data_diagnostic else 0.0)
        if design_creative_experiment_focus and not strong_data_diagnostic:
            data_signal_boost = min(data_signal_boost, 0.24)

        score_by_runtime["data"] = score_by_runtime.get("data", 0.0) + data_boost
        signal_strength_by_runtime["data"] = signal_strength_by_runtime.get("data", 0.0) + data_signal_boost

    if (
        prefer_data_primary
        and "data" in score_by_runtime
        and "ops" in score_by_runtime
    ):
        data_score = float(score_by_runtime.get("data") or 0.0)
        ops_score = float(score_by_runtime.get("ops") or 0.0)
        if data_score + 0.10 < ops_score:
            score_by_runtime["data"] = ops_score + 0.06

    # 非电商域下的“分析/查询”任务，若 data 与 ops 分数接近，优先 data，
    # 避免“留存/增长”等业务词把纯分析诉求误导为运营主导。
    if (
        not marketing_favored_domain
        and analysis_action
        and "data" in score_by_runtime
        and "ops" in score_by_runtime
        and strong_data_diagnostic
    ):
        data_score = float(score_by_runtime.get("data") or 0.0)
        ops_score = float(score_by_runtime.get("ops") or 0.0)
        if data_score >= 2.2 and ops_score > data_score and (ops_score - data_score) <= 0.6:
            score_by_runtime["data"] = ops_score + 0.05

    if (
        design_creative_experiment_focus
        and "design" in score_by_runtime
        and "data" in score_by_runtime
        and not strong_data_diagnostic
    ):
        design_score = float(score_by_runtime.get("design") or 0.0)
        data_score = float(score_by_runtime.get("data") or 0.0)
        if data_score >= design_score:
            score_by_runtime["design"] = data_score + 0.10
            signal_strength_by_runtime["design"] = max(
                float(signal_strength_by_runtime.get("design", 0.0)),
                float(signal_strength_by_runtime.get("data", 0.0)) + 0.06,
            )

    ranked = sorted(score_by_runtime.items(), key=lambda x: x[1], reverse=True)
    suggestions = [
        role
        for role, score in ranked
        if score > 0 and (
            float(signal_strength_by_runtime.get(role, 0.0)) >= 0.50
            or float(score) >= 2.2
        )
    ]

    if suggestions and marketing_favored_domain and marketing_hits > 0 and not has_strong_data_evidence:
        if marketing_action:
            suggestions = [role for role in suggestions if role != "data"]
        if "ops" in suggestions and suggestions[0] != "ops":
            suggestions = ["ops", *[role for role in suggestions if role != "ops"]]

    if not suggestions:
        top_signal = float(signal_strength_by_runtime.get(ranked[0][0], 0.0)) if ranked else 0.0
        if (not action_has_evidence) and marketing_hits == 0 and data_evidence_hits == 0 and data_weak_hits == 0 and ops_execution_hits == 0 and top_signal < 0.25:
            if "ops" in available_runtime_roles:
                return ["ops"][: max(1, max_roles)]
            return []

        if ranked:
            fallback = [role for role, score in ranked if float(score) > 0.7]
            if fallback:
                if marketing_favored_domain and marketing_hits > 0 and not has_strong_data_evidence and marketing_action:
                    fallback = [role for role in fallback if role != "data"]
                    if "ops" in fallback and fallback[0] != "ops":
                        fallback = ["ops", *[role for role in fallback if role != "ops"]]
                if fallback:
                    return fallback[: max(1, max_roles)]
        if "ops" in available_runtime_roles:
            return ["ops"][: max(1, max_roles)]
        return []
    return suggestions[: max(1, max_roles)]
