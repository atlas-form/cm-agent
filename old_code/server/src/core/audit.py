"""Unified high-risk audit helpers."""

from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src.core.request_context import get_current_request_id, get_current_trace_id
from src.database import get_db


def _safe_json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            return {}
    return {}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except Exception:
        return default
    if math.isnan(result) or math.isinf(result):
        return default
    return result


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    token = str(value or "").strip().lower()
    return token in {"1", "true", "yes", "y", "on"}


def _parse_iso_datetime(value: Any) -> Optional[datetime]:
    token = str(value or "").strip()
    if not token:
        return None
    normalized = token.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _p95(values: list[int]) -> int:
    if not values:
        return 0
    cleaned = sorted(v for v in values if v >= 0)
    if not cleaned:
        return 0
    idx = max(0, min(len(cleaned) - 1, math.ceil(0.95 * len(cleaned)) - 1))
    return int(cleaned[idx])


_COLLAB_DECISION_REASON_ACTIONS: dict[str, str] = {
    "smalltalk_guard": "补充小聊识别词典并加入业务词反例，降低误判业务消息风险。",
    "short_message_guard": "为短消息补任务锚点与上下文补全，必要时先追问再决定协作。",
    "allowed": "回放近窗隐式协作样本，收紧自动触发条件并校准 support_roles 扩张策略。",
    "no_collab_signal": "核查意图分层与岗位信号提取，避免应协作场景漏触发。",
    "role_locked_guard": "评估锁岗策略是否过严，必要时提供受控白名单岗位参与协作。",
    "manual_support_missing": "手动模式下补齐支持岗位选择，避免空协作配置。",
    "mode_single": "单岗位模式默认不协作，若需要多岗位需引导用户切换模式。",
}


def _build_collaboration_reason_playbook(reason_counts: dict[str, int], limit: int = 3) -> list[dict[str, Any]]:
    if not isinstance(reason_counts, dict):
        return []

    safe_limit = max(1, min(int(limit), 10))
    playbook: list[dict[str, Any]] = []
    sorted_items = sorted(reason_counts.items(), key=lambda item: (-_to_int(item[1], 0), str(item[0] or "")))

    for reason_raw, count_raw in sorted_items:
        reason = str(reason_raw or "").strip()
        count = max(0, _to_int(count_raw, 0))
        if not reason or count <= 0:
            continue

        action = _COLLAB_DECISION_REASON_ACTIONS.get(
            reason,
            "按该判定原因回放会话样本，校准协作触发与保护阈值。",
        )
        playbook.append(
            {
                "reason": reason,
                "count": int(count),
                "action": str(action),
            }
        )
        if len(playbook) >= safe_limit:
            break

    return playbook


def _format_collaboration_reason_playbook(playbook: list[dict[str, Any]], limit: int = 2) -> str:
    if not isinstance(playbook, list):
        return ""

    safe_limit = max(1, min(int(limit), 6))
    parts: list[str] = []
    for item in playbook[:safe_limit]:
        reason = str((item or {}).get("reason") or "").strip()
        action = str((item or {}).get("action") or "").strip()
        count = max(0, _to_int((item or {}).get("count"), 0))
        if not reason or not action or count <= 0:
            continue
        parts.append(f"{reason}×{count}：{action}")

    return "；".join(parts)


_RUNTIME_OBSERVABILITY_DEFAULT_THRESHOLDS: dict[str, Any] = {
    "failure_rate_high": 0.20,
    "failure_rate_medium": 0.12,
    "blocked_rate_high": 0.20,
    "blocked_rate_medium": 0.10,
    "p95_duration_ms_high": 12000,
    "p95_duration_ms_medium": 6000,
    "trace_coverage_min": 0.85,
    "pending_approval_high": 12,
    "pending_approval_medium": 6,
    "collab_false_trigger_proxy_rate_high": 0.45,
    "collab_false_trigger_proxy_rate_medium": 0.30,
    "total_estimated_cost_usd_high": 15.0,
    "total_estimated_cost_usd_medium": 5.0,
}


def get_default_runtime_observability_thresholds() -> dict[str, Any]:
    return dict(_RUNTIME_OBSERVABILITY_DEFAULT_THRESHOLDS)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _pick_first_number(metadata: dict[str, Any], keys: tuple[str, ...], default: float = 0.0) -> float:
    for key in keys:
        if key in metadata:
            return _to_float(metadata.get(key), default)
    return default


def _pick_first_int(metadata: dict[str, Any], keys: tuple[str, ...], default: int = 0) -> int:
    for key in keys:
        if key in metadata:
            return _to_int(metadata.get(key), default)
    return default


def _extract_cost_usage_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    usage_obj = metadata.get("usage") if isinstance(metadata.get("usage"), dict) else {}

    model_cost_usd = max(
        0.0,
        _pick_first_number(
            metadata,
            ("model_cost_usd", "llm_estimated_cost_usd", "llm_cost_usd"),
            0.0,
        ),
    )
    tool_cost_usd = max(
        0.0,
        _pick_first_number(
            metadata,
            ("tool_cost_usd", "tool_estimated_cost_usd"),
            0.0,
        ),
    )
    external_api_cost_usd = max(
        0.0,
        _pick_first_number(
            metadata,
            ("external_api_cost_usd", "external_api_estimated_cost_usd", "external_cost_usd"),
            0.0,
        ),
    )

    prompt_tokens = max(
        0,
        _pick_first_int(metadata, ("prompt_tokens", "input_tokens"), 0),
    )
    completion_tokens = max(
        0,
        _pick_first_int(metadata, ("completion_tokens", "output_tokens"), 0),
    )
    total_tokens = max(0, _pick_first_int(metadata, ("total_tokens",), 0))

    if prompt_tokens <= 0 and usage_obj:
        prompt_tokens = max(0, _to_int(usage_obj.get("prompt_tokens"), 0))
        if prompt_tokens <= 0:
            prompt_tokens = max(0, _to_int(usage_obj.get("input_tokens"), 0))
    if completion_tokens <= 0 and usage_obj:
        completion_tokens = max(0, _to_int(usage_obj.get("completion_tokens"), 0))
        if completion_tokens <= 0:
            completion_tokens = max(0, _to_int(usage_obj.get("output_tokens"), 0))
    if total_tokens <= 0 and usage_obj:
        total_tokens = max(0, _to_int(usage_obj.get("total_tokens"), 0))
    if total_tokens <= 0 and (prompt_tokens > 0 or completion_tokens > 0):
        total_tokens = prompt_tokens + completion_tokens

    return {
        "model_cost_usd": model_cost_usd,
        "tool_cost_usd": tool_cost_usd,
        "external_api_cost_usd": external_api_cost_usd,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
    }


def normalize_runtime_observability_thresholds(raw: Any) -> dict[str, Any]:
    defaults = get_default_runtime_observability_thresholds()
    incoming = raw if isinstance(raw, dict) else {}

    def _read_rate(key: str) -> float:
        return round(_clamp(_to_float(incoming.get(key), defaults[key]), 0.0, 1.0), 4)

    def _read_int(key: str, min_value: int = 1) -> int:
        return max(min_value, _to_int(incoming.get(key), defaults[key]))

    def _read_cost(key: str) -> float:
        return round(max(0.0, _to_float(incoming.get(key), defaults[key])), 6)

    return {
        "failure_rate_high": _read_rate("failure_rate_high"),
        "failure_rate_medium": _read_rate("failure_rate_medium"),
        "blocked_rate_high": _read_rate("blocked_rate_high"),
        "blocked_rate_medium": _read_rate("blocked_rate_medium"),
        "p95_duration_ms_high": _read_int("p95_duration_ms_high", 100),
        "p95_duration_ms_medium": _read_int("p95_duration_ms_medium", 100),
        "trace_coverage_min": _read_rate("trace_coverage_min"),
        "pending_approval_high": _read_int("pending_approval_high", 1),
        "pending_approval_medium": _read_int("pending_approval_medium", 1),
        "collab_false_trigger_proxy_rate_high": _read_rate("collab_false_trigger_proxy_rate_high"),
        "collab_false_trigger_proxy_rate_medium": _read_rate("collab_false_trigger_proxy_rate_medium"),
        "total_estimated_cost_usd_high": _read_cost("total_estimated_cost_usd_high"),
        "total_estimated_cost_usd_medium": _read_cost("total_estimated_cost_usd_medium"),
    }


