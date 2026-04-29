"""
系统提示词构建器 — 将角色身份、认知风格、特性、知识规则、记忆和信任等级
组装成 ≤ 2500 字符的系统提示词。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.config import CONFIG_DIR, MAX_PROMPT_CHARS

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# 认知风格（8个角色 × 8种风格）
# ═══════════════════════════════════════════════════════════════════════════

COGNITIVE_STYLES: Dict[str, str] = {
    "ops":         "执行型 — 拆解→盘点→规划→止损→监控",
    "data":        "证据型 — 怀疑→验证→排除替代→结论",
    "service":     "共情型 — 情绪→共情→分类→SOP→执行→满意度",
    "design":      "视觉型 — 理解→参考→视觉策略→规格→验收",
    "accounting":  "审慎型 — 采集→核对→标记→确认→结论",
    "engineering": "系统型 — 需求→评估→风险→方案→回滚→实施",
    "web":         "优化型 — 诊断→定位→方案→预估→验证",
    "creative":    "发散型 — 趋势→联想→碰撞→过滤→呈现",
}

# ═══════════════════════════════════════════════════════════════════════════
# 加载配置文件（懒加载单例）
# ═══════════════════════════════════════════════════════════════════════════

_role_presets: Optional[Dict[str, Any]] = None
_knowledge_rules: Optional[List[Dict[str, Any]]] = None
_execution_mandates: Optional[Dict[str, str]] = None


def _load_role_presets() -> Dict[str, Any]:
    """加载 role_presets.json，按 role 字段建索引。"""
    global _role_presets
    if _role_presets is not None:
        return _role_presets

    path = CONFIG_DIR / "role_presets.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            presets = json.load(f)
        _role_presets = {}
        for p in presets:
            role_key = p.get("role", "")
            if role_key:
                _role_presets[role_key] = p
    except Exception as e:
        logger.warning("Failed to load role_presets.json: %s", e)
        _role_presets = {}
    return _role_presets


def _load_knowledge_rules() -> List[Dict[str, Any]]:
    """加载 knowledge_rules.json。"""
    global _knowledge_rules
    if _knowledge_rules is not None:
        return _knowledge_rules

    path = CONFIG_DIR / "knowledge_rules.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            _knowledge_rules = json.load(f)
    except Exception as e:
        logger.warning("Failed to load knowledge_rules.json: %s", e)
        _knowledge_rules = []
    return _knowledge_rules



def _load_execution_mandates() -> Dict[str, str]:
    """加载 execution_mandates.json（角色执行规则，支持配置热更新）。"""
    global _execution_mandates
    if _execution_mandates is not None:
        return _execution_mandates

    path = CONFIG_DIR / "execution_mandates.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        if isinstance(payload, dict):
            normalized: Dict[str, str] = {}
            for key, value in payload.items():
                role_key = str(key or "").strip().lower()
                mandate = str(value or "").strip()
                if role_key and mandate:
                    normalized[role_key] = mandate
            _execution_mandates = normalized
        else:
            _execution_mandates = {}
    except Exception as e:
        logger.warning("Failed to load execution_mandates.json: %s", e)
        _execution_mandates = {}
    return _execution_mandates

def _runtime_role_profile(role: str) -> Dict[str, Any]:
    role_key = str(role or "").strip().lower()
    profile: Dict[str, Any] = {
        "display_name": role_key,
        "domains": [],
        "keywords": [],
        "preferred_actions": [],
        "prompt_hints": [],
        "skill_preferences": [],
        "execution_mandate": "",
    }
    if not role_key:
        return profile

    try:
        from src.core.role_router import build_runtime_role_context, load_role_catalog

        ctx = build_runtime_role_context()
        label_map = ctx.get("label_map") if isinstance(ctx.get("label_map"), dict) else {}
        display = str(label_map.get(role_key) or "").strip()
        if display:
            profile["display_name"] = display

        def _merge_unique(target: List[str], source: Any, limit: int = 12) -> None:
            if not isinstance(source, list):
                return
            for item in source:
                token = str(item or "").strip()
                if not token or token in target:
                    continue
                target.append(token)
                if len(target) >= limit:
                    return

        for item in load_role_catalog():
            if str(item.get("runtime_role") or "").strip().lower() != role_key:
                continue

            role_name = str(item.get("name") or "").strip()
            if role_name and profile["display_name"] == role_key:
                profile["display_name"] = role_name

            _merge_unique(profile["domains"], item.get("domains"))
            _merge_unique(profile["keywords"], item.get("keywords"), limit=16)
            _merge_unique(profile["preferred_actions"], item.get("preferred_actions"), limit=6)
            _merge_unique(profile["prompt_hints"], item.get("prompt_hints"), limit=8)
            _merge_unique(profile["skill_preferences"], item.get("skill_preferences"), limit=8)

            mandate = str(item.get("execution_mandate") or "").strip()
            if mandate and not str(profile.get("execution_mandate") or "").strip():
                profile["execution_mandate"] = mandate
    except Exception:
        pass

    return profile


# ═══════════════════════════════════════════════════════════════════════════
# 提示词组装
# ═══════════════════════════════════════════════════════════════════════════

def build_system_prompt(
    role: str,
    message: str,
    memories: Optional[List[Dict[str, Any]]] = None,
    platform: str = "",
    product_context: str = "",
    trust_level: str = "",
    features: Optional[List[str]] = None,
    metrics_context: str = "",
    cross_role_context: str = "",
    realtime_context: str = "",
    domain_context: str = "",
) -> str:
    """
    构建系统提示词，总长度控制在 MAX_PROMPT_CHARS 以内。

    Parameters
    ----------
    role : str
        当前角色。
    message : str
        用户消息（用于上下文感知裁剪）。
    memories : list | None
        从 DB 加载的学习记忆条目。
    platform : str
        检测到的平台（如 taobao, jd）。
    product_context : str
        产品上下文字符串。
    trust_level : str
        当前信任等级（HIGH / MODERATE / LOW / NONE）。
    features : list[str] | None
        由 feature_budget 选出的特性名列表。

    Returns
    -------
    str
        组装后的系统提示词。
    """
    parts: List[str] = []
    role_key = str(role or "").strip().lower() or "ops"

    presets = _load_role_presets()
    preset = presets.get(role_key, {}) if isinstance(presets, dict) else {}
    runtime_profile = _runtime_role_profile(role_key)

    # ── 0. English header (required for gpt-oss/thinking models) ──
    # Some LLMs (e.g. gpt-oss:20b) misinterpret Chinese-only system prompts.
    # This English preamble ensures the model understands its role and language requirement.
    identity = preset.get("identity", {}) if isinstance(preset.get("identity", {}), dict) else {}
    display = str(preset.get("display_name") or runtime_profile.get("display_name") or role_key)
    archetype = str(identity.get("archetype") or ("通用岗位Agent" if not preset else ""))
    vibe = str(identity.get("vibe") or ("保持专业、结构化、可执行。" if not preset else ""))
    experience = str(identity.get("experience_domain") or "")
    if not experience:
        domains = runtime_profile.get("domains") if isinstance(runtime_profile.get("domains"), list) else []
        if domains:
            experience = " / ".join([str(x) for x in domains[:3] if str(x).strip()])

    domain_hint = "cross-industry operations" if not preset else "Chinese e-commerce operations"

    parts.append(
        f"You are {display} ({archetype}), a specialized AI assistant for {domain_hint}. "
        f"CRITICAL: Always respond in Chinese (中文). Never respond in English. "
        f"Your expertise covers: {experience or role}. "
        f"IMPORTANT: Continuity — use ALL context from previous messages. "
        f"NEVER fabricate specific numbers, metrics, or data the user has not provided."
    )

    # ── 1. 角色身份 ──
    parts.append(f"你是{display}（{archetype}）。{vibe}")
    if experience:
        parts.append(f"专业领域：{experience}")

    # ── 2. 认知风格 ──
    cog = COGNITIVE_STYLES.get(role_key, "")
    if not cog:
        preferred_actions = runtime_profile.get("preferred_actions") if isinstance(runtime_profile.get("preferred_actions"), list) else []
        if preferred_actions:
            cog = "自适应型 — " + "→".join([str(x) for x in preferred_actions[:4] if str(x).strip()])
        else:
            cog = "自适应型 — 理解目标→拆解任务→调用能力→验证结果"
    if cog:
        parts.append(f"思维模式：{cog}")

    # ── 3. 沟通风格 ──
    comm = preset.get("communication_style", {})
    if comm:
        tone = comm.get("tone", "")
        fmt = comm.get("response_format", "")
        if tone:
            parts.append(f"语气：{tone}")
        if fmt:
            parts.append(f"输出格式：{fmt}")

    # ── 4. 关键规则（最多取5条以节省空间）──
    rules = preset.get("critical_rules", [])
    if rules:
        selected_rules = rules[:5]
        rules_text = "关键规则：" + "；".join(selected_rules)
        parts.append(rules_text)

    # ── 5. 特性注入（最多3个）──
    if features:
        from src.core.feature_budget import get_feature_description
        feat_descs = []
        for fname in features[:3]:
            desc = get_feature_description(fname)
            if desc:
                feat_descs.append(desc)
        if feat_descs:
            parts.append("激活特性：" + "｜".join(feat_descs))

    # ── 6. 知识规则（匹配角色的，最多3条）──
    knowledge = _load_knowledge_rules()
    role_rules = [
        r.get("content", r.get("rule", ""))
        for r in knowledge
        if role_key in r.get("roles", []) or r.get("role", "") == role_key
    ][:3]
    if role_rules:
        parts.append("知识规则：" + "；".join(role_rules))
    else:
        profile_keywords = runtime_profile.get("keywords") if isinstance(runtime_profile.get("keywords"), list) else []
        if profile_keywords:
            parts.append("岗位关键词：" + "、".join([str(x) for x in profile_keywords[:8] if str(x).strip()]))

    prompt_hints = runtime_profile.get("prompt_hints") if isinstance(runtime_profile.get("prompt_hints"), list) else []
    if prompt_hints:
        parts.append("岗位提示：" + "；".join([str(x) for x in prompt_hints[:4] if str(x).strip()]))

    skill_preferences = runtime_profile.get("skill_preferences") if isinstance(runtime_profile.get("skill_preferences"), list) else []
    if skill_preferences:
        parts.append("优先技能：" + "、".join([str(x) for x in skill_preferences[:6] if str(x).strip()]))

    # ── 7. 平台上下文 ──
    if platform and platform != "general":
        parts.append(f"当前平台：{platform}")

    # 兼容保护：电商主场景保持历史提示词，不额外注入领域文本。
    if domain_context and domain_context not in {"domain.ecommerce", "domain.general"}:
        parts.append(f"当前业务领域：{domain_context}")

    # ── 7.5. 真实店铺指标数据 ──
    if metrics_context:
        parts.append(metrics_context)
        parts.append(
            "IMPORTANT: 上方是用户店铺的真实数据，分析时必须引用这些具体数字，"
            "禁止编造或替换为其他数值。如需更细粒度数据，请调用 query_store_metrics 工具。"
        )

    # ── 8. 产品上下文 ──
    if product_context:
        parts.append(f"产品信息：{product_context}")

    # ── 9. 信任等级 ──
    if trust_level:
        trust_hints = {
            "HIGH": "信任等级HIGH：可自主决策，减少确认步骤。",
            "MODERATE": "信任等级MODERATE：正常流程，关键决策需确认。",
            "LOW": "信任等级LOW：谨慎输出，多提供依据和替代方案。",
            "NONE": "信任等级NONE：仅提供信息，所有建议需用户确认。",
        }
        hint = trust_hints.get(trust_level, "")
        if hint:
            parts.append(hint)

    # ── 10. 学习记忆（分类注入，最多5条）──
    if memories:
        success_mems = [m for m in memories if m.get("category") == "success"]
        warn_mems = [m for m in memories if m.get("category") in ("blindspot", "improvement")]
        mem_parts = []
        if success_mems:
            success_texts = [m["content"] for m in success_mems[:3] if m.get("content")]
            if success_texts:
                mem_parts.append("✓ 有效模式：" + "；".join(success_texts))
        if warn_mems:
            warn_texts = [m["content"] for m in warn_mems[:2] if m.get("content")]
            if warn_texts:
                mem_parts.append("⚠ 注意事项：" + "；".join(warn_texts))
        if mem_parts:
            parts.append("历史经验（请参考）：\n" + "\n".join(mem_parts))

    # ── 11. ★ 智能注入：行为引导 + 子专业 + 认知风格 + 平台知识 ──
    try:
        from src.core.prompt_injectors import build_intelligence_prompt_section
        intel_section = build_intelligence_prompt_section(
            role=role_key, message=message, platform=platform or "general",
        )
        if intel_section:
            parts.append(intel_section)
    except Exception as e:
        logger.warning("Failed to build intelligence section: %s", e)

    # ── 11.4. ★ 实时搜索情报注入（Pipeline主动搜索结果）────────────────────
    # 这是 Pipeline 在本次 LLM 调用前自动执行的真实网络搜索，数据比训练数据新
    if realtime_context:
        parts.append(
            "【★ 实时市场情报（Pipeline已为你搜索，优先级高于训练数据）】\n"
            + realtime_context
            + "\n注：以上数据来自实时网络搜索，时效性强。分析时请优先引用以上数据中的具体数字和结论，"
            "如与你的训练数据冲突，以上述实时数据为准，并注明「据实时数据」。"
        )

    # ── 11.5. ★ 智能执行内核指令 ──────────────────────────────────────────
    # 核心原则：调用工具真实执行，而非描述执行路径
    parts.append(
        "【执行原则·不可违反】\n"
        "你是AI执行内核，不是流程顾问。用户要求完成任务时，你必须直接调用工具产出结果，"
        "而不是描述「你应该这样做」的步骤框架。\n"
        "• 要内容 → 立即调用生成技能，输出真实文字内容，不给「建议写法」\n"
        "• 要数据分析 → 先调用数据工具获取真实指标，再分析，不凭空描述\n"
        "• 要财务计算 → 调用计算工具给出精确数字，不用「大约/可能」模糊替代\n"
        "• 要竞品/市场/行情/同行 → 立即调用 search_competitor/search_market_info/search_trends，"
        "禁止凭记忆列举竞品，搜索结果才是真实数据\n"
        "• 用户说「帮我搜」「查一下」「找竞品」→ 必须调用搜索工具，不问用户要数据\n"
        "• 可多轮调用：先调工具A获数据 → 以A结果作为B的输入 → 层层深入直到完成任务\n"
        "• 可跨角色调用：运营+财务+数据工具可同时使用，相互验证\n"
        "• 不确定从哪开始时：调用 coordination_skill_chain_planner 获取建议路径（仅供参考）"
    )

    # ── 11.6. ★ 角色专属执行强制规则（配置驱动）──────────────────────────────
    execution_mandates = _load_execution_mandates()
    manifest_mandate = str(runtime_profile.get("execution_mandate") or "").strip()
    mandate = manifest_mandate or execution_mandates.get(role_key, "")
    if not mandate and not preset:
        preferred_actions = runtime_profile.get("preferred_actions") if isinstance(runtime_profile.get("preferred_actions"), list) else []
        action_text = "、".join([str(x) for x in preferred_actions[:4] if str(x).strip()]) or "query、create、plan、execute"
        keyword_text = "、".join([str(x) for x in (runtime_profile.get("keywords") or [])[:6] if str(x).strip()])
        mandate = (
            "【通用执行规则】优先调用与当前岗位相关的技能直接完成任务，"
            "不要只给原则建议。"
            f"建议动作：{action_text}。"
        )
        if keyword_text:
            mandate += f"关注关键词：{keyword_text}。"
    if mandate:
        parts.append(mandate)

    # ── 12. ★ 因果推理框架（仅 data 角色 + 异常类问题）──
    if role_key == "data":
        try:
            from src.core.causal_reasoning import build_multi_causal_prompt
            causal_section = build_multi_causal_prompt(message)
            if causal_section:
                parts.append(causal_section)
        except Exception as e:
            logger.warning("Failed to build causal reasoning: %s", e)

    # ── 13. 跨角色情报（其他角色发现的关键信息）──
    if cross_role_context:
        parts.append(f"【跨角色情报·供参考】\n{cross_role_context}")

    # ── 组装并截断 ──
    prompt = "\n".join(parts)
    if len(prompt) > MAX_PROMPT_CHARS:
        prompt = prompt[:MAX_PROMPT_CHARS - 3] + "..."

    return prompt


def build_quick_reply_prompt() -> str:
    """
    构建快速回复场景的极简系统提示词（打招呼、状态查询等）。

    Returns
    -------
    str
        快速回复专用的系统提示词。
    """
    return (
        "You are a multi-role AI assistant for Chinese e-commerce. "
        "CRITICAL: Always respond in Chinese (中文). Never respond in English.\n"
        "你是一个电商多职能AI助手。用户发来了简短的问候或状态查询。"
        "请用简洁友好的中文回复，不超过3句话。"
        "如果用户问你能做什么，简述你拥有运营、数据、客服、设计、财务、技术、建站、创意8个专业角色。"
    )
