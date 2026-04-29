"""
多Agent协作调度器 — 主Agent回复后，支持Agent自动补充分析。

核心设计:
1. 主Agent回复完成后，检查 intent.tier == TIER_MULTI
2. 并发调度 support_roles，每个用精简 prompt + 主Agent回复上下文
3. 每个支持Agent也经过完整管道: prompt构建 + tool_use
4. 合并各Agent贡献为结构化的协作结果
5. 整个过程通过 SSE 事件流实时推送给前端
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional

logger = logging.getLogger(__name__)


def _smart_truncate_reply(reply: str, max_chars: int = 2000) -> str:
    """
    智能截断主Agent回复：保留开头上下文 + 结构化关键内容 + 结尾结论。
    比硬截断 [:1500] 更能保留语义完整性。
    """
    if len(reply) <= max_chars:
        return reply

    head_size = min(600, max_chars // 3)
    tail_size = min(350, max_chars // 6)
    middle_budget = max_chars - head_size - tail_size - 30  # 30 for "..." separators

    head = reply[:head_size]
    tail = reply[-tail_size:]
    middle_raw = reply[head_size:-tail_size]

    # 提取结构化行：数字列表、标题、含数据/百分比的行、短摘要行
    structured: List[str] = []
    for line in middle_raw.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if (
            re.match(r"^[一二三四五六七八九十\d]+[、.。:：]", stripped)
            or re.match(r"^[#*\-►▶→]", stripped)
            or "¥" in stripped
            or "%" in stripped
            or re.search(r"\d+[\d,.]*\s*[万千百元]", stripped)
            or len(stripped) <= 50  # 短行通常是摘要/标题
        ):
            structured.append(stripped)

    middle_text = "\n".join(structured)
    if len(middle_text) > middle_budget:
        middle_text = middle_text[:middle_budget]

    return f"{head}\n...\n{middle_text}\n...\n{tail}"


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



_SUPPORT_AGENT_ROLE_TIMEOUT_HARD_CAP_SECONDS = 90
_SUPPORT_AGENT_TOTAL_TIMEOUT_HARD_CAP_SECONDS = 3600
_SUPPORT_AGENT_MAX_ROLE_SAFETY_CAP = 2048
_SUPPORT_AGENT_MAX_TOKENS = 300
_SUPPORT_AGENT_MAX_TOKENS_RICH = 520
_SUPPORT_AGENT_MAX_TOKENS_BALANCED = 440
_SUPPORT_AGENT_MAX_TOKENS_COMPACT = 360
_SUPPORT_AGENT_REPLY_CHAR_LIMIT_DEFAULT = 420


def _resolve_support_agent_reply_budget(total_support_roles: int) -> Dict[str, int]:
    # 按协作规模动态分配单支持角色回复预算。
    try:
        role_count = int(total_support_roles or 1)
    except Exception:
        role_count = 1
    role_count = max(1, role_count)

    if role_count <= 2:
        return {"max_tokens": _SUPPORT_AGENT_MAX_TOKENS_RICH, "char_limit": 760, "min_chars": 340}
    if role_count <= 4:
        return {"max_tokens": _SUPPORT_AGENT_MAX_TOKENS_BALANCED, "char_limit": 620, "min_chars": 280}
    if role_count <= 6:
        return {"max_tokens": _SUPPORT_AGENT_MAX_TOKENS_COMPACT, "char_limit": 520, "min_chars": 220}
    return {"max_tokens": _SUPPORT_AGENT_MAX_TOKENS, "char_limit": 420, "min_chars": 180}


_SUPPORT_AGENT_RESCUE_TIMEOUT_SECONDS = 26

_SUPPORT_ROLE_GUARANTEED_ACTIONS: Dict[str, List[str]] = {
    "ops": [
        "先锁定本轮目标指标与负责人，按日拆解执行节奏。",
        "将主方案动作按优先级排为A/B两档，先跑A档闭环。",
        "为关键动作补充验收口径（完成条件、截止时间、复盘节点）。",
    ],
    "data": [
        "先统一口径并拉齐核心指标（曝光、点击、转化、客单、退款）。",
        "按渠道/活动/人群拆分看板，标记异常波动与阈值。",
        "给出下一轮验证实验（变量、样本量、判定标准、止损线）。",
    ],
    "service": [
        "整理高频咨询与投诉主题，补齐标准话术和升级路径。",
        "对退款/差评场景设置优先级分流，缩短响应时长。",
        "建立售后复盘清单，把问题闭环回传给运营与商品侧。",
    ],
    "design": [
        "先对主视觉做信息层级校准，确保利益点在首屏可见。",
        "输出至少两套A/B版本（标题、卖点、按钮）并明确测试周期。",
        "补充移动端可读性与点击热区检查，减少误触与跳失。",
    ],
    "accounting": [
        "先核对预算消耗与毛利区间，设定日度预警阈值。",
        "按渠道拆分投入产出，优先保留正向ROI动作。",
        "补充现金流与结算节奏检查，避免活动期资金错配。",
    ],
    "engineering": [
        "优先校验关键链路稳定性（埋点、下单、支付、回传）。",
        "给出最小化发布与回滚方案，避免高峰期扩散故障。",
        "为高风险节点补监控与告警，确保异常可定位可恢复。",
    ],
    "web": [
        "先校准核心关键词与页面意图匹配，修正标题与描述。",
        "排查站内搜索与落地页一致性，减少流量损耗。",
        "建立周度排名与点击跟踪表，按波动触发内容迭代。",
    ],
    "creative": [
        "先统一内容主叙事与利益点，避免表达分散。",
        "输出多场景素材脚本（短视频/图文/直播口播）并分发排期。",
        "补充素材淘汰标准，保留高互动版本持续迭代。",
    ],
}

_SUPPORT_ROLE_GUARANTEED_RISKS: Dict[str, List[str]] = {
    "ops": ["避免动作过多导致执行分散，优先保留前两级关键动作。", "若关键指标连续下滑，应立即触发回滚与复盘。"],
    "data": ["避免样本量不足导致误判，先满足最小样本再下结论。", "跨渠道对比前需统一归因窗口与口径。"],
    "service": ["避免口径不一致引发二次投诉，先统一标准话术。", "高风险用户场景需人工复核，避免自动化误判。"],
    "design": ["避免只改样式不改信息层级，优先保障可读与可点击。", "A/B测试期间避免同时改动过多变量。"],
    "accounting": ["避免只看GMV忽略利润，需同步盯毛利与费用率。", "预算调整应与结算周期联动，防止现金流压力。"],
    "engineering": ["避免高峰时段做高风险改动，优先灰度发布。", "关键依赖异常时要有降级与兜底路径。"],
    "web": ["避免关键词堆砌导致质量下降，保持语义自然与相关性。", "排名波动需结合点击与转化综合判断。"],
    "creative": ["避免素材风格与人群错配，先做小样本验证。", "内容节奏过密可能导致疲劳，需控制频次。"],
}


def _compact_text(text: str, limit: int = 72) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    if not cleaned:
        return ""
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(12, limit - 3)] + "..."



def _is_complete_support_reply(reply: str, *, min_chars: int = 140) -> bool:
    text = str(reply or '').strip()
    if not text:
        return False

    minimum = max(80, int(min_chars or 140))
    if len(text) < minimum:
        return False

    structure_hits = sum(
        1
        for token in ("关键判断", "可执行动作", "风险与边界", "与主方案衔接")
        if token in text
    )
    if structure_hits >= 3:
        return True

    # 兼容模型生成的自然段格式，避免把“已完整补位”误记为未完成。
    return len(text) >= max(180, minimum) and ("\n" in text)


def _should_mark_timeout_as_unresolved(*, timed_out: bool, reply: str, min_chars: int = 140) -> bool:
    if not bool(timed_out):
        return False
    return not _is_complete_support_reply(reply, min_chars=min_chars)

def _extract_primary_reference_points(reply: str, limit: int = 2) -> List[str]:
    points: List[str] = []
    seen: set[str] = set()
    for raw_line in re.split(r"[\n。！？]", str(reply or "")):
        line = re.sub(r"^[#*\-\d\s、.。:：]+", "", str(raw_line or "")).strip()
        if len(line) < 8:
            continue
        key = re.sub(r"\s+", "", line).lower()
        if key in seen:
            continue
        seen.add(key)
        points.append(line)
        if len(points) >= max(1, int(limit or 1)):
            break
    return points


def _build_support_role_guaranteed_reply(
    *,
    support_role: str,
    display_name: str,
    message: str,
    primary_reply: str,
    timeout_degraded: bool,
    reason: str = "",
    char_limit: int = _SUPPORT_AGENT_REPLY_CHAR_LIMIT_DEFAULT,
) -> str:
    role_key = str(support_role or "").strip().lower() or "ops"
    actions = list(_SUPPORT_ROLE_GUARANTEED_ACTIONS.get(role_key) or _SUPPORT_ROLE_GUARANTEED_ACTIONS["ops"])
    risks = list(_SUPPORT_ROLE_GUARANTEED_RISKS.get(role_key) or _SUPPORT_ROLE_GUARANTEED_RISKS["ops"])

    user_goal = _compact_text(message, limit=56) or "当前用户任务"
    primary_points = _extract_primary_reference_points(primary_reply, limit=2)
    primary_focus = _compact_text(primary_points[0], limit=52) if primary_points else "先执行主方案中已明确的优先动作"

    status_note = "（主链路超时，已切换保障补位）" if timeout_degraded else "（主链路异常，已切换保障补位）"
    lines: List[str] = [
        f"【{display_name}补位建议{status_note}】",
        f"关键判断：围绕“{user_goal}”，本岗位先保证主方案可落地、可验证。",
        "可执行动作：",
        f"1. {actions[0]}",
        f"2. {actions[1]}",
        f"3. {actions[2]}",
        "风险与边界：",
        f"- {risks[0]}",
        f"- {risks[1]}",
        f"与主方案衔接：先推进“{primary_focus}”，随后由{display_name}补齐本岗位验证闭环。",
    ]

    text = "\n".join(lines).strip()
    max_chars = max(220, int(char_limit or _SUPPORT_AGENT_REPLY_CHAR_LIMIT_DEFAULT))
    if len(text) > max_chars:
        text = _smart_truncate_reply(text, max_chars=max_chars)
    return text


async def _try_support_role_rescue_stream(
    *,
    support_role: str,
    display_name: str,
    message: str,
    primary_reply: str,
    timeout_seconds: float,
    char_limit: int,
) -> str:
    try:
        from src.llm_client import call_llm_stream
    except Exception:
        return ""

    safe_timeout = max(4.0, min(float(timeout_seconds or 0.0), float(_SUPPORT_AGENT_RESCUE_TIMEOUT_SECONDS)))
    if safe_timeout < 4.0:
        return ""

    primary_points = _extract_primary_reference_points(primary_reply, limit=2)
    primary_focus = "；".join(primary_points) if primary_points else "沿用主方案优先动作"

    system_prompt = (
        f"你是{display_name}。在主方案基础上做岗位补位，禁止重复主方案，必须给可执行内容。"
        "输出结构固定为：关键判断、可执行动作(3条)、风险与边界(2条)、与主方案衔接。"
    )
    user_prompt = (
        f"用户问题：{message}\n"
        f"主方案摘要：{primary_focus}\n"
        "请直接给出补位结果，使用中文，保持简洁可执行。"
    )

    pieces: List[str] = []
    try:
        async with asyncio.timeout(safe_timeout):
            async for ev in call_llm_stream(
                system=system_prompt,
                message=user_prompt,
                tools=None,
                max_tokens=max(180, min(420, int(char_limit * 1.25))),
                temperature=0.2,
                chunk_timeout=max(4.0, min(safe_timeout, 8.0)),
            ):
                etype = str(ev.get("type") or "")
                if etype == "token":
                    token = str(ev.get("text") or "")
                    if token:
                        pieces.append(token)
                        if len("".join(pieces)) >= int(max(260, char_limit * 1.4)):
                            break
                elif etype == "done":
                    break
    except (TimeoutError, asyncio.TimeoutError):
        return ""
    except Exception:
        return ""

    text = "".join(pieces).strip()
    if not text:
        return ""

    if (
        "所有模型均响应超时" in text
        or "大模型调用失败" in text
        or "[LLM未配置]" in text
        or "⚠️" in text
    ):
        return ""

    if len(text) < 80:
        return ""

    max_chars = max(220, int(char_limit or _SUPPORT_AGENT_REPLY_CHAR_LIMIT_DEFAULT))
    if len(text) > max_chars:
        text = _smart_truncate_reply(text, max_chars=max_chars)
    return text


async def _generate_support_role_guaranteed_reply(
    *,
    support_role: str,
    display_name: str,
    message: str,
    primary_reply: str,
    reason: str,
    timeout_degraded: bool,
    char_limit: int,
    prefer_model: bool,
    deadline_monotonic: Optional[float] = None,
) -> str:
    if prefer_model:
        rescue_timeout = float(_SUPPORT_AGENT_RESCUE_TIMEOUT_SECONDS)
        if deadline_monotonic is not None:
            remaining = float(deadline_monotonic - time.monotonic())
            rescue_timeout = max(4.0, min(float(_SUPPORT_AGENT_RESCUE_TIMEOUT_SECONDS), remaining + 6.0))

        rescue_text = await _try_support_role_rescue_stream(
            support_role=support_role,
            display_name=display_name,
            message=message,
            primary_reply=primary_reply,
            timeout_seconds=rescue_timeout,
            char_limit=char_limit,
        )
        if rescue_text:
            normalized = str(rescue_text or "").strip()
            if "补位建议" not in normalized:
                status_note = "（主链路超时，已切换保障补位）" if timeout_degraded else "（主链路异常，已切换保障补位）"
                normalized = f"【{display_name}补位建议{status_note}】\n{normalized}"
            max_chars = max(220, int(char_limit or _SUPPORT_AGENT_REPLY_CHAR_LIMIT_DEFAULT))
            if len(normalized) > max_chars:
                normalized = _smart_truncate_reply(normalized, max_chars=max_chars)
            return normalized

    return _build_support_role_guaranteed_reply(
        support_role=support_role,
        display_name=display_name,
        message=message,
        primary_reply=primary_reply,
        timeout_degraded=timeout_degraded,
        reason=reason,
        char_limit=char_limit,
    )


@dataclass
class AgentContribution:
    """单个支持Agent的贡献."""
    role: str
    display_name: str
    reply: str
    skills_used: List[str] = field(default_factory=list)
    elapsed_ms: int = 0


async def dispatch_support_agents(
    message: str,
    primary_role: str,
    primary_reply: str,
    support_roles: List[str],
    user_id: int,
    *,
    product_id: Optional[int] = None,
    platform: str = "general",
    action: str = "",
    domain_id: str = "domain.general",
    max_roles_override: Optional[int] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    调度支持Agent补充分析。

    Yields SSE-compatible event dicts:
      - {"event": "collab_start", "agents": [...]} 
      - {"event": "collab_agent_start", "role": ..., "name": ...}
      - {"event": "collab_token", "role": ..., "text": ...}
      - {"event": "collab_tool_call", "role": ..., "name": ..., "args": ...}
      - {"event": "collab_tool_result", "role": ..., "name": ..., "result": ...}
      - {"event": "collab_agent_done", "role": ..., "name": ..., "reply": ..., "elapsed_ms": ...}
      - {"event": "collab_done", "contributions": [...]} 
    """
    from src.config import (
        SUPPORT_AGENT_MAX_CONCURRENCY,
        SUPPORT_AGENT_MAX_ROLES,
        SUPPORT_AGENT_TIMEOUT_SECONDS,
        SUPPORT_AGENT_TOTAL_TIMEOUT_SECONDS,
    )

    if not support_roles:
        return

    requested_support_roles = list(support_roles)
    configured_limit = max(1, int(SUPPORT_AGENT_MAX_ROLES or 32))
    if max_roles_override is not None:
        try:
            raw_limit = int(max_roles_override)
        except Exception:
            raw_limit = configured_limit
    else:
        raw_limit = configured_limit

    max_support_roles = max(1, min(raw_limit, int(_SUPPORT_AGENT_MAX_ROLE_SAFETY_CAP)))
    effective_support_roles = list(requested_support_roles)[:max_support_roles]

    max_concurrency = max(1, int(SUPPORT_AGENT_MAX_CONCURRENCY or 6))
    max_concurrency = min(max_concurrency, max(1, len(effective_support_roles)))

    role_timeout_seconds = max(8, int(SUPPORT_AGENT_TIMEOUT_SECONDS or 28))
    role_timeout_seconds = min(role_timeout_seconds, int(_SUPPORT_AGENT_ROLE_TIMEOUT_HARD_CAP_SECONDS))
    if len(effective_support_roles) <= 4:
        # 小规模协作优先保证每个岗位给出完整结论，避免“没做完”体感。
        role_timeout_seconds = max(role_timeout_seconds, 50)
    elif len(effective_support_roles) <= 6:
        role_timeout_seconds = max(role_timeout_seconds, 44)
    else:
        # 大规模协作场景也给每个岗位更多窗口，减少“已选岗位未产出”的错觉。
        role_timeout_seconds = max(role_timeout_seconds, 36)

    wave_count = max(1, (len(effective_support_roles) + max_concurrency - 1) // max_concurrency)
    default_total_budget = role_timeout_seconds * wave_count
    configured_total_timeout = int(SUPPORT_AGENT_TOTAL_TIMEOUT_SECONDS or default_total_budget)
    total_timeout_seconds = max(role_timeout_seconds, configured_total_timeout, default_total_budget)
    if len(effective_support_roles) >= 7:
        total_timeout_seconds = max(total_timeout_seconds, role_timeout_seconds * wave_count + 18)
    total_timeout_seconds = min(total_timeout_seconds, int(_SUPPORT_AGENT_TOTAL_TIMEOUT_HARD_CAP_SECONDS))

    collab_started_at = time.monotonic()
    collab_deadline = collab_started_at + float(total_timeout_seconds)
    worker_semaphore = asyncio.Semaphore(max_concurrency)

    agents_info = [
        {"role": r, "name": _role_display_name(r)}
        for r in effective_support_roles
    ]
    yield {
        "event": "collab_start",
        "agents": agents_info,
        "primary_role": primary_role,
        "max_roles": int(max_support_roles),
        "max_concurrency": int(max_concurrency),
        "total_timeout_seconds": int(total_timeout_seconds),
        "input_requested_roles": int(len(requested_support_roles)),
        "input_requested_role_list": list(requested_support_roles),
        "scheduled_roles": int(len(effective_support_roles)),
        "scheduled_role_list": list(effective_support_roles),
        "truncated_by_budget": bool(len(requested_support_roles) > len(effective_support_roles)),
    }

    contributions_by_role: Dict[str, Dict[str, Any]] = {}
    timed_out_roles_set: set[str] = set()
    degraded_roles_set: set[str] = set()
    event_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()

    for role in effective_support_roles:
        display_name = _role_display_name(role)
        yield {"event": "collab_agent_start", "role": role, "name": display_name}

    async def _run_role_worker(role: str) -> None:
        display_name = _role_display_name(role)
        role_budget = _resolve_support_agent_reply_budget(len(effective_support_roles))
        role_char_limit = max(220, int(role_budget.get("char_limit") or _SUPPORT_AGENT_REPLY_CHAR_LIMIT_DEFAULT))

        async with worker_semaphore:
            start = time.monotonic()
            if start >= collab_deadline:
                elapsed = int((time.monotonic() - start) * 1000)
                reply = await _generate_support_role_guaranteed_reply(
                    support_role=role,
                    display_name=display_name,
                    message=message,
                    primary_reply=primary_reply,
                    reason="collab_deadline_exceeded",
                    timeout_degraded=True,
                    char_limit=role_char_limit,
                    prefer_model=False,
                )
                unresolved_timeout = _should_mark_timeout_as_unresolved(
                    timed_out=True,
                    reply=reply,
                    min_chars=max(120, int(role_budget.get("min_chars") or 180)),
                )
                await event_queue.put(
                    {
                        "kind": "done",
                        "role": role,
                        "name": display_name,
                        "reply": reply,
                        "skills_used": [],
                        "elapsed_ms": elapsed,
                        "timed_out": unresolved_timeout,
                        "degraded": True,
                    }
                )
                return

            per_role_deadline = min(collab_deadline, start + float(role_timeout_seconds))
            reply_parts: List[str] = []
            skills_used: List[str] = []
            timed_out = False

            try:
                async for chunk in _run_support_agent_stream(
                    message=message,
                    primary_role=primary_role,
                    primary_reply=primary_reply,
                    support_role=role,
                    user_id=user_id,
                    product_id=product_id,
                    platform=platform,
                    action=action,
                    domain_id=domain_id,
                    deadline_monotonic=per_role_deadline,
                    support_role_count=len(effective_support_roles),
                ):
                    chunk_type = str(chunk.get("type") or "")
                    if chunk_type == "token":
                        token_text = str(chunk.get("text") or "")
                        if token_text:
                            reply_parts.append(token_text)
                            await event_queue.put(
                                {
                                    "kind": "token",
                                    "role": role,
                                    "name": display_name,
                                    "text": token_text,
                                }
                            )
                    elif chunk_type == "skill":
                        skill_name = str(chunk.get("name") or "").strip()
                        if skill_name:
                            skills_used.append(skill_name)
                    elif chunk_type == "timeout":
                        timed_out = True
            except Exception as e:
                logger.warning("Support agent %s failed: %s", role, e)
                elapsed = int((time.monotonic() - start) * 1000)
                reply = await _generate_support_role_guaranteed_reply(
                    support_role=role,
                    display_name=display_name,
                    message=message,
                    primary_reply=primary_reply,
                    reason=f"support_stream_error:{type(e).__name__}",
                    timeout_degraded=bool(timed_out),
                    char_limit=role_char_limit,
                    prefer_model=True,
                    deadline_monotonic=collab_deadline,
                )
                unresolved_timeout = _should_mark_timeout_as_unresolved(
                    timed_out=bool(timed_out),
                    reply=reply,
                    min_chars=max(120, int(role_budget.get("min_chars") or 180)),
                )
                await event_queue.put(
                    {
                        "kind": "done",
                        "role": role,
                        "name": display_name,
                        "reply": reply,
                        "skills_used": [],
                        "elapsed_ms": elapsed,
                        "timed_out": unresolved_timeout,
                        "degraded": bool(timed_out),
                    }
                )
                return

            reply = "".join(reply_parts).strip()
            min_reply_chars = max(120, int(role_budget.get("min_chars") or 180))
            if not reply:
                timed_out = bool(timed_out or time.monotonic() >= per_role_deadline)
                reply = await _generate_support_role_guaranteed_reply(
                    support_role=role,
                    display_name=display_name,
                    message=message,
                    primary_reply=primary_reply,
                    reason="support_empty_or_timeout",
                    timeout_degraded=bool(timed_out),
                    char_limit=role_char_limit,
                    prefer_model=True,
                    deadline_monotonic=collab_deadline,
                )
            elif timed_out and len(reply) < min_reply_chars:
                repaired_reply = await _generate_support_role_guaranteed_reply(
                    support_role=role,
                    display_name=display_name,
                    message=message,
                    primary_reply=primary_reply,
                    reason="support_partial_timeout",
                    timeout_degraded=True,
                    char_limit=role_char_limit,
                    prefer_model=True,
                    deadline_monotonic=collab_deadline,
                )
                if repaired_reply:
                    reply = repaired_reply

            elapsed = int((time.monotonic() - start) * 1000)

            unresolved_timeout = _should_mark_timeout_as_unresolved(
                timed_out=bool(timed_out),
                reply=reply,
                min_chars=max(120, min_reply_chars // 2),
            )

            await event_queue.put(
                {
                    "kind": "done",
                    "role": role,
                    "name": display_name,
                    "reply": reply,
                    "skills_used": skills_used,
                    "elapsed_ms": elapsed,
                    "timed_out": unresolved_timeout,
                    "degraded": bool(timed_out),
                }
            )

    worker_tasks: List[asyncio.Task] = [
        asyncio.create_task(_run_role_worker(role), name=f"support_agent_{idx}_{role}")
        for idx, role in enumerate(effective_support_roles)
    ]

    done_roles: set[str] = set()

    try:
        while len(done_roles) < len(effective_support_roles):
            remaining = float(collab_deadline - time.monotonic())
            if remaining <= 0:
                break
            try:
                item = await asyncio.wait_for(event_queue.get(), timeout=remaining)
            except (TimeoutError, asyncio.TimeoutError):
                break

            kind = str(item.get("kind") or "")
            if kind == "token":
                yield {
                    "event": "collab_token",
                    "role": item.get("role"),
                    "name": item.get("name"),
                    "text": item.get("text", ""),
                }
                continue

            if kind != "done":
                continue

            role = str(item.get("role") or "").strip().lower()
            if not role or role in done_roles:
                continue

            done_roles.add(role)
            contribution = {
                "role": role,
                "name": str(item.get("name") or _role_display_name(role)),
                "reply": str(item.get("reply") or ""),
                "skills_used": list(item.get("skills_used") or []),
                "elapsed_ms": int(item.get("elapsed_ms") or 0),
            }
            contributions_by_role[role] = contribution
            if bool(item.get("timed_out")):
                timed_out_roles_set.add(role)
            if bool(item.get("degraded")):
                degraded_roles_set.add(role)

            reply_text = contribution["reply"]
            if reply_text:
                asyncio.create_task(_broadcast_key_finding(role, reply_text, user_id))
    finally:
        for task in worker_tasks:
            if not task.done():
                task.cancel()
        if worker_tasks:
            await asyncio.gather(*worker_tasks, return_exceptions=True)

    for role in effective_support_roles:
        if role in done_roles:
            continue
        display_name = _role_display_name(role)
        timeout_reply = _build_support_role_guaranteed_reply(
            support_role=role,
            display_name=display_name,
            message=message,
            primary_reply=primary_reply,
            timeout_degraded=True,
            reason="collab_global_timeout",
            char_limit=max(220, int(_resolve_support_agent_reply_budget(len(effective_support_roles)).get("char_limit") or _SUPPORT_AGENT_REPLY_CHAR_LIMIT_DEFAULT)),
        )
        contributions_by_role[role] = {
            "role": role,
            "name": display_name,
            "reply": timeout_reply,
            "skills_used": [],
            "elapsed_ms": int((time.monotonic() - collab_started_at) * 1000),
        }
        degraded_roles_set.add(role)
        if _should_mark_timeout_as_unresolved(timed_out=True, reply=timeout_reply, min_chars=120):
            timed_out_roles_set.add(role)

    ordered_contributions: List[Dict[str, Any]] = []
    for role in effective_support_roles:
        contribution = contributions_by_role.get(role)
        if not contribution:
            continue
        ordered_contributions.append(contribution)
        yield {
            "event": "collab_agent_done",
            "role": contribution["role"],
            "name": contribution["name"],
            "reply": contribution["reply"],
            "skills_used": contribution["skills_used"],
            "elapsed_ms": contribution["elapsed_ms"],
        }

    timed_out_roles = [role for role in effective_support_roles if role in timed_out_roles_set]
    degraded_roles = [role for role in effective_support_roles if role in degraded_roles_set]
    collab_elapsed_ms = int((time.monotonic() - collab_started_at) * 1000)
    yield {
        "event": "collab_done",
        "contributions": ordered_contributions,
        "elapsed_ms": collab_elapsed_ms,
        "timeout_roles": timed_out_roles,
        "timeout_count": len(timed_out_roles),
        "degraded_roles": degraded_roles,
        "degraded_count": len(degraded_roles),
        "requested_roles": len(effective_support_roles),
        "completed_roles": len(ordered_contributions),
        "input_requested_roles": int(len(requested_support_roles)),
        "input_requested_role_list": list(requested_support_roles),
        "scheduled_roles": int(len(effective_support_roles)),
        "scheduled_role_list": list(effective_support_roles),
        "truncated_by_budget": bool(len(requested_support_roles) > len(effective_support_roles)),
    }


async def _broadcast_key_finding(from_role: str, reply: str, user_id: int) -> None:
    """
    从支持Agent的回复中提炼关键发现，写入 role_shared_context 供其他角色参考。

    广播规则：
      accounting → ops（毛利/净利/超支预警）
      data       → ops + service（异常/趋势）
      service    → ops + design（投诉主题/NPS）
    """
    _BROADCAST_MAP: Dict[str, List[str]] = {
        "accounting": ["ops"],
        "data": ["ops", "service"],
        "service": ["ops", "design"],
    }
    to_roles = _BROADCAST_MAP.get(from_role, [])
    if not to_roles or not reply.strip():
        return

    # 提取关键发现（截取最有价值的部分，≤80字）
    finding = _extract_finding(reply, from_role)
    if not finding:
        return

    try:
        from src.database import get_db
        db = await get_db()
        for to_role in to_roles:
            await db.execute(
                """INSERT INTO role_shared_context
                   (workspace_id, from_role, to_role, content, visibility)
                   VALUES (NULL, ?, ?, ?, 'all')""",
                (from_role, to_role, finding),
            )
        await db.commit()
        logger.debug("Broadcast from %s to %s: %s...", from_role, to_roles, finding[:40])
    except Exception as e:
        logger.debug("Broadcast failed: %s", e)


def _extract_finding(reply: str, role: str) -> str:
    """从角色回复中提取一句话关键发现（≤80字）。"""
    # 关键信号词，按角色定制
    _SIGNAL_PATTERNS: Dict[str, List[str]] = {
        "accounting": ["毛利率", "净利率", "亏损", "超支", "利润", "成本占比", "ROI低", "预算"],
        "data": ["异常", "下降", "跌", "转化率", "退款率", "增长", "趋势", "预警"],
        "service": ["投诉", "差评", "NPS", "满意度", "退款", "纠纷", "风险"],
    }
    signals = _SIGNAL_PATTERNS.get(role, [])

    # 找包含信号词的第一句话
    sentences = [s.strip() for s in re.split(r"[。！？\n]", reply) if s.strip()]
    for sentence in sentences:
        if any(sig in sentence for sig in signals):
            # 截取关键句（去掉Markdown符号）
            clean = re.sub(r"[*_#`>]", "", sentence).strip()
            if 5 < len(clean) <= 80:
                return f"[{role}]{clean}"
            elif len(clean) > 80:
                return f"[{role}]{clean[:77]}..."

    return ""


async def _run_support_agent_stream(
    message: str,
    primary_role: str,
    primary_reply: str,
    support_role: str,
    user_id: int,
    *,
    product_id: Optional[int] = None,
    platform: str = "general",
    action: str = "",
    domain_id: str = "domain.general",
    deadline_monotonic: Optional[float] = None,
    support_role_count: int = 1,
) -> AsyncGenerator[Dict[str, Any], None]:
    """
    运行单个支持Agent（流式版本）。

    逐 token yield 事件:
      {"type": "token", "text": "..."}
      {"type": "skill", "name": "..."}
    """
    from src.core.prompt_builder import build_system_prompt
    from src.core.feature_budget import select_features
    from src.llm_client import call_llm_stream
    from src.config import (
        ENABLE_TOOL_USE,
        MAX_TOOL_ROUNDS,
        SUPPORT_AGENT_ENABLE_TOOL_USE,
        SUPPORT_AGENT_MAX_TOOL_ROUNDS,
        SUPPORT_AGENT_TOOL_TIMEOUT_SECONDS,
        SUPPORT_AGENT_PRIMARY_REPLY_MAX_CHARS,
        SUPPORT_AGENT_TOOL_LIMIT,
    )

    features = select_features(message, support_role)[:2]

    from src.core.chat_pipeline import _load_memories, _load_trust, _load_product_ctx, _prefilter_tools

    memories, trust_level, product_ctx = await asyncio.gather(
        _load_memories(support_role, limit=3),
        _load_trust(support_role),
        _load_product_ctx(product_id),
    )

    system_prompt = build_system_prompt(
        role=support_role,
        message=message,
        memories=memories,
        platform=platform,
        product_context=product_ctx,
        trust_level=trust_level,
        features=features,
    )

    primary_name = _role_display_name(primary_role)
    support_name = _role_display_name(support_role)

    reply_budget = _resolve_support_agent_reply_budget(support_role_count)
    reply_token_budget = max(64, int(reply_budget.get("max_tokens") or _SUPPORT_AGENT_MAX_TOKENS))
    reply_char_limit = max(120, int(reply_budget.get("char_limit") or _SUPPORT_AGENT_REPLY_CHAR_LIMIT_DEFAULT))
    reply_char_floor = max(120, int(reply_budget.get("min_chars") or min(240, reply_char_limit // 2)))

    reply_context_chars = min(800, max(500, int(SUPPORT_AGENT_PRIMARY_REPLY_MAX_CHARS or 1200)))
    truncated_reply = _smart_truncate_reply(primary_reply, max_chars=reply_context_chars)
    context_message = (
        f"用户提问: {message}\n\n"
        f"--- {primary_name}已给出以下回复 ---\n"
        f"{truncated_reply}\n"
        f"--- 回复结束 ---\n\n"
        f"请你作为{support_name}，从你的专业角度补充分析。"
        f"不要重复{primary_name}已说的内容，只补充你专业领域的见解、数据或建议。"
        f"至少覆盖3项：关键发现、可执行动作、风险与边界。"
        f"信息充分时不少于{reply_char_floor}字，建议控制在{reply_char_limit}字以内。"
    )

    tools = None
    if ENABLE_TOOL_USE and SUPPORT_AGENT_ENABLE_TOOL_USE:
        try:
            from src.skills.registry import get_registry

            reg = get_registry()
            raw_tools = reg.get_tools_for_role(support_role)
            filtered_tools: List[Dict[str, Any]] = []
            for tool in raw_tools or []:
                fn = tool.get("function") if isinstance(tool, dict) else None
                name = str((fn or {}).get("name") or "")
                if not name:
                    continue
                # 协作补位阶段避免再触发二次协作与联网搜索，控制时延抖动
                if name.startswith("coordination_") or name.startswith("search_"):
                    continue
                filtered_tools.append(tool)

            tool_limit = max(0, int(SUPPORT_AGENT_TOOL_LIMIT or 6))
            if tool_limit > 0 and filtered_tools:
                filtered_tools = _prefilter_tools(
                    filtered_tools,
                    message,
                    max_role_tools=tool_limit,
                    role=support_role,
                    action=str(action or ""),
                    domain_id=str(domain_id or ""),
                    extra_context=truncated_reply,
                )
            elif tool_limit <= 0:
                filtered_tools = []
            tools = filtered_tools or None
        except Exception:
            tools = None

    llm_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": context_message},
    ]

    reply_parts: List[str] = []
    # 支持Agent默认仅允许1轮工具调用，避免协作长尾
    max_tool_rounds = max(0, int(SUPPORT_AGENT_MAX_TOOL_ROUNDS or 1))
    max_rounds = min(int(MAX_TOOL_ROUNDS), max_tool_rounds)
    tool_timeout_seconds = max(3, int(SUPPORT_AGENT_TOOL_TIMEOUT_SECONDS or 10))
    timeout_reached = False

    for _round in range(max_rounds + 1):
        pending_tool_calls: List[Dict[str, Any]] = []
        tools_for_round = tools if (tools and _round < max_rounds) else None

        try:
            if deadline_monotonic is not None:
                remaining = float(deadline_monotonic - time.monotonic())
                if remaining <= 0:
                    timeout_reached = True
                    break
                async with asyncio.timeout(remaining):
                    async for ev in call_llm_stream(
                        system="",
                        message="",
                        history=llm_messages,
                        tools=tools_for_round,
                        max_tokens=reply_token_budget,
                    ):
                        if ev["type"] == "token":
                            reply_parts.append(ev["text"])
                            yield {"type": "token", "text": ev["text"]}
                        elif ev["type"] == "tool_call":
                            pending_tool_calls.append(ev)
                            yield {"type": "skill", "name": ev.get("name", "unknown")}
                        elif ev["type"] == "done":
                            break
            else:
                async for ev in call_llm_stream(
                    system="",
                    message="",
                    history=llm_messages,
                    tools=tools_for_round,
                    max_tokens=reply_token_budget,
                ):
                    if ev["type"] == "token":
                        reply_parts.append(ev["text"])
                        yield {"type": "token", "text": ev["text"]}
                    elif ev["type"] == "tool_call":
                        pending_tool_calls.append(ev)
                        yield {"type": "skill", "name": ev.get("name", "unknown")}
                    elif ev["type"] == "done":
                        break
        except (TimeoutError, asyncio.TimeoutError):
            timeout_reached = True
            break

        if not pending_tool_calls or tools_for_round is None:
            break

        llm_messages.append(
            {
                "role": "assistant",
                "content": "".join(reply_parts),
                "tool_calls": [
                    {
                        "id": tc.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc.get("args", {})),
                        },
                    }
                    for tc in pending_tool_calls
                ],
            }
        )
        reply_parts.clear()

        for tc in pending_tool_calls:
            try:
                from src.skills.registry import get_registry

                reg = get_registry()
                per_tool_timeout = float(tool_timeout_seconds)
                if deadline_monotonic is not None:
                    remaining = float(deadline_monotonic - time.monotonic())
                    if remaining <= 0:
                        raise asyncio.TimeoutError()
                    per_tool_timeout = max(1.0, min(per_tool_timeout, remaining))

                result = await asyncio.wait_for(
                    reg.execute(tc["name"], tc.get("args", {})),
                    timeout=per_tool_timeout,
                )
            except asyncio.TimeoutError:
                timeout_reached = True
                result = {
                    "error": f"support_tool_timeout_{tool_timeout_seconds}s",
                    "message": "支持岗位工具执行超时，已跳过并继续生成。",
                }
            except Exception as e:
                result = {"error": str(e)}

            llm_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                }
            )

        if timeout_reached:
            break

    if timeout_reached and not reply_parts:
        yield {
            "type": "token",
            "text": _build_support_role_guaranteed_reply(
                support_role=support_role,
                display_name=support_name,
                message=message,
                primary_reply=primary_reply,
                timeout_degraded=True,
                reason="support_stream_timeout",
                char_limit=reply_char_limit,
            ),
        }

    if timeout_reached:
        yield {"type": "timeout"}