_RUNTIME_OBSERVABILITY_DEFAULT_ALERT_POLICY: dict[str, Any] = {
    "enable_auto_dispatch": True,
    "enable_touch_delivery": False,
    "touch_channels": [],
    "enable_silence": True,
    "silence_window_minutes": 30,
    "enable_escalation": True,
    "escalation_window_minutes": 180,
    "escalation_repeat_count": 3,
    "dispatch_dedupe_minutes": 2,
    "default_route": "ops_oncall",
    "escalation_route": "ops_lead",
    "metric_routes": {
        "failure_rate": "reliability_ops",
        "blocked_rate": "governance_ops",
        "p95_duration_ms": "platform_perf",
        "trace_coverage_rate": "platform_observability",
        "pending_approval": "approval_ops",
        "collab_false_trigger_proxy_rate": "collaboration_ops",
        "total_estimated_cost_usd": "finops",
    },
}


def get_default_runtime_observability_alert_policy() -> dict[str, Any]:
    defaults = dict(_RUNTIME_OBSERVABILITY_DEFAULT_ALERT_POLICY)
    defaults["metric_routes"] = dict(_RUNTIME_OBSERVABILITY_DEFAULT_ALERT_POLICY.get("metric_routes") or {})
    defaults["touch_channels"] = list(_RUNTIME_OBSERVABILITY_DEFAULT_ALERT_POLICY.get("touch_channels") or [])
    return defaults


def normalize_runtime_observability_alert_policy(raw: Any) -> dict[str, Any]:
    defaults = get_default_runtime_observability_alert_policy()
    incoming = raw if isinstance(raw, dict) else {}

    def _read_bool(key: str) -> bool:
        if key not in incoming:
            return _to_bool(defaults.get(key))
        return _to_bool(incoming.get(key))

    def _read_minutes(key: str, min_value: int = 1, max_value: int = 60 * 24 * 30) -> int:
        return max(min_value, min(max_value, _to_int(incoming.get(key), _to_int(defaults.get(key), min_value))))

    def _read_count(key: str, min_value: int = 1, max_value: int = 100) -> int:
        return max(min_value, min(max_value, _to_int(incoming.get(key), _to_int(defaults.get(key), min_value))))

    def _read_route(key: str, fallback: str) -> str:
        token = str(incoming.get(key, fallback) or fallback).strip()
        return token[:128] if token else fallback

    metric_routes = dict(defaults.get("metric_routes") or {})
    incoming_metric_routes = incoming.get("metric_routes")
    if isinstance(incoming_metric_routes, dict):
        for metric, route in incoming_metric_routes.items():
            metric_key = str(metric or "").strip()
            route_key = str(route or "").strip()
            if not metric_key or not route_key:
                continue
            metric_routes[metric_key[:64]] = route_key[:128]

    allowed_touch_channels = {"wecom", "feishu", "telegram", "discord"}
    touch_channels: list[str] = []
    incoming_touch_channels = incoming.get("touch_channels")
    if isinstance(incoming_touch_channels, list):
        for item in incoming_touch_channels:
            token = str(item or "").strip().lower()
            if token in allowed_touch_channels and token not in touch_channels:
                touch_channels.append(token)
    if not touch_channels:
        for item in defaults.get("touch_channels") or []:
            token = str(item or "").strip().lower()
            if token in allowed_touch_channels and token not in touch_channels:
                touch_channels.append(token)

    return {
        "enable_auto_dispatch": _read_bool("enable_auto_dispatch"),
        "enable_touch_delivery": _read_bool("enable_touch_delivery"),
        "touch_channels": touch_channels,
        "enable_silence": _read_bool("enable_silence"),
        "silence_window_minutes": _read_minutes("silence_window_minutes", 1),
        "enable_escalation": _read_bool("enable_escalation"),
        "escalation_window_minutes": _read_minutes("escalation_window_minutes", 1),
        "escalation_repeat_count": _read_count("escalation_repeat_count", 1),
        "dispatch_dedupe_minutes": _read_minutes("dispatch_dedupe_minutes", 1),
        "default_route": _read_route("default_route", str(defaults.get("default_route") or "ops_oncall")),
        "escalation_route": _read_route("escalation_route", str(defaults.get("escalation_route") or "ops_lead")),
        "metric_routes": metric_routes,
    }


def _safe_json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return "{}"


