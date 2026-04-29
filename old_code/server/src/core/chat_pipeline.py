'\n聊天管道编排?v4.1 ?智能内核增强?\n\n核心能力:\n1. 意图分析 ?角色识别 + 动作 + 平台 + 复杂度分?\n2. 功能预算 ?精?top-3 特征注入 prompt\n3. LLM 流式调用 + tool_use 循环（最?MAX_TOOL_ROUNDS 轮）\n4. ?多Agent协作：TIER_MULTI 时自动调度支持Agent补充分析\n5. ?丰富 SSE 事件流：前端可视化智能分析全过程\n6. 后台闭环：质量→信任→学习→告警（fire-and-forget?\n'

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


ROLE_DISPLAY_NAMES = {
    "ops": "运营专家",
    "data": "数据分析师",
    "service": "客服专家",
    "design": "设计师",
    "accounting": "财务分析师",
    "engineering": "技术工程师",
    "web": "SEO专家",
    "creative": "内容创作者",
}


def _resolve_role_display_names() -> Dict[str, str]:
    names = {str(k): str(v) for k, v in ROLE_DISPLAY_NAMES.items()}
    try:
        from src.core.role_router import build_runtime_role_context

        ctx = build_runtime_role_context()
        label_map = ctx.get('label_map') if isinstance(ctx.get('label_map'), dict) else {}
        for role, label in label_map.items():
            r = str(role or '').strip().lower()
            l = str(label or '').strip()
            if r and l:
                names[r] = l
    except Exception:
        pass
    return names


def _role_display_name(role: str) -> str:
    role_key = str(role or '').strip().lower()
    if not role_key:
        return '未知岗位'
    return _resolve_role_display_names().get(role_key, role_key)


def _normalize_runtime_role_token(
    raw_role: str | None,
    *,
    allow_default: bool = False,
    role_ctx: Optional[Dict[str, Any]] = None,
) -> str:
    token = str(raw_role or '').strip().lower()
    if not token:
        return ''

    built_in_roles = {
        str(role_key or '').strip().lower()
        for role_key in ROLE_DISPLAY_NAMES.keys()
        if str(role_key or '').strip()
    }
    if token in built_in_roles:
        return token

    built_in_label_aliases = {
        str(label or '').strip().lower(): str(role_key or '').strip().lower()
        for role_key, label in ROLE_DISPLAY_NAMES.items()
        if str(role_key or '').strip() and str(label or '').strip()
    }
    if token in built_in_label_aliases:
        mapped = built_in_label_aliases.get(token, '')
        if mapped:
            return mapped

    try:
        from src.core import role_router as role_router_module
        from src.core.role_router import build_runtime_role_context, normalize_runtime_role

        ctx = role_ctx or build_runtime_role_context()
        runtime_roles = [
            str(item).strip().lower()
            for item in (ctx.get('runtime_roles') or [])
            if str(item).strip()
        ]
        runtime_set = set(runtime_roles)
        if token in runtime_set:
            return token

        legacy_alias_map = (
            getattr(role_router_module, '_LEGACY_RUNTIME_ROLE_ALIASES', {})
            if hasattr(role_router_module, '_LEGACY_RUNTIME_ROLE_ALIASES')
            else {}
        )
        if isinstance(legacy_alias_map, dict):
            legacy_mapped = str(legacy_alias_map.get(token) or '').strip().lower()
            if legacy_mapped:
                return legacy_mapped

        alias_map = ctx.get('alias_map') if isinstance(ctx.get('alias_map'), dict) else {}
        mapped = str(alias_map.get(token) or '').strip().lower()
        if mapped in runtime_set:
            return mapped
        if mapped in built_in_roles:
            return mapped

        label_map = ctx.get('label_map') if isinstance(ctx.get('label_map'), dict) else {}
        if isinstance(label_map, dict):
            for runtime_role, display_label in label_map.items():
                label_token = str(display_label or '').strip().lower()
                runtime_token = str(runtime_role or '').strip().lower()
                if not label_token or not runtime_token:
                    continue
                if token != label_token:
                    continue
                if runtime_token in runtime_set or runtime_token in built_in_roles:
                    return runtime_token

        if allow_default:
            fallback = normalize_runtime_role(token, context=ctx)
            return str(fallback or '').strip().lower() or 'ops'
        return ''
    except Exception:
        if token in built_in_roles:
            return token
        return 'ops' if allow_default else ''


# 姣忎釜瑙掕壊鍦ㄥ洖澶嶄腑鍑虹幇鏃讹紝鏆楃ず闇€瑕佽瑙掕壊浠嬪叆鐨勪俊鍙疯瘝
_REPLY_ROLE_SIGNALS: Dict[str, List[str]] = {
    "data": ["鏁版嵁鍒嗘瀽", '统计', "鎸囨爣", "杞寲鐜?", "婕忔枟", "鎶ヨ〃", "瓒嬪娍", "寮傚父鏁版嵁"],
    "design": ["璁捐", "瑙嗚", "涓诲浘", '详情?', "娴锋姤", "閰嶈壊", "绱犳潗"],
    "accounting": ["璐㈠姟", '成本核算', "鍒╂鼎", "棰勭畻", "璐圭敤", "姣涘埄", "鐩堜簭"],
    "service": ["瀹㈡湇", "鍞悗", "鎶曡瘔", "閫€娆?", '话术', "瀹㈣瘔"],
    "engineering": ['抢?', "鎺ュ彛", "绯荤粺", '弢?', "鏋舵瀯", '性能'],
    "web": ["SEO", '关键词优?', "鎼滅储鎺掑悕", "鏍囬浼樺寲", "搴楅摵瑁呬慨"],
    "creative": ["鏂囨", "鐭棰?", "绉嶈崏", "鍐呭鍒涗綔", "鑴氭湰", "鐩存挱鑴氭湰"],
    "ops": ["杩愯惀绛栫暐", '促销', "娲诲姩绛栧垝", "鎺ㄥ箍", "鎶曟斁绛栫暐"],
}

_COLLAB_ROLE_DELEGATION_CUES: tuple[str, ...] = (
    "请数据",
    "请运营",
    "请客服",
    "请设计",
    "请财务",
    "请技术",
    "请工程",
    "请seo",
    "请内容",
    "建议由",
    "建议让",
    "交给",
    "协同",
    "协作",
    "并由",
    "并让",
    "分工",
    "配合",
    "联动",
    "补位",
)


def _resolve_reply_role_signals() -> Dict[str, List[str]]:
    signals: Dict[str, List[str]] = {
        role: [str(x).strip() for x in words if str(x).strip()]
        for role, words in _REPLY_ROLE_SIGNALS.items()
    }
    try:
        from src.core.role_router import build_runtime_role_context

        ctx = build_runtime_role_context()
        role_packages = ctx.get("role_packages") if isinstance(ctx.get("role_packages"), dict) else {}

        for runtime_role, candidates in role_packages.items():
            role_key = str(runtime_role or "").strip().lower()
            if not role_key:
                continue

            merged = list(signals.get(role_key, []))
            if isinstance(candidates, list):
                for candidate in candidates:
                    if not isinstance(candidate, dict):
                        continue
                    keywords = candidate.get("keywords")
                    if isinstance(keywords, list):
                        merged.extend([str(x).strip() for x in keywords if str(x).strip()])

            deduped: List[str] = []
            seen: set[str] = set()
            for kw in merged:
                token = str(kw or "").strip()
                if not token or token in seen:
                    continue
                seen.add(token)
                deduped.append(token)
                if len(deduped) >= 16:
                    break

            signals[role_key] = deduped
    except Exception:
        pass

    return signals


def _detect_needed_roles_from_reply(reply: str, current_role: str, existing: List[str]) -> List[str]:
    """
    扫描主Agent回复，识别其中提及需要其他专业能力的信号。
    为避免误触发，仅当回复中出现“明确协作/委派语气”时才扩展支持岗位。
    """
    if not reply or len(reply) < 40:
        return []

    text = str(reply or "").strip()
    lowered = text.lower()
    if not any(cue in text or cue in lowered for cue in _COLLAB_ROLE_DELEGATION_CUES):
        return []

    current = str(current_role or "").strip().lower()
    existing_set = {str(x).strip().lower() for x in existing if str(x).strip()}

    extra: List[str] = []
    role_signals = _resolve_reply_role_signals()
    for role, signals in role_signals.items():
        if role == current or role in existing_set:
            continue
        if sum(1 for s in signals if s and (s in text or s.lower() in lowered)) >= 2:
            extra.append(role)
    max_extra = max(0, _support_agent_role_limit(collaboration_mode=_COLLABORATION_MODE_AUTO) - len(existing))
    return extra[:max_extra]


def _is_asking_user(reply: str) -> bool:
    '''
    判断LLM是否在向用户追问信息（非给出分析）?
    如果是追问，应跳过质量重试和多Agent协作调度?
    '''
    text = str(reply or "").strip()
    if not text:
        return False

    has_question = ("？" in text) or ("?" in text)
    if not has_question:
        return False

    question_patterns = [
        "请问",
        "能告诉我",
        "方便提供",
        "需要了解",
        "请提供",
        "您能",
        "可以告诉",
        "请描述",
        "能否",
        "还需要",
    ]
    if not any(p in text for p in question_patterns):
        return False

    # 当回复已经包含结构化可执行内容时，不应被视为“仅追问”，否则会误跳过后续质量守卫。
    if len(text) >= 220 and re.search(
        r"30\s*秒|结论|可执行|步骤|今日先做|下一步|风险|回滚|KPI|指标|##|###",
        text,
        re.IGNORECASE,
    ):
        return False

    list_points = re.findall(r"^\s*(?:[-•*]|\d+[.、\)])\s+", text, re.MULTILINE)
    if len(list_points) >= 2 and len(text) >= 180:
        return False

    return True


_RESPONSE_MODE_EXECUTION = "execution"
_RESPONSE_MODE_LEARNING = "learning"
_VALID_RESPONSE_MODES = {_RESPONSE_MODE_EXECUTION, _RESPONSE_MODE_LEARNING}

# 学习模式默认更容易拉长回复，这里给一层硬上限，优先保证首轮响应时延。
_LEARNING_PRIMARY_MAX_TOKENS_CAP = 520
_LEARNING_MANUAL_PRIMARY_MAX_TOKENS_CAP = 420

_LEARNING_LEVEL_GENERAL = "general"
_LEARNING_LEVEL_VOCATIONAL = "vocational"
_LEARNING_LEVEL_HIGHER_VOCATIONAL = "higher_vocational"
_LEARNING_LEVEL_UNDERGRADUATE = "undergraduate"
_VALID_LEARNING_LEVELS = {
    _LEARNING_LEVEL_VOCATIONAL,
    _LEARNING_LEVEL_HIGHER_VOCATIONAL,
    _LEARNING_LEVEL_UNDERGRADUATE,
}
_LEARNING_LEVEL_LABELS: Dict[str, str] = {
    _LEARNING_LEVEL_GENERAL: "通用",
    _LEARNING_LEVEL_VOCATIONAL: "中职",
    _LEARNING_LEVEL_HIGHER_VOCATIONAL: "高职",
    _LEARNING_LEVEL_UNDERGRADUATE: "本科",
}

_OUTLINE_BULLET_RE = re.compile(
    r"^\s*(?:[-*•]+|\d+\s*[\.)、]|[一二三四五六七八九十]+\s*[、\.)])\s*"
)

_TASK_PURPOSE_LABELS: Dict[str, str] = {
    "execution": "执行落地",
    "analysis": "问题分析",
    "creation": "内容创作",
    "learning": "知识理解",
    "general": "综合咨询",
}

_TASK_PURPOSE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("execution", ("怎么做", "执行", "落地", "步骤", "计划", "方案", "优化", "投放", "排期", "策略")),
    ("analysis", ("分析", "原因", "为什么", "诊断", "复盘", "评估", "对比", "归因", "异常")),
    ("creation", ("写", "生成", "文案", "脚本", "标题", "海报", "详情页", "主图", "创作")),
    ("learning", ("解释", "是什么", "科普", "概念", "教学", "原理", "区别")),
)

_TASK_PLATFORM_HINTS: tuple[str, ...] = (
    "抖音", "淘宝", "天猫", "京东", "拼多多", "小红书", "快手", "微信", "企微", "飞书",
    "amazon", "tiktok", "shopify", "aliexpress",
)
_TASK_OBJECTIVE_HINTS: tuple[str, ...] = (
    "gmv", "roi", "roas", "ctr", "cvr", "转化", "成交", "销售额", "利润", "毛利",
    "点击", "曝光", "订单", "客单", "复购", "留存", "投产", "目标", "增长",
)
_TASK_TIME_HINTS_RE = re.compile(
    r"(今天|明天|昨天|本周|下周|本月|下月|近\d+天|最近\d+天|\d+天内|近一周|近一月|Q[1-4]|\d{4}[-/.年]\d{1,2}(?:[-/.月]\d{1,2})?)"
)
_TASK_CONSTRAINT_HINTS: tuple[str, ...] = ("预算", "成本", "人力", "资源", "限制", "上限", "下限", "库存")
_TASK_AUDIENCE_HINTS: tuple[str, ...] = ("人群", "用户", "客群", "受众", "买家", "粉丝")
_TASK_CONTEXT_HINTS: tuple[str, ...] = ("产品", "商品", "sku", "店铺", "类目", "渠道", "账号")
_TASK_DIRECT_DELIVERABLE_REQUEST_HINTS: tuple[str, ...] = (
    "给我", "请给", "请把", "输出", "列出", "生成", "写", "制定", "做一版", "提供", "整理",
)
_TASK_DIRECT_DELIVERABLE_ARTIFACT_HINTS: tuple[str, ...] = (
    "sop", "模板", "话术", "清单", "脚本", "钩子", "矩阵", "分级", "方案", "计划", "步骤",
    "优先级", "a/b", "ab测试", "实验", "排期", "报告", "洞察", "洞察报告", "分析报告",
)
_TASK_REVISION_REQUEST_HINTS: tuple[str, ...] = (
    "改版",
    "改成",
    "改写",
    "重写",
    "调整",
    "压缩",
    "精简",
    "收敛",
    "继续",
    "补充约束",
    "新增约束",
)
_TASK_CARRYOVER_HINTS: tuple[str, ...] = (
    "基于上面",
    "基于上个",
    "按上面",
    "按上个",
    "承接",
    "承接上面",
    "继续",
    "沿用",
    "改版",
    "调整",
    "改成",
    "压缩",
    "精简",
    "补充约束",
    "新增约束",
    "在上个方案",
    "上一个方案",
    "上一轮",
    "最终方案",
    "该方案",
    "这个方案",
    "上一版",
    "上面方案",
)
_TASK_CONSTRAINT_ANCHOR_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:人|条|元|小时|天|周|月|单|次|%|％)")
_TASK_CARRYOVER_KEYWORD_ANCHOR_RE = re.compile(
    r"一步一步|三步|[\u4e00-\u9fff]{2,8}(?:店铺|店|框架|方案|计划|排查|预算|访客|下单|转化率|价值|话术|清单|指标|场景)"
)
_TASK_CARRYOVER_NUMBER_ANCHOR_RE = re.compile(r"\d+(?:\.\d+)?")
_TASK_CARRYOVER_KEYWORD_STOPWORDS: tuple[str, ...] = (
    "上面框架", "上面内容", "继续上面", "承接上面", "今天就执行", "关键场景", "关键数字",
)

_TASK_FIELD_LABELS: Dict[str, str] = {
    "platform": "平台",
    "objective": "目标指标",
    "timeframe": "时间范围",
    "audience": "目标人群",
    "constraints": "资源约束",
    "context": "产品/店铺上下文",
}
_TASK_PURPOSE_REQUIRED_FIELDS: Dict[str, List[str]] = {
    "execution": ["platform", "objective", "timeframe"],
    "analysis": ["objective", "timeframe"],
    "creation": ["platform", "audience"],
    "learning": [],
    "general": ["objective"],
}
_TASK_CLARIFY_QUESTION_MAP: Dict[str, str] = {
    "platform": "你主要要落地在哪个平台（如抖音、淘宝）？",
    "objective": "你这次最优先优化的目标指标是什么（如 GMV/ROI/转化率）？",
    "timeframe": "希望覆盖哪个时间范围（如近7天、本周）？",
    "audience": "这次面向的核心人群是谁？",
    "constraints": "当前预算或资源上限大概是多少？",
    "context": "对应的产品/店铺范围是什么？",
}


def _normalize_response_mode(response_mode: Optional[str]) -> str:
    mode = str(response_mode or "").strip().lower()
    if mode in _VALID_RESPONSE_MODES:
        return mode
    return _RESPONSE_MODE_EXECUTION


def _normalize_learning_level(
    learning_level: Optional[str],
    *,
    response_mode: str = _RESPONSE_MODE_EXECUTION,
) -> str:
    mode = _normalize_response_mode(response_mode)
    if mode != _RESPONSE_MODE_LEARNING:
        return _LEARNING_LEVEL_GENERAL

    level = str(learning_level or "").strip().lower()
    if level in _VALID_LEARNING_LEVELS:
        return level
    return _LEARNING_LEVEL_HIGHER_VOCATIONAL


def _resolve_primary_generation_max_tokens(
    *,
    response_mode: str,
    tier: int,
    message: str,
    has_attachments: bool,
    requires_verified_sources: bool,
) -> int:
    mode = _normalize_response_mode(response_mode)
    budget = 520 if mode == _RESPONSE_MODE_LEARNING else 760

    if int(tier or 0) >= 2:
        budget += 120
    if has_attachments:
        budget += 120
    if requires_verified_sources:
        budget += 80

    message_len = len(str(message or "").strip())
    if message_len >= 180:
        budget += 80
    if message_len >= 360:
        budget += 80

    return max(300, min(760, int(budget)))


def _resolve_quality_retry_max_tokens(
    *,
    response_mode: str,
    primary_generation_max_tokens: int,
) -> int:
    mode = _normalize_response_mode(response_mode)
    retry_cap = 520 if mode == _RESPONSE_MODE_LEARNING else 760
    return max(320, min(int(primary_generation_max_tokens or retry_cap), retry_cap))


def _response_mode_instruction(response_mode: str) -> str:
    if response_mode == _RESPONSE_MODE_LEARNING:
        return (
            "【回复模式】教学模式。先给学习目标结论（最多 2 条），再按“为什么/怎么做/场景示例/常见误区/自检问题”展开。"
            " 必须显式写出这5个小节；术语首次出现要附一句白话解释。"
            " 优先讲清原理与迁移方法，避免被 P1/P2/P3 执行清单喧宾夺主。"
        )
    return (
        "【回复模式】执行模式。先给 30 秒可执行结论（3-5 条动作），再展开依据、风险与回滚建议。"
        " 默认不要展开教学五段（为什么/怎么做/常见误区/自检问题），除非用户明确要求教学。"
        " 避免堆叠背景知识。"
    )

def _learning_level_instruction(response_mode: str, learning_level: str) -> str:
    mode = _normalize_response_mode(response_mode)
    if mode != _RESPONSE_MODE_LEARNING:
        return ""

    level = _normalize_learning_level(learning_level, response_mode=mode)
    label = _LEARNING_LEVEL_LABELS.get(level, "高职")

    if level == _LEARNING_LEVEL_VOCATIONAL:
        return (
            f"【教学层级】{label}。术语先做生活化白话解释，每个关键点配 1 个小例子，步骤不超过 3 步。"
        )

    if level == _LEARNING_LEVEL_UNDERGRADUATE:
        return (
            f"【教学层级】{label}。在可读前提下补充原理，明确相同点/不同点、边界条件与权衡，并给出可验证假设。"
        )

    return (
        f"【教学层级】{label}。强调概念到做法再到场景示例，避免过深理论堆叠。"
    )

def _learning_interaction_instruction(response_mode: str, learning_level: str) -> str:
    mode = _normalize_response_mode(response_mode)
    if mode != _RESPONSE_MODE_LEARNING:
        return ""

    level = _normalize_learning_level(learning_level, response_mode=mode)
    if level == _LEARNING_LEVEL_VOCATIONAL:
        return "【互动引导】详细讲解后补 1 个自检问题（优先是/否或单选），并说明答错时怎么纠正。"
    if level == _LEARNING_LEVEL_UNDERGRADUATE:
        return "【互动引导】详细讲解后补 1 个思考题（请使用‘思考题：’前缀），并给出相同点/不同点对比，以及至少 1 个反例或边界条件纠偏。"
    return "【互动引导】详细讲解后补 1 个自检问题，并补 1 个常见误区纠偏。"

def _apply_response_mode_prompt(system_prompt: str, response_mode: str, learning_level: str = "") -> str:
    mode_header = _response_mode_instruction(response_mode)
    level_header = _learning_level_instruction(response_mode, learning_level)
    interaction_header = _learning_interaction_instruction(response_mode, learning_level)
    headers = "\n".join([
        x
        for x in (mode_header, level_header, interaction_header)
        if str(x or "").strip()
    ])

    if not system_prompt:
        return headers
    if not headers:
        return system_prompt
    return f"{headers}\n{system_prompt}"


def _collaboration_mode_instruction(
    collaboration_mode: str,
    hired_roles: Optional[List[str]] | None = None,
    *,
    response_mode: str = _RESPONSE_MODE_EXECUTION,
) -> str:
    mode = _normalize_collaboration_mode(collaboration_mode)
    normalized_roles = _normalize_hired_roles(
        list(hired_roles or []),
        allow_engineering=True,
        collaboration_mode=mode,
    )
    role_labels = [_role_display_name(role) for role in normalized_roles[:4]]
    normalized_response_mode = _normalize_response_mode(response_mode)
    auto_cap = _support_agent_role_limit(collaboration_mode=_COLLABORATION_MODE_AUTO)

    if mode == _COLLABORATION_MODE_SINGLE:
        return (
            "【协作策略】单角色直答。仅代表当前主角色输出，"
            "不要扩展到多角色分工、会签或并行协作措辞。"
            "禁止出现“协作触发条件/手动协作分工矩阵/角色补位讲解”小节。"
        )

    if mode == _COLLABORATION_MODE_MANUAL:
        if role_labels:
            if normalized_response_mode == _RESPONSE_MODE_LEARNING:
                return (
                    "【协作策略】手动协作。"
                    f"只允许以下已勾选角色参与补位：{'、'.join(role_labels)}。"
                    "禁止引入未勾选角色。"
                    "教学模式下必须增加“角色补位讲解”小节；每个勾选角色至少补 1 条“关注点+证据口径+易错点纠偏”。"
                )
            return (
                "【协作策略】手动协作。"
                f"只允许以下已勾选角色参与补位：{'、'.join(role_labels)}。"
                "禁止引入未勾选角色。"
            )
        return (
            "【协作策略】手动协作。当前未勾选补位角色，请先按主角色给出结论，"
            "如需分工需提示用户勾选参与角色。"
        )

    return (
        "【协作策略】自动协作。先输出主角色结论，"
        "仅在确有必要时补充协作观点，并明确哪些内容属于补位角色建议。"
        f"默认最多补 {auto_cap} 个角色视角，避免协作泛滥。"
    )


def _apply_collaboration_mode_prompt(
    system_prompt: str,
    collaboration_mode: str,
    hired_roles: Optional[List[str]] | None = None,
    *,
    response_mode: str = _RESPONSE_MODE_EXECUTION,
) -> str:
    instruction = _collaboration_mode_instruction(
        collaboration_mode,
        hired_roles=hired_roles,
        response_mode=response_mode,
    )
    if not instruction:
        return system_prompt
    if not system_prompt:
        return instruction
    return f"{instruction}\n{system_prompt}"


_STRATEGY_SECTION_LABELS: Dict[str, str] = {
    "first_screen_exec": "首屏导航（执行）",
    "first_screen_learning": "首屏导航（教学）",
    "first_screen_manual": "首屏协作导航",
    "execution_scope_definition": "口径定义",
    "execution_diagnosis_hypothesis": "诊断假设",
    "execution_validation_plan": "验证方案",
    "execution_why_how": "执行依据（原因与动作方法）",
    "execution_example": "执行场景示例",
    "execution_priority": "优先级",
    "execution_summary": "30秒可执行结论",
    "execution_next_step": "下一步（24小时内）",
    "execution_risk": "风险与回滚",
    "learning_why": "为什么",
    "learning_how": "怎么做",
    "learning_example": "场景示例",
    "learning_pitfall": "常见误区",
    "learning_self_check": "自检问题",
    "learning_level_signature": "教学层级签名",
    "collab_signature_single": "回答策略：单角色直答",
    "collab_signature_manual": "回答策略：手动协作",
    "collab_signature_auto": "回答策略：自动协作",
    "collab_auto_trigger": "协作触发条件",
    "collab_manual_roles": "手动协作角色边界",
    "collab_manual_matrix": "手动协作分工矩阵",
    "collab_manual_role_lens": "角色补位讲解",
}


def _strategy_combo_contract_required_sections(
    *,
    response_mode: str,
    collaboration_mode: str,
    hired_roles: Optional[List[str]] | None = None,
    learning_level: str = _LEARNING_LEVEL_HIGHER_VOCATIONAL,
) -> tuple[str, str, List[str], List[str]]:
    mode = _normalize_response_mode(response_mode)
    collab = _normalize_collaboration_mode(collaboration_mode)
    normalized_roles = _normalize_hired_roles(
        list(hired_roles or []),
        allow_engineering=True,
        collaboration_mode=collab,
    )

    required_sections: List[str] = []
    if mode == _RESPONSE_MODE_LEARNING:
        required_sections.extend(
            [
                "first_screen_learning",
                "learning_why",
                "learning_how",
                "learning_example",
                "learning_pitfall",
                "learning_self_check",
                "learning_level_signature",
            ]
        )
    else:
        required_sections.extend(
            [
                "first_screen_exec",
                "execution_summary",
                "execution_scope_definition",
                "execution_diagnosis_hypothesis",
                "execution_validation_plan",
                "execution_why_how",
                "execution_example",
                "execution_priority",
                "execution_next_step",
                "execution_risk",
            ]
        )

    if collab == _COLLABORATION_MODE_SINGLE:
        required_sections.append("collab_signature_single")
    elif collab == _COLLABORATION_MODE_MANUAL:
        required_sections.append("first_screen_manual")
        required_sections.append("collab_signature_manual")
        required_sections.append("collab_manual_roles")
        required_sections.append("collab_manual_matrix")
        if mode == _RESPONSE_MODE_LEARNING and normalized_roles:
            required_sections.append("collab_manual_role_lens")
    else:
        required_sections.append("collab_signature_auto")
        required_sections.append("collab_auto_trigger")

    deduped_sections: List[str] = []
    seen_sections: set[str] = set()
    for section_id in required_sections:
        token = str(section_id or "").strip()
        if not token or token in seen_sections:
            continue
        seen_sections.add(token)
        deduped_sections.append(token)

    return mode, collab, normalized_roles, deduped_sections


def _strategy_combo_contract_instruction(
    *,
    response_mode: str,
    collaboration_mode: str,
    learning_level: str,
    hired_roles: Optional[List[str]] | None = None,
) -> str:
    mode, collab, normalized_roles, required_sections = _strategy_combo_contract_required_sections(
        response_mode=response_mode,
        collaboration_mode=collaboration_mode,
        hired_roles=hired_roles,
        learning_level=learning_level,
    )
    role_labels = [_role_display_name(role) for role in normalized_roles[:4]]

    mode_label = "教学模式" if mode == _RESPONSE_MODE_LEARNING else "执行模式"
    collab_label = {
        _COLLABORATION_MODE_SINGLE: "单角色直答",
        _COLLABORATION_MODE_MANUAL: "手动协作",
        _COLLABORATION_MODE_AUTO: "自动协作",
    }.get(collab, "自动协作")

    required_labels = [
        _STRATEGY_SECTION_LABELS.get(section_id, section_id)
        for section_id in required_sections
    ]

    lines: List[str] = [
        f"【回答策略合同】本轮固定为“{mode_label} + {collab_label}”，不要混写其他模式话术。",
        "请显式包含以下关键小节（允许同义标题）："
        + "、".join(required_labels),
        "首屏必须先给“首屏导航”块：3行内说明本轮结论主线、执行顺序和风险/协作触发。",
    ]

    if mode == _RESPONSE_MODE_LEARNING:
        level_label = _LEARNING_LEVEL_LABELS.get(
            _normalize_learning_level(learning_level, response_mode=mode),
            "高职",
        )
        lines.append(f"教学层级按“{level_label}”口径输出，术语需给白话解释。")
        lines.append("教学模式优先保证“概念澄清+迁移理解”，不要把内容写成 P1/P2/P3 执行清单。")
    else:
        lines.append("执行模式请补齐“口径定义→诊断假设→验证方案→优先级→24小时下一步”闭环，并至少给1个可追踪指标。")
        lines.append("若用户同时提到教学/解释诉求，执行模式可补1句“原因说明+动作方法”和1个场景示例，但不要切成教学五段结构。")

    if collab == _COLLABORATION_MODE_MANUAL:
        if role_labels:
            lines.append(f"手动协作仅允许这些勾选角色参与：{'、'.join(role_labels)}。")
            lines.append("每个勾选角色最多补 1 条关键证据与 1 条风险提示，避免同质化重复。")
        else:
            lines.append("手动协作当前未勾选角色，先按主角色输出，不要虚构补位角色。")
    elif collab == _COLLABORATION_MODE_AUTO:
        lines.append(f"自动协作请明确“协作触发条件”，并将补位角色控制在最多{_support_agent_role_limit(collaboration_mode=_COLLABORATION_MODE_AUTO)}个，避免无差别堆叠多角色口径。")

    return "\n".join(lines)


def _apply_strategy_combo_contract_prompt(
    system_prompt: str,
    *,
    response_mode: str,
    collaboration_mode: str,
    learning_level: str,
    hired_roles: Optional[List[str]] | None = None,
) -> str:
    instruction = _strategy_combo_contract_instruction(
        response_mode=response_mode,
        collaboration_mode=collaboration_mode,
        learning_level=learning_level,
        hired_roles=hired_roles,
    )
    if not instruction:
        return system_prompt
    if not system_prompt:
        return instruction
    return f"{instruction}\n{system_prompt}"


_QUALITY_DIMENSION_REWRITE_GUIDANCE: Dict[str, str] = {
    "completeness": "补全用户问题核心要素，避免遗漏关键约束。",
    "accuracy": "避免绝对化结论；对不确定信息加“需验证/仅供参考”标注。",
    "fabrication": "删除用户未提供的具体数字；若必须举例，显式标注为“行业参考值/示例”。",
    "actionability": "补齐可执行动作：编号步骤 + 负责人/时间点 + 验收标准。",
    "relevance": "围绕用户目标重写，删除与目标无关的泛化段落。",
    "goal_satisfaction": "输出“结论 + 步骤 + 量化目标 + 下一步动作”，直接对应用户目标。",
    "value_density": "减少套话，补充“新增信息 + 决策依据 + 触发条件”，确保每段都有信息增量。",
    "clarity": "使用短段落和清晰标题，保持层次可读。",
    "risk_awareness": "补充风险、触发条件、回滚动作与兜底方案。",
    "confidence_marking": "对预测性/估算内容补充置信边界与适用条件。",
}


_TOOL_EVIDENCE_MARKER_RE = re.compile(
    r"证据|来源：|数据来源|根据工具|工具结果|检索结果|搜索结果|返回结果|接口返回",
    re.IGNORECASE,
)


def _inject_tool_evidence_disclosure(reply: str, skills_used: List[str]) -> str:
    body = str(reply or "").strip()
    if not body:
        return body

    used = [str(name or "").strip() for name in (skills_used or []) if str(name or "").strip()]
    if not used:
        return body

    if _TOOL_EVIDENCE_MARKER_RE.search(body):
        return body

    tools_text = "、".join(used[:3])
    suffix = (
        "\n\n证据来源：本轮已调用工具 "
        f"{tools_text}"
        "。上述结论基于工具返回结果整理；若需要，我可以继续展开每条证据与结论对应关系。"
    )
    return body + suffix


def _inject_attachment_vision_fallback_notice(reply: str, attachments: List[Dict[str, Any]]) -> str:
    body = str(reply or "").strip()
    if not body:
        return body

    items = attachments if isinstance(attachments, list) else []
    degraded: List[tuple[int, str, str]] = []
    for idx, item in enumerate(items, 1):
        if not isinstance(item, dict):
            continue
        warning = str(item.get("vision_warning") or "").strip()
        engine = str(item.get("vision_engine") or "").strip().lower()
        is_degraded = bool(item.get("vision_is_degraded")) or bool(warning) or engine in {"ocr-fallback", "fallback-ocr"}
        if not is_degraded:
            continue
        filename = str(item.get("filename") or item.get("name") or f"附件{idx}").strip() or f"附件{idx}"
        reason = str(item.get("vision_fallback_reason") or "").strip().lower()
        if reason.startswith("network_"):
            reason_label = "豆包网络故障"
        elif reason.startswith("upstream_status_"):
            reason_label = "豆包服务异常"
        elif reason == "config_missing":
            reason_label = "豆包配置缺失"
        else:
            reason_label = "豆包读图不可用"
        warning_text = warning or "【醒目提示】豆包读图暂不可用，已切换为 OCR 兜底，识别质量可能下降。"
        degraded.append((idx, filename, f"{warning_text}（{reason_label}）"))

    if not degraded:
        return body

    if "豆包读图" in body and "OCR" in body:
        return body

    lines = ["【醒目提示】检测到图像附件使用 OCR 兜底，请谨慎使用识别结果："]
    for idx, filename, warning_line in degraded[:3]:
        lines.append(f"- [附件{idx}] {filename}: {warning_line}")
    notice = "\n".join(lines)
    return f"{notice}\n\n{body}"


_CAPACITY_SLA_MARKER_RE = re.compile(r"容量|SLA|优先级|工单|队列", re.IGNORECASE)


def _inject_constraint_capacity_sla_guard(reply: str, message: str, response_mode: str) -> str:
    body = str(reply or "").strip()
    if not body:
        return body

    if _normalize_response_mode(response_mode) != _RESPONSE_MODE_EXECUTION:
        return body

    msg = str(message or "").strip()
    if not msg:
        return body

    carryover_required = _contains_any(msg.lower(), _TASK_CARRYOVER_HINTS)
    if not carryover_required:
        return body

    anchors: List[str] = []
    for hit in _TASK_CONSTRAINT_ANCHOR_RE.findall(msg):
        token = str(hit or "").strip()
        if token and token not in anchors:
            anchors.append(token)
        if len(anchors) >= 3:
            break

    if not anchors:
        return body

    if _CAPACITY_SLA_MARKER_RE.search(body):
        return body

    anchor_text = "、".join(anchors)
    suffix = (
        "\n\n容量与SLA优先级补充：\n"
        f"- 新增约束复述：{anchor_text}。\n"
        "- 容量评估：按客服总处理上限拆分时段队列，超上限工单进入延时回访池并标注处理时限。\n"
        "- SLA优先级：P1（支付/退款风险）> P2（物流异常）> P3（一般咨询），高峰时先保P1与高转化会话。\n"
        "- 取舍逻辑：当峰值超上限时，先保障高风险与高价值工单，其余按承诺时限排队处理。"
    )
    return body + suffix


def _build_quality_retry_requirements(
    *,
    quality: Any,
    response_mode: str,
    learning_level: str,
    action: str,
    role: str,
    tool_used: bool,
) -> List[str]:
    requirements: List[str] = []
    mode = _normalize_response_mode(response_mode)
    action_token = str(action or "").strip().lower()
    role_token = str(role or "").strip().lower()

    if mode == _RESPONSE_MODE_LEARNING:
        requirements.append("按“30秒结论-为什么-怎么做-常见误区”结构重写。")
        requirements.append("必须补1个电商场景示例，并补1个自检/思考题。")
        if _normalize_learning_level(learning_level, response_mode=mode) == _LEARNING_LEVEL_UNDERGRADUATE:
            requirements.append("必须补“相同点/不同点 + 反例边界条件 + 可验证假设”。")
    else:
        requirements.append("按“30秒结论-可执行步骤-风险与回滚”结构重写。")

    if action_token in {"create", "optimize", "execute", "plan"}:
        requirements.append("必须包含编号步骤、量化目标(KPI)与下一步动作。")
    if action_token in {"analysis", "analyze", "diagnosis", "diagnose"}:
        requirements.append("必须补“诊断假设-验证设计-优先级(P1/P2/P3)-指标口径”。")
    if role_token == "design":
        requirements.append(
            "必须补“版块优先级(P1/P2/P3)-A/B实验矩阵(对照/测试/样本/周期)-指标阈值与回滚条件-仅供参考并基线校准”。"
        )
    if role_token in {"ops", "accounting", "engineering", "service"}:
        requirements.append("必须补充风险提示与避免建议。")

    if tool_used:
        requirements.append(
            "若使用了工具/检索，必须包含“证据->结论”映射：至少列1-3条关键证据（来源或工具结果）并说明如何支撑结论；"
            "若本轮工具无有效返回，需显式写明“本轮工具未返回有效证据”。"
        )

    dims = getattr(quality, "dimensions", {}) if quality is not None else {}
    if isinstance(dims, dict):
        low_dim_items = sorted(
            ((str(k), float(v)) for k, v in dims.items()),
            key=lambda item: item[1],
        )
        for dim_name, dim_score in low_dim_items:
            if dim_score >= 0.72:
                continue
            guidance = _QUALITY_DIMENSION_REWRITE_GUIDANCE.get(dim_name)
            if guidance:
                requirements.append(f"{dim_name}维度偏低({dim_score:.0%})，重写时需：{guidance}")

    try:
        from src.core.prompt_injectors import get_role_output_contract

        role_contract = str(get_role_output_contract(role_token) or "").strip()
    except Exception:
        role_contract = ""
    if role_contract:
        requirements.append(f"必须满足岗位输出契约：{role_contract}")

    deduped: List[str] = []
    seen: set[str] = set()
    for item in requirements:
        line = str(item or "").strip()
        if not line or line in seen:
            continue
        seen.add(line)
        deduped.append(line)
    return deduped


def _normalize_outline_point(raw: str, *, max_chars: int = 80) -> str:
    text = _OUTLINE_BULLET_RE.sub("", str(raw or "").strip())
    text = re.sub(r"\s+", " ", text).strip(' -?')
    if len(text) > max_chars:
        text = text[:max_chars].rstrip('??:?')
        text = f"{text}..."
    return text


def _extract_outline_points(text: str, *, limit: int) -> List[str]:
    if not text or limit <= 0:
        return []

    lines = [str(x).strip() for x in text.splitlines() if str(x).strip()]
    points: List[str] = []
    seen: set[str] = set()

    def _push(candidate: str) -> None:
        item = _normalize_outline_point(candidate)
        if len(item) < 4:
            return
        key = re.sub(r"\s+", "", item).lower()
        if not key or key in seen:
            return
        seen.add(key)
        points.append(item)

    for line in lines:
        if _OUTLINE_BULLET_RE.match(line):
            _push(line)
            if len(points) >= limit:
                return points[:limit]

    if not points:
        compact_text = re.sub(r"\s+", " ", text).strip()
        sentences = re.split(r"(?<=[。！？!?])\s*", compact_text)
        for sentence in sentences:
            if not sentence:
                continue
            _push(sentence)
            if len(points) >= limit:
                return points[:limit]

    if not points and lines:
        _push(lines[0])

    return points[:limit]


def _build_response_outline(
    reply: str,
    response_mode: str,
    *,
    llm_is_asking: bool = False,
) -> Dict[str, Any]:
    text = str(reply or "").strip()
    if llm_is_asking or len(text) < 24:
        return {}

    mode = _normalize_response_mode(response_mode)
    summary_limit = 3 if mode == _RESPONSE_MODE_LEARNING else 5
    summary_points = _extract_outline_points(text, limit=summary_limit)
    if not summary_points:
        return {}

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p and p.strip()]
    detail_source = paragraphs[1] if len(paragraphs) > 1 else text
    detail_preview = re.sub(r"\s+", " ", detail_source).strip()
    if len(detail_preview) > 140:
        detail_preview = detail_preview[:140].rstrip("。！？!?;；:,，")
        detail_preview = f"{detail_preview}..."

    action_points: List[str] = []
    required_action_points = 3 if mode == _RESPONSE_MODE_EXECUTION else 0
    if mode == _RESPONSE_MODE_EXECUTION:
        action_signal_re = re.compile(r"今日先做|下一步|行动项|24小时|P[0-3]|执行|落地|验证|回滚|止损", re.IGNORECASE)
        action_points = [item for item in summary_points if action_signal_re.search(item)]

        if len(action_points) < required_action_points:
            action_candidates = _extract_outline_points(detail_source, limit=6)
            for item in action_candidates:
                if action_signal_re.search(item):
                    action_points.append(item)
                if len(action_points) >= required_action_points:
                    break

        deduped_actions: List[str] = []
        seen_action_keys: set[str] = set()
        for item in action_points:
            normalized = re.sub(r"\s+", "", str(item or "")).lower()
            if not normalized or normalized in seen_action_keys:
                continue
            seen_action_keys.add(normalized)
            deduped_actions.append(str(item).strip())

        action_points = deduped_actions

        if len(action_points) < required_action_points:
            fallback = [
                summary_points[0] if summary_points else "先统一目标、口径和验收阈值。",
                summary_points[1] if len(summary_points) > 1 else "按“今日执行-24小时复盘”两步推进。",
                summary_points[2] if len(summary_points) > 2 else "下一步（24小时内）：今天先落地1个高影响动作，明天按核心指标复盘后决定加码或回滚。",
            ]
            for item in fallback:
                token = str(item or "").strip()
                if not token:
                    continue
                if token not in action_points:
                    action_points.append(token)
                if len(action_points) >= required_action_points:
                    break

        if len(action_points) < required_action_points:
            canonical_fallbacks = [
                "P1（最高优先级）：先统一目标、口径与验收阈值，再启动首轮动作。",
                "P2（次高优先级）：按“影响度×验证成本”推进验证，并记录核心指标变化。",
                "下一步（24小时内）：完成第1个高影响动作并复盘指标，决定加码或回滚。",
            ]
            for item in canonical_fallbacks:
                token = str(item or "").strip()
                if token and token not in action_points:
                    action_points.append(token)
                if len(action_points) >= required_action_points:
                    break

    short_first_ready = bool(
        len(summary_points) >= 2
        and (mode != _RESPONSE_MODE_EXECUTION or len(action_points) >= required_action_points)
    )

    return {
        "summary_points": summary_points[:summary_limit],
        "action_points": action_points[:3],
        "detail_title": "详细讲解" if mode == _RESPONSE_MODE_LEARNING else "详细展开",
        "detail_preview": detail_preview,
        "mode": mode,
        "short_first_ready": short_first_ready,
    }


_EXECUTION_ACTION_ORIENTED_ACTIONS = {"create", "optimize", "execute", "plan"}
_EXECUTION_LIST_LINE_RE = re.compile(r"^\s*(?:[-•*]|\d+[.、\)])", re.MULTILINE)
_EXECUTION_NEXT_STEP_RE = re.compile(r"(?:^|\n)\s*(?:(?:[-•*]|\d+[.、\)])\s*)?(?:下一步|先做|先执行|今日动作|本周计划|24小时内|明日动作|执行顺序|行动项)[:：]?", re.MULTILINE)



def _inject_short_first_summary_guard(
    reply: str,
    *,
    response_mode: str,
    message: str = "",
    llm_is_asking: bool = False,
) -> tuple[str, Dict[str, Any]]:
    text = str(reply or "").strip()
    mode = _normalize_response_mode(response_mode)

    meta: Dict[str, Any] = {
        "applied": False,
        "reason": "",
        "summary_points": [],
        "action_points": [],
    }

    if not text:
        meta["reason"] = "empty_reply"
        return reply, meta

    if llm_is_asking:
        meta["reason"] = "asking_user"
        return reply, meta

    if _looks_like_smalltalk_message(str(message or "")):
        meta["reason"] = "smalltalk_no_short_first_guard"
        return reply, meta

    if len(text) < 80:
        meta["reason"] = "reply_short_enough"
        return reply, meta

    has_short_first_heading = bool(re.search(r"##\s*30\s*秒结论", text))
    has_short_first_semantic = bool(re.search(r"30\s*秒(?:可执行)?(?:结论|动作)", text))
    has_next_step = bool(_EXECUTION_NEXT_STEP_RE.search(text))

    if mode == _RESPONSE_MODE_EXECUTION and (has_short_first_heading or has_short_first_semantic):
        if not has_next_step and not _MODE_COPY_READY_MESSAGE_RE.search(str(message or "")):
            next_step_line = (
                "下一步（24小时内）：今天先做目标定义（平台、目标指标、预算上限）并启动首轮动作，"
                "明天按核心指标复盘后决定加码或回滚。"
            )
            guarded = f"{text}\n\n{next_step_line}"
            meta["applied"] = True
            meta["reason"] = "short_first_next_step_injected"
            meta["action_points"] = [next_step_line]
            return guarded, meta

        meta["reason"] = "already_has_short_first_semantic"
        return reply, meta

    if len(text) < 260:
        meta["reason"] = "reply_short_enough"
        return reply, meta

    outline = _build_response_outline(text, mode, llm_is_asking=False)
    summary_points = [str(x).strip() for x in (outline.get("summary_points") or []) if str(x).strip()]
    action_points = [str(x).strip() for x in (outline.get("action_points") or []) if str(x).strip()]

    if not summary_points:
        meta["reason"] = "no_summary_points"
        return reply, meta

    lines: List[str] = ["## 30秒结论"]
    for item in summary_points[:3]:
        lines.append(f"- {item}")

    if mode == _RESPONSE_MODE_EXECUTION and action_points:
        lines.append("")
        lines.append("### 今日先做")
        for idx, item in enumerate(action_points[:3], start=1):
            lines.append(f"{idx}. {item}")

    guarded = "\n".join(lines).strip() + "\n\n---\n\n" + text
    meta["applied"] = True
    meta["reason"] = "short_first_summary_injected"
    meta["summary_points"] = summary_points[:3]
    meta["action_points"] = action_points[:3]
    return guarded, meta

_EXECUTION_KPI_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％|元|万|天|周|月)|KPI|ROI|ROAS|GMV|转化率|指标")
_EXECUTION_RISK_RE = re.compile(r"风险|注意|回滚|止损|合规|预案|兜底")
_ANALYSIS_HYPOTHESIS_RE = re.compile(r"假设|可能原因|根因|原因假设|诊断假设")
_ANALYSIS_VERIFY_RE = re.compile(r"验证|校验|对照|实验|A/B|AB测试|回归|排查")
_ANALYSIS_PRIORITY_RE = re.compile(r"P[0-3]|优先级|先验证|高优先|中优先|低优先")
_ANALYSIS_METRIC_RE = re.compile(r"指标|口径|阈值|KPI|ROI|ROAS|GMV|转化率")
_DESIGN_PRIORITY_RE = re.compile(r"优先级|P[0-3]|高优先|中优先|低优先|先做|后做|信息层级")
_DESIGN_PRIORITY_TIER_RE = re.compile(r"P1|P2|P3|高优先|中优先|低优先")
_DESIGN_PRIORITY_MODULE_RE = re.compile(r"首屏|卖点|详情页|参数|对比|评价|买家秀|保障|FAQ|版块|模块")
_DESIGN_EXPERIMENT_RE = re.compile(r"A/B|AB测试|实验组|对照组|版本A|版本B|测试假设|实验设计")
_DESIGN_EXPERIMENT_CONTROL_RE = re.compile(r"对照组|测试组|实验组|版本A|版本B")
_DESIGN_EXPERIMENT_WINDOW_RE = re.compile(r"\d+\s*(?:天|周)|周期|窗口|样本|流量占比|50%|30%")
_DESIGN_METRIC_RE = re.compile(r"CTR|CVR|转化率|点击率|跳失率|停留时长|加购率|指标|阈值|显著")
_DESIGN_METRIC_THRESHOLD_RE = re.compile(r"\d+(?:\.\d+)?\s*(?:%|％)|阈值|显著|提升|下降|高于|低于|>=|<=|回滚")
_DESIGN_CALIBRATION_RE = re.compile(r"仅供参考|行业参考值|基线|校准|按实际|因平台而异|需复核|视情况")
_DESIGN_SPEC_RE = re.compile(r"尺寸|像素|px|色值|#[0-9a-fA-F]{3,6}|字体|字重|留白|栅格|间距|对比度|可访问")
_DESIGN_ACCEPTANCE_RE = re.compile(r"验收|检查清单|通过标准|上线条件|发布门禁|QA|验收项")
_WEB_KEYWORD_LAYER_RE = re.compile(r"关键词层级|关键词分层|核心词|次级词|长尾词|词簇|关键词矩阵")
_WEB_INTERNAL_LINK_RE = re.compile(r"内链|站内链|内部链接|锚文本|Hub页|支柱页|集群页|互链")
_WEB_CLUSTER_RE = re.compile(r"内容集群|主题集群|topic cluster|主题簇|内容矩阵", re.IGNORECASE)
_WEB_TIMELINE_RE = re.compile(r"\d+\s*(?:天|周)|第[一二三四五六七八九十]周|D\d+|30天|周计划")
_CREATIVE_VARIANTS_RE = re.compile(r"版本\s*[A-C]|版本\s*[1-3]|方案[一二三]|3\s*(?:套|个)?(?:版本|方案|创意)|至少\s*3\s*(?:套|个)?")
_CREATIVE_HOOK_RE = re.compile(r"钩子|开场(?:前|首)?\s*3秒|前3秒|前5秒|黄金3秒|抓注意")
_CREATIVE_SCRIPT_RE = re.compile(r"脚本|分镜|镜头|口播|字幕|时长|节奏|开场|转折|结尾|CTA")
_CREATIVE_METRIC_RE = re.compile(r"完播率|互动率|点击率|CTR|转化率|收藏率|播放|目标|阈值")

_EXECUTION_CONFIDENCE_MARKERS: tuple[str, ...] = (
    "仅供参考",
    "行业参考值",
    "行业均值",
    "参考数据",
    "估算",
    "基于假设",
    "典型案例",
)
_EXECUTION_FORECAST_HINT_RE = re.compile(
    r"预计|预估|预测|目标|提升|增长|下降|波动|风险|概率|区间|约|大约|可能"
)
_EXECUTION_DELIVERY_SUPPLEMENT_TITLE = "## 补充执行要点"
_LEGACY_EXECUTION_DELIVERY_SUPPLEMENT_TITLE = "## 执行补全（系统保障）"


def _append_execution_confidence_note_if_needed(
    text: str,
    *,
    role: str,
    action: str,
) -> tuple[str, bool]:
    body = str(text or "").strip()
    role_token = str(role or "").strip().lower()
    action_token = str(action or "").strip().lower()
    data_actions = {"analysis", "analyze", "diagnosis", "diagnose", "plan", "optimize", "execute"}
    min_len = 180 if role_token == "data" and action_token in data_actions else 280
    if (
        _EXECUTION_DELIVERY_SUPPLEMENT_TITLE in body
        or _LEGACY_EXECUTION_DELIVERY_SUPPLEMENT_TITLE in body
    ):
        min_len = min(min_len, 220)
    if len(body) < min_len:
        return body, False

    if any(marker in body for marker in _EXECUTION_CONFIDENCE_MARKERS):
        return body, False

    # 设计/SEO/创意类同样存在阈值与趋势判断，默认补一条置信度声明，降低“过度确定”风险。
    if role_token in {"design", "web", "creative"} and action_token in {"create", "optimize", "plan", "execute"}:
        note = "说明：以上阈值、流量占比和转化预估属于方案参考值，需结合你的近14天基线数据复核后执行。"
        return f"{body}\n\n{note}", True

    # 数据诊断类回复常被用于直接决策，默认补“复核 + 偏差阈值 + 重新排序”说明，稳定降低过度自信表达。
    if role_token == "data" and action_token in data_actions:
        note = (
            "说明：以上诊断中的比例、阈值与优先级仅供参考，需以你的实时分渠道数据复核；"
            "若与当前基线偏差超过 10%，请以实时数据优先并重排验证优先级。"
        )
        return f"{body}\n\n{note}", True

    # 财务/风控类长回复默认补充置信度说明，避免“过度确定”表述。
    if role_token != "accounting" and not _EXECUTION_FORECAST_HINT_RE.search(body):
        return body, False
    if role_token not in {"ops", "data", "accounting", "service"} and action_token not in {"analyze", "analysis", "optimize", "plan", "execute"}:
        return body, False

    note = "说明：涉及预估、行业均值与趋势判断的数值仅供参考，需以你的实时业务数据复核后执行。"
    return f"{body}\n\n{note}", True


def _collect_execution_delivery_gaps(
    reply: str,
    quality_issues: List[str] | None = None,
    *,
    role: str = "",
    action: str = "",
    message: str = "",
) -> Dict[str, bool]:
    text = str(reply or "")
    issue_text = "；".join([str(x).strip() for x in (quality_issues or []) if str(x).strip()])
    role_token = str(role or "").strip().lower()
    action_token = str(action or "").strip().lower()

    missing_list = not bool(_EXECUTION_LIST_LINE_RE.search(text))
    missing_next_step = not bool(_EXECUTION_NEXT_STEP_RE.search(text))
    missing_kpi = not bool(_EXECUTION_KPI_RE.search(text))
    missing_risk = not bool(_EXECUTION_RISK_RE.search(text))

    if "结构化步骤" in issue_text:
        missing_list = True
    if "下一步" in issue_text:
        missing_next_step = True
    if "量化目标" in issue_text or "KPI" in issue_text:
        missing_kpi = True
    if "风险" in issue_text or "回滚" in issue_text:
        missing_risk = True

    web_context = role_token == "web" or ("seo" in action_token)
    missing_keyword_layer = False
    missing_internal_link = False
    missing_cluster_timeline = False

    if web_context:
        missing_keyword_layer = not bool(_WEB_KEYWORD_LAYER_RE.search(text))
        missing_internal_link = not bool(_WEB_INTERNAL_LINK_RE.search(text))
        has_cluster = bool(_WEB_CLUSTER_RE.search(text))
        has_timeline = bool(_WEB_TIMELINE_RE.search(text))
        missing_cluster_timeline = not (has_cluster and has_timeline)

        if "关键词" in issue_text or "层级" in issue_text:
            missing_keyword_layer = True
        if "内链" in issue_text or "锚文本" in issue_text:
            missing_internal_link = True
        if "集群" in issue_text or "30天" in issue_text or "周期" in issue_text:
            missing_cluster_timeline = True

    creative_context = role_token == "creative" or ("creative" in action_token)
    missing_creative_variants = False
    missing_creative_hook = False
    missing_creative_script_metric = False

    if creative_context:
        missing_creative_variants = not bool(_CREATIVE_VARIANTS_RE.search(text))
        missing_creative_hook = not bool(_CREATIVE_HOOK_RE.search(text))
        has_script = bool(_CREATIVE_SCRIPT_RE.search(text))
        has_metric = bool(_CREATIVE_METRIC_RE.search(text))
        missing_creative_script_metric = not (has_script and has_metric)

        if "版本" in issue_text or "创意" in issue_text or "三套" in issue_text:
            missing_creative_variants = True
        if "钩子" in issue_text or "前3秒" in issue_text or "开场" in issue_text:
            missing_creative_hook = True
        if "脚本" in issue_text or "分镜" in issue_text or "完播率" in issue_text or "互动率" in issue_text:
            missing_creative_script_metric = True

    data_context = role_token == "data" or bool(
        re.search(r"数据|份额|口径|假设|来源|出处|引用|样本|同比|环比|市场", str(message or ""))
    )
    service_context = role_token == "service" or bool(
        re.search(r"客服|客诉|投诉|安抚|升级|工单|补偿|回访|SLA", str(message or ""), re.IGNORECASE)
    )
    missing_data_evidence = False
    if data_context:
        has_data_method = bool(re.search(r"口径|假设|来源|出处|引用|数据源|样本", text))
        has_quant_data = bool(re.search(r"\d+[%元万亿]|[0-9]+\.[0-9]", text))
        missing_data_evidence = not (has_data_method and has_quant_data)
        if (
            "数据角色回复未包含量化数据" in issue_text
            or ("来源" in issue_text and ("缺少" in issue_text or "未" in issue_text))
            or ("口径" in issue_text and ("缺少" in issue_text or "未" in issue_text))
            or ("假设" in issue_text and ("缺少" in issue_text or "未" in issue_text))
        ):
            missing_data_evidence = True

    missing_service_playbook = False
    if service_context:
        has_sla_threshold = bool(re.search(r"SLA|首响|首轮响应|<=?\s*\d+\s*(?:秒|分钟|分|小时)", text, re.IGNORECASE))
        has_trigger = bool(re.search(r"触发|升级条件|门禁|阈值|超时", text))
        has_service_fallback = bool(re.search(r"回滚|兜底|转人工|升级|工单分级", text))
        missing_service_playbook = not (has_sla_threshold and has_trigger and has_service_fallback)
        if (
            "高风险场景缺少风险或回滚提示" in issue_text
            or "service角色长回复未包含风险提示" in issue_text
            or ("时效" in issue_text and ("缺少" in issue_text or "未" in issue_text))
        ):
            missing_service_playbook = True

    return {
        "list": bool(missing_list),
        "next_step": bool(missing_next_step),
        "kpi": bool(missing_kpi),
        "risk": bool(missing_risk),
        "keyword_layer": bool(missing_keyword_layer),
        "internal_link": bool(missing_internal_link),
        "cluster_timeline": bool(missing_cluster_timeline),
        "creative_variants": bool(missing_creative_variants),
        "creative_hook": bool(missing_creative_hook),
        "creative_script_metric": bool(missing_creative_script_metric),
        "data_evidence": bool(missing_data_evidence),
        "service_playbook": bool(missing_service_playbook),
    }


def _apply_execution_delivery_guard(
    reply: str,
    *,
    response_mode: str,
    role: str,
    action: str,
    message: str = "",
    llm_is_asking: bool = False,
    quality_issues: List[str] | None = None,
) -> tuple[str, Dict[str, Any]]:
    text = str(reply or "").strip()
    mode = _normalize_response_mode(response_mode)
    action_token = str(action or "").strip().lower()
    role_token = str(role or "").strip().lower()

    meta: Dict[str, Any] = {
        "applied": False,
        "reason": "",
        "gaps": {},
    }

    if not text:
        meta["reason"] = "empty_reply"
        return reply, meta

    if llm_is_asking:
        meta["reason"] = "asking_user"
        return reply, meta

    if mode != _RESPONSE_MODE_EXECUTION:
        meta["reason"] = "not_execution_mode"
        return reply, meta

    if _looks_like_smalltalk_message(str(message or "")):
        meta["reason"] = "smalltalk_no_execution_guard"
        return reply, meta

    if _MODE_COPY_READY_MESSAGE_RE.search(str(message or "")):
        meta["reason"] = "copy_ready_scene_skip_execution_guard"
        return reply, meta

    enforce_roles = {"ops", "data", "accounting", "service", "engineering", "creative", "design", "web"}
    if action_token not in _EXECUTION_ACTION_ORIENTED_ACTIONS and role_token not in enforce_roles:
        meta["reason"] = "non_action_query"
        return reply, meta

    text, confidence_note_added = _append_execution_confidence_note_if_needed(
        text,
        role=role_token,
        action=action_token,
    )
    meta["confidence_note_added"] = bool(confidence_note_added)

    issue_items = [str(x).strip() for x in (quality_issues or []) if str(x).strip()]
    if not issue_items:
        if confidence_note_added:
            meta["applied"] = True
            meta["reason"] = "confidence_note_added"
            return text, meta
        meta["reason"] = "quality_issues_empty_skip"
        return reply, meta

    issue_text = "；".join(issue_items)
    issue_needs_guard = any(
        token in issue_text
        for token in (
            "缺少", "不足", "可执行", "下一步", "量化", "风险", "回滚", "目标满足度", "价值密度", "套话"
        )
    )
    meta["issue_needs_guard"] = bool(issue_needs_guard)
    if not issue_needs_guard:
        if confidence_note_added:
            meta["applied"] = True
            meta["reason"] = "confidence_note_added"
            return text, meta
        meta["reason"] = "quality_issues_not_delivery_related"
        return reply, meta

    gaps = _collect_execution_delivery_gaps(
        text,
        quality_issues=quality_issues,
        role=role_token,
        action=action_token,
        message=message,
    )
    meta["gaps"] = gaps
    if not any(bool(v) for v in gaps.values()):
        if confidence_note_added:
            meta["applied"] = True
            meta["reason"] = "confidence_note_added"
            return text, meta
        meta["reason"] = "already_complete"
        return reply, meta

    if (
        _EXECUTION_DELIVERY_SUPPLEMENT_TITLE in text
        or _LEGACY_EXECUTION_DELIVERY_SUPPLEMENT_TITLE in text
    ):
        meta["reason"] = "already_guarded"
        return reply, meta

    supplement_items: List[str] = []
    if gaps.get("list"):
        supplement_items.append("执行动作：将任务拆成3个可追踪动作，按“高影响、低成本”优先级推进。")
    if gaps.get("next_step"):
        supplement_items.append("下一步（24小时内）：完成基线盘点、负责人分配、以及首轮执行排期。")
    if gaps.get("kpi"):
        supplement_items.append("量化KPI：7天内关键指标提升不低于 5%，并将成本偏差控制在 10% 以内。")
    if gaps.get("risk"):
        supplement_items.append("风险与回滚：若关键指标连续2天恶化，立即暂停新增动作并回滚到上一个稳定版本。")
    if gaps.get("keyword_layer"):
        supplement_items.append("关键词层级：拆成核心词（品牌/品类）-场景词-长尾问题词，并为每层定义落地页面。")
    if gaps.get("internal_link"):
        supplement_items.append("内链策略：建立1个Hub页+若干支持页，统一锚文本规则，确保核心页获得稳定内链投票。")
    if gaps.get("cluster_timeline"):
        supplement_items.append("集群节奏：按30天拆解为“选词建簇-内容发布-排名观察-迭代扩簇”四周节奏，并定义周复盘动作。")
    if gaps.get("creative_variants"):
        supplement_items.append("创意版本：至少给出3套可执行版本（如利益型/反常识型/痛点反转型），并标注各自适用人群。")
    if gaps.get("creative_hook"):
        supplement_items.append("开场钩子：每套版本补“前3秒钩子”，至少含口播句和画面动作，确保能直接拍摄落地。")
    if gaps.get("creative_script_metric"):
        supplement_items.append("脚本与指标：补“分镜/台词/时长/CTA”，并定义完播率、互动率、点击率目标及淘汰阈值（仅供参考，需基线校准）。")
    if gaps.get("data_evidence"):
        supplement_items.append("数据口径与假设：先明确时间窗口/地域范围/统计口径，并为每条结论标注来源与日期；量化示例可按“份额同比-2.0%、环比-1.2%”书写。")
    if gaps.get("service_playbook"):
        supplement_items.append("服务门禁与升级：设置首响≤30秒、方案反馈≤20分钟、升级转接≤10分钟；明确触发阈值（用户二次拒绝/明确投诉/超权限诉求）与回滚动作（立即转人工并同步工单记录）。")

    if len(supplement_items) == 1:
        guarded_reply = f"{text}\n\n补充：{supplement_items[0]}"
    else:
        lines: List[str] = [_EXECUTION_DELIVERY_SUPPLEMENT_TITLE]
        for idx, item in enumerate(supplement_items, start=1):
            lines.append(f"{idx}. {item}")
        guarded_reply = f"{text}\n\n" + "\n".join(lines)
    guarded_reply, late_confidence_note_added = _append_execution_confidence_note_if_needed(
        guarded_reply,
        role=role_token,
        action=action_token,
    )
    confidence_note_added = bool(confidence_note_added or late_confidence_note_added)
    meta["confidence_note_added"] = confidence_note_added
    meta["applied"] = True
    meta["reason"] = "delivery_and_confidence_guard_applied" if confidence_note_added else "delivery_guard_applied"
    return guarded_reply, meta


def _collect_analysis_delivery_gaps(reply: str, quality_issues: List[str] | None = None) -> Dict[str, bool]:
    text = str(reply or "")
    issue_text = "；".join([str(x).strip() for x in (quality_issues or []) if str(x).strip()])

    missing_hypothesis = not bool(_ANALYSIS_HYPOTHESIS_RE.search(text))
    missing_verification = not bool(_ANALYSIS_VERIFY_RE.search(text))
    missing_priority = not bool(_ANALYSIS_PRIORITY_RE.search(text))
    missing_metric = not bool(_ANALYSIS_METRIC_RE.search(text))

    if "原因" in issue_text or "依据" in issue_text:
        missing_hypothesis = True
    if "验证" in issue_text or "排查" in issue_text:
        missing_verification = True
    if "优先级" in issue_text:
        missing_priority = True
    if "指标" in issue_text or "KPI" in issue_text:
        missing_metric = True

    return {
        "hypothesis": bool(missing_hypothesis),
        "verification": bool(missing_verification),
        "priority": bool(missing_priority),
        "metric": bool(missing_metric),
    }


def _apply_analysis_delivery_guard(
    reply: str,
    *,
    response_mode: str,
    action: str,
    llm_is_asking: bool = False,
    quality_issues: List[str] | None = None,
) -> tuple[str, Dict[str, Any]]:
    text = str(reply or "").strip()
    mode = _normalize_response_mode(response_mode)
    action_token = str(action or "").strip().lower()

    meta: Dict[str, Any] = {
        "applied": False,
        "reason": "",
        "gaps": {},
    }

    if not text:
        meta["reason"] = "empty_reply"
        return reply, meta

    if llm_is_asking:
        meta["reason"] = "asking_user"
        return reply, meta

    if mode != _RESPONSE_MODE_EXECUTION:
        meta["reason"] = "not_execution_mode"
        return reply, meta

    if action_token not in {"analysis", "analyze", "diagnosis", "diagnose"}:
        meta["reason"] = "not_analysis_action"
        return reply, meta

    gaps = _collect_analysis_delivery_gaps(text, quality_issues=quality_issues)
    meta["gaps"] = gaps
    if not any(bool(v) for v in gaps.values()):
        meta["reason"] = "already_complete"
        return reply, meta

    if "诊断补全（系统保障）" in text:
        meta["reason"] = "already_guarded"
        return reply, meta

    lines: List[str] = ["## 诊断补全（系统保障）"]
    idx = 1
    if gaps.get("hypothesis"):
        lines.append(f"{idx}. 诊断假设：至少列出3个可验证假设（数据口径、流量质量、转化链路）。")
        idx += 1
    if gaps.get("verification"):
        lines.append(f"{idx}. 验证设计：为每个假设补“数据来源+观察窗口+判定阈值”，并说明通过/不通过后动作。")
        idx += 1
    if gaps.get("priority"):
        lines.append(f"{idx}. 优先级：按“影响度×验证成本”排序为 P1/P2/P3，先做高影响低成本项。")
        idx += 1
    if gaps.get("metric"):
        lines.append(f"{idx}. 验证指标：至少定义1个主指标+2个辅指标，并统一指标口径。")

    guarded_reply = f"{text}\n\n" + "\n".join(lines)
    meta["applied"] = True
    meta["reason"] = "analysis_delivery_guard_applied"
    return guarded_reply, meta


def _collect_design_delivery_gaps(reply: str, quality_issues: List[str] | None = None) -> Dict[str, bool]:
    text = str(reply or "")
    issue_text = "；".join([str(x).strip() for x in (quality_issues or []) if str(x).strip()])

    has_priority_marker = bool(_DESIGN_PRIORITY_RE.search(text))
    has_priority_tier = bool(_DESIGN_PRIORITY_TIER_RE.search(text))
    priority_module_hits = len(_DESIGN_PRIORITY_MODULE_RE.findall(text))

    has_experiment_marker = bool(_DESIGN_EXPERIMENT_RE.search(text))
    has_experiment_control = bool(_DESIGN_EXPERIMENT_CONTROL_RE.search(text))
    has_experiment_window = bool(_DESIGN_EXPERIMENT_WINDOW_RE.search(text))

    has_metric_marker = bool(_DESIGN_METRIC_RE.search(text))
    has_metric_threshold = bool(_DESIGN_METRIC_THRESHOLD_RE.search(text))
    has_calibration = bool(_DESIGN_CALIBRATION_RE.search(text))
    has_spec = bool(_DESIGN_SPEC_RE.search(text))
    has_acceptance = bool(_DESIGN_ACCEPTANCE_RE.search(text))

    missing_priority = not (has_priority_marker and (has_priority_tier or priority_module_hits >= 2))
    missing_experiment = not (has_experiment_marker and has_experiment_control and has_experiment_window)
    missing_metric = not (has_metric_marker and has_metric_threshold)
    missing_calibration = not has_calibration
    missing_spec = not has_spec
    missing_acceptance = not has_acceptance

    if "优先级" in issue_text or "版块" in issue_text or "模块" in issue_text or "层级" in issue_text:
        missing_priority = True
    if "A/B" in issue_text or "AB" in issue_text or "实验" in issue_text or "测试" in issue_text or "对照" in issue_text:
        missing_experiment = True
    if "指标" in issue_text or "阈值" in issue_text or "显著" in issue_text or "量化" in issue_text:
        missing_metric = True
    if "置信" in issue_text or "参考" in issue_text or "校准" in issue_text:
        missing_calibration = True
    if "规格" in issue_text or "尺寸" in issue_text or "色值" in issue_text or "字体" in issue_text:
        missing_spec = True
    if "验收" in issue_text or "检查清单" in issue_text or "上线门禁" in issue_text:
        missing_acceptance = True

    return {
        "priority": bool(missing_priority),
        "experiment": bool(missing_experiment),
        "metric": bool(missing_metric),
        "calibration": bool(missing_calibration),
        "spec": bool(missing_spec),
        "acceptance": bool(missing_acceptance),
    }


def _apply_design_delivery_guard(
    reply: str,
    *,
    response_mode: str,
    role: str,
    action: str,
    llm_is_asking: bool = False,
    quality_issues: List[str] | None = None,
) -> tuple[str, Dict[str, Any]]:
    text = str(reply or "").strip()
    mode = _normalize_response_mode(response_mode)
    role_token = str(role or "").strip().lower()
    action_token = str(action or "").strip().lower()

    meta: Dict[str, Any] = {
        "applied": False,
        "reason": "",
        "gaps": {},
    }

    if not text:
        meta["reason"] = "empty_reply"
        return reply, meta

    if llm_is_asking:
        meta["reason"] = "asking_user"
        return reply, meta

    if mode != _RESPONSE_MODE_EXECUTION:
        meta["reason"] = "not_execution_mode"
        return reply, meta

    if role_token != "design" and action_token not in {"design"}:
        meta["reason"] = "not_design_context"
        return reply, meta

    gaps = _collect_design_delivery_gaps(text, quality_issues=quality_issues)
    meta["gaps"] = gaps
    if not any(bool(v) for v in gaps.values()):
        meta["reason"] = "already_complete"
        return reply, meta

    if "设计补全（系统保障）" in text:
        meta["reason"] = "already_guarded"
        return reply, meta

    lines: List[str] = ["## 设计补全（系统保障）"]
    idx = 1
    if gaps.get("priority"):
        lines.append(f"{idx}. 版块优先级：按“影响转化潜力×改造成本”输出 P1/P2/P3，并至少覆盖首屏卖点、信任背书、成交促单3个模块。")
        idx += 1
    if gaps.get("experiment"):
        lines.append(f"{idx}. A/B实验：至少给出2组实验，明确变量、对照组/测试组、样本分配（如50/50）与测试周期。")
        idx += 1
    if gaps.get("metric"):
        lines.append(f"{idx}. 验证指标：为每组实验定义主指标+护栏指标，并写明通过阈值、失败阈值和回滚条件。")
        idx += 1
    if gaps.get("spec"):
        lines.append(f"{idx}. 视觉规格：至少补齐尺寸/栅格/留白、主辅色值（可给HEX）、字体层级（标题/正文/按钮）与按钮状态规范。")
        idx += 1
    if gaps.get("acceptance"):
        lines.append(f"{idx}. 验收清单：补“上线前检查项”（视觉一致性、可读性、跳转链路、埋点完整性），并定义发布门禁。")
        idx += 1
    if gaps.get("calibration"):
        lines.append(f"{idx}. 阈值校准：以上阈值仅供参考，需按近14天基线数据与平台规则校准后再正式放量。")

    guarded_reply = f"{text}\n\n" + "\n".join(lines)
    meta["applied"] = True
    meta["reason"] = "design_delivery_guard_applied"
    return guarded_reply, meta


_LEARNING_WHY_RE = re.compile(r"为什么|原理|机制|本质|原因")
_LEARNING_HOW_RE = re.compile(r"怎么做|步骤|做法|操作|落地|执行|纠偏|改进|复盘|先.*再|步骤[一二三四五1-5]")
_LEARNING_EXAMPLE_RE = re.compile(r"例如|比如|示例|案例")
_LEARNING_MISCONCEPTION_RE = re.compile(r"误区|常见错误|纠偏|避免")
_LEARNING_QUESTION_RE = re.compile(r"思考题|自检问题|你可以思考|请思考|请回答|练习题|反思题|请你思考|你会如何")
_LEARNING_BOUNDARY_RE = re.compile(r"边界条件|反例|不适用|前提")
_LEARNING_COMPARE_RE = re.compile(r"区别|对比|相同点|不同点|vs")


_DEFAULT_LEARNING_GUARD_POLICY: Dict[str, bool] = {
    "require_why": True,
    "require_how": True,
    "require_example": True,
    "require_misconception": True,
    "require_question": True,
    "require_boundary_compare_undergraduate": True,
}


def _coerce_learning_guard_policy(raw: Optional[Dict[str, Any]]) -> Dict[str, bool]:
    policy = dict(_DEFAULT_LEARNING_GUARD_POLICY)
    if not isinstance(raw, dict):
        return policy
    for key in policy.keys():
        if key in raw:
            policy[key] = bool(raw.get(key))
    return policy


def _load_learning_guard_policy_config() -> Dict[str, Any]:
    raw = str(os.getenv("LEARNING_GUARD_POLICY_JSON") or "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        logger.warning("Invalid LEARNING_GUARD_POLICY_JSON, fallback to defaults")
        return {}


def _resolve_learning_guard_policy(
    *,
    learning_level: str,
    domain_id: str = "",
    policy_override: Optional[Dict[str, Any]] = None,
) -> Dict[str, bool]:
    policy = dict(_DEFAULT_LEARNING_GUARD_POLICY)
    cfg = _load_learning_guard_policy_config()

    default_cfg = cfg.get("default") if isinstance(cfg, dict) else None
    if isinstance(default_cfg, dict):
        policy = _coerce_learning_guard_policy({**policy, **default_cfg})

    level_token = _normalize_learning_level(learning_level, response_mode=_RESPONSE_MODE_LEARNING)
    level_cfgs = cfg.get("levels") if isinstance(cfg.get("levels"), dict) else {}
    level_cfg = level_cfgs.get(level_token) if isinstance(level_cfgs, dict) else None
    if isinstance(level_cfg, dict):
        policy = _coerce_learning_guard_policy({**policy, **level_cfg})

    domain_key = str(domain_id or "").strip().lower()
    if domain_key:
        domain_cfgs = cfg.get("domains") if isinstance(cfg.get("domains"), dict) else {}
        domain_cfg = domain_cfgs.get(domain_key) if isinstance(domain_cfgs, dict) else None
        if isinstance(domain_cfg, dict):
            policy = _coerce_learning_guard_policy({**policy, **domain_cfg})

    if isinstance(policy_override, dict):
        policy = _coerce_learning_guard_policy({**policy, **policy_override})

    return policy


def _collect_learning_delivery_gaps(
    reply: str,
    *,
    learning_level: str,
    quality_issues: List[str] | None = None,
    policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, bool]:
    text = str(reply or "").strip()
    issue_text = "；".join([str(x).strip() for x in (quality_issues or []) if str(x).strip()])
    level = _normalize_learning_level(learning_level, response_mode=_RESPONSE_MODE_LEARNING)
    effective_policy = _coerce_learning_guard_policy(policy)

    missing_why = bool(effective_policy.get("require_why")) and not bool(_LEARNING_WHY_RE.search(text))
    missing_how = bool(effective_policy.get("require_how")) and not bool(_LEARNING_HOW_RE.search(text))
    missing_example = bool(effective_policy.get("require_example")) and not bool(_LEARNING_EXAMPLE_RE.search(text))
    missing_misconception = bool(effective_policy.get("require_misconception")) and not bool(_LEARNING_MISCONCEPTION_RE.search(text))
    missing_question = bool(effective_policy.get("require_question")) and not bool(_LEARNING_QUESTION_RE.search(text))

    if "为什么/怎么做" in issue_text:
        if bool(effective_policy.get("require_why")):
            missing_why = True
        if bool(effective_policy.get("require_how")):
            missing_how = True
    if "缺少示例" in issue_text and bool(effective_policy.get("require_example")):
        missing_example = True
    if "缺少误区" in issue_text and bool(effective_policy.get("require_misconception")):
        missing_misconception = True

    missing_boundary_compare = False
    if level == _LEARNING_LEVEL_UNDERGRADUATE and bool(effective_policy.get("require_boundary_compare_undergraduate")):
        has_boundary = bool(_LEARNING_BOUNDARY_RE.search(text))
        has_compare = bool(_LEARNING_COMPARE_RE.search(text))
        missing_boundary_compare = not (has_boundary and has_compare)
        if "边界条件" in issue_text or "反例" in issue_text:
            missing_boundary_compare = True

    return {
        "why": bool(missing_why),
        "how": bool(missing_how),
        "example": bool(missing_example),
        "misconception": bool(missing_misconception),
        "question": bool(missing_question),
        "boundary_compare": bool(missing_boundary_compare),
    }


def _apply_learning_delivery_guard(
    reply: str,
    *,
    response_mode: str,
    learning_level: str,
    domain_id: str = "",
    llm_is_asking: bool = False,
    quality_issues: List[str] | None = None,
    learning_guard_policy: Optional[Dict[str, Any]] = None,
) -> tuple[str, Dict[str, Any]]:
    text = str(reply or "").strip()
    mode = _normalize_response_mode(response_mode)

    meta: Dict[str, Any] = {
        "applied": False,
        "reason": "",
        "gaps": {},
    }

    if not text:
        meta["reason"] = "empty_reply"
        return reply, meta

    if llm_is_asking:
        meta["reason"] = "asking_user"
        return reply, meta

    if mode != _RESPONSE_MODE_LEARNING:
        meta["reason"] = "not_learning_mode"
        return reply, meta

    resolved_policy = _resolve_learning_guard_policy(
        learning_level=learning_level,
        domain_id=domain_id,
        policy_override=learning_guard_policy,
    )

    gaps = _collect_learning_delivery_gaps(
        text,
        learning_level=learning_level,
        quality_issues=quality_issues,
        policy=resolved_policy,
    )
    meta["gaps"] = gaps
    if not any(bool(v) for v in gaps.values()):
        meta["reason"] = "already_complete"
        return reply, meta

    if "补充学习要点" in text:
        meta["reason"] = "already_guarded"
        return reply, meta

    level = _normalize_learning_level(learning_level, response_mode=mode)

    lines: List[str] = ["## 补充学习要点"]
    idx = 1
    if gaps.get("why"):
        lines.append(f"{idx}. 为什么：补充核心原理与成立前提，避免只记结论不看条件。")
        idx += 1
    if gaps.get("how"):
        lines.append(f"{idx}. 怎么做：按“准备-执行-复盘”补齐 3 步，每步写清输入与产出。")
        idx += 1
    if gaps.get("example"):
        lines.append(f"{idx}. 场景示例（电商）：补 1 个可量化案例，明确动作、指标和判断标准。")
        idx += 1
    if gaps.get("misconception"):
        lines.append(f"{idx}. 常见误区：补 1-2 个高频误区，并给出对应纠偏动作。")
        idx += 1
    if gaps.get("question"):
        lines.append(f"{idx}. 自检问题：关键前提变化时，你会先调整哪一步，为什么？")
        idx += 1
    if level == _LEARNING_LEVEL_UNDERGRADUATE and gaps.get("boundary_compare"):
        lines.append(
            f"{idx}. 本科加严：补“相同点/不同点 + 关键公式（如 ROI/ROAS）+ 反例边界条件”，并说明何时切换策略。"
        )
        idx += 1

    if not any(marker in text for marker in _EXECUTION_CONFIDENCE_MARKERS):
        lines.append(f"{idx}. 说明：以上为学习补充框架，落地前请结合你的真实业务数据复核（仅供参考）。")

    guarded_reply = f"{text}\n\n" + "\n".join(lines)
    meta["applied"] = True
    meta["reason"] = "learning_delivery_guard_applied"
    return guarded_reply, meta





_MODE_EXECUTION_CROSSTALK_RE = re.compile(r"思考题|自检问题|常见误区|为什么[:：]|怎么做[:：]|原理")
_MODE_EXECUTION_ACTION_SIGNATURE_RE = re.compile(r"P1|P2|P3|今日先做|下一步|行动项|24小时")
_MODE_LEARNING_EXECUTION_CROSSTALK_RE = re.compile(r"P1|P2|P3|今日先做|24小时|止损|回滚|执行动作|行动项")
_MODE_LEARNING_INTENT_RE = re.compile(r"教学|讲解|解释|为什么|怎么做|原理|机制|示例|案例|复盘|培训")
_MODE_COPY_READY_MESSAGE_RE = re.compile(
    r"可直接复制|图文笔记|回复邮件|邮件模板|口播脚本|在线回复话术|客服话术|IM在线回复",
    re.IGNORECASE,
)
_COLLABORATION_STRATEGY_SIGNATURE_RE = re.compile(r"(回答策略|协作模式)[:：]")
_LEARNING_MANUAL_ROLE_LENS_SECTION_RE = re.compile(r"角色补位讲解|角色视角补充|角色补位视角|角色分工讲解")
_LEARNING_MANUAL_ROLE_LENS_FOCUS_BY_ROLE: Dict[str, str] = {
    "ops": "补“动作优先级与执行节奏”，并给出1个可验证里程碑。",
    "data": "补“指标口径/阈值/采样周期”，明确如何判断方案是否有效。",
    "service": "补“用户沟通关键话术+升级门禁”，避免执行偏差。",
    "design": "补“信息层级与视觉取舍理由”，并说明验证方式。",
    "accounting": "补“成本-收益口径与预算边界”，说明止损触发条件。",
    "engineering": "补“技术可行性、依赖约束与上线风险”，并给出回滚门禁。",
    "web": "补“流量入口与SEO假设”，明确监控指标与观察窗口。",
    "creative": "补“内容钩子与表达策略”，并给出A/B验证信号。",
}


def _has_learning_manual_role_lens(text: str, role_labels: List[str]) -> bool:
    body = str(text or "")
    if not _LEARNING_MANUAL_ROLE_LENS_SECTION_RE.search(body):
        return False
    if not role_labels:
        return True
    return any(label in body for label in role_labels)


def _build_learning_manual_role_lens_block(normalized_roles: List[str], role_labels: List[str]) -> str:
    lines = ["## 角色补位讲解（手动协作）"]
    for idx, role_key in enumerate((normalized_roles or [])[:3]):
        label = role_labels[idx] if idx < len(role_labels) else _role_display_name(role_key)
        focus = _LEARNING_MANUAL_ROLE_LENS_FOCUS_BY_ROLE.get(
            str(role_key or "").strip().lower(),
            "补充本题关注点、证据口径与易错点纠偏。",
        )
        lines.append(f"- {label}：{focus}")

    if len(lines) == 1:
        lines.append("- 主角色：补充本题关注点、证据口径与易错点纠偏。")

    lines.append("说明：以上为手动协作教学补位框架，请结合你的业务数据复核（仅供参考）。")
    return "\n".join(lines)


def _apply_mode_differentiation_guard(
    reply: str,
    *,
    response_mode: str,
    message: str = "",
    llm_is_asking: bool = False,
) -> tuple[str, Dict[str, Any]]:
    text = str(reply or "").strip()
    mode = _normalize_response_mode(response_mode)

    meta: Dict[str, Any] = {
        "applied": False,
        "reason": "",
        "mode": mode,
    }

    if not text:
        meta["reason"] = "empty_reply"
        return reply, meta

    if llm_is_asking:
        meta["reason"] = "asking_user"
        return reply, meta

    if _looks_like_smalltalk_message(str(message or "")):
        meta["reason"] = "smalltalk_skip"
        return reply, meta

    if mode == _RESPONSE_MODE_EXECUTION:
        if _MODE_COPY_READY_MESSAGE_RE.search(str(message or "")):
            meta["reason"] = "copy_ready_scene_skip"
            return reply, meta

        has_learning_cross_talk = bool(_MODE_EXECUTION_CROSSTALK_RE.search(text))
        has_execution_signature = bool(_MODE_EXECUTION_ACTION_SIGNATURE_RE.search(text))
        has_learning_intent_in_message = bool(_MODE_LEARNING_INTENT_RE.search(str(message or "")))
        has_why = bool(re.search(r"为什么|原因|原理|机制", text))
        has_how = bool(re.search(r"怎么做|步骤|做法|纠偏|改进|复盘", text))
        has_learning_bridge = bool(has_why and has_how)
        needs_learning_bridge = bool(has_learning_intent_in_message and not has_learning_bridge)
        meta["has_learning_cross_talk"] = has_learning_cross_talk
        meta["has_execution_signature"] = has_execution_signature
        meta["has_learning_intent_in_message"] = has_learning_intent_in_message
        meta["has_learning_bridge"] = has_learning_bridge

        if not has_learning_cross_talk and not needs_learning_bridge:
            meta["reason"] = "execution_no_cross_talk"
            return reply, meta

        if needs_learning_bridge:
            if "补充理解框架（执行模式简版）" in text:
                meta["reason"] = "execution_learning_bridge_already_present"
                return reply, meta

            points = _extract_outline_points(text, limit=2)
            why_line = points[0] if points else "先明确目标指标与口径，避免动作有效但指标不可比。"
            how_line = points[1] if len(points) > 1 else "按“确认基线-执行动作-24小时复盘”三步推进。"
            bridge_block = (
                "补充理解框架（执行模式简版）：\n"
                f"为什么：{why_line}\n"
                f"怎么做：{how_line}\n"
                "示例：先挑一个核心指标跑 24 小时小样本验证，再决定是否放量。"
            )
            guarded = f"{text}\n\n{bridge_block}"
            meta["applied"] = True
            meta["reason"] = "execution_learning_bridge_appended"
            return guarded, meta

        # 回答已经具备执行动作签名时，不做硬注入，避免“系统话术”外露。
        if has_execution_signature:
            meta["reason"] = "execution_signature_present"
            return reply, meta

        if "补充执行三步：" in text or "执行模式速用版（系统校准）" in text:
            meta["reason"] = "already_guarded"
            return reply, meta

        points = _extract_outline_points(text, limit=3)
        p1 = points[0] if points else "先统一目标、口径和验收阈值。"
        p2 = points[1] if len(points) > 1 else "再执行高影响低成本动作，并记录关键指标变化。"
        p3 = points[2] if len(points) > 2 else "最后做复盘与止损回滚，沉淀可复用动作。"
        guard_block = (
            "补充执行三步：\n"
            f"1. 今天先做：{p1}\n"
            f"2. 随后执行：{p2}\n"
            f"3. 本周收口：{p3}\n"
            "下一步（24小时内）：完成第1步并记录基线数据，明天按核心指标复盘后决定加码或回滚。"
        )
        guarded = f"{guard_block}\n\n{text}"
        meta["applied"] = True
        meta["reason"] = "execution_cross_talk_detected"
        return guarded, meta

    has_execution_cross_talk = bool(_MODE_LEARNING_EXECUTION_CROSSTALK_RE.search(text))
    has_why = bool(re.search(r"为什么|原因|原理|机制", text))
    has_how = bool(re.search(r"怎么做|步骤|做法|纠偏|改进|复盘", text))
    has_learning_core_signature = bool(has_why and has_how)
    meta["has_execution_cross_talk"] = has_execution_cross_talk
    meta["has_learning_core_signature"] = has_learning_core_signature

    if has_learning_core_signature:
        meta["reason"] = "learning_signature_ok"
        return reply, meta

    if not has_execution_cross_talk:
        meta["reason"] = "learning_no_cross_talk"
        return reply, meta

    if "补充学习路径：" in text or "教学模式学习路径（系统校准）" in text:
        meta["reason"] = "already_guarded"
        return reply, meta

    points = _extract_outline_points(text, limit=2)
    anchor = points[0] if points else "先理解概念成立前提，再映射到你的业务场景。"
    how_step = points[1] if len(points) > 1 else "按“准备-执行-复盘”三步推进，并在每步记录输入输出。"
    guard_block = (
        "补充学习路径：\n"
        f"为什么：{anchor}\n"
        f"怎么做：{how_step}\n"
        "场景示例：用一个可量化的小场景演示“动作-指标-判断”如何闭环。\n"
        "常见误区：只记结论不看前提，导致跨场景直接套用后失效。\n"
        "自检问题：如果关键前提变化，你会先调整哪一步？"
    )
    guarded = f"{guard_block}\n\n{text}"
    meta["applied"] = True
    meta["reason"] = "learning_execution_cross_talk_detected"
    return guarded, meta


def _apply_collaboration_mode_signature_guard(
    reply: str,
    *,
    collaboration_mode: str,
    hired_roles: Optional[List[str]] | None = None,
    response_mode: str = _RESPONSE_MODE_EXECUTION,
    message: str = "",
    llm_is_asking: bool = False,
) -> tuple[str, Dict[str, Any]]:
    text = str(reply or "").strip()
    mode = _normalize_collaboration_mode(collaboration_mode)
    normalized_response_mode = _normalize_response_mode(response_mode)
    normalized_roles = _normalize_hired_roles(
        list(hired_roles or []),
        allow_engineering=True,
        collaboration_mode=mode,
    )

    meta: Dict[str, Any] = {
        "applied": False,
        "reason": "",
        "mode": mode,
        "roles": normalized_roles,
        "response_mode": normalized_response_mode,
        "role_lens_appended": False,
    }

    if not text:
        meta["reason"] = "empty_reply"
        return reply, meta

    if llm_is_asking:
        meta["reason"] = "asking_user"
        return reply, meta

    if _looks_like_smalltalk_message(str(message or "")):
        meta["reason"] = "smalltalk_skip"
        return reply, meta

    role_labels = [_role_display_name(role) for role in normalized_roles[:4]]
    manual_learning_needs_lens = bool(
        mode == _COLLABORATION_MODE_MANUAL
        and normalized_response_mode == _RESPONSE_MODE_LEARNING
        and role_labels
    )

    if _COLLABORATION_STRATEGY_SIGNATURE_RE.search(text):
        if manual_learning_needs_lens and not _has_learning_manual_role_lens(text, role_labels):
            lens_block = _build_learning_manual_role_lens_block(normalized_roles, role_labels)
            guarded = f"{text}\n\n{lens_block}"
            meta["applied"] = True
            meta["reason"] = "manual_learning_role_lens_appended"
            meta["signature"] = "present"
            meta["role_lens_appended"] = True
            return guarded, meta

        meta["reason"] = "signature_present"
        return reply, meta

    if mode == _COLLABORATION_MODE_SINGLE:
        signature = "回答策略：单角色直答（仅当前主角色输出，不启用多角色协作）。"
        reason = "single_signature_injected"
    elif mode == _COLLABORATION_MODE_MANUAL:
        if role_labels:
            signature = f"回答策略：手动协作（仅使用已勾选角色：{'、'.join(role_labels)}）。"
        else:
            signature = "回答策略：手动协作（当前未勾选补位角色，先按主角色输出）。"
        reason = "manual_signature_injected"
    else:
        signature = "回答策略：自动协作（系统按任务复杂度决定是否启用角色补位）。"
        reason = "auto_signature_injected"

    guarded = f"{signature}\n\n{text}"
    if manual_learning_needs_lens and not _has_learning_manual_role_lens(text, role_labels):
        lens_block = _build_learning_manual_role_lens_block(normalized_roles, role_labels)
        guarded = f"{guarded}\n\n{lens_block}"
        reason = "manual_signature_with_learning_role_lens_injected"
        meta["role_lens_appended"] = True

    meta["applied"] = True
    meta["reason"] = reason
    meta["signature"] = signature
    return guarded, meta


def _strategy_combo_section_present(
    text: str,
    section_id: str,
    role_labels: List[str],
    learning_level: str = _LEARNING_LEVEL_HIGHER_VOCATIONAL,
) -> bool:
    body = str(text or "")

    if section_id == "first_screen_exec":
        return bool(re.search(r"首屏导航（执行）|首屏速览（执行）|首屏执行", body, re.IGNORECASE))
    if section_id == "first_screen_learning":
        return bool(re.search(r"首屏导航（教学）|首屏速览（教学）|首屏教学", body, re.IGNORECASE))
    if section_id == "first_screen_manual":
        return bool(re.search(r"首屏协作导航|首屏协作排布|协作排布", body, re.IGNORECASE))

    if section_id == "execution_scope_definition":
        return bool(re.search(r"口径定义|指标口径|统计口径|口径说明|口径[:：]", body, re.IGNORECASE))
    if section_id == "execution_diagnosis_hypothesis":
        return bool(re.search(r"诊断假设|成因假设|原因假设|可能原因|假设", body, re.IGNORECASE))
    if section_id == "execution_validation_plan":
        return bool(re.search(r"验证方案|验证计划|验证步骤|A/B|AB测试|对照实验|样本验证", body, re.IGNORECASE))
    if section_id == "execution_why_how":
        has_heading = bool(
            re.search(
                r"执行依据（原因与动作方法）|执行依据（原因/动作方法）|执行依据（原因）|执行依据",
                body,
                re.IGNORECASE,
            )
        )
        has_reason = bool(re.search(r"原因|依据|因为|由于|成因", body, re.IGNORECASE))
        has_method = bool(re.search(r"动作方法|执行方法|落地方法|做法|步骤|怎么做", body, re.IGNORECASE))
        return has_heading or (has_reason and has_method)
    if section_id == "execution_example":
        return bool(re.search(r"执行场景示例|场景示例|执行示例|例如|比如|案例", body, re.IGNORECASE))
    if section_id == "execution_priority":
        return bool(re.search(r"优先级|先后顺序|优先处理|P0|P1|P2|P3", body, re.IGNORECASE))
    if section_id == "execution_summary":
        return bool(re.search(r"30\s*秒.{0,8}结论|可执行结论|今日先做|执行结论", body, re.IGNORECASE))
    if section_id == "execution_next_step":
        return bool(re.search(r"下一步.{0,8}24\s*小时|24\s*小时内|明天.*复盘", body, re.IGNORECASE))
    if section_id == "execution_risk":
        return bool(re.search(r"风险|回滚|止损|兜底", body, re.IGNORECASE))

    if section_id == "learning_why":
        return bool(re.search(r"为什么|原因|原理|机制", body))
    if section_id == "learning_how":
        return bool(re.search(r"怎么做|步骤|做法", body))
    if section_id == "learning_example":
        return bool(re.search(r"场景示例|示例|案例", body))
    if section_id == "learning_pitfall":
        return bool(re.search(r"常见误区|误区|易错", body))
    if section_id == "learning_self_check":
        return bool(re.search(r"自检问题|思考题|自测", body))
    if section_id == "learning_level_signature":
        level = _normalize_learning_level(learning_level, response_mode=_RESPONSE_MODE_LEARNING)
        level_label = _LEARNING_LEVEL_LABELS.get(level, "高职")
        return bool(
            re.search(
                rf'教学层级[:：]\s*{re.escape(level_label)}|按[“"]?{re.escape(level_label)}[”"]?口径',
                body,
            )
        )

    if section_id == "collab_signature_single":
        return "回答策略：单角色直答" in body
    if section_id == "collab_signature_manual":
        return "回答策略：手动协作" in body
    if section_id == "collab_signature_auto":
        return "回答策略：自动协作" in body
    if section_id == "collab_auto_trigger":
        return bool(re.search(r"协作触发条件|何时协作|触发协作", body))
    if section_id == "collab_manual_roles":
        if not role_labels:
            return ("未勾选补位角色" in body) or ("回答策略：手动协作" in body)
        return all(label in body for label in role_labels)
    if section_id == "collab_manual_matrix":
        if "手动协作分工矩阵" in body or "角色分工矩阵" in body:
            return True
        if role_labels and all(label in body for label in role_labels):
            return bool(re.search(r"分工|协作|责任|交付", body))
        return False
    if section_id == "collab_manual_role_lens":
        return _has_learning_manual_role_lens(body, role_labels)

    return False


def _strategy_combo_section_patch(
    section_id: str,
    *,
    mode: str,
    collab: str,
    normalized_roles: List[str],
    role_labels: List[str],
    outline_points: List[str],
    learning_level: str = _LEARNING_LEVEL_HIGHER_VOCATIONAL,
) -> str:
    anchor = outline_points[0] if outline_points else "先统一目标口径并锁定优先级。"
    second = outline_points[1] if len(outline_points) > 1 else "再执行高影响、低成本动作并记录基线。"

    if section_id == "first_screen_exec":
        return (
            "## 首屏导航（执行）\n"
            f"- 结论主线：{anchor}\n"
            "- 执行顺序：先口径与基线，再动作落地，最后复盘纠偏。\n"
            "- 风险回滚：若核心指标连续下滑，立即回滚并复盘触发条件。"
        )
    if section_id == "first_screen_learning":
        return (
            "## 首屏导航（教学）\n"
            f"- 本轮先讲：{anchor}\n"
            "- 再讲怎么做：按步骤拆解并解释每一步判断依据。\n"
            "- 最后自检：用示例与自测题确认是否真的会用。"
        )
    if section_id == "first_screen_manual":
        roles_text = "、".join(role_labels) if role_labels else "当前未勾选"
        return (
            "## 首屏协作导航\n"
            f"- 主线角色：当前主角色先给结论主线。\n"
            f"- 补位角色：{roles_text}。\n"
            "- 阅读顺序：先主线结论，再逐角色补证据与风险。"
        )

    if section_id == "execution_scope_definition":
        return "口径定义：统一核心指标口径（统计窗口、分母口径、数据源），先以“同口径转化率”作为主判定指标。"
    if section_id == "execution_diagnosis_hypothesis":
        return f"诊断假设：围绕“{anchor}”优先验证流量质量变化与页面转化阻塞两类主因，再决定是否扩改。"
    if section_id == "execution_validation_plan":
        return "验证方案：按“渠道分层→漏斗拆解→小流量实验”三步验证；每步记录样本量、对照组和结论。"
    if section_id == "execution_why_how":
        return "执行依据（原因）：先锁定主因再投入资源，可显著降低试错成本。动作方法：先小步验证，再按结果扩量或回滚。"
    if section_id == "execution_example":
        return "执行场景示例：例如先在1个渠道做小流量实验，若转化率提升>=5%再扩量到全渠道。"
    if section_id == "execution_priority":
        return "优先级：P1先做高影响低成本修复，P2推进中期优化，P3保留观察项并设置触发阈值。"
    if section_id == "execution_summary":
        return f"30秒可执行结论：{anchor}"
    if section_id == "execution_next_step":
        return "下一步（24小时内）：完成口径确认并落地1个高影响动作，目标让核心指标先止跌（跌幅收敛>=20%），明天按同口径复盘。"
    if section_id == "execution_risk":
        return "风险与回滚：若核心指标连续下滑或超预算，立即回滚到基线方案并复盘触发条件。"

    if section_id == "learning_why":
        return f"为什么：{anchor}"
    if section_id == "learning_how":
        return f"怎么做：{second}"
    if section_id == "learning_example":
        return "场景示例：选一个小样本场景，演示“动作-指标-判断”如何闭环。"
    if section_id == "learning_pitfall":
        return "常见误区：只记结论不看前提，跨场景直接套用会导致失效。"
    if section_id == "learning_self_check":
        return "自检问题：如果关键前提变化，你会先调整哪一步？"
    if section_id == "learning_level_signature":
        level = _normalize_learning_level(learning_level, response_mode=_RESPONSE_MODE_LEARNING)
        level_label = _LEARNING_LEVEL_LABELS.get(level, "高职")
        if level == _LEARNING_LEVEL_VOCATIONAL:
            return f"教学层级：{level_label}。本轮先白话解释，再给不超过3步的上手做法。"
        if level == _LEARNING_LEVEL_UNDERGRADUATE:
            return f"教学层级：{level_label}。本轮需要补充边界条件、反例与可验证假设。"
        return f"教学层级：{level_label}。本轮按“概念→流程→场景”三段展开。"

    if section_id == "collab_signature_single":
        return "回答策略：单角色直答（仅当前主角色输出，不启用多角色协作）。"
    if section_id == "collab_signature_manual":
        if role_labels:
            return f"回答策略：手动协作（仅使用已勾选角色：{'、'.join(role_labels)}）。"
        return "回答策略：手动协作（当前未勾选补位角色，先按主角色输出）。"
    if section_id == "collab_signature_auto":
        return "回答策略：自动协作（系统按任务复杂度决定是否启用角色补位）。"
    if section_id == "collab_auto_trigger":
        return f"协作触发条件：仅当出现跨岗位依赖、证据冲突或执行阻塞时，才补充协作观点；自动补位最多 {_support_agent_role_limit(collaboration_mode=_COLLABORATION_MODE_AUTO)} 个角色。"
    if section_id == "collab_manual_roles":
        if role_labels:
            return f"手动协作角色：仅允许以下已勾选角色参与：{'、'.join(role_labels)}。"
        return "手动协作角色：当前未勾选补位角色，保持主角色单线输出。"
    if section_id == "collab_manual_matrix":
        if not normalized_roles:
            return "## 手动协作分工矩阵\n- 主角色：负责给出完整结论与执行主线。\n- 补位角色：当前未勾选。"
        lines = ["## 手动协作分工矩阵"]
        for idx, role_key in enumerate(normalized_roles[:4], start=1):
            label = role_labels[idx - 1] if idx - 1 < len(role_labels) else _role_display_name(role_key)
            focus = _LEARNING_MANUAL_ROLE_LENS_FOCUS_BY_ROLE.get(
                str(role_key or "").strip().lower(),
                "补充关注点、证据口径与可验证信号。",
            )
            lines.append(f"- {label}：分工#{idx}，{focus}")
        lines.append("说明：先主角色结论，再按以上分工补位，禁止引入未勾选角色。")
        return "\n".join(lines)
    if section_id == "collab_manual_role_lens":
        return _build_learning_manual_role_lens_block(normalized_roles, role_labels)

    return ""


def _apply_strategy_combo_contract_guard(
    reply: str,
    *,
    response_mode: str,
    collaboration_mode: str,
    learning_level: str = _LEARNING_LEVEL_HIGHER_VOCATIONAL,
    hired_roles: Optional[List[str]] | None = None,
    message: str = "",
    llm_is_asking: bool = False,
) -> tuple[str, Dict[str, Any]]:
    text = str(reply or "").strip()
    mode, collab, normalized_roles, required_sections = _strategy_combo_contract_required_sections(
        response_mode=response_mode,
        collaboration_mode=collaboration_mode,
        hired_roles=hired_roles,
        learning_level=learning_level,
    )
    role_labels = [_role_display_name(role) for role in normalized_roles[:4]]

    meta: Dict[str, Any] = {
        "applied": False,
        "reason": "",
        "response_mode": mode,
        "collaboration_mode": collab,
        "required_sections": list(required_sections),
        "missing_sections": [],
        "missing_section_labels": [],
    }

    if not text:
        meta["reason"] = "empty_reply"
        return reply, meta

    if llm_is_asking:
        meta["reason"] = "asking_user"
        return reply, meta

    if _looks_like_smalltalk_message(str(message or "")):
        meta["reason"] = "smalltalk_skip"
        return reply, meta

    missing_sections = [
        section_id
        for section_id in required_sections
        if not _strategy_combo_section_present(text, section_id, role_labels, learning_level)
    ]
    missing_section_labels = [
        _STRATEGY_SECTION_LABELS.get(section_id, section_id)
        for section_id in missing_sections
    ]
    meta["missing_sections"] = list(missing_sections)
    meta["missing_section_labels"] = list(missing_section_labels)

    if not missing_sections:
        meta["reason"] = "contract_satisfied"
        return reply, meta

    outline_points = _extract_outline_points(text, limit=2)
    front_patch_blocks: List[str] = []
    tail_patch_blocks: List[str] = []
    tail_missing_labels: List[str] = []
    for section_id in missing_sections:
        patch = _strategy_combo_section_patch(
            section_id,
            mode=mode,
            collab=collab,
            normalized_roles=normalized_roles,
            role_labels=role_labels,
            outline_points=outline_points,
            learning_level=learning_level,
        )
        if not patch:
            continue
        if section_id.startswith("first_screen_"):
            front_patch_blocks.append(patch)
        else:
            tail_patch_blocks.append(patch)
            tail_missing_labels.append(_STRATEGY_SECTION_LABELS.get(section_id, section_id))

    if not front_patch_blocks and not tail_patch_blocks:
        meta["reason"] = "contract_missing_sections_detected_no_patch"
        return reply, meta

    guarded = text
    if front_patch_blocks:
        guarded = "\n\n".join(front_patch_blocks) + "\n\n" + guarded

    if tail_patch_blocks:
        missing_text = "、".join(tail_missing_labels) or "关键小节"
        patch_header = f"## 回答策略合同补齐（缺失：{missing_text}）"
        guarded = f"{guarded}\n\n{patch_header}\n\n" + "\n\n".join(tail_patch_blocks)

    meta["applied"] = True
    meta["reason"] = "contract_missing_sections_appended"
    return guarded, meta


_COPY_SCENE_XHS_HINTS: tuple[str, ...] = (
    "小红书", "图文笔记", "笔记", "种草", "标题", "标签", "可直接复制"
)

_COPY_SCENE_EMAIL_HINTS: tuple[str, ...] = (
    "邮件", "email", "mail", "客服回复", "回复邮件", "邮件回复", "邮件模板", "邮件正文"
)

_COPY_SCENE_EMAIL_SUBJECT_HINTS: tuple[str, ...] = (
    "主题", "subject", "主题行", "subject line"
)

_COPY_SCENE_EMAIL_CONTEXT_HINTS: tuple[str, ...] = (
    "邮件", "email", "mail", "邮箱", "outlook", "gmail"
)

_COPY_SCENE_DOUYIN_SCRIPT_HINTS: tuple[str, ...] = (
    "抖音", "短视频", "口播", "脚本", "开场钩子", "结尾cta"
)

_COPY_SCENE_IM_HINTS: tuple[str, ...] = (
    "客服话术", "im", "在线客服", "私信回复", "聊天回复"
)


def _is_xhs_copy_scene(message: str) -> bool:
    lowered = str(message or "").lower()
    return any(h.lower() in lowered for h in _COPY_SCENE_XHS_HINTS)


def _is_email_copy_scene(message: str) -> bool:
    lowered = str(message or "").lower()
    if any(h.lower() in lowered for h in _COPY_SCENE_EMAIL_HINTS):
        return True
    if any(h.lower() in lowered for h in _COPY_SCENE_EMAIL_SUBJECT_HINTS):
        return any(h.lower() in lowered for h in _COPY_SCENE_EMAIL_CONTEXT_HINTS)
    return False


def _is_douyin_script_copy_scene(message: str) -> bool:
    lowered = str(message or "").lower()
    return any(h.lower() in lowered for h in _COPY_SCENE_DOUYIN_SCRIPT_HINTS)


def _is_im_copy_scene(message: str) -> bool:
    lowered = str(message or "").lower()
    return any(h.lower() in lowered for h in _COPY_SCENE_IM_HINTS)


def _has_copy_xhs_pack(reply: str) -> bool:
    text = str(reply or "")
    has_title = ("标题：" in text) or ("标题:" in text)
    has_body = ("正文：" in text) or ("正文:" in text)
    has_tags = ("标签：" in text) or ("标签:" in text) or bool(re.search(r"#\S+", text))
    return bool(has_title and has_body and has_tags)


def _has_copy_email_pack(reply: str) -> bool:
    text = str(reply or "")
    has_subject = ("主题：" in text) or ("主题:" in text) or ("Subject:" in text)
    has_body = ("正文：" in text) or ("正文:" in text) or ("Dear" in text)
    return bool(has_subject and has_body)


def _has_copy_douyin_script_pack(reply: str) -> bool:
    text = str(reply or "")
    return bool(("开场钩子" in text) and ("口播正文" in text) and ("结尾CTA" in text))


def _has_copy_im_pack(reply: str) -> bool:
    text = str(reply or "")
    has_im_lines = ("首轮回复" in text) and ("跟进回复" in text)
    has_risk = ("风险提示" in text) or bool(re.search(r"风险|回滚|止损|预案|注意事项", text))
    has_next_step = ("下一步" in text) or bool(re.search(r"今日先做|24小时|先执行|行动项", text))
    return bool(has_im_lines and has_risk and has_next_step)


def _build_copy_xhs_pack(reply: str) -> str:
    points = _extract_outline_points(str(reply or ""), limit=3)
    title_core = points[0] if points else "3步把店铺内容转化率提上来"
    title = f"{title_core[:24]}｜今天就能执行"

    body_lines: List[str] = []
    if points:
        for idx, item in enumerate(points[:3], start=1):
            body_lines.append(f"{idx}. {item}")
    else:
        body_lines = [
            "1. 先定今天唯一主目标，并明确验收指标。",
            "2. 再做低成本高影响动作，优先验证主链路。",
            "3. 晚上复盘结果，保留有效动作，淘汰无效动作。",
        ]

    body = "\n".join(body_lines)
    tags = "#电商运营 #实操清单 #店铺增长 #复盘"
    return (
        "## 可直接复制（小红书图文）\n"
        f"标题：{title}\n"
        f"正文：\n{body}\n"
        f"标签：{tags}"
    )


def _build_copy_email_pack(reply: str) -> str:
    points = _extract_outline_points(str(reply or ""), limit=2)
    reason_line = points[0] if points else "目前订单在清关环节出现延迟"
    solution_line = points[1] if len(points) > 1 else "我们已加急跟进并将持续同步最新进展"
    return (
        "## 可直接复制（客服邮件）\n"
        "主题：关于您的订单进度更新（清关处理中）\n"
        "正文：\n"
        "Dear Customer,\n\n"
        "Thank you for your patience. " + reason_line + "。"
        " " + solution_line + "。\n"
        "If needed, we can also provide an alternative solution for you.\n\n"
        "Best regards,\nCustomer Support"
    )


def _build_copy_douyin_script_pack(reply: str) -> str:
    points = _extract_outline_points(str(reply or ""), limit=3)
    hook = points[0] if points else "你是不是每天发内容，却总觉得没人停留？"
    body = points[1] if len(points) > 1 else "今天给你一个低成本可执行的门店内容增长方案，先抓高影响动作。"
    cta = points[2] if len(points) > 2 else "先收藏这条，按评论区清单执行，明天复盘数据。"
    return (
        "## 可直接复制（短视频口播脚本）\n"
        f"开场钩子：{hook}\n"
        f"口播正文：{body}\n"
        f"结尾CTA：{cta}"
    )


def _build_copy_im_pack(reply: str) -> str:
    points = _extract_outline_points(str(reply or ""), limit=2)
    first = points[0] if points else "您好，已收到您的问题，这边先帮您核对订单进度。"
    follow = points[1] if len(points) > 1 else "目前卡在清关环节，我们已加急处理，预计24小时内再次同步。"
    return (
        "## 可直接复制（客服IM话术）\n"
        f"首轮回复：{first}\n"
        f"跟进回复：{follow}\n"
        "升级处理：若超24小时仍无进展，我这边可立即转高级客服人工跟进。\n"
        "风险提示：涉及退款、质量争议或人身安全风险时，先保留证据并升级人工复核，避免超权限承诺。\n"
        "下一步：今天先做首轮安抚与工单分级，24小时内复盘投诉率、升级率并决定是否触发回滚预案。"
    )


def _apply_copy_ready_delivery_guard(
    reply: str,
    *,
    response_mode: str,
    message: str,
    llm_is_asking: bool = False,
) -> tuple[str, Dict[str, Any]]:
    text = str(reply or "").strip()
    mode = _normalize_response_mode(response_mode)

    meta: Dict[str, Any] = {
        "applied": False,
        "reason": "",
        "scene": "",
    }

    if not text:
        meta["reason"] = "empty_reply"
        return reply, meta
    if llm_is_asking:
        meta["reason"] = "asking_user"
        return reply, meta
    if mode != _RESPONSE_MODE_EXECUTION:
        meta["reason"] = "not_execution_mode"
        return reply, meta

    is_email = _is_email_copy_scene(message)
    is_douyin_script = (not is_email) and _is_douyin_script_copy_scene(message)
    is_im = (not is_email and not is_douyin_script) and _is_im_copy_scene(message)
    is_xhs = (not is_email and not is_douyin_script and not is_im) and _is_xhs_copy_scene(message)
    if not is_xhs and not is_email and not is_douyin_script and not is_im:
        meta["reason"] = "not_copy_scene"
        return reply, meta

    if is_xhs:
        meta["scene"] = "xiaohongshu"
        if _has_copy_xhs_pack(text):
            meta["reason"] = "already_has_copy_pack"
            return reply, meta
        guarded = f"{text}\n\n---\n\n{_build_copy_xhs_pack(text)}"
        meta["applied"] = True
        meta["reason"] = "copy_pack_injected"
        return guarded, meta

    if is_email:
        meta["scene"] = "email"
        if _has_copy_email_pack(text):
            meta["reason"] = "already_has_copy_pack"
            return reply, meta
        guarded = f"{text}\n\n---\n\n{_build_copy_email_pack(text)}"
        meta["applied"] = True
        meta["reason"] = "copy_pack_injected"
        return guarded, meta

    if is_douyin_script:
        meta["scene"] = "douyin_script"
        if _has_copy_douyin_script_pack(text):
            meta["reason"] = "already_has_copy_pack"
            return reply, meta
        guarded = f"{text}\n\n---\n\n{_build_copy_douyin_script_pack(text)}"
        meta["applied"] = True
        meta["reason"] = "copy_pack_injected"
        return guarded, meta

    meta["scene"] = "customer_im"
    if _has_copy_im_pack(text):
        meta["reason"] = "already_has_copy_pack"
        return reply, meta
    guarded = f"{text}\n\n---\n\n{_build_copy_im_pack(text)}"
    meta["applied"] = True
    meta["reason"] = "copy_pack_injected"
    return guarded, meta


def _contains_any(text: str, hints: tuple[str, ...]) -> bool:
    if not text:
        return False
    return any(str(h).strip() and str(h).strip() in text for h in hints)


def _count_contains_any(text: str, hints: tuple[str, ...]) -> int:
    if not text:
        return 0
    return sum(1 for hint in hints if str(hint).strip() and str(hint).strip() in text)


def _looks_like_data_diagnostic_request(message: str) -> bool:
    """Detect data-diagnostic requests robustly even when intent.action drifts to execute/plan."""
    text = str(message or "").strip().lower()
    if not text:
        return False

    strong_hits = _count_contains_any(text, _DISPATCH_DATA_STRONG_KEYWORDS)
    medium_hits = _count_contains_any(text, _DISPATCH_DATA_MEDIUM_KEYWORDS)
    analysis_hits = _count_contains_any(text, dict(_TASK_PURPOSE_HINTS).get("analysis", ()))

    diagnostic_pattern = bool(
        re.search(
            r"(漏斗|转化率|留存率|指标|口径|sql|报表).{0,16}(下降|下滑|异常|波动|原因|诊断|分析|复盘)",
            text,
        )
    )

    if strong_hits >= 1:
        return True
    if medium_hits >= 2 and analysis_hits >= 1:
        return True
    if diagnostic_pattern:
        return True
    return False


def _infer_task_purpose(message: str, *, action: str = "") -> str:
    text = str(message or "").strip().lower()
    action_key = str(action or "").strip().lower()

    strong_data_diagnostic = _looks_like_data_diagnostic_request(text)

    if action_key in {"execute", "plan", "optimize"}:
        if strong_data_diagnostic:
            return "analysis"
        return "execution"
    if action_key in {"analyze", "analysis"}:
        return "analysis"
    if action_key in {"create", "creative"}:
        return "creation"

    for purpose_key, hints in _TASK_PURPOSE_HINTS:
        if _contains_any(text, hints):
            if purpose_key == "execution" and strong_data_diagnostic:
                return "analysis"
            return purpose_key

    if strong_data_diagnostic:
        return "analysis"

    return "general"


def _detect_task_brief_signals(message: str) -> Dict[str, bool]:
    text = str(message or "").strip()
    lowered = text.lower()
    return {
        "platform": _contains_any(lowered, _TASK_PLATFORM_HINTS),
        "objective": _contains_any(lowered, _TASK_OBJECTIVE_HINTS),
        "timeframe": bool(_TASK_TIME_HINTS_RE.search(text)),
        "audience": _contains_any(lowered, _TASK_AUDIENCE_HINTS),
        "constraints": _contains_any(lowered, _TASK_CONSTRAINT_HINTS),
        "context": _contains_any(lowered, _TASK_CONTEXT_HINTS),
    }


def _looks_like_direct_deliverable_request(message: str) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False

    has_request = any(token in text for token in _TASK_DIRECT_DELIVERABLE_REQUEST_HINTS)
    if not has_request:
        has_request = bool(
            re.search(
                r"(给|做|写|产出|形成).{0,12}(报告|洞察|清单|方案|计划|脚本|话术|模板|sop)",
                text,
            )
        )
    has_revision_request = any(token in text for token in _TASK_REVISION_REQUEST_HINTS)
    if not has_request and not has_revision_request:
        return False

    has_artifact = any(token in text for token in _TASK_DIRECT_DELIVERABLE_ARTIFACT_HINTS)
    has_count_request = bool(re.search(r"\d+\s*(?:条|版|组|个|份|步|项)", text))
    return bool(has_artifact or has_count_request or has_revision_request)



def _infer_missing_task_fields(purpose_key: str, signals: Dict[str, bool]) -> List[str]:
    required = _TASK_PURPOSE_REQUIRED_FIELDS.get(purpose_key) or _TASK_PURPOSE_REQUIRED_FIELDS["general"]
    missing: List[str] = []
    for field in required:
        if not bool(signals.get(field)):
            missing.append(field)
    return missing


def _label_task_fields(field_keys: List[str]) -> List[str]:
    labels: List[str] = []
    for key in field_keys:
        labels.append(_TASK_FIELD_LABELS.get(str(key or ""), str(key or "")))
    return labels


def _build_task_framing(
    message: str,
    *,
    action: str = "",
    response_mode: str = _RESPONSE_MODE_EXECUTION,
) -> Dict[str, Any]:
    text = str(message or "").strip()
    if not text:
        return {}

    mode = _normalize_response_mode(response_mode)
    purpose_key = _infer_task_purpose(text, action=action)
    purpose_label = _TASK_PURPOSE_LABELS.get(purpose_key, _TASK_PURPOSE_LABELS["general"])
    signals = _detect_task_brief_signals(text)
    missing_keys = _infer_missing_task_fields(purpose_key, signals)
    missing_fields = _label_task_fields(missing_keys)
    carryover_required = _contains_any(text.lower(), _TASK_CARRYOVER_HINTS)
    direct_deliverable = _looks_like_direct_deliverable_request(text)
    if mode == _RESPONSE_MODE_EXECUTION and carryover_required:
        direct_deliverable = True

    # 显式数据诊断请求在信息未满配时也优先给可执行结论，避免误触发“先追问”。
    strong_data_diagnostic = bool(
        purpose_key == "analysis" and _looks_like_data_diagnostic_request(text)
    )

    # 鐢ㄦ埛杈撳叆寰堥暱鏃讹紝榛樿缁欏嚭涓存椂鏂规骞舵彁绀鸿ˉ淇℃伅锛屼笉寮鸿鍏堣拷闂€?
    smalltalk_message = _looks_like_smalltalk_message(text)
    should_clarify = (
        bool(missing_keys)
        and len(text) <= 180
        and not smalltalk_message
        and not carryover_required
        and not direct_deliverable
        and not strong_data_diagnostic
    )
    clarify_question = _TASK_CLARIFY_QUESTION_MAP.get(missing_keys[0], "") if should_clarify else ""
    carryover_constraint_anchors: List[str] = []
    carryover_keyword_anchors: List[str] = []
    if carryover_required:
        for match in _TASK_CONSTRAINT_ANCHOR_RE.findall(text):
            token = str(match or "").strip()
            if token and token not in carryover_constraint_anchors:
                carryover_constraint_anchors.append(token)
            if len(carryover_constraint_anchors) >= 4:
                break

        for pattern in (_TASK_CARRYOVER_KEYWORD_ANCHOR_RE, _TASK_CARRYOVER_NUMBER_ANCHOR_RE):
            for match in pattern.findall(text):
                token = str(match or "").strip()
                token = re.sub(r"^(请|请你|给我|帮我|继续|承接|按|把)+", "", token)
                token = re.sub(r"(适合今天就执行|今天就执行|继续上面内容|请继续上面内容)$", "", token)
                token = token.strip("，。；、:： ")
                if not token:
                    continue
                if any(stop in token for stop in _TASK_CARRYOVER_KEYWORD_STOPWORDS):
                    continue
                if len(token) == 1 and not token.isdigit():
                    continue
                if len(token) > 12 and not token.isdigit():
                    continue
                if token not in carryover_keyword_anchors:
                    carryover_keyword_anchors.append(token)
                if len(carryover_keyword_anchors) >= 5:
                    break
            if len(carryover_keyword_anchors) >= 5:
                break

    return {
        "mode": mode,
        "purpose_key": purpose_key,
        "purpose": purpose_label,
        "missing_fields": missing_fields,
        "should_clarify": should_clarify,
        "clarify_question": clarify_question,
        "smalltalk": bool(smalltalk_message),
        "direct_deliverable": bool(direct_deliverable),
        "strong_data_diagnostic": bool(strong_data_diagnostic),
        "carryover_required": carryover_required,
        "carryover_constraint_anchors": carryover_constraint_anchors,
        "carryover_keyword_anchors": carryover_keyword_anchors,
        "strategy": "先框定用途，再给短答，最后展开",
        "temporary_plan_policy": "若信息未补齐，先给基于当前假设的结论并明确标注“假设”。",
    }


def _task_framing_instruction(task_framing: Dict[str, Any], response_mode: str) -> str:
    if not isinstance(task_framing, dict) or not task_framing:
        return ""

    purpose = str(task_framing.get("purpose") or "综合咨询").strip()
    missing_fields = task_framing.get("missing_fields") if isinstance(task_framing.get("missing_fields"), list) else []
    missing_text = "、".join([str(x).strip() for x in missing_fields if str(x).strip()])
    carryover_required = bool(task_framing.get("carryover_required"))
    carryover_anchors = task_framing.get("carryover_constraint_anchors") if isinstance(task_framing.get("carryover_constraint_anchors"), list) else []
    carryover_anchor_text = "、".join([str(x).strip() for x in carryover_anchors if str(x).strip()])
    carryover_keywords = task_framing.get("carryover_keyword_anchors") if isinstance(task_framing.get("carryover_keyword_anchors"), list) else []
    carryover_keyword_text = "、".join([str(x).strip() for x in carryover_keywords if str(x).strip()])
    mode = _normalize_response_mode(response_mode)
    hide_system_labels = "不要输出“任务框定/用途判断/系统校准”等系统化标签。"

    if bool(task_framing.get("smalltalk")):
        return "【任务框定】当前是寒暄/闲聊型输入。请简短自然回复，不追问业务参数，不追加执行清单。"

    if mode == _RESPONSE_MODE_LEARNING and carryover_required:
        if carryover_anchor_text and carryover_keyword_text:
            return (
                f"【任务框定】这是{purpose}类承接追问。先用一句自然语言承接上一轮框架（重点承接：{carryover_anchor_text}），"
                f"并覆盖本轮关键词/数字约束：{carryover_keyword_text}；再给本轮教学回答。{hide_system_labels}"
            )
        if carryover_anchor_text:
            return (
                f"【任务框定】这是{purpose}类承接追问。先用一句自然语言承接上一轮框架（重点承接：{carryover_anchor_text}），"
                f"并覆盖本轮关键场景词或数字；再给本轮教学回答。{hide_system_labels}"
            )
        if carryover_keyword_text:
            return (
                f"【任务框定】这是{purpose}类承接追问。先用一句自然语言承接上一轮框架，并覆盖这些关键词：{carryover_keyword_text}；"
                f"再给本轮教学回答。{hide_system_labels}"
            )
        return (
            f"【任务框定】这是{purpose}类承接追问。先用一句自然语言承接上一轮关键结论与约束，再给本轮教学回答；"
            f"必须沿用上一轮主题，不得换题。{hide_system_labels}"
        )

    if mode == _RESPONSE_MODE_EXECUTION and carryover_required:
        if carryover_anchor_text:
            return (
                f"【任务框定】这是{purpose}类承接追问。先用一句自然语言承接上一轮并复述这些新增约束：{carryover_anchor_text}；"
                f"再给改版方案，明确优先级、取舍、风险与回滚。{hide_system_labels}"
            )
        return (
            f"【任务框定】这是{purpose}类承接追问。先用一句自然语言承接上一轮关键约束，"
            f"再给改版方案，明确优先级与取舍。{hide_system_labels}"
        )

    if bool(task_framing.get("direct_deliverable")) and mode == _RESPONSE_MODE_EXECUTION and missing_text:
        return (
            "【任务框定】这是直接交付型需求。优先直接给可执行成品，禁止先反问；"
            f"若信息不足，在结尾用一句“关键假设”补充即可。{hide_system_labels}"
        )

    if bool(task_framing.get("should_clarify")) and missing_text:
        max_points = 3 if mode == _RESPONSE_MODE_LEARNING else 5
        return (
            f"【任务框定】当前仍缺少关键信息：{missing_text}。"
            "先用一句自然语言确认你的理解，只提1个最关键追问；"
            f"同时给“基于当前假设的30秒结论”（不超过 {max_points} 条）。{hide_system_labels}"
        )

    return (
        f"【任务框定】这是{purpose}类请求，信息已基本充分。"
        f"直接给结论与可执行步骤，必要时补风险与回滚。{hide_system_labels}"
    )

def _apply_task_framing_prompt(system_prompt: str, task_framing: Dict[str, Any], response_mode: str) -> str:
    instruction = _task_framing_instruction(task_framing, response_mode)
    if not instruction:
        return system_prompt
    if not system_prompt:
        return instruction
    return f"{instruction}\n{system_prompt}"


_COLLABORATION_MODE_AUTO = "auto"
_COLLABORATION_MODE_SINGLE = "single"
_COLLABORATION_MODE_MANUAL = "manual"
_VALID_COLLABORATION_MODES = {
    _COLLABORATION_MODE_AUTO,
    _COLLABORATION_MODE_SINGLE,
    _COLLABORATION_MODE_MANUAL,
}

# 手动协作允许更大角色集合，避免用户多角色组合被硬截断。
_SUPPORT_ROLE_LIMIT_SAFETY_CAP = 2048
_MANUAL_HIRED_ROLE_SELECTION_HARD_CAP = 2048

# 当主回复已经耗时较长且用户并未明确要求协作时，跳过多Agent分发，优先保证首轮可用响应时延。
_COLLAB_DISPATCH_START_LATENCY_BUDGET_SECONDS = 35.0
_COLLAB_DISPATCH_START_LATENCY_BUDGET_SECONDS_AUTO_MULTI = 120.0

_EXPLICIT_COLLAB_SIGNALS = (
    "多智能体",
    "多agent",
    "multi-agent",
    "multi agent",
    "多角色",
    "协同",
    "协作",
)


def _normalize_collaboration_mode(raw_mode: str | None) -> str:
    mode = str(raw_mode or "").strip().lower()
    return mode if mode in _VALID_COLLABORATION_MODES else _COLLABORATION_MODE_AUTO


def _resolve_role_lock(
    *,
    role: str | None,
    collaboration_mode: str | None,
    role_lock: bool | None = None,
) -> bool:
    """Resolve whether primary-role lock should be active for this turn."""
    has_role = bool(str(role or "").strip())
    if not has_role:
        return False

    mode = _normalize_collaboration_mode(collaboration_mode)
    if role_lock is None:
        return mode in {_COLLABORATION_MODE_MANUAL, _COLLABORATION_MODE_SINGLE}
    return bool(role_lock)


def _manual_collaboration_role_floor(default_floor: int = 8) -> int:
    floor = max(1, int(default_floor or 8))
    try:
        from src.core.role_router import build_runtime_role_context

        role_ctx = build_runtime_role_context()
        runtime_roles = [
            str(item).strip().lower()
            for item in (role_ctx.get("runtime_roles") or [])
            if str(item).strip()
        ]
        if runtime_roles:
            floor = max(floor, len(set(runtime_roles)))
    except Exception:
        pass
    return floor


def _support_agent_role_limit(
    *,
    collaboration_mode: str | None = None,
    default_auto: int = 32,
    default_manual: int = 8,
) -> int:
    """Resolve support-role cap by collaboration mode with a hard safety ceiling."""
    mode = _normalize_collaboration_mode(collaboration_mode)
    try:
        from src.config import SUPPORT_AGENT_MAX_ROLES, SUPPORT_AGENT_MAX_ROLES_MANUAL

        auto_limit = int(SUPPORT_AGENT_MAX_ROLES or default_auto)
        manual_floor = max(_manual_collaboration_role_floor(default_manual), auto_limit)
        manual_limit = int(SUPPORT_AGENT_MAX_ROLES_MANUAL or manual_floor)
        manual_limit = max(manual_floor, manual_limit)
    except Exception:
        auto_limit = int(default_auto)
        manual_limit = int(max(_manual_collaboration_role_floor(default_manual), auto_limit))

    raw_limit = manual_limit if mode == _COLLABORATION_MODE_MANUAL else auto_limit
    return max(1, min(raw_limit, _SUPPORT_ROLE_LIMIT_SAFETY_CAP))


def _normalize_hired_roles(
    raw_roles: List[str],
    *,
    allow_engineering: bool,
    collaboration_mode: str | None = None,
    max_roles_override: int | None = None,
    role_ctx: Optional[Dict[str, Any]] = None,
) -> List[str]:
    normalized: List[str] = []
    seen: set[str] = set()
    resolved_role_ctx = role_ctx
    if resolved_role_ctx is None:
        try:
            from src.core.role_router import build_runtime_role_context

            resolved_role_ctx = build_runtime_role_context()
        except Exception:
            resolved_role_ctx = None

    hired_limit = _support_agent_role_limit(collaboration_mode=collaboration_mode) + 1
    if max_roles_override is not None:
        try:
            hired_limit = int(max_roles_override)
        except Exception:
            pass
    mode = _normalize_collaboration_mode(collaboration_mode)
    if mode == _COLLABORATION_MODE_MANUAL:
        hired_limit = max(1, min(hired_limit, _MANUAL_HIRED_ROLE_SELECTION_HARD_CAP))
    else:
        hired_limit = max(1, min(hired_limit, _SUPPORT_ROLE_LIMIT_SAFETY_CAP + 1))

    for role in raw_roles:
        token = _normalize_runtime_role_token(
            str(role or "").strip(),
            allow_default=False,
            role_ctx=resolved_role_ctx,
        )
        if not token:
            continue
        if (
            not allow_engineering
            and token == "engineering"
            and mode != _COLLABORATION_MODE_MANUAL
        ):
            continue
        if token in seen:
            continue
        seen.add(token)
        normalized.append(token)
        if len(normalized) >= hired_limit:
            break
    return normalized


def _resolve_manual_collaboration_roles(
    *,
    requested_primary_role: str | None,
    hired_roles: List[str],
    fallback_primary_role: str | None,
    role_ctx: Optional[Dict[str, Any]] = None,
) -> tuple[str, List[str], List[str], bool]:
    resolved_role_ctx = role_ctx
    if resolved_role_ctx is None:
        try:
            from src.core.role_router import build_runtime_role_context

            resolved_role_ctx = build_runtime_role_context()
        except Exception:
            resolved_role_ctx = None

    ordered_roles: List[str] = []
    seen_roles: set[str] = set()
    for raw_role in hired_roles or []:
        token = _normalize_runtime_role_token(
            str(raw_role or "").strip(),
            allow_default=False,
            role_ctx=resolved_role_ctx,
        )
        if not token or token in seen_roles:
            continue
        seen_roles.add(token)
        ordered_roles.append(token)

    explicit_primary = _normalize_runtime_role_token(
        requested_primary_role,
        allow_default=False,
        role_ctx=resolved_role_ctx,
    )
    fallback_primary = _normalize_runtime_role_token(
        fallback_primary_role,
        allow_default=True,
        role_ctx=resolved_role_ctx,
    )

    fallback_used = False
    if explicit_primary:
        primary_role = explicit_primary
        if primary_role not in ordered_roles:
            ordered_roles = [primary_role, *ordered_roles]
    elif ordered_roles:
        primary_role = ordered_roles[0]
        fallback_used = True
    elif fallback_primary:
        primary_role = fallback_primary
        ordered_roles = [primary_role]
        fallback_used = True
    else:
        primary_role = "ops"
        ordered_roles = [primary_role]
        fallback_used = True

    support_roles = [role for role in ordered_roles if role != primary_role]
    return primary_role, ordered_roles, support_roles, fallback_used


def _role_list_consistency(expected_roles: List[str], actual_roles: List[str]) -> Dict[str, Any]:
    expected: List[str] = []
    seen_expected: set[str] = set()
    for role in expected_roles or []:
        token = str(role or "").strip().lower()
        if not token or token in seen_expected:
            continue
        seen_expected.add(token)
        expected.append(token)

    actual: List[str] = []
    seen_actual: set[str] = set()
    for role in actual_roles or []:
        token = str(role or "").strip().lower()
        if not token or token in seen_actual:
            continue
        seen_actual.add(token)
        actual.append(token)

    actual_set = set(actual)
    expected_set = set(expected)
    missing = [role for role in expected if role not in actual_set]
    extra = [role for role in actual if role not in expected_set]
    return {
        "match": not missing and not extra,
        "missing": missing,
        "extra": extra,
        "expected": expected,
        "actual": actual,
    }


def _filter_support_roles_by_dispatch_profile(
    roles: List[str],
    *,
    primary_role: str,
    intent_profile: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Filter noisy support-role candidates for auto/free scheduling."""
    if not roles:
        return []

    profile = intent_profile if isinstance(intent_profile, dict) else {}
    if not bool(profile.get("suppress_data_support")):
        return list(roles)

    primary = str(primary_role or "").strip().lower()
    filtered: List[str] = []
    seen: set[str] = set()
    for role in roles:
        token = str(role or "").strip().lower()
        if not token or token in seen:
            continue
        if token == "data" and primary != "data":
            continue
        seen.add(token)
        filtered.append(token)
    return filtered



def _count_role_package_keyword_hits(
    role: str,
    message: str,
    *,
    role_ctx: Optional[Dict[str, Any]] = None,
) -> int:
    role_key = str(role or "").strip().lower()
    text_lower = str(message or "").strip().lower()
    if not role_key or not text_lower:
        return 0

    packages = role_ctx.get("role_packages") if isinstance(role_ctx, dict) else {}
    if not isinstance(packages, dict):
        return 0

    candidates = packages.get(role_key) if isinstance(packages.get(role_key), list) else []
    if not candidates:
        return 0

    seen_keywords: set[str] = set()
    hits = 0
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        keywords = candidate.get("keywords") if isinstance(candidate.get("keywords"), list) else []
        for kw in keywords:
            token = str(kw or "").strip().lower()
            if not token or token in seen_keywords:
                continue
            if token in text_lower:
                seen_keywords.add(token)
                hits += 1
    return hits


def _apply_role_router_suggestions_to_intent(
    intent: Any,
    *,
    message: str,
    collaboration_mode: str | None = None,
    role_locked: bool = False,
    explicit_collab_requested: bool = False,
    role_ctx: Optional[Dict[str, Any]] = None,
    allow_engineering: bool = True,
    max_support_roles: int = 3,
    intent_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Apply role-router suggestion as a generic second-pass dispatcher guard.

    Goal: reduce intent-only misroutes in auto/single mode by re-checking the
    runtime role package router, while never overriding explicit user selection.
    """
    trace: Dict[str, Any] = {
        "applied": False,
        "primary_overridden": False,
        "added_support_roles": [],
        "suggested_roles": [],
        "reason": "",
        "current_primary_hits": 0,
        "candidate_primary_hits": 0,
    }

    if intent is None:
        trace["reason"] = "empty_intent"
        return trace

    mode = _normalize_collaboration_mode(collaboration_mode)
    if mode not in {_COLLABORATION_MODE_AUTO, _COLLABORATION_MODE_SINGLE}:
        trace["reason"] = "non_auto_single_mode"
        return trace
    if bool(role_locked):
        trace["reason"] = "role_locked"
        return trace
    if bool(explicit_collab_requested):
        trace["reason"] = "explicit_collaboration_requested"
        return trace

    text = str(message or "").strip()
    if not text:
        trace["reason"] = "empty_message"
        return trace

    action = str(getattr(intent, "action", "") or "").strip().lower()
    domain_id = str(getattr(intent, "domain_id", "") or "").strip() or "domain.general"
    profile = intent_profile if isinstance(intent_profile, dict) else {}
    prefer_ops_primary = bool(profile.get("prefer_ops_primary"))
    prefer_data_primary = bool(profile.get("prefer_data_primary"))
    visual_analysis_focus = bool(profile.get("visual_analysis_focus"))

    try:
        from src.core.role_router import suggest_roles

        requested_max = max(2, int(max_support_roles or 0) + 1)
        suggested_raw = suggest_roles(
            text,
            domain_id,
            max_roles=requested_max,
            action=action,
        )
    except Exception:
        trace["reason"] = "role_router_unavailable"
        return trace

    suggested_roles: List[str] = []
    seen_suggestions: set[str] = set()
    engineering_filtered_out = False
    for role_name in suggested_raw or []:
        token = _normalize_runtime_role_token(
            str(role_name or ""),
            allow_default=False,
            role_ctx=role_ctx,
        )
        if not token:
            continue
        if not allow_engineering and token == "engineering":
            engineering_filtered_out = True
            continue
        if token in seen_suggestions:
            continue
        seen_suggestions.add(token)
        suggested_roles.append(token)

    if not suggested_roles:
        trace["reason"] = "no_suggestions"
        return trace

    trace["suggested_roles"] = list(suggested_roles)

    current_primary = _normalize_runtime_role_token(
        str(getattr(intent, "primary_role", "") or ""),
        allow_default=True,
        role_ctx=role_ctx,
    ) or "ops"

    support_roles: List[str] = []
    seen_support: set[str] = set()
    for item in list(getattr(intent, "support_roles", []) or []):
        token = _normalize_runtime_role_token(
            str(item or ""),
            allow_default=False,
            role_ctx=role_ctx,
        )
        if not token:
            continue
        if not allow_engineering and token == "engineering":
            continue
        if token == current_primary or token in seen_support:
            continue
        seen_support.add(token)
        support_roles.append(token)

    candidate_primary = str(suggested_roles[0] or "").strip().lower()
    current_hits = _count_role_package_keyword_hits(
        current_primary,
        text,
        role_ctx=role_ctx,
    )
    candidate_hits = _count_role_package_keyword_hits(
        candidate_primary,
        text,
        role_ctx=role_ctx,
    )
    trace["current_primary_hits"] = int(current_hits)
    trace["candidate_primary_hits"] = int(candidate_hits)

    changed = False
    if candidate_primary and candidate_primary != current_primary:
        top_two = set(suggested_roles[:2])
        should_override = False
        if (
            prefer_ops_primary
            and candidate_primary == "ops"
            and current_primary == "data"
            and not prefer_data_primary
        ):
            should_override = True
        elif (
            prefer_data_primary
            and candidate_primary == "data"
            and current_primary != "data"
        ):
            should_override = True
        elif current_primary not in top_two:
            should_override = True
        elif int(candidate_hits) >= int(current_hits) + 2:
            should_override = True
        elif int(current_hits) == 0 and int(candidate_hits) > 0:
            should_override = True
        elif (
            action in {"analyze", "query"}
            and candidate_primary == suggested_roles[0]
            and int(candidate_hits) >= max(1, int(current_hits))
            and not (
                visual_analysis_focus
                and candidate_primary == "data"
                and not prefer_data_primary
            )
        ):
            # 分析/查询动作下，若路由器第一候选与当前主角色冲突且命中不弱于当前，
            # 优先采用路由器建议，减少“指标诊断仍停留在运营主角色”的误派。
            should_override = True

        if (
            not should_override
            and visual_analysis_focus
            and current_primary == "data"
            and candidate_primary in {"design", "creative", "ops"}
            and int(candidate_hits) >= max(1, int(current_hits))
        ):
            should_override = True

        if (
            should_override
            and visual_analysis_focus
            and candidate_primary == "data"
            and not prefer_data_primary
        ):
            should_override = False

        if (
            should_override
            and engineering_filtered_out
            and current_primary == "ops"
            and candidate_primary == "data"
            and action in {"execute", "optimize"}
            and int(candidate_hits) <= int(current_hits) + 1
        ):
            # engineering 被配置关闭时，避免将技术排障类请求误降级到 data 主角色；
            # 这类请求在缺工程岗位时优先由 ops 承接。
            should_override = False

        if should_override:
            current_primary = candidate_primary
            setattr(intent, "primary_role", current_primary)
            trace["primary_overridden"] = True
            changed = True

    added_support: List[str] = []
    for role_name in suggested_roles[1:]:
        token = str(role_name or "").strip().lower()
        if not token or token == current_primary:
            continue
        if token in support_roles:
            continue
        support_roles.append(token)
        added_support.append(token)

    if int(max_support_roles or 0) > 0:
        support_roles = support_roles[: int(max_support_roles)]

    if support_roles != list(getattr(intent, "support_roles", []) or []):
        setattr(intent, "support_roles", support_roles)
        changed = True

    if added_support:
        trace["added_support_roles"] = added_support

    trace["applied"] = bool(changed)
    trace["reason"] = "adjusted" if changed else "no_change_needed"
    return trace

def _apply_dispatch_profile_to_intent(
    intent: Any,
    *,
    intent_profile: Optional[Dict[str, Any]] = None,
    collaboration_mode: str | None = None,
    role_locked: bool = False,
    explicit_collab_requested: bool = False,
    role_ctx: Optional[Dict[str, Any]] = None,
    allow_engineering: bool = True,
) -> Dict[str, Any]:
    """Apply dispatch-profile corrections to intent in auto/single modes.

    This is an intent-level correction layer to avoid marketing requests being
    routed to data as primary role when there is no strong data evidence.
    """
    trace: Dict[str, Any] = {
        "applied": False,
        "primary_overridden": False,
        "removed_support_roles": [],
        "added_support_roles": [],
        "reason": "",
    }

    if intent is None:
        trace["reason"] = "empty_intent"
        return trace

    mode = _normalize_collaboration_mode(collaboration_mode)
    if mode not in {_COLLABORATION_MODE_AUTO, _COLLABORATION_MODE_SINGLE}:
        trace["reason"] = "non_auto_single_mode"
        return trace
    if bool(role_locked):
        trace["reason"] = "role_locked"
        return trace
    if bool(explicit_collab_requested):
        trace["reason"] = "explicit_collaboration_requested"
        return trace

    profile = intent_profile if isinstance(intent_profile, dict) else {}
    marketing_focus = bool(profile.get("marketing_focus"))
    prefer_ops_primary = bool(profile.get("prefer_ops_primary"))
    prefer_data_primary = bool(profile.get("prefer_data_primary"))
    prefer_design_primary = bool(profile.get("prefer_design_primary"))
    suppress_data_support = bool(profile.get("suppress_data_support"))
    has_any_data_evidence = bool(profile.get("has_any_data_evidence"))
    has_strong_data_evidence = bool(profile.get("has_strong_data_evidence"))
    strong_data_diagnostic = bool(profile.get("strong_data_diagnostic"))
    marketing_action = bool(profile.get("marketing_action"))
    analysis_action = bool(profile.get("analysis_action"))
    visual_analysis_focus = bool(profile.get("visual_analysis_focus"))
    attachment_visual_marketing_focus = bool(profile.get("attachment_visual_marketing_focus"))
    attachment_data_table_focus = bool(profile.get("attachment_data_table_focus"))

    primary_role = _normalize_runtime_role_token(
        str(getattr(intent, "primary_role", "") or ""),
        allow_default=True,
        role_ctx=role_ctx,
    ) or "ops"

    support_raw = list(getattr(intent, "support_roles", []) or [])
    support_roles: List[str] = []
    seen_support: set[str] = set()
    for item in support_raw:
        token = _normalize_runtime_role_token(
            str(item or ""),
            allow_default=False,
            role_ctx=role_ctx,
        )
        if not token:
            continue
        if not allow_engineering and token == "engineering":
            continue
        if token == primary_role or token in seen_support:
            continue
        seen_support.add(token)
        support_roles.append(token)

    changed = False

    should_promote_data_primary = bool(
        prefer_data_primary
        and not visual_analysis_focus
        and primary_role != "data"
        and (
            strong_data_diagnostic
            or has_strong_data_evidence
            or analysis_action
            or int(profile.get("data_diagnostic_hits") or 0) >= 2
            or (attachment_data_table_focus and has_any_data_evidence)
        )
    )
    if should_promote_data_primary:
        preferred_data_role = _normalize_runtime_role_token(
            "data",
            allow_default=False,
            role_ctx=role_ctx,
        )
        if preferred_data_role and preferred_data_role != primary_role:
            primary_role = preferred_data_role
            setattr(intent, "primary_role", primary_role)
            trace["primary_overridden"] = True
            changed = True

    should_promote_design_primary = bool(
        (prefer_design_primary or visual_analysis_focus)
        and primary_role != "design"
        and not has_strong_data_evidence
        and primary_role in {"data", "ops", "creative", "web", "service"}
    )
    if should_promote_design_primary:
        preferred_design_role = _normalize_runtime_role_token(
            "design",
            allow_default=False,
            role_ctx=role_ctx,
        )
        if preferred_design_role and preferred_design_role != primary_role:
            primary_role = preferred_design_role
            setattr(intent, "primary_role", primary_role)
            trace["primary_overridden"] = True
            changed = True

    should_demote_data_primary = bool(
        (marketing_focus or visual_analysis_focus)
        and primary_role == "data"
        and (
            prefer_ops_primary
            or prefer_design_primary
            or visual_analysis_focus
            or (marketing_action and not has_strong_data_evidence)
            or (suppress_data_support and not has_strong_data_evidence)
            or (analysis_action and not has_strong_data_evidence and bool(profile.get("marketing_intensity", 0) >= 2))
        )
    )

    if should_demote_data_primary:
        fallback_primary_hint = "design" if prefer_design_primary else "ops"
        preferred_primary = _normalize_runtime_role_token(
            fallback_primary_hint,
            allow_default=True,
            role_ctx=role_ctx,
        ) or "ops"
        if preferred_primary and preferred_primary != primary_role:
            primary_role = preferred_primary
            setattr(intent, "primary_role", primary_role)
            trace["primary_overridden"] = True
            changed = True

    if suppress_data_support and primary_role != "data":
        filtered_support: List[str] = []
        removed_support: List[str] = []
        for role_name in support_roles:
            if role_name == "data":
                removed_support.append(role_name)
                continue
            filtered_support.append(role_name)
        if removed_support:
            trace["removed_support_roles"] = removed_support
            support_roles = filtered_support
            changed = True

    intent_tags = {
        str(tag or "").strip().lower()
        for tag in (profile.get("intent_tags") if isinstance(profile.get("intent_tags"), list) else [])
        if str(tag or "").strip()
    }
    required_support_by_tag: Dict[str, str] = {
        "engineering": "engineering",
        "service": "service",
        "finance": "accounting",
        "creative": "creative",
        "design": "design",
        "web": "web",
    }
    if bool(profile.get("has_strong_data_evidence")) and "data" in intent_tags:
        required_support_by_tag["data"] = "data"

    if attachment_visual_marketing_focus:
        required_support_by_tag.setdefault("attachment_visual_design", "design")
        required_support_by_tag.setdefault("attachment_visual_creative", "creative")
        required_support_by_tag.setdefault("attachment_visual_ops", "ops")

    if visual_analysis_focus:
        required_support_by_tag.setdefault("attachment_visual_design", "design")
        required_support_by_tag.setdefault("attachment_visual_ops", "ops")

    if (
        attachment_data_table_focus
        and not suppress_data_support
        and (has_any_data_evidence or has_strong_data_evidence or strong_data_diagnostic)
    ):
        required_support_by_tag.setdefault("attachment_data_table", "data")

    added_support_roles: List[str] = []
    for tag, mapped_role in required_support_by_tag.items():
        if (
            tag not in intent_tags
            and not tag.startswith("attachment_")
        ):
            continue
        if mapped_role == primary_role:
            continue
        if mapped_role in support_roles:
            continue
        if not allow_engineering and mapped_role == "engineering":
            continue
        support_roles.append(mapped_role)
        added_support_roles.append(mapped_role)

    if added_support_roles:
        trace["added_support_roles"] = added_support_roles
        changed = True

    if support_roles != list(getattr(intent, "support_roles", []) or []):
        setattr(intent, "support_roles", support_roles)
        changed = True

    trace["applied"] = bool(changed)
    trace["reason"] = "adjusted" if changed else "no_change_needed"
    return trace


def _build_dispatch_debug_fields(
    intent_profile: Optional[Dict[str, Any]],
    role_router_adjustment: Optional[Dict[str, Any]],
    profile_adjustment: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    profile = intent_profile if isinstance(intent_profile, dict) else {}
    role_router = role_router_adjustment if isinstance(role_router_adjustment, dict) else {}
    profile_trace = profile_adjustment if isinstance(profile_adjustment, dict) else {}

    return {
        "dispatch_scheduler_version": "global_dispatch_v2",
        "dispatch_marketing_focus": bool(profile.get("marketing_focus")),
        "dispatch_marketing_intensity": int(profile.get("marketing_intensity") or 0),
        "dispatch_data_strong_hits": int(profile.get("data_strong_hits") or 0),
        "dispatch_data_medium_hits": int(profile.get("data_medium_hits") or 0),
        "dispatch_data_weak_hits": int(profile.get("data_weak_hits") or 0),
        "dispatch_data_diagnostic_hits": int(profile.get("data_diagnostic_hits") or 0),
        "dispatch_strategy_delivery_hits": int(profile.get("strategy_delivery_hits") or 0),
        "dispatch_growth_goal_hits": int(profile.get("growth_goal_hits") or 0),
        "dispatch_prefer_ops_primary": bool(profile.get("prefer_ops_primary")),
        "dispatch_prefer_data_primary": bool(profile.get("prefer_data_primary")),
        "dispatch_prefer_design_primary": bool(profile.get("prefer_design_primary")),
        "dispatch_visual_request_focus": bool(profile.get("visual_request_focus")),
        "dispatch_visual_analysis_focus": bool(profile.get("visual_analysis_focus")),
        "dispatch_attachment_visual_query_without_metric": bool(profile.get("attachment_visual_query_without_metric")),
        "dispatch_message_data_metric_hits": int(profile.get("message_data_metric_hits") or 0),
        "dispatch_message_visual_intent_hits": int(profile.get("message_visual_intent_hits") or 0),
        "dispatch_strong_data_diagnostic": bool(profile.get("strong_data_diagnostic")),
        "dispatch_growth_strategy_focus": bool(profile.get("growth_strategy_focus")),
        "dispatch_suppress_data_support": bool(profile.get("suppress_data_support")),
        "dispatch_attachment_image_count": int(profile.get("attachment_image_count") or 0),
        "dispatch_attachment_table_count": int(profile.get("attachment_table_count") or 0),
        "dispatch_attachment_visual_hits": int(profile.get("attachment_visual_hits") or 0),
        "dispatch_attachment_data_hits": int(profile.get("attachment_data_hits") or 0),
        "dispatch_attachment_visual_marketing_focus": bool(profile.get("attachment_visual_marketing_focus")),
        "dispatch_attachment_data_table_focus": bool(profile.get("attachment_data_table_focus")),
        "dispatch_history_marketing_hits": int(profile.get("history_marketing_hits") or 0),
        "dispatch_history_data_strong_hits": int(profile.get("history_data_strong_hits") or 0),
        "dispatch_history_data_medium_hits": int(profile.get("history_data_medium_hits") or 0),
        "dispatch_history_design_creative_hits": int(profile.get("history_design_creative_hits") or 0),
        "dispatch_role_router_adjusted": bool(role_router.get("applied")),
        "dispatch_role_router_reason": str(role_router.get("reason") or ""),
        "dispatch_role_router_primary_overridden": bool(role_router.get("primary_overridden")),
        "dispatch_role_router_suggested_roles": list(role_router.get("suggested_roles") or []),
        "dispatch_role_router_added_support_roles": list(role_router.get("added_support_roles") or []),
        "dispatch_role_router_current_primary_hits": int(role_router.get("current_primary_hits") or 0),
        "dispatch_role_router_candidate_primary_hits": int(role_router.get("candidate_primary_hits") or 0),
        "dispatch_profile_adjusted": bool(profile_trace.get("applied")),
        "dispatch_profile_reason": str(profile_trace.get("reason") or ""),
        "dispatch_primary_overridden": bool(profile_trace.get("primary_overridden")),
        "dispatch_removed_support_roles": list(profile_trace.get("removed_support_roles") or []),
        "dispatch_profile_added_support_roles": list(profile_trace.get("added_support_roles") or []),
    }


def _contains_explicit_collab_signal(message: str) -> bool:
    text = str(message or "").strip().lower()
    if not text:
        return False
    return any(sig in text for sig in _EXPLICIT_COLLAB_SIGNALS)


def _is_explicit_collaboration_requested(
    message: str,
    *,
    collaboration_mode: str | None = None,
    hired_roles: Optional[List[str]] | None = None,
) -> bool:
    """Treat manual role selection as an explicit collaboration request.

    Relying only on message keywords can miss explicit user intent when the
    user selects manual collaboration from UI controls. In manual mode, any
    non-empty role selection should bypass implicit-collaboration guards.
    """
    mode = _normalize_collaboration_mode(collaboration_mode)
    if mode == _COLLABORATION_MODE_MANUAL:
        for role in hired_roles or []:
            if str(role or "").strip():
                return True
    return _contains_explicit_collab_signal(message)


_SMALLTALK_HINTS = (
    "你好",
    "您好",
    "在吗",
    "hi",
    "hello",
    "hey",
    "讲个笑话",
    "来个笑话",
    "说个笑话",
    "笑话",
    "你是谁",
    "你会什么",
    "早上好",
    "晚上好",
)

_SMALLTALK_BUSINESS_CUES = (
    "运营",
    "选品",
    "店铺",
    "数据",
    "roi",
    "转化",
    "预算",
    "广告",
    "投放",
    "活动",
    "产品",
    "上架",
    "复盘",
    "方案",
    "报告",
    "分析",
    "社群",
    "直播",
    "客服",
    "脚本",
    "周报",
    "月报",
    "素材",
    "标题",
    "文案",
    "海报",
    "平台",
    "抖音",
    "小红书",
    "淘宝",
    "京东",
    "拼多多",
    "亚马逊",
    "excel",
    "附件",
    "图片",
    "pdf",
)

_LEARNING_EXPLAIN_HINTS = (
    "什么是",
    "是什么意思",
    "怎么理解",
    "解释一下",
    "解释下",
    "讲解一下",
    "讲讲",
    "区别",
    "原理",
    "why",
    "what is",
)

_LEARNING_EXPLAIN_COMPLEXITY_CUES = (
    "方案",
    "策略",
    "排期",
    "预算",
    "执行",
    "落地",
    "拆解",
    "复盘",
    "优化",
    "分析",
    "指标",
    "roi",
    "gmv",
    "转化",
)

_COLLAB_BUSINESS_COMPLEXITY_CUES = (
    "并且",
    "同时",
    "分别",
    "分工",
    "协作",
    "协同",
    "补充",
    "联动",
    "方案",
    "策略",
    "排期",
    "预算",
    "风险",
    "复盘",
    "优化",
    "分析",
    "指标",
    "roi",
    "转化",
    "gmv",
    "目标",
    "执行",
)


def _looks_like_collaboration_worthy_message(message: str) -> bool:
    text = str(message or "").strip()
    if not text:
        return False

    lowered = text.lower()
    if _contains_explicit_collab_signal(text):
        return True

    if any(cue in lowered for cue in _COLLAB_BUSINESS_COMPLEXITY_CUES):
        return True

    if len(text) >= 56:
        return True

    if "\n" in text:
        return True

    if any(token in text for token in ("；", ";", "、", " and ", " then ")) and len(text) >= 20:
        return True

    return False


def _looks_like_smalltalk_message(message: str) -> bool:
    text = str(message or "").strip()
    if not text:
        return False

    lowered = text.lower()

    # 业务关键词优先，避免“你好+业务诉求”被误判为闲聊。
    if any(cue in lowered for cue in _SMALLTALK_BUSINESS_CUES):
        return False

    if any(hint in lowered for hint in _SMALLTALK_HINTS):
        return len(text) <= 36

    return False


def _looks_like_learning_explain_message(message: str) -> bool:
    text = str(message or "").strip()
    if not text:
        return False

    lowered = text.lower()
    if any(cue in lowered for cue in _LEARNING_EXPLAIN_COMPLEXITY_CUES):
        return False

    if any(hint in lowered for hint in _LEARNING_EXPLAIN_HINTS):
        return len(text) <= 72

    if len(text) <= 72 and re.search(r"(什么是|什么意思|怎么理解|区别|原理|why|what\s+is)", lowered):
        return True

    return False


def _describe_collab_not_executed_reason(reason: str, *, decision_reason: str = "") -> str:
    code = str(reason or "").strip().lower()
    decision = str(decision_reason or "").strip()
    mapping = {
        "": "",
        "quick_path": "当前走了快速回复路径，本轮未进入协作调度。",
        "handoff_disabled": "协作引擎当前处于关闭状态，未触发协作。",
        "asking_guard": "主回复判定为追问澄清，暂不触发协作。",
        "empty_primary_reply": "主回复为空，协作调度被跳过。",
        "dispatch_not_started": "协作已请求，但调度未成功启动。",
        "dispatch_failed": "协作调度启动后失败，请检查后端日志。",
        "mode_single": "当前为单角色模式，不触发协作。",
        "decision_guard": "协作决策门禁未通过，未触发协作。",
    }
    if code in mapping:
        return mapping[code]
    if code.startswith("decision_"):
        reason_key = code.split("decision_", 1)[1]
        if reason_key:
            return f"协作决策门禁未通过（{reason_key}），未触发协作。"
        return "协作决策门禁未通过，未触发协作。"
    if decision:
        return f"协作决策未通过（{decision}），未触发协作。"
    return "本轮协作未执行。"


def _should_run_multi_agent_collaboration(
    *,
    tier: int,
    support_roles: List[str],
    extra_from_reply: List[str],
    collaboration_mode: str,
    role_locked: bool,
    explicit_collab_requested: bool,
    message: str,
    task_should_clarify: bool = False,
    intent_confidence: float = 0.0,
    response_mode: Optional[str] = None,
    has_attachment_context: bool = False,
    decision_trace: Optional[Dict[str, Any]] = None,
) -> bool:
    has_support = bool(support_roles)
    auto_signal = bool(extra_from_reply)
    normalized_collaboration_mode = _normalize_collaboration_mode(collaboration_mode)
    message_complex = _looks_like_collaboration_worthy_message(message)
    base_should_collab = (
        (tier == 2 and has_support)
        or auto_signal
        # Auto mode is expected to honor scheduler-selected support roles.
        # Complexity and safety guards are still enforced below.
        or (normalized_collaboration_mode == _COLLABORATION_MODE_AUTO and has_support)
    )
    effective_response_mode = _normalize_response_mode(response_mode)

    trace = decision_trace if isinstance(decision_trace, dict) else None
    if trace is not None:
        trace["tier"] = int(tier)
        trace["has_support"] = bool(has_support)
        trace["auto_signal"] = bool(auto_signal)
        trace["base_signal"] = bool(base_should_collab)
        trace["collaboration_mode"] = normalized_collaboration_mode
        trace["message_complex"] = bool(message_complex)
        trace["role_locked"] = bool(role_locked)
        trace["explicit_collab_requested"] = bool(explicit_collab_requested)
        trace["task_should_clarify"] = bool(task_should_clarify)
        trace["intent_confidence"] = float(intent_confidence or 0.0)
        trace["response_mode"] = effective_response_mode
        trace["has_attachment_context"] = bool(has_attachment_context)

    def _mark(reason: str, should_collab: bool) -> bool:
        if trace is not None:
            trace["reason"] = str(reason or "").strip()
            trace["should_collab"] = bool(should_collab)
            trace["smalltalk_blocked"] = bool(reason == "smalltalk_guard")
        return bool(should_collab)

    if normalized_collaboration_mode == _COLLABORATION_MODE_SINGLE:
        return _mark("mode_single", False)

    if normalized_collaboration_mode == _COLLABORATION_MODE_MANUAL:
        return _mark("manual_support_present" if has_support else "manual_support_missing", has_support)

    if not base_should_collab:
        return _mark("no_collab_signal", False)

    if role_locked and not explicit_collab_requested:
        return _mark("role_locked_guard", False)

    if normalized_collaboration_mode == _COLLABORATION_MODE_AUTO and not explicit_collab_requested:
        if bool(task_should_clarify) and not bool(has_attachment_context) and not bool(has_support):
            return _mark("clarify_guard", False)

        if (
            effective_response_mode == _RESPONSE_MODE_LEARNING
            and _looks_like_learning_explain_message(message)
            and not message_complex
        ):
            return _mark("learning_explain_guard", False)

        if _looks_like_smalltalk_message(message):
            return _mark("smalltalk_guard", False)

        if auto_signal and int(tier or 0) < 2 and not message_complex and not has_support:
            return _mark("auto_signal_low_context_guard", False)

        if (
            int(tier or 0) >= 2
            and float(intent_confidence or 0.0) < 0.55
            and not message_complex
            and not has_support
        ):
            return _mark("low_confidence_guard", False)

    if (
        len(str(message or "").strip()) <= 8
        and not explicit_collab_requested
        and not bool(has_attachment_context)
    ):
        return _mark("short_message_guard", False)

    return _mark("allowed", True)



def _apply_auto_collab_force_dispatch_guard(
    *,
    should_collab: bool,
    collaboration_mode: str,
    support_roles: List[str],
    explicit_collab_requested: bool,
    role_locked: bool,
    message: str,
    decision_trace: Optional[Dict[str, Any]] = None,
) -> bool:
    if bool(should_collab):
        return True

    normalized_mode = _normalize_collaboration_mode(collaboration_mode)
    has_support = bool(support_roles)
    trace = decision_trace if isinstance(decision_trace, dict) else None

    if (
        normalized_mode != _COLLABORATION_MODE_AUTO
        or not has_support
        or bool(explicit_collab_requested)
        or bool(role_locked)
    ):
        return False

    blocked_reason = str((trace or {}).get("reason") or "").strip().lower()
    force_reasons = {
        "no_collab_signal",
        "clarify_guard",
        "auto_signal_low_context_guard",
        "low_confidence_guard",
    }
    if blocked_reason not in force_reasons:
        return False

    if _looks_like_smalltalk_message(message):
        return False

    if trace is not None:
        trace["reason"] = "auto_support_present_force_dispatch"
        trace["auto_force_dispatch"] = True
        trace["auto_force_dispatch_from"] = blocked_reason

    return True

_TASK_ANCHOR_SHORT_MESSAGE_MAX_CHARS = 24
_TASK_ANCHOR_MID_MESSAGE_MAX_CHARS = 96
_TASK_ANCHOR_CONFIDENCE_OVERRIDE_THRESHOLD = 0.62
_TASK_ANCHOR_CONTINUATION_CONFIDENCE_THRESHOLD = 0.78
_TASK_ANCHOR_GREETING_HINTS = (
    "你好",
    "您好",
    "在吗",
    "hi",
    "hello",
    "早上好",
)
_TASK_ANCHOR_CONTINUATION_HINTS = (
    "继续",
    "补充",
    "接着",
    "延续",
    "上面",
    "刚才",
    "之前",
    "基于",
    "沿用",
    "细化",
)
_TASK_ANCHOR_TOPIC_SWITCH_HINTS = (
    "换个话题",
    "换个",
    "另一个",
    "重新来",
    "新话题",
    "改成",
    "不要这个",
    "不聊这个",
)


_TASK_ANCHOR_PLATFORM_ALIASES: Dict[str, tuple[str, ...]] = {
    "weixin": ("weixin", "微信", "企微", "公众号", "视频号", "小程序", "微信小店"),
    "douyin": ("douyin", "抖音", "抖店", "千川", "巨量"),
    "xiaohongshu": ("xiaohongshu", "小红书", "红薯"),
    "taobao": ("taobao", "淘宝"),
    "tmall": ("tmall", "天猫"),
    "jd": ("jd", "京东"),
    "pdd": ("pdd", "拼多多"),
    "kuaishou": ("kuaishou", "快手"),
    "amazon": ("amazon", "亚马逊"),
    "tiktok": ("tiktok", "tik tok"),
    "shopify": ("shopify",),
}


def _task_anchor_message_matches_platform(message_lowered: str, anchor_platform: str) -> bool:
    platform = str(anchor_platform or "").strip().lower()
    if not platform or platform == "general":
        return False
    candidates = _TASK_ANCHOR_PLATFORM_ALIASES.get(platform, ())
    if not candidates:
        candidates = (platform,)
    for token in candidates:
        normalized = str(token or "").strip().lower()
        if normalized and normalized in message_lowered:
            return True
    return False


def _normalize_task_anchor(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}

    role = str(raw.get("role") or "").strip().lower()
    action = str(raw.get("action") or "").strip().lower()
    platform = str(raw.get("platform") or "").strip().lower()
    domain_id = str(raw.get("domain_id") or "").strip()

    support_roles: List[str] = []
    for item in raw.get("support_roles") if isinstance(raw.get("support_roles"), list) else []:
        token = str(item or "").strip().lower()
        if not token or token in support_roles:
            continue
        support_roles.append(token)
        if len(support_roles) >= 3:
            break

    normalized = {
        "role": role,
        "action": action,
        "platform": platform,
        "domain_id": domain_id,
        "domain_confidence": float(raw.get("domain_confidence") or 0.0),
        "support_roles": support_roles,
        "task_should_clarify": bool(raw.get("task_should_clarify")),
    }

    if not any(
        [
            normalized["role"],
            normalized["action"],
            normalized["platform"],
            normalized["domain_id"],
            normalized["support_roles"],
        ]
    ):
        return {}

    return normalized


def _is_task_anchor_followup_candidate(
    message: str,
    *,
    intent_tier: int,
    intent_confidence: float,
    explicit_collab_requested: bool,
    anchor_platform: str = "",
) -> bool:
    text = str(message or "").strip()
    if not text:
        return False
    if explicit_collab_requested:
        return False

    lowered = text.lower()
    if any(hint in lowered for hint in _TASK_ANCHOR_GREETING_HINTS):
        return False
    if any(hint in lowered for hint in _TASK_ANCHOR_TOPIC_SWITCH_HINTS):
        return False

    if "\n" in text and len(text) > _TASK_ANCHOR_SHORT_MESSAGE_MAX_CHARS:
        return False

    has_continuation_hint = any(hint in lowered for hint in _TASK_ANCHOR_CONTINUATION_HINTS)
    matches_anchor_platform = _task_anchor_message_matches_platform(lowered, anchor_platform)

    if len(text) <= _TASK_ANCHOR_SHORT_MESSAGE_MAX_CHARS:
        # Short messages are frequently clarification replies, but high-confidence
        # new intents should not be force-anchored unless we have continuation signal.
        if has_continuation_hint or matches_anchor_platform:
            return True
        return float(intent_confidence or 0.0) < _TASK_ANCHOR_CONFIDENCE_OVERRIDE_THRESHOLD

    if len(text) > _TASK_ANCHOR_MID_MESSAGE_MAX_CHARS:
        return False

    if not has_continuation_hint:
        return False

    if int(intent_tier) == 0:
        return True

    return float(intent_confidence or 0.0) < _TASK_ANCHOR_CONTINUATION_CONFIDENCE_THRESHOLD

def _apply_task_anchor_guard(
    *,
    intent: Any,
    message: str,
    role_locked: bool,
    explicit_collab_requested: bool,
    task_anchor: Dict[str, Any],
    allow_engineering_agent: bool,
    intent_profile: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    guard: Dict[str, Any] = {
        "applied": False,
        "reason": "",
        "source": {},
        "overrides": [],
    }

    anchor = _normalize_task_anchor(task_anchor)
    if not anchor:
        return guard

    guard["source"] = {
        "role": anchor.get("role", ""),
        "action": anchor.get("action", ""),
        "platform": anchor.get("platform", ""),
        "domain_id": anchor.get("domain_id", ""),
        "task_should_clarify": bool(anchor.get("task_should_clarify")),
    }

    if role_locked:
        guard["reason"] = "role_locked"
        return guard

    if not bool(anchor.get("task_should_clarify")):
        guard["reason"] = "anchor_not_clarify"
        return guard

    profile = intent_profile if isinstance(intent_profile, dict) else {}
    if bool(profile):
        visual_attachment_turn = bool(
            int(profile.get("attachment_image_count") or 0) > 0
            and not bool(profile.get("attachment_data_table_focus"))
            and (
                bool(profile.get("visual_analysis_focus"))
                or bool(profile.get("attachment_visual_query_without_metric"))
                or bool(profile.get("visual_request_focus"))
                or int(profile.get("message_visual_intent_hits") or 0) > 0
            )
        )
        if visual_attachment_turn:
            # 当前轮若是“图片理解/解析”意图，禁止旧任务锚点覆盖当前意图，避免错派到数据岗。
            guard["reason"] = "visual_attachment_turn_skip_anchor"
            return guard

    if not _is_task_anchor_followup_candidate(
        message,
        intent_tier=int(getattr(intent, "tier", 1) or 1),
        intent_confidence=float(getattr(intent, "confidence", 0.0) or 0.0),
        explicit_collab_requested=explicit_collab_requested,
        anchor_platform=str(anchor.get('platform') or ''),
    ):
        guard["reason"] = "not_followup_candidate"
        return guard

    anchor_role = str(anchor.get("role") or "").strip().lower()
    if anchor_role and (allow_engineering_agent or anchor_role != "engineering") and anchor_role != str(getattr(intent, "primary_role", "") or "").strip().lower():
        guard["overrides"].append("primary_role")
        intent.primary_role = anchor_role

    anchor_action = str(anchor.get("action") or "").strip().lower()
    if anchor_action and anchor_action != str(getattr(intent, "action", "") or "").strip().lower():
        guard["overrides"].append("action")
        intent.action = anchor_action

    anchor_domain = str(anchor.get("domain_id") or "").strip()
    if anchor_domain and anchor_domain != str(getattr(intent, "domain_id", "") or "").strip():
        guard["overrides"].append("domain_id")
        intent.domain_id = anchor_domain
        intent.domain_confidence = max(float(getattr(intent, "domain_confidence", 0.0) or 0.0), float(anchor.get("domain_confidence") or 0.0), 0.68)

    anchor_platform = str(anchor.get("platform") or "").strip().lower()
    current_platform = str(getattr(intent, "platform", "") or "").strip().lower()
    if anchor_platform and anchor_platform != "general" and (not current_platform or current_platform == "general"):
        guard["overrides"].append("platform")
        intent.platform = anchor_platform

    existing_support = [
        str(x or "").strip().lower()
        for x in (getattr(intent, "support_roles", []) or [])
        if str(x or "").strip()
    ]
    merged_support = list(existing_support)
    for token in anchor.get("support_roles", []):
        role_token = str(token or "").strip().lower()
        if not role_token or role_token == str(getattr(intent, "primary_role", "") or "").strip().lower():
            continue
        if (not allow_engineering_agent) and role_token == "engineering":
            continue
        if role_token in merged_support:
            continue
        merged_support.append(role_token)
        if len(merged_support) >= 3:
            break

    if merged_support != existing_support:
        guard["overrides"].append("support_roles")
        intent.support_roles = merged_support[:3]

    raw_tier = getattr(intent, "tier", 1)
    try:
        current_tier = int(raw_tier) if raw_tier is not None else 1
    except Exception:
        current_tier = 1
    if current_tier == 0:
        guard["overrides"].append("tier")
        intent.tier = 1

    if float(getattr(intent, "confidence", 0.0) or 0.0) < _TASK_ANCHOR_CONFIDENCE_OVERRIDE_THRESHOLD:
        guard["overrides"].append("confidence")
        intent.confidence = max(float(getattr(intent, "confidence", 0.0) or 0.0), _TASK_ANCHOR_CONFIDENCE_OVERRIDE_THRESHOLD)

    if guard["overrides"]:
        guard["applied"] = True
        guard["reason"] = "clarify_followup_keep_task_anchor"
    else:
        guard["reason"] = "already_aligned"

    return guard


def _is_collaboration_reply_placeholder(reply: str) -> bool:
    text = str(reply or "").strip()
    if not text:
        return True
    if text.startswith("[") and text.endswith("]"):
        return True
    return False


def _build_collaboration_unified_plan(
    primary_reply: str,
    contributions: List[Dict[str, Any]],
) -> Dict[str, Any]:
    primary_points = _extract_outline_points(str(primary_reply or "").strip(), limit=3)

    role_points: List[Dict[str, str]] = []
    seen: set[str] = set()
    for item in contributions or []:
        role_name = str(item.get("name") or item.get("role") or "补位岗位").strip() or "补位岗位"
        role_reply = str(item.get("reply") or "").strip()
        if _is_collaboration_reply_placeholder(role_reply):
            continue
        points = _extract_outline_points(role_reply, limit=2)
        if not points:
            continue
        picked = points[0]
        key = re.sub(r"\s+", "", f"{role_name}:{picked}").lower()
        if key in seen:
            continue
        seen.add(key)
        role_points.append({"name": role_name, "point": picked})
        if len(role_points) >= 4:
            break

    if not primary_points and not role_points:
        return {
            "markdown": "",
            "primary_points": [],
            "role_points": [],
        }

    lines: List[str] = [
        "### 多Agent统一方案",
        "> 将主回复与多岗位补位建议融合为一条可执行路径。",
    ]

    if primary_points:
        lines.append("")
        lines.append("**统一步骤**")
        for idx, point in enumerate(primary_points, 1):
            lines.append(f"{idx}. {point}")

    if role_points:
        lines.append("")
        lines.append("**岗位补位要点**")
        for item in role_points:
            lines.append(f"- {item['name']}：{item['point']}")

    lines.append("")
    lines.append("**执行顺序建议**")
    if primary_points:
        lines.append(f"1. 先落地统一步骤第 1 项：{primary_points[0]}")
        if len(primary_points) > 1:
            lines.append(f"2. 再推进统一步骤第 2 项：{primary_points[1]}")
        else:
            lines.append("2. 补齐验证指标与里程碑，再进入放量阶段。")
    else:
        lines.append("1. 先明确目标、边界与评估口径。")
        lines.append("2. 再把岗位补位建议整合为分工执行表。")

    if role_points:
        role_names = "、".join(item.get("name", "") for item in role_points[:3])
        lines.append(f"3. 按 {role_names} 的补位建议并行执行并每周复盘。")
    else:
        lines.append("3. 按单路径执行并滚动复盘优化。")

    return {
        "markdown": "\n".join(lines).strip(),
        "primary_points": primary_points,
        "role_points": role_points,
    }
_CHAT_ATTACHMENT_MAX_ITEMS = 5
_CHAT_ATTACHMENT_MAX_ITEM_CHARS = 1800
_CHAT_ATTACHMENT_MAX_TOTAL_CHARS = 5200
_CHAT_ATTACHMENT_STRUCTURED_MAX_ITEM_CHARS = 12000
_CHAT_ATTACHMENT_STRUCTURED_MAX_TOTAL_CHARS = 48000
_CHAT_ATTACHMENT_ANCHOR_PATTERN = re.compile('\\[附件(\\d+)\\]')
_CHAT_ATTACHMENT_TAG_PATTERN = re.compile(r"\[(ATT-[A-Z0-9]{4,16})\]")

_CHAT_ATTACHMENT_TABLE_MAX_SCAN_LINES = 520
_CHAT_ATTACHMENT_TABLE_DETECTION_SCAN_LINES = 140
_CHAT_ATTACHMENT_TABLE_HEADER_SCAN_LINES = 96
_CHAT_ATTACHMENT_TABLE_HEADER_LOOKAHEAD_LINES = 84
_CHAT_ATTACHMENT_TABLE_MAX_FIELDS = 8
_CHAT_ATTACHMENT_TABLE_MAX_SAMPLES = 2
_CHAT_ATTACHMENT_TABLE_ENTITY_MAX_ROWS = 120
_CHAT_ATTACHMENT_TABLE_ENTITY_FULL_SCAN_MAX_ROWS = _CHAT_ATTACHMENT_TABLE_MAX_SCAN_LINES
_CHAT_ATTACHMENT_TABLE_ENTITY_ROW_RECORD_LIMIT = _CHAT_ATTACHMENT_TABLE_MAX_SCAN_LINES
_CHAT_ATTACHMENT_TABLE_ENTITY_MAX_VALUES_PER_FIELD = 180
_CHAT_ATTACHMENT_TABLE_ENTITY_MAX_PREVIEW = 3
_CHAT_ATTACHMENT_TABLE_ENTITY_MIN_VALUE_LEN = 2
_CHAT_ATTACHMENT_TABLE_HINT_SUFFIXES = (".csv", ".tsv", ".xlsx", ".xls")
_CHAT_ATTACHMENT_TABLE_HINT_TOKENS = ("csv", "excel", "spreadsheet", "sheet", "tabular", "table", "openpyxl")
_CHAT_ATTACHMENT_TABLE_WHITESPACE_DELIMITER = "__ws_align__"
_CHAT_ATTACHMENT_TABLE_DELIMITERS = [",", "\t", "|", ";", _CHAT_ATTACHMENT_TABLE_WHITESPACE_DELIMITER]
_CHAT_ATTACHMENT_TABLE_DELIMITER_ALIASES: Dict[str, Tuple[str, ...]] = {
    ",": (",", "，"),
    "\t": ("\t",),
    "|": ("|", "｜", "¦", "│", "┃", "┆"),
    ";": (";", "；"),
}
_CHAT_ATTACHMENT_TABLE_WHITESPACE_PATTERN = re.compile(r"\S[ \t\u3000]{2,}\S")
_CHAT_ATTACHMENT_TABLE_WHITESPACE_SPLIT_PATTERN = re.compile(r"[ \t\u3000]{2,}")
_CHAT_ATTACHMENT_LAYOUT_TABLE_MARKER = '[结构化表格候选]'
_CHAT_ATTACHMENT_LAYOUT_CANDIDATE_BLANK_GAP_TOLERANCE = 1
_CHAT_ATTACHMENT_LAYOUT_GRID_BORDER_JOINT_CHARS = "+鈹尖敩鈹粹敜鈹溾攼鈹屸敇鈹斺晪鈺︹暕鈺ｂ暊鈺?"
_CHAT_ATTACHMENT_LAYOUT_GRID_BORDER_LINE_CHARS = set("-=鈹€鈹佲晲_~路 ") | set(_CHAT_ATTACHMENT_LAYOUT_GRID_BORDER_JOINT_CHARS)
_CHAT_ATTACHMENT_LAYOUT_BLOCK_DISTANCE_HINT = 160
_CHAT_ATTACHMENT_LAYOUT_BLOCK_MIN_OVERLAP_RATIO = 0.2
_CHAT_ATTACHMENT_LAYOUT_BLOCK_ENTITY_PREVIEW_LIMIT = 24
_CHAT_ATTACHMENT_LAYOUT_BLOCK_MIN_QUALITY = 0.18
_CHAT_ATTACHMENT_LAYOUT_BLOCK_QUALITY_GAP_REJECT = 0.28
_CHAT_ATTACHMENT_LAYOUT_SIGNATURE_SIMILARITY_KEEP = 0.55

_CHAT_ATTACHMENT_KEY_HINT_GROUPS: Dict[str, Tuple[str, ...]] = {
    "sku": ("sku", "spu", "itemid", "商品id", "宝贝id", "货号", "款号"),
    "order": ("orderid", "订单号", "订单id", "子订单号"),
    "user": ("userid", "buyerid", "memberid", "用户id", "买家id"),
    "date": ("date", "day", "日期", "时间"),
    "shop": ("shop", "store", "店铺", "渠道", "platform"),
}

_CHAT_ATTACHMENT_FIELD_GROUP_LABELS: Dict[str, str] = {
    "sku": "SKU/商品ID",
    "order": "订单ID",
    "user": "用户ID",
    "date": "日期",
    "shop": "店铺/渠道",
}

_CHAT_ATTACHMENT_FIELD_GROUP_WEIGHTS: Dict[str, float] = {
    "sku": 1.0,
    "order": 0.95,
    "user": 0.9,
    "date": 0.8,
    "shop": 0.7,
}

_CHAT_ATTACHMENT_FIELD_ALIAS_FILE_ENV = "ATTACHMENT_FIELD_ALIAS_FILE"
_CHAT_ATTACHMENT_FIELD_ALIAS_DEFAULT_FILE = "attachment_field_aliases.json"
_CHAT_ATTACHMENT_FIELD_ALIAS_DISABLE_VALUES = {"", "0", "false", "off", "none", "disable", "disabled"}
_CHAT_ATTACHMENT_LATIN_SUFFIX_HINTS = {
    "id",
    "ids",
    "no",
    "num",
    "number",
    "code",
    "key",
    "uid",
    "uuid",
    "sn",
    "dt",
    "date",
    "time",
}
_CHAT_ATTACHMENT_CJK_PATTERN = re.compile(r"[\u4e00-\u9fff]")
_CHAT_ATTACHMENT_FIELD_SEMANTIC_CACHE: Dict[str, Any] = {
    "signature": None,
    "resources": None,
}


def _normalize_attachment_field_key(raw: str) -> str:
    text = str(raw or "").strip().lower()
    if not text:
        return ""
    compact = re.sub('[\\s_\\-:?|]+', "", text)
    compact = re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", compact)
    return compact[:40]


def _normalize_attachment_cell_value(raw: str) -> str:
    text = str(raw or "").strip().lower()
    if not text:
        return ""

    compact = re.sub(r"[\s\t\r\n]+", "", text)
    compact = re.sub('[,??\\|\\[\\]\\(\\){}<>"\\\'`~!@#$%^&*+=?"]+', "", compact)
    compact = re.sub(r"[^0-9a-z\u4e00-\u9fff\-_.]", "", compact)
    return compact[:64]


_CHAT_ATTACHMENT_KEY_HINTS_NORMALIZED: Dict[str, Tuple[str, ...]] = {
    group: tuple(
        sorted(
            {
                token
                for token in (_normalize_attachment_field_key(x) for x in values)
                if token
            },
            key=lambda item: (-len(item), item),
        )
    )
    for group, values in _CHAT_ATTACHMENT_KEY_HINT_GROUPS.items()
}


def _contains_cjk_text(token: str) -> bool:
    return bool(_CHAT_ATTACHMENT_CJK_PATTERN.search(str(token or "")))


def _attachment_field_hint_match(token: str, hint: str) -> bool:
    left = str(token or "").strip()
    right = str(hint or "").strip()
    if not left or not right:
        return False
    if left == right:
        return True

    if _contains_cjk_text(left) or _contains_cjk_text(right):
        return (right in left) or (left in right)

    if len(left) <= 2 or len(right) <= 2:
        return False

    if left.startswith(right):
        suffix = left[len(right):]
        if suffix.isdigit() or suffix in _CHAT_ATTACHMENT_LATIN_SUFFIX_HINTS:
            return True

    if right.startswith(left):
        suffix = right[len(left):]
        if suffix.isdigit() or suffix in _CHAT_ATTACHMENT_LATIN_SUFFIX_HINTS:
            return True

    return False


def _resolve_attachment_alias_dictionary_path() -> Optional[Path]:
    raw = str(
        os.getenv(_CHAT_ATTACHMENT_FIELD_ALIAS_FILE_ENV, _CHAT_ATTACHMENT_FIELD_ALIAS_DEFAULT_FILE) or ""
    ).strip()
    if raw.lower() in _CHAT_ATTACHMENT_FIELD_ALIAS_DISABLE_VALUES:
        return None

    path = Path(raw)
    if not path.is_absolute():
        path = (Path(__file__).resolve().parents[2] / "config" / raw).resolve()
    return path


def _attachment_alias_dict_signature(path: Optional[Path]) -> Tuple[Any, ...]:
    if path is None:
        return ("disabled",)

    try:
        stat = path.stat()
        return (str(path), int(stat.st_mtime_ns), int(stat.st_size))
    except FileNotFoundError:
        return (str(path), "missing")
    except Exception:
        return (str(path), "error")


def _safe_attachment_group_weight(raw: Any, fallback: float) -> float:
    try:
        value = float(raw)
    except Exception:
        return float(fallback)
    if value <= 0:
        return float(fallback)
    return round(min(2.0, max(0.1, value)), 3)


def _iter_attachment_semantic_groups(payload: Dict[str, Any]) -> List[Tuple[str, Any]]:
    if not isinstance(payload, dict):
        return []

    groups_raw = payload.get("groups")
    if isinstance(groups_raw, dict):
        return [(str(group), value) for group, value in groups_raw.items()]

    reserved = {"version", "updated_at", "description", "meta", "metadata"}
    items: List[Tuple[str, Any]] = []
    for key, value in payload.items():
        key_text = str(key or "").strip()
        if not key_text or key_text.lower() in reserved:
            continue
        items.append((key_text, value))
    return items


def _load_attachment_semantic_resources() -> Dict[str, Any]:
    dictionary_path = _resolve_attachment_alias_dictionary_path()
    signature = _attachment_alias_dict_signature(dictionary_path)

    cached_signature = _CHAT_ATTACHMENT_FIELD_SEMANTIC_CACHE.get("signature")
    cached_resources = _CHAT_ATTACHMENT_FIELD_SEMANTIC_CACHE.get("resources")
    if cached_resources and signature == cached_signature:
        return cached_resources

    hints_by_group: Dict[str, set[str]] = {
        str(group): {str(token) for token in hints if str(token).strip()}
        for group, hints in _CHAT_ATTACHMENT_KEY_HINTS_NORMALIZED.items()
    }
    labels: Dict[str, str] = dict(_CHAT_ATTACHMENT_FIELD_GROUP_LABELS)
    weights: Dict[str, float] = {
        str(group): float(value)
        for group, value in _CHAT_ATTACHMENT_FIELD_GROUP_WEIGHTS.items()
    }

    default_pairs = {
        (group, token)
        for group, hints in _CHAT_ATTACHMENT_KEY_HINTS_NORMALIZED.items()
        for token in hints
    }
    custom_alias_pairs: set[Tuple[str, str]] = set()
    custom_group_count = 0
    dictionary_source = ""

    if dictionary_path and dictionary_path.exists():
        dictionary_source = dictionary_path.name
        try:
            payload = json.loads(dictionary_path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to load attachment field alias dictionary %s: %s", dictionary_path, exc)
            payload = {}

        if isinstance(payload, dict):
            for raw_group, config in _iter_attachment_semantic_groups(payload):
                group_key = _normalize_attachment_field_key(raw_group)
                if not group_key:
                    continue

                is_new_group = group_key not in hints_by_group
                if is_new_group:
                    hints_by_group[group_key] = set()
                    custom_group_count += 1

                aliases_raw: List[Any] = [raw_group, group_key]
                label_value = ""
                weight_value: Any = None

                if isinstance(config, dict):
                    for alias_key in ("aliases", "tokens", "synonyms", "keys"):
                        raw_aliases = config.get(alias_key)
                        if isinstance(raw_aliases, list):
                            aliases_raw.extend(raw_aliases)
                        elif isinstance(raw_aliases, str):
                            aliases_raw.append(raw_aliases)
                    label_value = str(config.get("label") or config.get("display_name") or "").strip()
                    weight_value = config.get("weight")
                elif isinstance(config, list):
                    aliases_raw.extend(config)
                elif isinstance(config, str):
                    aliases_raw.append(config)

                for alias in aliases_raw:
                    token = _normalize_attachment_field_key(alias)
                    if not token:
                        continue
                    if token not in hints_by_group[group_key]:
                        hints_by_group[group_key].add(token)
                    pair = (group_key, token)
                    if pair not in default_pairs:
                        custom_alias_pairs.add(pair)

                if label_value:
                    labels[group_key] = label_value
                elif group_key not in labels:
                    labels[group_key] = str(raw_group or group_key).strip() or group_key

                fallback_weight = float(weights.get(group_key, 0.6))
                if weight_value is not None:
                    weights[group_key] = _safe_attachment_group_weight(weight_value, fallback_weight)
                elif group_key not in weights:
                    weights[group_key] = fallback_weight

    hints_normalized = {
        group: tuple(sorted(tokens, key=lambda item: (-len(item), item)))
        for group, tokens in hints_by_group.items()
        if tokens
    }

    resources = {
        "hints_normalized": hints_normalized,
        "labels": labels,
        "weights": weights,
        "dictionary_source": dictionary_source,
        "custom_alias_count": len(custom_alias_pairs),
        "custom_group_count": custom_group_count,
    }
    _CHAT_ATTACHMENT_FIELD_SEMANTIC_CACHE["signature"] = signature
    _CHAT_ATTACHMENT_FIELD_SEMANTIC_CACHE["resources"] = resources
    return resources


def _match_attachment_field_group(key: str, *, semantic_resources: Optional[Dict[str, Any]] = None) -> str:
    token = _normalize_attachment_field_key(key)
    if not token:
        return ''

    resources = semantic_resources if isinstance(semantic_resources, dict) else _load_attachment_semantic_resources()
    hints_by_group = resources.get("hints_normalized") if isinstance(resources.get("hints_normalized"), dict) else {}

    for group, hints in hints_by_group.items():
        for hint in hints:
            if _attachment_field_hint_match(token, str(hint)):
                return str(group)
    return ''


def _has_tabular_hint(item: Dict[str, Any]) -> bool:
    filename = str(item.get("filename") or "").strip().lower()
    if any(filename.endswith(suffix) for suffix in _CHAT_ATTACHMENT_TABLE_HINT_SUFFIXES):
        return True

    parser = str(item.get("parser") or "").strip().lower()
    file_type = str(item.get("file_type") or "").strip().lower()
    combined = f"{parser} {file_type}"
    return any(token in combined for token in _CHAT_ATTACHMENT_TABLE_HINT_TOKENS)


def _table_delimiter_tokens(delimiter: str) -> Tuple[str, ...]:
    token = str(delimiter or "")
    if not token:
        return tuple()

    aliases = _CHAT_ATTACHMENT_TABLE_DELIMITER_ALIASES.get(token)
    if isinstance(aliases, tuple) and aliases:
        deduped: List[str] = []
        for item in aliases:
            alias = str(item or "")
            if alias and alias not in deduped:
                deduped.append(alias)
        if deduped:
            return tuple(deduped)

    return (token,)


def _line_contains_table_delimiter(line: str, delimiter: str) -> bool:
    text = str(line or "")
    if not text:
        return False
    if delimiter == _CHAT_ATTACHMENT_TABLE_WHITESPACE_DELIMITER:
        normalized = text.replace("\u3000", " ")
        return bool(_CHAT_ATTACHMENT_TABLE_WHITESPACE_PATTERN.search(normalized))
    return any(token in text for token in _table_delimiter_tokens(delimiter))


def _split_table_cells(line: str, delimiter: str) -> List[str]:
    text = str(line or "")
    if delimiter == _CHAT_ATTACHMENT_TABLE_WHITESPACE_DELIMITER:
        normalized = text.replace("\u3000", " ").strip()
        if not normalized:
            return []
        return [str(cell or "").strip() for cell in _CHAT_ATTACHMENT_TABLE_WHITESPACE_SPLIT_PATTERN.split(normalized)]

    tokens = _table_delimiter_tokens(delimiter)
    if not tokens:
        stripped = text.strip()
        return [stripped] if stripped else []

    if len(tokens) == 1:
        return [str(cell or "").strip() for cell in text.split(tokens[0])]

    split_pattern = "|".join(re.escape(token) for token in tokens)
    return [str(cell or "").strip() for cell in re.split(split_pattern, text)]


def _guess_table_delimiter(lines: List[str]) -> str:
    if len(lines) < 2:
        return ""

    best_delimiter = ""
    best_score = 0
    for delimiter in _CHAT_ATTACHMENT_TABLE_DELIMITERS:
        counts: List[int] = []
        for line in lines[:12]:
            if not _line_contains_table_delimiter(line, delimiter):
                continue
            cells = [x for x in _split_table_cells(line, delimiter) if x]
            col_count = len(cells)
            if 2 <= col_count <= 16:
                counts.append(col_count)

        if len(counts) < 2:
            continue

        mode_count, mode_hits = Counter(counts).most_common(1)[0]
        stable = sum(1 for c in counts if abs(c - mode_count) <= 1)
        score = stable * mode_count + mode_hits
        if score > best_score:
            best_score = score
            best_delimiter = delimiter

    return best_delimiter if best_score >= 6 else ""


def _is_layout_grid_border_line(line: str) -> bool:
    text = str(line or "").strip()
    if len(text) < 5:
        return False

    joint_count = sum(1 for ch in text if ch in _CHAT_ATTACHMENT_LAYOUT_GRID_BORDER_JOINT_CHARS)
    if joint_count < 2:
        return False

    border_like = sum(1 for ch in text if ch in _CHAT_ATTACHMENT_LAYOUT_GRID_BORDER_LINE_CHARS)
    if border_like < max(4, int(round(len(text) * 0.55))):
        return False

    text_like = 0
    for ch in text:
        if ch.isalnum() or bool(_CHAT_ATTACHMENT_CJK_PATTERN.search(ch)):
            text_like += 1
            if text_like > 2:
                return False
    return True


def _infer_layout_grid_column_count(lines: List[str]) -> int:
    counts: List[int] = []
    for raw_line in lines:
        line = str(raw_line or "").strip()
        if not _is_layout_grid_border_line(line):
            continue

        joints = [idx for idx, ch in enumerate(line) if ch in _CHAT_ATTACHMENT_LAYOUT_GRID_BORDER_JOINT_CHARS]
        if len(joints) < 3:
            continue
        column_count = len(joints) - 1
        if 2 <= column_count <= 12:
            counts.append(column_count)

    if not counts:
        return 0
    return int(Counter(counts).most_common(1)[0][0])


def _normalize_layout_candidate_grid_lines(lines: List[str]) -> Optional[Dict[str, Any]]:
    expected_columns = _infer_layout_grid_column_count(lines)
    if expected_columns < 2:
        return None

    normalized_lines: List[str] = []
    source_indexes: List[int] = []

    for idx, raw_line in enumerate(lines):
        line = str(raw_line or "").strip()
        if not line:
            continue
        if _is_layout_grid_border_line(line):
            continue

        tokens = [str(token or "").strip() for token in re.split('[ \t〢]+', line) if str(token or "").strip()]
        if len(tokens) < expected_columns:
            continue
        if len(tokens) > expected_columns:
            head = tokens[: expected_columns - 1]
            tail = " ".join(tokens[expected_columns - 1 :]).strip()
            tokens = head + [tail]

        if len(tokens) != expected_columns:
            continue

        normalized_lines.append("	".join(tokens))
        source_indexes.append(idx)

        if len(normalized_lines) >= _CHAT_ATTACHMENT_TABLE_MAX_SCAN_LINES:
            break

    if len(normalized_lines) < 2:
        return None

    return {
        "lines": normalized_lines,
        "source_indexes": source_indexes,
        "column_count": expected_columns,
    }


def _layout_block_header_signature(lines: List[str], delimiter: str) -> Tuple[str, str, int]:
    best_line = ""
    best_key_signature = ""
    best_columns = 0
    best_score = -1.0

    for candidate_line in lines[: min(24, len(lines))]:
        if not _line_contains_table_delimiter(candidate_line, delimiter):
            continue

        cells = [x for x in _split_table_cells(candidate_line, delimiter) if x]
        if len(cells) < 2:
            continue

        headers, header_keys = _build_table_header_profile(candidate_line, delimiter)
        if len(header_keys) < 2:
            continue

        text_like = sum(1 for cell in headers if re.search(r"[A-Za-z\u4e00-\u9fff]", str(cell or "")))
        numeric_like = sum(1 for cell in headers if _is_numeric_like_table_cell(cell))
        score = float(text_like) * 2.0 + float(len(header_keys)) * 0.5 - float(numeric_like) * 1.2
        if _is_probable_table_header_cells(headers):
            score += 3.0

        if score <= best_score:
            continue

        best_score = score
        best_line = candidate_line
        best_key_signature = "|".join(header_keys)
        best_columns = len(header_keys)

    return best_line, best_key_signature, best_columns


def _estimate_layout_block_quality(
    lines: List[str],
    delimiter: str,
    *,
    header_line: str,
    expected_columns: int,
) -> Dict[str, float]:
    if not lines:
        return {
            "quality_score": 0.0,
            "valid_row_density": 0.0,
            "column_stability": 0.0,
            "expected_alignment": 0.0,
            "data_row_count": 0.0,
        }

    data_rows = 0
    valid_rows = 0
    col_counts: List[int] = []
    aligned_rows = 0

    header_seen = False
    first_row_skipped = False

    for raw_line in lines[: min(72, len(lines))]:
        line = str(raw_line or "").strip()
        if not line:
            continue
        if not _line_contains_table_delimiter(line, delimiter):
            continue

        cells = [x for x in _split_table_cells(line, delimiter) if x]
        if len(cells) < 2:
            continue

        if header_line and not header_seen:
            if line == header_line:
                header_seen = True
            continue

        if not header_line and not first_row_skipped:
            first_row_skipped = True
            continue

        data_rows += 1
        col_count = len(cells)
        col_counts.append(col_count)

        usable = cells[: min(10, max(2, col_count))]
        non_empty = sum(1 for val in usable if str(val or "").strip())
        if non_empty >= max(2, int(round(len(usable) * 0.5))):
            valid_rows += 1

        if expected_columns >= 2 and abs(col_count - expected_columns) <= 1:
            aligned_rows += 1

    if data_rows <= 0:
        return {
            "quality_score": 0.0,
            "valid_row_density": 0.0,
            "column_stability": 0.0,
            "expected_alignment": 0.0,
            "data_row_count": 0.0,
        }

    valid_row_density = float(valid_rows) / float(data_rows)
    mode_count = Counter(col_counts).most_common(1)[0][0] if col_counts else 0
    stable_rows = sum(1 for c in col_counts if abs(c - mode_count) <= 1)
    column_stability = float(stable_rows) / float(max(1, len(col_counts)))

    if expected_columns >= 2:
        expected_alignment = float(aligned_rows) / float(max(1, data_rows))
    else:
        expected_alignment = column_stability

    coverage_score = min(1.0, float(data_rows) / 8.0)
    header_bonus = 0.08 if header_line else 0.0

    quality = (
        valid_row_density * 0.30
        + column_stability * 0.25
        + expected_alignment * 0.35
        + coverage_score * 0.10
        + header_bonus
    )

    if expected_alignment < 0.6:
        quality -= (0.6 - expected_alignment) * 0.7

    quality_score = round(max(0.0, min(1.0, quality)), 4)

    return {
        "quality_score": quality_score,
        "valid_row_density": round(valid_row_density, 4),
        "column_stability": round(column_stability, 4),
        "expected_alignment": round(expected_alignment, 4),
        "data_row_count": float(data_rows),
    }


def _extract_layout_block_entity_keys(lines: List[str], delimiter: str, header_line: str) -> set[str]:
    if not lines:
        return set()

    keys: set[str] = set()
    header_seen = False
    first_row_skipped = False
    max_scan = min(len(lines), _CHAT_ATTACHMENT_LAYOUT_BLOCK_ENTITY_PREVIEW_LIMIT + 10)

    for line in lines[:max_scan]:
        row = str(line or "").strip()
        if not row:
            continue

        if header_line and not header_seen:
            if row == header_line:
                header_seen = True
            continue

        if not header_line and not first_row_skipped:
            first_row_skipped = True
            continue

        if not _line_contains_table_delimiter(row, delimiter):
            continue

        cells = [x for x in _split_table_cells(row, delimiter) if x]
        if len(cells) < 2:
            continue

        key = _normalize_attachment_cell_value(cells[0])
        if len(key) < _CHAT_ATTACHMENT_TABLE_ENTITY_MIN_VALUE_LEN:
            continue

        keys.add(key)
        if len(keys) >= _CHAT_ATTACHMENT_LAYOUT_BLOCK_ENTITY_PREVIEW_LIMIT:
            break

    return keys


def _split_layout_header_signature(signature: str) -> List[str]:
    raw = str(signature or "").strip()
    if not raw:
        return []
    return [str(token or "").strip() for token in raw.split("|") if str(token or "").strip()]


def _layout_header_signature_similarity(anchor_signature: str, block_signature: str) -> float:
    left_keys = _split_layout_header_signature(anchor_signature)
    right_keys = _split_layout_header_signature(block_signature)
    if not left_keys or not right_keys:
        return 0.0

    if anchor_signature == block_signature:
        return 1.0

    left_set = set(left_keys)
    right_set = set(right_keys)
    overlap = float(len(left_set & right_set)) / float(max(1, min(len(left_set), len(right_set))))

    prefix = min(len(left_keys), len(right_keys))
    ordered_hits = 0
    for idx in range(prefix):
        if left_keys[idx] == right_keys[idx]:
            ordered_hits += 1
    ordered_ratio = float(ordered_hits) / float(max(1, prefix))

    left_groups = {g for g in (_match_attachment_field_group(k) for k in left_keys) if g}
    right_groups = {g for g in (_match_attachment_field_group(k) for k in right_keys) if g}
    group_overlap = 0.0
    if left_groups and right_groups:
        group_overlap = float(len(left_groups & right_groups)) / float(max(1, min(len(left_groups), len(right_groups))))

    first_left = left_keys[0]
    first_right = right_keys[0]
    first_group_left = _match_attachment_field_group(first_left)
    first_group_right = _match_attachment_field_group(first_right)
    first_key_match = _attachment_field_hint_match(first_left, first_right)
    if not first_key_match and first_group_left and first_group_left == first_group_right:
        first_key_match = True

    similarity = max(overlap, ordered_ratio * 0.88, group_overlap * 0.92)
    if first_key_match:
        if abs(len(left_keys) - len(right_keys)) <= 1:
            similarity = max(similarity, 0.58)
        else:
            similarity = max(similarity, 0.5)

    if abs(len(left_keys) - len(right_keys)) >= 3:
        similarity -= 0.08

    return round(max(0.0, min(1.0, similarity)), 4)


def _layout_block_continuity_score(
    *,
    signature_similarity: float,
    has_both_signatures: bool,
    anchor_columns: int,
    anchor_quality: float,
    block_columns: int,
    block_quality: float,
    distance: int,
    overlap_ratio: float,
) -> float:
    score = 0.0

    similarity = max(0.0, min(1.0, float(signature_similarity or 0.0)))
    if has_both_signatures:
        score += similarity * 0.52 - 0.14
        if similarity < _CHAT_ATTACHMENT_LAYOUT_SIGNATURE_SIMILARITY_KEEP:
            score -= 0.08
    else:
        score += 0.08 + similarity * 0.12

    if anchor_columns >= 2 and block_columns >= 2:
        diff = abs(anchor_columns - block_columns)
        if diff == 0:
            score += 0.22
        elif diff == 1:
            score += 0.14
        elif diff >= 3:
            score -= 0.12

    quality_floor = min(max(0.0, anchor_quality), max(0.0, block_quality))
    score += min(0.2, quality_floor * 0.24)

    if distance <= _CHAT_ATTACHMENT_LAYOUT_BLOCK_DISTANCE_HINT:
        score += 0.14
    elif distance <= (_CHAT_ATTACHMENT_LAYOUT_BLOCK_DISTANCE_HINT * 2):
        score += 0.08
    elif distance <= (_CHAT_ATTACHMENT_LAYOUT_BLOCK_DISTANCE_HINT * 4):
        score += 0.03

    score += min(0.24, max(0.0, overlap_ratio) * 0.55)

    return round(max(0.0, min(1.0, score)), 4)


def _refine_layout_selected_blocks(selected_blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if len(selected_blocks) <= 1:
        return selected_blocks

    ordered_blocks = sorted(selected_blocks, key=lambda x: int(x.get("start_index") or 0))

    anchor = max(
        ordered_blocks,
        key=lambda x: (
            float(x.get("quality_score") or 0.0),
            float(x.get("score") or 0.0),
            int(x.get("row_hits") or 0),
            len(x.get("lines") or []),
            -int(x.get("start_index") or 0),
        ),
    )

    anchor_signature = str(anchor.get("header_key_signature") or "").strip()
    anchor_columns = int(anchor.get("header_column_count") or 0)
    if anchor_columns <= 0:
        anchor_columns = int(anchor.get("column_count") or 0)

    anchor_start = int(anchor.get("start_index") or 0)
    anchor_keys = set(anchor.get("_entity_keys") or set())
    anchor_quality = float(anchor.get("quality_score") or 0.0)

    refined: List[Dict[str, Any]] = [anchor]
    refined_starts: List[int] = [anchor_start]
    refined_keys: set[str] = set(anchor_keys)

    for block in ordered_blocks:
        if block is anchor:
            continue

        block_signature = str(block.get("header_key_signature") or "").strip()
        has_both_signatures = bool(anchor_signature and block_signature)
        if has_both_signatures:
            signature_similarity = _layout_header_signature_similarity(anchor_signature, block_signature)
            if signature_similarity < _CHAT_ATTACHMENT_LAYOUT_SIGNATURE_SIMILARITY_KEEP:
                continue
        elif not anchor_signature and not block_signature:
            signature_similarity = 0.5
        else:
            signature_similarity = 0.42

        block_columns = int(block.get("header_column_count") or 0)
        if block_columns <= 0:
            block_columns = int(block.get("column_count") or 0)
        if anchor_columns >= 2 and block_columns >= 2 and abs(block_columns - anchor_columns) > 1:
            continue

        block_start = int(block.get("start_index") or 0)
        nearest_distance = min(abs(block_start - start) for start in refined_starts) if refined_starts else abs(block_start - anchor_start)

        block_keys = set(block.get("_entity_keys") or set())
        overlap_ratio = 0.0
        if refined_keys and block_keys:
            overlap_count = len(refined_keys & block_keys)
            overlap_base = max(1, min(len(refined_keys), len(block_keys)))
            overlap_ratio = float(overlap_count) / float(overlap_base)

        block_quality = float(block.get("quality_score") or 0.0)
        continuity_score = _layout_block_continuity_score(
            signature_similarity=signature_similarity,
            has_both_signatures=has_both_signatures,
            anchor_columns=anchor_columns,
            anchor_quality=anchor_quality,
            block_columns=block_columns,
            block_quality=block_quality,
            distance=nearest_distance,
            overlap_ratio=overlap_ratio,
        )

        keep = False
        if overlap_ratio >= _CHAT_ATTACHMENT_LAYOUT_BLOCK_MIN_OVERLAP_RATIO:
            keep = True
        elif nearest_distance <= _CHAT_ATTACHMENT_LAYOUT_BLOCK_DISTANCE_HINT:
            keep = True
        elif continuity_score >= 0.66 and len(refined) == 1:
            keep = True
        elif (not refined_keys or not block_keys) and nearest_distance <= (_CHAT_ATTACHMENT_LAYOUT_BLOCK_DISTANCE_HINT * 2):
            keep = True

        quality_gap = max(0.0, anchor_quality - block_quality)
        if (
            keep
            and quality_gap >= _CHAT_ATTACHMENT_LAYOUT_BLOCK_QUALITY_GAP_REJECT
            and overlap_ratio < _CHAT_ATTACHMENT_LAYOUT_BLOCK_MIN_OVERLAP_RATIO
            and nearest_distance >= max(6, int(_CHAT_ATTACHMENT_LAYOUT_BLOCK_DISTANCE_HINT * 0.15))
        ):
            if not (continuity_score >= 0.78 and len(refined) == 1):
                keep = False

        if keep:
            refined.append(block)
            refined_starts.append(block_start)
            if block_keys:
                refined_keys.update(block_keys)

    if len(refined) <= 1:
        sorted_by_score = sorted(
            ordered_blocks,
            key=lambda x: (
                -float(x.get("quality_score") or 0.0),
                -float(x.get("score") or 0.0),
                -int(x.get("row_hits") or 0),
                int(x.get("start_index") or 0),
            ),
        )
        for block in sorted_by_score:
            if block is anchor:
                continue
            signature = str(block.get("header_key_signature") or "").strip()
            if anchor_signature and signature:
                if _layout_header_signature_similarity(anchor_signature, signature) < _CHAT_ATTACHMENT_LAYOUT_SIGNATURE_SIMILARITY_KEEP:
                    continue
            refined.append(block)
            break

    refined.sort(key=lambda x: int(x.get("start_index") or 0))
    return refined


def _prepare_layout_block_lines_for_merge(
    lines: List[str],
    delimiter: str,
    *,
    header_line: str,
    expected_columns: int,
) -> List[str]:
    if not lines:
        return []

    cleaned: List[str] = []
    for raw_line in lines:
        line = str(raw_line or "").strip()
        if not line:
            continue
        if not _line_contains_table_delimiter(line, delimiter):
            continue

        cells = [x for x in _split_table_cells(line, delimiter) if x]
        if len(cells) < 2:
            continue
        if expected_columns >= 2 and abs(len(cells) - expected_columns) > 1:
            continue

        cleaned.append(line)

    if len(cleaned) < 2:
        fallback = [str(x).strip() for x in lines if str(x or "").strip()]
        return fallback[:_CHAT_ATTACHMENT_TABLE_MAX_SCAN_LINES]

    if header_line:
        header = str(header_line).strip()
        if header and header in cleaned:
            cleaned = cleaned[cleaned.index(header) :]

        deduped: List[str] = []
        header_seen = False
        for line in cleaned:
            if header and line == header:
                if header_seen:
                    continue
                header_seen = True
            deduped.append(line)
        cleaned = deduped

    return cleaned[:_CHAT_ATTACHMENT_TABLE_MAX_SCAN_LINES]


def _extract_layout_candidate_table_lines(lines_all: List[str]) -> Optional[Dict[str, Any]]:
    if not lines_all:
        return None

    blocks: List[Dict[str, Any]] = []

    for idx, raw_line in enumerate(lines_all):
        line = str(raw_line or "").strip()
        if _CHAT_ATTACHMENT_LAYOUT_TABLE_MARKER not in line:
            continue

        segment: List[str] = []
        blank_gap_count = 0
        for probe_line in lines_all[idx + 1 :]:
            probe = str(probe_line or "").strip()
            if not probe:
                if not segment:
                    continue
                blank_gap_count += 1
                if blank_gap_count > _CHAT_ATTACHMENT_LAYOUT_CANDIDATE_BLANK_GAP_TOLERANCE:
                    break
                continue

            blank_gap_count = 0
            is_block_marker = (
                probe.startswith("[")
                and probe.endswith("]")
                and not any(_line_contains_table_delimiter(probe, delim) for delim in _CHAT_ATTACHMENT_TABLE_DELIMITERS)
            )
            if is_block_marker:
                if segment:
                    break
                continue

            segment.append(probe)
            if len(segment) >= _CHAT_ATTACHMENT_TABLE_MAX_SCAN_LINES:
                break

        if len(segment) < 2:
            continue

        delimiter = _guess_table_delimiter(segment[:_CHAT_ATTACHMENT_TABLE_DETECTION_SCAN_LINES])
        if not delimiter:
            delimiter = _guess_table_delimiter(segment)

        gridline_used = False
        grid_columns = 0
        if not delimiter:
            grid_layout = _normalize_layout_candidate_grid_lines(segment)
            if isinstance(grid_layout, dict):
                normalized_lines = grid_layout.get("lines") if isinstance(grid_layout.get("lines"), list) else []
                normalized_lines = [str(x).strip() for x in normalized_lines if str(x or "").strip()]
                if len(normalized_lines) >= 2:
                    segment = normalized_lines
                    delimiter = "	"
                    gridline_used = True
                    grid_columns = int(grid_layout.get("column_count") or 0)

        if not delimiter:
            continue

        row_hits = 0
        row_col_counts: List[int] = []
        for candidate in segment[: min(36, len(segment))]:
            if not _line_contains_table_delimiter(candidate, delimiter):
                continue
            cells = [x for x in _split_table_cells(candidate, delimiter) if x]
            if len(cells) >= 2:
                row_hits += 1
                row_col_counts.append(len(cells))

        if row_hits < 2:
            continue

        mode_col_count = Counter(row_col_counts).most_common(1)[0][0] if row_col_counts else 0
        header_line, header_key_signature, header_column_count = _layout_block_header_signature(segment, delimiter)
        expected_columns = header_column_count if header_column_count >= 2 else mode_col_count
        quality_meta = _estimate_layout_block_quality(
            segment,
            delimiter,
            header_line=header_line,
            expected_columns=expected_columns,
        )
        quality_score = float(quality_meta.get("quality_score") or 0.0)
        valid_row_density = float(quality_meta.get("valid_row_density") or 0.0)
        column_stability = float(quality_meta.get("column_stability") or 0.0)
        expected_alignment = float(quality_meta.get("expected_alignment") or 0.0)

        if quality_score < _CHAT_ATTACHMENT_LAYOUT_BLOCK_MIN_QUALITY and row_hits < 4:
            continue

        score = float(row_hits) * 7.2 + float(min(len(segment), 56)) * 0.45 + quality_score * 18.0
        if delimiter == "	":
            score += 6.0
        if header_key_signature:
            score += 2.8

        blocks.append(
            {
                "start_index": idx + 1,
                "lines": segment,
                "delimiter": delimiter,
                "row_hits": row_hits,
                "score": score,
                "quality_score": quality_score,
                "valid_row_density": valid_row_density,
                "column_stability": column_stability,
                "expected_alignment": expected_alignment,
                "gridline_used": gridline_used,
                "grid_columns": grid_columns,
                "column_count": mode_col_count,
                "header_line": header_line,
                "header_key_signature": header_key_signature,
                "header_column_count": header_column_count,
            }
        )

    if not blocks:
        return None

    delimiter_counter: Counter[str] = Counter()
    for block in blocks:
        delimiter = str(block.get("delimiter") or "")
        if not delimiter:
            continue
        delimiter_counter[delimiter] += int(block.get("row_hits") or 0) + len(block.get("lines") or [])

    selected_delimiter = delimiter_counter.most_common(1)[0][0] if delimiter_counter else ""
    if not selected_delimiter:
        blocks.sort(key=lambda x: (-float(x.get("score") or 0.0), int(x.get("start_index") or 0)))
        selected_delimiter = str(blocks[0].get("delimiter") or "")

    selected_blocks: List[Dict[str, Any]] = [
        block for block in blocks if str(block.get("delimiter") or "") == selected_delimiter
    ]
    if not selected_blocks:
        selected_blocks = blocks[:1]

    signature_counter: Counter[str] = Counter()
    signature_column_hint: Dict[str, int] = {}
    for block in selected_blocks:
        signature = str(block.get("header_key_signature") or "").strip()
        if not signature:
            continue
        weight = int(block.get("row_hits") or 0) + len(block.get("lines") or [])
        signature_counter[signature] += max(1, weight)
        candidate_columns = int(block.get("header_column_count") or 0)
        if candidate_columns <= 0:
            candidate_columns = int(block.get("column_count") or 0)
        if candidate_columns > int(signature_column_hint.get(signature) or 0):
            signature_column_hint[signature] = candidate_columns

    selected_signature = signature_counter.most_common(1)[0][0] if signature_counter else ""
    if selected_signature:
        expected_columns = int(signature_column_hint.get(selected_signature) or 0)
        filtered_blocks: List[Dict[str, Any]] = []
        for block in selected_blocks:
            signature = str(block.get("header_key_signature") or "").strip()
            block_columns = int(block.get("header_column_count") or 0)
            if block_columns <= 0:
                block_columns = int(block.get("column_count") or 0)
            column_compatible = (
                expected_columns < 2
                or (block_columns >= 2 and abs(block_columns - expected_columns) <= 1)
            )

            if signature:
                similarity = _layout_header_signature_similarity(selected_signature, signature)
                block["_selected_signature_similarity"] = similarity
                if similarity >= _CHAT_ATTACHMENT_LAYOUT_SIGNATURE_SIMILARITY_KEEP and column_compatible:
                    filtered_blocks.append(block)
                continue

            if column_compatible:
                filtered_blocks.append(block)

        if filtered_blocks:
            selected_blocks = filtered_blocks

    for block in selected_blocks:
        block_lines = [str(x).strip() for x in block.get("lines", []) if str(x or "").strip()]
        block_delimiter = str(block.get("delimiter") or "")
        header_line = str(block.get("header_line") or "").strip()
        block["_entity_keys"] = _extract_layout_block_entity_keys(block_lines, block_delimiter, header_line)

    selected_blocks = _refine_layout_selected_blocks(selected_blocks)
    selected_blocks.sort(key=lambda x: int(x.get("start_index") or 0))

    merged_lines: List[str] = []
    merged_seen: set[str] = set()
    header_signature = ""
    total_row_hits = 0
    gridline_used_any = False
    grid_columns = 0

    for block in selected_blocks:
        raw_lines = [str(x).strip() for x in block.get("lines", []) if str(x or "").strip()]
        if len(raw_lines) < 2:
            continue

        block_delimiter = str(block.get("delimiter") or selected_delimiter)
        expected_columns = int(block.get("header_column_count") or 0)
        if expected_columns <= 0:
            expected_columns = int(block.get("column_count") or 0)
        block_header = str(block.get("header_line") or "").strip()

        lines = _prepare_layout_block_lines_for_merge(
            raw_lines,
            block_delimiter,
            header_line=block_header,
            expected_columns=expected_columns,
        )
        if len(lines) < 2:
            continue

        total_row_hits += int(block.get("row_hits") or 0)
        if bool(block.get("gridline_used")):
            gridline_used_any = True
            grid_columns = max(grid_columns, int(block.get("grid_columns") or 0))

        local_header = ""
        if block_header and block_header in lines:
            local_header = block_header
        else:
            for candidate_line in lines:
                if not _line_contains_table_delimiter(candidate_line, selected_delimiter):
                    continue
                cells = [x for x in _split_table_cells(candidate_line, selected_delimiter) if x]
                if len(cells) >= 2:
                    local_header = candidate_line
                    break

        if not header_signature and local_header:
            header_signature = local_header

        for line_text in lines:
            if len(merged_lines) >= _CHAT_ATTACHMENT_TABLE_MAX_SCAN_LINES:
                break

            if merged_lines and local_header and line_text == local_header:
                continue

            if header_signature and line_text == header_signature and merged_lines:
                continue

            if line_text in merged_seen and line_text == header_signature:
                continue

            merged_lines.append(line_text)
            if line_text == header_signature:
                merged_seen.add(line_text)

        if len(merged_lines) >= _CHAT_ATTACHMENT_TABLE_MAX_SCAN_LINES:
            break

    if len(merged_lines) < 2:
        blocks.sort(key=lambda x: (-float(x.get("score") or 0.0), int(x.get("start_index") or 0)))
        fallback = blocks[0]
        merged_lines = [str(x).strip() for x in fallback.get("lines", []) if str(x or "").strip()]
        selected_delimiter = str(fallback.get("delimiter") or selected_delimiter)
        selected_blocks = [fallback]
        total_row_hits = int(fallback.get("row_hits") or 0)
        gridline_used_any = bool(fallback.get("gridline_used"))
        grid_columns = int(fallback.get("grid_columns") or 0)

    if len(merged_lines) < 2 or not selected_delimiter:
        return None

    return {
        "start_index": int(selected_blocks[0].get("start_index") or 0),
        "lines": merged_lines,
        "delimiter": selected_delimiter,
        "block_count": len(selected_blocks),
        "merged_row_hits": total_row_hits,
        "gridline_used": gridline_used_any,
        "grid_columns": grid_columns,
    }


def _is_probable_table_page_marker(line: str) -> bool:
    text = str(line or "").strip()
    if not text:
        return False

    compact = re.sub(r"\s+", " ", text.lower())
    if "page break" in compact or "分页符" in text:
        return True

    if re.match(r"^(?:-+\s*)?(?:page|p)\s*\d+(?:\s*(?:/|of)\s*\d+)?(?:\s*-+)?$", compact):
        return True
    if re.match(r"^第?\s*\d+\s*[页頁](?:\s*(?:/|of|共)\s*(?:(?:第|共)?\s*)?\d+\s*[页頁]?)?$", text, flags=re.IGNORECASE):
        return True
    if re.match(r"^第?\s*\d+\s*[页頁]\s*(?:-|—|~|至)\s*第?\s*\d+\s*[页頁]$", text):
        return True
    return False


def _is_numeric_like_table_cell(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False

    if re.fullmatch(r"[+\-]?\d[\d,%％\-/:]*", text):
        return True
    if re.fullmatch(r"\d{4}[-/]\d{1,2}[-/]\d{1,2}", text):
        return True
    return False


def _is_probable_table_header_cells(cells: List[str]) -> bool:
    non_empty = [str(cell or "").strip() for cell in cells if str(cell or "").strip()]
    if len(non_empty) < 2:
        return False

    numeric_like = sum(1 for cell in non_empty if _is_numeric_like_table_cell(cell))
    if numeric_like >= len(non_empty):
        return False

    text_like = sum(1 for cell in non_empty if re.search(r"[A-Za-z\u4e00-\u9fff]", cell))
    if text_like < max(1, len(non_empty) // 2):
        return False

    label_like = sum(1 for cell in non_empty if re.search(r"[A-Za-z\u4e00-\u9fff]", cell) and not re.search(r"\d", cell))
    if label_like < 1:
        return False

    return True


def _build_table_header_profile(line: str, delimiter: str) -> Tuple[List[str], List[str]]:
    row_cells = _split_table_cells(line, delimiter)
    if len([x for x in row_cells if x]) < 2:
        return [], []

    headers: List[str] = []
    header_keys: List[str] = []
    seen_keys: set[str] = set()
    for cell in row_cells:
        label = str(cell or "").strip()
        if not label:
            continue
        key = _normalize_attachment_field_key(label)
        if not key or key in seen_keys:
            continue
        seen_keys.add(key)
        headers.append(label[:24])
        header_keys.append(key)
        if len(headers) >= _CHAT_ATTACHMENT_TABLE_MAX_FIELDS:
            break
    return headers, header_keys


def _is_repeated_table_header_line(line: str, delimiter: str, header_keys: List[str]) -> bool:
    if not header_keys:
        return False
    if not _line_contains_table_delimiter(line, delimiter):
        return False
    if _is_probable_table_page_marker(line):
        return False

    row_cells = _split_table_cells(line, delimiter)
    row_keys: List[str] = []
    for cell in row_cells:
        token = _normalize_attachment_field_key(cell)
        if not token:
            continue
        row_keys.append(token)
        if len(row_keys) >= _CHAT_ATTACHMENT_TABLE_MAX_FIELDS:
            break

    if len(row_keys) < 2:
        return False
    if row_keys[: len(header_keys)] == header_keys[: len(row_keys)]:
        return True

    overlap = len(set(row_keys) & set(header_keys))
    if overlap >= max(2, int(round(len(header_keys) * 0.7))) and _is_probable_table_header_cells(row_cells):
        return True
    return False


def _score_table_header_candidate(
    *,
    line_index: int,
    line: str,
    delimiter: str,
    has_hint: bool,
    lines_all: List[str],
) -> Tuple[float, List[str], List[str]]:
    headers, header_keys = _build_table_header_profile(line, delimiter)
    if len(headers) < 2 or len(header_keys) < 2:
        return 0.0, [], []
    if not _is_probable_table_header_cells(headers):
        return 0.0, [], []

    lookahead = lines_all[line_index + 1 : line_index + 1 + _CHAT_ATTACHMENT_TABLE_HEADER_LOOKAHEAD_LINES]
    valid_rows = 0
    repeated_headers = 0
    for row_line in lookahead:
        if _is_probable_table_page_marker(row_line):
            continue
        if not _line_contains_table_delimiter(row_line, delimiter):
            continue
        if _is_repeated_table_header_line(row_line, delimiter, header_keys):
            repeated_headers += 1
            continue

        row_cells = _split_table_cells(row_line, delimiter)
        if len([x for x in row_cells if x]) < 2:
            continue
        usable = row_cells[: len(headers)]
        non_empty = sum(1 for val in usable if val)
        if non_empty < max(1, len(headers) // 3):
            continue
        valid_rows += 1

    if valid_rows < 1 and not has_hint:
        return 0.0, [], []

    text_like = sum(1 for cell in headers if re.search(r"[A-Za-z\u4e00-\u9fff]", cell))
    label_like = sum(1 for cell in headers if re.search(r"[A-Za-z\u4e00-\u9fff]", cell) and not re.search(r"\d", cell))
    numeric_like = sum(1 for cell in headers if _is_numeric_like_table_cell(cell))
    semantic_hits = sum(1 for key in header_keys if _match_attachment_field_group(key))

    score = float(valid_rows) * 5.0 + float(text_like) * 1.6 - float(numeric_like) * 1.2
    score += float(len(headers)) * 0.45
    score += float(label_like) * 0.9
    score += float(semantic_hits) * 2.4
    if has_hint:
        score += 1.2
    score -= float(line_index) * 0.08
    score -= float(repeated_headers) * 0.05
    return score, headers, header_keys


def _extract_attachment_table_profile(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(item, dict):
        return None

    content = str(item.get("content") or "").replace("\x00", "")
    if not content:
        return None

    lines_all: List[str] = []
    for raw_line in content.splitlines():
        line = str(raw_line or "").strip()
        if not line:
            continue
        lines_all.append(line)
        if len(lines_all) >= _CHAT_ATTACHMENT_TABLE_MAX_SCAN_LINES:
            break

    if len(lines_all) < 3:
        return None

    has_hint = _has_tabular_hint(item)

    layout_candidate = _extract_layout_candidate_table_lines(lines_all)
    layout_candidate_used = False
    layout_candidate_rows = 0
    layout_candidate_block_count = 0
    layout_candidate_gridline_used = False
    layout_candidate_grid_columns = 0
    grid_source_indexes: List[int] = []
    scan_origin_offset = 0
    scan_lines = lines_all
    delimiter = ""

    if isinstance(layout_candidate, dict):
        candidate_lines = layout_candidate.get("lines") if isinstance(layout_candidate.get("lines"), list) else []
        candidate_lines = [str(x).strip() for x in candidate_lines if str(x or "").strip()]
        if len(candidate_lines) >= 2:
            layout_candidate_used = True
            layout_candidate_rows = len(candidate_lines)
            layout_candidate_block_count = max(1, int(layout_candidate.get("block_count") or 0))
            layout_candidate_gridline_used = bool(layout_candidate.get("gridline_used"))
            layout_candidate_grid_columns = int(layout_candidate.get("grid_columns") or 0)
            scan_lines = candidate_lines
            delimiter = str(layout_candidate.get("delimiter") or "")
            scan_origin_offset = int(layout_candidate.get("start_index") or 0)

    if not delimiter:
        detection_lines = scan_lines[:_CHAT_ATTACHMENT_TABLE_DETECTION_SCAN_LINES]
        delimiter = _guess_table_delimiter(detection_lines)
        if not delimiter:
            delimiter = _guess_table_delimiter(scan_lines)

    if not delimiter:
        grid_layout = _normalize_layout_candidate_grid_lines(scan_lines)
        if isinstance(grid_layout, dict):
            normalized_lines = grid_layout.get("lines") if isinstance(grid_layout.get("lines"), list) else []
            normalized_lines = [str(x).strip() for x in normalized_lines if str(x or "").strip()]
            if len(normalized_lines) >= 2:
                scan_lines = normalized_lines
                source_idx_raw = grid_layout.get("source_indexes") if isinstance(grid_layout.get("source_indexes"), list) else []
                grid_source_indexes = [int(x) for x in source_idx_raw if isinstance(x, int) or str(x).isdigit()]
                delimiter = "	"
                layout_candidate_gridline_used = True
                layout_candidate_grid_columns = int(grid_layout.get("column_count") or 0)

    if not delimiter:
        return None

    header_index = -1
    headers: List[str] = []
    header_keys: List[str] = []
    best_score = 0.0
    header_scan_limit = min(len(scan_lines), _CHAT_ATTACHMENT_TABLE_HEADER_SCAN_LINES)
    for idx in range(header_scan_limit):
        line = scan_lines[idx]
        if not _line_contains_table_delimiter(line, delimiter):
            continue
        if _is_probable_table_page_marker(line):
            continue

        score, cand_headers, cand_header_keys = _score_table_header_candidate(
            line_index=idx,
            line=line,
            delimiter=delimiter,
            has_hint=has_hint,
            lines_all=scan_lines,
        )
        if score <= best_score:
            continue
        best_score = score
        header_index = idx
        headers = cand_headers
        header_keys = cand_header_keys

    if header_index < 0 or len(headers) < 2:
        return None

    rows_estimate = 0
    sample_rows: List[str] = []
    valid_rows: List[List[str]] = []
    repeated_header_skipped = 0
    page_marker_skipped = 0

    for line in scan_lines[header_index + 1 :]:
        if _is_probable_table_page_marker(line):
            page_marker_skipped += 1
            continue
        if not _line_contains_table_delimiter(line, delimiter):
            continue
        if _is_repeated_table_header_line(line, delimiter, header_keys):
            repeated_header_skipped += 1
            continue

        row_cells = _split_table_cells(line, delimiter)
        if len([x for x in row_cells if x]) < 2:
            continue

        usable = row_cells[: len(headers)]
        non_empty = sum(1 for val in usable if val)
        if non_empty < max(1, len(headers) // 3):
            continue

        rows_estimate += 1
        valid_rows.append(usable)

        if len(sample_rows) < _CHAT_ATTACHMENT_TABLE_MAX_SAMPLES:
            pairs: List[str] = []
            for idx, value in enumerate(usable):
                if not value:
                    continue
                pairs.append(f"{headers[idx]}={value[:22]}")
                if len(pairs) >= 3:
                    break
            if pairs:
                sample_rows.append("；".join(pairs))

    if rows_estimate < 1:
        return None
    if rows_estimate < 2 and not has_hint:
        return None

    selected_rows: List[List[str]] = []
    entity_scan_mode = "sampled"
    if valid_rows:
        if len(valid_rows) <= _CHAT_ATTACHMENT_TABLE_ENTITY_FULL_SCAN_MAX_ROWS:
            selected_rows = valid_rows
            entity_scan_mode = "full"
        elif len(valid_rows) <= _CHAT_ATTACHMENT_TABLE_ENTITY_MAX_ROWS:
            selected_rows = valid_rows
        else:
            if _CHAT_ATTACHMENT_TABLE_ENTITY_MAX_ROWS <= 1:
                selected_rows = [valid_rows[0]]
            else:
                step = float(len(valid_rows) - 1) / float(_CHAT_ATTACHMENT_TABLE_ENTITY_MAX_ROWS - 1)
                sampled_indexes: List[int] = []
                seen_idx: set[int] = set()
                for i in range(_CHAT_ATTACHMENT_TABLE_ENTITY_MAX_ROWS):
                    idx = int(round(i * step))
                    idx = min(len(valid_rows) - 1, max(0, idx))
                    if idx in seen_idx:
                        continue
                    seen_idx.add(idx)
                    sampled_indexes.append(idx)
                selected_rows = [valid_rows[idx] for idx in sampled_indexes]

    entity_values: Dict[str, set[str]] = {}
    entity_preview: Dict[str, List[str]] = {}
    row_records: List[Dict[str, str]] = []
    for usable in selected_rows:
        row_record: Dict[str, str] = {}
        for idx, value in enumerate(usable):
            raw_val = str(value or "").strip()
            if not raw_val:
                continue

            field_key = header_keys[idx] if idx < len(header_keys) else ""
            if not field_key:
                continue

            norm_val = _normalize_attachment_cell_value(raw_val)
            if len(norm_val) < _CHAT_ATTACHMENT_TABLE_ENTITY_MIN_VALUE_LEN:
                continue

            row_record[field_key] = norm_val

            bucket = entity_values.setdefault(field_key, set())
            if len(bucket) < _CHAT_ATTACHMENT_TABLE_ENTITY_MAX_VALUES_PER_FIELD:
                bucket.add(norm_val)

            preview_bucket = entity_preview.setdefault(field_key, [])
            preview_value = raw_val[:30]
            if preview_value and preview_value not in preview_bucket and len(preview_bucket) < _CHAT_ATTACHMENT_TABLE_ENTITY_MAX_PREVIEW:
                preview_bucket.append(preview_value)

        if row_record:
            row_records.append(row_record)


    delimiter_name = {
        ",": "逗号",
        "\t": "制表符",
        "|": "竖线",
        ";": "分号",
        "，": "中文逗号",
        _CHAT_ATTACHMENT_TABLE_WHITESPACE_DELIMITER: "空白对齐",
    }.get(delimiter, "分隔符")

    entity_values_public: Dict[str, List[str]] = {
        key: sorted(list(values))[:_CHAT_ATTACHMENT_TABLE_ENTITY_MAX_VALUES_PER_FIELD]
        for key, values in entity_values.items()
        if values
    }

    if layout_candidate_used:
        if layout_candidate_gridline_used and 0 <= header_index < len(grid_source_indexes):
            header_line = scan_origin_offset + int(grid_source_indexes[header_index]) + 1
        else:
            header_line = scan_origin_offset + header_index + 1
    else:
        header_line = header_index + 1

    return {
        "fields": headers,
        "rows_estimate": rows_estimate,
        "rows_scanned": len(valid_rows),
        "entity_sample_rows": len(selected_rows),
        "entity_scan_mode": entity_scan_mode,
        "header_line": header_line,
        "repeated_header_skipped": repeated_header_skipped,
        "page_marker_skipped": page_marker_skipped,
        "layout_candidate_used": layout_candidate_used,
        "layout_candidate_rows": layout_candidate_rows,
        "layout_candidate_block_count": layout_candidate_block_count,
        "layout_candidate_gridline_used": layout_candidate_gridline_used,
        "layout_candidate_grid_columns": layout_candidate_grid_columns,
        "delimiter": delimiter_name,
        "sample_rows": sample_rows,
        "entity_values": entity_values_public,
        "entity_preview": entity_preview,
        "row_records": row_records[:_CHAT_ATTACHMENT_TABLE_ENTITY_ROW_RECORD_LIMIT],
    }


def _field_key_priority_score(
    key: str,
    *,
    count: int,
    total_tables: int,
    semantic_resources: Optional[Dict[str, Any]] = None,
) -> float:
    normalized_key = _normalize_attachment_field_key(key)
    if not normalized_key:
        return 0.0

    coverage = float(count) / float(max(1, total_tables))
    score = float(count) * 12.0 + coverage * 40.0

    group = _match_attachment_field_group(normalized_key, semantic_resources=semantic_resources)
    if group in ("sku", "order", "user"):
        score += 32.0
    elif group == "date":
        score += 20.0
    elif group:
        score += 14.0

    if normalized_key.endswith("id") or normalized_key.startswith("id"):
        score += 10.0
    if len(normalized_key) <= 2:
        score -= 6.0

    return round(score, 3)


def _infer_primary_key_candidates(
    field_counter: Counter[str],
    field_display: Dict[str, str],
    field_group_counter: Counter[str],
    *,
    table_count: int,
    semantic_resources: Optional[Dict[str, Any]] = None,
) -> List[str]:
    if table_count < 2:
        return []

    resources = semantic_resources if isinstance(semantic_resources, dict) else _load_attachment_semantic_resources()
    labels = resources.get("labels") if isinstance(resources.get("labels"), dict) else {}
    weights = resources.get("weights") if isinstance(resources.get("weights"), dict) else {}

    candidates: List[str] = []

    grouped_ranked: List[Tuple[float, int, str]] = []
    for group, count in field_group_counter.items():
        if count < 2:
            continue
        weight = float(weights.get(group, _CHAT_ATTACHMENT_FIELD_GROUP_WEIGHTS.get(group, 0.5)))
        coverage = float(count) / float(max(1, table_count))
        score = coverage * 100.0 + weight * 10.0 + float(count)
        grouped_ranked.append((score, count, group))

    grouped_ranked.sort(key=lambda x: (-x[0], -x[1], x[2]))
    for _score, _count, group in grouped_ranked:
        label = str(labels.get(group) or _CHAT_ATTACHMENT_FIELD_GROUP_LABELS.get(group) or group).strip()
        if not label or label in candidates:
            continue
        candidates.append(label)
        if len(candidates) >= 4:
            return candidates

    ranked: List[Tuple[float, int, str]] = []
    for key, count in field_counter.items():
        if count < 2:
            continue
        score = _field_key_priority_score(
            key,
            count=count,
            total_tables=table_count,
            semantic_resources=resources,
        )
        if score <= 0:
            continue
        ranked.append((score, count, key))

    ranked.sort(key=lambda x: (-x[0], -x[1], x[2]))

    for _score, _count, key in ranked:
        label = str(field_display.get(key) or key).strip()
        if not label or label in candidates:
            continue
        candidates.append(label)
        if len(candidates) >= 4:
            break
    return candidates


def _build_composite_row_profile(
    row_records: List[Dict[str, Any]],
    *,
    primary_key: str,
    secondary_keys: List[str],
) -> Dict[str, Any]:
    normalized_secondary_keys = [
        _normalize_attachment_field_key(key)
        for key in (secondary_keys or [])
        if _normalize_attachment_field_key(key)
    ]

    if not primary_key or not normalized_secondary_keys:
        return {
            "record_map": {},
            "keys": set(),
            "total": 0,
            "unique_count": 0,
            "duplicate_ratio": 0.0,
        }

    record_map: Dict[str, Dict[str, str]] = {}
    total = 0
    for raw_record in row_records:
        if not isinstance(raw_record, dict):
            continue
        record = {
            _normalize_attachment_field_key(k): _normalize_attachment_cell_value(v)
            for k, v in raw_record.items()
            if _normalize_attachment_field_key(k) and _normalize_attachment_cell_value(v)
        }

        primary_value = str(record.get(primary_key) or "").strip()
        if not primary_value:
            continue

        composite_parts: List[str] = [primary_value]
        valid_secondary = True
        for secondary_key in normalized_secondary_keys:
            secondary_value = str(record.get(secondary_key) or "").strip()
            if not secondary_value:
                valid_secondary = False
                break
            composite_parts.append(secondary_value)

        if not valid_secondary:
            continue

        total += 1
        composite_key = "|".join(composite_parts)
        if composite_key in record_map:
            continue
        record_map[composite_key] = record

    unique_count = len(record_map)
    duplicate_ratio = 0.0
    if total > 0:
        duplicate_ratio = max(0.0, 1.0 - (float(unique_count) / float(total)))

    return {
        "record_map": record_map,
        "keys": set(record_map.keys()),
        "total": total,
        "unique_count": unique_count,
        "duplicate_ratio": round(duplicate_ratio, 3),
    }


def _evaluate_attachment_composite_candidate(
    left_row_records: List[Dict[str, Any]],
    right_row_records: List[Dict[str, Any]],
    *,
    left_primary_key: str,
    right_primary_key: str,
    left_secondary_keys: List[str],
    right_secondary_keys: List[str],
) -> Dict[str, Any]:
    normalized_left_secondary = [
        _normalize_attachment_field_key(key)
        for key in (left_secondary_keys or [])
        if _normalize_attachment_field_key(key)
    ]
    normalized_right_secondary = [
        _normalize_attachment_field_key(key)
        for key in (right_secondary_keys or [])
        if _normalize_attachment_field_key(key)
    ]

    if not normalized_left_secondary or not normalized_right_secondary:
        return {}
    if len(normalized_left_secondary) != len(normalized_right_secondary):
        return {}

    left_profile = _build_composite_row_profile(
        left_row_records,
        primary_key=left_primary_key,
        secondary_keys=normalized_left_secondary,
    )
    right_profile = _build_composite_row_profile(
        right_row_records,
        primary_key=right_primary_key,
        secondary_keys=normalized_right_secondary,
    )

    left_keys = left_profile.get("keys") if isinstance(left_profile.get("keys"), set) else set()
    right_keys = right_profile.get("keys") if isinstance(right_profile.get("keys"), set) else set()
    if not left_keys or not right_keys:
        return {}

    overlap_values = sorted(left_keys & right_keys)
    overlap_count = len(overlap_values)
    if overlap_count <= 0:
        return {}

    left_count = len(left_keys)
    right_count = len(right_keys)
    min_count = max(1, min(left_count, right_count))
    overlap_ratio = float(overlap_count) / float(min_count)
    left_hit_ratio = float(overlap_count) / float(max(1, left_count))
    right_hit_ratio = float(overlap_count) / float(max(1, right_count))

    left_duplicate_ratio = float(left_profile.get("duplicate_ratio") or 0.0)
    right_duplicate_ratio = float(right_profile.get("duplicate_ratio") or 0.0)
    duplicate_pressure = max(left_duplicate_ratio, right_duplicate_ratio)

    key_count = len(normalized_left_secondary)
    score = (
        overlap_ratio * 100.0
        + min(16.0, float(overlap_count) * 2.4)
        - duplicate_pressure * 18.0
        + min(4.0, float(max(0, key_count - 1)) * 1.4)
    )

    return {
        "score": round(score, 3),
        "overlap_count": overlap_count,
        "overlap_ratio": round(overlap_ratio, 3),
        "left_hit_ratio": round(left_hit_ratio, 3),
        "right_hit_ratio": round(right_hit_ratio, 3),
        "left_duplicate_ratio": round(left_duplicate_ratio, 3),
        "right_duplicate_ratio": round(right_duplicate_ratio, 3),
        "duplicate_pressure": round(duplicate_pressure, 3),
        "overlap_examples": overlap_values[:_CHAT_ATTACHMENT_TABLE_ENTITY_MAX_PREVIEW],
        "overlap_values": overlap_values,
        "secondary_key_count": key_count,
        "left_profile": left_profile,
        "right_profile": right_profile,
    }


def _build_alignment_suggestions(
    table_items: List[Dict[str, Any]],
    field_counter: Counter[str],
    field_display: Dict[str, str],
    field_group_counter: Counter[str],
    *,
    table_count: int,
    semantic_resources: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    if table_count < 2:
        return []

    resources = semantic_resources if isinstance(semantic_resources, dict) else _load_attachment_semantic_resources()
    labels = resources.get("labels") if isinstance(resources.get("labels"), dict) else {}
    weights = resources.get("weights") if isinstance(resources.get("weights"), dict) else {}
    weak_fallback_groups = {"order", "user", "date"}

    suggestions: List[Dict[str, Any]] = []

    for i in range(len(table_items)):
        left = table_items[i]
        left_keys = {str(x).strip() for x in left.get("_normalized_fields", []) if str(x or "").strip()}
        left_group_map = left.get("_group_to_key") if isinstance(left.get("_group_to_key"), dict) else {}
        if not left_keys and not left_group_map:
            continue

        for j in range(i + 1, len(table_items)):
            right = table_items[j]
            right_keys = {str(x).strip() for x in right.get("_normalized_fields", []) if str(x or "").strip()}
            right_group_map = right.get("_group_to_key") if isinstance(right.get("_group_to_key"), dict) else {}
            if not right_keys and not right_group_map:
                continue

            shared = sorted(left_keys & right_keys)
            shared_groups = sorted(set(left_group_map.keys()) & set(right_group_map.keys()))

            exact_candidate: Optional[Dict[str, Any]] = None
            if shared:
                ranked_shared = sorted(
                    shared,
                    key=lambda k: _field_key_priority_score(
                        k,
                        count=int(field_counter.get(k) or 1),
                        total_tables=table_count,
                        semantic_resources=resources,
                    ),
                    reverse=True,
                )
                join_key = ranked_shared[0]
                join_field = str(field_display.get(join_key) or join_key)
                exact_candidate = {
                    "match_mode": "exact",
                    "semantic_group": _match_attachment_field_group(join_key, semantic_resources=resources),
                    "join_key": join_key,
                    "join_field": join_field,
                    "join_left_key": join_key,
                    "join_right_key": join_key,
                    "join_left_field": join_field,
                    "join_right_field": join_field,
                    "normalization_note": "",
                    "shared_fields": [str(field_display.get(k) or k) for k in ranked_shared[:3]],
                    "ranked_shared": ranked_shared,
                }

            semantic_candidate: Optional[Dict[str, Any]] = None
            if shared_groups:
                shared_groups.sort(
                    key=lambda g: (
                        -int(field_group_counter.get(g) or 0),
                        -float(weights.get(g, _CHAT_ATTACHMENT_FIELD_GROUP_WEIGHTS.get(g, 0.5))),
                        g,
                    )
                )
                for group in shared_groups:
                    left_key = str(left_group_map.get(group) or "").strip()
                    right_key = str(right_group_map.get(group) or "").strip()
                    if not left_key or not right_key:
                        continue

                    join_field = str(
                        labels.get(group)
                        or _CHAT_ATTACHMENT_FIELD_GROUP_LABELS.get(group)
                        or field_display.get(left_key)
                        or field_display.get(right_key)
                        or group
                    )
                    join_left_field = str(field_display.get(left_key) or left_key)
                    join_right_field = str(field_display.get(right_key) or right_key)
                    normalization_note = ""
                    if join_left_field != join_right_field:
                        normalization_note = f"字段归一: {join_left_field} ≈ {join_right_field}"

                    shared_fields: List[str] = []
                    for token in (join_left_field, join_right_field):
                        t = str(token or "").strip()
                        if t and t not in shared_fields:
                            shared_fields.append(t)

                    semantic_candidate = {
                        "match_mode": "semantic",
                        "semantic_group": group,
                        "join_key": left_key,
                        "join_field": join_field,
                        "join_left_key": left_key,
                        "join_right_key": right_key,
                        "join_left_field": join_left_field,
                        "join_right_field": join_right_field,
                        "normalization_note": normalization_note,
                        "shared_fields": shared_fields[:3],
                        "ranked_shared": [left_key],
                    }
                    break

            if not exact_candidate and not semantic_candidate:
                continue

            selected = exact_candidate if exact_candidate else semantic_candidate
            alignment_tier = "primary_exact"
            fallback_reason = ""

            if exact_candidate and semantic_candidate:
                exact_group = str(exact_candidate.get("semantic_group") or "")
                semantic_group = str(semantic_candidate.get("semantic_group") or "")
                exact_is_weak = exact_group in weak_fallback_groups
                semantic_is_weak = semantic_group in weak_fallback_groups

                if exact_is_weak and not semantic_is_weak:
                    selected = semantic_candidate
                    alignment_tier = "primary_semantic"
                    fallback_reason = (
                        f"存在共享弱键“{exact_candidate.get('join_field') or '弱主键'}”，"
                        f"已优先使用更强语义主键“{semantic_candidate.get('join_field') or '语义主键'}”。"
                    )
                else:
                    selected = exact_candidate
                    if exact_is_weak:
                        alignment_tier = "weak_fallback"
                        fallback_reason = f"暂未识别到更强共享主键，回退使用弱键“{exact_candidate.get('join_field') or '弱主键'}”。"
                    else:
                        alignment_tier = "primary_exact"
            elif selected is exact_candidate:
                exact_group = str(exact_candidate.get("semantic_group") or "") if isinstance(exact_candidate, dict) else ""
                if exact_group in weak_fallback_groups:
                    alignment_tier = "weak_fallback"
                    fallback_reason = f"暂未识别到更强共享主键，回退使用弱键“{exact_candidate.get('join_field') or '弱主键'}”。"
                else:
                    alignment_tier = "primary_exact"
            else:
                semantic_group = str(semantic_candidate.get("semantic_group") or "") if isinstance(semantic_candidate, dict) else ""
                if semantic_group in weak_fallback_groups:
                    alignment_tier = "weak_fallback"
                    fallback_reason = f"缺少 SKU 等强主键，回退使用弱语义键“{semantic_candidate.get('join_field') or '弱主键'}”。"
                else:
                    alignment_tier = "primary_semantic"

            selected = selected or {}
            match_mode = str(selected.get("match_mode") or "exact")
            semantic_group = str(selected.get("semantic_group") or "")
            join_key = str(selected.get("join_key") or "")
            join_field = str(selected.get("join_field") or "")
            join_left_key = str(selected.get("join_left_key") or join_key)
            join_right_key = str(selected.get("join_right_key") or join_key)
            join_left_field = str(selected.get("join_left_field") or join_field)
            join_right_field = str(selected.get("join_right_field") or join_field)
            normalization_note = str(selected.get("normalization_note") or "")
            shared_fields = [
                str(token).strip()
                for token in (selected.get("shared_fields") or [])
                if str(token or "").strip()
            ][:3]
            ranked_shared = [
                str(token).strip()
                for token in (selected.get("ranked_shared") or [])
                if str(token or "").strip()
            ]

            join_secondary_key = ""
            join_secondary_left_key = ""
            join_secondary_right_key = ""
            join_secondary_field = ""
            join_secondary_left_field = ""
            join_secondary_right_field = ""
            join_tertiary_key = ""
            join_tertiary_left_key = ""
            join_tertiary_right_key = ""
            join_tertiary_field = ""
            join_tertiary_left_field = ""
            join_tertiary_right_field = ""
            composite_enabled = False
            composite_overlap_hint = 0
            composite_duplicate_pressure_hint = 0.0
            composite_secondary_source = ""
            composite_secondary_count = 0

            if alignment_tier == "weak_fallback":
                left_row_records = left.get("_row_records") if isinstance(left.get("_row_records"), list) else []
                right_row_records = right.get("_row_records") if isinstance(right.get("_row_records"), list) else []

                unit_candidates: List[Dict[str, Any]] = []
                seen_secondary_pairs: set[Tuple[str, str]] = set()

                for group in shared_groups:
                    group_key = str(group or "").strip()
                    if not group_key or group_key in weak_fallback_groups:
                        continue
                    left_secondary = str(left_group_map.get(group_key) or "").strip()
                    right_secondary = str(right_group_map.get(group_key) or "").strip()
                    if not left_secondary or not right_secondary:
                        continue
                    pair = (left_secondary, right_secondary)
                    if pair in seen_secondary_pairs:
                        continue
                    seen_secondary_pairs.add(pair)

                    priority = (
                        float(field_group_counter.get(group_key) or 0) * 10.0
                        + float(weights.get(group_key, _CHAT_ATTACHMENT_FIELD_GROUP_WEIGHTS.get(group_key, 0.5))) * 8.0
                    )
                    unit_candidates.append(
                        {
                            "source": "group",
                            "group": group_key,
                            "left_keys": [left_secondary],
                            "right_keys": [right_secondary],
                            "left_fields": [str(field_display.get(left_secondary) or left_secondary)],
                            "right_fields": [str(field_display.get(right_secondary) or right_secondary)],
                            "display_fields": [
                                str(
                                    labels.get(group_key)
                                    or _CHAT_ATTACHMENT_FIELD_GROUP_LABELS.get(group_key)
                                    or field_display.get(left_secondary)
                                    or field_display.get(right_secondary)
                                    or group_key
                                )
                            ],
                            "priority": round(priority, 3),
                        }
                    )

                for secondary_key in shared:
                    candidate_key = str(secondary_key or "").strip()
                    if not candidate_key or candidate_key == join_key:
                        continue
                    candidate_group = _match_attachment_field_group(candidate_key, semantic_resources=resources)
                    if candidate_group in weak_fallback_groups:
                        continue

                    pair = (candidate_key, candidate_key)
                    if pair in seen_secondary_pairs:
                        continue
                    seen_secondary_pairs.add(pair)

                    candidate_score = _field_key_priority_score(
                        candidate_key,
                        count=int(field_counter.get(candidate_key) or 1),
                        total_tables=table_count,
                        semantic_resources=resources,
                    )
                    unit_candidates.append(
                        {
                            "source": "exact",
                            "group": candidate_group,
                            "left_keys": [candidate_key],
                            "right_keys": [candidate_key],
                            "left_fields": [str(field_display.get(candidate_key) or candidate_key)],
                            "right_fields": [str(field_display.get(candidate_key) or candidate_key)],
                            "display_fields": [str(field_display.get(candidate_key) or candidate_key)],
                            "priority": round(float(candidate_score), 3),
                        }
                    )

                scored_candidates: List[Dict[str, Any]] = []
                for unit in unit_candidates:
                    composite_metrics = _evaluate_attachment_composite_candidate(
                        left_row_records,
                        right_row_records,
                        left_primary_key=join_left_key,
                        right_primary_key=join_right_key,
                        left_secondary_keys=list(unit.get("left_keys") or []),
                        right_secondary_keys=list(unit.get("right_keys") or []),
                    )

                    has_metrics = bool(composite_metrics)
                    overlap_count_hint = int(composite_metrics.get("overlap_count") or 0) if has_metrics else 0
                    overlap_ratio_hint = float(composite_metrics.get("overlap_ratio") or 0.0) if has_metrics else 0.0
                    duplicate_pressure_hint = float(composite_metrics.get("duplicate_pressure") or 0.0) if has_metrics else 1.0

                    metric_score = float(composite_metrics.get("score") or 0.0) if has_metrics else -50.0
                    total_score = float(unit.get("priority") or 0.0) + metric_score

                    ranked = dict(unit)
                    ranked["metrics"] = composite_metrics
                    ranked["overlap_count_hint"] = overlap_count_hint
                    ranked["overlap_ratio_hint"] = overlap_ratio_hint
                    ranked["duplicate_pressure_hint"] = duplicate_pressure_hint
                    ranked["total_score"] = round(total_score, 3)
                    scored_candidates.append(ranked)

                max_pair_seed = min(6, len(scored_candidates))
                pair_seeds = sorted(
                    scored_candidates,
                    key=lambda item: (
                        -int(item.get("overlap_count_hint") or 0),
                        -float(item.get("overlap_ratio_hint") or 0.0),
                        float(item.get("duplicate_pressure_hint") or 1.0),
                        -float(item.get("total_score") or 0.0),
                    ),
                )[:max_pair_seed]

                for i_unit in range(len(pair_seeds)):
                    for j_unit in range(i_unit + 1, len(pair_seeds)):
                        first = pair_seeds[i_unit]
                        second = pair_seeds[j_unit]

                        first_left_keys = list(first.get("left_keys") or [])
                        first_right_keys = list(first.get("right_keys") or [])
                        second_left_keys = list(second.get("left_keys") or [])
                        second_right_keys = list(second.get("right_keys") or [])
                        if not first_left_keys or not first_right_keys or not second_left_keys or not second_right_keys:
                            continue

                        left_pair_keys = [str(first_left_keys[0]), str(second_left_keys[0])]
                        right_pair_keys = [str(first_right_keys[0]), str(second_right_keys[0])]
                        if len(set(left_pair_keys)) < 2 or len(set(right_pair_keys)) < 2:
                            continue

                        if any(
                            (list(item.get("left_keys") or []) == left_pair_keys and list(item.get("right_keys") or []) == right_pair_keys)
                            for item in scored_candidates
                        ):
                            continue

                        composite_metrics = _evaluate_attachment_composite_candidate(
                            left_row_records,
                            right_row_records,
                            left_primary_key=join_left_key,
                            right_primary_key=join_right_key,
                            left_secondary_keys=left_pair_keys,
                            right_secondary_keys=right_pair_keys,
                        )

                        has_metrics = bool(composite_metrics)
                        overlap_count_hint = int(composite_metrics.get("overlap_count") or 0) if has_metrics else 0
                        overlap_ratio_hint = float(composite_metrics.get("overlap_ratio") or 0.0) if has_metrics else 0.0
                        duplicate_pressure_hint = float(composite_metrics.get("duplicate_pressure") or 0.0) if has_metrics else 1.0

                        metric_score = float(composite_metrics.get("score") or 0.0) if has_metrics else -60.0
                        priority = float(first.get("priority") or 0.0) + float(second.get("priority") or 0.0) + 3.0
                        synergy_bonus = 0.0
                        if overlap_count_hint >= 2:
                            synergy_bonus += min(6.0, float(overlap_count_hint))
                        if overlap_ratio_hint >= 0.5:
                            synergy_bonus += 2.0
                        total_score = priority + metric_score + synergy_bonus

                        scored_candidates.append(
                            {
                                "source": "pair",
                                "group": "",
                                "left_keys": left_pair_keys,
                                "right_keys": right_pair_keys,
                                "left_fields": [
                                    str((first.get("left_fields") or [left_pair_keys[0]])[0]),
                                    str((second.get("left_fields") or [left_pair_keys[1]])[0]),
                                ],
                                "right_fields": [
                                    str((first.get("right_fields") or [right_pair_keys[0]])[0]),
                                    str((second.get("right_fields") or [right_pair_keys[1]])[0]),
                                ],
                                "display_fields": [
                                    str((first.get("display_fields") or [left_pair_keys[0]])[0]),
                                    str((second.get("display_fields") or [left_pair_keys[1]])[0]),
                                ],
                                "priority": round(priority, 3),
                                "metrics": composite_metrics,
                                "overlap_count_hint": overlap_count_hint,
                                "overlap_ratio_hint": overlap_ratio_hint,
                                "duplicate_pressure_hint": duplicate_pressure_hint,
                                "total_score": round(total_score, 3),
                            }
                        )

                scored_candidates.sort(
                    key=lambda item: (
                        -int(item.get("overlap_count_hint") or 0),
                        -float(item.get("overlap_ratio_hint") or 0.0),
                        float(item.get("duplicate_pressure_hint") or 1.0),
                        -len(list(item.get("left_keys") or [])),
                        -float(item.get("total_score") or 0.0),
                        str("+".join(str(x) for x in (item.get("display_fields") or []))),
                    )
                )

                if scored_candidates:
                    best_candidate = scored_candidates[0]
                    composite_pair_gain_note = ""

                    if str(best_candidate.get("source") or "") == "pair":
                        best_unit_candidate = next(
                            (
                                item
                                for item in scored_candidates
                                if str(item.get("source") or "") != "pair"
                            ),
                            None,
                        )
                        if isinstance(best_unit_candidate, dict):
                            pair_overlap = int(best_candidate.get("overlap_count_hint") or 0)
                            pair_ratio = float(best_candidate.get("overlap_ratio_hint") or 0.0)
                            pair_pressure = float(best_candidate.get("duplicate_pressure_hint") or 1.0)
                            unit_overlap = int(best_unit_candidate.get("overlap_count_hint") or 0)
                            unit_ratio = float(best_unit_candidate.get("overlap_ratio_hint") or 0.0)
                            unit_pressure = float(best_unit_candidate.get("duplicate_pressure_hint") or 1.0)

                            pair_has_gain = False
                            if unit_overlap <= 0 and pair_overlap > 0:
                                pair_has_gain = True
                            elif pair_overlap >= unit_overlap + 1:
                                pair_has_gain = True
                            elif pair_ratio >= unit_ratio + 0.12:
                                pair_has_gain = True
                            elif pair_pressure + 0.12 <= unit_pressure:
                                pair_has_gain = True

                            if pair_has_gain:
                                gain_parts: List[str] = []
                                if pair_overlap > unit_overlap:
                                    gain_parts.append(f"鍛戒腑 {pair_overlap}>{unit_overlap}")
                                if pair_ratio > unit_ratio:
                                    gain_parts.append(
                                        f"瑕嗙洊 {int(round(pair_ratio * 100))}%>{int(round(unit_ratio * 100))}%"
                                    )
                                if pair_pressure < unit_pressure:
                                    gain_parts.append(
                                        f"閲嶅鍘嬪姏 {int(round(pair_pressure * 100))}%<{int(round(unit_pressure * 100))}%"
                                    )
                                composite_pair_gain_note = "；".join(gain_parts)
                            else:
                                best_candidate = best_unit_candidate

                    overlap_count_hint = int(best_candidate.get("overlap_count_hint") or 0)
                    overlap_ratio_hint = float(best_candidate.get("overlap_ratio_hint") or 0.0)
                    duplicate_pressure_hint = float(best_candidate.get("duplicate_pressure_hint") or 0.0)
                    best_left_keys = [str(x).strip() for x in (best_candidate.get("left_keys") or []) if str(x).strip()]
                    best_right_keys = [str(x).strip() for x in (best_candidate.get("right_keys") or []) if str(x).strip()]
                    best_left_fields = [str(x).strip() for x in (best_candidate.get("left_fields") or []) if str(x).strip()]
                    best_right_fields = [str(x).strip() for x in (best_candidate.get("right_fields") or []) if str(x).strip()]
                    best_display_fields = [str(x).strip() for x in (best_candidate.get("display_fields") or []) if str(x).strip()]

                    if (overlap_count_hint >= 2 or overlap_ratio_hint >= 0.34) and best_left_keys and best_right_keys:
                        join_secondary_left_key = best_left_keys[0]
                        join_secondary_right_key = best_right_keys[0]
                        join_secondary_key = join_secondary_left_key
                        join_secondary_left_field = best_left_fields[0] if best_left_fields else str(field_display.get(join_secondary_left_key) or join_secondary_left_key)
                        join_secondary_right_field = best_right_fields[0] if best_right_fields else str(field_display.get(join_secondary_right_key) or join_secondary_right_key)
                        join_secondary_field = best_display_fields[0] if best_display_fields else (join_secondary_left_field or join_secondary_right_field)

                        if len(best_left_keys) >= 2 and len(best_right_keys) >= 2:
                            join_tertiary_left_key = best_left_keys[1]
                            join_tertiary_right_key = best_right_keys[1]
                            join_tertiary_key = join_tertiary_left_key
                            join_tertiary_left_field = best_left_fields[1] if len(best_left_fields) >= 2 else str(field_display.get(join_tertiary_left_key) or join_tertiary_left_key)
                            join_tertiary_right_field = best_right_fields[1] if len(best_right_fields) >= 2 else str(field_display.get(join_tertiary_right_key) or join_tertiary_right_key)
                            join_tertiary_field = best_display_fields[1] if len(best_display_fields) >= 2 else (join_tertiary_left_field or join_tertiary_right_field)

                        composite_enabled = bool(join_secondary_left_key and join_secondary_right_key)
                        composite_overlap_hint = overlap_count_hint
                        composite_duplicate_pressure_hint = duplicate_pressure_hint
                        composite_secondary_source = str(best_candidate.get("source") or "")
                        composite_secondary_count = 2 if join_tertiary_left_key and join_tertiary_right_key else 1
                        if composite_secondary_source == "pair" and composite_pair_gain_note:
                            if fallback_reason:
                                fallback_reason = f"{fallback_reason} 鍙屾閿鐩婏細{composite_pair_gain_note}銆?"
                            else:
                                fallback_reason = f"鍙屾閿鐩婏細{composite_pair_gain_note}銆?"

                if composite_enabled:
                    key_parts: List[str] = [join_field, join_secondary_field]
                    if join_tertiary_field:
                        key_parts.append(join_tertiary_field)
                    composite_note = f"复合键“{'+'.join([part for part in key_parts if part])}”"
                    hit_note = f"（预计命中 {composite_overlap_hint}）" if composite_overlap_hint > 0 else ""
                    if fallback_reason:
                        fallback_reason = f"{fallback_reason} 已启用 {composite_note}{hit_note} 降低弱键歧义。"
                    else:
                        fallback_reason = f"已启用 {composite_note}{hit_note} 降低弱键歧义。"

            left_rows = int(left.get("rows_estimate") or 0)
            right_rows = int(right.get("rows_estimate") or 0)
            rows_ratio = 0.0
            if left_rows > 0 and right_rows > 0:
                rows_ratio = float(min(left_rows, right_rows)) / float(max(left_rows, right_rows))

            if match_mode == "exact":
                shared_bonus = min(len(ranked_shared), 3) * 0.05
                key_coverage = float(int(field_counter.get(join_key) or 1)) / float(max(2, table_count))
                base = 0.60 + shared_bonus + min(0.2, key_coverage * 0.2)
                confidence = min(0.98, max(0.55, base + rows_ratio * 0.08))
            else:
                group_key = semantic_group or _match_attachment_field_group(join_key, semantic_resources=resources)
                group_coverage = float(int(field_group_counter.get(group_key) or 1)) / float(max(2, table_count))
                base = 0.54 + min(0.22, group_coverage * 0.24)
                confidence = min(0.93, max(0.5, base + rows_ratio * 0.08))

            if not shared_fields:
                if match_mode == "exact":
                    shared_fields = [str(field_display.get(k) or k) for k in ranked_shared[:3]]
                else:
                    for token in (join_left_field, join_right_field):
                        t = str(token or "").strip()
                        if t and t not in shared_fields:
                            shared_fields.append(t)

            if alignment_tier == "weak_fallback":
                if composite_enabled:
                    hint_bonus = min(0.06, float(composite_overlap_hint) * 0.01)
                    hint_penalty = min(0.08, float(composite_duplicate_pressure_hint) * 0.12)
                    confidence = min(0.88, max(0.40, confidence - 0.06 + hint_bonus - hint_penalty))
                else:
                    confidence = min(0.78, max(0.30, confidence - 0.16))
            elif alignment_tier == "primary_semantic" and fallback_reason:
                confidence = min(0.95, confidence + 0.02)

            suggestions.append(
                {
                    "left_index": int(left.get("index") or 0),
                    "right_index": int(right.get("index") or 0),
                    "left_anchor": str(left.get("anchor") or ""),
                    "right_anchor": str(right.get("anchor") or ""),
                    "left_filename": str(left.get("filename") or ""),
                    "right_filename": str(right.get("filename") or ""),
                    "match_mode": match_mode,
                    "semantic_group": semantic_group,
                    "join_field": str(join_field or "涓婚敭瀛楁"),
                    "join_key": str(join_key or ""),
                    "join_left_key": str(join_left_key or join_key or ""),
                    "join_right_key": str(join_right_key or join_key or ""),
                    "join_left_field": str(join_left_field or join_field or ""),
                    "join_right_field": str(join_right_field or join_field or ""),
                    "join_secondary_key": str(join_secondary_key or ""),
                    "join_secondary_left_key": str(join_secondary_left_key or ""),
                    "join_secondary_right_key": str(join_secondary_right_key or ""),
                    "join_secondary_field": str(join_secondary_field or ""),
                    "join_secondary_left_field": str(join_secondary_left_field or ""),
                    "join_secondary_right_field": str(join_secondary_right_field or ""),
                    "join_tertiary_key": str(join_tertiary_key or ""),
                    "join_tertiary_left_key": str(join_tertiary_left_key or ""),
                    "join_tertiary_right_key": str(join_tertiary_right_key or ""),
                    "join_tertiary_field": str(join_tertiary_field or ""),
                    "join_tertiary_left_field": str(join_tertiary_left_field or ""),
                    "join_tertiary_right_field": str(join_tertiary_right_field or ""),
                    "composite_enabled": bool(composite_enabled),
                    "composite_secondary_count": int(composite_secondary_count or 0),
                    "composite_overlap_hint": int(composite_overlap_hint or 0),
                    "composite_duplicate_pressure_hint": round(float(composite_duplicate_pressure_hint or 0.0), 3),
                    "composite_secondary_source": str(composite_secondary_source or ""),
                    "normalization_note": normalization_note,
                    "shared_fields": shared_fields[:3],
                    "alignment_tier": alignment_tier,
                    "fallback_reason": fallback_reason,
                    "confidence": round(confidence, 3),
                }
            )

    suggestions.sort(
        key=lambda item: (
            -float(item.get("confidence") or 0.0),
            int(item.get("left_index") or 0),
            int(item.get("right_index") or 0),
        )
    )
    return suggestions[:6]

def _build_entity_alignment_summaries(
    table_items: List[Dict[str, Any]],
    alignment_suggestions: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    if len(table_items) < 2 or not alignment_suggestions:
        return []

    table_by_index: Dict[int, Dict[str, Any]] = {}
    for item in table_items:
        try:
            idx = int(item.get("index") or 0)
        except Exception:
            idx = 0
        if idx > 0:
            table_by_index[idx] = item

    summaries: List[Dict[str, Any]] = []
    for suggestion in alignment_suggestions:
        if not isinstance(suggestion, dict):
            continue

        try:
            left_index = int(suggestion.get("left_index") or 0)
            right_index = int(suggestion.get("right_index") or 0)
        except Exception:
            continue

        left = table_by_index.get(left_index)
        right = table_by_index.get(right_index)
        if not left or not right:
            continue

        left_values_map = left.get("_entity_values") if isinstance(left.get("_entity_values"), dict) else {}
        right_values_map = right.get("_entity_values") if isinstance(right.get("_entity_values"), dict) else {}
        left_row_records = left.get("_row_records") if isinstance(left.get("_row_records"), list) else []
        right_row_records = right.get("_row_records") if isinstance(right.get("_row_records"), list) else []

        left_key = _normalize_attachment_field_key(
            suggestion.get("join_left_key") or suggestion.get("join_key") or suggestion.get("join_left_field")
        )
        right_key = _normalize_attachment_field_key(
            suggestion.get("join_right_key") or suggestion.get("join_key") or suggestion.get("join_right_field")
        )
        if not left_key or not right_key:
            continue

        left_secondary_key = _normalize_attachment_field_key(
            suggestion.get("join_secondary_left_key") or suggestion.get("join_secondary_key") or suggestion.get("join_secondary_left_field")
        )
        right_secondary_key = _normalize_attachment_field_key(
            suggestion.get("join_secondary_right_key") or suggestion.get("join_secondary_key") or suggestion.get("join_secondary_right_field")
        )
        left_tertiary_key = _normalize_attachment_field_key(
            suggestion.get("join_tertiary_left_key") or suggestion.get("join_tertiary_key") or suggestion.get("join_tertiary_left_field")
        )
        right_tertiary_key = _normalize_attachment_field_key(
            suggestion.get("join_tertiary_right_key") or suggestion.get("join_tertiary_key") or suggestion.get("join_tertiary_right_field")
        )

        left_secondary_keys: List[str] = [key for key in (left_secondary_key, left_tertiary_key) if key]
        right_secondary_keys: List[str] = [key for key in (right_secondary_key, right_tertiary_key) if key]
        composite_enabled = bool(
            left_secondary_keys
            and right_secondary_keys
            and len(left_secondary_keys) == len(right_secondary_keys)
        )

        left_values = {
            str(x).strip()
            for x in (left_values_map.get(left_key) or [])
            if str(x or "").strip()
        }
        right_values = {
            str(x).strip()
            for x in (right_values_map.get(right_key) or [])
            if str(x or "").strip()
        }
        if not left_values or not right_values:
            continue

        overlap_values = sorted(left_values & right_values)
        overlap_count = len(overlap_values)
        if overlap_count <= 0:
            continue

        left_count = len(left_values)
        right_count = len(right_values)
        min_count = max(1, min(left_count, right_count))

        overlap_ratio = float(overlap_count) / float(min_count)
        left_hit_ratio = float(overlap_count) / float(max(1, left_count))
        right_hit_ratio = float(overlap_count) / float(max(1, right_count))

        left_record_map: Dict[str, Dict[str, str]] = {}
        left_join_total = 0
        for raw_record in left_row_records:
            if not isinstance(raw_record, dict):
                continue
            record = {
                _normalize_attachment_field_key(k): _normalize_attachment_cell_value(v)
                for k, v in raw_record.items()
                if _normalize_attachment_field_key(k) and _normalize_attachment_cell_value(v)
            }
            join_value = str(record.get(left_key) or "").strip()
            if not join_value:
                continue
            left_join_total += 1
            if join_value in left_record_map:
                continue
            left_record_map[join_value] = record

        right_record_map: Dict[str, Dict[str, str]] = {}
        right_join_total = 0
        for raw_record in right_row_records:
            if not isinstance(raw_record, dict):
                continue
            record = {
                _normalize_attachment_field_key(k): _normalize_attachment_cell_value(v)
                for k, v in raw_record.items()
                if _normalize_attachment_field_key(k) and _normalize_attachment_cell_value(v)
            }
            join_value = str(record.get(right_key) or "").strip()
            if not join_value:
                continue
            right_join_total += 1
            if join_value in right_record_map:
                continue
            right_record_map[join_value] = record

        left_duplicate_ratio = 0.0
        if left_join_total > 0:
            left_duplicate_ratio = max(0.0, 1.0 - (float(len(left_record_map)) / float(left_join_total)))
        right_duplicate_ratio = 0.0
        if right_join_total > 0:
            right_duplicate_ratio = max(0.0, 1.0 - (float(len(right_record_map)) / float(right_join_total)))
        duplicate_pressure = max(left_duplicate_ratio, right_duplicate_ratio)

        composite_overlap_count = 0
        composite_reason_note = ""
        join_secondary_field = str(suggestion.get("join_secondary_field") or "").strip()
        join_tertiary_field = str(suggestion.get("join_tertiary_field") or "").strip()
        if composite_enabled:
            composite_metrics = _evaluate_attachment_composite_candidate(
                left_row_records,
                right_row_records,
                left_primary_key=left_key,
                right_primary_key=right_key,
                left_secondary_keys=left_secondary_keys,
                right_secondary_keys=right_secondary_keys,
            )

            if composite_metrics:
                left_profile = composite_metrics.get("left_profile") if isinstance(composite_metrics.get("left_profile"), dict) else {}
                right_profile = composite_metrics.get("right_profile") if isinstance(composite_metrics.get("right_profile"), dict) else {}
                left_composite_map = left_profile.get("record_map") if isinstance(left_profile.get("record_map"), dict) else {}
                right_composite_map = right_profile.get("record_map") if isinstance(right_profile.get("record_map"), dict) else {}

                if left_composite_map and right_composite_map:
                    left_record_map = left_composite_map
                    right_record_map = right_composite_map

                    left_values = set(left_composite_map.keys())
                    right_values = set(right_composite_map.keys())
                    overlap_values = [str(x).strip() for x in (composite_metrics.get("overlap_values") or []) if str(x or "").strip()]
                    if not overlap_values:
                        overlap_values = sorted(left_values & right_values)

                    composite_overlap_count = int(composite_metrics.get("overlap_count") or len(overlap_values))
                    overlap_count = composite_overlap_count

                    left_count = len(left_values)
                    right_count = len(right_values)
                    min_count = max(1, min(left_count, right_count))

                    overlap_ratio = float(composite_metrics.get("overlap_ratio") or 0.0)
                    left_hit_ratio = float(composite_metrics.get("left_hit_ratio") or 0.0)
                    right_hit_ratio = float(composite_metrics.get("right_hit_ratio") or 0.0)
                    left_duplicate_ratio = float(composite_metrics.get("left_duplicate_ratio") or left_duplicate_ratio)
                    right_duplicate_ratio = float(composite_metrics.get("right_duplicate_ratio") or right_duplicate_ratio)
                    duplicate_pressure = float(composite_metrics.get("duplicate_pressure") or duplicate_pressure)

                    primary_label = suggestion.get("join_field") or "涓婚敭"
                    key_parts: List[str] = [str(primary_label)]
                    if join_secondary_field:
                        key_parts.append(str(join_secondary_field))
                    if join_tertiary_field:
                        key_parts.append(str(join_tertiary_field))
                    composite_reason_note = f"琛岀骇瀵归綈閲囩敤澶嶅悎閿€{'+'.join(key_parts)}鈥?"
                else:
                    composite_enabled = False
            else:
                composite_enabled = False

        conflict_fields_counter: Counter[str] = Counter()
        conflict_examples: List[str] = []
        conflict_key_count = 0

        for overlap_key in overlap_values:
            left_record = left_record_map.get(overlap_key)
            right_record = right_record_map.get(overlap_key)
            if not left_record or not right_record:
                continue

            comparable_fields = sorted(set(left_record.keys()) & set(right_record.keys()))
            row_conflict = False
            for field_key in comparable_fields:
                protected_keys = {left_key, right_key}
                if composite_enabled:
                    for protected_key in left_secondary_keys + right_secondary_keys:
                        if protected_key:
                            protected_keys.add(protected_key)
                if field_key in protected_keys:
                    continue

                left_val = str(left_record.get(field_key) or "").strip()
                right_val = str(right_record.get(field_key) or "").strip()
                if not left_val or not right_val:
                    continue
                if left_val == right_val:
                    continue

                row_conflict = True
                conflict_fields_counter[field_key] += 1
                if len(conflict_examples) < _CHAT_ATTACHMENT_TABLE_ENTITY_MAX_PREVIEW:
                    conflict_examples.append(f"{overlap_key}:{field_key}({left_val}!={right_val})")

            if row_conflict:
                conflict_key_count += 1

        conflict_ratio = float(conflict_key_count) / float(max(1, overlap_count))
        conflict_fields = [key for key, _count in conflict_fields_counter.most_common(3)]

        if overlap_count >= 5 or overlap_ratio >= 0.65:
            evidence_level = "high"
        elif overlap_count >= 2 and overlap_ratio >= 0.35:
            evidence_level = "medium"
        else:
            evidence_level = "low"

        if conflict_key_count <= 0:
            conflict_level = "none"
        elif conflict_ratio >= 0.6 or conflict_key_count >= 3:
            conflict_level = "high"
        elif conflict_ratio >= 0.3 or conflict_key_count >= 2:
            conflict_level = "medium"
        else:
            conflict_level = "low"

        entity_confidence = min(
            0.99,
            max(
                0.32,
                0.42 + overlap_ratio * 0.36 + min(0.18, overlap_count * 0.03),
            ),
        )
        alignment_tier = str(suggestion.get("alignment_tier") or "primary_exact")
        fallback_reason = str(suggestion.get("fallback_reason") or "").strip()
        if composite_reason_note:
            if fallback_reason:
                if composite_reason_note not in fallback_reason:
                    fallback_reason = f"{fallback_reason} {composite_reason_note}銆?"
            else:
                fallback_reason = composite_reason_note

        if alignment_tier == "weak_fallback":
            if duplicate_pressure >= 0.4:
                evidence_level = "low"
                if composite_enabled and composite_overlap_count > 0:
                    entity_confidence = min(0.72, max(0.26, entity_confidence - 0.12))
                else:
                    entity_confidence = min(0.66, max(0.22, entity_confidence - 0.16))
                duplicate_note = f"寮遍敭閲嶅鍊艰緝楂橈紙L{int(round(left_duplicate_ratio * 100))}%/R{int(round(right_duplicate_ratio * 100))}%锛?"
                if fallback_reason:
                    fallback_reason = f"{fallback_reason} {duplicate_note}銆?"
                else:
                    fallback_reason = duplicate_note
            elif duplicate_pressure >= 0.2:
                if composite_enabled and composite_overlap_count > 0:
                    entity_confidence = min(0.78, max(0.30, entity_confidence - 0.07))
                else:
                    entity_confidence = min(0.74, max(0.26, entity_confidence - 0.10))
                duplicate_note = f"寮遍敭瀛樺湪閲嶅鍘嬪姏锛圠{int(round(left_duplicate_ratio * 100))}%/R{int(round(right_duplicate_ratio * 100))}%锛?"
                if fallback_reason:
                    fallback_reason = f"{fallback_reason} {duplicate_note}銆?"
                else:
                    fallback_reason = duplicate_note
            else:
                if composite_enabled and composite_overlap_count > 0:
                    entity_confidence = min(0.84, max(0.34, entity_confidence - 0.03))
                else:
                    entity_confidence = min(0.78, max(0.28, entity_confidence - 0.08))

        summaries.append(
            {
                "left_index": left_index,
                "right_index": right_index,
                "left_anchor": str(suggestion.get("left_anchor") or f"附件{left_index}"),
                "right_anchor": str(suggestion.get("right_anchor") or f"附件{right_index}"),
                "join_field": str(suggestion.get("join_field") or "涓婚敭瀛楁"),
                "join_left_key": left_key,
                "join_right_key": right_key,
                "join_secondary_key": str(left_secondary_key or ""),
                "join_secondary_left_key": str(left_secondary_key or ""),
                "join_secondary_right_key": str(right_secondary_key or ""),
                "join_secondary_field": join_secondary_field,
                "join_tertiary_key": str(left_tertiary_key or ""),
                "join_tertiary_left_key": str(left_tertiary_key or ""),
                "join_tertiary_right_key": str(right_tertiary_key or ""),
                "join_tertiary_field": join_tertiary_field,
                "composite_enabled": bool(composite_enabled and composite_overlap_count > 0),
                "composite_secondary_count": int(len(left_secondary_keys) if (composite_enabled and composite_overlap_count > 0) else 0),
                "composite_overlap_count": int(composite_overlap_count or 0),
                "match_mode": str(suggestion.get("match_mode") or "exact"),
                "semantic_group": str(suggestion.get("semantic_group") or ""),
                "alignment_tier": alignment_tier,
                "fallback_reason": fallback_reason,
                "left_key_duplicate_ratio": round(left_duplicate_ratio, 3),
                "right_key_duplicate_ratio": round(right_duplicate_ratio, 3),
                "duplicate_pressure": round(duplicate_pressure, 3),
                "overlap_count": overlap_count,
                "left_key_values": left_count,
                "right_key_values": right_count,
                "overlap_ratio": round(overlap_ratio, 3),
                "left_hit_ratio": round(left_hit_ratio, 3),
                "right_hit_ratio": round(right_hit_ratio, 3),
                "coverage_basis": "min(left,right)",
                "coverage_basis_count": min_count,
                "overlap_examples": overlap_values[:_CHAT_ATTACHMENT_TABLE_ENTITY_MAX_PREVIEW],
                "conflict_key_count": conflict_key_count,
                "conflict_ratio": round(conflict_ratio, 3),
                "conflict_fields": conflict_fields,
                "conflict_examples": conflict_examples[:_CHAT_ATTACHMENT_TABLE_ENTITY_MAX_PREVIEW],
                "conflict_level": conflict_level,
                "evidence_level": evidence_level,
                "confidence": round(entity_confidence, 3),
            }
        )

    summaries.sort(
        key=lambda item: (
            -float(item.get("overlap_ratio") or 0.0),
            -int(item.get("overlap_count") or 0),
            int(item.get("left_index") or 0),
            int(item.get("right_index") or 0),
        )
    )
    return summaries[:6]

def _build_attachment_structured_insights(
    attachments: List[Dict[str, Any]],
) -> Dict[str, Any]:
    semantic_resources = _load_attachment_semantic_resources()
    semantic_source = str(semantic_resources.get("dictionary_source") or "").strip()
    semantic_custom_aliases = int(semantic_resources.get("custom_alias_count") or 0)
    semantic_custom_groups = int(semantic_resources.get("custom_group_count") or 0)

    if not attachments:
        return {
            "table_attachment_count": 0,
            "common_fields": [],
            "primary_key_candidates": [],
            "alignment_suggestions": [],
            "entity_alignment_summaries": [],
            "attachment_tables": [],
            "semantic_dictionary_source": semantic_source,
            "semantic_custom_aliases": semantic_custom_aliases,
            "semantic_custom_groups": semantic_custom_groups,
        }

    table_items: List[Dict[str, Any]] = []
    field_counter: Counter[str] = Counter()
    field_group_counter: Counter[str] = Counter()
    field_display: Dict[str, str] = {}

    for idx, item in enumerate(attachments, 1):
        if not isinstance(item, dict):
            continue

        profile = _extract_attachment_table_profile(item)
        if not profile:
            continue

        raw_fields = profile.get("fields") if isinstance(profile.get("fields"), list) else []
        fields = [str(x).strip() for x in raw_fields if str(x or "").strip()][: _CHAT_ATTACHMENT_TABLE_MAX_FIELDS]
        if len(fields) < 2:
            continue

        normalized_keys: set[str] = set()
        for field in fields:
            key = _normalize_attachment_field_key(field)
            if not key:
                continue
            normalized_keys.add(key)
            if key not in field_display:
                field_display[key] = field

        group_to_key: Dict[str, str] = {}
        for key in normalized_keys:
            field_counter[key] += 1
            group = _match_attachment_field_group(key, semantic_resources=semantic_resources)
            if group and group not in group_to_key:
                group_to_key[group] = key

        for group in group_to_key:
            field_group_counter[group] += 1

        table_items.append(
            {
                "index": idx,
                "anchor": f"附件{idx}",
                "filename": str(item.get("filename") or '附件')[:80],
                "fields": fields,
                "rows_estimate": int(profile.get("rows_estimate") or 0),
                "rows_scanned": int(profile.get("rows_scanned") or 0),
                "entity_sample_rows": int(profile.get("entity_sample_rows") or 0),
                "entity_scan_mode": str(profile.get("entity_scan_mode") or "sampled"),
                "header_line": int(profile.get("header_line") or 0),
                "repeated_header_skipped": int(profile.get("repeated_header_skipped") or 0),
                "page_marker_skipped": int(profile.get("page_marker_skipped") or 0),
                "layout_candidate_used": bool(profile.get("layout_candidate_used")),
                "layout_candidate_rows": int(profile.get("layout_candidate_rows") or 0),
                "layout_candidate_block_count": int(profile.get("layout_candidate_block_count") or 0),
                "layout_candidate_gridline_used": bool(profile.get("layout_candidate_gridline_used")),
                "layout_candidate_grid_columns": int(profile.get("layout_candidate_grid_columns") or 0),
                "delimiter": str(profile.get("delimiter") or ""),
                "sample_rows": list(profile.get("sample_rows") or [])[: _CHAT_ATTACHMENT_TABLE_MAX_SAMPLES],
                "_entity_values": dict(profile.get("entity_values") or {}),
                "_entity_preview": dict(profile.get("entity_preview") or {}),
                "_row_records": list(profile.get("row_records") or []),
                "_normalized_fields": sorted(normalized_keys),
                "_group_to_key": group_to_key,
            }
        )

    common_fields: List[str] = []
    for key, count in field_counter.most_common():
        if count < 2:
            continue
        common_fields.append(field_display.get(key, key))
        if len(common_fields) >= 6:
            break

    table_attachment_count = len(table_items)
    primary_key_candidates = _infer_primary_key_candidates(
        field_counter,
        field_display,
        field_group_counter,
        table_count=table_attachment_count,
        semantic_resources=semantic_resources,
    )
    alignment_suggestions = _build_alignment_suggestions(
        table_items,
        field_counter,
        field_display,
        field_group_counter,
        table_count=table_attachment_count,
        semantic_resources=semantic_resources,
    )
    entity_alignment_summaries = _build_entity_alignment_summaries(
        table_items,
        alignment_suggestions,
    )

    public_table_items: List[Dict[str, Any]] = []
    for item in table_items:
        cloned = dict(item)
        cloned.pop("_entity_values", None)
        cloned.pop("_entity_preview", None)
        cloned.pop("_row_records", None)
        cloned.pop("_normalized_fields", None)
        cloned.pop("_group_to_key", None)
        public_table_items.append(cloned)

    return {
        "table_attachment_count": table_attachment_count,
        "common_fields": common_fields,
        "primary_key_candidates": primary_key_candidates,
        "alignment_suggestions": alignment_suggestions,
        "entity_alignment_summaries": entity_alignment_summaries,
        "attachment_tables": public_table_items,
        "semantic_dictionary_source": semantic_source,
        "semantic_custom_aliases": semantic_custom_aliases,
        "semantic_custom_groups": semantic_custom_groups,
    }


def _normalize_chat_attachments(
    raw_attachments: List[Dict[str, Any]],
    *,
    max_items: int = _CHAT_ATTACHMENT_MAX_ITEMS,
    max_item_chars: int = _CHAT_ATTACHMENT_MAX_ITEM_CHARS,
    max_total_chars: int = _CHAT_ATTACHMENT_MAX_TOTAL_CHARS,
) -> List[Dict[str, Any]]:
    if not isinstance(raw_attachments, list):
        return []

    normalized: List[Dict[str, Any]] = []
    total_chars = 0

    for item in raw_attachments:
        if not isinstance(item, dict):
            continue

        filename = str(item.get("filename") or item.get("name") or '附件').strip()[:80]
        file_type = str(item.get("file_type") or item.get("type") or "").strip()[:80]
        status = str(item.get("status") or "").strip().lower()[:32]
        summary = str(item.get("summary") or "").replace("\x00", "").strip()[:200]
        content = str(item.get("content") or "").replace("\x00", "").strip()

        attachment_id = str(item.get("id") or "").strip()[:64]
        citation_tag = str(item.get("citation_tag") or "").strip().upper()[:20]
        parser = str(item.get("parser") or "").strip()[:40]
        layout_source = str(item.get("layout_source") or "").strip().lower()[:16]
        if layout_source not in {"markdown", "ruled", "space"}:
            layout_source = ""
        extracted_at = str(item.get("extracted_at") or "").strip()[:64]
        vision_engine = str(item.get("vision_engine") or "").strip().lower()[:32]
        vision_fallback_reason = str(item.get("vision_fallback_reason") or "").strip().lower()[:64]
        vision_warning = str(item.get("vision_warning") or "").replace("\x00", "").strip()[:220]
        vision_is_degraded = bool(item.get("vision_is_degraded"))
        if not vision_is_degraded and vision_warning:
            vision_is_degraded = True
        if not vision_is_degraded and vision_engine in {"ocr-fallback", "fallback-ocr"}:
            vision_is_degraded = True

        raw_notices = item.get("notices")
        if isinstance(raw_notices, list):
            notice_text = "；".join(str(x).strip() for x in raw_notices if str(x or "").strip())[:220]
        else:
            notice_text = str(raw_notices or "").strip()[:220]

        if not citation_tag and attachment_id:
            citation_tag = f"ATT-{attachment_id[:8].upper()}"

        if not filename:
            filename = '附件'

        if content and max_total_chars > 0 and max_item_chars > 0:
            remain = max_total_chars - total_chars
            if remain <= 0:
                content = ""
                if not notice_text:
                    notice_text = '内容较长，已截断用于上下文?'
            else:
                content_limit = min(max_item_chars, remain)
                if len(content) > content_limit:
                    content = content[:content_limit]
                    if notice_text:
                        if "宸叉埅鏂敤浜庝笂涓嬫枃" not in notice_text:
                            notice_text = f"{notice_text}锛涘唴瀹硅緝闀匡紝宸叉埅鏂敤浜庝笂涓嬫枃銆?"
                    else:
                        notice_text = '内容较长，已截断用于上下文?'
                total_chars += len(content)
        elif content:
            content = ""

        normalized.append(
            {
                "id": attachment_id,
                "filename": filename,
                "file_type": file_type,
                "status": status,
                "summary": summary,
                "content": content,
                "citation_tag": citation_tag,
                "parser": parser,
                "layout_source": layout_source,
                "extracted_at": extracted_at,
                "notice": notice_text,
                "vision_engine": vision_engine,
                "vision_fallback_reason": vision_fallback_reason,
                "vision_warning": vision_warning,
                "vision_is_degraded": vision_is_degraded,
            }
        )

        if len(normalized) >= max(1, int(max_items)):
            break

    return normalized


def _build_attachment_context(
    attachments: List[Dict[str, Any]],
    *,
    attachment_structured: Optional[Dict[str, Any]] = None,
) -> str:
    if not attachments:
        return ""

    structured = attachment_structured if isinstance(attachment_structured, dict) else _build_attachment_structured_insights(attachments)
    table_attachment_count = int(structured.get("table_attachment_count") or 0)
    common_fields = (
        [str(x).strip() for x in structured.get("common_fields", []) if str(x or "").strip()]
        if isinstance(structured.get("common_fields"), list)
        else []
    )
    primary_key_candidates = (
        [str(x).strip() for x in structured.get("primary_key_candidates", []) if str(x or "").strip()]
        if isinstance(structured.get("primary_key_candidates"), list)
        else []
    )
    semantic_dictionary_source = str(structured.get("semantic_dictionary_source") or "").strip()
    semantic_custom_aliases = int(structured.get("semantic_custom_aliases") or 0)

    alignment_suggestions_raw = structured.get("alignment_suggestions") if isinstance(structured.get("alignment_suggestions"), list) else []
    alignment_suggestions: List[Dict[str, Any]] = []
    for item in alignment_suggestions_raw:
        if not isinstance(item, dict):
            continue
        alignment_suggestions.append(item)

    entity_alignment_raw = structured.get("entity_alignment_summaries") if isinstance(structured.get("entity_alignment_summaries"), list) else []
    entity_alignment_summaries: List[Dict[str, Any]] = []
    for item in entity_alignment_raw:
        if not isinstance(item, dict):
            continue
        entity_alignment_summaries.append(item)

    table_meta_by_index: Dict[int, Dict[str, Any]] = {}
    raw_tables = structured.get("attachment_tables") if isinstance(structured.get("attachment_tables"), list) else []
    for meta in raw_tables:
        if not isinstance(meta, dict):
            continue
        try:
            idx = int(meta.get("index") or 0)
        except Exception:
            idx = 0
        if idx > 0:
            table_meta_by_index[idx] = meta

    lines: List[str] = [
        "【用户上传附件解析】",
        "引用要求：若回答使用附件中的事实、数字或结论，必须在句末标注 [附件N]。",
        "若附件内容不足以支持结论，请明确说明“不足以判断”。",
    ]

    degraded_items: List[Tuple[int, Dict[str, Any]]] = []
    for idx, item in enumerate(attachments, 1):
        if not isinstance(item, dict):
            continue
        warning_text = str(item.get("vision_warning") or "").strip()
        is_degraded = bool(item.get("vision_is_degraded")) or bool(warning_text)
        engine = str(item.get("vision_engine") or "").strip().lower()
        if not is_degraded and engine in {"ocr-fallback", "fallback-ocr"}:
            is_degraded = True
        if is_degraded:
            degraded_items.append((idx, item))

    if degraded_items:
        lines.append("图像解析告警：以下附件因豆包读图不可用已切换 OCR 兜底，相关结论请提高审慎度。")
        for idx, item in degraded_items[:4]:
            filename = str(item.get("filename") or f"附件{idx}").strip() or f"附件{idx}"
            warning_text = str(item.get("vision_warning") or "").strip()
            fallback_reason = str(item.get("vision_fallback_reason") or "").strip().lower()
            reason_label = ""
            if fallback_reason.startswith("network_"):
                reason_label = "（豆包网络故障）"
            elif fallback_reason.startswith("upstream_status_"):
                reason_label = "（豆包服务异常）"
            elif fallback_reason == "config_missing":
                reason_label = "（豆包配置缺失）"
            warning_line = warning_text or "豆包读图暂不可用，已使用 OCR 兜底。"
            lines.append(f"- [附件{idx}] {filename}{reason_label}: {warning_line}")

    if table_attachment_count > 0:
        lines.append(f"结构化识别：检测到 {table_attachment_count} 份表格型附件，可优先做字段对齐分析。")
        if semantic_dictionary_source:
            if semantic_custom_aliases > 0:
                lines.append(f"字段归一词典: {semantic_dictionary_source}（扩展别名 {semantic_custom_aliases} 项）")
            else:
                lines.append(f"字段归一词典: {semantic_dictionary_source}")
        if common_fields:
            lines.append(f"跨附件共同字段候选: {'、'.join(common_fields[:6])}")
        if primary_key_candidates:
            lines.append(f"主键候选字段: {'、'.join(primary_key_candidates[:4])}")
        if alignment_suggestions:
            lines.append("主键对齐建议:")
            for suggestion in alignment_suggestions[:3]:
                left_anchor = str(suggestion.get("left_anchor") or (f"附件{int(suggestion.get('left_index') or 0)}" if suggestion.get("left_index") else "附件A")).strip()
                right_anchor = str(suggestion.get("right_anchor") or (f"附件{int(suggestion.get('right_index') or 0)}" if suggestion.get("right_index") else "附件B")).strip()
                join_field = str(suggestion.get("join_field") or "主键字段").strip()
                confidence = float(suggestion.get("confidence") or 0.0)
                score_text = f"（匹配度 {int(round(confidence * 100))}%）" if confidence > 0 else ""

                alias_note = str(suggestion.get("normalization_note") or "").strip()
                if not alias_note and str(suggestion.get("match_mode") or "") == "semantic":
                    left_field = str(suggestion.get("join_left_field") or "").strip()
                    right_field = str(suggestion.get("join_right_field") or "").strip()
                    if left_field and right_field and left_field != right_field:
                        alias_note = f"（字段归一: {left_field} ≈ {right_field}）"

                alignment_tier = str(suggestion.get("alignment_tier") or "").strip()
                fallback_reason = str(suggestion.get("fallback_reason") or "").strip()
                composite_enabled = bool(suggestion.get("composite_enabled"))
                secondary_field = str(suggestion.get("join_secondary_field") or "").strip()
                tertiary_field = str(suggestion.get("join_tertiary_field") or "").strip()
                composite_overlap_hint = int(suggestion.get("composite_overlap_hint") or 0)
                composite_secondary_source = str(suggestion.get("composite_secondary_source") or "").strip()
                composite_note = ""
                if composite_enabled:
                    key_parts = [join_field]
                    if secondary_field:
                        key_parts.append(secondary_field)
                    if tertiary_field:
                        key_parts.append(tertiary_field)
                    if len(key_parts) >= 2:
                        composite_note = f"（复合键: {'+'.join(key_parts)}）"
                    else:
                        composite_note = "（复合键）"
                    if composite_overlap_hint > 0:
                        composite_note += f"（预估命中 {composite_overlap_hint}）"
                    if composite_secondary_source == "pair":
                        composite_note += "（双次键）"
                tier_note = ""
                if alignment_tier == "weak_fallback":
                    if fallback_reason:
                        tier_note = f"；弱键回退: {fallback_reason}"
                    else:
                        tier_note = "；弱键回退"
                elif fallback_reason and alignment_tier == "primary_semantic":
                    tier_note = f"；{fallback_reason}"

                lines.append(f"- {left_anchor} -> {right_anchor}: 建议按“{join_field}”对齐{score_text}{alias_note}{composite_note}{tier_note}")

        if entity_alignment_summaries:
            lines.append("行级实体对齐预览:")
            for summary in entity_alignment_summaries[:3]:
                left_anchor = str(summary.get("left_anchor") or (f"附件{int(summary.get('left_index') or 0)}" if summary.get("left_index") else "附件A")).strip()
                right_anchor = str(summary.get("right_anchor") or (f"附件{int(summary.get('right_index') or 0)}" if summary.get("right_index") else "附件B")).strip()
                join_field = str(summary.get("join_field") or "主键字段").strip()
                overlap_count = int(summary.get("overlap_count") or 0)
                left_values = int(summary.get("left_key_values") or 0)
                right_values = int(summary.get("right_key_values") or 0)
                overlap_ratio = float(summary.get("overlap_ratio") or 0.0)
                left_hit_ratio = float(summary.get("left_hit_ratio") or 0.0)
                right_hit_ratio = float(summary.get("right_hit_ratio") or 0.0)
                coverage_basis_count = int(summary.get("coverage_basis_count") or 0)

                ratio_parts: List[str] = []
                if overlap_ratio > 0:
                    ratio_parts.append(f"最小基数覆盖 {int(round(overlap_ratio * 100))}%")
                if left_hit_ratio > 0 or right_hit_ratio > 0:
                    ratio_parts.append(f"双侧覆盖 L{int(round(left_hit_ratio * 100))}%/R{int(round(right_hit_ratio * 100))}%")

                size_text = f"{left_values}/{right_values}"
                if coverage_basis_count > 0:
                    size_text += f"（基数 {coverage_basis_count}）"
                if ratio_parts:
                    size_text += f"（{'；'.join(ratio_parts)}）"

                examples = [str(x).strip() for x in summary.get("overlap_examples", []) if str(x or "").strip()] if isinstance(summary.get("overlap_examples"), list) else []
                example_text = f"；示例键值: {'、'.join(examples[:3])}" if examples else ""

                conflict_key_count = int(summary.get("conflict_key_count") or 0)
                conflict_ratio = float(summary.get("conflict_ratio") or 0.0)
                conflict_fields = [str(x).strip() for x in summary.get("conflict_fields", []) if str(x or "").strip()] if isinstance(summary.get("conflict_fields"), list) else []
                conflict_examples = [str(x).strip() for x in summary.get("conflict_examples", []) if str(x or "").strip()] if isinstance(summary.get("conflict_examples"), list) else []
                alignment_tier = str(summary.get("alignment_tier") or "").strip()
                fallback_reason = str(summary.get("fallback_reason") or "").strip()
                duplicate_pressure = float(summary.get("duplicate_pressure") or 0.0)
                composite_enabled = bool(summary.get("composite_enabled"))
                composite_overlap_count = int(summary.get("composite_overlap_count") or 0)
                secondary_field = str(summary.get("join_secondary_field") or "").strip()
                tertiary_field = str(summary.get("join_tertiary_field") or "").strip()

                conflict_text = ""
                if conflict_key_count > 0:
                    conflict_parts: List[str] = [f"冲突键值 {conflict_key_count} 个"]
                    if conflict_ratio > 0:
                        conflict_parts.append(f"冲突占比 {int(round(conflict_ratio * 100))}%")
                    if conflict_fields:
                        conflict_parts.append(f"冲突字段: {'、'.join(conflict_fields[:2])}")
                    if conflict_examples:
                        conflict_parts.append(f"冲突示例: {'、'.join(conflict_examples[:2])}")
                    conflict_text = "；" + "；".join(conflict_parts)

                dup_text = ""
                if duplicate_pressure >= 0.2:
                    dup_text = f"；弱键重复压力 {int(round(duplicate_pressure * 100))}%"

                composite_text = ""
                if composite_enabled:
                    key_parts = [join_field]
                    if secondary_field:
                        key_parts.append(secondary_field)
                    if tertiary_field:
                        key_parts.append(tertiary_field)
                    if len(key_parts) >= 2:
                        composite_text = f"；复合键 {'+'.join(key_parts)} 命中 {composite_overlap_count} 个"
                    else:
                        composite_text = f"；复合键命中 {composite_overlap_count} 个"

                tier_text = ""
                if alignment_tier == "weak_fallback":
                    tier_text = f"；弱键回退: {fallback_reason or '该对齐仅用于弱主键辅助比对'}"

                lines.append(
                    f"- {left_anchor} -> {right_anchor}: 在“{join_field}”上识别到 {overlap_count} 个重叠键值（{size_text}）{example_text}{conflict_text}{dup_text}{composite_text}{tier_text}"
                )
        elif alignment_suggestions:
            lines.append("行级实体对齐预览: 当前样本尚未识别到稳定重叠键值，建议补充更完整数据后再做合并判断。")
    lines.append("")

    for idx, item in enumerate(attachments, 1):
        filename = item.get("filename", "附件")
        file_type = item.get("file_type", "")
        summary = item.get("summary", "")
        content = item.get("content", "")
        status = item.get("status", "")
        citation_tag = item.get("citation_tag", "")
        parser = item.get("parser", "")
        notice = item.get("notice", "")

        header = f"[附件{idx}] {filename}"
        if file_type:
            header += f" ({file_type})"
        lines.append(header)

        if citation_tag:
            lines.append(f"引用标签: [{citation_tag}]")
        if parser:
            lines.append(f"解析方式: {parser}")
        layout_source = str(item.get("layout_source") or "").strip().lower()
        if layout_source in {"markdown", "ruled", "space"}:
            source_labels = {"markdown": "Markdown竖线表", "ruled": "格线/管道表", "space": "空白对齐表"}
            lines.append(f"结构候选来源: {source_labels.get(layout_source, layout_source)}")

        if summary:
            lines.append(f"摘要: {summary}")

        table_meta = table_meta_by_index.get(idx)
        if table_meta:
            fields = [str(x).strip() for x in table_meta.get("fields", []) if str(x or "").strip()] if isinstance(table_meta.get("fields"), list) else []
            if fields:
                lines.append(f"结构化字段: {'、'.join(fields[:6])}")
            rows_estimate = int(table_meta.get("rows_estimate") or 0)
            if rows_estimate > 0:
                lines.append(f"估计有效行数: {rows_estimate}")
            rows_scanned = int(table_meta.get("rows_scanned") or 0)
            entity_sample_rows = int(table_meta.get("entity_sample_rows") or 0)
            entity_scan_mode = str(table_meta.get("entity_scan_mode") or "").strip().lower()
            if rows_scanned > 0 and entity_sample_rows > 0:
                if entity_scan_mode == "full" and entity_sample_rows >= rows_scanned:
                    lines.append(f"实体扫描范围: {rows_scanned} 行（全量扫描做键值比对）")
                else:
                    lines.append(f"实体扫描范围: {rows_scanned} 行（抽样 {entity_sample_rows} 行做键值比对）")
            header_line = int(table_meta.get("header_line") or 0)
            if header_line > 1:
                lines.append(f"表头定位: 第 {header_line} 行（自动识别）")
            repeated_header_skipped = int(table_meta.get("repeated_header_skipped") or 0)
            page_marker_skipped = int(table_meta.get("page_marker_skipped") or 0)
            layout_candidate_used = bool(table_meta.get("layout_candidate_used"))
            layout_candidate_rows = int(table_meta.get("layout_candidate_rows") or 0)
            layout_candidate_block_count = int(table_meta.get("layout_candidate_block_count") or 0)
            layout_candidate_gridline_used = bool(table_meta.get("layout_candidate_gridline_used"))
            layout_candidate_grid_columns = int(table_meta.get("layout_candidate_grid_columns") or 0)
            cleanup_notes: List[str] = []
            if repeated_header_skipped > 0:
                cleanup_notes.append(f"跳过重复表头 {repeated_header_skipped} 行")
            if page_marker_skipped > 0:
                cleanup_notes.append(f"跳过分页标记 {page_marker_skipped} 行")
            if cleanup_notes:
                lines.append(f"结构清洗: {'；'.join(cleanup_notes)}")
            if layout_candidate_used:
                candidate_notes: List[str] = []
                if layout_candidate_block_count > 0:
                    candidate_notes.append(f"候选块 {layout_candidate_block_count}")
                if layout_candidate_rows > 0:
                    candidate_notes.append(f"候选行 {layout_candidate_rows}")
                if layout_candidate_gridline_used:
                    if layout_candidate_grid_columns >= 2:
                        candidate_notes.append(f"格线推断 {layout_candidate_grid_columns} 列")
                    else:
                        candidate_notes.append("格线推断")
                if candidate_notes:
                    lines.append(f"结构化候选来源: 已启用（{'；'.join(candidate_notes)}）")
                else:
                    lines.append("结构化候选来源: 已启用")
            sample_rows = [str(x).strip() for x in table_meta.get("sample_rows", []) if str(x or "").strip()] if isinstance(table_meta.get("sample_rows"), list) else []
            if sample_rows:
                lines.append(f"样例行: {' | '.join(sample_rows[:2])}")

        if content:
            lines.append(content)
        elif status and status != "parsed":
            lines.append(f"备注: 附件解析状态 {status}，建议结合用户补充说明。")

        if notice:
            lines.append(f"提示: {notice}")

        lines.append("")

    return "\n".join(lines).strip()
def _attachment_quality_stats(attachments: List[Dict[str, Any]]) -> Dict[str, int]:
    total = len(attachments)
    parsed = sum(1 for item in attachments if str(item.get("status") or "").lower() == "parsed")
    partial = sum(1 for item in attachments if str(item.get("status") or "").lower() == "partial")

    degraded_vision = 0
    vision_fallback = 0
    layout_table = 0
    layout_markdown = 0
    layout_ruled = 0
    layout_space = 0

    for item in attachments:
        warning = str(item.get("vision_warning") or "").strip()
        engine = str(item.get("vision_engine") or "").strip().lower()
        is_degraded = bool(item.get("vision_is_degraded")) or bool(warning)
        if not is_degraded and engine in {"ocr-fallback", "fallback-ocr"}:
            is_degraded = True
        if is_degraded:
            degraded_vision += 1
        if engine in {"ocr-fallback", "fallback-ocr"}:
            vision_fallback += 1

        source = str(item.get("layout_source") or "").strip().lower()
        if source in {"markdown", "ruled", "space"}:
            layout_table += 1
            if source == "markdown":
                layout_markdown += 1
            elif source == "ruled":
                layout_ruled += 1
            elif source == "space":
                layout_space += 1

    return {
        "total": total,
        "parsed": parsed,
        "partial": partial,
        "degraded_vision": degraded_vision,
        "vision_fallback": vision_fallback,
        "layout_table": layout_table,
        "layout_markdown": layout_markdown,
        "layout_ruled": layout_ruled,
        "layout_space": layout_space,
    }


def _extract_attachment_citations(reply: str, attachments: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not attachments:
        return {
            "cited_count": 0,
            "total_count": 0,
            "coverage": 0.0,
            "cited_anchors": [],
            "uncited_anchors": [],
            "cited_tags": [],
        }

    cited_numbers: set[int] = set()
    for token in _CHAT_ATTACHMENT_ANCHOR_PATTERN.findall(str(reply or "")):
        try:
            n = int(token)
        except Exception:
            continue
        if 1 <= n <= len(attachments):
            cited_numbers.add(n)

    tags_in_reply = {x.upper() for x in _CHAT_ATTACHMENT_TAG_PATTERN.findall(str(reply or ""))}
    cited_tags: List[str] = []
    for idx, item in enumerate(attachments, 1):
        tag = str(item.get("citation_tag") or "").strip().upper()
        if tag and tag in tags_in_reply:
            cited_numbers.add(idx)
            if tag not in cited_tags:
                cited_tags.append(tag)

    cited_anchors = [f"附件{idx}" for idx in sorted(cited_numbers)]
    total_count = len(attachments)
    uncited_anchors = [f"附件{idx}" for idx in range(1, total_count + 1) if idx not in cited_numbers]
    coverage = (len(cited_anchors) / total_count) if total_count else 0.0

    return {
        "cited_count": len(cited_anchors),
        "total_count": total_count,
        "coverage": round(coverage, 3),
        "cited_anchors": cited_anchors,
        "uncited_anchors": uncited_anchors,
        "cited_tags": cited_tags,
    }


def _normalize_source_time(raw_value: Any) -> str:
    if raw_value is None:
        return ""

    dt: Optional[datetime] = None
    if isinstance(raw_value, datetime):
        dt = raw_value
    elif isinstance(raw_value, (int, float)):
        ts = float(raw_value)
        if ts <= 0:
            return ""
        if ts > 10_000_000_000:  # 姣鏃堕棿鎴?
            ts /= 1000.0
        try:
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return ""
    else:
        text = str(raw_value or "").strip()
        if not text:
            return ""

        # 瀛楃涓叉椂闂存埑锛堢/姣锛?
        if re.fullmatch(r"\d{10,13}", text):
            ts = float(text)
            if len(text) >= 13:
                ts /= 1000.0
            try:
                dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            except (OverflowError, OSError, ValueError):
                dt = None

        # ISO 鏃堕棿
        if dt is None:
            candidates = [text]
            if text.endswith("Z"):
                candidates.append(f"{text[:-1]}+00:00")
            if " " in text and "T" not in text:
                candidates.append(text.replace(" ", "T", 1))
            for candidate in candidates:
                try:
                    dt = datetime.fromisoformat(candidate)
                    break
                except ValueError:
                    continue

        # RFC2822锛堝 RSS pubDate锛?
        if dt is None:
            try:
                dt = parsedate_to_datetime(text)
            except (TypeError, ValueError, IndexError):
                dt = None

        # yyyy-mm-dd / yyyy骞磎m鏈坉d鏃?
        if dt is None:
            m = re.search('(20\\d{2})[?\\-](\\d{1,2})[?\\-](\\d{1,2})', text)
            if m:
                try:
                    dt = datetime(
                        int(m.group(1)),
                        int(m.group(2)),
                        int(m.group(3)),
                        tzinfo=timezone.utc,
                    )
                except ValueError:
                    dt = None

    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_realtime_source(item: Any) -> Optional[Dict[str, str]]:
    if not isinstance(item, dict):
        return None

    title = str(item.get("title") or item.get("name") or item.get("鏍囬") or "").strip()
    url = str(item.get("url") or item.get("link") or item.get("閾炬帴") or "").strip()
    snippet = str(
        item.get("snippet")
        or item.get("body")
        or item.get("summary")
        or item.get("鎽樿")
        or item.get("desc")
        or ""
    ).strip()
    published_at_raw = (
        item.get("published_at")
        or item.get("publishedAt")
        or item.get("pubDate")
        or item.get("pub_date")
        or item.get("date")
        or item.get("time")
        or item.get('发布时间')
    )
    published_at = _normalize_source_time(published_at_raw)

    if not title and not url:
        return None

    normalized: Dict[str, str] = {
        "title": title[:120] or '未命名来?',
        "url": url[:300],
        "snippet": snippet[:220],
    }
    if published_at:
        normalized["published_at"] = published_at
    return normalized


def _merge_realtime_sources(*groups: Any, limit: int = 8) -> List[Dict[str, str]]:
    merged: List[Dict[str, str]] = []
    seen: set[str] = set()

    for group in groups:
        if not isinstance(group, list):
            continue
        for raw in group:
            src = _normalize_realtime_source(raw)
            if not src:
                continue
            key = (src.get("url") or "").strip().rstrip("/").lower()
            if not key:
                key = (src.get("title") or "").strip().lower()
            if not key:
                key = f'{src.get("title", "")}|{src.get("snippet", "")}'.strip().lower()[:120]
            if key in seen:
                continue
            seen.add(key)
            merged.append(src)
            if len(merged) >= limit:
                return merged
    return merged


def _compact_realtime_sources(results: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, str]]:
    return _merge_realtime_sources(results, limit=limit)


def _extract_sources_from_skill_result(skill_result: Dict[str, Any], limit: int = 5) -> List[Dict[str, str]]:
    if not isinstance(skill_result, dict):
        return []
    candidates: List[Any] = []
    for key in (
        "sources",
        "source_list",
        "search_sources",
        '来源明细',
        '来源列表',
        '棢索来?',
        '参来?',
        '原始结果',
        '原始结果列表',
        "results",
    ):
        value = skill_result.get(key)
        if isinstance(value, list):
            candidates.extend(value)
    return _merge_realtime_sources(candidates, limit=limit)


def _extract_search_sources_count(skill_result: Dict[str, Any], fallback: int = 0) -> int:
    if not isinstance(skill_result, dict):
        return max(0, int(fallback or 0))
    for key in ('原始结果?', '信息来源?', '数据来源?', "鍘熷鏁版嵁鏁?", "sources_count"):
        value = skill_result.get(key)
        if value is None:
            continue
        try:
            return max(0, int(float(str(value).strip())))
        except (TypeError, ValueError):
            continue
    return max(0, int(fallback or 0))


def _join_search_engines(engines: List[str], limit: int = 3) -> str:
    deduped: List[str] = []
    seen: set[str] = set()
    for engine in engines:
        token = str(engine or "").strip()
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(token)
        if len(deduped) >= limit:
            break
    return "+".join(deduped)


def _build_source_freshness_meta(
    sources: List[Dict[str, str]],
    *,
    realtime_attempted: bool,
    fallback_count: int,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    ages: List[float] = []
    for src in sources:
        iso_time = _normalize_source_time(src.get("published_at"))
        if not iso_time:
            continue
        try:
            ts = datetime.fromisoformat(iso_time.replace("Z", "+00:00"))
        except ValueError:
            continue
        delta = (now - ts).total_seconds() / 86400.0
        ages.append(max(0.0, delta))

    if ages:
        min_days = min(ages)
        max_days = max(ages)
        if min_days <= 3:
            freshness = "high"
            freshness_label = "楂樻椂鏁?"
        elif min_days <= 30:
            freshness = "medium"
            freshness_label = "涓椂鏁?"
        else:
            freshness = "low"
            freshness_label = "浣庢椂鏁?"
        note = (
            f"鏉ユ簮鏃堕棿瑕嗙洊 {len(ages)}/{max(len(sources), 1)} 鏉★紝"
            f"鏈€鏂版潵婧愮害 {int(round(min_days))} 澶╁墠銆?"
        )
        return {
            "freshness": freshness,
            "freshness_label": freshness_label,
            "note": note,
            "timed_sources": len(ages),
            "min_days": min_days,
            "max_days": max_days,
        }

    # 鏃犲彂甯冩椂闂存椂鍥為€€鍒版棫绛栫暐
    if fallback_count >= 2:
        freshness = "high"
        freshness_label = "楂樻椂鏁?"
    elif realtime_attempted:
        freshness = "medium"
        freshness_label = "涓椂鏁?"
    else:
        freshness = "low"
        freshness_label = "浣庢椂鏁?"
    return {
        "freshness": freshness,
        "freshness_label": freshness_label,
        "note": '来源未提供明确发布时间，时效等级按检索触发情况估算?',
        "timed_sources": 0,
        "min_days": None,
        "max_days": None,
    }


_CONFLICT_METRIC_HINTS: List[str] = [
    "转化率", "点击率", "毛利率", "净利率", "退货率", "退款率",
    "客单价", "ROI", "ROAS", "GMV", "销量", "价格", "佣金", "NPS", "DSR",
]


def _normalize_numeric_value(value: float, unit: str) -> tuple[str, float]:
    unit_clean = str(unit or "").strip()
    unit_low = unit_clean.lower()
    if unit_clean in {"%", "％"}:
        return "percent", value
    if unit_clean == "万元":
        return "currency", value * 10000.0
    if unit_clean == "元":
        return "currency", value
    if unit_clean == "万":
        return "count", value * 10000.0
    if unit_clean == "亿":
        return "count", value * 100000000.0
    if unit_low == "k":
        return "count", value * 1000.0
    if unit_low == "m":
        return "count", value * 1000000.0
    if unit_clean == "倍":
        return "ratio", value
    return "number", value


def _detect_source_conflicts(sources: List[Dict[str, str]]) -> Dict[str, Any]:
    if not sources:
        return {"has_conflict": False, "note": "", "points": []}

    pattern = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)\s*(%|％|万元|元|万|亿|k|K|m|M|倍)?")
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    seen_src_metric: set[str] = set()

    for src in sources:
        title = str(src.get("title") or "").strip()
        snippet = str(src.get("snippet") or "").strip()
        text = f"{title} {snippet}".strip()
        if not text:
            continue
        source_name = title or str(src.get("url") or '来源')

        metric_hint = ""
        low_text = text.lower()
        for kw in _CONFLICT_METRIC_HINTS:
            if kw.lower() in low_text:
                metric_hint = kw
                break

        for m in pattern.finditer(text):
            try:
                raw_val = float(m.group(1))
            except (TypeError, ValueError):
                continue
            unit = str(m.group(2) or "").strip()
            value_type, normalized = _normalize_numeric_value(raw_val, unit)
            bucket_key = metric_hint or f"generic:{value_type}:{unit or 'none'}"
            dedup_key = f"{source_name}|{bucket_key}"
            if dedup_key in seen_src_metric:
                continue
            seen_src_metric.add(dedup_key)
            buckets.setdefault(bucket_key, []).append(
                {
                    "source": source_name[:60],
                    "value": normalized,
                    "raw_display": f"{raw_val:g}{unit}",
                    "value_type": value_type,
                }
            )

    points: List[str] = []
    for bucket_key, values in buckets.items():
        if len(values) < 2:
            continue
        sorted_values = sorted(values, key=lambda x: x["value"])
        low = sorted_values[0]
        high = sorted_values[-1]
        low_val = float(low["value"])
        high_val = float(high["value"])
        if low_val <= 0:
            ratio = 999.0 if high_val > 0 else 1.0
        else:
            ratio = high_val / low_val
        abs_gap = high_val - low_val
        value_type = str(low["value_type"])

        if value_type == "percent":
            conflicted = abs_gap >= 5.0 and ratio >= 1.5
        elif value_type == "ratio":
            conflicted = ratio >= 1.6
        elif value_type in {"currency", "count"}:
            conflicted = abs_gap >= 10.0 and ratio >= 1.8
        else:
            conflicted = abs_gap >= 1.0 and ratio >= 1.8

        if not conflicted:
            continue

        metric_label = bucket_key if not bucket_key.startswith("generic:") else "关键数值"
        points.append(
            f"{metric_label} 在「{low['source']}」与「{high['source']}」存在明显差异"
            f"（{low['raw_display']} vs {high['raw_display']}）。"
        )
        if len(points) >= 3:
            break

    if not points:
        return {"has_conflict": False, "note": "", "points": []}

    return {
        "has_conflict": True,
        "note": "检测到来源间关键数值存在冲突，请对齐统计口径与发布时间后再决策。",
        "points": points,
    }


def _compose_credibility_snapshot(
    *,
    intent_confidence: float,
    domain_confidence: float,
    trust_level: str,
    attachment_stats: Dict[str, int],
    realtime_sources: List[Dict[str, str]],
    realtime_sources_count_hint: int,
    realtime_attempted: bool,
    has_metrics_ctx: bool,
    engine: str,
    requires_verified_sources: bool = False,
    verified_sources_ready: bool = True,
) -> Dict[str, Any]:
    normalized_sources = _merge_realtime_sources(realtime_sources, limit=8)
    realtime_sources_count = max(int(realtime_sources_count_hint or 0), len(normalized_sources))
    verified_sources_count = _count_verified_realtime_sources(normalized_sources)
    verified_sources_ready = bool(
        verified_sources_ready and (not requires_verified_sources or verified_sources_count > 0)
    )

    snapshot = _build_credibility_snapshot(
        intent_confidence=intent_confidence,
        domain_confidence=domain_confidence,
        trust_level=trust_level,
        attachment_stats=attachment_stats,
        realtime_sources_count=realtime_sources_count,
        realtime_attempted=realtime_attempted,
        has_metrics_ctx=has_metrics_ctx,
        requires_verified_sources=requires_verified_sources,
        verified_sources_ready=verified_sources_ready,
        verified_sources_count=verified_sources_count,
    )
    freshness_meta = _build_source_freshness_meta(
        normalized_sources,
        realtime_attempted=realtime_attempted,
        fallback_count=realtime_sources_count,
    )
    conflict_meta = _detect_source_conflicts(normalized_sources)

    snapshot["freshness"] = freshness_meta["freshness"]
    snapshot["freshness_label"] = freshness_meta["freshness_label"]
    snapshot["freshness_note"] = freshness_meta["note"]
    if freshness_meta.get("min_days") is not None:
        snapshot["freshness_days_min"] = round(float(freshness_meta["min_days"]), 1)
    if freshness_meta.get("max_days") is not None:
        snapshot["freshness_days_max"] = round(float(freshness_meta["max_days"]), 1)

    if requires_verified_sources and not verified_sources_ready:
        snapshot["freshness"] = "low"
        snapshot["freshness_label"] = "闇€鑱旂綉澶嶆牳"
        if not str(snapshot.get("freshness_note") or "").strip():
            snapshot["freshness_note"] = '已触发联网检索，但暂未获得可验证来源，时效与真实性需复核?'

    snapshot["engine"] = engine or ""
    snapshot["sources"] = normalized_sources[:5]
    snapshot["caliber_note"] = _build_caliber_note(
        has_metrics_ctx=has_metrics_ctx,
        realtime_sources_count=realtime_sources_count,
        attachment_stats=attachment_stats,
    )
    snapshot["conflict_note"] = conflict_meta["note"]
    snapshot["conflict_points"] = conflict_meta["points"]
    snapshot["requires_verified_sources"] = bool(requires_verified_sources)
    snapshot["verified_sources_ready"] = bool(verified_sources_ready)
    snapshot["verified_sources_count"] = int(verified_sources_count)
    snapshot["verified_sources_required_unmet"] = bool(requires_verified_sources and not verified_sources_ready)
    snapshot["guardrail_note"] = (
        _VERIFIED_SOURCE_GUARDRAIL_NOTE
        if requires_verified_sources and not verified_sources_ready
        else ""
    )

    if isinstance(snapshot.get("source_breakdown"), dict):
        snapshot["source_breakdown"]["realtime_sources"] = realtime_sources_count
        snapshot["source_breakdown"]["timed_realtime_sources"] = int(freshness_meta.get("timed_sources") or 0)
        snapshot["source_breakdown"]["has_conflict"] = bool(conflict_meta.get("has_conflict"))
        snapshot["source_breakdown"]["verified_realtime_sources"] = int(verified_sources_count)
        snapshot["source_breakdown"]["verified_required_unmet"] = bool(
            requires_verified_sources and not verified_sources_ready
        )

    return snapshot



def _build_caliber_note(
    *,
    has_metrics_ctx: bool,
    realtime_sources_count: int,
    attachment_stats: Dict[str, int],
) -> str:
    has_attachment_data = int(attachment_stats.get("parsed", 0)) > 0

    if has_metrics_ctx and realtime_sources_count > 0:
        return (
            '口径说明：已综合店铺内部指标与外部公弢来源。两者统计周期与定义可能不同?'
            '结论以方向判断为主，关键数字请按同口径二次校验?'
        )

    if has_metrics_ctx and realtime_sources_count == 0:
        return (
            '口径说明：当前结论主要基于店铺内部数据，缺少外部实时来源交叉验证?'
            '如涉及行业对标，请补充联网检紃69?'
        )

    if not has_metrics_ctx and realtime_sources_count > 0:
        return (
            '口径说明：当前结论主要基于外部公弢来源，未接入店铺内部指标?'
            '涉及ROI/利润等决策前，建议结合店内真实数据复核?'
        )

    if has_attachment_data:
        return '口径说明：当前结论依赖用户附件文本，请确认附件是否为朢新版本与完整样本?'

    return '口径说明：当前证据有限，建议补充店铺指标、附件或实时来源后再执行关键决策?'


def _count_realtime_sources(realtime_context: str) -> int:
    text = str(realtime_context or "")
    if not text:
        return 0
    matches = re.findall(r"(?m)^\[(\d+)\]", text)
    if matches:
        return len(matches)
    return min(text.count("["), 20)


def _build_credibility_snapshot(
    *,
    intent_confidence: float,
    domain_confidence: float,
    trust_level: str,
    attachment_stats: Dict[str, int],
    realtime_sources_count: int,
    realtime_attempted: bool,
    has_metrics_ctx: bool,
    requires_verified_sources: bool = False,
    verified_sources_ready: bool = True,
    verified_sources_count: int = 0,
) -> Dict[str, Any]:
    trust_score_map = {
        "HIGH": 0.95,
        "MODERATE": 0.75,
        "LOW": 0.55,
        "NONE": 0.45,
        "DEFAULT": 0.45,
    }
    trust_score = trust_score_map.get(str(trust_level or "").upper(), 0.45)

    attachment_score = 1.0 if attachment_stats.get("parsed", 0) > 0 else (0.5 if attachment_stats.get("total", 0) > 0 else 0.2)
    realtime_score = 1.0 if realtime_sources_count > 0 else (0.55 if realtime_attempted else 0.35)
    metrics_score = 1.0 if has_metrics_ctx else 0.35

    score = (
        0.30 * max(0.0, min(float(intent_confidence or 0.0), 1.0))
        + 0.15 * max(0.0, min(float(domain_confidence or 0.0), 1.0))
        + 0.20 * trust_score
        + 0.20 * attachment_score
        + 0.10 * realtime_score
        + 0.05 * metrics_score
    )
    score = max(0.3, min(score, 0.95))

    verified_required_unmet = bool(requires_verified_sources and not verified_sources_ready)
    if verified_required_unmet:
        score = min(score, 0.58)

    if score >= 0.78:
        grade = "A"
        grade_label = '高可?'
    elif score >= 0.60:
        grade = "B"
        grade_label = '中可?'
    else:
        grade = "C"
        grade_label = "闇€璋ㄦ厧"

    if verified_required_unmet:
        freshness = "low"
        freshness_label = "闇€鑱旂綉澶嶆牳"
    elif realtime_sources_count >= 2:
        freshness = "high"
        freshness_label = "楂樻椂鏁?"
    elif realtime_attempted:
        freshness = "medium"
        freshness_label = "涓椂鏁?"
    else:
        freshness = "low"
        freshness_label = "浣庢椂鏁?"

    evidence_units = 0
    if attachment_stats.get("parsed", 0) > 0:
        evidence_units += 1
    if realtime_sources_count > 0:
        evidence_units += 1
    if has_metrics_ctx:
        evidence_units += 1

    if evidence_units >= 2:
        evidence_level = "strong"
        evidence_label = '证据较充?'
    elif evidence_units == 1:
        evidence_level = "medium"
        evidence_label = '证据中等'
    else:
        evidence_level = "limited"
        evidence_label = '证据有限'

    return {
        "score": round(score, 3),
        "grade": grade,
        "grade_label": grade_label,
        "freshness": freshness,
        "freshness_label": freshness_label,
        "evidence_level": evidence_level,
        "evidence_label": evidence_label,
        "requires_verified_sources": bool(requires_verified_sources),
        "verified_sources_ready": bool(verified_sources_ready),
        "verified_sources_count": int(verified_sources_count),
        "verified_sources_required_unmet": verified_required_unmet,
        "source_breakdown": {
            "attachment_total": int(attachment_stats.get("total", 0)),
            "attachment_parsed": int(attachment_stats.get("parsed", 0)),
            "attachment_partial": int(attachment_stats.get("partial", 0)),
            "realtime_sources": int(realtime_sources_count),
            "verified_realtime_sources": int(verified_sources_count),
            "verified_required_unmet": verified_required_unmet,
            "has_metrics": bool(has_metrics_ctx),
        },
    }



# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
# 鏅鸿兘鎼滅储璋冨害 鈥?鍒ゆ柇鐢ㄦ埛娑堟伅鏄惁闇€瑕佸疄鏃朵簰鑱旂綉鏁版嵁
# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?

# 闇€瑕佸疄鏃舵悳绱㈢殑淇″彿璇嶏紙鏃舵晥鎬?澶栭儴鏁版嵁闇€姹傦級
_REALTIME_SIGNALS = {
    "最新", "最近", "当前", "现在", "今天", "今年", "本周", "这周", "本月",
    "2026", "2025", "刚刚", "最新消息", "最新情况", "最新动态", "有没有变",
    "有什么变化", "现在怎么样", "还有效吗", "还准确吗",
    "热销", "爆款", "趋势", "流行", "热门",
    "竞品", "竞争对手", "同行", "对手",
    "市场价", "行情", "均价", "报价",
    "政策", "规则", "算法", "平台规定",
    "行业数据", "行业报告", "行业均值",
    "口碑", "评价", "用户反馈",
    "搜索一下", "查一下", "找一下", "网上",
}

# 明确不需要搜索的信号（纯本地计算/数学/定义类任务）
_LOCAL_SIGNALS = {
    "帮我计算", "计算一个", "算一算", "算出来", "解释一下", "什么是", "定义",
    "我的GMV是", "我的成本是", "我的利润是",
}

_VERIFIED_SOURCE_STRONG_SIGNALS = {
    "真实数据",
    "真实联网",
    "联网数据",
    "联网搜索",
    "联网查",
    "必须联网",
    "务必联网",
    "必须真实",
    "务必真实",
    "确保真实",
    "可验证来源",
    "官方来源",
    "给出来源",
    "附来源",
    "带来源",
    "source",
    "sources",
}
_VERIFIED_SOURCE_FORCE_WORDS = {"务必", "必须", "一定", "确保", "强制", "只要", "务求"}
_VERIFIED_SOURCE_CONTEXT_WORDS = {"联网", "真实", "最新", "市场", "新闻", "政策", "行情", "外部"}
_VERIFIED_SOURCE_GUARDRAIL_NOTE = (
    "【联网真实性门禁】你已明确要求基于真实联网数据回答，"
    "但当前未获取到可验证外部来源。以下结论仅供参考，"
    "请在联网检索恢复后重试并以可验证来源复核。"
)


def _needs_real_time_data(message: str) -> bool:
    '\n    判断用户消息是否霢要实时网络搜索数据?\n    默认返回 True（绝大多数业务分析任务都受益于实时数据）?\n    仅在明确是纯本地计算/定义类任务时返回 False?\n    '
    text = message or ""
    local_hits = sum(1 for sig in _LOCAL_SIGNALS if sig in text)
    realtime_hits = sum(1 for sig in _REALTIME_SIGNALS if sig in text)

    # 鏄庣‘鏈湴璁＄畻/瀹氫箟闂锛屼笖娌℃湁鏃舵晥淇″彿 鈫?涓嶅仛鑱旂綉鎼滅储
    if local_hits >= 1 and realtime_hits == 0:
        return False
    # 鍑虹幇浠讳竴瀹炴椂/澶栭儴淇℃伅淇″彿璇?鈫?瑙﹀彂鑱旂綉鎼滅储
    if realtime_hits > 0:
        return True
    # 鏃犳槑鏄句俊鍙锋椂锛岄粯璁や笉涓诲姩鎼滅储锛堢敱 LLM 宸ュ叿璋冪敤鍏滃簳锛?
    return False


def _requires_verified_realtime_sources(message: str) -> bool:
    '\n    棢测用户是否明确要求必须基于可验证联网来源”?\n    '
    text = str(message or "").strip()
    if not text:
        return False

    text_lower = text.lower()
    if any(sig in text_lower for sig in _VERIFIED_SOURCE_STRONG_SIGNALS):
        return True

    has_force_word = any(word in text for word in _VERIFIED_SOURCE_FORCE_WORDS)
    has_context_word = any(word in text for word in _VERIFIED_SOURCE_CONTEXT_WORDS)
    return has_force_word and has_context_word


def _count_verified_realtime_sources(sources: List[Dict[str, Any]]) -> int:
    '\n    统计可验证外部来源数量（去重后）?\n    '
    if not sources:
        return 0
    seen: set[str] = set()
    count = 0
    for item in sources:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        title = str(item.get("title") or item.get("name") or "").strip()
        snippet = str(item.get("snippet") or item.get("summary") or item.get("body") or "").strip()
        if not url and not title:
            continue
        source_key = url.lower() if url else f"{title.lower()}::{snippet[:100].lower()}"
        if source_key in seen:
            continue
        seen.add(source_key)
        count += 1
    return count


_TOOL_RISK_LEVELS: tuple[str, ...] = ("L0", "L1", "L2", "L3")
_TOOL_RISK_L0_PREFIXES: tuple[str, ...] = (
    "search_",
    "query_",
    "data_",
    "web_keyword_",
)
_TOOL_RISK_L3_TOKENS: set[str] = {
    "delete", "remove", "approve", "reject", "publish", "send", "charge",
    "transfer", "sync", "webhook", "grant", "revoke", "truncate", "drop",
}
_TOOL_RISK_L2_TOKENS: set[str] = {
    "create", "update", "set", "write", "import", "export", "execute", "run",
}
_TOOL_RISK_L3_ARG_KEYS: set[str] = {
    "approve", "approved", "confirm", "confirmed", "delete", "remove", "webhook_url",
}
_TOOL_RISK_L2_ARG_KEYS: set[str] = {
    "workspace_id", "action_type", "payload", "target_id", "url",
}


def _stable_json_hash(value: Any) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        raw = str(value)
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()


def _classify_tool_risk_level(skill_name: str, args: Dict[str, Any]) -> str:
    name = str(skill_name or "").strip().lower()
    if any(name.startswith(prefix) for prefix in _TOOL_RISK_L0_PREFIXES):
        return "L0"

    tokens = [x for x in re.split(r"[^a-z0-9]+", name) if x]
    token_set = set(tokens)
    if token_set & _TOOL_RISK_L3_TOKENS:
        return "L3"
    if token_set & _TOOL_RISK_L2_TOKENS:
        return "L2"

    args_dict = args if isinstance(args, dict) else {}
    lowered_keys = {str(k or "").strip().lower() for k in args_dict.keys()}
    if lowered_keys & _TOOL_RISK_L3_ARG_KEYS:
        return "L3"
    if lowered_keys & _TOOL_RISK_L2_ARG_KEYS:
        return "L2"

    if isinstance(args_dict.get("confirm"), bool) and bool(args_dict.get("confirm")):
        return "L3"

    return "L1"


def _tool_requires_approval(
    *,
    risk_level: str,
    workspace_id: Optional[int],
    require_approval: bool,
) -> bool:
    if not require_approval:
        return False
    if int(workspace_id or 0) <= 0:
        return False
    return str(risk_level) in {"L2", "L3"}


def _build_tool_approval_block_result(skill_name: str, risk_level: str) -> Dict[str, Any]:
    return {
        "error": (
            f"宸ュ叿 {skill_name} 椋庨櫓绛夌骇 {risk_level}锛屽綋鍓嶇瓥鐣ヨ姹備汉宸ュ鎵瑰悗鎵ц銆?"
            '请在审批流程完成后重试?'
        ),
        "approval_required": True,
        "risk_level": risk_level,
        "reason": "approval_required",
    }


def _as_non_negative_int(raw: Any) -> int:
    try:
        value = int(raw)
    except Exception:
        try:
            value = int(float(raw))
        except Exception:
            return 0
    return max(0, value)


def _as_non_negative_float(raw: Any) -> float:
    try:
        value = float(raw)
    except Exception:
        return 0.0
    if value < 0:
        return 0.0
    if value == float("inf") or value == float("-inf") or value != value:
        return 0.0
    return value


def _extract_nested_float_value(payload: Any, keys: tuple[str, ...], *, depth: int = 3) -> float:
    if depth < 0 or not isinstance(payload, dict):
        return 0.0

    for key in keys:
        if key in payload:
            value = _as_non_negative_float(payload.get(key))
            if value > 0:
                return value

    for child_key in ("cost", "costs", "billing", "usage", "meta", "metadata", "stats", "metrics"):
        if child_key in payload:
            value = _extract_nested_float_value(payload.get(child_key), keys, depth=depth - 1)
            if value > 0:
                return value

    return 0.0


def _extract_nested_int_value(payload: Any, keys: tuple[str, ...], *, depth: int = 3) -> int:
    if depth < 0 or not isinstance(payload, dict):
        return 0

    for key in keys:
        if key in payload:
            value = _as_non_negative_int(payload.get(key))
            if value > 0:
                return value

    for child_key in ("usage", "meta", "metadata", "stats", "metrics"):
        if child_key in payload:
            value = _extract_nested_int_value(payload.get(child_key), keys, depth=depth - 1)
            if value > 0:
                return value

    return 0


def _extract_tool_observability_metrics(result: Dict[str, Any]) -> Dict[str, Any]:
    payload = result if isinstance(result, dict) else {}

    model_cost_usd = _extract_nested_float_value(
        payload,
        ("model_cost_usd", "llm_cost_usd", "llm_estimated_cost_usd"),
    )
    tool_cost_usd = _extract_nested_float_value(
        payload,
        ("tool_cost_usd", "tool_estimated_cost_usd"),
    )
    external_api_cost_usd = _extract_nested_float_value(
        payload,
        ("external_api_cost_usd", "external_api_estimated_cost_usd", "external_cost_usd"),
    )

    prompt_tokens = _extract_nested_int_value(payload, ("prompt_tokens", "input_tokens"))
    completion_tokens = _extract_nested_int_value(payload, ("completion_tokens", "output_tokens"))
    total_tokens = _extract_nested_int_value(payload, ("total_tokens",))
    if total_tokens <= 0 and (prompt_tokens > 0 or completion_tokens > 0):
        total_tokens = prompt_tokens + completion_tokens

    return {
        "model_cost_usd": round(model_cost_usd, 6),
        "tool_cost_usd": round(tool_cost_usd, 6),
        "external_api_cost_usd": round(external_api_cost_usd, 6),
        "prompt_tokens": int(prompt_tokens),
        "completion_tokens": int(completion_tokens),
        "total_tokens": int(total_tokens),
    }


def _empty_llm_usage_totals() -> Dict[str, int]:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }


def _merge_llm_usage_totals(totals: Dict[str, int], usage: Any) -> Dict[str, int]:
    base = totals if isinstance(totals, dict) else _empty_llm_usage_totals()
    incoming = usage if isinstance(usage, dict) else {}

    prompt_tokens = _as_non_negative_int(incoming.get("prompt_tokens"))
    if prompt_tokens <= 0:
        prompt_tokens = _as_non_negative_int(incoming.get("input_tokens"))

    completion_tokens = _as_non_negative_int(incoming.get("completion_tokens"))
    if completion_tokens <= 0:
        completion_tokens = _as_non_negative_int(incoming.get("output_tokens"))

    total_tokens = _as_non_negative_int(incoming.get("total_tokens"))
    if total_tokens <= 0 and (prompt_tokens > 0 or completion_tokens > 0):
        total_tokens = prompt_tokens + completion_tokens

    base["prompt_tokens"] = _as_non_negative_int(base.get("prompt_tokens")) + prompt_tokens
    base["completion_tokens"] = _as_non_negative_int(base.get("completion_tokens")) + completion_tokens
    base["total_tokens"] = _as_non_negative_int(base.get("total_tokens")) + total_tokens
    return base


def _estimate_llm_cost_usd(usage_totals: Dict[str, int]) -> float:
    prompt_tokens = _as_non_negative_int((usage_totals or {}).get("prompt_tokens"))
    completion_tokens = _as_non_negative_int((usage_totals or {}).get("completion_tokens"))

    # Conservative default estimation for observability when provider-side pricing is unavailable.
    prompt_rate_per_1k = 0.0005
    completion_rate_per_1k = 0.0015
    estimated = (prompt_tokens / 1000.0) * prompt_rate_per_1k + (completion_tokens / 1000.0) * completion_rate_per_1k
    return round(max(0.0, estimated), 6)


async def _log_collaboration_decision_audit_event(
    *,
    user_id: int,
    workspace_id: Optional[int],
    collaboration_mode: str,
    tier: int,
    explicit_collab_requested: bool,
    role_locked: bool,
    message: str,
    decision_reason: str,
    should_collab: bool,
    smalltalk_blocked: bool,
    support_role_count: int,
    extra_from_reply_count: int,
    dispatch_started: bool,
    dispatch_done: bool,
    dispatch_failed: bool,
    contributions_count: int,
    handoff_enabled: bool,
    llm_is_asking: bool,
) -> None:
    if int(workspace_id or 0) <= 0:
        return

    try:
        from src.core.audit import log_audit_event

        normalized_mode = _normalize_collaboration_mode(collaboration_mode)
        reason = str(decision_reason or '').strip() or ('allowed' if should_collab else 'blocked')
        await log_audit_event(
            actor_user_id=int(user_id),
            workspace_id=int(workspace_id),
            action='chat.collaboration.decision',
            target_type='collaboration',
            target_id=normalized_mode,
            status='success',
            metadata={
                'collaboration_mode': normalized_mode,
                'tier': int(tier),
                'explicit_collab_requested': bool(explicit_collab_requested),
                'role_locked': bool(role_locked),
                'message_length': int(len(str(message or '').strip())),
                'decision_reason': reason,
                'should_collab': bool(should_collab),
                'smalltalk_blocked': bool(smalltalk_blocked),
                'support_role_count': int(max(0, support_role_count)),
                'extra_from_reply_count': int(max(0, extra_from_reply_count)),
                'handoff_enabled': bool(handoff_enabled),
                'llm_is_asking': bool(llm_is_asking),
                'dispatch_started': bool(dispatch_started),
                'dispatch_done': bool(dispatch_done),
                'dispatch_failed': bool(dispatch_failed),
                'contributions_count': int(max(0, contributions_count)),
            },
        )
    except Exception as _e:
        logger.debug('collaboration audit log failed: %s', _e)


async def _log_llm_audit_event(
    *,
    user_id: int,
    workspace_id: Optional[int],
    stage: str,
    llm_calls: int,
    usage_totals: Dict[str, int],
    estimated_cost_usd: float,
) -> None:
    if int(workspace_id or 0) <= 0:
        return

    try:
        from src.core.audit import log_audit_event

        prompt_tokens = _as_non_negative_int((usage_totals or {}).get("prompt_tokens"))
        completion_tokens = _as_non_negative_int((usage_totals or {}).get("completion_tokens"))
        total_tokens = _as_non_negative_int((usage_totals or {}).get("total_tokens"))
        if total_tokens <= 0 and (prompt_tokens > 0 or completion_tokens > 0):
            total_tokens = prompt_tokens + completion_tokens

        await log_audit_event(
            actor_user_id=user_id,
            workspace_id=int(workspace_id),
            action="chat.llm.execute",
            target_type="llm",
            target_id=stage,
            status="success",
            metadata={
                "stage": stage,
                "llm_calls": int(max(1, llm_calls)),
                "prompt_tokens": int(prompt_tokens),
                "completion_tokens": int(completion_tokens),
                "total_tokens": int(total_tokens),
                "model_cost_usd": float(max(0.0, estimated_cost_usd)),
            },
        )
    except Exception as _e:
        logger.debug("llm audit log failed: %s", _e)


async def _log_tool_audit_event(
    *,
    user_id: int,
    workspace_id: Optional[int],
    skill_name: str,
    args: Dict[str, Any],
    result: Dict[str, Any],
    status: str,
    reason: str,
    risk_level: str,
    approval_required: bool,
    duration_ms: int,
) -> None:
    try:
        from src.core.audit import log_audit_event

        observability_metrics = _extract_tool_observability_metrics(result)
        await log_audit_event(
            actor_user_id=user_id,
            workspace_id=workspace_id if int(workspace_id or 0) > 0 else None,
            action="chat.tool.execute",
            target_type="skill",
            target_id=skill_name,
            status=status,
            reason=reason,
            metadata={
                "skill": skill_name,
                "risk_level": risk_level,
                "approval_required": bool(approval_required),
                "duration_ms": int(duration_ms),
                "args_hash": _stable_json_hash(args if isinstance(args, dict) else {}),
                "result_hash": _stable_json_hash(result if isinstance(result, dict) else {}),
                **observability_metrics,
            },
        )
    except Exception as _e:
        logger.debug("tool audit log failed: %s", _e)


_LONGTERM_MEMORY_MAX_ITEMS = 8
_LONGTERM_CONTEXT_MAX_ROWS = 6
_LONGTERM_SUMMARY_MAX_CHARS = 220
_LONGTERM_PROMPT_MAX_CHARS = 1100
_LONGTERM_GOAL_SIGNALS = (
    "提升", "提高", "增长", "优化", "目标", "转化", "复购", "留存", "roi", "gmv", "ctr", "cvr"
)
_LONGTERM_CONSTRAINT_SIGNALS = (
    "预算", "上限", "不超过", "周期", "天内", "成本", "库存", "人手", "时间"
)
_LONGTERM_PLATFORM_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("douyin", "抖音"),
    ("taobao", "淘宝"),
    ("tmall", "天猫"),
    ("jd", "京东"),
    ("pdd", "拼多多"),
    ("xiaohongshu", "小红书"),
    ("kuaishou", "快手"),
    ("weixin", "微信"),
)


def _normalize_memory_list(items: List[str], max_items: int = _LONGTERM_MEMORY_MAX_ITEMS) -> List[str]:
    deduped: List[str] = []
    seen: set[str] = set()
    for raw in items:
        value = re.sub(r"\s+", " ", str(raw or "").strip())
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
        if len(deduped) >= max_items:
            break
    return deduped


def _extract_sentence_candidates(
    text: str,
    *,
    signals: tuple[str, ...],
    max_items: int = 3,
    max_chars: int = 90,
) -> List[str]:
    candidates: List[str] = []
    source = str(text or "").strip()
    if not source:
        return []

    signal_tokens = tuple(str(s or "").lower() for s in signals if str(s or "").strip())
    sentences = [seg.strip() for seg in re.split(r"[。！？；;\n]", source) if seg.strip()]
    if not sentences:
        sentences = [source]

    for raw in sentences:
        sentence = re.sub(r"\s+", " ", raw).strip()
        if len(sentence) < 6:
            continue
        lowered = sentence.lower()
        if signal_tokens and not any(sig in lowered for sig in signal_tokens):
            continue
        candidates.append(sentence[:max_chars])
        if len(candidates) >= max_items:
            break

    if not candidates:
        lowered = source.lower()
        if any(sig in lowered for sig in signal_tokens):
            candidates.append(re.sub(r"\s+", " ", source)[:max_chars])

    return _normalize_memory_list(candidates, max_items=max_items)


def _extract_longterm_facts_from_turn(
    *,
    message: str,
    reply: str,
    role: str,
    action: str,
    platform: str,
) -> Dict[str, Any]:
    text = str(message or "")
    focus_platforms: List[str] = []
    for platform_key, keyword in _LONGTERM_PLATFORM_KEYWORDS:
        if keyword in text:
            focus_platforms.append(platform_key)

    if str(platform or "").strip() and str(platform).strip().lower() != "general":
        focus_platforms.append(str(platform).strip().lower())

    goals = _extract_sentence_candidates(
        text,
        signals=_LONGTERM_GOAL_SIGNALS,
        max_items=3,
        max_chars=90,
    )
    constraints = _extract_sentence_candidates(
        text,
        signals=_LONGTERM_CONSTRAINT_SIGNALS,
        max_items=3,
        max_chars=90,
    )

    summary = re.sub(r"\s+", " ", str(reply or "").strip())
    if len(summary) > _LONGTERM_SUMMARY_MAX_CHARS:
        summary = summary[:_LONGTERM_SUMMARY_MAX_CHARS] + "..."

    return {
        "last_role": str(role or "").strip().lower(),
        "last_action": str(action or "").strip().lower(),
        "last_platform": str(platform or "").strip().lower(),
        "focus_platforms": _normalize_memory_list(focus_platforms, max_items=4),
        "goals": goals,
        "constraints": constraints,
        "last_reply_summary": summary,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _merge_longterm_facts(
    existing: Dict[str, Any],
    incoming: Dict[str, Any],
    *,
    max_items: int = _LONGTERM_MEMORY_MAX_ITEMS,
) -> Dict[str, Any]:
    old = existing if isinstance(existing, dict) else {}
    new = incoming if isinstance(incoming, dict) else {}

    merged: Dict[str, Any] = {
        "last_role": str(new.get("last_role") or old.get("last_role") or "").strip().lower(),
        "last_action": str(new.get("last_action") or old.get("last_action") or "").strip().lower(),
        "last_platform": str(new.get("last_platform") or old.get("last_platform") or "").strip().lower(),
        "last_reply_summary": str(new.get("last_reply_summary") or old.get("last_reply_summary") or "")[:_LONGTERM_SUMMARY_MAX_CHARS],
        "updated_at": str(new.get("updated_at") or datetime.now(timezone.utc).isoformat()),
    }

    merged["focus_platforms"] = _normalize_memory_list(
        list(old.get("focus_platforms") or []) + list(new.get("focus_platforms") or []),
        max_items=4,
    )
    merged["goals"] = _normalize_memory_list(
        list(old.get("goals") or []) + list(new.get("goals") or []),
        max_items=max_items,
    )
    merged["constraints"] = _normalize_memory_list(
        list(old.get("constraints") or []) + list(new.get("constraints") or []),
        max_items=max_items,
    )
    return merged


def _build_longterm_memory_prompt(
    *,
    persona: str,
    platforms: List[str],
    goals: List[str],
    constraints: List[str],
    summaries: List[str],
) -> str:
    parts: List[str] = ["【跨会话长期记忆】"]

    persona_value = str(persona or "").strip()
    if persona_value:
        parts.append(f"用户画像：{persona_value[:80]}")

    normalized_platforms = _normalize_memory_list(platforms, max_items=4)
    if normalized_platforms:
        parts.append("常用平台：" + "、".join(normalized_platforms))

    normalized_goals = _normalize_memory_list(goals, max_items=4)
    if normalized_goals:
        parts.append("历史目标偏好：\n" + "\n".join(f"- {x}" for x in normalized_goals))

    normalized_constraints = _normalize_memory_list(constraints, max_items=4)
    if normalized_constraints:
        parts.append("历史约束条件：\n" + "\n".join(f"- {x}" for x in normalized_constraints))

    normalized_summaries = _normalize_memory_list(summaries, max_items=4)
    if normalized_summaries:
        parts.append("近期跨会话结论摘要：\n" + "\n".join(f"- {x}" for x in normalized_summaries))

    if len(parts) <= 1:
        return ""

    prompt = "\n".join(parts)
    if len(prompt) > _LONGTERM_PROMPT_MAX_CHARS:
        prompt = prompt[:_LONGTERM_PROMPT_MAX_CHARS] + "..."
    return prompt

async def _load_longterm_memory_bundle(user_id: int, conversation_id: Optional[str]) -> Dict[str, Any]:
    if int(user_id or 0) <= 0:
        return {}

    try:
        from src.database import get_db
        from src.services.profile_service import get_profile

        db = await get_db()
        profile = await get_profile(user_id)

        params: List[Any] = [user_id]
        where_extra = ""
        if conversation_id:
            where_extra = " AND cc.conversation_id != ?"
            params.append(str(conversation_id))
        params.append(_LONGTERM_CONTEXT_MAX_ROWS)

        rows = await db.execute_fetchall(
            f"""
            SELECT cc.conversation_id, cc.summary, cc.facts, cc.updated_at
            FROM conversation_contexts cc
            JOIN conversations c ON c.id = cc.conversation_id
            WHERE c.user_id = ? {where_extra}
            ORDER BY cc.updated_at DESC, cc.id DESC
            LIMIT ?
            """,
            tuple(params),
        )

        goals: List[str] = []
        constraints: List[str] = []
        summaries: List[str] = []
        platforms: List[str] = []
        for row in rows or []:
            summary = re.sub(r"\s+", " ", str(row["summary"] or "").strip())
            if summary:
                summaries.append(summary[:100])

            facts_raw = row["facts"]
            facts_obj: Dict[str, Any] = {}
            if isinstance(facts_raw, dict):
                facts_obj = facts_raw
            else:
                try:
                    parsed = json.loads(str(facts_raw or "{}"))
                    if isinstance(parsed, dict):
                        facts_obj = parsed
                except Exception:
                    facts_obj = {}

            goals.extend([str(x) for x in (facts_obj.get("goals") or [])])
            constraints.extend([str(x) for x in (facts_obj.get("constraints") or [])])
            platforms.extend([str(x) for x in (facts_obj.get("focus_platforms") or [])])

        profile_platforms = profile.get("platforms") if isinstance(profile.get("platforms"), dict) else {}
        for key in profile_platforms.keys():
            platforms.append(str(key))

        prompt_text = _build_longterm_memory_prompt(
            persona=str(profile.get("persona") or ""),
            platforms=platforms,
            goals=goals,
            constraints=constraints,
            summaries=summaries,
        )

        fact_count = len(_normalize_memory_list(goals + constraints, max_items=64))
        return {
            "prompt_text": prompt_text,
            "fact_count": int(fact_count),
            "context_count": int(len(rows or [])),
            "persona": str(profile.get("persona") or ""),
            "platforms": _normalize_memory_list(platforms, max_items=4),
        }
    except Exception:
        logger.exception("load longterm memory failed user=%s conv=%s", user_id, conversation_id)
        return {}


def _extract_key_entities(message: str) -> str:
    '\n    从消息中提取核心业务实体词（商品?品类/品牌/抢术名），用于构搜索查诃69?\n    通过移除常见动词/副词前缀来定位实体?\n    '
    # 甯歌鍔ㄨ瘝/鍔╄瘝鍓嶇紑锛岄渶瑕佽烦杩?
    prefixes = [
        "甯垜", '请帮', "楹荤儲甯?", "鑳戒笉鑳?", '可以', '请问', '我想',
        "甯繖", '我要', "闇€瑕?", '给我', "鍋氫竴涓?", "鍋氫釜", "鐢熸垚", "鍐欎竴涓?", "鍐欎釜",
        "鍒嗘瀽", "鏌ヤ竴涓?", "鎼滀竴涓?", "甯垜鎼?", "甯垜鏌?", "甯垜鍋?"]
    # 甯歌鍚庣紑/鎻忚堪璇嶏紝绉婚櫎鍚庡彧鍓╁疄浣?
    suffixes = [
        "绔炲搧鍒嗘瀽", "绔炲搧", "绔炰簤鍒嗘瀽", "甯傚満鍒嗘瀽", "瓒嬪娍鍒嗘瀽", "鏁版嵁鍒嗘瀽",
        "鎻愬崌鏂规", "鏀瑰杽鏂规", "瑙ｅ喅鏂规", "浼樺寲鏂规", '执行计划', "杩愯惀鏂规",
        "閫夊瀷鍒嗘瀽", "閫夊瀷寤鸿", '抢术对?', '性能对比',
        "鐨勬柟妗?", '的分?', "鐨勭瓥鐣?", '的建?', "鐨勬暟鎹?", "鐨勬姤鍛?", '怎么?', "濡備綍", '怎么', "濡備綍鍋?", "浠€涔堟儏鍐?"]
    # 娓呯悊娑堟伅
    cleaned = message.strip()
    for p in prefixes:
        if cleaned.startswith(p):
            cleaned = cleaned[len(p):]
    for s in suffixes:
        if cleaned.endswith(s):
            cleaned = cleaned[:-len(s)]

    # 鍙栧墠30瀛椾綔涓哄疄浣撴牳蹇?
    entity_core = cleaned.strip()[:30]
    if not entity_core:
        entity_core = message[:20]
    return entity_core


# 瑙﹀彂涓诲姩鎼滅储鐨勫叧閿瘝 鈫?(鏌ヨ妯℃澘, topic, days)
_PROACTIVE_SEARCH_RULES: List[tuple] = [
    # 绔炲搧/鍚岃绫?
    ({"绔炲搧", "绔炰簤瀵规墜", "鍚岃", "瀵规墜", "绔炰簤"}, "{entities} 绔炲搧 瀹氫环 甯傚満鍒嗘瀽 2026", "general", 30),
    # 甯傚満瓒嬪娍绫?
    ({"瓒嬪娍", "鐑攢", "鐖嗘", "娴佽", "鐑棬", "甯傚満"}, "{entities} 甯傚満瓒嬪娍 鐑攢 琛屼笟鍔ㄦ€?2026", "general", 30),
    # 骞冲彴鏀跨瓥绫?
    ({"鏀跨瓥", '规则', "绠楁硶", "鍚堣", "浣ｉ噾", '平台规定'}, '{entities} 平台政策 规则 朢?2026', "general", 60),
    # 琛屼笟鍩哄噯绫?
    ({"琛屼笟", "鍩哄噯", "鍧囧€?", "鏍囧噯", "鍚岃姘村钩", "琛屼笟鏁版嵁"}, "{entities} 琛屼笟鍩哄噯 鏁版嵁 骞冲潎姘村钩 2026", "general", 90),
    # 璇勪环/鍙ｇ绫?
    ({'评价', '口碑', "宸瘎", "濂借瘎", '用户反馈'}, '{entities} 用户评价 口碑 2026', "general", 30),
    # DSR/NPS/鏈嶅姟绫?
    ({"DSR", "NPS", "瀹㈡湇", "鎶曡瘔", "鍞悗"}, '{entities} 电商客服 DSR 服务改善 2026', "general", 60),
    # 璐㈠姟/鍒╂鼎绫?
    ({"姣涘埄鐜?", "鍑€鍒╃巼", "ROI", "鍒╂鼎鐜?", "璐㈠姟"}, "鐢靛晢 {entities} 琛屼笟姣涘埄鐜?璐㈠姟鍩哄噯 2026", "general", 90),
    # 鎶€鏈€夊瀷/Bug绫?
    ({'抢术型', "Bug", "鎺ュ彛瓒呮椂", "鏋舵瀯", "閫夊瀷"}, '{entities} 解决方案 抢术对?2026', "general", 90),
    # 璁捐绫?
    ({"涓诲浘", "娴锋姤", "璁捐", "瑙嗚"}, "{entities} 鐢靛晢璁捐瓒嬪娍 鐖嗘 2026", "general", 60),
]


async def _proactive_search(message: str, role: str) -> Dict[str, Any]:
    '\n    ?Pipeline Step 5 之前主动执行针对性搜索，结果注入 system prompt?\n    不依?LLM 调用 search 工具—pipeline 自己搜，确保 LLM 收到实时数据?\n    默认朢多尝?次查询改写；超时则跳过，不阻塞主流程?\n    '
    try:
        from src.skills.search import _web_search, _format_results_for_llm
    except ImportError:
        return {}

    from src.config import (
        PROACTIVE_SEARCH_QUERY_TIMEOUT_SECONDS,
        PROACTIVE_SEARCH_TOTAL_BUDGET_SECONDS,
    )

    per_query_timeout = max(3.0, float(PROACTIVE_SEARCH_QUERY_TIMEOUT_SECONDS or 8.0))
    total_budget_seconds = max(per_query_timeout, float(PROACTIVE_SEARCH_TOTAL_BUDGET_SECONDS or 20.0))

    entities = _extract_key_entities(message)

    # 鎵惧埌绗竴涓尮閰嶇殑瑙勫垯
    query_template = None
    topic = "general"
    days = 30
    for keywords, template, _topic, _days in _PROACTIVE_SEARCH_RULES:
        if any(kw in message for kw in keywords):
            query_template = template
            topic = _topic
            days = _days
            break

    if not query_template:
        # 娌℃湁鏄庣‘瑙﹀彂璇嶆椂锛屾牴鎹鑹插仛閫氱敤鎼滅储
        role_defaults = {
            "ops": '电商运营 市场竞争 朢新策?2026',
            "data": "鐢靛晢 琛屼笟鏁版嵁 鍩哄噯 瓒嬪娍 2026",
            "accounting": "鐢靛晢 璐㈠姟鍩哄噯 姣涘埄鐜?骞垮憡ROI 2026",
            "service": '电商客服 DSR 服务评分 提升 2026',
            "web": '电商SEO 搜索算法 关键词策?2026',
            "creative": "鐢靛晢鍐呭钀ラ攢 鐖嗘 瓒嬪娍 2026",
            "engineering": '电商抢?架构 性能优化 2026',
            "design": "鐢靛晢瑙嗚璁捐 鐖嗘涓诲浘 瓒嬪娍 2026",
        }
        default_q = role_defaults.get(role, "")
        if not default_q:
            return {}
        query_template = default_q

    # 鐢熸垚鍊欓€夋煡璇細鍩虹鏌ヨ + 绔炲搧/瓒嬪娍涓撻」鎵╁睍 + 绠€鍖栧疄浣撶増鏈?
    raw_query = query_template.replace("{entities}", entities)
    query_candidates: List[str] = [raw_query]

    simplified = entities
    for sep in ("，", "、", "；", ",", ";", "|"):
        if sep in simplified:
            simplified = simplified.split(sep, 1)[0]
            break
    simplified = simplified.strip()
    if simplified and simplified != entities:
        query_candidates.append(query_template.replace("{entities}", simplified))

    # 瀵光€滅珵鍝?瓒嬪娍鈥濈被闂鑷姩灞曞紑澶氫釜鎼滅储瀛愪换鍔★紝妯℃嫙澶栭儴鑱旂綉妯″瀷鐨勪富鍔ㄨˉ鍏ㄨ涓恒€?
    if any(k in message for k in ("绔炲搧", "绔炰簤瀵规墜", "鍚岃", "瀵规墜")):
        seed = simplified or entities
        query_candidates.extend([
            f"{seed} 绔炲搧 瀵规瘮 浠锋牸 2026",
            f"{seed} 娣樺疂 浜笢 鎷煎澶?鍚岀被 鍟嗗搧",
            f"{seed} 鐢ㄦ埛璇勪环 鍙ｇ 宸瘎",
        ])
    if any(k in message for k in ("瓒嬪娍", "鐑攢", "鐖嗘", "娴佽", "琛屼笟")):
        seed = simplified or entities
        query_candidates.extend([
            f"{seed} 甯傚満瓒嬪娍 琛屼笟鍔ㄦ€?2026",
            f"{seed} 鐑攢 鐖嗘 杩戞湡",
        ])

    # 去重并依次尝试；设置总预算与单次超时，避免长尾阻塞
    query_candidates = query_candidates[:6]
    tried: set[str] = set()
    merged_results: List[Dict[str, Any]] = []
    merged_engine = "proactive"
    proactive_search_deadline = time.monotonic() + total_budget_seconds
    for q in query_candidates:
        q = q.strip()
        if not q or q in tried:
            continue
        if time.monotonic() >= proactive_search_deadline:
            break
        tried.add(q)
        remaining = proactive_search_deadline - time.monotonic()
        timeout_seconds = max(1.0, min(per_query_timeout, remaining))
        try:
            results, engine = await asyncio.wait_for(
                _web_search(q, topic=topic, max_results=5, days=days),
                timeout=float(timeout_seconds),
            )
            if results:
                merged_engine = engine or merged_engine
                merged_results.extend(results[:3])
                if len(merged_results) >= 6:
                    break
        except Exception:
            continue

    if not merged_results:
        return {}

    # 杞诲害鍘婚噸锛堟寜 URL锛?
    dedup: List[Dict[str, Any]] = []
    seen_urls: set[str] = set()
    for r in merged_results:
        u = (r.get("url") or "").strip().lower()
        if u and u in seen_urls:
            continue
        if u:
            seen_urls.add(u)
        dedup.append(r)
        if len(dedup) >= 8:
            break

    return {
        "prompt_text": _format_results_for_llm(dedup, max_per_item=300),
        "engine": merged_engine,
        "sources": _compact_realtime_sources(dedup, limit=5),
    }


def _extract_execution_suggestion(skill_name: str, result: dict) -> dict | None:
    """Extract an executable suggestion from selected operation skills."""
    if not isinstance(result, dict):
        return None

    price_skills = {"ops_smart_pricing", "ops_pricing_strategy", "ops_promo_planning"}
    inventory_skills = {"ops_inventory_optimizer", "ops_abcxyz_classifier"}
    if skill_name not in price_skills | inventory_skills:
        return None

    if skill_name in price_skills:
        candidates = (
            result.get("recommended_prices")
            or result.get("pricing_candidates")
            or result.get("candidates")
            or []
        )
        best: Dict[str, Any] = {}
        if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
            best = candidates[0]

        raw_price = (
            best.get("price")
            or best.get("recommended_price")
            or best.get("new_price")
            or result.get("price")
            or result.get("recommended_price")
        )
        if raw_price is not None:
            try:
                parsed_price = float(str(raw_price).replace("￥", "").replace(",", "").strip())
            except Exception:
                parsed_price = None
            if parsed_price is not None:
                return {
                    "action_type": "update_price",
                    "description": f"建议调整价格到 {parsed_price:g}（来源: {skill_name}）",
                    "platform": "",
                    "payload": {"new_price": parsed_price},
                    "expected_impact": str(
                        best.get("expected_impact")
                        or result.get("expected_impact")
                        or ""
                    ),
                }

    if skill_name in inventory_skills:
        inventory_plan = result.get("inventory_plan") if isinstance(result.get("inventory_plan"), dict) else {}
        raw_qty = (
            result.get("eoq")
            or result.get("recommended_quantity")
            or result.get("quantity")
            or inventory_plan.get("eoq")
        )
        if raw_qty is not None:
            try:
                qty = int(float(str(raw_qty).replace(",", "").strip()))
            except Exception:
                qty = None
            if qty is not None:
                return {
                    "action_type": "update_inventory",
                    "description": f"建议补货数量 {qty}（来源: {skill_name}）",
                    "platform": "",
                    "payload": {"quantity": qty},
                    "expected_impact": str(result.get("inventory_impact") or result.get("expected_impact") or ""),
                }

    return None


# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
# 鎶€鑳介绛涢€?鈥?閬垮厤71+鎶€鑳芥椂LLM璇箟娣蜂贡
# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?


_TOOL_TAG_PATTERNS: Dict[str, List[str]] = {
    "marketing": [
        "promo", "campaign", "channel", "pricing", "listing", "ad", "roi", "growth",
        "营销", "推广", "投放", "拉新", "促活", "留存", "复购", "活动",
    ],
    "data": [
        "analysis", "diagnosis", "dashboard", "query_store", "metrics", "trend", "forecast",
        "ab_test", "rfm", "attribution", "cohort", "funnel", "sql", "报表", "漏斗", "归因", "统计",
    ],
    "service": [
        "service", "ticket", "return", "refund", "after_sales", "nps", "sentiment", "客服", "售后", "退款", "投诉",
    ],
    "creative": [
        "creative", "script", "copy", "content", "seeding", "live", "video", "文案", "脚本", "短视频", "种草", "内容",
    ],
    "web": [
        "seo", "keyword", "title", "store_design", "page_conversion", "搜索", "关键词", "标题", "店铺装修",
    ],
    "finance": [
        "accounting", "profit", "budget", "cash_flow", "margin", "waterfall", "财务", "利润", "预算", "成本", "现金流",
    ],
    "engineering": [
        "engineering", "bug", "perf", "sla", "system", "api", "技术", "接口", "性能", "故障",
    ],
    "design": [
        "design", "poster", "image", "color", "detail_page", "主图", "详情页", "海报", "视觉", "配色",
    ],
}

_INTENT_TAG_KEYWORDS: Dict[str, List[str]] = {
    "marketing": [
        "营销", "营销方式", "营销策略", "增长", "拉新", "促活", "留存", "复购", "推广", "投放", "活动", "品牌",
    ],
    "data": [
        "数据", "sql", "报表", "指标", "漏斗", "同比", "环比", "归因", "ab测试", "显著性", "置信", "统计",
    ],
    "service": ["客服", "售后", "退款", "退货", "投诉", "差评", "话术", "安抚", "升级处理", "工单", "nps", "情绪"],
    "creative": ["文案", "脚本", "内容", "短视频", "种草", "直播", "品牌故事"],
    "web": ["seo", "搜索", "关键词", "标题", "店铺装修"],
    "finance": ["财务", "成本", "利润", "预算", "毛利", "现金流", "对账"],
    "engineering": ["技术", "接口", "系统", "故障", "性能", "架构", "发布", "上线", "部署", "灰度", "回滚", "回退", "监控", "告警", "稳定性", "压测", "sla", "bug"],
    "design": ["设计", "主图", "详情页", "海报", "视觉", "配色"],
}

_ACTION_TOOL_TAG_BIAS: Dict[str, Dict[str, float]] = {
    "analyze": {"data": 1.2, "marketing": 0.4, "finance": 0.8},
    "query": {"data": 0.9, "service": 0.3, "finance": 0.4},
    "plan": {"marketing": 1.1, "ops": 0.8, "creative": 0.5, "web": 0.5},
    "optimize": {"marketing": 1.0, "web": 0.7, "data": 0.6, "engineering": 0.6},
    "create": {"creative": 1.1, "design": 1.0, "marketing": 0.5, "web": 0.5},
    "execute": {"marketing": 1.0, "service": 0.7, "engineering": 0.7},
}

_ROLE_TOOL_TAG_BIAS: Dict[str, Dict[str, float]] = {
    "ops": {"marketing": 1.5, "data": 0.4, "creative": 0.4, "web": 0.4},
    "data": {"data": 1.6, "finance": 0.4, "marketing": 0.2},
    "service": {"service": 1.7, "marketing": 0.2},
    "creative": {"creative": 1.7, "marketing": 0.4, "design": 0.3},
    "web": {"web": 1.7, "marketing": 0.4, "data": 0.2},
    "accounting": {"finance": 1.7, "data": 0.3},
    "engineering": {"engineering": 1.7, "data": 0.4},
    "design": {"design": 1.7, "creative": 0.4, "marketing": 0.2},
}

_DISPATCH_MARKETING_KEYWORDS: tuple[str, ...] = (
    "营销", "营销方式", "营销策略", "增长", "拉新", "促活", "留存", "复购", "推广", "投放", "投流", "活动", "渠道", "渠道组合",
    "达人", "种草", "内容矩阵", "创意", "素材", "转化链路", "转化路径", "起量", "品牌", "campaign", "growth", "acquisition",
    "activation", "media mix", "go-to-market", "gtm",
)

_DISPATCH_DATA_STRONG_KEYWORDS: tuple[str, ...] = (
    "sql", "报表", "漏斗", "同比", "环比", "归因", "显著性", "置信", "统计", "看板", "仪表板", "口径",
    "join", "table", "query", "warehouse", "cohort", "attribution", "dashboard",
)

_DISPATCH_DATA_MEDIUM_KEYWORDS: tuple[str, ...] = (
    "指标", "roi", "roas", "gmv", "uv", "pv", "转化率", "留存率", "点击率", "客单价", "ctr", "cvr", "cac", "ltv",
    "复购率", "曝光", "消耗", "预算", "人群包",
)

_DISPATCH_DATA_WEAK_KEYWORDS: tuple[str, ...] = (
    "分析", "analysis", "analyze", "诊断", "评估", "复盘", "趋势",
)


_DISPATCH_STRATEGY_DELIVERY_KEYWORDS: tuple[str, ...] = (
    "方案", "策略", "计划", "执行", "落地", "动作", "步骤", "排期", "节奏",
    "优先级", "打法", "推进", "路径", "roadmap", "playbook",
)


_DISPATCH_GROWTH_GOAL_KEYWORDS: tuple[str, ...] = (
    "提升", "优化", "增长", "拉新", "促活", "复购", "转化", "转化率", "点击率",
    "roi", "roas", "gmv", "客单价", "投放", "投流", "渠道", "活动", "起量",
)


_DISPATCH_DATA_DIAGNOSTIC_KEYWORDS: tuple[str, ...] = (
    "原因", "根因", "归因", "诊断", "排查", "下滑", "波动", "异常", "拆解",
    "显著性", "置信", "验证", "口径", "sql", "报表", "漏斗", "看板", "dashboard",
)

_DISPATCH_DESIGN_CREATIVE_KEYWORDS: tuple[str, ...] = (
    "设计", "视觉", "文案", "素材", "主图", "详情页", "海报", "版式", "排版",
    "钩子", "脚本", "封面", "创意", "品牌风格", "色值", "字体",
)

_DISPATCH_EXPERIMENT_KEYWORDS: tuple[str, ...] = (
    "a/b", "ab测试", "ab 实验", "实验", "测试", "对照组", "测试组",
)

_DISPATCH_DATA_STRONG_CONTEXT_KEYWORDS: tuple[str, ...] = (
    "sql", "报表", "漏斗", "看板", "仪表板", "口径", "归因", "显著性", "置信", "dashboard",
)

_DISPATCH_VISUAL_ATTACHMENT_KEYWORDS: tuple[str, ...] = (
    "主图", "详情页", "海报", "封面", "素材", "视觉", "版式", "排版", "创意", "文案",
    "图片", "图像", "截图", "照片", "这张图", "这个图", "这张图片", "这个图片",
    "image", "poster", "banner", "creative", "visual",
)

_DISPATCH_ATTACHMENT_IMAGE_FILE_TOKENS: tuple[str, ...] = (
    "image", "png", "jpg", "jpeg", "webp", "gif", "bmp", "svg",
)

_DISPATCH_ATTACHMENT_TABLE_FILE_TOKENS: tuple[str, ...] = (
    "csv", "xlsx", "xls", "table", "tsv",
)


def _extract_intent_tags_from_message(message: str) -> set[str]:
    tags: set[str] = set()
    msg = str(message or "").lower()
    if not msg:
        return tags

    for tag, keywords in _INTENT_TAG_KEYWORDS.items():
        if any(str(kw or "").lower() in msg for kw in keywords):
            tags.add(tag)

    return tags


def _count_dispatch_keyword_hits(text: str, keywords: tuple[str, ...]) -> int:
    lowered = str(text or "").lower()
    if not lowered:
        return 0
    return sum(1 for token in keywords if token and str(token).lower() in lowered)


def _build_dispatch_intent_profile(
    message: str,
    *,
    action: str = "",
    domain_id: str = "",
    history_text: str = "",
    attachments: Optional[List[Dict[str, Any]]] = None,
    attachment_structured: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a global dispatch profile from message + history + attachments.

    Goal: reduce role/tool mismatches in free scheduling by combining
    lexical intent, recent context memory, and attachment modality signals.
    """
    text = str(message or "")
    lowered = text.lower()
    history = str(history_text or "")
    history_lower = history.lower()
    action_key = str(action or "").strip().lower()
    domain_key = str(domain_id or "").strip().lower()

    intent_tags = _extract_intent_tags_from_message(text)
    marketing_hits = _count_dispatch_keyword_hits(lowered, _DISPATCH_MARKETING_KEYWORDS)
    data_strong_hits = _count_dispatch_keyword_hits(lowered, _DISPATCH_DATA_STRONG_KEYWORDS)
    data_medium_hits = _count_dispatch_keyword_hits(lowered, _DISPATCH_DATA_MEDIUM_KEYWORDS)
    data_weak_hits = _count_dispatch_keyword_hits(lowered, _DISPATCH_DATA_WEAK_KEYWORDS)
    strategy_delivery_hits = _count_dispatch_keyword_hits(lowered, _DISPATCH_STRATEGY_DELIVERY_KEYWORDS)
    growth_goal_hits = _count_dispatch_keyword_hits(lowered, _DISPATCH_GROWTH_GOAL_KEYWORDS)
    data_diagnostic_hits = _count_dispatch_keyword_hits(lowered, _DISPATCH_DATA_DIAGNOSTIC_KEYWORDS)
    design_creative_hits = _count_dispatch_keyword_hits(lowered, _DISPATCH_DESIGN_CREATIVE_KEYWORDS)
    experiment_hits = _count_dispatch_keyword_hits(lowered, _DISPATCH_EXPERIMENT_KEYWORDS)
    data_strong_context_hits = _count_dispatch_keyword_hits(lowered, _DISPATCH_DATA_STRONG_CONTEXT_KEYWORDS)
    message_data_metric_hits = int(data_strong_hits + data_medium_hits + data_diagnostic_hits)
    message_visual_intent_hits = _count_dispatch_keyword_hits(lowered, _DISPATCH_VISUAL_ATTACHMENT_KEYWORDS)

    # Carry context memory into current dispatch, but at reduced weight.
    history_marketing_hits = _count_dispatch_keyword_hits(history_lower, _DISPATCH_MARKETING_KEYWORDS)
    history_data_strong_hits = _count_dispatch_keyword_hits(history_lower, _DISPATCH_DATA_STRONG_KEYWORDS)
    history_data_medium_hits = _count_dispatch_keyword_hits(history_lower, _DISPATCH_DATA_MEDIUM_KEYWORDS)
    history_design_creative_hits = _count_dispatch_keyword_hits(history_lower, _DISPATCH_DESIGN_CREATIVE_KEYWORDS)
    history_data_diagnostic_hits = _count_dispatch_keyword_hits(history_lower, _DISPATCH_DATA_DIAGNOSTIC_KEYWORDS)

    marketing_hits += int(round(history_marketing_hits * 0.45))
    data_strong_hits += int(round(history_data_strong_hits * 0.35))
    data_medium_hits += int(round(history_data_medium_hits * 0.30))
    design_creative_hits += int(round(history_design_creative_hits * 0.35))
    data_diagnostic_hits += int(round(history_data_diagnostic_hits * 0.30))

    action_token = action_key or ""
    marketing_action = action_token in {"plan", "optimize", "execute", "create"}
    analysis_action = action_token in {"analyze", "query"}

    attachment_items = attachments if isinstance(attachments, list) else []
    attachment_image_count = 0
    attachment_table_count = 0
    attachment_visual_hits = 0
    attachment_data_hits = 0

    for item in attachment_items:
        if not isinstance(item, dict):
            continue
        file_type = str(item.get("file_type") or item.get("type") or "").strip().lower()
        filename = str(item.get("filename") or "").strip().lower()
        parser = str(item.get("parser") or "").strip().lower()
        summary = str(item.get("summary") or "").strip().lower()
        content = str(item.get("content") or "").strip().lower()[:400]
        blob = " ".join([file_type, filename, parser, summary, content]).strip()

        ext = ""
        if "." in filename:
            ext = filename.rsplit(".", 1)[-1].strip().lower()

        is_image_like = any(tok in file_type for tok in _DISPATCH_ATTACHMENT_IMAGE_FILE_TOKENS) or ext in {
            "png", "jpg", "jpeg", "webp", "gif", "bmp", "svg",
        }
        is_table_like = any(tok in file_type for tok in _DISPATCH_ATTACHMENT_TABLE_FILE_TOKENS) or ext in {
            "csv", "xlsx", "xls", "tsv",
        }

        if is_image_like:
            attachment_image_count += 1
        if is_table_like:
            attachment_table_count += 1

        if blob:
            attachment_visual_hits += _count_dispatch_keyword_hits(blob, _DISPATCH_VISUAL_ATTACHMENT_KEYWORDS)
            attachment_data_hits += _count_dispatch_keyword_hits(blob, _DISPATCH_DATA_STRONG_CONTEXT_KEYWORDS)

    structured_table_count = 0
    if isinstance(attachment_structured, dict):
        try:
            structured_table_count = int(attachment_structured.get("table_attachment_count") or 0)
        except Exception:
            structured_table_count = 0
    attachment_table_count = max(attachment_table_count, structured_table_count)

    explicit_visual_query = bool(
        message_visual_intent_hits > 0
        or re.search(r"((看|读|识别|解析|分析).{0,4}(图|图片|图像|截图|照片|主图|海报))", lowered)
        or re.search(r"(这张图|这个图|这张图片|这个图片|图里|图片里|图中|主图|海报|视觉|排版|文案)", lowered)
    )
    attachment_visual_query_without_metric = bool(
        attachment_image_count > 0
        and explicit_visual_query
        and attachment_table_count <= 0
        and message_data_metric_hits <= 0
    )

    has_growth_strategy_pattern = bool(
        re.search(r"(怎么|如何).{0,8}(提升|优化|增长|做)", lowered)
        or re.search(r"(提升|优化|增长).{0,8}(方案|策略|计划|动作|步骤|落地)", lowered)
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
            or any(token in lowered for token in ("投放", "投流", "渠道", "活动", "拉新", "促活", "复购", "增长", "转化"))
            or (data_medium_hits >= 1 and has_growth_strategy_pattern)
        )
    )

    attachment_visual_marketing_focus = bool(
        attachment_image_count > 0
        and (
            marketing_hits > 0
            or design_creative_hits > 0
            or growth_goal_hits > 0
            or attachment_visual_hits > 0
            or growth_strategy_focus
        )
    )
    attachment_data_table_focus = bool(
        attachment_table_count > 0
        and (
            analysis_action
            or data_diagnostic_hits > 0
            or data_medium_hits > 0
            or data_strong_hits > 0
            or attachment_data_hits > 0
        )
    )

    if attachment_visual_marketing_focus:
        marketing_hits += 1
        design_creative_hits += max(1, min(3, attachment_image_count + attachment_visual_hits))

    if attachment_visual_query_without_metric:
        # 仅图像理解且无数据指标证据时，优先视觉/运营路径，避免误分派到数据岗。
        design_creative_hits += 2

    if attachment_data_table_focus:
        data_medium_hits += max(1, min(3, attachment_table_count))
        data_strong_context_hits += max(1, min(2, attachment_data_hits or attachment_table_count))

    marketing_focus = bool(marketing_hits > 0 or "marketing" in intent_tags or growth_strategy_focus)
    marketing_intensity = int(marketing_hits + (2 if "marketing" in intent_tags else 0) + min(growth_goal_hits, 2))
    design_creative_experiment_focus = bool(
        experiment_hits >= 1
        and design_creative_hits >= 2
        and data_diagnostic_hits <= 1
        and not (analysis_action and data_strong_context_hits >= 2)
    )

    data_evidence_score = int(data_strong_hits * 2 + data_medium_hits)
    has_any_data_evidence = bool(data_evidence_score > 0)
    has_strong_data_evidence = bool(
        data_strong_hits >= 1
        or data_medium_hits >= 2
        or (data_medium_hits >= 1 and action_key in {"analyze", "query"} and "data" in intent_tags)
        or (experiment_hits >= 1 and data_strong_context_hits >= 1 and analysis_action)
        or (attachment_data_table_focus and (data_medium_hits >= 1 or attachment_table_count >= 1))
    )
    strong_data_diagnostic = bool(
        has_strong_data_evidence
        and (
            data_diagnostic_hits >= 2
            or (analysis_action and data_diagnostic_hits >= 1 and data_medium_hits >= 1)
            or (data_strong_hits >= 2 and data_strong_context_hits >= 1)
            or (attachment_data_table_focus and analysis_action)
        )
    )

    visual_request_hits = _count_dispatch_keyword_hits(lowered, _DISPATCH_VISUAL_ATTACHMENT_KEYWORDS)
    visual_request_focus = bool(
        visual_request_hits > 0
        or explicit_visual_query
    )
    visual_analysis_focus = bool(
        attachment_image_count > 0
        and visual_request_focus
        and not attachment_data_table_focus
        and (
            attachment_visual_query_without_metric
            or (
                not has_strong_data_evidence
                and data_strong_hits == 0
                and data_medium_hits <= 1
                and data_strong_context_hits == 0
            )
        )
    )

    prefer_data_primary = bool(
        not attachment_visual_query_without_metric
        and not design_creative_experiment_focus
        and strong_data_diagnostic
        and (
            analysis_action
            or data_diagnostic_hits >= 2
            or (data_strong_hits >= 2 and data_strong_context_hits >= 1)
            or (attachment_data_table_focus and analysis_action)
        )
    )
    prefer_design_primary = bool(
        (
            attachment_visual_marketing_focus
            and visual_request_focus
            and not has_strong_data_evidence
            and design_creative_hits >= 2
        )
        or visual_analysis_focus
    )
    prefer_ops_primary = bool(
        marketing_focus
        and growth_strategy_focus
        and not prefer_data_primary
        and not prefer_design_primary
        and (
            marketing_action
            or strategy_delivery_hits > 0
            or growth_goal_hits > 0
            or (analysis_action and data_strong_hits == 0 and data_medium_hits <= 1)
            or attachment_visual_marketing_focus
        )
    )

    suppress_data_support = bool(
        (prefer_ops_primary and not strong_data_diagnostic)
        or (design_creative_experiment_focus and not strong_data_diagnostic)
        or (
            marketing_focus
            and not has_any_data_evidence
            and (
                data_weak_hits > 0
                or marketing_intensity >= 1
                or growth_strategy_focus
                or attachment_visual_marketing_focus
            )
        )
        or (
            attachment_visual_marketing_focus
            and not has_strong_data_evidence
            and data_evidence_score <= 1
        )
        or visual_analysis_focus
    )

    return {
        "marketing_focus": marketing_focus,
        "marketing_intensity": int(marketing_intensity),
        "business_strategy_focus": bool(business_strategy_focus),
        "growth_strategy_focus": bool(growth_strategy_focus),
        "strong_data_diagnostic": bool(strong_data_diagnostic),
        "has_any_data_evidence": has_any_data_evidence,
        "has_strong_data_evidence": has_strong_data_evidence,
        "data_evidence_score": int(data_evidence_score),
        "data_diagnostic_hits": int(data_diagnostic_hits),
        "strategy_delivery_hits": int(strategy_delivery_hits),
        "growth_goal_hits": int(growth_goal_hits),
        "data_weak_hits": int(data_weak_hits),
        "data_medium_hits": int(data_medium_hits),
        "data_strong_hits": int(data_strong_hits),
        "marketing_hits": int(marketing_hits),
        "design_creative_hits": int(design_creative_hits),
        "experiment_hits": int(experiment_hits),
        "data_strong_context_hits": int(data_strong_context_hits),
        "design_creative_experiment_focus": bool(design_creative_experiment_focus),
        "action": action_key,
        "domain_id": domain_key,
        "analysis_action": analysis_action,
        "marketing_action": marketing_action,
        "visual_request_focus": bool(visual_request_focus),
        "visual_analysis_focus": bool(visual_analysis_focus),
        "attachment_visual_query_without_metric": bool(attachment_visual_query_without_metric),
        "message_data_metric_hits": int(message_data_metric_hits),
        "message_visual_intent_hits": int(message_visual_intent_hits),
        "suppress_data_support": suppress_data_support,
        "prefer_ops_primary": prefer_ops_primary,
        "prefer_data_primary": prefer_data_primary,
        "prefer_design_primary": prefer_design_primary,
        "attachment_image_count": int(attachment_image_count),
        "attachment_table_count": int(attachment_table_count),
        "attachment_visual_hits": int(attachment_visual_hits),
        "attachment_data_hits": int(attachment_data_hits),
        "attachment_visual_marketing_focus": bool(attachment_visual_marketing_focus),
        "attachment_data_table_focus": bool(attachment_data_table_focus),
        "history_marketing_hits": int(history_marketing_hits),
        "history_data_strong_hits": int(history_data_strong_hits),
        "history_data_medium_hits": int(history_data_medium_hits),
        "history_design_creative_hits": int(history_design_creative_hits),
        "history_data_diagnostic_hits": int(history_data_diagnostic_hits),
        "intent_tags": sorted(intent_tags),
    }

def _infer_tool_tags(name: str, description: str) -> set[str]:
    tags: set[str] = set()
    combined = f"{name} {description}".lower()
    if not combined.strip():
        return tags

    for tag, patterns in _TOOL_TAG_PATTERNS.items():
        if any(str(pattern or "").lower() in combined for pattern in patterns):
            tags.add(tag)

    return tags


def _score_tool_relevance(
    tool: Dict[str, Any],
    message: str,
    *,
    role: str = "",
    action: str = "",
    domain_id: str = "",
    intent_profile: Optional[Dict[str, Any]] = None,
) -> float:
    """Score tool relevance in range [0, 10] by lexical overlap + intent/action/role priors."""
    fn = tool.get("function") if isinstance(tool, dict) else {}
    name = str((fn or {}).get("name") or "")
    desc = str((fn or {}).get("description") or "")

    if name.startswith("coordination_"):
        return 10.0

    message = str(message or "")
    msg_lower = message.lower()
    combined = (name + " " + desc).lower().strip()
    if not combined:
        return 0.0

    score = 0.0

    if name and name.lower() in msg_lower:
        score += 3.0

    # Character n-gram overlap with per-length cap, avoid long-message score explosion.
    for length, weight, cap in ((2, 0.22, 8), (3, 0.38, 6), (4, 0.58, 4)):
        if len(message) < length:
            continue
        seen_tokens: set[str] = set()
        hits = 0
        for i in range(len(message) - length + 1):
            token = message[i:i + length].strip().lower()
            if not token or token in seen_tokens:
                continue
            if token in combined:
                seen_tokens.add(token)
                score += weight
                hits += 1
                if hits >= cap:
                    break

    keyword_map: Dict[str, List[str]] = {
        "refund": ["refund", "return", "after_sales"],
        "return": ["refund", "return", "after_sales"],
        "销售": ["sales", "trend", "forecast", "demand"],
        "趋势": ["trend", "forecast", "seasonal"],
        "预测": ["forecast", "demand_forecast", "trend_forecast"],
        "定价": ["pricing", "price", "elasticity", "smart_pricing"],
        "价格": ["pricing", "price", "elasticity", "smart_pricing"],
        "广告": ["roi", "channel", "attribution", "fatigue", "ad"],
        "roi": ["roi", "channel", "attribution"],
        "利润": ["profit", "pl", "margin", "waterfall"],
        "预算": ["budget", "budget_vs_actual"],
        "成本": ["cost", "break_even", "cost_calc"],
        "客户": ["customer", "segment", "rfm", "ltv", "nps"],
        "竞品": ["competitor", "competitor_analysis"],
        "库存": ["inventory", "inventory_optimizer", "eoq"],
        "现金流": ["cash_flow", "cash_flow_forecast"],
        "财务": ["finance", "profit", "waterfall", "pl"],
        "报表": ["dashboard", "store_metrics", "query_store"],
        "a/b": ["ab_test", "ab_test_analyzer"],
        "归因": ["attribution", "attribution_analysis"],
        "情绪": ["sentiment", "sentiment_analyzer"],
        "nps": ["nps", "nps_analyzer", "nps_driver"],
        "rfm": ["rfm", "segment", "customer_segmentation"],
        "营销": ["promo", "campaign", "channel", "growth", "listing", "seeding", "copy"],
        "拉新": ["campaign", "channel", "growth", "seeding", "copy"],
        "复购": ["rfm", "ltv", "nps", "campaign", "service"],
    }

    for key, tags in keyword_map.items():
        if key in msg_lower:
            for tag in tags:
                if tag in combined:
                    score += 1.1

    inferred_tool_tags = _infer_tool_tags(name, desc)
    intent_tags = _extract_intent_tags_from_message(message)

    overlap = intent_tags.intersection(inferred_tool_tags)
    if overlap:
        score += 1.6 * len(overlap)

    role_key = str(role or "").strip().lower()

    profile = intent_profile if isinstance(intent_profile, dict) else {}
    suppress_data_support = bool(profile.get("suppress_data_support"))
    marketing_focus = bool(profile.get("marketing_focus"))
    has_strong_data_evidence = bool(profile.get("has_strong_data_evidence"))
    marketing_action = bool(profile.get("marketing_action"))
    analysis_action = bool(profile.get("analysis_action"))
    attachment_visual_marketing_focus = bool(profile.get("attachment_visual_marketing_focus"))
    attachment_data_table_focus = bool(profile.get("attachment_data_table_focus"))

    if suppress_data_support and "data" in inferred_tool_tags and role_key != "data":
        # Marketing-focused query without strong data evidence: avoid data-tool over-trigger.
        score -= 3.2 if marketing_action else 2.8
    if (
        marketing_focus
        and not has_strong_data_evidence
        and "data" in inferred_tool_tags
        and role_key in {"ops", "creative", "web", "design", "service"}
    ):
        score -= 0.8
    if suppress_data_support and "marketing" in inferred_tool_tags and role_key in {"ops", "creative", "web", "design"}:
        score += 0.7
    if analysis_action and has_strong_data_evidence and "data" in inferred_tool_tags and role_key in {"data", "ops", "accounting"}:
        score += 0.4

    if attachment_visual_marketing_focus:
        if {"marketing", "design", "creative"}.intersection(inferred_tool_tags) and role_key in {"ops", "design", "creative", "web"}:
            score += 0.85
        if "data" in inferred_tool_tags and role_key in {"ops", "design", "creative", "web", "service"}:
            score -= 1.2

    if attachment_data_table_focus:
        if "data" in inferred_tool_tags and role_key in {"data", "ops", "accounting"}:
            score += 0.9
        if "design" in inferred_tool_tags and role_key in {"data", "ops", "accounting"}:
            score -= 0.35

    if role_key and name.startswith(f"{role_key}_"):
        score += 1.2

    for tag, bonus in (_ROLE_TOOL_TAG_BIAS.get(role_key) or {}).items():
        if tag in inferred_tool_tags:
            score += float(bonus)

    action_key = str(action or "").strip().lower()
    for tag, bonus in (_ACTION_TOOL_TAG_BIAS.get(action_key) or {}).items():
        if tag in inferred_tool_tags:
            score += float(bonus)

    # Domain hint for ecommerce growth tasks.
    if str(domain_id or "").strip().lower() == "domain.ecommerce":
        if "marketing" in intent_tags and "marketing" in inferred_tool_tags:
            score += 0.6
        if "data" in intent_tags and "data" in inferred_tool_tags:
            score += 0.4

    # Avoid debug/test noise when user didn't ask.
    if any(k in name for k in ("debug", "mock", "test")) and not any(k in msg_lower for k in ("debug", "test", "测试")):
        score -= 1.0

    return max(0.0, min(score, 10.0))


def _prefilter_tools(
    tools: List[Dict[str, Any]],
    message: str,
    max_role_tools: int = 8,
    *,
    role: str = "",
    action: str = "",
    domain_id: str = "",
    extra_context: str = "",
    intent_profile: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    从工具列表筛最相关技能，降低自由调度下的错技能调用。

    策略：
    - coordination/search 工具无条件保留；
    - 其余工具按“词面匹配 + 意图标签 + 角色/动作偏置”评分；
    - role 工具只保留 top-N，避免大量无关工具干扰模型判断。
    """
    if not tools:
        return tools

    def _is_always_keep(name: str) -> bool:
        token = str(name or "")
        return token.startswith("coordination_") or token.startswith("search_")

    coord_tools = [t for t in tools if _is_always_keep((t.get("function") or {}).get("name", ""))]
    role_tools = [t for t in tools if not _is_always_keep((t.get("function") or {}).get("name", ""))]

    if not role_tools:
        return coord_tools

    role_cap = max(1, int(max_role_tools or 1))
    if len(role_tools) <= role_cap:
        return role_tools + coord_tools

    score_message = str(message or "")
    extra = str(extra_context or "").strip()
    if extra:
        score_message = f"{score_message}\n{extra}"

    effective_intent_profile = (
        intent_profile
        if isinstance(intent_profile, dict)
        else _build_dispatch_intent_profile(
            message,
            action=action,
            domain_id=domain_id,
        )
    )

    scored = [
        (
            t,
            _score_tool_relevance(
                t,
                score_message,
                role=role,
                action=action,
                domain_id=domain_id,
                intent_profile=effective_intent_profile,
            ),
            _infer_tool_tags(
                str((t.get("function") or {}).get("name") or ""),
                str((t.get("function") or {}).get("description") or ""),
            ),
        )
        for t in role_tools
    ]
    scored.sort(
        key=lambda item: (
            item[1],
            str((item[0].get("function") or {}).get("name") or "").lower(),
        ),
        reverse=True,
    )

    role_key = str(role or "").strip().lower()
    suppress_data_support = bool(effective_intent_profile.get("suppress_data_support"))
    has_strong_data_evidence = bool(effective_intent_profile.get("has_strong_data_evidence"))
    strict_data_suppression = bool(suppress_data_support and not has_strong_data_evidence)

    if suppress_data_support and role_key != "data":
        non_data_scored = [item for item in scored if "data" not in item[2]]
        data_scored = [item for item in scored if "data" in item[2]]
        selected_scored = list(non_data_scored[:role_cap])
        if len(selected_scored) < role_cap and not strict_data_suppression:
            selected_scored.extend(data_scored[: (role_cap - len(selected_scored))])
        elif len(selected_scored) < min(2, role_cap):
            high_conf_data = [item for item in data_scored if float(item[1]) >= 7.0]
            selected_scored.extend(high_conf_data[: (role_cap - len(selected_scored))])
        selected_role_tools = [item[0] for item in selected_scored[:role_cap]]
    else:
        selected_role_tools = [item[0] for item in scored[:role_cap]]
    return selected_role_tools + coord_tools
# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
# Pipeline 浜嬩欢
# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?

@dataclass
class PipelineEvent:
    event: str
    data: Dict[str, Any] = field(default_factory=dict)

    def to_sse(self) -> str:
        payload = json.dumps({"event": self.event, **self.data}, ensure_ascii=False)
        return f"data: {payload}\n\n"

# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
# 涓婁笅鏂囧姞杞?
# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?

async def _load_history(conversation_id: Optional[str], user_id: int, limit: int = 10) -> List[Dict[str, Any]]:
    if not conversation_id:
        return []
    try:
        from src.database import get_db
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT role, content FROM messages WHERE conversation_id = ? AND user_id = ? ORDER BY created_at DESC LIMIT ?",
            (conversation_id, user_id, limit),
        )
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    except Exception:
        return []




async def _load_recent_task_anchor(conversation_id: Optional[str], user_id: int) -> Dict[str, Any]:
    if not conversation_id:
        return {}
    try:
        from src.database import get_db

        db = await get_db()
        row = await db.execute_fetchone(
            """
            SELECT metadata
            FROM messages
            WHERE conversation_id = ? AND user_id = ? AND role = 'assistant'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (conversation_id, user_id),
        )
        if not row:
            return {}

        raw_meta = row["metadata"]
        if isinstance(raw_meta, dict):
            meta = raw_meta
        else:
            meta = json.loads(str(raw_meta or "{}"))
            if not isinstance(meta, dict):
                return {}

        return _normalize_task_anchor(meta)
    except Exception:
        return {}

async def _load_memories(role: str, limit: int = 5) -> List[Dict]:
    try:
        from src.database import get_db
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT category, content, confidence FROM learnings WHERE role = ? AND category != 'routing_keyword' AND confidence >= 0.5 ORDER BY updated_at DESC LIMIT ?",
            (role, limit),
        )
        return [{"category": r["category"], "content": r["content"], "confidence": r["confidence"]} for r in rows]
    except Exception:
        return []


async def _load_learned_keywords() -> Dict[str, List[str]]:
    """Load learned routing keywords by role for tool prefiltering."""
    try:
        from src.database import get_db
        db = await get_db()
        rows = await db.execute_fetchall(
            "SELECT role, content FROM learnings WHERE category = 'routing_keyword' AND confidence >= 0.5 ORDER BY confidence DESC LIMIT 200",
            (),
        )
        result: Dict[str, List[str]] = {}
        for r in rows:
            role = r["role"]
            if role not in result:
                result[role] = []
            result[role].append(r["content"])
        return result
    except Exception:
        return {}


async def _load_trust(role: str) -> str:
    try:
        from src.database import get_db
        db = await get_db()
        row = await db.execute_fetchone(
            "SELECT trust_level FROM trust_scores WHERE role = ? ORDER BY updated_at DESC LIMIT 1",
            (role,),
        )
        return row["trust_level"] if row else ""
    except Exception:
        return ""


async def _load_product_ctx(product_id: Optional[int], query: str = "") -> str:
    """Load lightweight product context for a single product id."""
    if not product_id:
        return ""
    return await _load_product_ctx_rich([product_id], query=query)


_TEXT_EXTENSIONS = {".txt", ".md", ".csv", ".json", ".xml", ".html", ".htm"}
_MAX_FILE_CONTENT_CHARS = 1500  # 姣忎釜鏂囦欢鏈€澶氳鍙栧瓧绗︽暟
_MAX_TOTAL_FILE_CHARS = 3000    # 鎵€鏈夋枃浠跺姞璧锋潵鏈€澶?


async def _load_product_ctx_rich(product_ids: List[int], query: str = "") -> str:
    """
    Build rich product context from product ids, knowledge snippets, and local files.
    Priority: product fields -> related KB -> text files -> summary fallback.
    """
    if not product_ids:
        return ""
    try:
        from pathlib import Path
        from src.database import get_db
        db = await get_db()
        sections: List[str] = []

        for pid in product_ids[:3]:  # 鏈€澶?涓骇鍝?
            row = await db.execute_fetchone(
                "SELECT name, category, sku, description, lifecycle_status, folder_path FROM products WHERE id = ?",
                (pid,),
            )
            if not row:
                continue

            # 鈹€鈹€ Level 1: 鍩烘湰淇℃伅 鈹€鈹€
            parts = [f"Product {pid}: {row['name']}"]
            if row["category"]:
                parts.append(f"绫荤洰：{row['category']}")
            if row["sku"]:
                parts.append(f"SKU：{row['sku']}")
            parts.append(f"鐘舵€侊細{row['lifecycle_status']}")
            if row["description"]:
                desc = row["description"][:300] + "..." if len(row["description"]) > 300 else row["description"]
                parts.append(f"浜у搧鎻忚堪：{desc}")

            # 鈹€鈹€ Level 2: 鐭ヨ瘑搴撴潯鐩紙璇箟鎼滅储浼樺厛锛宖allback 鍏抽敭璇嶏級鈹€鈹€
            know_texts_found: List[str] = []
            try:
                from src.core.vector_store import search_product_knowledge
                semantic_results = await search_product_knowledge(query=query or "", product_id=pid, top_k=5)
                if semantic_results:
                    know_texts_found = [f"  [璇箟鍖归厤] {c[:200]}" for c in semantic_results]
            except Exception:
                pass

            if not know_texts_found:
                know_rows = await db.execute_fetchall(
                    "SELECT content, content_type FROM product_knowledge WHERE product_id = ? ORDER BY confidence DESC LIMIT 5",
                    (pid,),
                )
                know_texts_found = [f"  [{r['content_type']}] {r['content'][:200]}" for r in know_rows]

            if know_texts_found:
                parts.append("产品知识：\n" + "\n".join(know_texts_found))

            # 鈹€鈹€ Level 2.5: 绔炲搧淇℃伅 鈹€鈹€
            comp_rows = await db.execute_fetchall(
                "SELECT name, platform, price, rating, monthly_sales, notes FROM product_competitors WHERE product_id = ? ORDER BY created_at DESC LIMIT 5",
                (pid,),
            )
            if comp_rows:
                comp_lines = []
                platform_names = {"taobao": "娣樺疂", "jd": "浜笢", "pdd": "鎷煎澶?", "douyin": "鎶栭煶"}
                for cr in comp_rows:
                    line = f"  {cr['name']}"
                    if cr["platform"]:
                        line += f" ({platform_names.get(cr['platform'], cr['platform'])})"
                    if cr["price"]:
                        line += f" 鍞环楼{cr['price']}"
                    if cr["rating"]:
                        line += f" 璇勫垎{cr['rating']}"
                    if cr["monthly_sales"]:
                        line += f" 鏈堥攢{cr['monthly_sales']}"
                    if cr["notes"]:
                        line += f" | {cr['notes'][:100]}"
                    comp_lines.append(line)
                parts.append("竞品信息：\n" + "\n".join(comp_lines))

            # 鈹€鈹€ Level 3: 鏂囨湰鏂囦欢瀹為檯鍐呭 鈹€鈹€
            text_file_rows = await db.execute_fetchall(
                "SELECT filename, original_name, file_path, file_type FROM product_assets WHERE product_id = ? AND file_type = 'docs' ORDER BY created_at DESC LIMIT 10",
                (pid,),
            )
            total_file_chars = 0
            file_contents: List[str] = []
            for fr in text_file_rows:
                if total_file_chars >= _MAX_TOTAL_FILE_CHARS:
                    break
                file_path = Path(fr["file_path"]) if fr["file_path"] else None
                if not file_path or not file_path.exists():
                    continue
                ext = file_path.suffix.lower()
                if ext not in _TEXT_EXTENSIONS:
                    continue
                try:
                    raw = file_path.read_text(encoding="utf-8", errors="ignore")
                    remaining = min(_MAX_FILE_CONTENT_CHARS, _MAX_TOTAL_FILE_CHARS - total_file_chars)
                    content = raw[:remaining]
                    if len(raw) > remaining:
                        content += "..."
                    fname = fr["original_name"] or fr["filename"]
                    file_contents.append(f"  馃搫 {fname}锛歕n{content}")
                    total_file_chars += len(content)
                except Exception:
                    pass

            if file_contents:
                parts.append("Product docs content:\n" + "\n".join(file_contents))
            # 鈹€鈹€ Level 4: 闈炴枃鏈枃浠剁粺璁?鈹€鈹€
            media_rows = await db.execute_fetchall(
                "SELECT file_type, COUNT(*) as cnt FROM product_assets WHERE product_id = ? AND file_type != 'docs' GROUP BY file_type",
                (pid,),
            )
            doc_total_row = await db.execute_fetchone(
                "SELECT COUNT(*) as cnt FROM product_assets WHERE product_id = ? AND file_type = 'docs'",
                (pid,),
            )
            summary_parts = []
            if doc_total_row and doc_total_row["cnt"]:
                summary_parts.append(f"鏂囨。脳{doc_total_row['cnt']}")
            for mr in media_rows:
                summary_parts.append(f"{mr['file_type']}脳{mr['cnt']}")
            if summary_parts:
                parts.append(f"鍏ㄩ儴鏂囦欢：{'、'.join(summary_parts)}")

            sections.append("\n".join(parts))

        return "\n\n---\n".join(sections)
    except Exception as e:
        return ""


async def _save_message(conversation_id: str, user_id: int, role: str, content: str, metadata: Optional[Dict] = None):
    try:
        from src.database import get_db
        db = await get_db()

        normalized_role = str(role or '').strip().lower()
        meta_role = ''
        if isinstance(metadata, dict):
            meta_role = str(metadata.get('role') or '').strip().lower()

        if normalized_role == 'assistant':
            conversation_role = meta_role or 'ops'
        elif normalized_role and normalized_role != 'user':
            conversation_role = normalized_role
        else:
            conversation_role = 'ops'

        raw_text = str(content or '').replace('\x00', ' ').strip()
        compact_text = re.sub(r'\s+', ' ', raw_text)
        auto_title = compact_text[:160] if normalized_role == 'user' else ''

        await db.execute(
            """
            INSERT INTO conversations (id, user_id, title, agent_role, created_at, updated_at)
            VALUES (?, ?, '', ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            ON CONFLICT(id) DO UPDATE SET
                updated_at = CURRENT_TIMESTAMP,
                agent_role = CASE
                    WHEN trim(COALESCE(excluded.agent_role, '')) != '' THEN excluded.agent_role
                    ELSE conversations.agent_role
                END
            WHERE conversations.user_id = excluded.user_id
            """,
            (conversation_id, user_id, conversation_role),
        )

        if auto_title:
            await db.execute(
                """
                UPDATE conversations
                SET
                    title = CASE
                        WHEN trim(COALESCE(title, '')) = '' THEN ?
                        ELSE title
                    END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?
                """,
                (auto_title, conversation_id, user_id),
            )

        await db.execute(
            "INSERT INTO messages (conversation_id, user_id, role, content, metadata) VALUES (?, ?, ?, ?, ?)",
            (conversation_id, user_id, role, content, json.dumps(metadata or {}, ensure_ascii=False)),
        )
        await db.commit()
    except Exception as e:
        logger.warning("Failed to save message: %s", e)


# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
# 鍚庡彴浠诲姟鍒嗗彂锛坒ire-and-forget锛岀粨鏋滃啓DB锛?
# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?

async def _dispatch_background(
    user_id: int, conversation_id: str, role: str,
    message: str, reply: str, intent: Any,
    product_ids: Optional[List[int]] = None,
) -> None:
    """Dispatch fire-and-forget background jobs and persist event records."""
    from src.config import ENABLE_QUALITY_CHECK, ENABLE_TRUST_SCORING, ENABLE_LEARNING, ENABLE_ALERTS, ENABLE_METRICS

    quality_score = 0.7
    quality_issues: List[str] = []

    # Phase 1: 璐ㄩ噺妫€鏌?
    if ENABLE_QUALITY_CHECK:
        try:
            from src.core.quality_checker import check_quality, save_quality_check
            result = check_quality(message, reply, role, action=getattr(intent, "action", ""))
            await save_quality_check(role, message, reply, result)
            quality_score = result.score
            quality_issues = result.issues
            await _save_bg_event(user_id, conversation_id, "quality_check", {
                "role": role, "score": round(result.score, 3), "passed": result.passed,
                "dimensions": {k: round(v, 3) for k, v in result.dimensions.items()},
                "issues": result.issues, "suggestions": result.suggestions,
            })
        except Exception as e:
            logger.warning("BG quality check failed: %s", e)

    # Phase 2: 骞跺彂
    tasks = []
    if ENABLE_TRUST_SCORING:
        tasks.append(_bg_trust(user_id, conversation_id, role, quality_score))
    if ENABLE_LEARNING:
        tasks.append(_bg_learning(user_id, conversation_id, role, message, reply, intent, quality_score, quality_issues))
    if ENABLE_LEARNING and conversation_id:
        tasks.append(_bg_longterm_memory(user_id, conversation_id, role, message, reply, intent))
    if ENABLE_ALERTS:
        tasks.append(_bg_alerts(user_id, conversation_id, role, message, reply, quality_score))
    if ENABLE_METRICS:
        tasks.append(_bg_metrics(role, message, reply, intent))
    # 鈽?浜у搧鐭ヨ瘑娌夋穩
    if product_ids and ENABLE_LEARNING and quality_score >= 0.6:
        tasks.append(_bg_product_knowledge(product_ids, message, reply, role))
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _save_bg_event(user_id: int, conversation_id: str, event_type: str, data: dict):
    try:
        from src.database import get_db
        db = await get_db()
        await db.execute(
            "INSERT INTO background_events (user_id, conversation_id, event_type, data) VALUES (?, ?, ?, ?)",
            (user_id, conversation_id, event_type, json.dumps(data, ensure_ascii=False)),
        )
        await db.commit()
    except Exception as e:
        logger.warning("Failed to save bg event: %s", e)


async def _bg_trust(user_id: int, conv_id: str, role: str, quality_score: float):
    try:
        from src.core.trust_scorer import update_trust
        update = await update_trust(role, quality_score, quality_score >= 0.7)
        await _save_bg_event(user_id, conv_id, "trust_update", {
            "role": update.role, "previous_level": update.previous_level,
            "new_level": update.new_level, "new_score": round(update.new_score, 3),
            "delta": round(update.delta, 3),
        })
    except Exception as e:
        logger.warning("BG trust failed: %s", e)


async def _bg_learning(user_id: int, conv_id: str, role: str, message: str, reply: str, intent: Any, quality_score: float, quality_issues: List[str]):
    try:
        from src.core.agent_memory import extract_learnings, save_learnings, extract_routing_keywords, save_routing_keywords
        learnings = extract_learnings(
            role=role, message=message, reply=reply,
            quality_score=quality_score, quality_issues=quality_issues,
            action=getattr(intent, "action", ""),
        )
        saved = await save_learnings(learnings)

        # 鈽?鑷€傚簲鍏抽敭璇嶅涔?
        new_kw = extract_routing_keywords(message, reply, role, quality_score)
        kw_saved = 0
        if new_kw:
            kw_saved = await save_routing_keywords(role, new_kw)

        await _save_bg_event(user_id, conv_id, "learning_applied", {
            "role": role, "extracted": len(learnings), "saved": saved,
            "new_keywords": kw_saved,
        })
    except Exception as e:
        logger.warning("BG learning failed: %s", e)


async def _bg_longterm_memory(user_id: int, conv_id: str, role: str, message: str, reply: str, intent: Any):
    try:
        if int(user_id or 0) <= 0 or not str(conv_id or "").strip():
            return

        from src.services.context_memory import get_context, save_context
        from src.services.profile_service import get_profile, update_profile

        existing_ctx = await get_context(conv_id) or {}
        existing_facts = existing_ctx.get("facts") if isinstance(existing_ctx.get("facts"), dict) else {}
        incoming_facts = _extract_longterm_facts_from_turn(
            message=message,
            reply=reply,
            role=role,
            action=getattr(intent, "action", ""),
            platform=getattr(intent, "platform", ""),
        )
        merged_facts = _merge_longterm_facts(existing_facts, incoming_facts)

        summary = str(merged_facts.get("last_reply_summary") or existing_ctx.get("summary") or "").strip()
        if not summary:
            summary = re.sub(r"\s+", " ", str(reply or "").strip())[:_LONGTERM_SUMMARY_MAX_CHARS]
        if len(summary) > _LONGTERM_SUMMARY_MAX_CHARS:
            summary = summary[:_LONGTERM_SUMMARY_MAX_CHARS] + "..."

        await save_context(conv_id, summary=summary, facts=merged_facts)

        profile = await get_profile(user_id)
        platforms_counter = dict(profile.get("platforms") or {}) if isinstance(profile.get("platforms"), dict) else {}
        interests_counter = dict(profile.get("interests") or {}) if isinstance(profile.get("interests"), dict) else {}

        for platform_key in merged_facts.get("focus_platforms") or []:
            key = str(platform_key or "").strip().lower()[:32]
            if not key:
                continue
            prev = int(platforms_counter.get(key) or 0)
            platforms_counter[key] = min(prev + 1, 999)

        for goal in merged_facts.get("goals") or []:
            key = str(goal or "").strip().lower()[:36]
            if not key:
                continue
            prev = int(interests_counter.get(key) or 0)
            interests_counter[key] = min(prev + 1, 999)

        if len(platforms_counter) > 24:
            top_platforms = sorted(platforms_counter.items(), key=lambda x: int(x[1] or 0), reverse=True)[:24]
            platforms_counter = {k: int(v) for k, v in top_platforms}
        if len(interests_counter) > 32:
            top_interests = sorted(interests_counter.items(), key=lambda x: int(x[1] or 0), reverse=True)[:32]
            interests_counter = {k: int(v) for k, v in top_interests}

        await update_profile(user_id, {
            "persona": str(profile.get("persona") or ""),
            "traits": profile.get("traits") if isinstance(profile.get("traits"), dict) else {},
            "interests": interests_counter,
            "platforms": platforms_counter,
        })

        await _save_bg_event(user_id, conv_id, "longterm_memory_updated", {
            "role": role,
            "goal_count": len(merged_facts.get("goals") or []),
            "constraint_count": len(merged_facts.get("constraints") or []),
            "platforms": list(merged_facts.get("focus_platforms") or [])[:4],
            "summary_chars": len(summary),
        })
    except Exception as e:
        logger.warning("BG longterm memory failed: %s", e)


async def _bg_alerts(user_id: int, conv_id: str, role: str, message: str, reply: str, quality_score: float):
    try:
        from src.core.alert_engine import scan_alerts, save_alerts
        trust_level = await _load_trust(role)
        alerts = scan_alerts(message=message, reply=reply, role=role, quality_score=quality_score, trust_level=trust_level)
        if alerts:
            await save_alerts(alerts)
            for a in alerts:
                await _save_bg_event(user_id, conv_id, "proactive_alert", a.to_dict())
    except Exception as e:
        logger.warning("BG alerts failed: %s", e)


async def _bg_metrics(role: str, message: str, reply: str, intent: Any):
    try:
        from src.database import get_db
        db = await get_db()
        await db.execute(
            "INSERT INTO metrics (metric_type, metric_key, metric_value, metadata) VALUES (?, ?, ?, ?)",
            ("chat", f"chat_{role}_{getattr(intent, 'action', 'unknown')}", 1.0,
             json.dumps({"role": role, "msg_len": len(message), "reply_len": len(reply)}, ensure_ascii=False)),
        )
        await db.commit()
    except Exception as e:
        logger.warning("BG metrics failed: %s", e)


async def _bg_product_knowledge(product_ids: List[int], message: str, reply: str, role: str) -> None:
    """
    Extract product-related insights from the reply and store them into product_knowledge.
    Trigger only when the response contains enough valuable operational signals.
    """
    import re
    try:
        from src.database import get_db
        db = await get_db()

        # 绠€鍗曡鍒欙細妫€娴嬪洖澶嶆槸鍚﹀寘鍚湁浠峰€肩殑浜у搧淇℃伅
        # 瑙﹀彂璇嶏細鍖呭惈鏄庣‘鐨勪骇鍝佺壒鎬ф弿杩?
        value_signals = ["鍗栫偣", "鐗圭偣", "浼樺娍", "瑙勬牸", '参数', '材质', "鍔熻兘", "閫傜敤", "娉ㄦ剰", "寤鸿", '问题', "鏀硅繘", "绔炲搧", "瀵规瘮", "鐩爣瀹㈢兢", "鐥涚偣"]
        signal_count = sum(1 for s in value_signals if s in reply)

        if signal_count < 2:
            return  # 淇″彿涓嶈冻锛屼笉鎻愬彇

        # 鎸夊彞鍒嗗壊锛屾壘鍑哄惈淇″彿璇嶇殑鏈変环鍊煎彞瀛愶紙鏈€澶?鏉★級
        sentences = re.split('[。！？\\n]', reply)
        valuable: List[str] = []
        for sent in sentences:
            sent = sent.strip()
            if len(sent) < 20 or len(sent) > 300:
                continue
            if sum(1 for s in value_signals if s in sent) >= 1:
                valuable.append(sent)
            if len(valuable) >= 3:
                break

        if not valuable:
            return

        for pid in product_ids[:3]:
            for content in valuable:
                await db.execute(
                    """INSERT OR IGNORE INTO product_knowledge (product_id, content_type, content, source_type, source_id, confidence)
                       VALUES (?", 'text', ?, 'chat', ?, 0.65)""",
                    (pid, content, role),
                )
        await db.commit()
    except Exception as e:
        logger.warning("BG product knowledge failed: %s", e)


# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
# 鏍稿績绠￠亾锛歝hat_stream锛坴4.1 鈥?澶欰gent鍗忎綔 + 鍙鍖栦簨浠舵祦锛?
# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?

async def chat_stream(
    message: str,
    user_id: int,
    *,
    role: Optional[str] = None,
    role_lock: Optional[bool] = None,
    conversation_id: Optional[str] = None,
    product_id: Optional[int] = None,
    product_ids: Optional[List[int]] = None,
    workspace_id: Optional[int] = None,
    response_mode: str = _RESPONSE_MODE_EXECUTION,
    learning_level: str = _LEARNING_LEVEL_HIGHER_VOCATIONAL,
    collaboration_mode: str = _COLLABORATION_MODE_AUTO,
    hired_roles: Optional[List[str]] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> AsyncGenerator[PipelineEvent, None]:
    '\n    核心管道 v4.1 ?智能分析全流程可视化?\n\n    事件?\n      status(intent_analyzed)     ?意图分析结果（角?动作/平台/置信?支持角色?\n      status(features_selected)   ?功能预算选择\n      status(context_loaded)      ?上下文加载完成（记忆?信任/产品?\n      status(prompt_built)        ?系统提示构建\n      status(tools_loaded)        ?工具加载（数?名称?\n      token(text)                 ?流式文本\n      tool_call(name, args)       ?LLM决定调用抢?\n      tool_result(name, result)   ?抢能执行结?\n      status(primary_done)        ?主Agent完成\n      collab_start(agents)        ??多Agent协作弢?\n      collab_agent_start(role)    ??支持Agent启动\n      collab_token(role, text)    ??支持Agent流式文本\n      collab_agent_done(role)     ??支持Agent完成\n      collab_done(contributions)  ??协作完成汇?\n      done(...)                   ?全部完成\n    '
    start = time.monotonic()
    llm_usage_totals: Dict[str, int] = _empty_llm_usage_totals()
    pipeline_stage_timings: Dict[str, int] = {}
    _stage_cursor = start

    def _mark_stage(stage_name: str) -> None:
        nonlocal _stage_cursor
        now = time.monotonic()
        pipeline_stage_timings[stage_name] = int(max(0.0, (now - _stage_cursor) * 1000))
        _stage_cursor = now

    from src.core.intent import analyze_intent, TIER_QUICK, TIER_SINGLE, TIER_MULTI
    from src.core.feature_budget import select_features
    from src.core.prompt_builder import build_system_prompt, build_quick_reply_prompt
    from src.llm_client import call_llm_stream
    from src.config import (
        ENABLE_TOOL_USE,
        MAX_TOOL_ROUNDS,
        MAX_TOOL_ENABLED_ROUNDS,
        SINGLE_TIER_TOOL_ENABLED_ROUNDS,
        ENABLE_HANDOFF,
        ENABLE_ENGINEERING_AGENT,
        ENABLE_EXECUTION_ORCHESTRATION,
        ENABLE_PROACTIVE_SEARCH,
        ENABLE_BACKGROUND_TASKS,
        EXECUTION_ACTION_REQUIRE_APPROVAL,
        TOOL_EXECUTION_TIMEOUT_SECONDS,
        SEARCH_TOOL_TIMEOUT_SECONDS,
        MAX_TOOL_CALLS_PER_ROUND,
        SINGLE_TIER_MAX_TOOL_CALLS_PER_ROUND,
        TOOL_EXECUTION_TOTAL_BUDGET_SECONDS,
        SINGLE_TIER_TOOL_EXECUTION_TOTAL_BUDGET_SECONDS,
    )

    effective_response_mode = _normalize_response_mode(response_mode)
    effective_learning_level = _normalize_learning_level(
        learning_level,
        response_mode=effective_response_mode,
    )
    learning_interaction_enabled = effective_response_mode == _RESPONSE_MODE_LEARNING
    learning_structure_template = (
        '30秒结?详细讲解(为什?怎么?常见误区)/自检问题'
        if learning_interaction_enabled
        else ""
    )

    # 鈹€鈹€ Step 0: 鈽?涓婁笅鏂囦赴瀵屽寲 鈥斺€斺€斺€斺€斺€斺€斺€斺€斺€斺€斺€斺€斺€斺€斺€斺€斺€斺€斺€斺€斺€斺€斺€斺€斺€斺€斺€斺€斺€?
    history_texts = []
    enriched_message = message
    context_enriched = False
    normalized_attachments = _normalize_chat_attachments(attachments or [])
    structured_attachments = _normalize_chat_attachments(
        attachments or [],
        max_item_chars=_CHAT_ATTACHMENT_STRUCTURED_MAX_ITEM_CHARS,
        max_total_chars=_CHAT_ATTACHMENT_STRUCTURED_MAX_TOTAL_CHARS,
    )
    attachment_stats = _attachment_quality_stats(normalized_attachments)
    attachment_layout_breakdown = {
        "markdown": int(attachment_stats.get("layout_markdown") or 0),
        "ruled": int(attachment_stats.get("layout_ruled") or 0),
        "space": int(attachment_stats.get("layout_space") or 0),
    }
    attachment_structured = _build_attachment_structured_insights(structured_attachments)
    if conversation_id:
        hist = await _load_history(conversation_id, user_id, limit=6)
        history_texts = [m["content"] for m in hist if m.get("content")]
        try:
            from src.core.context_enricher import enrich_message
            enriched_message, context_enriched = enrich_message(message, hist)
        except Exception as e:
            logger.warning("Context enrichment failed: %s", e)

    attachment_context = _build_attachment_context(
        normalized_attachments,
        attachment_structured=attachment_structured,
    )
    if attachment_context:
        enriched_message = f"{enriched_message}\n\n{attachment_context}"
    requires_verified_sources = _requires_verified_realtime_sources(message)
    verified_sources_ready = not requires_verified_sources
    verified_sources_count = 0
    verified_sources_guardrail_note = ""

    longterm_memory_ctx = ""
    longterm_memory_fact_count = 0
    longterm_memory_context_count = 0
    longterm_profile_persona = ""
    longterm_profile_platforms: List[str] = []

    # 鈹€鈹€ Step 1: 鎰忓浘鍒嗘瀽锛堝惈瀛︿範鍏抽敭璇嶅寮猴級鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    learned_keywords = await _load_learned_keywords()
    intent = analyze_intent(message, history=history_texts, learned_keywords=learned_keywords or None)
    dispatch_intent_profile = _build_dispatch_intent_profile(
        message,
        action=str(intent.action or ""),
        domain_id=str(intent.domain_id or ""),
        history_text="\n".join(history_texts[-6:]) if history_texts else "",
        attachments=normalized_attachments,
        attachment_structured=attachment_structured,
    )
    effective_collaboration_mode = _normalize_collaboration_mode(collaboration_mode)
    role_locked = _resolve_role_lock(
        role=role,
        collaboration_mode=effective_collaboration_mode,
        role_lock=role_lock,
    )
    explicit_collab_requested = _is_explicit_collaboration_requested(
        message,
        collaboration_mode=effective_collaboration_mode,
        hired_roles=hired_roles,
    )

    runtime_role_ctx: Optional[Dict[str, Any]] = None
    try:
        from src.core.role_router import build_runtime_role_context

        runtime_role_ctx = build_runtime_role_context()
    except Exception:
        runtime_role_ctx = None

    task_anchor_source = await _load_recent_task_anchor(conversation_id, user_id)
    task_anchor_guard = _apply_task_anchor_guard(
        intent=intent,
        message=message,
        role_locked=role_locked,
        explicit_collab_requested=explicit_collab_requested,
        task_anchor=task_anchor_source,
        allow_engineering_agent=ENABLE_ENGINEERING_AGENT,
        intent_profile=dispatch_intent_profile,
    )
    dispatch_role_router_adjustment = _apply_role_router_suggestions_to_intent(
        intent,
        message=message,
        collaboration_mode=effective_collaboration_mode,
        role_locked=role_locked,
        explicit_collab_requested=explicit_collab_requested,
        role_ctx=runtime_role_ctx,
        allow_engineering=ENABLE_ENGINEERING_AGENT,
        max_support_roles=_support_agent_role_limit(collaboration_mode=effective_collaboration_mode),
        intent_profile=dispatch_intent_profile,
    )
    dispatch_profile_adjustment = _apply_dispatch_profile_to_intent(
        intent,
        intent_profile=dispatch_intent_profile,
        collaboration_mode=effective_collaboration_mode,
        role_locked=role_locked,
        explicit_collab_requested=explicit_collab_requested,
        role_ctx=runtime_role_ctx,
        allow_engineering=ENABLE_ENGINEERING_AGENT,
    )
    dispatch_debug_fields = _build_dispatch_debug_fields(
        dispatch_intent_profile,
        dispatch_role_router_adjustment,
        dispatch_profile_adjustment,
    )

    role_hint = _normalize_runtime_role_token(
        role,
        allow_default=False,
        role_ctx=runtime_role_ctx,
    )
    role_seed = role_hint if role_locked else (intent.primary_role or role_hint)

    effective_role = _normalize_runtime_role_token(
        role_seed,
        allow_default=True,
        role_ctx=runtime_role_ctx,
    )
    if not effective_role:
        effective_role = str(role_seed or "ops").strip().lower() or "ops"
    if (
        not ENABLE_ENGINEERING_AGENT
        and effective_role == "engineering"
        and effective_collaboration_mode != _COLLABORATION_MODE_MANUAL
    ):
        effective_role = _normalize_runtime_role_token(
            "ops",
            allow_default=True,
            role_ctx=runtime_role_ctx,
        ) or "ops"

    support_role_cap = _support_agent_role_limit(collaboration_mode=effective_collaboration_mode)
    support_roles = _normalize_hired_roles(
        list(intent.support_roles or []),
        allow_engineering=ENABLE_ENGINEERING_AGENT,
        collaboration_mode=effective_collaboration_mode,
        max_roles_override=support_role_cap,
        role_ctx=runtime_role_ctx,
    )
    support_roles = [role_item for role_item in support_roles if role_item != effective_role]

    manual_requested_max_roles = None
    if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL:
        requested_count = len([x for x in (hired_roles or []) if str(x or "").strip()])
        if requested_count > 0:
            manual_requested_max_roles = min(_MANUAL_HIRED_ROLE_SELECTION_HARD_CAP, requested_count + 1)

    manual_hired_roles = _normalize_hired_roles(
        hired_roles or [],
        allow_engineering=ENABLE_ENGINEERING_AGENT,
        collaboration_mode=effective_collaboration_mode,
        max_roles_override=manual_requested_max_roles,
        role_ctx=runtime_role_ctx,
    )

    manual_primary_fallback_used = False
    manual_support_roles: List[str] = []
    if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL:
        manual_primary_role, manual_hired_roles, manual_support_roles, manual_primary_fallback_used = _resolve_manual_collaboration_roles(
            requested_primary_role=effective_role if role_locked else "",
            hired_roles=manual_hired_roles,
            fallback_primary_role=effective_role,
            role_ctx=runtime_role_ctx,
        )
        effective_role = manual_primary_role

    if effective_collaboration_mode == _COLLABORATION_MODE_SINGLE:
        support_roles = []
    elif effective_collaboration_mode == _COLLABORATION_MODE_MANUAL:
        support_roles = list(manual_support_roles)
        support_role_cap = max(support_role_cap, len(support_roles))
    else:
        for hired_role in manual_hired_roles:
            if hired_role != effective_role and hired_role not in support_roles:
                support_roles.append(hired_role)

    support_roles = support_roles[:support_role_cap]

    if effective_collaboration_mode == _COLLABORATION_MODE_AUTO and not explicit_collab_requested:
        support_roles = _filter_support_roles_by_dispatch_profile(
            support_roles,
            primary_role=effective_role,
            intent_profile=dispatch_intent_profile,
        )

    collab_input_requested_roles = int(len(support_roles))
    collab_input_requested_role_list = list(support_roles)
    collab_scheduled_roles = 0
    collab_scheduled_role_list: List[str] = []
    collab_completed_roles = 0
    collab_completed_role_list = []
    collab_truncated_by_budget = False

    if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL:
        manual_schedule_consistency: Dict[str, Any] = _role_list_consistency(support_roles, [])
        manual_completion_consistency: Dict[str, Any] = _role_list_consistency(support_roles, [])
    else:
        manual_schedule_consistency = {
            "match": True,
            "missing": [],
            "extra": [],
            "expected": [],
            "actual": [],
        }
        manual_completion_consistency = {
            "match": True,
            "missing": [],
            "extra": [],
            "expected": [],
            "actual": [],
        }

    primary_display = _role_display_name(effective_role)

    yield PipelineEvent("status", {
        "step": "intent_analyzed",
        "role": effective_role,
        "role_name": primary_display,
        "action": intent.action,
        "tier": intent.tier,
        "tier_label": {0: "quick", 1: "single", 2: "multi"}.get(intent.tier, "single"),
        "confidence": intent.confidence,
        "platform": intent.platform,
        "domain_id": intent.domain_id,
        "domain_confidence": intent.domain_confidence,
        "domain_candidates": intent.domain_candidates,
        "support_roles": support_roles,
        "support_names": [_role_display_name(r) for r in support_roles],
        "context_enriched": context_enriched,
        "keywords": intent.keywords,
        "response_mode": effective_response_mode,
        "learning_level": effective_learning_level,
        "learning_interaction_enabled": learning_interaction_enabled,
        "learning_structure_template": learning_structure_template,
        "collaboration_mode": effective_collaboration_mode,
        "role_locked": role_locked,
        "explicit_collaboration_requested": explicit_collab_requested,
        "hired_roles": manual_hired_roles,
        "manual_primary_role": effective_role if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else "",
        "manual_support_roles": list(support_roles) if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else [],
        "manual_primary_fallback_used": bool(manual_primary_fallback_used),
        "task_anchor_applied": bool(task_anchor_guard.get("applied")),
        "task_anchor_reason": str(task_anchor_guard.get("reason") or ""),
        "task_anchor_source": task_anchor_guard.get("source") if isinstance(task_anchor_guard.get("source"), dict) else {},
        "task_anchor_overrides": list(task_anchor_guard.get("overrides") or []),
        **dispatch_debug_fields,
        "attachment_count": len(normalized_attachments),
        "attachment_parsed_count": attachment_stats.get("parsed", 0),
        "attachment_degraded_count": attachment_stats.get("degraded_vision", 0),
        "attachment_vision_fallback_count": attachment_stats.get("vision_fallback", 0),
        "attachment_layout_table_count": attachment_stats.get("layout_table", 0),
        "attachment_layout_source_breakdown": attachment_layout_breakdown,
        "attachment_table_count": int(attachment_structured.get("table_attachment_count") or 0),
        "attachment_common_fields": list(attachment_structured.get("common_fields") or [])[:6],
        "attachment_primary_keys": list(attachment_structured.get("primary_key_candidates") or [])[:4],
        "attachment_alignment_suggestions": list(attachment_structured.get("alignment_suggestions") or [])[:4],
        "attachment_entity_alignment_summaries": list(attachment_structured.get("entity_alignment_summaries") or [])[:4],
        "attachment_semantic_dictionary_source": str(attachment_structured.get("semantic_dictionary_source") or "")[:120],
        "attachment_semantic_custom_aliases": int(attachment_structured.get("semantic_custom_aliases") or 0),
        "has_attachment_context": bool(attachment_context),
        "requires_verified_sources": bool(requires_verified_sources),
        "verified_sources_ready": bool(verified_sources_ready),
        "verified_sources_count": int(verified_sources_count),
        "longterm_memory_loaded": False,
        "longterm_memory_fact_count": int(longterm_memory_fact_count),
        "longterm_memory_context_count": int(longterm_memory_context_count),
    })

    if requires_verified_sources:
        yield PipelineEvent("status", {
            "step": "verified_sources_required",
            "message": '棢测到你要求使用真实联网数据，系统将按可验证来源进行门禁校验?',
            "requires_verified_sources": True,
        })

    task_framing = _build_task_framing(
        message,
        action=intent.action,
        response_mode=effective_response_mode,
    )
    if task_framing:
        yield PipelineEvent("status", {
            "step": "task_framed",
            **task_framing,
        })

    _mark_stage("intent_and_framing_ms")

    # 鈹€鈹€ Step 1.5: 鈽?涓诲姩閫氱煡寮曟搸 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    if intent.tier != TIER_QUICK:
        try:
            from src.core.proactive_engine import generate_notifications
            notifications = await generate_notifications(user_id)
            if notifications:
                yield PipelineEvent("status", {
                    "step": "proactive_notifications",
                    "notifications": [
                        {"type": n.get("type", ""), "priority": n.get("priority", ""),
                         "message": n.get("message", ""), "action_role": n.get("action_role", "")}
                        for n in notifications[:5]  # 鏈€澶?鏉?
                    ],
                })
        except Exception as e:
            logger.warning("Proactive engine failed: %s", e)

    # 鈹€鈹€ Step 2: Quick reply path 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    quick_path_skipped_for_collaboration = bool(
        intent.tier == TIER_QUICK
        and int(collab_input_requested_roles or 0) > 0
        and (
            bool(explicit_collab_requested)
            or effective_collaboration_mode == _COLLABORATION_MODE_MANUAL
        )
    )
    if quick_path_skipped_for_collaboration:
        yield PipelineEvent("status", {
            "step": "quick_path_skipped_for_collaboration",
            "collaboration_mode": effective_collaboration_mode,
            "input_requested_roles": int(collab_input_requested_roles),
            "input_requested_role_list": list(collab_input_requested_role_list),
            "explicit_collaboration_requested": bool(explicit_collab_requested),
        })

    if intent.tier == TIER_QUICK and not quick_path_skipped_for_collaboration:
        quick_template_reply: Optional[str] = None
        if (not normalized_attachments) and (not requires_verified_sources):
            quick_template_reply = _build_quick_template_reply(message)

        if quick_template_reply:
            reply = quick_template_reply
            quick_llm_usage = dict(llm_usage_totals)
            quick_llm_estimated_cost_usd = _estimate_llm_cost_usd(quick_llm_usage)
            yield PipelineEvent("status", {
                "step": "quick_template_reply",
                "source": "rule_based",
            })
            yield PipelineEvent("token", {"text": reply})
        else:
            quick_prompt = _apply_response_mode_prompt(
                build_quick_reply_prompt(),
                effective_response_mode,
                effective_learning_level,
            )
            quick_prompt = _apply_collaboration_mode_prompt(
                quick_prompt,
                effective_collaboration_mode,
                hired_roles=manual_hired_roles,
                response_mode=effective_response_mode,
            )
            quick_prompt = _apply_strategy_combo_contract_prompt(
                quick_prompt,
                response_mode=effective_response_mode,
                collaboration_mode=effective_collaboration_mode,
                learning_level=effective_learning_level,
                hired_roles=manual_hired_roles,
            )
            quick_prompt = _apply_task_framing_prompt(quick_prompt, task_framing, effective_response_mode)
            parts: List[str] = []
            quick_generation_max_tokens = 360 if effective_response_mode == _RESPONSE_MODE_LEARNING else 420
            async for ev in call_llm_stream(
                system=quick_prompt,
                message=enriched_message,
                max_tokens=quick_generation_max_tokens,
            ):
                if ev["type"] == "token":
                    parts.append(ev["text"])
                    yield PipelineEvent("token", {"text": ev["text"]})
                elif ev["type"] == "done":
                    llm_usage_totals = _merge_llm_usage_totals(llm_usage_totals, ev.get("usage"))
                    break
            reply = "".join(parts)
            quick_llm_usage = dict(llm_usage_totals)
            quick_llm_estimated_cost_usd = _estimate_llm_cost_usd(quick_llm_usage)
        quick_credibility = _compose_credibility_snapshot(
            intent_confidence=float(intent.confidence or 0.0),
            domain_confidence=float(intent.domain_confidence or 0.0),
            trust_level='未评?',
            attachment_stats=attachment_stats,
            realtime_sources=[],
            realtime_sources_count_hint=0,
            realtime_attempted=False,
            has_metrics_ctx=False,
            engine="",
            requires_verified_sources=requires_verified_sources,
            verified_sources_ready=verified_sources_ready,
        )
        quick_guardrail_note = str(quick_credibility.get("guardrail_note") or "")
        if requires_verified_sources and quick_guardrail_note:
            reply = f"{quick_guardrail_note}\n\n{reply}".strip()
            yield PipelineEvent("status", {
                "step": "verified_sources_required_unmet",
                "message": quick_guardrail_note,
                "requires_verified_sources": True,
                "verified_sources_count": int(quick_credibility.get("verified_sources_count") or 0),
                "final": True,
                "source": "quick_path",
            })

        quick_guard_meta = {"applied": False, "gaps": {}}
        reply, quick_guard_meta = _apply_execution_delivery_guard(
            reply,
            response_mode=effective_response_mode,
            role=effective_role,
            action=str(intent.action or ""),
            message=message,
            llm_is_asking=False,
            quality_issues=[],
        )
        if bool(quick_guard_meta.get("applied")):
            yield PipelineEvent("replace_reply", {
                "reply": reply,
                "source": "execution_delivery_guard_quick",
                "gaps": quick_guard_meta.get("gaps") or {},
            })

        quick_learning_guard_meta = {"applied": False, "gaps": {}}
        reply, quick_learning_guard_meta = _apply_learning_delivery_guard(
            reply,
            response_mode=effective_response_mode,
            learning_level=effective_learning_level,
            domain_id=str(intent.domain_id or ""),
            llm_is_asking=False,
            quality_issues=[],
        )
        if bool(quick_learning_guard_meta.get("applied")):
            yield PipelineEvent("replace_reply", {
                "reply": reply,
                "source": "learning_delivery_guard_quick",
                "gaps": quick_learning_guard_meta.get("gaps") or {},
            })

        quick_analysis_guard_meta = {"applied": False, "gaps": {}}
        reply, quick_analysis_guard_meta = _apply_analysis_delivery_guard(
            reply,
            response_mode=effective_response_mode,
            action=str(intent.action or ""),
            llm_is_asking=False,
            quality_issues=[],
        )
        if bool(quick_analysis_guard_meta.get("applied")):
            yield PipelineEvent("replace_reply", {
                "reply": reply,
                "source": "analysis_delivery_guard_quick",
                "gaps": quick_analysis_guard_meta.get("gaps") or {},
            })

        quick_design_guard_meta = {"applied": False, "gaps": {}}
        reply, quick_design_guard_meta = _apply_design_delivery_guard(
            reply,
            response_mode=effective_response_mode,
            role=effective_role,
            action=str(intent.action or ""),
            llm_is_asking=False,
            quality_issues=[],
        )
        if bool(quick_design_guard_meta.get("applied")):
            yield PipelineEvent("replace_reply", {
                "reply": reply,
                "source": "design_delivery_guard_quick",
                "gaps": quick_design_guard_meta.get("gaps") or {},
            })

        quick_mode_differentiation_meta = {"applied": False, "reason": ""}
        reply, quick_mode_differentiation_meta = _apply_mode_differentiation_guard(
            reply,
            response_mode=effective_response_mode,
            message=message,
            llm_is_asking=False,
        )
        if bool(quick_mode_differentiation_meta.get("applied")):
            yield PipelineEvent("replace_reply", {
                "reply": reply,
                "source": "mode_differentiation_guard_quick",
                "reason": str(quick_mode_differentiation_meta.get("reason") or ""),
            })

        quick_collaboration_mode_meta = {"applied": False, "reason": ""}
        reply, quick_collaboration_mode_meta = _apply_collaboration_mode_signature_guard(
            reply,
            collaboration_mode=effective_collaboration_mode,
            hired_roles=manual_hired_roles,
            response_mode=effective_response_mode,
            message=message,
            llm_is_asking=False,
        )
        if bool(quick_collaboration_mode_meta.get("applied")):
            yield PipelineEvent("replace_reply", {
                "reply": reply,
                "source": "collaboration_mode_guard_quick",
                "reason": str(quick_collaboration_mode_meta.get("reason") or ""),
            })

        quick_strategy_combo_meta = {"applied": False, "reason": "", "missing_sections": []}
        reply, quick_strategy_combo_meta = _apply_strategy_combo_contract_guard(
            reply,
            response_mode=effective_response_mode,
            collaboration_mode=effective_collaboration_mode,
            learning_level=effective_learning_level,
            hired_roles=manual_hired_roles,
            message=message,
            llm_is_asking=False,
        )
        if bool(quick_strategy_combo_meta.get("applied")):
            yield PipelineEvent("replace_reply", {
                "reply": reply,
                "source": "strategy_combo_contract_guard_quick",
                "reason": str(quick_strategy_combo_meta.get("reason") or ""),
                "missing_sections": list(quick_strategy_combo_meta.get("missing_sections") or []),
            })

        quick_response_outline = _build_response_outline(reply, effective_response_mode, llm_is_asking=False)
        quick_attachment_citations = _extract_attachment_citations(reply, normalized_attachments)
        quick_collab_not_executed_reason = ""
        quick_collab_not_executed_detail = ""
        if int(collab_input_requested_roles or 0) > 0 and int(collab_scheduled_roles or 0) == 0 and int(collab_completed_roles or 0) == 0:
            quick_collab_not_executed_reason = "quick_path"
            quick_collab_not_executed_detail = _describe_collab_not_executed_reason(quick_collab_not_executed_reason)
        _mark_stage("quick_generation_ms")
        elapsed = time.monotonic() - start
        quick_pipeline_stage_timings = dict(pipeline_stage_timings)
        quick_pipeline_stage_timings["total_ms"] = int(elapsed * 1000)
        yield PipelineEvent("done", {
            "role": effective_role,
            "role_name": primary_display,
            "elapsed_ms": int(elapsed * 1000),
            "tier": "quick",
            "llm_calls": 1,
            "tool_calls_executed": 0,
            "tool_calls_dropped_by_round_cap": 0,
            "tool_calls_dropped_by_budget": 0,
            "tool_calls_dropped_total": 0,
            "tool_execution_budget_exhausted": False,
            "tool_execution_budget_seconds": 0.0,
            "response_mode": effective_response_mode,
            "learning_level": effective_learning_level,
            "learning_interaction_enabled": learning_interaction_enabled,
            "learning_structure_template": learning_structure_template,
            "collaboration_mode": effective_collaboration_mode,
            "role_locked": role_locked,
            "hired_roles": list(manual_hired_roles),
            "manual_primary_role": effective_role if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else "",
            "manual_support_roles": list(final_support_roles) if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else [],
            "manual_primary_fallback_used": bool(manual_primary_fallback_used),
            "collab_input_requested_roles": int(collab_input_requested_roles),
            "collab_input_requested_role_list": list(collab_input_requested_role_list),
            "collab_scheduled_roles": int(collab_scheduled_roles),
            "collab_scheduled_role_list": list(collab_scheduled_role_list),
            "collab_completed_roles": int(collab_completed_roles),
            "collab_completed_role_list": list(collab_completed_role_list),
            "collab_truncated_by_budget": bool(collab_truncated_by_budget),
            "collab_not_executed_reason": quick_collab_not_executed_reason,
            "collab_not_executed_reason_detail": quick_collab_not_executed_detail,
            "manual_support_schedule_match": bool(manual_schedule_consistency.get("match")) if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else True,
            "manual_support_schedule_missing_roles": list(manual_schedule_consistency.get("missing") or []) if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else [],
            "manual_support_schedule_extra_roles": list(manual_schedule_consistency.get("extra") or []) if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else [],
            "manual_support_completion_match": bool(manual_completion_consistency.get("match")) if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else True,
            "manual_support_completion_missing_roles": list(manual_completion_consistency.get("missing") or []) if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else [],
            "manual_support_completion_extra_roles": list(manual_completion_consistency.get("extra") or []) if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else [],
            "attachment_count": len(normalized_attachments),
            "attachment_degraded_count": attachment_stats.get("degraded_vision", 0),
            "attachment_vision_fallback_count": attachment_stats.get("vision_fallback", 0),
            "attachment_layout_table_count": attachment_stats.get("layout_table", 0),
            "attachment_layout_source_breakdown": attachment_layout_breakdown,
            "attachment_citations": quick_attachment_citations,
            "attachment_structured": attachment_structured,
            "credibility": quick_credibility,
            "requires_verified_sources": bool(requires_verified_sources),
            "verified_sources_ready": bool(quick_credibility.get("verified_sources_ready")),
            "verified_sources_count": int(quick_credibility.get("verified_sources_count") or 0),
            "verified_sources_guardrail_note": quick_guardrail_note,
            "longterm_memory_loaded": False,
            "longterm_memory_fact_count": 0,
            "longterm_memory_context_count": 0,
            "longterm_profile_persona": "",
            "longterm_profile_platforms": [],
            **dispatch_debug_fields,
            "response_outline": quick_response_outline,
            "response_short_first": bool(quick_response_outline.get("short_first_ready")),
            "response_action_count": len(quick_response_outline.get("action_points", [])),
            "task_framing": task_framing,
            "task_should_clarify": bool(task_framing.get("should_clarify")),
            "task_anchor_applied": bool(task_anchor_guard.get("applied")),
            "task_anchor_reason": str(task_anchor_guard.get("reason") or ""),
            "task_anchor_overrides": list(task_anchor_guard.get("overrides") or []),
            "llm_usage": quick_llm_usage,
            "llm_estimated_cost_usd": quick_llm_estimated_cost_usd,
            "learning_delivery_guard_applied": bool(quick_learning_guard_meta.get("applied")),
            "learning_delivery_guard_sources": ["learning_delivery_guard_quick"] if bool(quick_learning_guard_meta.get("applied")) else [],
            "learning_delivery_guard_gaps": quick_learning_guard_meta.get("gaps") or {},
            "analysis_delivery_guard_applied": bool(quick_analysis_guard_meta.get("applied")),
            "analysis_delivery_guard_sources": ["analysis_delivery_guard_quick"] if bool(quick_analysis_guard_meta.get("applied")) else [],
            "analysis_delivery_guard_gaps": quick_analysis_guard_meta.get("gaps") or {},
            "design_delivery_guard_applied": bool(quick_design_guard_meta.get("applied")),
            "design_delivery_guard_sources": ["design_delivery_guard_quick"] if bool(quick_design_guard_meta.get("applied")) else [],
            "design_delivery_guard_gaps": quick_design_guard_meta.get("gaps") or {},
            "mode_differentiation_guard_applied": bool(quick_mode_differentiation_meta.get("applied")),
            "mode_differentiation_guard_reason": str(quick_mode_differentiation_meta.get("reason") or ""),
            "collaboration_mode_guard_applied": bool(quick_collaboration_mode_meta.get("applied")),
            "collaboration_mode_guard_reason": str(quick_collaboration_mode_meta.get("reason") or ""),
            "strategy_combo_contract_guard_applied": bool(quick_strategy_combo_meta.get("applied")),
            "strategy_combo_contract_guard_reason": str(quick_strategy_combo_meta.get("reason") or ""),
            "strategy_combo_contract_missing_sections": list(quick_strategy_combo_meta.get("missing_sections") or []),
            "pipeline_stage_timings_ms": quick_pipeline_stage_timings,
        })
        if conversation_id:
            await _save_message(conversation_id, user_id, "user", message)
            await _save_message(
                conversation_id,
                user_id,
                "assistant",
                reply,
                {
                    "role": effective_role,
                    "tier": "quick",
                    "tool_calls_executed": 0,
                    "tool_calls_dropped_by_round_cap": 0,
                    "tool_calls_dropped_by_budget": 0,
                    "tool_calls_dropped_total": 0,
                    "tool_execution_budget_exhausted": False,
                    "tool_execution_budget_seconds": 0.0,
                    "response_mode": effective_response_mode,
                    "learning_level": effective_learning_level,
                    "learning_interaction_enabled": learning_interaction_enabled,
                    "learning_structure_template": learning_structure_template,
                    "collaboration_mode": effective_collaboration_mode,
                    "role_locked": role_locked,
                    "collab_not_executed_reason": quick_collab_not_executed_reason,
                    "collab_not_executed_reason_detail": quick_collab_not_executed_detail,
                    "attachment_count": len(normalized_attachments),
                    "attachment_degraded_count": attachment_stats.get("degraded_vision", 0),
                    "attachment_vision_fallback_count": attachment_stats.get("vision_fallback", 0),
                    "attachment_layout_table_count": attachment_stats.get("layout_table", 0),
                    "attachment_layout_source_breakdown": attachment_layout_breakdown,
                    "attachment_names": [x.get("filename") for x in normalized_attachments],
                    "attachment_citations": quick_attachment_citations,
                    "attachment_structured": attachment_structured,
                    "credibility": quick_credibility,
                    "requires_verified_sources": bool(requires_verified_sources),
                    "verified_sources_ready": bool(quick_credibility.get("verified_sources_ready")),
                    "verified_sources_count": int(quick_credibility.get("verified_sources_count") or 0),
                    "verified_sources_guardrail_note": quick_guardrail_note,
                    "longterm_memory_loaded": False,
                    "longterm_memory_fact_count": 0,
                    "longterm_memory_context_count": 0,
                    "longterm_profile_persona": "",
                    "longterm_profile_platforms": [],
                    **dispatch_debug_fields,
                    "response_outline": quick_response_outline,
                    "response_short_first": bool(quick_response_outline.get("short_first_ready")),
                    "response_action_count": len(quick_response_outline.get("action_points", [])),
                    "task_framing": task_framing,
                    "task_should_clarify": bool(task_framing.get("should_clarify")),
                    "task_anchor_applied": bool(task_anchor_guard.get("applied")),
                    "task_anchor_reason": str(task_anchor_guard.get("reason") or ""),
                    "task_anchor_overrides": list(task_anchor_guard.get("overrides") or []),
                    "llm_usage": quick_llm_usage,
                    "llm_estimated_cost_usd": quick_llm_estimated_cost_usd,
                    "learning_delivery_guard_applied": bool(quick_learning_guard_meta.get("applied")),
                    "learning_delivery_guard_sources": ["learning_delivery_guard_quick"] if bool(quick_learning_guard_meta.get("applied")) else [],
                    "learning_delivery_guard_gaps": quick_learning_guard_meta.get("gaps") or {},
                    "analysis_delivery_guard_applied": bool(quick_analysis_guard_meta.get("applied")),
                    "analysis_delivery_guard_sources": ["analysis_delivery_guard_quick"] if bool(quick_analysis_guard_meta.get("applied")) else [],
                    "analysis_delivery_guard_gaps": quick_analysis_guard_meta.get("gaps") or {},
                    "design_delivery_guard_applied": bool(quick_design_guard_meta.get("applied")),
                    "design_delivery_guard_sources": ["design_delivery_guard_quick"] if bool(quick_design_guard_meta.get("applied")) else [],
                    "design_delivery_guard_gaps": quick_design_guard_meta.get("gaps") or {},
                    "mode_differentiation_guard_applied": bool(quick_mode_differentiation_meta.get("applied")),
                    "mode_differentiation_guard_reason": str(quick_mode_differentiation_meta.get("reason") or ""),
                    "collaboration_mode_guard_applied": bool(quick_collaboration_mode_meta.get("applied")),
                    "collaboration_mode_guard_reason": str(quick_collaboration_mode_meta.get("reason") or ""),
                    "strategy_combo_contract_guard_applied": bool(quick_strategy_combo_meta.get("applied")),
                    "strategy_combo_contract_guard_reason": str(quick_strategy_combo_meta.get("reason") or ""),
                    "strategy_combo_contract_missing_sections": list(quick_strategy_combo_meta.get("missing_sections") or []),
                    "pipeline_stage_timings_ms": quick_pipeline_stage_timings,
                },
            )

        await _log_llm_audit_event(
            user_id=user_id,
            workspace_id=workspace_id,
            stage="quick_path",
            llm_calls=1,
            usage_totals=quick_llm_usage,
            estimated_cost_usd=quick_llm_estimated_cost_usd,
        )
        return


    if _should_use_fast_single_path(
        message=message,
        intent_tier=int(intent.tier or TIER_SINGLE),
        explicit_collab_requested=explicit_collab_requested,
        collaboration_mode=effective_collaboration_mode,
        normalized_attachment_count=len(normalized_attachments),
        requires_verified_sources=requires_verified_sources,
    ):
        fast_single_template_reply = _build_fast_single_template_reply(
            message,
            response_mode=effective_response_mode,
        )
        fast_single_llm_calls = 0

        if fast_single_template_reply:
            reply = fast_single_template_reply
            yield PipelineEvent("status", {
                "step": "fast_single_template_reply",
                "source": "rule_based",
            })
            yield PipelineEvent("token", {"text": reply})
        else:
            fast_single_prompt = _apply_response_mode_prompt(
                _build_fast_single_compact_prompt(
                    response_mode=effective_response_mode,
                    action=str(intent.action or ""),
                ),
                effective_response_mode,
                effective_learning_level,
            )
            fast_single_prompt = _apply_collaboration_mode_prompt(
                fast_single_prompt,
                effective_collaboration_mode,
                hired_roles=manual_hired_roles,
                response_mode=effective_response_mode,
            )
            fast_single_prompt = _apply_strategy_combo_contract_prompt(
                fast_single_prompt,
                response_mode=effective_response_mode,
                collaboration_mode=effective_collaboration_mode,
                learning_level=effective_learning_level,
                hired_roles=manual_hired_roles,
            )
            fast_single_prompt = _apply_task_framing_prompt(
                fast_single_prompt,
                task_framing,
                effective_response_mode,
            )

            yield PipelineEvent("status", {
                "step": "fast_single_path",
                "source": "compact_prompt",
            })

            fast_single_llm_calls = 1
            parts: List[str] = []
            async for ev in call_llm_stream(
                system=fast_single_prompt,
                message=enriched_message,
                temperature=0.2,
                max_tokens=320,
            ):
                if ev["type"] == "token":
                    parts.append(ev["text"])
                    yield PipelineEvent("token", {"text": ev["text"]})
                elif ev["type"] == "done":
                    llm_usage_totals = _merge_llm_usage_totals(llm_usage_totals, ev.get("usage"))
                    break
            reply = "".join(parts).strip()
            if not reply:
                reply = _build_fast_single_fallback_reply(
                    message,
                    response_mode=effective_response_mode,
                )
                yield PipelineEvent("status", {
                    "step": "fast_single_fallback_reply",
                    "source": "rule_based",
                })
                yield PipelineEvent("token", {"text": reply})

        fast_single_llm_usage = dict(llm_usage_totals)
        fast_single_llm_estimated_cost_usd = _estimate_llm_cost_usd(fast_single_llm_usage)
        fast_single_credibility = _compose_credibility_snapshot(
            intent_confidence=float(intent.confidence or 0.0),
            domain_confidence=float(intent.domain_confidence or 0.0),
            trust_level='未评估',
            attachment_stats=attachment_stats,
            realtime_sources=[],
            realtime_sources_count_hint=0,
            realtime_attempted=False,
            has_metrics_ctx=False,
            engine="",
            requires_verified_sources=requires_verified_sources,
            verified_sources_ready=verified_sources_ready,
        )

        fast_single_guardrail_note = str(fast_single_credibility.get("guardrail_note") or "")
        if requires_verified_sources and fast_single_guardrail_note:
            reply = f"{fast_single_guardrail_note}\n\n{reply}".strip()
            yield PipelineEvent("status", {
                "step": "verified_sources_required_unmet",
                "message": fast_single_guardrail_note,
                "requires_verified_sources": True,
                "verified_sources_count": int(fast_single_credibility.get("verified_sources_count") or 0),
                "final": True,
                "source": "fast_single_path",
            })

        fast_single_guard_meta = {"applied": False, "gaps": {}}
        reply, fast_single_guard_meta = _apply_execution_delivery_guard(
            reply,
            response_mode=effective_response_mode,
            role=effective_role,
            action=str(intent.action or ""),
            message=message,
            llm_is_asking=False,
            quality_issues=[],
        )
        if bool(fast_single_guard_meta.get("applied")):
            yield PipelineEvent("replace_reply", {
                "reply": reply,
                "source": "execution_delivery_guard_fast_single",
                "gaps": fast_single_guard_meta.get("gaps") or {},
            })

        fast_single_learning_guard_meta = {"applied": False, "gaps": {}}
        reply, fast_single_learning_guard_meta = _apply_learning_delivery_guard(
            reply,
            response_mode=effective_response_mode,
            learning_level=effective_learning_level,
            domain_id=str(intent.domain_id or ""),
            llm_is_asking=False,
            quality_issues=[],
        )
        if bool(fast_single_learning_guard_meta.get("applied")):
            yield PipelineEvent("replace_reply", {
                "reply": reply,
                "source": "learning_delivery_guard_fast_single",
                "gaps": fast_single_learning_guard_meta.get("gaps") or {},
            })

        fast_single_analysis_guard_meta = {"applied": False, "gaps": {}}
        reply, fast_single_analysis_guard_meta = _apply_analysis_delivery_guard(
            reply,
            response_mode=effective_response_mode,
            action=str(intent.action or ""),
            llm_is_asking=False,
            quality_issues=[],
        )
        if bool(fast_single_analysis_guard_meta.get("applied")):
            yield PipelineEvent("replace_reply", {
                "reply": reply,
                "source": "analysis_delivery_guard_fast_single",
                "gaps": fast_single_analysis_guard_meta.get("gaps") or {},
            })

        fast_single_design_guard_meta = {"applied": False, "gaps": {}}
        reply, fast_single_design_guard_meta = _apply_design_delivery_guard(
            reply,
            response_mode=effective_response_mode,
            role=effective_role,
            action=str(intent.action or ""),
            llm_is_asking=False,
            quality_issues=[],
        )
        if bool(fast_single_design_guard_meta.get("applied")):
            yield PipelineEvent("replace_reply", {
                "reply": reply,
                "source": "design_delivery_guard_fast_single",
                "gaps": fast_single_design_guard_meta.get("gaps") or {},
            })

        fast_single_mode_differentiation_meta = {"applied": False, "reason": ""}
        reply, fast_single_mode_differentiation_meta = _apply_mode_differentiation_guard(
            reply,
            response_mode=effective_response_mode,
            message=message,
            llm_is_asking=False,
        )
        if bool(fast_single_mode_differentiation_meta.get("applied")):
            yield PipelineEvent("replace_reply", {
                "reply": reply,
                "source": "mode_differentiation_guard_fast_single",
                "reason": str(fast_single_mode_differentiation_meta.get("reason") or ""),
            })

        fast_single_collaboration_mode_meta = {"applied": False, "reason": ""}
        reply, fast_single_collaboration_mode_meta = _apply_collaboration_mode_signature_guard(
            reply,
            collaboration_mode=effective_collaboration_mode,
            hired_roles=manual_hired_roles,
            response_mode=effective_response_mode,
            message=message,
            llm_is_asking=False,
        )
        if bool(fast_single_collaboration_mode_meta.get("applied")):
            yield PipelineEvent("replace_reply", {
                "reply": reply,
                "source": "collaboration_mode_guard_fast_single",
                "reason": str(fast_single_collaboration_mode_meta.get("reason") or ""),
            })

        fast_single_strategy_combo_meta = {"applied": False, "reason": "", "missing_sections": []}
        reply, fast_single_strategy_combo_meta = _apply_strategy_combo_contract_guard(
            reply,
            response_mode=effective_response_mode,
            collaboration_mode=effective_collaboration_mode,
            learning_level=effective_learning_level,
            hired_roles=manual_hired_roles,
            message=message,
            llm_is_asking=False,
        )
        if bool(fast_single_strategy_combo_meta.get("applied")):
            yield PipelineEvent("replace_reply", {
                "reply": reply,
                "source": "strategy_combo_contract_guard_fast_single",
                "reason": str(fast_single_strategy_combo_meta.get("reason") or ""),
                "missing_sections": list(fast_single_strategy_combo_meta.get("missing_sections") or []),
            })

        fast_single_response_outline = _build_response_outline(
            reply,
            effective_response_mode,
            llm_is_asking=False,
        )
        fast_single_attachment_citations = _extract_attachment_citations(reply, normalized_attachments)
        _mark_stage("fast_single_generation_ms")
        elapsed = time.monotonic() - start
        fast_single_pipeline_stage_timings = dict(pipeline_stage_timings)
        fast_single_pipeline_stage_timings["total_ms"] = int(elapsed * 1000)

        yield PipelineEvent("done", {
            "role": effective_role,
            "role_name": primary_display,
            "elapsed_ms": int(elapsed * 1000),
            "tier": "single",
            "path": "fast_single",
            "llm_calls": int(max(0, fast_single_llm_calls)),
            "tool_calls_executed": 0,
            "tool_calls_dropped_by_round_cap": 0,
            "tool_calls_dropped_by_budget": 0,
            "tool_calls_dropped_total": 0,
            "tool_execution_budget_exhausted": False,
            "tool_execution_budget_seconds": 0.0,
            "features_used": [],
            "skills_used": [],
            "support_roles": [],
            "support_names": [],
            "response_mode": effective_response_mode,
            "learning_level": effective_learning_level,
            "learning_interaction_enabled": learning_interaction_enabled,
            "learning_structure_template": learning_structure_template,
            "collaboration_mode": effective_collaboration_mode,
            "role_locked": role_locked,
            "attachment_count": len(normalized_attachments),
            "attachment_degraded_count": attachment_stats.get("degraded_vision", 0),
            "attachment_vision_fallback_count": attachment_stats.get("vision_fallback", 0),
            "attachment_layout_table_count": attachment_stats.get("layout_table", 0),
            "attachment_layout_source_breakdown": attachment_layout_breakdown,
            "attachment_citations": fast_single_attachment_citations,
            "attachment_structured": attachment_structured,
            "credibility": fast_single_credibility,
            "requires_verified_sources": bool(requires_verified_sources),
            "verified_sources_ready": bool(fast_single_credibility.get("verified_sources_ready")),
            "verified_sources_count": int(fast_single_credibility.get("verified_sources_count") or 0),
            "verified_sources_guardrail_note": fast_single_guardrail_note,
            "longterm_memory_loaded": False,
            "longterm_memory_fact_count": 0,
            "longterm_memory_context_count": 0,
            "longterm_profile_persona": "",
            "longterm_profile_platforms": [],
            **dispatch_debug_fields,
            "response_outline": fast_single_response_outline,
            "response_short_first": bool(fast_single_response_outline.get("short_first_ready")),
            "response_action_count": len(fast_single_response_outline.get("action_points", [])),
            "task_framing": task_framing,
            "task_should_clarify": bool(task_framing.get("should_clarify")),
            "task_anchor_applied": bool(task_anchor_guard.get("applied")),
            "task_anchor_reason": str(task_anchor_guard.get("reason") or ""),
            "task_anchor_overrides": list(task_anchor_guard.get("overrides") or []),
            "llm_usage": fast_single_llm_usage,
            "llm_estimated_cost_usd": fast_single_llm_estimated_cost_usd,
            "learning_delivery_guard_applied": bool(fast_single_learning_guard_meta.get("applied")),
            "learning_delivery_guard_sources": ["learning_delivery_guard_fast_single"] if bool(fast_single_learning_guard_meta.get("applied")) else [],
            "learning_delivery_guard_gaps": fast_single_learning_guard_meta.get("gaps") or {},
            "analysis_delivery_guard_applied": bool(fast_single_analysis_guard_meta.get("applied")),
            "analysis_delivery_guard_sources": ["analysis_delivery_guard_fast_single"] if bool(fast_single_analysis_guard_meta.get("applied")) else [],
            "analysis_delivery_guard_gaps": fast_single_analysis_guard_meta.get("gaps") or {},
            "design_delivery_guard_applied": bool(fast_single_design_guard_meta.get("applied")),
            "design_delivery_guard_sources": ["design_delivery_guard_fast_single"] if bool(fast_single_design_guard_meta.get("applied")) else [],
            "design_delivery_guard_gaps": fast_single_design_guard_meta.get("gaps") or {},
            "mode_differentiation_guard_applied": bool(fast_single_mode_differentiation_meta.get("applied")),
            "mode_differentiation_guard_reason": str(fast_single_mode_differentiation_meta.get("reason") or ""),
            "collaboration_mode_guard_applied": bool(fast_single_collaboration_mode_meta.get("applied")),
            "collaboration_mode_guard_reason": str(fast_single_collaboration_mode_meta.get("reason") or ""),
            "strategy_combo_contract_guard_applied": bool(fast_single_strategy_combo_meta.get("applied")),
            "strategy_combo_contract_guard_reason": str(fast_single_strategy_combo_meta.get("reason") or ""),
            "strategy_combo_contract_missing_sections": list(fast_single_strategy_combo_meta.get("missing_sections") or []),
            "pipeline_stage_timings_ms": fast_single_pipeline_stage_timings,
        })

        if conversation_id:
            await _save_message(conversation_id, user_id, "user", message)
            await _save_message(
                conversation_id,
                user_id,
                "assistant",
                reply,
                {
                    "role": effective_role,
                    "tier": "single",
                    "path": "fast_single",
                    "llm_calls": int(max(0, fast_single_llm_calls)),
                    "tool_calls_executed": 0,
                    "tool_calls_dropped_by_round_cap": 0,
                    "tool_calls_dropped_by_budget": 0,
                    "tool_calls_dropped_total": 0,
                    "tool_execution_budget_exhausted": False,
                    "tool_execution_budget_seconds": 0.0,
                    "features_used": [],
                    "skills_used": [],
                    "support_roles": [],
                    "support_names": [],
                    "response_mode": effective_response_mode,
                    "learning_level": effective_learning_level,
                    "learning_interaction_enabled": learning_interaction_enabled,
                    "learning_structure_template": learning_structure_template,
                    "collaboration_mode": effective_collaboration_mode,
                    "role_locked": role_locked,
                    "attachment_count": len(normalized_attachments),
                    "attachment_degraded_count": attachment_stats.get("degraded_vision", 0),
                    "attachment_vision_fallback_count": attachment_stats.get("vision_fallback", 0),
                    "attachment_layout_table_count": attachment_stats.get("layout_table", 0),
                    "attachment_layout_source_breakdown": attachment_layout_breakdown,
                    "attachment_names": [x.get("filename") for x in normalized_attachments],
                    "attachment_citations": fast_single_attachment_citations,
                    "attachment_structured": attachment_structured,
                    "credibility": fast_single_credibility,
                    "requires_verified_sources": bool(requires_verified_sources),
                    "verified_sources_ready": bool(fast_single_credibility.get("verified_sources_ready")),
                    "verified_sources_count": int(fast_single_credibility.get("verified_sources_count") or 0),
                    "verified_sources_guardrail_note": fast_single_guardrail_note,
                    "longterm_memory_loaded": False,
                    "longterm_memory_fact_count": 0,
                    "longterm_memory_context_count": 0,
                    "longterm_profile_persona": "",
                    "longterm_profile_platforms": [],
                    **dispatch_debug_fields,
                    "response_outline": fast_single_response_outline,
                    "response_short_first": bool(fast_single_response_outline.get("short_first_ready")),
                    "response_action_count": len(fast_single_response_outline.get("action_points", [])),
                    "task_framing": task_framing,
                    "task_should_clarify": bool(task_framing.get("should_clarify")),
                    "task_anchor_applied": bool(task_anchor_guard.get("applied")),
                    "task_anchor_reason": str(task_anchor_guard.get("reason") or ""),
                    "task_anchor_overrides": list(task_anchor_guard.get("overrides") or []),
                    "llm_usage": fast_single_llm_usage,
                    "llm_estimated_cost_usd": fast_single_llm_estimated_cost_usd,
                    "learning_delivery_guard_applied": bool(fast_single_learning_guard_meta.get("applied")),
                    "learning_delivery_guard_sources": ["learning_delivery_guard_fast_single"] if bool(fast_single_learning_guard_meta.get("applied")) else [],
                    "learning_delivery_guard_gaps": fast_single_learning_guard_meta.get("gaps") or {},
                    "analysis_delivery_guard_applied": bool(fast_single_analysis_guard_meta.get("applied")),
                    "analysis_delivery_guard_sources": ["analysis_delivery_guard_fast_single"] if bool(fast_single_analysis_guard_meta.get("applied")) else [],
                    "analysis_delivery_guard_gaps": fast_single_analysis_guard_meta.get("gaps") or {},
                    "design_delivery_guard_applied": bool(fast_single_design_guard_meta.get("applied")),
                    "design_delivery_guard_sources": ["design_delivery_guard_fast_single"] if bool(fast_single_design_guard_meta.get("applied")) else [],
                    "design_delivery_guard_gaps": fast_single_design_guard_meta.get("gaps") or {},
                    "mode_differentiation_guard_applied": bool(fast_single_mode_differentiation_meta.get("applied")),
                    "mode_differentiation_guard_reason": str(fast_single_mode_differentiation_meta.get("reason") or ""),
                    "collaboration_mode_guard_applied": bool(fast_single_collaboration_mode_meta.get("applied")),
                    "collaboration_mode_guard_reason": str(fast_single_collaboration_mode_meta.get("reason") or ""),
                    "strategy_combo_contract_guard_applied": bool(fast_single_strategy_combo_meta.get("applied")),
                    "strategy_combo_contract_guard_reason": str(fast_single_strategy_combo_meta.get("reason") or ""),
                    "strategy_combo_contract_missing_sections": list(fast_single_strategy_combo_meta.get("missing_sections") or []),
                    "pipeline_stage_timings_ms": fast_single_pipeline_stage_timings,
                },
            )

        await _log_llm_audit_event(
            user_id=user_id,
            workspace_id=workspace_id,
            stage="fast_single_path",
            llm_calls=int(max(0, fast_single_llm_calls)),
            usage_totals=fast_single_llm_usage,
            estimated_cost_usd=fast_single_llm_estimated_cost_usd,
        )
        return
    # 鈹€鈹€ Step 3: 鍔熻兘棰勭畻 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    features = select_features(message, effective_role, platform=intent.platform)
    yield PipelineEvent("status", {"step": "features_selected", "features": features})

    # 鈹€鈹€ Step 4: 鍔犺浇涓婁笅鏂囷紙骞跺彂锛夆攢鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    # 鍚堝苟 product_id 鍜?product_ids
    all_product_ids: List[int] = list(product_ids or [])
    if product_id and product_id not in all_product_ids:
        all_product_ids.insert(0, product_id)

    memories, trust_level, product_ctx = await asyncio.gather(
        _load_memories(effective_role),
        _load_trust(effective_role),
        _load_product_ctx_rich(all_product_ids, query=message),
    )

    # 鈹€鈹€ Step 4.5: 鍔犺浇鐪熷疄搴楅摵鎸囨爣 + 鈽呬富鍔ㄦ悳绱紙骞跺彂锛屽悇鑷嫭绔嬶級鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    metrics_context = ""
    proactive_search_ctx = ""
    proactive_sources: List[Dict[str, str]] = []
    proactive_engine = "pipeline"
    realtime_sources: List[Dict[str, str]] = []
    search_engines: List[str] = []
    realtime_attempted = False

    async def _load_metrics() -> str:
        if effective_role not in ("data", "ops", "accounting", "service"):
            return ""
        try:
            from src.core.metrics_store import get_summary, detect_anomalies, format_for_prompt
            _s, _a = await asyncio.gather(get_summary(user_id, days=30), detect_anomalies(user_id, days=14))
            return format_for_prompt(_s, _a) if _s.get("has_data") else ""
        except Exception as _e:
            logger.debug("metrics_store load failed: %s", _e)
            return ""

    needs_realtime_data = _needs_real_time_data(message) or requires_verified_sources
    realtime_attempted = bool(needs_realtime_data and ENABLE_PROACTIVE_SEARCH)

    async def _run_proactive_search() -> Dict[str, Any]:
        if not needs_realtime_data or not ENABLE_PROACTIVE_SEARCH:
            return {}
        try:
            return await _proactive_search(message, effective_role)
        except Exception:
            return {}

    async def _run_longterm_memory_load() -> Dict[str, Any]:
        if int(user_id or 0) <= 0:
            return {}
        try:
            return await _load_longterm_memory_bundle(user_id=user_id, conversation_id=conversation_id)
        except Exception as _e:
            logger.debug("longterm memory load failed: %s", _e)
            return {}

    metrics_context, proactive_payload, longterm_payload = await asyncio.gather(
        _load_metrics(),
        _run_proactive_search(),
        _run_longterm_memory_load(),
    )
    if isinstance(proactive_payload, dict):
        proactive_search_ctx = str(proactive_payload.get("prompt_text") or "")
        proactive_sources = _merge_realtime_sources(proactive_payload.get("sources") or [], limit=8)
        proactive_engine = str(proactive_payload.get("engine") or "pipeline")

    if isinstance(longterm_payload, dict):
        longterm_memory_ctx = str(longterm_payload.get("prompt_text") or "")
        longterm_memory_fact_count = int(longterm_payload.get("fact_count") or 0)
        longterm_memory_context_count = int(longterm_payload.get("context_count") or 0)
        longterm_profile_persona = str(longterm_payload.get("persona") or "")
        longterm_profile_platforms = list(longterm_payload.get("platforms") or [])[:4]

    if proactive_engine and (proactive_search_ctx or proactive_sources):
        search_engines.append(proactive_engine)
    realtime_sources = _merge_realtime_sources(realtime_sources, proactive_sources, limit=12)
    realtime_sources_count = len(realtime_sources) if realtime_sources else _count_realtime_sources(proactive_search_ctx)
    verified_sources_count = _count_verified_realtime_sources(realtime_sources)
    verified_sources_ready = (not requires_verified_sources) or verified_sources_count > 0
    if requires_verified_sources and not verified_sources_ready:
        verified_sources_guardrail_note = _VERIFIED_SOURCE_GUARDRAIL_NOTE

    if proactive_search_ctx:
        yield PipelineEvent("search_used", {
            "skill": "proactive_pipeline_search",
            "engine": proactive_engine or "pipeline",
            "sources_count": realtime_sources_count,
            "sources": realtime_sources[:5],
        })
    elif needs_realtime_data:
        # 涓嶅啀闈欓粯澶辫触锛氭樉寮忓憡璇夊墠绔€滃凡灏濊瘯浣嗘湭鎷垮埌澶栭儴缁撴灉鈥濄€?
        unavailable_message = (
            '已尝试实时联网搜索，但未获取到有效结果；将先基于已有知识回答?'
            if ENABLE_PROACTIVE_SEARCH
            else '当前联网棢索能力暂不可用；将先基于已有知识回答?'
        )
        yield PipelineEvent("status", {
            "step": "search_unavailable",
            "message": unavailable_message,
        })
        if requires_verified_sources and not verified_sources_ready:
            yield PipelineEvent("status", {
                "step": "verified_sources_required_unmet",
                "message": verified_sources_guardrail_note or _VERIFIED_SOURCE_GUARDRAIL_NOTE,
                "requires_verified_sources": True,
                "verified_sources_count": int(verified_sources_count),
                "source": "proactive_pipeline_search",
            })

    # 鈹€鈹€ Step 4.55: 鍔犺浇璺ㄨ鑹茬煡璇嗘儏鎶?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    cross_role_context = ""
    try:
        db_cx = await get_db()
        cx_rows = await db_cx.execute_fetchall(
            """SELECT from_role, content FROM role_shared_context
               WHERE to_role=? AND workspace_id IS NULL
               AND created_at >= datetime('now', '-7 days')
               ORDER BY created_at DESC LIMIT 5""",
            (effective_role,),
        )
        if cx_rows:
            parts_cx = [f"{r[0]}瑙掕壊：{r[1]}" for r in cx_rows]
            cross_role_context = "\n".join(parts_cx)
    except Exception as _e:
        logger.debug("cross_role_context load failed: %s", _e)

    # 鈹€鈹€ Step 4.6: 鈽?瀹炴椂杩愯惀瑙勫垯瑙﹀彂寮曟搸 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    active_triggers = []
    operational_alert_text = ""
    if effective_role in ("data", "ops", "accounting", "service") and user_id:
        try:
            from src.core.operational_rules import get_active_triggers
            active_triggers, operational_alert_text = await get_active_triggers(user_id)
            if active_triggers:
                yield PipelineEvent("status", {
                    "step": "operational_rules_triggered",
                    "trigger_count": len(active_triggers),
                    "triggers": [t.to_dict() for t in active_triggers[:3]],
                })
        except Exception as _e:
            logger.debug("operational_rules failed: %s", _e)

    yield PipelineEvent("status", {
        "step": "context_loaded",
        "memories_count": len(memories),
        "trust_level": trust_level or '未评?',
        "has_product_ctx": bool(product_ctx),
        "has_metrics_ctx": bool(metrics_context),
        "has_attachments": bool(normalized_attachments),
        "attachment_parsed_count": attachment_stats.get("parsed", 0),
        "attachment_degraded_count": attachment_stats.get("degraded_vision", 0),
        "attachment_vision_fallback_count": attachment_stats.get("vision_fallback", 0),
        "attachment_layout_table_count": attachment_stats.get("layout_table", 0),
        "attachment_layout_source_breakdown": attachment_layout_breakdown,
        "attachment_table_count": int(attachment_structured.get("table_attachment_count") or 0),
        "realtime_sources_count": realtime_sources_count,
        "requires_verified_sources": bool(requires_verified_sources),
        "verified_sources_ready": bool(verified_sources_ready),
        "verified_sources_count": int(verified_sources_count),
        "longterm_memory_loaded": bool(longterm_memory_ctx),
        "longterm_memory_fact_count": int(longterm_memory_fact_count),
        "longterm_memory_context_count": int(longterm_memory_context_count),
        "longterm_profile_persona": longterm_profile_persona,
        "longterm_profile_platforms": list(longterm_profile_platforms),
    })

    if longterm_memory_ctx:
        yield PipelineEvent("status", {
            "step": "longterm_memory_loaded",
            "fact_count": int(longterm_memory_fact_count),
            "context_count": int(longterm_memory_context_count),
            "persona": longterm_profile_persona,
            "platforms": list(longterm_profile_platforms),
        })

    credibility_snapshot = _compose_credibility_snapshot(
        intent_confidence=float(intent.confidence or 0.0),
        domain_confidence=float(intent.domain_confidence or 0.0),
        trust_level=trust_level or '未评?',
        attachment_stats=attachment_stats,
        realtime_sources=realtime_sources,
        realtime_sources_count_hint=realtime_sources_count,
        realtime_attempted=realtime_attempted,
        has_metrics_ctx=bool(metrics_context),
        engine=_join_search_engines(search_engines),
        requires_verified_sources=requires_verified_sources,
        verified_sources_ready=verified_sources_ready,
    )
    yield PipelineEvent("status", {
        "step": "credibility_snapshot",
        **credibility_snapshot,
        "attachment_layout_table_count": attachment_stats.get("layout_table", 0),
        "attachment_layout_source_breakdown": attachment_layout_breakdown,
    })

    _mark_stage("context_enrichment_ms")

    # 鈹€鈹€ Step 5: 鏋勫缓prompt 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    # 灏嗚繍钀ヨ鍒欏憡璀︽枃鏈悎骞跺埌 metrics_context 涓紙澶嶇敤鐜版湁娉ㄥ叆閫氶亾锛?
    effective_metrics_ctx = metrics_context
    if operational_alert_text:
        effective_metrics_ctx = (
            f"{metrics_context}\n\n{operational_alert_text}" if metrics_context
            else operational_alert_text
        )

    effective_cross_role_context = cross_role_context
    if longterm_memory_ctx:
        effective_cross_role_context = (
            f"{cross_role_context}\n\n{longterm_memory_ctx}" if cross_role_context
            else longterm_memory_ctx
        )

    system_prompt = build_system_prompt(
        role=effective_role, message=message, memories=memories,
        platform=intent.platform, product_context=product_ctx,
        trust_level=trust_level, features=features,
        metrics_context=effective_metrics_ctx,
        cross_role_context=effective_cross_role_context,
        realtime_context=proactive_search_ctx,
        domain_context=intent.domain_id,
    )
    system_prompt = _apply_response_mode_prompt(
        system_prompt,
        effective_response_mode,
        effective_learning_level,
    )
    system_prompt = _apply_collaboration_mode_prompt(
        system_prompt,
        effective_collaboration_mode,
        hired_roles=manual_hired_roles,
        response_mode=effective_response_mode,
    )
    system_prompt = _apply_strategy_combo_contract_prompt(
        system_prompt,
        response_mode=effective_response_mode,
        collaboration_mode=effective_collaboration_mode,
        learning_level=effective_learning_level,
        hired_roles=manual_hired_roles,
    )
    system_prompt = _apply_task_framing_prompt(system_prompt, task_framing, effective_response_mode)
    yield PipelineEvent("status", {"step": "prompt_built", "prompt_length": len(system_prompt)})

    # 鈹€鈹€ Step 6: 鍔犺浇瀵硅瘽鍘嗗彶 + 宸ュ叿 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    history = await _load_history(conversation_id, user_id, limit=10) if conversation_id else []

    tools = None
    tool_names: List[str] = []
    tool_use_enabled = bool(ENABLE_TOOL_USE) and _should_enable_tool_use_for_request(
        message=message,
        requires_verified_sources=bool(requires_verified_sources),
        workspace_id=workspace_id,
        product_id=product_id,
        product_ids=all_product_ids,
    )
    if tool_use_enabled:
        try:
            from src.skills.registry import get_registry
            reg = get_registry()
            all_tools = reg.get_tools_for_role(effective_role)
            # 鈹€鈹€ 鎶€鑳介绛涢€夛細瓒呰繃8涓椂鎸夎涔夌浉鍏虫€ц瘎鍒嗭紝淇濈暀top-8 + coordination宸ュ叿 鈹€鈹€
            tools = _prefilter_tools(
                all_tools,
                message,
                max_role_tools=8,
                role=effective_role,
                action=str(intent.action or ""),
                domain_id=str(intent.domain_id or ""),
                intent_profile=dispatch_intent_profile,
            )
            tool_names = [t["function"]["name"] for t in tools] if tools else []
        except Exception:
            tools = None

    yield PipelineEvent("status", {
        "step": "tools_loaded",
        "tool_count": len(tool_names),
        "tools": tool_names[:10],  # 鏈€澶氬睍绀?0涓?
        "tool_use_enabled": bool(tool_use_enabled),
    })

    _mark_stage("prompt_and_tool_loading_ms")

    # 鈹€鈹€ Step 7: LLM娴佸紡璋冪敤 + tool_use寰幆 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    reply_parts: List[str] = []
    llm_messages = list(history) if history else []
    if system_prompt:
        llm_messages.insert(0, {"role": "system", "content": system_prompt})
    llm_messages.append({"role": "user", "content": enriched_message})

    llm_calls = 0
    skills_used: List[str] = []
    primary_generation_max_tokens = _resolve_primary_generation_max_tokens(
        response_mode=effective_response_mode,
        tier=int(intent.tier),
        message=message,
        has_attachments=bool(normalized_attachments),
        requires_verified_sources=bool(requires_verified_sources),
    )
    if effective_response_mode == _RESPONSE_MODE_LEARNING:
        primary_generation_max_tokens = min(
            int(primary_generation_max_tokens),
            int(_LEARNING_PRIMARY_MAX_TOKENS_CAP),
        )
        if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL and manual_hired_roles:
            primary_generation_max_tokens = min(
                int(primary_generation_max_tokens),
                int(_LEARNING_MANUAL_PRIMARY_MAX_TOKENS_CAP),
            )
    quality_retry_max_tokens = _resolve_quality_retry_max_tokens(
        response_mode=effective_response_mode,
        primary_generation_max_tokens=primary_generation_max_tokens,
    )
    default_tool_timeout_seconds = max(8, int(TOOL_EXECUTION_TIMEOUT_SECONDS or 45))
    search_tool_timeout_seconds = max(8, int(SEARCH_TOOL_TIMEOUT_SECONDS or default_tool_timeout_seconds))
    max_tool_enabled_rounds = max(0, int(MAX_TOOL_ENABLED_ROUNDS))
    single_tier_tool_enabled_rounds = max(0, int(SINGLE_TIER_TOOL_ENABLED_ROUNDS))
    max_tool_calls_per_round = max(0, int(MAX_TOOL_CALLS_PER_ROUND))
    single_tier_max_tool_calls_per_round = max(0, int(SINGLE_TIER_MAX_TOOL_CALLS_PER_ROUND))
    tool_execution_total_budget_seconds = max(0.0, float(TOOL_EXECUTION_TOTAL_BUDGET_SECONDS))
    single_tier_tool_execution_total_budget_seconds = max(
        0.0,
        float(SINGLE_TIER_TOOL_EXECUTION_TOTAL_BUDGET_SECONDS),
    )

    # 单岗请求常被误触发 tool_call 长尾，这里加硬保护上限，优先保证交互时延。
    single_tier_tool_timeout_cap_seconds = 10.0
    single_tier_tool_budget_cap_seconds = 20.0

    if int(intent.tier) <= 1:
        max_tool_enabled_rounds = min(max_tool_enabled_rounds, single_tier_tool_enabled_rounds)
        max_tool_calls_per_round = min(max_tool_calls_per_round, single_tier_max_tool_calls_per_round)
        tool_execution_total_budget_seconds = min(
            tool_execution_total_budget_seconds,
            single_tier_tool_execution_total_budget_seconds,
        )
        default_tool_timeout_seconds = min(float(default_tool_timeout_seconds), single_tier_tool_timeout_cap_seconds)
        search_tool_timeout_seconds = min(float(search_tool_timeout_seconds), single_tier_tool_timeout_cap_seconds)
        tool_execution_total_budget_seconds = min(
            float(tool_execution_total_budget_seconds),
            single_tier_tool_budget_cap_seconds,
        )

    tool_execution_budget_deadline = time.monotonic() + float(tool_execution_total_budget_seconds)
    tool_calls_executed = 0
    tool_calls_dropped_by_round_cap = 0
    tool_calls_dropped_by_budget = 0
    tool_execution_budget_exhausted = False

    for _round in range(MAX_TOOL_ROUNDS + 1):
        pending_tool_calls: List[Dict[str, Any]] = []
        llm_calls += 1
        tools_for_round = tools if (tools and _round < max_tool_enabled_rounds and not tool_execution_budget_exhausted) else None

        async for ev in call_llm_stream(
            system="" if llm_messages[0].get("role") == "system" else system_prompt,
            message="" if llm_messages else message,
            history=llm_messages if llm_messages else None,
            tools=tools_for_round,
            max_tokens=primary_generation_max_tokens,
        ):
            if ev["type"] == "token":
                reply_parts.append(ev["text"])
                yield PipelineEvent("token", {"text": ev["text"]})
            elif ev["type"] == "tool_call":
                pending_tool_calls.append(ev)
                yield PipelineEvent("tool_call", {
                    "name": ev.get("name", ""),
                    "args": ev.get("args", {}),
                    "status": "running",
                    "round": _round + 1,
                })
            elif ev["type"] == "done":
                llm_usage_totals = _merge_llm_usage_totals(llm_usage_totals, ev.get("usage"))
                break

        # 没有 tool_call 则直接结束
        if not pending_tool_calls:
            break

        # 达到工具轮数限制后，忽略后续 tool_call，避免多轮链式调用导致长尾时延
        if tools_for_round is None:
            limit_step = "tool_budget_exhausted" if tool_execution_budget_exhausted else "tool_round_limit_reached"
            yield PipelineEvent("status", {
                "step": limit_step,
                "round": _round + 1,
                "max_tool_enabled_rounds": int(max_tool_enabled_rounds),
                "ignored_tool_calls": len(pending_tool_calls),
                "tool_execution_budget_exhausted": bool(tool_execution_budget_exhausted),
                "tool_execution_budget_seconds": float(tool_execution_total_budget_seconds),
            })
            pending_tool_calls.clear()
            break

        # 执行技能并回传结果
        if len(pending_tool_calls) > max_tool_calls_per_round:
            dropped_count = len(pending_tool_calls) - max_tool_calls_per_round
            tool_calls_dropped_by_round_cap += dropped_count
            pending_tool_calls = pending_tool_calls[:max_tool_calls_per_round]
            yield PipelineEvent("status", {
                "step": "tool_call_round_cap_applied",
                "round": _round + 1,
                "max_tool_calls_per_round": int(max_tool_calls_per_round),
                "dropped_tool_calls": int(dropped_count),
            })

        if not pending_tool_calls:
            break

        assistant_tc = []
        for tc in pending_tool_calls:
            assistant_tc.append({
                "id": tc.get("id", ""),
                "type": "function",
                "function": {"name": tc["name"], "arguments": json.dumps(tc.get("args", {}))},
            })

        # 娣诲姞assistant娑堟伅锛堝惈tool_calls锛?
        llm_messages.append({
            "role": "assistant",
            "content": "".join(reply_parts),
            "tool_calls": assistant_tc,
        })
        reply_parts.clear()

        # 鎵ц姣忎釜宸ュ叿骞舵坊鍔爐ool缁撴灉
        for tc_idx, tc in enumerate(pending_tool_calls):
            remaining_budget_seconds = float(tool_execution_budget_deadline - time.monotonic())
            if remaining_budget_seconds <= 0.5:
                tool_execution_budget_exhausted = True
                skipped_calls = pending_tool_calls[tc_idx:]
                tool_calls_dropped_by_budget += len(skipped_calls)
                yield PipelineEvent("status", {
                    "step": "tool_budget_exhausted",
                    "round": _round + 1,
                    "tool_execution_budget_seconds": float(tool_execution_total_budget_seconds),
                    "tool_calls_executed": int(tool_calls_executed),
                    "dropped_tool_calls": int(len(skipped_calls)),
                })
                for skipped_tc in skipped_calls:
                    skipped_name = str(skipped_tc.get("name") or "")
                    skipped_result = {
                        "error": "tool_execution_budget_exhausted",
                        "message": (
                            f"工具总预算（{int(tool_execution_total_budget_seconds)}s）已耗尽，"
                            "该工具调用已跳过。"
                        ),
                        "budget_seconds": int(tool_execution_total_budget_seconds),
                    }
                    yield PipelineEvent("tool_result", {
                        "tool_call_id": skipped_tc.get("id", ""),
                        "name": skipped_name,
                        "result": skipped_result,
                        "duration_ms": 0,
                        "status": "skipped",
                        "risk_level": "L1",
                        "approval_required": False,
                    })
                    llm_messages.append({
                        "role": "tool",
                        "tool_call_id": skipped_tc.get("id", ""),
                        "content": json.dumps(skipped_result, ensure_ascii=False, default=str),
                    })
                break

            tool_calls_executed += 1
            skill_name = tc["name"]
            skill_args = tc.get("args", {}) if isinstance(tc.get("args"), dict) else {}
            skills_used.append(skill_name)
            skill_result: Dict[str, Any] = {}
            skill_start = time.monotonic()
            risk_level = _classify_tool_risk_level(skill_name, skill_args)
            approval_required = _tool_requires_approval(
                risk_level=risk_level,
                workspace_id=workspace_id,
                require_approval=bool(EXECUTION_ACTION_REQUIRE_APPROVAL),
            )

            if approval_required:
                approval_id = 0
                approval_deduped = False
                try:
                    from src.services.tool_approval_service import create_tool_approval_request

                    approval_item = await create_tool_approval_request(
                        user_id=user_id,
                        workspace_id=int(workspace_id or 0),
                        conversation_id=conversation_id,
                        request_role=effective_role,
                        skill_name=skill_name,
                        tool_call_id=str(tc.get("id") or ""),
                        risk_level=risk_level,
                        args=skill_args,
                    )
                    approval_id = int(approval_item.get("id") or 0)
                    approval_deduped = bool(approval_item.get("deduped"))
                except Exception as _approval_error:
                    logger.warning("create tool approval request failed: %s", _approval_error)

                skill_result = _build_tool_approval_block_result(skill_name, risk_level)
                if approval_id > 0:
                    skill_result["approval_id"] = approval_id
                    skill_result["approval_deduped"] = approval_deduped

                yield PipelineEvent("status", {
                    "step": "tool_approval_required",
                    "name": skill_name,
                    "risk_level": risk_level,
                    "workspace_id": int(workspace_id or 0),
                    "approval_id": int(approval_id),
                    "approval_deduped": bool(approval_deduped),
                    "message": str(skill_result.get("error") or ""),
                })
            else:
                tool_timeout_seconds = (
                    search_tool_timeout_seconds
                    if str(skill_name or "").startswith("search_")
                    else default_tool_timeout_seconds
                )
                tool_timeout_seconds = min(float(tool_timeout_seconds), remaining_budget_seconds)
                try:
                    if tool_use_enabled:
                        from src.skills.registry import get_registry
                        reg = get_registry()
                        skill_context = {
                            "user_id": user_id,
                            "workspace_id": workspace_id,
                            "role": effective_role,
                            "_needs_fresh_data": bool(needs_realtime_data),
                            "_requires_verified_sources": bool(requires_verified_sources),
                            "_tool_risk_level": risk_level,
                        }
                        skill_result = await asyncio.wait_for(
                            reg.execute(skill_name, skill_args, context=skill_context),
                            timeout=float(tool_timeout_seconds),
                        )
                        if not isinstance(skill_result, dict):
                            skill_result = {"data": skill_result}
                except asyncio.TimeoutError:
                    timeout_seconds_int = max(1, int(round(float(tool_timeout_seconds))))
                    skill_result = {
                        "error": f"tool_timeout_{timeout_seconds_int}s",
                        "message": f"工具执行超时（{timeout_seconds_int}s），已自动降级继续主回答。",
                        "timeout_seconds": timeout_seconds_int,
                    }
                    if str(skill_name or "").startswith("search_"):
                        skill_result.update(
                            {
                                "_search_engine": "timeout_fallback",
                                "sources": [],
                                "search_sources": 0,
                            }
                        )
                except Exception as e:
                    skill_result = {"error": str(e)}

            skill_elapsed = int((time.monotonic() - skill_start) * 1000)
            tool_event_status = "blocked" if approval_required else ("error" if "error" in skill_result else "complete")
            audit_status = "blocked" if approval_required else ("failed" if "error" in skill_result else "success")
            audit_reason = "approval_required" if approval_required else ("skill_error" if "error" in skill_result else "")

            yield PipelineEvent("tool_result", {
                "tool_call_id": tc.get("id", ""),
                "name": skill_name,
                "result": skill_result,
                "duration_ms": skill_elapsed,
                "status": tool_event_status,
                "risk_level": risk_level,
                "approval_required": bool(approval_required),
            })

            await _log_tool_audit_event(
                user_id=user_id,
                workspace_id=workspace_id,
                skill_name=skill_name,
                args=skill_args,
                result=skill_result if isinstance(skill_result, dict) else {"data": skill_result},
                status=audit_status,
                reason=audit_reason,
                risk_level=risk_level,
                approval_required=bool(approval_required),
                duration_ms=skill_elapsed,
            )

            # 鈹€鈹€ 缃戠粶鎼滅储鎸囩ず鍣細鎼滅储绫绘妧鑳芥墽琛屽悗閫氱煡鍓嶇 鈹€鈹€
            if skill_name.startswith("search_") or "_search_engine" in skill_result:
                realtime_attempted = True
                _engine = str(skill_result.get("_search_engine") or "缃戠粶鎼滅储")
                if _engine:
                    search_engines.append(_engine)
                _skill_sources = _extract_sources_from_skill_result(skill_result, limit=5)
                if _skill_sources:
                    realtime_sources = _merge_realtime_sources(realtime_sources, _skill_sources, limit=12)
                verified_sources_count = _count_verified_realtime_sources(realtime_sources)
                verified_sources_ready = (not requires_verified_sources) or verified_sources_count > 0
                if requires_verified_sources and not verified_sources_ready:
                    verified_sources_guardrail_note = _VERIFIED_SOURCE_GUARDRAIL_NOTE

                _sources = _extract_search_sources_count(skill_result, fallback=len(_skill_sources))
                yield PipelineEvent("search_used", {
                    "skill": skill_name,
                    "engine": _engine,
                    "sources_count": _sources,
                    "sources": _skill_sources[:5],
                })
                if int(_sources or 0) == 0 and not _skill_sources:
                    yield PipelineEvent("status", {
                        "step": "search_unavailable",
                        "message": f"鎼滅储鎶€鑳?{skill_name} 宸茶Е鍙戯紝浣嗗綋鍓嶆湭妫€绱㈠埌鏈夋晥澶栭儴缁撴灉銆?",
                    })
                    if requires_verified_sources and not verified_sources_ready:
                        yield PipelineEvent("status", {
                            "step": "verified_sources_required_unmet",
                            "message": verified_sources_guardrail_note or _VERIFIED_SOURCE_GUARDRAIL_NOTE,
                            "requires_verified_sources": True,
                            "verified_sources_count": int(verified_sources_count),
                            "source": skill_name,
                        })

            # 鈹€鈹€ 鎵ц寤鸿妫€娴嬶細浠锋牸/搴撳瓨/棰勭畻鎶€鑳界粨鏋滀腑鏈夋槑纭暟鍊煎缓璁椂鍐欏叆闃熷垪 鈹€鈹€
            _exec_suggestion = _extract_execution_suggestion(skill_name, skill_result)
            if _exec_suggestion:
                try:
                    _db = await __import__("src.database", fromlist=["get_db"]).get_db()
                    async with _db.execute(
                        """INSERT INTO execution_queue
                               (user_id, action_type, description, platform, payload, expected_impact, status)
                           VALUES (?", ?, ?, ?, ?, ?, 'pending')""",
                        (
                            user_id,
                            _exec_suggestion["action_type"],
                            _exec_suggestion["description"],
                            _exec_suggestion.get("platform", ""),
                            json.dumps(_exec_suggestion.get("payload", {}), ensure_ascii=False),
                            _exec_suggestion.get("expected_impact", ""),
                        ),
                    ) as _cur:
                        _eid = _cur.lastrowid
                    await _db.commit()
                    await _db.execute(
                        """INSERT INTO background_events (user_id, event_type, data)
                           VALUES (?", 'execution_suggested', ?)""",
                        (
                            user_id,
                            json.dumps({
                                "id": _eid,
                                **_exec_suggestion,
                            }, ensure_ascii=False),
                        ),
                    )
                    await _db.commit()
                    yield PipelineEvent("execution_suggested", {
                        "id": _eid,
                        **_exec_suggestion,
                    })
                except Exception as _ee:
                    pass  # 涓嶅奖鍝嶄富娴佺▼
            llm_messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": json.dumps(skill_result, ensure_ascii=False, default=str),
            })

    reply = "".join(reply_parts)
    reply = _inject_tool_evidence_disclosure(reply, skills_used)
    reply = _inject_attachment_vision_fallback_notice(reply, normalized_attachments)
    reply = _inject_constraint_capacity_sla_guard(reply, message, effective_response_mode)

    # 妫€娴婰LM鏄惁鍦ㄨ拷闂敤鎴凤紙鑰岄潪缁欏嚭鍒嗘瀽锛?
    llm_is_asking = _is_asking_user(reply)
    if llm_is_asking:
        yield PipelineEvent("status", {"step": "asking_for_info", "hint": 'AI正在收集更多信息'})

    # 鈹€鈹€ Step 7.5: 鈽?璐ㄩ噺閲嶈瘯寰幆 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    from src.config import ENABLE_QUALITY_CHECK
    MAX_QUALITY_RETRIES = 1  # 最多重试 1 次，控制延迟与成本
    QUALITY_REWRITE_SCORE_FLOOR = 0.86

    quality_retry_applied = False
    quality_score_before_retry: Optional[float] = None
    quality_score_after_retry: Optional[float] = None
    quality_issues_snapshot: List[str] = []
    learning_delivery_guard_sources: List[str] = []
    learning_delivery_guard_gaps: Dict[str, Any] = {}
    analysis_delivery_guard_sources: List[str] = []
    analysis_delivery_guard_gaps: Dict[str, Any] = {}
    design_delivery_guard_sources: List[str] = []
    design_delivery_guard_gaps: Dict[str, Any] = {}
    short_first_guard_applied = False
    short_first_guard_reason = ""
    short_first_summary_points: List[str] = []
    short_first_action_points: List[str] = []
    copy_ready_guard_applied = False
    copy_ready_guard_reason = ""
    copy_ready_guard_scene = ""
    mode_differentiation_guard_applied = False
    mode_differentiation_guard_reason = ""
    collaboration_mode_guard_applied = False
    collaboration_mode_guard_reason = ""
    strategy_combo_contract_guard_applied = False
    strategy_combo_contract_guard_reason = ""
    strategy_combo_contract_missing_sections: List[str] = []

    if ENABLE_QUALITY_CHECK and reply and len(reply) > 20 and not llm_is_asking:
        try:
            from src.core.quality_checker import check_quality
            quality = check_quality(message, reply, effective_role, action=intent.action, tool_used=bool(skills_used))
            quality_score_before_retry = float(quality.score)
            quality_issues_snapshot = list(quality.issues or [])

            # 先尝试执行补全守卫（无需额外 LLM 调用），能过则跳过质量重写，降低长尾时延
            if (
                effective_response_mode == _RESPONSE_MODE_EXECUTION
                and not quality.passed
            ):
                guarded_reply_pre_retry, pre_retry_guard_meta = _apply_execution_delivery_guard(
                    reply,
                    response_mode=effective_response_mode,
                    role=effective_role,
                    action=str(intent.action or ""),
                    message=message,
                    llm_is_asking=llm_is_asking,
                    quality_issues=quality_issues_snapshot,
                )
                if bool(pre_retry_guard_meta.get("applied")):
                    reply = guarded_reply_pre_retry
                    quality_retry_applied = True
                    quality = check_quality(message, reply, effective_role, action=intent.action, tool_used=bool(skills_used))
                    quality_score_after_retry = float(quality.score)
                    quality_issues_snapshot = list(quality.issues or [])
                    yield PipelineEvent("replace_reply", {
                        "reply": reply,
                        "source": "execution_delivery_guard_pre_retry",
                        "gaps": pre_retry_guard_meta.get("gaps") or {},
                        "score": round(quality.score, 3),
                        "passed": bool(quality.passed),
                    })

            if (
                effective_response_mode == _RESPONSE_MODE_EXECUTION
                and str(intent.action or "").strip().lower() in {"analysis", "analyze", "diagnosis", "diagnose"}
                and not quality.passed
            ):
                analysis_guarded_pre_retry, analysis_pre_retry_meta = _apply_analysis_delivery_guard(
                    reply,
                    response_mode=effective_response_mode,
                    action=str(intent.action or ""),
                    llm_is_asking=llm_is_asking,
                    quality_issues=quality_issues_snapshot,
                )
                if bool(analysis_pre_retry_meta.get("applied")):
                    reply = analysis_guarded_pre_retry
                    quality_retry_applied = True
                    analysis_delivery_guard_sources.append("analysis_delivery_guard_pre_retry")
                    analysis_delivery_guard_gaps = analysis_pre_retry_meta.get("gaps") or {}
                    quality = check_quality(message, reply, effective_role, action=intent.action, tool_used=bool(skills_used))
                    quality_score_after_retry = float(quality.score)
                    quality_issues_snapshot = list(quality.issues or [])
                    yield PipelineEvent("replace_reply", {
                        "reply": reply,
                        "source": "analysis_delivery_guard_pre_retry",
                        "gaps": analysis_pre_retry_meta.get("gaps") or {},
                        "score": round(quality.score, 3),
                        "passed": bool(quality.passed),
                    })

            if (
                effective_response_mode == _RESPONSE_MODE_EXECUTION
                and effective_role == "design"
                and not quality.passed
            ):
                design_guarded_pre_retry, design_pre_retry_meta = _apply_design_delivery_guard(
                    reply,
                    response_mode=effective_response_mode,
                    role=effective_role,
                    action=str(intent.action or ""),
                    llm_is_asking=llm_is_asking,
                    quality_issues=quality_issues_snapshot,
                )
                if bool(design_pre_retry_meta.get("applied")):
                    reply = design_guarded_pre_retry
                    quality_retry_applied = True
                    design_delivery_guard_sources.append("design_delivery_guard_pre_retry")
                    design_delivery_guard_gaps = design_pre_retry_meta.get("gaps") or {}
                    quality = check_quality(message, reply, effective_role, action=intent.action, tool_used=bool(skills_used))
                    quality_score_after_retry = float(quality.score)
                    quality_issues_snapshot = list(quality.issues or [])
                    yield PipelineEvent("replace_reply", {
                        "reply": reply,
                        "source": "design_delivery_guard_pre_retry",
                        "gaps": design_pre_retry_meta.get("gaps") or {},
                        "score": round(quality.score, 3),
                        "passed": bool(quality.passed),
                    })

            if (
                effective_response_mode == _RESPONSE_MODE_LEARNING
                and not quality.passed
            ):
                learning_guarded_pre_retry, learning_pre_retry_meta = _apply_learning_delivery_guard(
                    reply,
                    response_mode=effective_response_mode,
                    learning_level=effective_learning_level,
                    domain_id=str(intent.domain_id or ""),
                    llm_is_asking=llm_is_asking,
                    quality_issues=quality_issues_snapshot,
                )
                if bool(learning_pre_retry_meta.get("applied")):
                    reply = learning_guarded_pre_retry
                    quality_retry_applied = True
                    learning_delivery_guard_sources.append("learning_delivery_guard_pre_retry")
                    learning_delivery_guard_gaps = learning_pre_retry_meta.get("gaps") or {}
                    quality = check_quality(message, reply, effective_role, action=intent.action, tool_used=bool(skills_used))
                    quality_score_after_retry = float(quality.score)
                    quality_issues_snapshot = list(quality.issues or [])
                    yield PipelineEvent("replace_reply", {
                        "reply": reply,
                        "source": "learning_delivery_guard_pre_retry",
                        "gaps": learning_pre_retry_meta.get("gaps") or {},
                        "score": round(quality.score, 3),
                        "passed": bool(quality.passed),
                    })

            should_run_quality_retry = (
                (not quality.passed)
                and bool(quality.suggestions)
                and float(quality.score or 0.0) < float(QUALITY_REWRITE_SCORE_FLOOR)
            )

            if should_run_quality_retry:
                for _retry in range(MAX_QUALITY_RETRIES):
                    yield PipelineEvent("status", {
                        "step": "quality_retry",
                        "score": round(quality.score, 3),
                        "issues": quality.issues[:3],
                        "retry": _retry + 1,
                    })

                    rewrite_requirements = _build_quality_retry_requirements(
                        quality=quality,
                        response_mode=effective_response_mode,
                        learning_level=effective_learning_level,
                        action=str(intent.action or ""),
                        role=effective_role,
                        tool_used=bool(skills_used),
                    )

                    feedback = (
                        f"你上一版回复质量评分为 {quality.score:.0%}。"
                        f"主要问题：{'；'.join(quality.issues[:4]) or '结构与可执行性不足'}。"
                        f"改进建议：{'；'.join(quality.suggestions[:4]) or '补全结论、步骤与指标'}。"
                        f"重写要求：{'；'.join(rewrite_requirements[:6]) or '围绕低分维度补齐结构、证据、步骤与风险'}。"
                        "请直接给出重写后的完整最终答复，不要解释修改过程。"
                    )
                    llm_messages.append({"role": "assistant", "content": reply})
                    llm_messages.append({"role": "user", "content": feedback})

                    retry_parts: List[str] = []
                    llm_calls += 1
                    async for ev in call_llm_stream(
                        system="",
                        message="",
                        history=llm_messages,
                        tools=tools,
                        max_tokens=quality_retry_max_tokens,
                    ):
                        if ev["type"] == "token":
                            retry_parts.append(ev["text"])
                        elif ev["type"] == "done":
                            llm_usage_totals = _merge_llm_usage_totals(llm_usage_totals, ev.get("usage"))
                            break

                    if retry_parts:
                        rewritten_reply = "".join(retry_parts).strip()
                        if rewritten_reply:
                            reply = rewritten_reply
                            quality_retry_applied = True
                            quality = check_quality(message, reply, effective_role, action=intent.action, tool_used=bool(skills_used))
                            quality_score_after_retry = float(quality.score)
                            quality_issues_snapshot = list(quality.issues or [])
                            yield PipelineEvent("replace_reply", {
                                "reply": reply,
                                "score": round(quality.score, 3),
                                "passed": bool(quality.passed),
                                "retry": _retry + 1,
                            })
                            if quality.passed:
                                break
            elif not quality.passed and quality.suggestions:
                yield PipelineEvent("status", {
                    "step": "quality_retry_skipped",
                    "score": round(float(quality.score or 0.0), 3),
                    "score_floor": float(QUALITY_REWRITE_SCORE_FLOOR),
                    "reason": "score_near_pass",
                })
        except Exception as e:
            logger.warning("Quality retry failed: %s", e)

    execution_delivery_guard = {"applied": False, "gaps": {}}
    if not llm_is_asking:
        reply, execution_delivery_guard = _apply_execution_delivery_guard(
            reply,
            response_mode=effective_response_mode,
            role=effective_role,
            action=str(intent.action or ""),
            message=message,
            llm_is_asking=llm_is_asking,
            quality_issues=quality_issues_snapshot,
        )
        if bool(execution_delivery_guard.get("applied")):
            quality_retry_applied = True
            if ENABLE_QUALITY_CHECK and reply and len(reply) > 20:
                try:
                    from src.core.quality_checker import check_quality
                    guarded_quality = check_quality(message, reply, effective_role, action=intent.action, tool_used=bool(skills_used))
                    quality_score_after_retry = float(guarded_quality.score)
                    quality_issues_snapshot = list(guarded_quality.issues or [])
                except Exception as _quality_guard_error:
                    logger.warning("Execution delivery guard quality check failed: %s", _quality_guard_error)
            yield PipelineEvent("replace_reply", {
                "reply": reply,
                "source": "execution_delivery_guard",
                "gaps": execution_delivery_guard.get("gaps") or {},
            })

    learning_delivery_guard = {"applied": False, "gaps": {}}
    if not llm_is_asking:
        reply, learning_delivery_guard = _apply_learning_delivery_guard(
            reply,
            response_mode=effective_response_mode,
            learning_level=effective_learning_level,
            domain_id=str(intent.domain_id or ""),
            llm_is_asking=llm_is_asking,
            quality_issues=quality_issues_snapshot,
        )
        if bool(learning_delivery_guard.get("applied")):
            quality_retry_applied = True
            learning_delivery_guard_sources.append("learning_delivery_guard")
            learning_delivery_guard_gaps = learning_delivery_guard.get("gaps") or learning_delivery_guard_gaps
            if ENABLE_QUALITY_CHECK and reply and len(reply) > 20:
                try:
                    from src.core.quality_checker import check_quality
                    guarded_quality = check_quality(message, reply, effective_role, action=intent.action, tool_used=bool(skills_used))
                    quality_score_after_retry = float(guarded_quality.score)
                    quality_issues_snapshot = list(guarded_quality.issues or [])
                except Exception as _learning_quality_guard_error:
                    logger.warning("Learning delivery guard quality check failed: %s", _learning_quality_guard_error)
            yield PipelineEvent("replace_reply", {
                "reply": reply,
                "source": "learning_delivery_guard",
                "gaps": learning_delivery_guard.get("gaps") or {},
            })

    analysis_delivery_guard = {"applied": False, "gaps": {}}
    if not llm_is_asking:
        reply, analysis_delivery_guard = _apply_analysis_delivery_guard(
            reply,
            response_mode=effective_response_mode,
            action=str(intent.action or ""),
            llm_is_asking=llm_is_asking,
            quality_issues=quality_issues_snapshot,
        )
        if bool(analysis_delivery_guard.get("applied")):
            quality_retry_applied = True
            analysis_delivery_guard_sources.append("analysis_delivery_guard")
            analysis_delivery_guard_gaps = analysis_delivery_guard.get("gaps") or analysis_delivery_guard_gaps
            if ENABLE_QUALITY_CHECK and reply and len(reply) > 20:
                try:
                    from src.core.quality_checker import check_quality
                    guarded_quality = check_quality(message, reply, effective_role, action=intent.action, tool_used=bool(skills_used))
                    quality_score_after_retry = float(guarded_quality.score)
                    quality_issues_snapshot = list(guarded_quality.issues or [])
                except Exception as _analysis_quality_guard_error:
                    logger.warning("Analysis delivery guard quality check failed: %s", _analysis_quality_guard_error)
            yield PipelineEvent("replace_reply", {
                "reply": reply,
                "source": "analysis_delivery_guard",
                "gaps": analysis_delivery_guard.get("gaps") or {},
            })

    design_delivery_guard = {"applied": False, "gaps": {}}
    if not llm_is_asking:
        reply, design_delivery_guard = _apply_design_delivery_guard(
            reply,
            response_mode=effective_response_mode,
            role=effective_role,
            action=str(intent.action or ""),
            llm_is_asking=llm_is_asking,
            quality_issues=quality_issues_snapshot,
        )
        if bool(design_delivery_guard.get("applied")):
            quality_retry_applied = True
            design_delivery_guard_sources.append("design_delivery_guard")
            design_delivery_guard_gaps = design_delivery_guard.get("gaps") or design_delivery_guard_gaps
            if ENABLE_QUALITY_CHECK and reply and len(reply) > 20:
                try:
                    from src.core.quality_checker import check_quality
                    guarded_quality = check_quality(message, reply, effective_role, action=intent.action, tool_used=bool(skills_used))
                    quality_score_after_retry = float(guarded_quality.score)
                    quality_issues_snapshot = list(guarded_quality.issues or [])
                except Exception as _design_quality_guard_error:
                    logger.warning("Design delivery guard quality check failed: %s", _design_quality_guard_error)
            yield PipelineEvent("replace_reply", {
                "reply": reply,
                "source": "design_delivery_guard",
                "gaps": design_delivery_guard.get("gaps") or {},
            })

    if learning_delivery_guard_sources:
        learning_delivery_guard_sources = list(dict.fromkeys([str(x).strip() for x in learning_delivery_guard_sources if str(x).strip()]))
    if analysis_delivery_guard_sources:
        analysis_delivery_guard_sources = list(dict.fromkeys([str(x).strip() for x in analysis_delivery_guard_sources if str(x).strip()]))
    if design_delivery_guard_sources:
        design_delivery_guard_sources = list(dict.fromkeys([str(x).strip() for x in design_delivery_guard_sources if str(x).strip()]))

    # 在所有质量重写/补全守卫后再做一次证据披露兜底，避免重写结果覆盖证据映射。
    final_tool_evidence_reply = _inject_tool_evidence_disclosure(reply, skills_used)
    if final_tool_evidence_reply != reply:
        reply = final_tool_evidence_reply
        if ENABLE_QUALITY_CHECK and reply and len(reply) > 20:
            try:
                from src.core.quality_checker import check_quality
                guarded_quality = check_quality(message, reply, effective_role, action=intent.action, tool_used=bool(skills_used))
                quality_score_after_retry = float(guarded_quality.score)
                quality_issues_snapshot = list(guarded_quality.issues or [])
            except Exception as _tool_evidence_guard_error:
                logger.warning("Tool evidence disclosure quality check failed: %s", _tool_evidence_guard_error)
        yield PipelineEvent("replace_reply", {
            "reply": reply,
            "source": "tool_evidence_disclosure",
            "skills_used": skills_used[:3],
        })

    final_attachment_notice_reply = _inject_attachment_vision_fallback_notice(reply, normalized_attachments)
    if final_attachment_notice_reply != reply:
        reply = final_attachment_notice_reply
        if ENABLE_QUALITY_CHECK and reply and len(reply) > 20:
            try:
                from src.core.quality_checker import check_quality
                guarded_quality = check_quality(message, reply, effective_role, action=intent.action, tool_used=bool(skills_used))
                quality_score_after_retry = float(guarded_quality.score)
                quality_issues_snapshot = list(guarded_quality.issues or [])
            except Exception as _attachment_notice_guard_error:
                logger.warning("Attachment fallback notice quality check failed: %s", _attachment_notice_guard_error)
        yield PipelineEvent("replace_reply", {
            "reply": reply,
            "source": "attachment_vision_fallback_notice",
            "attachment_vision_fallback_count": attachment_stats.get("vision_fallback", 0),
        })

    final_capacity_sla_reply = _inject_constraint_capacity_sla_guard(reply, message, effective_response_mode)
    if final_capacity_sla_reply != reply:
        reply = final_capacity_sla_reply
        if ENABLE_QUALITY_CHECK and reply and len(reply) > 20:
            try:
                from src.core.quality_checker import check_quality
                guarded_quality = check_quality(message, reply, effective_role, action=intent.action, tool_used=bool(skills_used))
                quality_score_after_retry = float(guarded_quality.score)
                quality_issues_snapshot = list(guarded_quality.issues or [])
            except Exception as _tool_evidence_guard_error:
                logger.warning("Capacity/SLA guard quality check failed: %s", _tool_evidence_guard_error)
        yield PipelineEvent("replace_reply", {
            "reply": reply,
            "source": "capacity_sla_guard",
            "anchors_detected": _TASK_CONSTRAINT_ANCHOR_RE.findall(message)[:3],
        })

    short_first_reply, short_first_meta = _inject_short_first_summary_guard(
        reply,
        response_mode=effective_response_mode,
        message=message,
        llm_is_asking=llm_is_asking,
    )
    short_first_guard_reason = str(short_first_meta.get("reason") or "")
    if bool(short_first_meta.get("applied")):
        reply = short_first_reply
        short_first_guard_applied = True
        short_first_summary_points = [str(x).strip() for x in (short_first_meta.get("summary_points") or []) if str(x).strip()][:3]
        short_first_action_points = [str(x).strip() for x in (short_first_meta.get("action_points") or []) if str(x).strip()][:3]
        quality_retry_applied = True
        if ENABLE_QUALITY_CHECK and reply and len(reply) > 20:
            try:
                from src.core.quality_checker import check_quality
                guarded_quality = check_quality(message, reply, effective_role, action=intent.action, tool_used=bool(skills_used))
                quality_score_after_retry = float(guarded_quality.score)
                quality_issues_snapshot = list(guarded_quality.issues or [])
            except Exception as _short_first_guard_error:
                logger.warning("Short-first summary guard quality check failed: %s", _short_first_guard_error)
        yield PipelineEvent("replace_reply", {
            "reply": reply,
            "source": "short_first_summary_guard",
            "summary_points": short_first_summary_points,
            "action_points": short_first_action_points,
        })

    copy_ready_reply, copy_ready_meta = _apply_copy_ready_delivery_guard(
        reply,
        response_mode=effective_response_mode,
        message=message,
        llm_is_asking=llm_is_asking,
    )
    copy_ready_guard_reason = str(copy_ready_meta.get("reason") or "")
    copy_ready_guard_scene = str(copy_ready_meta.get("scene") or "")
    if bool(copy_ready_meta.get("applied")):
        reply = copy_ready_reply
        copy_ready_guard_applied = True
        quality_retry_applied = True
        if ENABLE_QUALITY_CHECK and reply and len(reply) > 20:
            try:
                from src.core.quality_checker import check_quality
                guarded_quality = check_quality(message, reply, effective_role, action=intent.action, tool_used=bool(skills_used))
                quality_score_after_retry = float(guarded_quality.score)
                quality_issues_snapshot = list(guarded_quality.issues or [])
            except Exception as _copy_ready_guard_error:
                logger.warning("Copy-ready guard quality check failed: %s", _copy_ready_guard_error)
        yield PipelineEvent("replace_reply", {
            "reply": reply,
            "source": "copy_ready_delivery_guard",
            "scene": copy_ready_guard_scene,
        })

    # Keep role-expansion detection anchored to pre-strategy/formatting reply to avoid
    # guard-injected boilerplate triggering false support-role expansion.
    role_expansion_probe_reply = str(reply or "")

    mode_differentiation_reply, mode_differentiation_meta = _apply_mode_differentiation_guard(
        reply,
        response_mode=effective_response_mode,
        message=message,
        llm_is_asking=llm_is_asking,
    )
    mode_differentiation_guard_reason = str(mode_differentiation_meta.get("reason") or "")
    if bool(mode_differentiation_meta.get("applied")):
        reply = mode_differentiation_reply
        mode_differentiation_guard_applied = True
        quality_retry_applied = True
        if ENABLE_QUALITY_CHECK and reply and len(reply) > 20:
            try:
                from src.core.quality_checker import check_quality
                guarded_quality = check_quality(message, reply, effective_role, action=intent.action, tool_used=bool(skills_used))
                quality_score_after_retry = float(guarded_quality.score)
                quality_issues_snapshot = list(guarded_quality.issues or [])
            except Exception as _mode_diff_guard_error:
                logger.warning("Mode differentiation guard quality check failed: %s", _mode_diff_guard_error)
        yield PipelineEvent("replace_reply", {
            "reply": reply,
            "source": "mode_differentiation_guard",
            "reason": mode_differentiation_guard_reason,
        })

    collaboration_mode_reply, collaboration_mode_meta = _apply_collaboration_mode_signature_guard(
        reply,
        collaboration_mode=effective_collaboration_mode,
        hired_roles=manual_hired_roles,
        response_mode=effective_response_mode,
        message=message,
        llm_is_asking=llm_is_asking,
    )
    collaboration_mode_guard_reason = str(collaboration_mode_meta.get("reason") or "")
    if bool(collaboration_mode_meta.get("applied")):
        reply = collaboration_mode_reply
        collaboration_mode_guard_applied = True
        quality_retry_applied = True
        if ENABLE_QUALITY_CHECK and reply and len(reply) > 20:
            try:
                from src.core.quality_checker import check_quality
                guarded_quality = check_quality(message, reply, effective_role, action=intent.action, tool_used=bool(skills_used))
                quality_score_after_retry = float(guarded_quality.score)
                quality_issues_snapshot = list(guarded_quality.issues or [])
            except Exception as _collab_guard_error:
                logger.warning("Collaboration mode guard quality check failed: %s", _collab_guard_error)
        yield PipelineEvent("replace_reply", {
            "reply": reply,
            "source": "collaboration_mode_guard",
            "reason": collaboration_mode_guard_reason,
        })

    strategy_combo_reply, strategy_combo_meta = _apply_strategy_combo_contract_guard(
        reply,
        response_mode=effective_response_mode,
        collaboration_mode=effective_collaboration_mode,
        learning_level=effective_learning_level,
        hired_roles=manual_hired_roles,
        message=message,
        llm_is_asking=llm_is_asking,
    )
    strategy_combo_contract_guard_reason = str(strategy_combo_meta.get("reason") or "")
    strategy_combo_contract_missing_sections = [
        str(item).strip()
        for item in (strategy_combo_meta.get("missing_sections") or [])
        if str(item).strip()
    ]
    if bool(strategy_combo_meta.get("applied")):
        reply = strategy_combo_reply
        strategy_combo_contract_guard_applied = True
        quality_retry_applied = True
        if ENABLE_QUALITY_CHECK and reply and len(reply) > 20:
            try:
                from src.core.quality_checker import check_quality
                guarded_quality = check_quality(message, reply, effective_role, action=intent.action, tool_used=bool(skills_used))
                quality_score_after_retry = float(guarded_quality.score)
                quality_issues_snapshot = list(guarded_quality.issues or [])
            except Exception as _strategy_combo_guard_error:
                logger.warning("Strategy combo contract guard quality check failed: %s", _strategy_combo_guard_error)
        yield PipelineEvent("replace_reply", {
            "reply": reply,
            "source": "strategy_combo_contract_guard",
            "reason": strategy_combo_contract_guard_reason,
            "missing_sections": list(strategy_combo_contract_missing_sections),
        })

    llm_estimated_cost_usd = _estimate_llm_cost_usd(llm_usage_totals)

    final_realtime_sources_count = max(realtime_sources_count, len(realtime_sources))
    verified_sources_count = _count_verified_realtime_sources(realtime_sources)
    verified_sources_ready = (not requires_verified_sources) or verified_sources_count > 0
    if requires_verified_sources and not verified_sources_ready:
        verified_sources_guardrail_note = _VERIFIED_SOURCE_GUARDRAIL_NOTE
    else:
        verified_sources_guardrail_note = ""

    latest_credibility_snapshot = _compose_credibility_snapshot(
        intent_confidence=float(intent.confidence or 0.0),
        domain_confidence=float(intent.domain_confidence or 0.0),
        trust_level=trust_level or '未评?',
        attachment_stats=attachment_stats,
        realtime_sources=realtime_sources,
        realtime_sources_count_hint=final_realtime_sources_count,
        realtime_attempted=realtime_attempted,
        has_metrics_ctx=bool(metrics_context),
        engine=_join_search_engines(search_engines),
        requires_verified_sources=requires_verified_sources,
        verified_sources_ready=verified_sources_ready,
    )
    if latest_credibility_snapshot != credibility_snapshot:
        credibility_snapshot = latest_credibility_snapshot
        yield PipelineEvent("status", {
            "step": "credibility_snapshot_updated",
            **credibility_snapshot,
            "attachment_layout_table_count": attachment_stats.get("layout_table", 0),
            "attachment_layout_source_breakdown": attachment_layout_breakdown,
        })

    if requires_verified_sources and not verified_sources_ready:
        yield PipelineEvent("status", {
            "step": "verified_sources_required_unmet",
            "message": verified_sources_guardrail_note or _VERIFIED_SOURCE_GUARDRAIL_NOTE,
            "requires_verified_sources": True,
            "verified_sources_count": int(verified_sources_count),
            "final": True,
            "source": "final_guardrail",
        })

    response_outline = _build_response_outline(
        reply,
        effective_response_mode,
        llm_is_asking=llm_is_asking,
    )
    if response_outline:
        yield PipelineEvent("status", {
            "step": "response_outline",
            **response_outline,
        })

    _mark_stage("primary_generation_ms")
    primary_elapsed = int((time.monotonic() - start) * 1000)

    # 鈹€鈹€ Step 8: 涓籄gent瀹屾垚锛屽憡鐭ュ墠绔?鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    yield PipelineEvent("status", {
        "step": "primary_done",
        "role": effective_role,
        "role_name": primary_display,
        "llm_calls": llm_calls,
        "skills_used": skills_used,
        "tool_calls_executed": int(tool_calls_executed),
        "tool_calls_dropped_by_round_cap": int(tool_calls_dropped_by_round_cap),
        "tool_calls_dropped_by_budget": int(tool_calls_dropped_by_budget),
        "tool_calls_dropped_total": int(tool_calls_dropped_by_round_cap + tool_calls_dropped_by_budget),
        "tool_execution_budget_exhausted": bool(tool_execution_budget_exhausted),
        "tool_execution_budget_seconds": float(tool_execution_total_budget_seconds),
        "elapsed_ms": primary_elapsed,
        "response_mode": effective_response_mode,
        "learning_level": effective_learning_level,
        "learning_interaction_enabled": learning_interaction_enabled,
        "learning_structure_template": learning_structure_template,
        "llm_usage": llm_usage_totals,
        "llm_estimated_cost_usd": llm_estimated_cost_usd,
        "quality_retry_applied": bool(quality_retry_applied),
        "quality_score_before_retry": round(float(quality_score_before_retry), 3) if quality_score_before_retry is not None else None,
        "quality_score_after_retry": round(float(quality_score_after_retry), 3) if quality_score_after_retry is not None else None,
        "learning_delivery_guard_applied": bool(learning_delivery_guard_sources),
        "learning_delivery_guard_sources": list(learning_delivery_guard_sources),
        "learning_delivery_guard_gaps": learning_delivery_guard_gaps,
        "analysis_delivery_guard_applied": bool(analysis_delivery_guard_sources),
        "analysis_delivery_guard_sources": list(analysis_delivery_guard_sources),
        "analysis_delivery_guard_gaps": analysis_delivery_guard_gaps,
        "design_delivery_guard_applied": bool(design_delivery_guard_sources),
        "design_delivery_guard_sources": list(design_delivery_guard_sources),
        "design_delivery_guard_gaps": design_delivery_guard_gaps,
        "strategy_combo_contract_guard_applied": bool(strategy_combo_contract_guard_applied),
        "strategy_combo_contract_guard_reason": strategy_combo_contract_guard_reason,
        "strategy_combo_contract_missing_sections": list(strategy_combo_contract_missing_sections),
    })

    # 鈹€鈹€ Step 9: 鈽?澶欰gent鍗忎綔璋冨害 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    collab_contributions: List[Dict[str, Any]] = []

    # 鐢ㄤ富Agent鍥炲鎵╁厖鏀寔瑙掕壊锛圠LM椹卞姩璺敱锛?
    # extra_from_reply: 浠呮潵鑷富Agent鍥炲淇″彿锛堢湡姝ｇ殑LLM椹卞姩锛?
    extra_from_reply: List[str] = []
    final_support_roles = list(support_roles)
    allow_support_expansion = (
        effective_collaboration_mode == _COLLABORATION_MODE_AUTO
        and (not role_locked or explicit_collab_requested)
    )
    if allow_support_expansion and reply and not llm_is_asking:
        extra_from_reply = _detect_needed_roles_from_reply(role_expansion_probe_reply, effective_role, final_support_roles)
        extra_from_reply = _normalize_hired_roles(
            extra_from_reply,
            allow_engineering=ENABLE_ENGINEERING_AGENT,
            collaboration_mode=effective_collaboration_mode,
            max_roles_override=support_role_cap,
            role_ctx=runtime_role_ctx,
        )
        if effective_collaboration_mode == _COLLABORATION_MODE_AUTO and not explicit_collab_requested:
            extra_from_reply = _filter_support_roles_by_dispatch_profile(
                extra_from_reply,
                primary_role=effective_role,
                intent_profile=dispatch_intent_profile,
            )
        if extra_from_reply:
            final_support_roles.extend(extra_from_reply)
            yield PipelineEvent("status", {
                "step": "collab_roles_expanded",
                "added": extra_from_reply,
                "added_names": [_role_display_name(r) for r in extra_from_reply],
                "total_support": final_support_roles,
            })

    # 鍗忎綔瑙﹀彂鏉′欢锛?
    # 1. 鎰忓浘鍒嗘瀽纭涓哄鍩燂紙TIER_MULTI锛変笖鏈夋敮鎸佽鑹?
    # 2. 鎴栧洖澶嶅唴瀹归┍鍔ㄨ瘑鍒嚭闇€瑕侀澶栦笓涓氭敮鎸侊紙LLM鐪熸"鎻愬嚭浜嗛渶姹?锛?"
    collab_decision_trace: Dict[str, Any] = {}
    should_collab = _should_run_multi_agent_collaboration(
        tier=int(intent.tier),
        support_roles=final_support_roles,
        extra_from_reply=extra_from_reply,
        collaboration_mode=effective_collaboration_mode,
        role_locked=role_locked,
        explicit_collab_requested=explicit_collab_requested,
        message=message,
        task_should_clarify=bool(task_framing.get("should_clarify")),
        intent_confidence=float(intent.confidence or 0.0),
        response_mode=effective_response_mode,
        has_attachment_context=bool(normalized_attachments) or bool(attachment_context),
        decision_trace=collab_decision_trace,
    )

    # Manual mode is an explicit per-turn scheduling contract: selected support roles must run.
    if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL and bool(final_support_roles):
        should_collab = True
        collab_decision_trace.setdefault("reason", "manual_support_present")
        collab_decision_trace["manual_force_dispatch"] = True

    # Auto mode should not silently skip collaboration when scheduler-selected support roles already exist.
    should_collab = _apply_auto_collab_force_dispatch_guard(
        should_collab=bool(should_collab),
        collaboration_mode=effective_collaboration_mode,
        support_roles=final_support_roles,
        explicit_collab_requested=bool(explicit_collab_requested),
        role_locked=bool(role_locked),
        message=message,
        decision_trace=collab_decision_trace,
    )

    collab_dispatch_started = False
    collab_dispatch_done = False
    collab_dispatch_failed = False
    collab_not_executed_reason = ""
    collab_not_executed_reason_detail = ""

    collab_input_requested_roles = int(len(final_support_roles))
    collab_input_requested_role_list = list(final_support_roles)
    collab_scheduled_roles = 0
    collab_scheduled_role_list: List[str] = []
    collab_completed_roles = 0
    collab_completed_role_list: List[str] = []
    collab_truncated_by_budget = False

    manual_schedule_consistency = {
        "match": True,
        "missing": [],
        "extra": [],
        "expected": [],
        "actual": [],
    }
    manual_completion_consistency = {
        "match": True,
        "missing": [],
        "extra": [],
        "expected": [],
        "actual": [],
    }

    collab_dispatch_elapsed_seconds = float(max(0.0, time.monotonic() - start))
    collab_dispatch_budget_seconds = float(_COLLAB_DISPATCH_START_LATENCY_BUDGET_SECONDS)
    if (
        effective_collaboration_mode == _COLLABORATION_MODE_AUTO
        and should_collab
        and not explicit_collab_requested
        and int(collab_input_requested_roles or 0) >= 1
    ):
        collab_dispatch_budget_seconds = max(
            collab_dispatch_budget_seconds,
            float(_COLLAB_DISPATCH_START_LATENCY_BUDGET_SECONDS_AUTO_MULTI),
        )
    collab_dispatch_blocked_by_budget = bool(
        should_collab
        and not explicit_collab_requested
        and effective_collaboration_mode == _COLLABORATION_MODE_SINGLE
        and collab_dispatch_elapsed_seconds >= collab_dispatch_budget_seconds
    )
    if collab_dispatch_blocked_by_budget:
        should_collab = False
        collab_decision_trace["reason"] = "latency_budget_guard"
        collab_decision_trace["latency_budget_seconds"] = collab_dispatch_budget_seconds
        collab_decision_trace["latency_elapsed_seconds"] = round(collab_dispatch_elapsed_seconds, 3)
        yield PipelineEvent("status", {
            "step": "collab_skipped_latency_budget",
            "elapsed_ms": int(collab_dispatch_elapsed_seconds * 1000),
            "budget_ms": int(collab_dispatch_budget_seconds * 1000),
            "explicit_collaboration_requested": bool(explicit_collab_requested),
        })

    collab_manual_force_dispatch = (
        effective_collaboration_mode == _COLLABORATION_MODE_MANUAL
        and bool(final_support_roles)
    )
    collab_auto_attachment_force_dispatch = (
        effective_collaboration_mode == _COLLABORATION_MODE_AUTO
        and bool(final_support_roles)
        and bool(normalized_attachments)
        and bool(should_collab)
    )
    collab_dispatch_asking_guard_passed = bool(
        collab_manual_force_dispatch
        or collab_auto_attachment_force_dispatch
        or not llm_is_asking
    )

    if ENABLE_HANDOFF and collab_dispatch_asking_guard_passed and should_collab and reply:
        collab_dispatch_started = True
        try:
            from src.core.multi_agent import dispatch_support_agents

            async for collab_ev in dispatch_support_agents(
                message=message,
                primary_role=effective_role,
                primary_reply=reply,
                support_roles=final_support_roles,
                user_id=user_id,
                product_id=product_id,
                platform=intent.platform,
                action=str(intent.action or ""),
                domain_id=str(intent.domain_id or ""),
                max_roles_override=support_role_cap,
            ):
                event_type = str(collab_ev.get("event") or "").strip()
                payload = {k: v for k, v in collab_ev.items() if k != "event"}
                if event_type:
                    yield PipelineEvent(event_type, payload)

                if event_type == "collab_start":
                    start_agents = collab_ev.get("agents") if isinstance(collab_ev.get("agents"), list) else []
                    start_roles = [
                        str(item.get("role") or "").strip().lower()
                        for item in start_agents
                        if isinstance(item, dict) and str(item.get("role") or "").strip()
                    ]
                    start_roles = _normalize_hired_roles(
                        start_roles,
                        allow_engineering=ENABLE_ENGINEERING_AGENT,
                        collaboration_mode=effective_collaboration_mode,
                        max_roles_override=64,
                        role_ctx=runtime_role_ctx,
                    )

                    input_requested = collab_ev.get("input_requested_role_list")
                    if isinstance(input_requested, list) and input_requested:
                        collab_input_requested_role_list = _normalize_hired_roles(
                            [str(item or "") for item in input_requested],
                            allow_engineering=ENABLE_ENGINEERING_AGENT,
                            collaboration_mode=effective_collaboration_mode,
                            max_roles_override=64,
                            role_ctx=runtime_role_ctx,
                        )
                    else:
                        collab_input_requested_role_list = list(final_support_roles)

                    scheduled_from_event = collab_ev.get("scheduled_role_list")
                    if isinstance(scheduled_from_event, list) and scheduled_from_event:
                        collab_scheduled_role_list = _normalize_hired_roles(
                            [str(item or "") for item in scheduled_from_event],
                            allow_engineering=ENABLE_ENGINEERING_AGENT,
                            collaboration_mode=effective_collaboration_mode,
                            max_roles_override=64,
                            role_ctx=runtime_role_ctx,
                        )
                    elif start_roles:
                        collab_scheduled_role_list = list(start_roles)
                    else:
                        collab_scheduled_role_list = list(final_support_roles)

                    collab_input_requested_roles = int(
                        collab_ev.get("input_requested_roles")
                        or len(collab_input_requested_role_list)
                        or len(final_support_roles)
                    )
                    collab_scheduled_roles = int(
                        collab_ev.get("scheduled_roles")
                        or len(collab_scheduled_role_list)
                        or len(start_roles)
                    )
                    collab_truncated_by_budget = bool(collab_ev.get("truncated_by_budget"))

                    if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL:
                        manual_schedule_consistency = _role_list_consistency(
                            final_support_roles,
                            collab_scheduled_role_list,
                        )
                        yield PipelineEvent("status", {
                            "step": "collab_manual_selection_check",
                            "expected_support_roles": list(final_support_roles),
                            "expected_support_names": [_role_display_name(r) for r in final_support_roles],
                            "input_requested_roles": int(collab_input_requested_roles),
                            "input_requested_role_list": list(collab_input_requested_role_list),
                            "scheduled_roles": int(collab_scheduled_roles),
                            "scheduled_role_list": list(collab_scheduled_role_list),
                            "scheduled_role_names": [_role_display_name(r) for r in collab_scheduled_role_list],
                            "truncated_by_budget": bool(collab_truncated_by_budget),
                            "match": bool(manual_schedule_consistency.get("match")),
                            "missing_roles": list(manual_schedule_consistency.get("missing") or []),
                            "extra_roles": list(manual_schedule_consistency.get("extra") or []),
                        })

                if event_type == "collab_done":
                    collab_dispatch_done = True
                    collab_contributions = collab_ev.get("contributions", [])
                    completed_roles_raw = [
                        str(item.get("role") or "").strip().lower()
                        for item in (collab_contributions or [])
                        if isinstance(item, dict) and str(item.get("role") or "").strip()
                    ]
                    collab_completed_role_list = _normalize_hired_roles(
                        completed_roles_raw,
                        allow_engineering=ENABLE_ENGINEERING_AGENT,
                        collaboration_mode=effective_collaboration_mode,
                        max_roles_override=64,
                        role_ctx=runtime_role_ctx,
                    )
                    collab_completed_roles = int(
                        collab_ev.get("completed_roles")
                        or len(collab_completed_role_list)
                    )

                    done_scheduled = collab_ev.get("scheduled_role_list")
                    if isinstance(done_scheduled, list) and done_scheduled:
                        collab_scheduled_role_list = _normalize_hired_roles(
                            [str(item or "") for item in done_scheduled],
                            allow_engineering=ENABLE_ENGINEERING_AGENT,
                            collaboration_mode=effective_collaboration_mode,
                            max_roles_override=64,
                            role_ctx=runtime_role_ctx,
                        )
                    collab_scheduled_roles = int(
                        collab_ev.get("scheduled_roles")
                        or len(collab_scheduled_role_list)
                    )

                    done_input_requested = collab_ev.get("input_requested_role_list")
                    if isinstance(done_input_requested, list) and done_input_requested:
                        collab_input_requested_role_list = _normalize_hired_roles(
                            [str(item or "") for item in done_input_requested],
                            allow_engineering=ENABLE_ENGINEERING_AGENT,
                            collaboration_mode=effective_collaboration_mode,
                            max_roles_override=64,
                            role_ctx=runtime_role_ctx,
                        )
                    collab_input_requested_roles = int(
                        collab_ev.get("input_requested_roles")
                        or len(collab_input_requested_role_list)
                        or collab_input_requested_roles
                    )
                    collab_truncated_by_budget = bool(collab_ev.get("truncated_by_budget") or collab_truncated_by_budget)

                    if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL:
                        manual_completion_consistency = _role_list_consistency(
                            final_support_roles,
                            collab_completed_role_list,
                        )
                        yield PipelineEvent("status", {
                            "step": "collab_manual_execution_check",
                            "expected_support_roles": list(final_support_roles),
                            "expected_support_names": [_role_display_name(r) for r in final_support_roles],
                            "completed_roles": int(collab_completed_roles),
                            "completed_role_list": list(collab_completed_role_list),
                            "completed_role_names": [_role_display_name(r) for r in collab_completed_role_list],
                            "truncated_by_budget": bool(collab_truncated_by_budget),
                            "match": bool(manual_completion_consistency.get("match")),
                            "missing_roles": list(manual_completion_consistency.get("missing") or []),
                            "extra_roles": list(manual_completion_consistency.get("extra") or []),
                        })

        except Exception as e:
            collab_dispatch_failed = True
            logger.warning("Multi-agent dispatch failed: %s", e)
            yield PipelineEvent("status", {
                "step": "collab_error",
                "message": f"澶欰gent鍗忎綔鍑洪敊: {str(e)[:100]}",
            })

    if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL:
        if not collab_dispatch_started:
            collab_input_requested_roles = int(len(final_support_roles))
            collab_input_requested_role_list = list(final_support_roles)
            collab_scheduled_roles = 0
            collab_scheduled_role_list = []
            collab_completed_roles = 0
            collab_completed_role_list = []
            manual_schedule_consistency = _role_list_consistency(final_support_roles, collab_scheduled_role_list)
            manual_completion_consistency = _role_list_consistency(final_support_roles, collab_completed_role_list)
        else:
            if not manual_schedule_consistency.get("expected") and not manual_schedule_consistency.get("actual"):
                manual_schedule_consistency = _role_list_consistency(final_support_roles, collab_scheduled_role_list)
            if not manual_completion_consistency.get("expected") and not manual_completion_consistency.get("actual"):
                manual_completion_consistency = _role_list_consistency(final_support_roles, collab_completed_role_list)

    if int(collab_input_requested_roles or 0) > 0 and int(collab_scheduled_roles or 0) == 0 and int(collab_completed_roles or 0) == 0:
        decision_reason = str(collab_decision_trace.get("reason") or "").strip()
        if collab_dispatch_failed:
            collab_not_executed_reason = "dispatch_failed"
        elif not ENABLE_HANDOFF:
            collab_not_executed_reason = "handoff_disabled"
        elif not collab_dispatch_asking_guard_passed:
            collab_not_executed_reason = "asking_guard"
        elif not should_collab:
            collab_not_executed_reason = f"decision_{decision_reason}" if decision_reason else "decision_guard"
        elif not str(reply or "").strip():
            collab_not_executed_reason = "empty_primary_reply"
        elif not collab_dispatch_started:
            collab_not_executed_reason = "dispatch_not_started"
        elif effective_collaboration_mode == _COLLABORATION_MODE_SINGLE:
            collab_not_executed_reason = "mode_single"

        if collab_not_executed_reason:
            collab_not_executed_reason_detail = _describe_collab_not_executed_reason(
                collab_not_executed_reason,
                decision_reason=decision_reason,
            )
            yield PipelineEvent("status", {
                "step": "collab_not_executed",
                "reason": collab_not_executed_reason,
                "reason_detail": collab_not_executed_reason_detail,
                "input_requested_roles": int(collab_input_requested_roles),
                "input_requested_role_list": list(collab_input_requested_role_list),
                "scheduled_roles": int(collab_scheduled_roles),
                "scheduled_role_list": list(collab_scheduled_role_list),
                "completed_roles": int(collab_completed_roles),
                "completed_role_list": list(collab_completed_role_list),
            })

    await _log_collaboration_decision_audit_event(
        user_id=user_id,
        workspace_id=workspace_id,
        collaboration_mode=effective_collaboration_mode,
        tier=int(intent.tier),
        explicit_collab_requested=bool(explicit_collab_requested),
        role_locked=bool(role_locked),
        message=message,
        decision_reason=str(collab_decision_trace.get("reason") or ""),
        should_collab=bool(should_collab),
        smalltalk_blocked=bool(collab_decision_trace.get("smalltalk_blocked")),
        support_role_count=len(final_support_roles),
        extra_from_reply_count=len(extra_from_reply),
        dispatch_started=bool(collab_dispatch_started),
        dispatch_done=bool(collab_dispatch_done),
        dispatch_failed=bool(collab_dispatch_failed),
        contributions_count=len(collab_contributions),
        handoff_enabled=bool(ENABLE_HANDOFF),
        llm_is_asking=bool(llm_is_asking),
    )

    # 鈹€鈹€ Step 9.5: 鍗忎綔鍚庤嚜鍔ㄧ敓鎴愭墽琛岀紪鎺掞紙Workspace鍐咃級鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    if (
        ENABLE_EXECUTION_ORCHESTRATION
        and workspace_id
        and conversation_id
        and not llm_is_asking
        and collab_contributions
    ):
        try:
            from src.services.workflow_orchestration import create_plan_from_collaboration

            plan = await create_plan_from_collaboration(
                workspace_id=workspace_id,
                user_id=user_id,
                user_message=message,
                primary_role=effective_role,
                contributions=collab_contributions,
                action=intent.action,
            )
            yield PipelineEvent("status", {
                "step": "execution_plan_created",
                "run_id": plan.get("run_id"),
                "task_count": len(plan.get("tasks", [])),
                "workspace_id": workspace_id,
            })
        except Exception as e:
            logger.warning("Execution orchestration plan failed: %s", e)
            yield PipelineEvent("status", {
                "step": "execution_plan_error",
                "message": f"鎵ц缂栨帓鐢熸垚澶辫触: {str(e)[:120]}",
            })

    _mark_stage("collaboration_orchestration_ms")

    # 鈹€鈹€ Step 10: 淇濆瓨娑堟伅 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    full_reply = reply
    if requires_verified_sources and not verified_sources_ready:
        guardrail_note = verified_sources_guardrail_note or _VERIFIED_SOURCE_GUARDRAIL_NOTE
        fallback_needed = len(str(full_reply or "").strip()) < 220 or not bool(_EXECUTION_LIST_LINE_RE.search(str(full_reply or "")))
        if fallback_needed:
            fallback_reply = _build_verified_source_unmet_fallback_reply(
                message,
                response_mode=effective_response_mode,
            )
            full_reply = f"{guardrail_note}\n\n{fallback_reply}".strip()
        else:
            full_reply = f"{guardrail_note}\n\n{full_reply}".strip()

    collab_unified_plan: Dict[str, Any] = {
        "markdown": "",
        "primary_points": [],
        "role_points": [],
    }
    if collab_contributions:
        collab_unified_plan = _build_collaboration_unified_plan(reply, collab_contributions)
        unified_markdown = str(collab_unified_plan.get("markdown") or "").strip()
        if unified_markdown:
            full_reply += "\n\n---\n" + unified_markdown
            yield PipelineEvent("status", {
                "step": "collab_unified_plan",
                "primary_points": collab_unified_plan.get("primary_points", []),
                "role_points": collab_unified_plan.get("role_points", []),
            })

        collab_sections: List[str] = []
        for idx, c in enumerate(collab_contributions, 1):
            section_reply = str(c.get("reply") or "").strip()
            if _is_collaboration_reply_placeholder(section_reply):
                continue
            section_name = str(c.get("name") or c.get("role") or f"涓撳{idx}").strip() or f"涓撳{idx}"
            collab_sections.append(f"#### 专岗补充 {idx} - {section_name}\n{section_reply}")

        if collab_sections:
            collab_header = (
                "\n\n---\n"
                "### 协作补充观点\n"
                "> 以下为本轮主回复之外的岗位补位建议，供决策参考。\n"
            )
            full_reply += collab_header + "\n\n".join(collab_sections)

    attachment_citations = _extract_attachment_citations(full_reply, normalized_attachments)
    if normalized_attachments:
        yield PipelineEvent("status", {
            "step": "attachment_citations",
            **attachment_citations,
        })

    pipeline_stage_timings_snapshot = dict(pipeline_stage_timings)
    pipeline_stage_timings_snapshot["total_ms"] = int((time.monotonic() - start) * 1000)

    if conversation_id:
        await _save_message(conversation_id, user_id, "user", message)

        await _save_message(conversation_id, user_id, "assistant", full_reply, {
            # 鍩虹瀛楁
            "role": effective_role,
            "role_name": primary_display,
            "action": intent.action,
            "features": features,
            "skills_used": skills_used,
            "response_mode": effective_response_mode,
            "learning_level": effective_learning_level,
            "learning_interaction_enabled": learning_interaction_enabled,
            "learning_structure_template": learning_structure_template,
            "collaboration_mode": effective_collaboration_mode,
            "role_locked": role_locked,
            "hired_roles": list(manual_hired_roles),
            "manual_primary_role": effective_role if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else "",
            "manual_support_roles": list(final_support_roles) if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else [],
            "manual_primary_fallback_used": bool(manual_primary_fallback_used),
            "collab_input_requested_roles": int(collab_input_requested_roles),
            "collab_input_requested_role_list": list(collab_input_requested_role_list),
            "collab_scheduled_roles": int(collab_scheduled_roles),
            "collab_scheduled_role_list": list(collab_scheduled_role_list),
            "collab_completed_roles": int(collab_completed_roles),
            "collab_completed_role_list": list(collab_completed_role_list),
            "collab_truncated_by_budget": bool(collab_truncated_by_budget),
            "collab_not_executed_reason": collab_not_executed_reason,
            "collab_not_executed_reason_detail": collab_not_executed_reason_detail,
            "manual_support_schedule_match": bool(manual_schedule_consistency.get("match")) if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else True,
            "manual_support_schedule_missing_roles": list(manual_schedule_consistency.get("missing") or []) if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else [],
            "manual_support_schedule_extra_roles": list(manual_schedule_consistency.get("extra") or []) if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else [],
            "manual_support_completion_match": bool(manual_completion_consistency.get("match")) if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else True,
            "manual_support_completion_missing_roles": list(manual_completion_consistency.get("missing") or []) if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else [],
            "manual_support_completion_extra_roles": list(manual_completion_consistency.get("extra") or []) if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else [],
            "attachment_count": len(normalized_attachments),
            "attachment_degraded_count": attachment_stats.get("degraded_vision", 0),
            "attachment_vision_fallback_count": attachment_stats.get("vision_fallback", 0),
            "attachment_layout_table_count": attachment_stats.get("layout_table", 0),
            "attachment_layout_source_breakdown": attachment_layout_breakdown,
            "attachment_names": [x.get("filename") for x in normalized_attachments],
            "attachment_tags": [x.get("citation_tag") for x in normalized_attachments if x.get("citation_tag")],
            "attachment_citations": attachment_citations,
            "attachment_structured": attachment_structured,
            "credibility": credibility_snapshot,
            "requires_verified_sources": bool(requires_verified_sources),
            "verified_sources_ready": bool(verified_sources_ready),
            "verified_sources_count": int(verified_sources_count),
            "verified_sources_guardrail_note": verified_sources_guardrail_note,
            "longterm_memory_loaded": bool(longterm_memory_ctx),
            "longterm_memory_fact_count": int(longterm_memory_fact_count),
            "longterm_memory_context_count": int(longterm_memory_context_count),
            "longterm_profile_persona": longterm_profile_persona,
            "longterm_profile_platforms": list(longterm_profile_platforms),
            "support_roles": final_support_roles,
            "support_names": [_role_display_name(r) for r in final_support_roles],
            "contributions": len(collab_contributions),
            "collab_unified_plan_ready": bool(collab_unified_plan.get("markdown")),
            "collab_unified_role_points": len(collab_unified_plan.get("role_points", [])),
            "collab_unified_plan": collab_unified_plan,
            **dispatch_debug_fields,
            # 瀹屾暣璋冨害淇℃伅锛堢敤浜庡巻鍙插璇濋噸寤篸ispatch鍗＄墖锛?
            "tier": intent.tier,
            "tier_label": {0: "quick", 1: "single", 2: "multi"}.get(intent.tier, "single"),
            "confidence": round(intent.confidence, 2),
            "platform": intent.platform,
            "domain_id": intent.domain_id,
            "domain_confidence": intent.domain_confidence,
            "keywords": intent.keywords,
            "tool_calls_executed": int(tool_calls_executed),
            "tool_calls_dropped_by_round_cap": int(tool_calls_dropped_by_round_cap),
            "tool_calls_dropped_by_budget": int(tool_calls_dropped_by_budget),
            "tool_calls_dropped_total": int(tool_calls_dropped_by_round_cap + tool_calls_dropped_by_budget),
            "tool_execution_budget_exhausted": bool(tool_execution_budget_exhausted),
            "tool_execution_budget_seconds": float(tool_execution_total_budget_seconds),
            "response_outline": response_outline,
            "response_short_first": bool(response_outline.get("short_first_ready")),
            "response_action_count": len(response_outline.get("action_points", [])),
            "task_framing": task_framing,
            "task_should_clarify": bool(task_framing.get("should_clarify")),
            "task_anchor_applied": bool(task_anchor_guard.get("applied")),
            "task_anchor_reason": str(task_anchor_guard.get("reason") or ""),
            "task_anchor_overrides": list(task_anchor_guard.get("overrides") or []),
            "llm_usage": llm_usage_totals,
            "llm_estimated_cost_usd": llm_estimated_cost_usd,
            "quality_retry_applied": bool(quality_retry_applied),
            "short_first_guard_applied": bool(short_first_guard_applied),
            "short_first_guard_reason": short_first_guard_reason,
            "short_first_summary_points": short_first_summary_points,
            "short_first_action_points": short_first_action_points,
            "copy_ready_guard_applied": bool(copy_ready_guard_applied),
            "copy_ready_guard_reason": copy_ready_guard_reason,
            "copy_ready_guard_scene": copy_ready_guard_scene,
            "mode_differentiation_guard_applied": bool(mode_differentiation_guard_applied),
            "mode_differentiation_guard_reason": mode_differentiation_guard_reason,
            "collaboration_mode_guard_applied": bool(collaboration_mode_guard_applied),
            "collaboration_mode_guard_reason": collaboration_mode_guard_reason,
            "strategy_combo_contract_guard_applied": bool(strategy_combo_contract_guard_applied),
            "strategy_combo_contract_guard_reason": strategy_combo_contract_guard_reason,
            "strategy_combo_contract_missing_sections": list(strategy_combo_contract_missing_sections),
            "quality_score_before_retry": round(float(quality_score_before_retry), 3) if quality_score_before_retry is not None else None,
            "quality_score_after_retry": round(float(quality_score_after_retry), 3) if quality_score_after_retry is not None else None,
            "learning_delivery_guard_applied": bool(learning_delivery_guard_sources),
            "learning_delivery_guard_sources": list(learning_delivery_guard_sources),
            "learning_delivery_guard_gaps": learning_delivery_guard_gaps,
            "analysis_delivery_guard_applied": bool(analysis_delivery_guard_sources),
            "analysis_delivery_guard_sources": list(analysis_delivery_guard_sources),
            "analysis_delivery_guard_gaps": analysis_delivery_guard_gaps,
            "design_delivery_guard_applied": bool(design_delivery_guard_sources),
            "design_delivery_guard_sources": list(design_delivery_guard_sources),
            "design_delivery_guard_gaps": design_delivery_guard_gaps,
            "pipeline_stage_timings_ms": pipeline_stage_timings_snapshot,
        })

    await _log_llm_audit_event(
        user_id=user_id,
        workspace_id=workspace_id,
        stage="chat_stream",
        llm_calls=llm_calls,
        usage_totals=llm_usage_totals,
        estimated_cost_usd=llm_estimated_cost_usd,
    )

    elapsed = time.monotonic() - start
    pipeline_stage_timings_final = dict(pipeline_stage_timings_snapshot)
    pipeline_stage_timings_final["total_ms"] = int(elapsed * 1000)

    yield PipelineEvent("done", {
        "role": effective_role,
        "role_name": primary_display,
        "elapsed_ms": int(elapsed * 1000),
        "tier": {0: "quick", 1: "single", 2: "multi"}.get(intent.tier, "single"),
        "llm_calls": llm_calls,
        "features_used": features,
        "skills_used": skills_used,
        "tool_calls_executed": int(tool_calls_executed),
        "tool_calls_dropped_by_round_cap": int(tool_calls_dropped_by_round_cap),
        "tool_calls_dropped_by_budget": int(tool_calls_dropped_by_budget),
        "tool_calls_dropped_total": int(tool_calls_dropped_by_round_cap + tool_calls_dropped_by_budget),
        "tool_execution_budget_exhausted": bool(tool_execution_budget_exhausted),
        "tool_execution_budget_seconds": float(tool_execution_total_budget_seconds),
        "support_roles": final_support_roles,
        "domain_id": intent.domain_id,
        "domain_confidence": intent.domain_confidence,
        "contributions": len(collab_contributions),
        "collab_unified_plan_ready": bool(collab_unified_plan.get("markdown")),
        "collab_unified_role_points": len(collab_unified_plan.get("role_points", [])),
        "was_asking": llm_is_asking,
        "response_mode": effective_response_mode,
        "learning_level": effective_learning_level,
        "learning_interaction_enabled": learning_interaction_enabled,
        "learning_structure_template": learning_structure_template,
        "collaboration_mode": effective_collaboration_mode,
        "role_locked": role_locked,
        "hired_roles": list(manual_hired_roles),
        "manual_primary_role": effective_role if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else "",
        "manual_support_roles": list(final_support_roles) if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else [],
        "manual_primary_fallback_used": bool(manual_primary_fallback_used),
        "collab_input_requested_roles": int(collab_input_requested_roles),
        "collab_input_requested_role_list": list(collab_input_requested_role_list),
        "collab_scheduled_roles": int(collab_scheduled_roles),
        "collab_scheduled_role_list": list(collab_scheduled_role_list),
        "collab_completed_roles": int(collab_completed_roles),
        "collab_completed_role_list": list(collab_completed_role_list),
        "collab_truncated_by_budget": bool(collab_truncated_by_budget),
        "collab_not_executed_reason": collab_not_executed_reason,
        "collab_not_executed_reason_detail": collab_not_executed_reason_detail,
        "manual_support_schedule_match": bool(manual_schedule_consistency.get("match")) if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else True,
        "manual_support_schedule_missing_roles": list(manual_schedule_consistency.get("missing") or []) if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else [],
        "manual_support_schedule_extra_roles": list(manual_schedule_consistency.get("extra") or []) if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else [],
        "manual_support_completion_match": bool(manual_completion_consistency.get("match")) if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else True,
        "manual_support_completion_missing_roles": list(manual_completion_consistency.get("missing") or []) if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else [],
        "manual_support_completion_extra_roles": list(manual_completion_consistency.get("extra") or []) if effective_collaboration_mode == _COLLABORATION_MODE_MANUAL else [],
        "attachment_count": len(normalized_attachments),
        "attachment_degraded_count": attachment_stats.get("degraded_vision", 0),
        "attachment_vision_fallback_count": attachment_stats.get("vision_fallback", 0),
        "attachment_layout_table_count": attachment_stats.get("layout_table", 0),
        "attachment_layout_source_breakdown": attachment_layout_breakdown,
        "attachment_citations": attachment_citations,
        "attachment_structured": attachment_structured,
        "credibility": credibility_snapshot,
        "requires_verified_sources": bool(requires_verified_sources),
        "verified_sources_ready": bool(verified_sources_ready),
        "verified_sources_count": int(verified_sources_count),
        "verified_sources_guardrail_note": verified_sources_guardrail_note,
        "longterm_memory_loaded": bool(longterm_memory_ctx),
        "longterm_memory_fact_count": int(longterm_memory_fact_count),
        "longterm_memory_context_count": int(longterm_memory_context_count),
        "longterm_profile_persona": longterm_profile_persona,
        "longterm_profile_platforms": list(longterm_profile_platforms),
        **dispatch_debug_fields,
        "response_outline": response_outline,
        "response_short_first": bool(response_outline.get("short_first_ready")),
        "response_action_count": len(response_outline.get("action_points", [])),
        "task_framing": task_framing,
        "task_should_clarify": bool(task_framing.get("should_clarify")),
        "task_anchor_applied": bool(task_anchor_guard.get("applied")),
        "task_anchor_reason": str(task_anchor_guard.get("reason") or ""),
        "task_anchor_overrides": list(task_anchor_guard.get("overrides") or []),
        "llm_usage": llm_usage_totals,
        "llm_estimated_cost_usd": llm_estimated_cost_usd,
        "quality_retry_applied": bool(quality_retry_applied),
        "quality_score_before_retry": round(float(quality_score_before_retry), 3) if quality_score_before_retry is not None else None,
        "quality_score_after_retry": round(float(quality_score_after_retry), 3) if quality_score_after_retry is not None else None,
        "learning_delivery_guard_applied": bool(learning_delivery_guard_sources),
        "learning_delivery_guard_sources": list(learning_delivery_guard_sources),
        "learning_delivery_guard_gaps": learning_delivery_guard_gaps,
        "analysis_delivery_guard_applied": bool(analysis_delivery_guard_sources),
        "analysis_delivery_guard_sources": list(analysis_delivery_guard_sources),
        "analysis_delivery_guard_gaps": analysis_delivery_guard_gaps,
        "design_delivery_guard_applied": bool(design_delivery_guard_sources),
        "design_delivery_guard_sources": list(design_delivery_guard_sources),
        "design_delivery_guard_gaps": design_delivery_guard_gaps,
        "mode_differentiation_guard_applied": bool(mode_differentiation_guard_applied),
        "mode_differentiation_guard_reason": mode_differentiation_guard_reason,
        "collaboration_mode_guard_applied": bool(collaboration_mode_guard_applied),
        "collaboration_mode_guard_reason": collaboration_mode_guard_reason,
        "strategy_combo_contract_guard_applied": bool(strategy_combo_contract_guard_applied),
        "strategy_combo_contract_guard_reason": strategy_combo_contract_guard_reason,
        "strategy_combo_contract_missing_sections": list(strategy_combo_contract_missing_sections),
        "pipeline_stage_timings_ms": pipeline_stage_timings_final,
    })

    # 鈹€鈹€ Step 11: 鍚庡彴浠诲姟 鈥?fire-and-forget 鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€鈹€
    # 杩介棶鍥炲涓嶇撼鍏ヨ川閲?淇′换璇勪及锛堥棶棰樻湰韬笉浠ｈ〃鍥炲璐ㄩ噺锛?
    if ENABLE_BACKGROUND_TASKS and not llm_is_asking:
        asyncio.create_task(_dispatch_background(
            user_id, conversation_id or "", effective_role, message, reply, intent,
            product_ids=all_product_ids or None,
        ))


# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?
# 闈炴祦寮忚皟鐢?
# 鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺愨晲鈺?



_FAST_SINGLE_SHORT_SIGNALS: tuple[str, ...] = (
    "简短", "要点", "3条", "三条", "一句话", "30秒", "短版", "简版",
    "brief", "short", "concise",
)

_FAST_SINGLE_PRIORITY_SIGNALS: tuple[str, ...] = (
    "优先级", "优先", "p1", "p2", "p3", "priority",
)

_FAST_SINGLE_REQUEST_SIGNALS: tuple[str, ...] = (
    "建议", "方案", "执行", "动作", "计划", "strategy", "plan", "action",
)

_FAST_SINGLE_HEAVY_BLOCKERS: tuple[str, ...] = (
    "详细", "全面", "完整", "深入", "原理", "推导", "原因", "代码", "sql", "报表", "数据表",
    "附件", "图片", "截图", "pdf", "联网", "最新", "引用", "来源", "证据",
    "多智能体", "multi-agent", "multi agent", "多角色", "协同", "协作",
    "并且", "同时", "分别", "逐步", "step by step", "deep dive", "root cause", "full analysis",
)


_FAST_SINGLE_MULTI_BLOCKERS: tuple[str, ...] = (
    "多智能体", "multi-agent", "multi agent", "多角色", "协作", "协同", "分工",
    "分别", "并且", "同时",
)

_FAST_SINGLE_MULTI_TIER_MAX_LEN = 160

_FAST_SINGLE_TEMPLATE_MAX_LEN = 140

_FAST_SINGLE_HEAVY_RE = re.compile(
    r"\b(detailed|comprehensive|full\s+analysis|deep\s*dive|root\s*cause|"
    r"break\s*down|step\s*by\s*step|architecture|algorithm|benchmark)\b"
)

_FAST_SINGLE_TOPIC_RE = re.compile(
    r"(?:关于)?(?P<topic>[^，。；！？,.;!?]{2,20}?)(?:执行建议|建议|方案|计划|策略)"
)


_TOOL_ENABLE_EXPLICIT_SIGNALS: tuple[str, ...] = (
    "调用工具", "工具", "查询", "查一下", "任务状态", "状态", "search", "query",
    "api", "webhook", "sql", "报表", "联网", "最新", "同步",
)

_TOOL_ENABLE_IMPLICIT_SIGNALS: tuple[str, ...] = (
    "roi", "roas", "推广",
)


def _should_enable_tool_use_for_request(
    *,
    message: str,
    requires_verified_sources: bool,
    workspace_id: Optional[int],
    product_id: Optional[int],
    product_ids: Optional[List[int]],
) -> bool:
    text = str(message or "").strip()
    if not text:
        return False

    lowered = text.lower()
    if bool(requires_verified_sources):
        return True

    if any(sig in lowered for sig in _TOOL_ENABLE_EXPLICIT_SIGNALS):
        return True

    if any(sig in lowered for sig in _TOOL_ENABLE_IMPLICIT_SIGNALS):
        return True

    has_entity_context = bool(workspace_id or product_id or (product_ids and len(product_ids) > 0))
    if has_entity_context and any(sig in lowered for sig in ("分析", "诊断", "排查", "审批")):
        return True

    return False


def _looks_heavy_for_fast_single(text_lower: str) -> bool:
    if any(token in text_lower for token in _FAST_SINGLE_HEAVY_BLOCKERS):
        return True
    if text_lower.count("?") + text_lower.count("？") >= 2:
        return True
    if text_lower.count("\n") >= 2:
        return True
    return bool(_FAST_SINGLE_HEAVY_RE.search(text_lower))


def _extract_fast_single_topic(message: str) -> str:
    text = str(message or "").strip()
    if not text:
        return "当前任务"

    compact = re.sub(r"\s+", "", text)
    compact = re.sub(r"^(请|麻烦|帮我|请给我|给我|请你|请帮我|请提供|提供|想要|我要)+", "", compact)

    match = _FAST_SINGLE_TOPIC_RE.search(compact)
    if match:
        topic = str(match.group("topic") or "").strip()
    else:
        topic = re.sub(
            r"(简短|要点|优先级|3条|三条|一句话|请|帮我|给我|说明|并|建议|方案|执行|计划|策略|"
            r"。|，|、|；|！|？|\?|,|;|\.)",
            "",
            compact,
        ).strip()

    topic = topic.strip("的：:，,。；;")
    if not topic:
        topic = "当前任务"
    return topic[:16]



_FAST_SINGLE_DEFAULT_METRICS: tuple[str, ...] = (
    "点击率CTR",
    "转化率CVR",
    "投产比ROI",
)


def _infer_fast_single_metric_names(message: str) -> List[str]:
    lowered = str(message or "").lower()
    metric_hints: tuple[tuple[tuple[str, ...], str], ...] = (
        (("roi", "roas", "投产", "投产比"), "投产比ROI"),
        (("gmv", "销售额", "成交额"), "GMV"),
        (("转化", "cvr", "下单"), "转化率CVR"),
        (("点击", "ctr", "曝光", "进店"), "点击率CTR"),
        (("复购", "留存"), "复购率"),
        (("客单", "aov"), "客单价AOV"),
        (("成本", "cpa", "cpc", "获客"), "获客成本CPA"),
    )

    metrics: List[str] = []
    for hints, metric_name in metric_hints:
        if any(hint in lowered for hint in hints):
            if metric_name not in metrics:
                metrics.append(metric_name)

    for metric_name in _FAST_SINGLE_DEFAULT_METRICS:
        if metric_name not in metrics:
            metrics.append(metric_name)
        if len(metrics) >= 3:
            break

    return metrics[:3]


def _infer_fast_single_stage_phrases(message: str) -> List[str]:
    lowered = str(message or "").lower()
    has_preheat = "预热" in lowered
    has_burst = "爆发" in lowered
    has_return = "返场" in lowered
    if has_preheat and has_burst and has_return:
        return ["预热期动作", "爆发期动作", "返场期动作"]
    return ["入口动作", "承接动作", "沉淀动作"]


def _build_fast_single_template_reply(message: str, *, response_mode: str) -> Optional[str]:
    text = str(message or "").strip()
    if not text:
        return None

    lowered = text.lower()
    if len(text) > _FAST_SINGLE_TEMPLATE_MAX_LEN:
        return None
    if not any(sig in lowered for sig in _FAST_SINGLE_SHORT_SIGNALS):
        return None
    if not any(sig in lowered for sig in _FAST_SINGLE_PRIORITY_SIGNALS):
        return None
    if not any(sig in lowered for sig in _FAST_SINGLE_REQUEST_SIGNALS):
        return None
    if _looks_heavy_for_fast_single(lowered):
        return None

    topic = _extract_fast_single_topic(text)
    metrics = _infer_fast_single_metric_names(text)
    stages = _infer_fast_single_stage_phrases(text)
    mode = _normalize_response_mode(response_mode)
    if mode == _RESPONSE_MODE_LEARNING:
        return (
            f"30秒结论：{topic}先抓主目标，再按优先级逐步落地。\n"
            f"P1（最高优先级）：先明确成功标准并启动{stages[0]}（指标：{metrics[0]}）。\n"
            f"P2（次优先级）：围绕关键动作小步试跑并复盘{stages[1]}（指标：{metrics[1]}）。\n"
            f"P3（第三优先级）：补齐低成本优化项，稳定{stages[2]}（指标：{metrics[2]}）。\n"
            f"下一步：今天定负责人并落地P1，明天按{metrics[0]}、{metrics[1]}复盘。"
        )

    return (
        f"30秒结论：{topic}先抓“引流入口→转化承接→复购沉淀”，按高影响低成本排序。\n"
        f"P1（最高优先级）：统一主卖点与入口素材，先做{stages[0]}（指标：{metrics[0]}）。\n"
        f"P2（次优先级）：优化详情页与套餐承接，强化{stages[1]}（指标：{metrics[1]}）。\n"
        f"P3（第三优先级）：补老客召回与复购机制，做稳{stages[2]}（指标：{metrics[2]}）。\n"
        f"下一步：今天内确定负责人并上线P1，24小时后按{metrics[0]}、{metrics[1]}判定加码或回滚。"
    )


def _build_fast_single_fallback_reply(message: str, *, response_mode: str) -> str:
    topic = _extract_fast_single_topic(message)
    mode = _normalize_response_mode(response_mode)
    if mode == _RESPONSE_MODE_LEARNING:
        return (
            f"30秒结论：{topic}可先用“目标-动作-复盘”三段法。\n"
            "P1（最高优先级）：先定义目标与成功标准。\n"
            "P2（次优先级）：执行1个关键动作并记录结果。\n"
            "P3（第三优先级）：根据结果补齐低成本优化项。\n"
            "下一步：今天完成首轮动作，明天复盘并决定是否扩大。"
        )
    return (
        f"30秒结论：{topic}先抓主链路，再按优先级推进。\n"
        "P1（最高优先级）：先做入口动作，拿到初始反馈。\n"
        "P2（次优先级）：优化转化承接环节，提升效率。\n"
        "P3（第三优先级）：补充复购与留存动作，稳住效果。\n"
        "下一步：今天确定动作与负责人，24小时后复盘。"
    )


def _build_fast_single_compact_prompt(*, response_mode: str, action: str = "") -> str:
    mode = _normalize_response_mode(response_mode)
    action_hint = str(action or "").strip().lower()
    action_line = f"用户动作偏好：{action_hint}。" if action_hint else ""

    if mode == _RESPONSE_MODE_LEARNING:
        return (
            "你是资深教学型顾问。用户要求简短版本。"
            f"{action_line}"
            "请严格输出5行，禁止多写："
            "1) 30秒结论：一句话；"
            "2) P1（最高优先级）：1个动作+为什么；"
            "3) P2（次优先级）：1个动作+为什么；"
            "4) P3（第三优先级）：1个动作+为什么；"
            "5) 下一步：今天可执行动作+明天复盘指标。"
            "总字数<=180，禁止追问，禁止解释你的思考过程。"
        )

    return (
        "你是资深执行顾问。用户要求简短版本。"
        f"{action_line}"
        "请严格输出5行，禁止多写："
        "1) 30秒结论：一句话；"
        "2) P1（最高优先级）：1个可执行动作；"
        "3) P2（次优先级）：1个可执行动作；"
        "4) P3（第三优先级）：1个可执行动作；"
        "5) 下一步：今天可执行动作+24小时复盘指标。"
        "总字数<=180，禁止追问，禁止解释你的思考过程。"
    )



def _should_use_fast_single_path(
    *,
    message: str,
    intent_tier: int,
    explicit_collab_requested: bool,
    collaboration_mode: str,
    normalized_attachment_count: int,
    requires_verified_sources: bool,
) -> bool:
    text = str(message or "").strip()
    if not text:
        return False

    if bool(explicit_collab_requested):
        return False

    if _normalize_collaboration_mode(collaboration_mode) == _COLLABORATION_MODE_MANUAL:
        return False

    if int(normalized_attachment_count or 0) > 0:
        return False

    if bool(requires_verified_sources):
        return False

    if len(text) > 180:
        return False

    lowered = text.lower()
    has_short_signal = any(sig in lowered for sig in _FAST_SINGLE_SHORT_SIGNALS)
    has_priority_signal = any(sig in lowered for sig in _FAST_SINGLE_PRIORITY_SIGNALS)
    if not (has_short_signal and has_priority_signal):
        return False

    if _looks_heavy_for_fast_single(lowered):
        return False

    tier = int(intent_tier or 1)
    if tier == 1:
        return True

    # 某些“短答+优先级”请求会被意图层抬到 multi；在无协作信号时允许降级走 fast-single。
    if tier == 2:
        if len(text) > _FAST_SINGLE_MULTI_TIER_MAX_LEN:
            return False
        if any(token in lowered for token in _FAST_SINGLE_MULTI_BLOCKERS):
            return False
        return True

    return False



_QUICK_TEMPLATE_BUSINESS_BLOCKERS: tuple[str, ...] = (
    "分析", "策略", "方案", "计划", "优化", "报表", "数据", "sql",
    "roi", "gmv", "转化", "投放", "预算", "活动", "库存", "商品", "订单",
    "设计", "代码", "接口", "部署", "财务", "成本", "利润", "退款", "投诉",
    "seo", "脚本", "文案", "流程", "执行", "排查", "故障", "bug",
)

_QUICK_TEMPLATE_ACK_CN_TOKENS: tuple[str, ...] = (
    "请确认", "确认一下", "确认下", "短确认", "简短确认", "收到请回复",
    "收到", "明白", "了解了", "知道了", "在吗",
)



def _is_business_heavy_for_quick_template(text_lower: str) -> bool:
    if any(token in text_lower for token in _QUICK_TEMPLATE_BUSINESS_BLOCKERS):
        return True
    return bool(
        re.search(
            r"\b(analy[sz]e|analysis|strategy|plan|optimi[sz]e|report|data|sql|"
            r"roi|gmv|conversion|campaign|budget|api|deploy|finance|cost|profit|"
            r"error|bug|workflow|execution)\b",
            text_lower,
        )
    )



def _build_quick_template_reply(message: str) -> Optional[str]:
    """为确认/问候类 quick 消息提供无 LLM 的极速模板回复。"""
    text = str(message or "").strip()
    if not text:
        return None

    text_lower = text.lower()
    if len(text) > 120:
        return None
    if _is_business_heavy_for_quick_template(text_lower):
        return None

    if re.search(r"\b(short|brief)\s+confirmation\b", text_lower):
        return "Confirmed. I am online and ready for your next instruction."

    if re.search(r"\b(please\s+)?(confirm|ack(?:nowledge)?|roger|noted)\b", text_lower):
        return "Confirmed. I am online and ready for your next instruction."

    if any(token in text_lower for token in _QUICK_TEMPLATE_ACK_CN_TOKENS):
        return "已确认收到，我在线。请继续告诉我下一步。"

    if text_lower in {"hello", "hi"}:
        return (
            "Hi, I am here and ready. "
            "You can send: (1) your goal + timeframe, "
            "(2) current data + constraints, or (3) ask for a 30-second starter plan. "
            "Next step: send platform + goal + budget, and I will return a first draft."
        )

    if text_lower in {"你好", "嗨"}:
        return (
            "你好，我在。为了马上给你有用结果，你可以直接发：\n"
            "1. 目标+时限（例：7天提升转化）\n"
            "2. 现状数据（预算/流量/转化）\n"
            "3. 你要的形式（30秒结论 / 今日3步动作）\n"
            "下一步：你只要回“平台+目标+预算”，我就给你首版方案。\n"
            "注意：以上用于快速对齐需求，具体建议需结合你的实际数据复核（仅供参考）。"
        )

    if any(token in text_lower for token in ("谢谢", "感谢", "thank you", "thanks")):
        return "不客气，我在这里，随时继续。"

    return None


def _build_verified_source_unmet_fallback_reply(message: str, *, response_mode: str) -> str:
    topic = str(message or "外部数据分析任务").strip().replace("\n", " ")[:80] or "外部数据分析任务"
    if _normalize_response_mode(response_mode) == _RESPONSE_MODE_LEARNING:
        return (
            f"30秒结论：关于“{topic}”，当前未拿到可验证联网来源，不能直接下最终结论；先按统一口径搭建可复核分析框架。\n"
            "为什么：市场份额与行业口径差异大（样本范围/统计周期不同），若缺少来源与日期映射，结论容易失真。\n"
            "怎么做：1) 先定义口径与时间窗口；2) 为每条结论标注来源与发布日期；3) 用同比/环比阈值判断是否显著变化。\n"
            "示例：可先用“份额同比<-1.5%、环比<-1.0%”作为预警阈值（示例阈值，需按你的业务复核）。\n"
            "下一步：请补充至少3条可验证来源（来源名+日期+关键数值），我会输出逐条证据映射版洞察。"
        )

    return (
        f"## 30秒结论\n"
        f"- 任务：{topic}。当前未获取到可验证联网来源，暂不下最终市场结论。\n"
        "- 先给可执行的“证据映射框架”，你补齐来源后可直接生成正式洞察。\n"
        "- 风险提示：缺来源直接决策会误判趋势，需先完成口径与来源校验。\n\n"
        "### 今日先做\n"
        "1. 统一口径：明确市场范围、时间窗口、统计口径（份额定义/样本边界）。\n"
        "2. 来源映射：每条结论至少对应1个可验证来源，并标注发布日期与关键数值。\n"
        "3. 阈值判定：先用示例阈值做预警（份额同比<-1.5%、环比<-1.0%），再按你的业务基线校准。\n\n"
        "下一步（24小时内）：补齐3条来源（来源名+日期+数值）后，我将输出“结论-证据一一对应”的正式洞察报告。"
    )

def _build_chat_timeout_fallback_reply(message: str, response_mode: str) -> str:
    topic = str(message or "用户问题").strip().replace("\n", " ")[:80] or "用户问题"
    if _normalize_response_mode(response_mode) == _RESPONSE_MODE_LEARNING:
        return (
            f"30秒结论：你问的是“{topic}”，建议先从定义、核心指标、常见误区三步掌握，再结合你自己的业务场景落地。\n\n"
            "详细讲解：\n"
            "1. 先明确概念边界：术语定义、适用场景、不适用场景。\n"
            "2. 再看指标关系：输入指标、过程指标、结果指标如何联动。\n"
            "3. 最后做实践映射：把方法映射到你的当前目标与约束。\n\n"
            "怎么做（可执行）：\n"
            "1. 先写一版你的现状基线（当前数据与目标差距）。\n"
            "2. 选2-3个关键动作，逐项设定量化验收标准。\n"
            "3. 按周复盘，记录有效动作并淘汰无效动作。\n\n"
            "常见误区：只看结论不看前提、只看单点指标不看链路、没有复盘就持续加动作。\n\n"
            "下一步：如果你愿意，我可以基于你的具体业务数据给出一版可直接执行的个性化计划。"
        )

    return (
        f"30秒结论：围绕“{topic}”，建议用“目标拆解-动作执行-风险回滚”三段法推进，本周优先跑一轮小步快跑验证。\n\n"
        "可执行步骤：\n"
        "1. 目标拆解：把总目标拆成流量、转化、客单、复购四个可监控子目标。\n"
        "2. 动作清单：每个子目标配置1-2个可落地动作，明确负责人、开始时间、截止时间。\n"
        "3. 验证闭环：每日跟踪核心指标，发现偏差就当日修正。\n\n"
        "量化KPI（示例）：\n"
        "- 流量：曝光与点击率同步提升；\n"
        "- 转化：关键页面转化率逐步抬升；\n"
        "- 成本：投放成本不突破预算红线。\n\n"
        "风险与回滚：\n"
        "- 若关键指标连续2天恶化，暂停新增动作并回滚到上一个稳定版本。\n"
        "- 若成本超预算阈值，立即收缩低ROI动作，保留高确定性动作。\n\n"
        "下一步：先执行24小时小规模验证，确认有效后再扩大投入。"
    )


async def chat_simple(
    message: str, user_id: int, *,
    role: Optional[str] = None,
    role_lock: Optional[bool] = None,
    conversation_id: Optional[str] = None,
    product_id: Optional[int] = None,
    product_ids: Optional[List[int]] = None,
    workspace_id: Optional[int] = None,
    response_mode: str = _RESPONSE_MODE_EXECUTION,
    learning_level: str = _LEARNING_LEVEL_HIGHER_VOCATIONAL,
    collaboration_mode: str = _COLLABORATION_MODE_AUTO,
    hired_roles: Optional[List[str]] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    from src.config import CHAT_SIMPLE_TIMEOUT_SECONDS

    # Non-stream endpoint should still allow a full response cycle under realistic model latency.
    base_timeout_seconds = max(30, int(CHAT_SIMPLE_TIMEOUT_SECONDS or 240))
    manual_selected_roles = {
        str(item or "").strip().lower()
        for item in (hired_roles or [])
        if str(item or "").strip()
    }
    manual_support_count = max(0, len(manual_selected_roles) - 1)
    normalized_collaboration_mode = _normalize_collaboration_mode(collaboration_mode)
    normalized_response_mode = _normalize_response_mode(response_mode)

    # Avoid premature timeout fallback for complex non-stream turns:
    # - default floor keeps single/auto execution stable under slower model responses;
    # - manual collaboration gets higher floor as support-role fan-out increases.
    timeout_floor = 180
    if normalized_collaboration_mode == _COLLABORATION_MODE_MANUAL:
        timeout_floor = 240 if manual_support_count >= 1 else 180
        if manual_support_count >= 4:
            timeout_floor = 300
    elif normalized_response_mode == _RESPONSE_MODE_LEARNING:
        timeout_floor = max(timeout_floor, 180)
    timeout_seconds = max(timeout_floor, base_timeout_seconds)
    parts: List[str] = []
    metadata: Dict[str, Any] = {}
    status_intent_snapshot: Dict[str, Any] = {}
    status_task_framing_snapshot: Dict[str, Any] = {}
    status_context_snapshot: Dict[str, Any] = {}
    status_collab_snapshot: Dict[str, Any] = {}

    def _merge_status_snapshots(target: Dict[str, Any]) -> Dict[str, Any]:
        merged = dict(target or {})

        if status_intent_snapshot:
            for key, value in status_intent_snapshot.items():
                if key == "step":
                    continue
                merged.setdefault(key, value)

        if status_task_framing_snapshot:
            framing = {
                key: value
                for key, value in status_task_framing_snapshot.items()
                if key != "step"
            }
            if framing:
                merged.setdefault("task_framing", framing)
            merged.setdefault(
                "task_should_clarify",
                bool(status_task_framing_snapshot.get("should_clarify")),
            )

        if status_context_snapshot:
            for key in (
                "has_product_ctx",
                "has_metrics_ctx",
                "has_attachments",
                "attachment_parsed_count",
                "attachment_degraded_count",
                "attachment_vision_fallback_count",
                "attachment_layout_table_count",
                "attachment_layout_source_breakdown",
                "attachment_table_count",
                "realtime_sources_count",
                "requires_verified_sources",
                "verified_sources_ready",
                "verified_sources_count",
                "longterm_memory_loaded",
                "longterm_memory_fact_count",
                "longterm_memory_context_count",
                "longterm_profile_persona",
                "longterm_profile_platforms",
            ):
                if key in status_context_snapshot:
                    merged.setdefault(key, status_context_snapshot.get(key))

        if status_collab_snapshot:
            completed_roles = [
                str(item.get("role") or "").strip().lower()
                for item in (status_collab_snapshot.get("contributions") or [])
                if isinstance(item, dict) and str(item.get("role") or "").strip()
            ]
            if completed_roles:
                merged.setdefault("collab_completed_role_list", completed_roles)
                merged.setdefault("collab_completed_roles", len(completed_roles))
            merged.setdefault(
                "collab_truncated_by_budget",
                bool(status_collab_snapshot.get("truncated_by_budget")),
            )
            if "scheduled_roles" in status_collab_snapshot:
                merged.setdefault("collab_scheduled_roles", int(status_collab_snapshot.get("scheduled_roles") or 0))
            if isinstance(status_collab_snapshot.get("scheduled_role_list"), list):
                merged.setdefault("collab_scheduled_role_list", list(status_collab_snapshot.get("scheduled_role_list") or []))

        return merged

    async def _collect_stream() -> None:
        nonlocal metadata, status_intent_snapshot, status_task_framing_snapshot, status_context_snapshot, status_collab_snapshot
        async for ev in chat_stream(
            message=message, user_id=user_id, role=role, role_lock=role_lock,
            conversation_id=conversation_id, product_id=product_id,
            product_ids=product_ids,
            workspace_id=workspace_id,
            response_mode=response_mode,
            learning_level=learning_level,
            collaboration_mode=collaboration_mode,
            hired_roles=hired_roles,
            attachments=attachments,
        ):
            if ev.event == "token":
                parts.append(ev.data.get("text", ""))
            elif ev.event == "replace_reply":
                rewritten = str(ev.data.get("reply") or "")
                if rewritten:
                    parts.clear()
                    parts.append(rewritten)
            elif ev.event == "status" and isinstance(ev.data, dict):
                step = str(ev.data.get("step") or "")
                if step == "intent_analyzed":
                    status_intent_snapshot = dict(ev.data)
                elif step == "task_framed":
                    status_task_framing_snapshot = dict(ev.data)
                elif step == "context_loaded":
                    status_context_snapshot = dict(ev.data)
                elif step == "collab_done":
                    status_collab_snapshot = dict(ev.data)
            elif ev.event == "collab_done" and isinstance(ev.data, dict):
                status_collab_snapshot = dict(ev.data)
            elif ev.event == "done":
                done_data = ev.data if isinstance(ev.data, dict) else {}
                metadata = _merge_status_snapshots(done_data)

    try:
        await asyncio.wait_for(_collect_stream(), timeout=float(timeout_seconds))
    except asyncio.TimeoutError:
        partial_reply = "".join(parts).strip()
        degraded_reply = partial_reply if len(partial_reply) >= 80 else _build_chat_timeout_fallback_reply(message, response_mode)
        metadata = _merge_status_snapshots(metadata if isinstance(metadata, dict) else {})
        metadata.update(
            {
                "degraded": True,
                "degraded_reason": "chat_timeout",
                "timeout_seconds": int(timeout_seconds),
                "partial_reply_used": bool(len(partial_reply) >= 80),
            }
        )
        
        skills_for_reply = metadata.get("skills_used") if isinstance(metadata, dict) else []
        degraded_reply = _inject_tool_evidence_disclosure(
            degraded_reply,
            skills_for_reply if isinstance(skills_for_reply, list) else [],
        )
        degraded_reply = _inject_attachment_vision_fallback_notice(
            degraded_reply,
            attachments or [],
        )
        degraded_reply = _inject_constraint_capacity_sla_guard(
            degraded_reply,
            message,
            response_mode,
        )
        return {
            "reply": degraded_reply,
            "role": str(metadata.get("role") or role or "ops"),
            "metadata": metadata,
        }

    final_reply = "".join(parts)
    skills_for_reply = metadata.get("skills_used") if isinstance(metadata, dict) else []
    final_reply = _inject_tool_evidence_disclosure(
        final_reply,
        skills_for_reply if isinstance(skills_for_reply, list) else [],
    )
    final_reply = _inject_attachment_vision_fallback_notice(
        final_reply,
        attachments or [],
    )
    final_reply = _inject_constraint_capacity_sla_guard(
        final_reply,
        message,
        response_mode,
    )
    return {"reply": final_reply, "role": metadata.get("role", "ops"), "metadata": metadata}

