async def _dispatch_runtime_alert_to_touch_channels(
    *,
    conn,
    user_id: int,
    workspace_id: int,
    alert_key: str,
    title: str,
    content: str,
    channels: list[str],
) -> dict[str, Any]:
    allowed = {"wecom", "feishu", "telegram", "discord"}
    target_channels = [
        str(item or "").strip().lower()
        for item in (channels or [])
        if str(item or "").strip().lower() in allowed
    ]
    deduplicated_channels: list[str] = []
    for token in target_channels:
        if token not in deduplicated_channels:
            deduplicated_channels.append(token)
    if not deduplicated_channels:
        return {
            "enabled": False,
            "attempted": 0,
            "success": 0,
            "failed": 0,
            "channels": [],
            "results": [],
        }

    placeholders = ", ".join("?" for _ in deduplicated_channels)
    rows = await conn.execute_fetchall(
        f"""
        SELECT id, user_id, channel, webhook_url, bot_token_enc, chat_id, secret_enc,
               extra_credentials, enabled, last_tested_at, last_error, created_at, updated_at
        FROM channel_connections
        WHERE user_id = ?
          AND enabled = 1
          AND channel IN ({placeholders})
        ORDER BY channel
        """,
        tuple([int(user_id), *deduplicated_channels]),
    )
    conn_map = {str(row["channel"] or "").strip().lower(): row for row in rows}

    from src.routes.platform_connect import _send_channel_message

    results: list[dict[str, Any]] = []
    success_count = 0
    failed_count = 0
    for token in deduplicated_channels:
        row = conn_map.get(token)
        if row is None:
            failed_count += 1
            results.append(
                {
                    "channel": token,
                    "success": False,
                    "message": "渠道未连接或已停用",
                    "status_code": 0,
                    "error_code": "NOT_CONNECTED",
                }
            )
            await conn.execute(
                """
                INSERT INTO channel_delivery_logs
                    (user_id, channel, target, title, content, status, response_json, error_message)
                VALUES (?, ?, ?, ?, ?, 'error', ?, ?)
                """,
                (
                    int(user_id),
                    token,
                    "",
                    str(title or ""),
                    str(content or ""),
                    _safe_json_dumps({"alert_key": str(alert_key or "")}),
                    "渠道未连接或已停用",
                ),
            )
            continue

        try:
            delivery = await _send_channel_message(
                row,
                channel=token,
                title=str(title or ""),
                content=str(content or ""),
            )
        except Exception as exc:
            delivery = {
                "success": False,
                "message": f"触达异常: {exc}",
                "status_code": 0,
                "response": {},
                "target": "",
                "error_code": "RUNTIME_ERROR",
            }

        delivery_success = bool(delivery.get("success"))
        if delivery_success:
            success_count += 1
        else:
            failed_count += 1

        results.append(
            {
                "channel": token,
                "success": delivery_success,
                "message": str(delivery.get("message") or ""),
                "status_code": int(delivery.get("status_code") or 0),
                "error_code": str(delivery.get("error_code") or ""),
            }
        )

        await conn.execute(
            """
            INSERT INTO channel_delivery_logs
                (user_id, channel, target, title, content, status, response_json, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(user_id),
                token,
                str(delivery.get("target") or ""),
                str(title or ""),
                str(content or ""),
                "success" if delivery_success else "error",
                _safe_json_dumps(delivery.get("response") or {}),
                "" if delivery_success else str(delivery.get("message") or "delivery_failed"),
            ),
        )

    return {
        "enabled": True,
        "attempted": int(len(deduplicated_channels)),
        "success": int(success_count),
        "failed": int(failed_count),
        "channels": deduplicated_channels,
        "results": results,
    }


def _build_runtime_observability_alerts(
    *,
    tool_total: int,
    failure_rate: float,
    blocked_rate: float,
    p95_duration_ms: int,
    trace_coverage_rate: float,
    pending_approval: int,
    collab_false_trigger_proxy_rate: float,
    collab_reason_playbook: list[dict[str, Any]],
    total_estimated_cost_usd: float,
    thresholds: dict[str, Any],
) -> list[dict[str, Any]]:
    alerts: list[dict[str, Any]] = []

    if tool_total > 0 and failure_rate >= _to_float(thresholds.get("failure_rate_high"), 0.2):
        alerts.append({
            "level": "high",
            "title": "工具失败率过高",
            "detail": f"失败率 {round(failure_rate * 100, 2)}%，建议优先排查异常工具与参数策略。",
            "metric": "failure_rate",
            "value": round(failure_rate, 4),
        })
    elif tool_total > 0 and failure_rate >= _to_float(thresholds.get("failure_rate_medium"), 0.12):
        alerts.append({
            "level": "medium",
            "title": "工具失败率偏高",
            "detail": f"失败率 {round(failure_rate * 100, 2)}%，建议跟踪 Top Tools 的失败分布。",
            "metric": "failure_rate",
            "value": round(failure_rate, 4),
        })

    if tool_total > 0 and blocked_rate >= _to_float(thresholds.get("blocked_rate_high"), 0.2):
        alerts.append({
            "level": "high",
            "title": "审批阻塞比例过高",
            "detail": f"阻塞率 {round(blocked_rate * 100, 2)}%，建议优化风险分层与白名单。",
            "metric": "blocked_rate",
            "value": round(blocked_rate, 4),
        })
    elif tool_total > 0 and blocked_rate >= _to_float(thresholds.get("blocked_rate_medium"), 0.1):
        alerts.append({
            "level": "medium",
            "title": "审批阻塞比例偏高",
            "detail": f"阻塞率 {round(blocked_rate * 100, 2)}%，建议评估审批阈值。",
            "metric": "blocked_rate",
            "value": round(blocked_rate, 4),
        })

    if p95_duration_ms >= _to_int(thresholds.get("p95_duration_ms_high"), 12000):
        alerts.append({
            "level": "high",
            "title": "P95 延迟过高",
            "detail": f"P95 {p95_duration_ms}ms，建议优先定位长尾慢调用。",
            "metric": "p95_duration_ms",
            "value": int(p95_duration_ms),
        })
    elif p95_duration_ms >= _to_int(thresholds.get("p95_duration_ms_medium"), 6000):
        alerts.append({
            "level": "medium",
            "title": "P95 延迟偏高",
            "detail": f"P95 {p95_duration_ms}ms，建议关注慢工具与重试链路。",
            "metric": "p95_duration_ms",
            "value": int(p95_duration_ms),
        })

    if tool_total > 0 and trace_coverage_rate < _to_float(thresholds.get("trace_coverage_min"), 0.85):
        alerts.append({
            "level": "medium",
            "title": "Trace 覆盖不足",
            "detail": f"覆盖率 {round(trace_coverage_rate * 100, 2)}%，建议补齐 trace_id 透传。",
            "metric": "trace_coverage_rate",
            "value": round(trace_coverage_rate, 4),
        })

    if pending_approval >= _to_int(thresholds.get("pending_approval_high"), 12):
        alerts.append({
            "level": "high",
            "title": "审批积压明显",
            "detail": f"待审批 {pending_approval} 条，建议引入分级 SLA 与批量审批。",
            "metric": "pending_approval",
            "value": int(pending_approval),
        })
    elif pending_approval >= _to_int(thresholds.get("pending_approval_medium"), 6):
        alerts.append({
            "level": "medium",
            "title": "审批有积压",
            "detail": f"待审批 {pending_approval} 条，建议及时清理审批队列。",
            "metric": "pending_approval",
            "value": int(pending_approval),
        })

    collab_playbook_preview = [dict(item) for item in (collab_reason_playbook or [])[:3]]
    collab_playbook_hint = _format_collaboration_reason_playbook(collab_playbook_preview, limit=2)

    if collab_false_trigger_proxy_rate >= _to_float(thresholds.get("collab_false_trigger_proxy_rate_high"), 0.45):
        detail = f"隐式协作噪声代理率 {round(collab_false_trigger_proxy_rate * 100, 2)}%，建议收紧自动协作触发条件。"
        if collab_playbook_hint:
            detail += f" 高频判定建议：{collab_playbook_hint}"
        alerts.append({
            "level": "high",
            "title": "协作误触发风险偏高",
            "detail": detail,
            "metric": "collab_false_trigger_proxy_rate",
            "value": round(collab_false_trigger_proxy_rate, 4),
            "reason_playbook": collab_playbook_preview,
        })
    elif collab_false_trigger_proxy_rate >= _to_float(thresholds.get("collab_false_trigger_proxy_rate_medium"), 0.30):
        detail = f"隐式协作噪声代理率 {round(collab_false_trigger_proxy_rate * 100, 2)}%，建议复核判定原因分布。"
        if collab_playbook_hint:
            detail += f" 高频判定建议：{collab_playbook_hint}"
        alerts.append({
            "level": "medium",
            "title": "协作误触发风险上升",
            "detail": detail,
            "metric": "collab_false_trigger_proxy_rate",
            "value": round(collab_false_trigger_proxy_rate, 4),
            "reason_playbook": collab_playbook_preview,
        })

    if total_estimated_cost_usd >= _to_float(thresholds.get("total_estimated_cost_usd_high"), 15.0):
        alerts.append({
            "level": "high",
            "title": "成本消耗过高",
            "detail": f"估算成本 ${total_estimated_cost_usd:.4f}，建议排查高成本调用与重复执行。",
            "metric": "total_estimated_cost_usd",
            "value": round(total_estimated_cost_usd, 6),
        })
    elif total_estimated_cost_usd >= _to_float(thresholds.get("total_estimated_cost_usd_medium"), 5.0):
        alerts.append({
            "level": "medium",
            "title": "成本消耗偏高",
            "detail": f"估算成本 ${total_estimated_cost_usd:.4f}，建议关注工具链路成本分布。",
            "metric": "total_estimated_cost_usd",
            "value": round(total_estimated_cost_usd, 6),
        })

    return alerts


async def log_audit_event(
    *,
    actor_user_id: int,
    action: str,
    target_type: str,
    target_id: str | int = "",
    status: str = "success",
    workspace_id: Optional[int] = None,
    reason: str = "",
    request_id: str = "",
    trace_id: str = "",
    run_id: Optional[int] = None,
    action_log_id: Optional[int] = None,
    metadata: Optional[dict[str, Any]] = None,
    db=None,
) -> int:
    conn = db or await get_db()
    effective_request_id = str(request_id or get_current_request_id(""))[:128]
    effective_trace_id = str(trace_id or get_current_trace_id(effective_request_id))[:128]

    metadata_obj = dict(metadata or {})
    if effective_trace_id and not metadata_obj.get("trace_id"):
        metadata_obj["trace_id"] = effective_trace_id

    metadata_json = json.dumps(metadata_obj, ensure_ascii=False)

    cur = await conn.execute(
        """
        INSERT INTO audit_logs
        (workspace_id, actor_user_id, action, target_type, target_id, status, reason, request_id, run_id, action_log_id, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            workspace_id,
            actor_user_id,
            action,
            target_type,
            str(target_id or ""),
            status,
            reason,
            effective_request_id,
            run_id,
            action_log_id,
            metadata_json,
        ),
    )
    if db is None:
        await conn.commit()
    return int(cur.lastrowid)


async def list_audit_logs(
    *,
    workspace_id: Optional[int] = None,
    actor_user_id: Optional[int] = None,
    action: str = "",
    target_type: str = "",
    status: str = "",
    run_id: Optional[int] = None,
    action_log_id: Optional[int] = None,
    created_from: str = "",
    created_to: str = "",
    limit: int = 100,
    db=None,
) -> list[dict[str, Any]]:
    conn = db or await get_db()
    where: list[str] = []
    params: list[Any] = []

    if workspace_id is not None:
        where.append("workspace_id = ?")
        params.append(workspace_id)
    if actor_user_id is not None:
        where.append("actor_user_id = ?")
        params.append(actor_user_id)
    if action:
        where.append("action = ?")
        params.append(action)
    if target_type:
        where.append("target_type = ?")
        params.append(target_type)
    if status:
        where.append("status = ?")
        params.append(status)
    if run_id is not None:
        where.append("run_id = ?")
        params.append(run_id)
    if action_log_id is not None:
        where.append("action_log_id = ?")
        params.append(action_log_id)
    if created_from:
        where.append("created_at >= ?")
        params.append(created_from)
    if created_to:
        where.append("created_at <= ?")
        params.append(created_to)

    where_sql = ""
    if where:
        where_sql = "WHERE " + " AND ".join(where)

    rows = await conn.execute_fetchall(
        f"""
        SELECT id, workspace_id, actor_user_id, action, target_type, target_id,
               status, reason, request_id, run_id, action_log_id, metadata, created_at
        FROM audit_logs
        {where_sql}
        ORDER BY id DESC
        LIMIT ?
        """,
        tuple([*params, max(1, min(int(limit), 1000))]),
    )

    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["metadata"] = _safe_json_object(item.get("metadata"))
        items.append(item)
    return items


async def summarize_workspace_runtime_observability(
    *,
    workspace_id: int,
    hours: int = 24,
    actor_user_id: Optional[int] = None,
    auto_dispatch: bool = True,
    db=None,
) -> dict[str, Any]:
    conn = db or await get_db()
    safe_hours = max(1, min(int(hours), 24 * 30))

    workspace_row = await conn.execute_fetchone(
        "SELECT user_id, config FROM workspaces WHERE id = ?",
        (int(workspace_id),),
    )
    workspace_obj = dict(workspace_row) if workspace_row else {}
    workspace_config = _safe_json_object(workspace_obj.get("config"))
    observability_config = workspace_config.get("observability") if isinstance(workspace_config.get("observability"), dict) else {}
    thresholds = normalize_runtime_observability_thresholds(
        observability_config.get("thresholds") if isinstance(observability_config, dict) else {},
    )
    alert_policy = normalize_runtime_observability_alert_policy(
        observability_config.get("alert_policy") if isinstance(observability_config, dict) else {},
    )

    rows = await conn.execute_fetchall(
        """
        SELECT action, status, reason, request_id, metadata, created_at
        FROM audit_logs
        WHERE workspace_id = ?
          AND created_at >= datetime('now', ? || ' hours')
          AND action IN ('chat.tool.execute', 'chat.llm.execute', 'chat.collaboration.decision', 'workspace.tool_approval.approve', 'workspace.tool_approval.reject')
        ORDER BY id DESC
        LIMIT 5000
        """,
        (int(workspace_id), f"-{safe_hours}"),
    )

    tool_total = 0
    tool_success = 0
    tool_failed = 0
    tool_blocked = 0
    durations: list[int] = []
    approval_required_count = 0
    trace_seen = 0

    risk_counts: dict[str, int] = {}
    skill_stats: dict[str, dict[str, int]] = {}
    approval_events = {"approved": 0, "rejected": 0}

    collab_decision_total = 0
    collab_auto_decision_total = 0
    collab_smalltalk_blocked_auto_count = 0
    collab_attempted_count = 0
    collab_executed_count = 0
    collab_failed_count = 0
    collab_implicit_attempt_count = 0
    collab_implicit_empty_count = 0
    collab_reason_counts: dict[str, int] = {}

    llm_estimated_cost_usd = 0.0
    tool_estimated_cost_usd = 0.0
    external_api_estimated_cost_usd = 0.0
    prompt_tokens_total = 0
    completion_tokens_total = 0
    total_tokens_total = 0

    for row in rows:
        row_obj = dict(row)
        action = str(row_obj.get("action") or "").strip()
        status = str(row_obj.get("status") or "").strip().lower()
        metadata = _safe_json_object(row_obj.get("metadata"))

        usage = _extract_cost_usage_metadata(metadata)
        llm_estimated_cost_usd += _to_float(usage.get("model_cost_usd"), 0.0)
        tool_estimated_cost_usd += _to_float(usage.get("tool_cost_usd"), 0.0)
        external_api_estimated_cost_usd += _to_float(usage.get("external_api_cost_usd"), 0.0)
        prompt_tokens_total += _to_int(usage.get("prompt_tokens"), 0)
        completion_tokens_total += _to_int(usage.get("completion_tokens"), 0)
        total_tokens_total += _to_int(usage.get("total_tokens"), 0)

        if action == "chat.tool.execute":
            tool_total += 1
            if status == "success":
                tool_success += 1
            elif status == "blocked":
                tool_blocked += 1
            else:
                tool_failed += 1

            duration_ms = _to_int(metadata.get("duration_ms"), -1)
            if duration_ms >= 0:
                durations.append(duration_ms)

            if _to_bool(metadata.get("approval_required")):
                approval_required_count += 1

            risk_level = str(metadata.get("risk_level") or "unknown").strip().upper() or "UNKNOWN"
            risk_counts[risk_level] = risk_counts.get(risk_level, 0) + 1

            skill_name = str(metadata.get("skill") or "").strip() or "unknown"
            if skill_name not in skill_stats:
                skill_stats[skill_name] = {"count": 0, "success": 0, "failed": 0, "blocked": 0}
            skill_stats[skill_name]["count"] += 1
            if status == "success":
                skill_stats[skill_name]["success"] += 1
            elif status == "blocked":
                skill_stats[skill_name]["blocked"] += 1
            else:
                skill_stats[skill_name]["failed"] += 1

            trace_id = str(metadata.get("trace_id") or row_obj.get("request_id") or "").strip()
            if trace_id:
                trace_seen += 1

        elif action == "chat.collaboration.decision":
            collab_decision_total += 1
            decision_reason = str(metadata.get("decision_reason") or "").strip() or "unknown"
            collab_reason_counts[decision_reason] = int(collab_reason_counts.get(decision_reason, 0) + 1)

            collab_mode = str(metadata.get("collaboration_mode") or "").strip().lower()
            if collab_mode == "auto":
                collab_auto_decision_total += 1

            explicit_collab_requested = _to_bool(metadata.get("explicit_collab_requested"))
            should_collab = _to_bool(metadata.get("should_collab"))
            smalltalk_blocked = _to_bool(metadata.get("smalltalk_blocked")) or decision_reason == "smalltalk_guard"

            dispatch_started = _to_bool(metadata.get("dispatch_started"))
            dispatch_done = _to_bool(metadata.get("dispatch_done"))
            dispatch_failed = _to_bool(metadata.get("dispatch_failed"))
            contributions_count = max(0, _to_int(metadata.get("contributions_count"), 0))

            if collab_mode == "auto" and smalltalk_blocked and not should_collab:
                collab_smalltalk_blocked_auto_count += 1

            if dispatch_started:
                collab_attempted_count += 1
            if dispatch_done:
                collab_executed_count += 1
            if dispatch_failed:
                collab_failed_count += 1

            if collab_mode == "auto" and dispatch_started and not explicit_collab_requested:
                collab_implicit_attempt_count += 1
                if contributions_count <= 0:
                    collab_implicit_empty_count += 1

        elif action == "workspace.tool_approval.approve":
            approval_events["approved"] += 1
        elif action == "workspace.tool_approval.reject":
            approval_events["rejected"] += 1

    approval_recent_rows = await conn.execute_fetchall(
        """
        SELECT status, COUNT(1) AS cnt
        FROM tool_approval_queue
        WHERE workspace_id = ?
          AND created_at >= datetime('now', ? || ' hours')
        GROUP BY status
        """,
        (int(workspace_id), f"-{safe_hours}"),
    )
    approval_all_rows = await conn.execute_fetchall(
        """
        SELECT status, COUNT(1) AS cnt
        FROM tool_approval_queue
        WHERE workspace_id = ?
        GROUP BY status
        """,
        (int(workspace_id),),
    )

    def _status_map(rows_obj: list[Any]) -> dict[str, int]:
        base: dict[str, int] = {"pending": 0, "approved": 0, "executed": 0, "failed": 0, "rejected": 0}
        for r in rows_obj:
            key = str(r["status"] or "").strip().lower()
            if not key:
                continue
            base[key] = _to_int(r["cnt"], 0)
        base["total"] = int(sum(base.values()))
        return base

    approval_recent_map = _status_map(approval_recent_rows)
    approval_all_map = _status_map(approval_all_rows)

    top_tools = [
        {
            "skill": skill,
            "count": int(stat["count"]),
            "success": int(stat["success"]),
            "failed": int(stat["failed"]),
            "blocked": int(stat["blocked"]),
        }
        for skill, stat in sorted(
            skill_stats.items(),
            key=lambda item: (
                -int(item[1].get("count") or 0),
                -int(item[1].get("blocked") or 0),
                -int(item[1].get("failed") or 0),
                item[0],
            ),
        )[:10]
    ]

    risk_distribution = [
        {"risk_level": risk, "count": int(cnt)}
        for risk, cnt in sorted(risk_counts.items(), key=lambda item: (-int(item[1]), item[0]))
    ]

    since = datetime.now(timezone.utc) - timedelta(hours=safe_hours)

    total_estimated_cost_usd = round(
        float(llm_estimated_cost_usd + tool_estimated_cost_usd + external_api_estimated_cost_usd),
        6,
    )
    token_total = int(total_tokens_total)
    if token_total <= 0 and (prompt_tokens_total > 0 or completion_tokens_total > 0):
        token_total = int(prompt_tokens_total + completion_tokens_total)

    failure_rate = _ratio(tool_failed, tool_total)
    blocked_rate = _ratio(tool_blocked, tool_total)
    trace_coverage_rate = _ratio(trace_seen, tool_total)
    p95_duration_ms = _p95(durations)
    pending_approval = int(approval_recent_map.get("pending") or 0)

    collab_reason_distribution = [
        {"reason": reason, "count": int(count)}
        for reason, count in sorted(collab_reason_counts.items(), key=lambda item: (-int(item[1]), item[0]))
    ]
    collab_reason_playbook = _build_collaboration_reason_playbook(collab_reason_counts, limit=4)
    collab_attempt_to_execute_rate = _ratio(collab_executed_count, collab_attempted_count)
    collab_smalltalk_block_rate = _ratio(collab_smalltalk_blocked_auto_count, collab_auto_decision_total)
    collab_false_trigger_proxy_rate = _ratio(collab_implicit_empty_count, collab_implicit_attempt_count)

    collaboration_observability = {
        "decision_total": int(collab_decision_total),
        "auto_mode_decision_total": int(collab_auto_decision_total),
        "smalltalk_blocked_auto_count": int(collab_smalltalk_blocked_auto_count),
        "smalltalk_block_rate": float(collab_smalltalk_block_rate),
        "attempted_count": int(collab_attempted_count),
        "executed_count": int(collab_executed_count),
        "failed_count": int(collab_failed_count),
        "attempt_to_execute_rate": float(collab_attempt_to_execute_rate),
        "implicit_auto_attempt_count": int(collab_implicit_attempt_count),
        "implicit_auto_empty_count": int(collab_implicit_empty_count),
        "false_trigger_proxy_rate": float(collab_false_trigger_proxy_rate),
        "decision_reason_distribution": collab_reason_distribution,
        "reason_playbook": collab_reason_playbook,
    }

    alerts = _build_runtime_observability_alerts(
        tool_total=int(tool_total),
        failure_rate=failure_rate,
        blocked_rate=blocked_rate,
        p95_duration_ms=int(p95_duration_ms),
        trace_coverage_rate=trace_coverage_rate,
        pending_approval=pending_approval,
        collab_false_trigger_proxy_rate=collab_false_trigger_proxy_rate,
        collab_reason_playbook=collab_reason_playbook,
        total_estimated_cost_usd=total_estimated_cost_usd,
        thresholds=thresholds,
    )

    collab_slo_hint = _format_collaboration_reason_playbook(collab_reason_playbook, limit=2)

    slo_recommendations: list[dict[str, Any]] = []
    if tool_total > 0 and failure_rate >= _to_float(thresholds.get("failure_rate_high"), 0.2):
        slo_recommendations.append(
            {
                "type": "auto_protection",
                "priority": "high",
                "trigger_metric": "failure_rate",
                "action": "建议切换降级模式并限制高风险工具并发，优先恢复成功率。",
            }
        )
    if tool_total > 0 and blocked_rate >= _to_float(thresholds.get("blocked_rate_high"), 0.2):
        slo_recommendations.append(
            {
                "type": "governance_tuning",
                "priority": "high",
                "trigger_metric": "blocked_rate",
                "action": "建议分层审批阈值与白名单校准，降低关键路径阻塞。",
            }
        )
    if collab_implicit_attempt_count > 0 and collab_false_trigger_proxy_rate >= _to_float(thresholds.get("collab_false_trigger_proxy_rate_high"), 0.45):
        collab_action = "建议收紧自动协作判定并回放 decision_reason 样本，优先降低隐式空产出。"
        if collab_slo_hint:
            collab_action += f" 优先执行：{collab_slo_hint}"
        slo_recommendations.append(
            {
                "type": "collaboration_guardrail",
                "priority": "high",
                "trigger_metric": "collab_false_trigger_proxy_rate",
                "action": collab_action,
            }
        )
    if p95_duration_ms >= _to_int(thresholds.get("p95_duration_ms_high"), 12000):
        slo_recommendations.append(
            {
                "type": "performance_guard",
                "priority": "high",
                "trigger_metric": "p95_duration_ms",
                "action": "建议启用慢调用保护策略，限制长尾工具链并触发性能排障。",
            }
        )

    enable_auto_dispatch = _to_bool(alert_policy.get("enable_auto_dispatch"))
    silence_window_minutes = _to_int(alert_policy.get("silence_window_minutes"), 30)
    escalation_window_minutes = _to_int(alert_policy.get("escalation_window_minutes"), 180)
    dispatch_dedupe_minutes = _to_int(alert_policy.get("dispatch_dedupe_minutes"), 2)
    dispatch_lookback_minutes = max(silence_window_minutes, escalation_window_minutes, dispatch_dedupe_minutes, 1)
    dispatch_rows = await conn.execute_fetchall(
        """
        SELECT metadata, created_at
        FROM audit_logs
        WHERE workspace_id = ?
          AND action = 'workspace.observability.runtime_alert.dispatch'
          AND created_at >= datetime('now', ? || ' minutes')
        ORDER BY id DESC
        LIMIT 5000
        """,
        (int(workspace_id), f"-{dispatch_lookback_minutes}"),
    )

    lifecycle_lookback_minutes = max(dispatch_lookback_minutes, safe_hours * 60, 60)
    lifecycle_rows = await conn.execute_fetchall(
        """
        SELECT action, metadata, created_at
        FROM audit_logs
        WHERE workspace_id = ?
          AND action IN ('workspace.observability.runtime_alert.ack', 'workspace.observability.runtime_alert.recover')
          AND created_at >= datetime('now', ? || ' minutes')
        ORDER BY id DESC
        LIMIT 5000
        """,
        (int(workspace_id), f"-{lifecycle_lookback_minutes}"),
    )

    dispatch_count_by_alert_key: dict[str, int] = {}
    dispatch_idempotency_keys: set[str] = set()
    dispatch_ack_latency_seconds_samples: list[float] = []
    dispatch_recovery_seconds_samples: list[float] = []
    dispatch_time_by_idempotency: dict[str, datetime] = {}
    dispatch_times_by_alert_key: dict[str, list[datetime]] = {}

    collab_alert_key_prefix = "collab_false_trigger_proxy_rate:"
    collab_alert_dispatch_total = 0
    collab_alert_ack_count = 0
    collab_alert_recover_count = 0
    collab_alert_pending_recovery = 0
    collab_dispatch_with_playbook_count = 0
    collab_dispatch_playbook_item_total = 0
    collab_playbook_selected_count = 0
    collab_playbook_action_counts: dict[str, int] = {}
    collab_playbook_reason_counts: dict[str, int] = {}
    collab_last_ack_at: Optional[datetime] = None
    collab_last_recover_at: Optional[datetime] = None

    for dispatch_row in dispatch_rows:
        dispatch_obj = dict(dispatch_row)
        metadata = _safe_json_object(dispatch_obj.get("metadata"))
        alert_key = str(metadata.get("alert_key") or "").strip()
        if not alert_key:
            continue

        dispatch_count_by_alert_key[alert_key] = int(dispatch_count_by_alert_key.get(alert_key, 0) + 1)
        if alert_key.startswith(collab_alert_key_prefix):
            collab_alert_dispatch_total += 1
            reason_playbook = metadata.get("reason_playbook") if isinstance(metadata.get("reason_playbook"), list) else []
            if reason_playbook:
                collab_dispatch_with_playbook_count += 1
                collab_dispatch_playbook_item_total += int(len(reason_playbook))

        dispatched_at = _parse_iso_datetime(
            metadata.get("dispatched_at_utc") or metadata.get("dispatched_at") or dispatch_obj.get("created_at")
        )
        if dispatched_at is not None:
            dispatch_times_by_alert_key.setdefault(alert_key, []).append(dispatched_at)

        idempotency_key = str(metadata.get("idempotency_key") or "").strip()
        if idempotency_key:
            dispatch_idempotency_keys.add(idempotency_key)
            if dispatched_at is not None:
                current = dispatch_time_by_idempotency.get(idempotency_key)
                if current is None or dispatched_at > current:
                    dispatch_time_by_idempotency[idempotency_key] = dispatched_at

        ack_latency_seconds = _to_float(metadata.get("ack_latency_seconds"), -1.0)
        if ack_latency_seconds >= 0:
            dispatch_ack_latency_seconds_samples.append(float(ack_latency_seconds))

        recovery_seconds = _to_float(metadata.get("recovery_latency_seconds"), -1.0)
        if recovery_seconds >= 0:
            dispatch_recovery_seconds_samples.append(float(recovery_seconds))

    for key in list(dispatch_times_by_alert_key.keys()):
        dispatch_times_by_alert_key[key] = sorted(dispatch_times_by_alert_key[key])

    for lifecycle_row in lifecycle_rows:
        lifecycle_obj = dict(lifecycle_row)
        action = str(lifecycle_obj.get("action") or "").strip()
        metadata = _safe_json_object(lifecycle_obj.get("metadata"))

        alert_key = str(metadata.get("alert_key") or "").strip()
        idempotency_key = str(metadata.get("idempotency_key") or "").strip()

        event_time = _parse_iso_datetime(
            metadata.get("event_at_utc")
            or metadata.get("ack_at_utc")
            or metadata.get("recovered_at_utc")
            or lifecycle_obj.get("created_at")
        )
        if event_time is None:
            continue

        base_dispatch_time: Optional[datetime] = None
        if idempotency_key:
            base_dispatch_time = dispatch_time_by_idempotency.get(idempotency_key)
        if base_dispatch_time is None and alert_key:
            candidates = dispatch_times_by_alert_key.get(alert_key) or []
            for ts in reversed(candidates):
                if ts <= event_time:
                    base_dispatch_time = ts
                    break

        if base_dispatch_time is None:
            continue

        latency_seconds = float((event_time - base_dispatch_time).total_seconds())
        if latency_seconds < 0:
            continue

        if action == "workspace.observability.runtime_alert.ack":
            dispatch_ack_latency_seconds_samples.append(latency_seconds)
        elif action == "workspace.observability.runtime_alert.recover":
            dispatch_recovery_seconds_samples.append(latency_seconds)

        if alert_key.startswith(collab_alert_key_prefix):
            selected_reason = str(
                metadata.get("playbook_reason")
                or metadata.get("selected_playbook_reason")
                or ""
            ).strip()
            selected_action = str(
                metadata.get("playbook_action")
                or metadata.get("selected_playbook_action")
                or ""
            ).strip()

            if action == "workspace.observability.runtime_alert.ack":
                collab_alert_ack_count += 1
                if collab_last_ack_at is None or event_time > collab_last_ack_at:
                    collab_last_ack_at = event_time
            elif action == "workspace.observability.runtime_alert.recover":
                collab_alert_recover_count += 1
                if collab_last_recover_at is None or event_time > collab_last_recover_at:
                    collab_last_recover_at = event_time

            if selected_reason:
                collab_playbook_reason_counts[selected_reason] = int(collab_playbook_reason_counts.get(selected_reason, 0) + 1)
            if selected_action:
                collab_playbook_selected_count += 1
                collab_playbook_action_counts[selected_action] = int(collab_playbook_action_counts.get(selected_action, 0) + 1)

    enable_silence = _to_bool(alert_policy.get("enable_silence"))
    enable_escalation = _to_bool(alert_policy.get("enable_escalation"))
    enable_touch_delivery = _to_bool(alert_policy.get("enable_touch_delivery"))
    touch_channels_raw = alert_policy.get("touch_channels") if isinstance(alert_policy.get("touch_channels"), list) else []
    touch_channels = [
        str(item or "").strip().lower()
        for item in touch_channels_raw
        if str(item or "").strip().lower() in {"wecom", "feishu", "telegram", "discord"}
    ]
    escalation_repeat_count = max(1, _to_int(alert_policy.get("escalation_repeat_count"), 3))
    default_route = str(alert_policy.get("default_route") or "ops_oncall").strip() or "ops_oncall"
    escalation_route = str(alert_policy.get("escalation_route") or default_route).strip() or default_route
    metric_routes = alert_policy.get("metric_routes") if isinstance(alert_policy.get("metric_routes"), dict) else {}

    enhanced_alerts: list[dict[str, Any]] = []
    muted_count = 0
    ready_count = 0
    deduplicated_count = 0
    escalated_count = 0
    dispatched_now = 0
    dispatch_error_count = 0
    touch_attempted = 0
    touch_success = 0
    touch_failed = 0
    dispatch_candidates: list[tuple[int, dict[str, Any]]] = []
    now_utc = datetime.now(timezone.utc)
    dedupe_bucket = max(1, int(now_utc.timestamp() // max(1, int(dispatch_dedupe_minutes * 60))))

    resolved_dispatch_actor_user_id = _to_int(actor_user_id, 0)
    if resolved_dispatch_actor_user_id <= 0:
        resolved_dispatch_actor_user_id = _to_int(workspace_obj.get("user_id"), 0)
    if resolved_dispatch_actor_user_id <= 0:
        fallback_user_row = await conn.execute_fetchone("SELECT id FROM users ORDER BY id ASC LIMIT 1")
        if fallback_user_row is not None:
            resolved_dispatch_actor_user_id = _to_int(fallback_user_row["id"], 0)

    dispatch_enabled = bool(auto_dispatch and enable_auto_dispatch and resolved_dispatch_actor_user_id > 0)

    for alert in alerts:
        metric = str(alert.get("metric") or "unknown").strip() or "unknown"
        level = str(alert.get("level") or "medium").strip().lower() or "medium"
        alert_key = f"{metric}:{level}"
        recent_dispatch_count = _to_int(dispatch_count_by_alert_key.get(alert_key), 0)
        occurrence_count = int(recent_dispatch_count + 1)

        muted_by_silence = bool(enable_silence and silence_window_minutes > 0 and recent_dispatch_count > 0)
        escalation_triggered = bool(
            enable_escalation
            and level == "high"
            and escalation_repeat_count > 0
            and occurrence_count >= escalation_repeat_count
        )

        base_route = str(metric_routes.get(metric) or default_route).strip() or default_route
        effective_route = escalation_route if escalation_triggered else base_route
        idempotency_key = f"runtime-alert:{int(workspace_id)}:{alert_key}:{effective_route}:{dedupe_bucket}"
        deduplicated = bool((not muted_by_silence) and idempotency_key in dispatch_idempotency_keys)
        if muted_by_silence:
            delivery_state = "muted"
        elif deduplicated:
            delivery_state = "deduplicated"
        else:
            delivery_state = "ready"

        if muted_by_silence:
            muted_count += 1
        elif deduplicated:
            deduplicated_count += 1
        else:
            ready_count += 1
        if escalation_triggered:
            escalated_count += 1

        merged = dict(alert)
        merged.update(
            {
                "alert_key": alert_key,
                "delivery_state": delivery_state,
                "delivery_route": base_route,
                "effective_route": effective_route,
                "silence_window_minutes": int(silence_window_minutes),
                "recent_dispatch_count": int(recent_dispatch_count),
                "occurrence_count": int(occurrence_count),
                "escalation_triggered": bool(escalation_triggered),
                "escalation_repeat_count": int(escalation_repeat_count),
                "escalation_route": escalation_route,
                "idempotency_key": idempotency_key,
            }
        )
        if delivery_state == "ready" and dispatch_enabled:
            dispatch_candidates.append(
                (
                    len(enhanced_alerts),
                    {
                        "alert_key": alert_key,
                        "metric": metric,
                        "level": level,
                        "title": str(alert.get("title") or "").strip(),
                        "detail": str(alert.get("detail") or "").strip(),
                        "delivery_route": base_route,
                        "effective_route": effective_route,
                        "idempotency_key": idempotency_key,
                        "reason_playbook": [
                            dict(item) for item in (alert.get("reason_playbook") or [])
                            if isinstance(item, dict)
                        ][:4],
                        "playbook_hint": _format_collaboration_reason_playbook(
                            [dict(item) for item in (alert.get("reason_playbook") or []) if isinstance(item, dict)],
                            limit=2,
                        ),
                    },
                )
            )
        enhanced_alerts.append(merged)

    if dispatch_candidates:
        for alert_idx, candidate in dispatch_candidates:
            idempotency_key = str(candidate.get("idempotency_key") or "").strip()
            if not idempotency_key or idempotency_key in dispatch_idempotency_keys:
                continue

            touch_delivery = {
                "enabled": False,
                "attempted": 0,
                "success": 0,
                "failed": 0,
                "channels": [],
                "results": [],
            }
            if enable_touch_delivery and touch_channels:
                touch_title = f"运行告警[{str(candidate.get('level') or '').upper() or 'MEDIUM'}] {str(candidate.get('title') or '').strip()}"
                touch_content = (
                    f"工作区 {workspace_id}\n"
                    f"指标 {str(candidate.get('metric') or '')}\n"
                    f"详情 {str(candidate.get('detail') or '').strip()}\n"
                    f"路由 {str(candidate.get('effective_route') or default_route)}"
                )
                touch_delivery = await _dispatch_runtime_alert_to_touch_channels(
                    conn=conn,
                    user_id=int(resolved_dispatch_actor_user_id),
                    workspace_id=int(workspace_id),
                    alert_key=str(candidate.get("alert_key") or ""),
                    title=touch_title,
                    content=touch_content,
                    channels=list(touch_channels),
                )
                touch_attempted += int(touch_delivery.get("attempted") or 0)
                touch_success += int(touch_delivery.get("success") or 0)
                touch_failed += int(touch_delivery.get("failed") or 0)

            try:
                await log_audit_event(
                    actor_user_id=int(resolved_dispatch_actor_user_id),
                    workspace_id=int(workspace_id),
                    action="workspace.observability.runtime_alert.dispatch",
                    target_type="workspace",
                    target_id=f"runtime-alert:{workspace_id}:{candidate.get('alert_key')}",
                    status="success",
                    metadata={
                        "alert_key": str(candidate.get("alert_key") or ""),
                        "metric": str(candidate.get("metric") or ""),
                        "level": str(candidate.get("level") or ""),
                        "title": str(candidate.get("title") or ""),
                        "detail": str(candidate.get("detail") or ""),
                        "delivery_route": str(candidate.get("delivery_route") or default_route),
                        "effective_route": str(candidate.get("effective_route") or default_route),
                        "idempotency_key": idempotency_key,
                        "reason_playbook": [
                            dict(item) for item in (candidate.get("reason_playbook") or [])
                            if isinstance(item, dict)
                        ][:4],
                        "playbook_hint": str(candidate.get("playbook_hint") or "").strip(),
                        "dispatched_at_utc": now_utc.isoformat(),
                        "touch_delivery": touch_delivery,
                        "policy": {
                            "enable_silence": bool(enable_silence),
                            "silence_window_minutes": int(silence_window_minutes),
                            "enable_escalation": bool(enable_escalation),
                            "enable_touch_delivery": bool(enable_touch_delivery),
                            "touch_channels": list(touch_channels),
                            "escalation_window_minutes": int(escalation_window_minutes),
                            "escalation_repeat_count": int(escalation_repeat_count),
                            "dispatch_dedupe_minutes": int(dispatch_dedupe_minutes),
                            "default_route": default_route,
                            "escalation_route": escalation_route,
                        },
                    },
                    db=conn,
                )
                dispatch_idempotency_keys.add(idempotency_key)
                dispatched_now += 1
                ready_count = max(0, ready_count - 1)
                enhanced_alerts[alert_idx]["delivery_state"] = "dispatched"
                enhanced_alerts[alert_idx]["dispatched_now"] = True
                enhanced_alerts[alert_idx]["touch_delivery"] = touch_delivery
            except Exception:
                dispatch_error_count += 1

    if db is None and dispatched_now > 0:
        await conn.commit()

    current_alert_keys = {
        str(item.get("alert_key") or "").strip()
        for item in enhanced_alerts
        if str(item.get("alert_key") or "").strip()
    }
    recent_dispatch_total = int(sum(dispatch_count_by_alert_key.values()) + dispatched_now)
    inactive_single_dispatch_count = int(
        sum(1 for key, cnt in dispatch_count_by_alert_key.items() if int(cnt) == 1 and key not in current_alert_keys)
    )
    avg_ack_latency_seconds = (
        round(sum(dispatch_ack_latency_seconds_samples) / len(dispatch_ack_latency_seconds_samples), 2)
        if dispatch_ack_latency_seconds_samples
        else 0.0
    )
    avg_recovery_seconds = (
        round(sum(dispatch_recovery_seconds_samples) / len(dispatch_recovery_seconds_samples), 2)
        if dispatch_recovery_seconds_samples
        else 0.0
    )

    collab_alert_pending_recovery = max(
        0,
        int(collab_alert_dispatch_total) - int(collab_alert_recover_count),
    )
    collab_playbook_action_distribution = [
        {"action": str(action), "count": int(count)}
        for action, count in sorted(
            collab_playbook_action_counts.items(),
            key=lambda item: (-int(item[1]), str(item[0])),
        )
    ]
    collab_playbook_reason_distribution = [
        {"reason": str(reason), "count": int(count)}
        for reason, count in sorted(
            collab_playbook_reason_counts.items(),
            key=lambda item: (-int(item[1]), str(item[0])),
        )
    ]
    governance_closure = {
        "collab_alert_dispatch_total": int(collab_alert_dispatch_total),
        "collab_alert_ack_count": int(collab_alert_ack_count),
        "collab_alert_recover_count": int(collab_alert_recover_count),
        "pending_recovery_count": int(collab_alert_pending_recovery),
        "dispatch_with_playbook_count": int(collab_dispatch_with_playbook_count),
        "dispatch_with_playbook_rate": _ratio(collab_dispatch_with_playbook_count, collab_alert_dispatch_total),
        "dispatch_playbook_item_total": int(collab_dispatch_playbook_item_total),
        "playbook_selected_count": int(collab_playbook_selected_count),
        "playbook_selection_rate": _ratio(
            collab_playbook_selected_count,
            collab_alert_ack_count + collab_alert_recover_count,
        ),
        "recovery_rate": _ratio(collab_alert_recover_count, collab_alert_dispatch_total),
        "playbook_action_distribution": collab_playbook_action_distribution,
        "playbook_reason_distribution": collab_playbook_reason_distribution,
        "last_ack_at_utc": collab_last_ack_at.isoformat() if collab_last_ack_at else "",
        "last_recover_at_utc": collab_last_recover_at.isoformat() if collab_last_recover_at else "",
    }
    collaboration_observability["governance_closure"] = governance_closure

    effectiveness = {
        "recent_dispatch_total": int(recent_dispatch_total),
        "inactive_single_dispatch_count": int(inactive_single_dispatch_count),
        "false_positive_proxy_rate": _ratio(inactive_single_dispatch_count, recent_dispatch_total),
        "acknowledged_count": int(len(dispatch_ack_latency_seconds_samples)),
        "avg_ack_latency_seconds": float(avg_ack_latency_seconds),
        "recovered_count": int(len(dispatch_recovery_seconds_samples)),
        "avg_recovery_seconds": float(avg_recovery_seconds),
    }

    alerts_delivery = {
        "total": int(len(enhanced_alerts)),
        "ready": int(ready_count),
        "muted": int(muted_count),
        "deduplicated": int(deduplicated_count),
        "dispatched_now": int(dispatched_now),
        "dispatch_errors": int(dispatch_error_count),
        "escalated": int(escalated_count),
        "dispatch_lookback_minutes": int(dispatch_lookback_minutes),
        "dispatch_dedupe_minutes": int(dispatch_dedupe_minutes),
        "silence_window_minutes": int(silence_window_minutes),
        "escalation_window_minutes": int(escalation_window_minutes),
        "auto_dispatch_enabled": bool(dispatch_enabled),
        "dispatch_actor_user_id": int(resolved_dispatch_actor_user_id),
        "touch_delivery_enabled": bool(enable_touch_delivery and len(touch_channels) > 0),
        "touch_channels": list(touch_channels),
        "touch_attempted": int(touch_attempted),
        "touch_success": int(touch_success),
        "touch_failed": int(touch_failed),
        "default_route": default_route,
        "escalation_route": escalation_route,
        "effectiveness": effectiveness,
    }

    return {
        "workspace_id": int(workspace_id),
        "time_window": {
            "hours": int(safe_hours),
            "since_utc": since.isoformat(),
        },
        "tool_execution": {
            "total": int(tool_total),
            "success": int(tool_success),
            "failed": int(tool_failed),
            "blocked": int(tool_blocked),
            "success_rate": _ratio(tool_success, tool_total),
            "failure_rate": failure_rate,
            "blocked_rate": blocked_rate,
            "approval_required_count": int(approval_required_count),
            "approval_required_rate": _ratio(approval_required_count, tool_total),
            "p95_duration_ms": p95_duration_ms,
            "trace_coverage_rate": trace_coverage_rate,
        },
        "approval_flow": {
            "audit_events": {
                "approved": int(approval_events["approved"]),
                "rejected": int(approval_events["rejected"]),
            },
            "recent": approval_recent_map,
            "all_time": approval_all_map,
        },
        "cost_summary": {
            "total_estimated_cost_usd": total_estimated_cost_usd,
            "llm_estimated_cost_usd": round(float(llm_estimated_cost_usd), 6),
            "tool_estimated_cost_usd": round(float(tool_estimated_cost_usd), 6),
            "external_api_estimated_cost_usd": round(float(external_api_estimated_cost_usd), 6),
            "prompt_tokens": int(prompt_tokens_total),
            "completion_tokens": int(completion_tokens_total),
            "total_tokens": int(token_total),
            "token_usage": {
                "prompt_tokens": int(prompt_tokens_total),
                "completion_tokens": int(completion_tokens_total),
                "total_tokens": int(token_total),
            },
        },
        "collaboration_observability": collaboration_observability,
        "thresholds_used": thresholds,
        "alert_policy_used": alert_policy,
        "alerts_delivery": alerts_delivery,
        "alerts": enhanced_alerts,
        "slo_recommendations": slo_recommendations,
        "risk_distribution": risk_distribution,
        "top_tools": top_tools,
    }
