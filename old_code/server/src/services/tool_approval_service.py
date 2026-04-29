"""Tool approval queue service for chat tool-call guardrails."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Dict, List, Optional

from src.core.audit import log_audit_event
from src.database import get_db


_VALID_APPROVAL_STATUSES: set[str] = {
    "pending",
    "approved",
    "executed",
    "failed",
    "rejected",
}


def _stable_hash(value: Any) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        raw = str(value)
    return hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest()


def _normalize_status(value: str) -> str:
    status = str(value or "").strip().lower()
    return status if status in _VALID_APPROVAL_STATUSES else ""


def _loads_json(raw: Any, fallback: Any) -> Any:
    if isinstance(raw, (dict, list)):
        return raw
    if not isinstance(raw, str):
        return fallback
    try:
        parsed = json.loads(raw)
        return parsed
    except Exception:
        return fallback


def _row_to_item(row: Any) -> Dict[str, Any]:
    item = dict(row)
    item["args"] = _loads_json(item.get("args_json"), {})
    item["result"] = _loads_json(item.get("result_json"), {})
    return {
        "id": int(item.get("id") or 0),
        "user_id": int(item.get("user_id") or 0),
        "workspace_id": int(item.get("workspace_id") or 0),
        "conversation_id": str(item.get("conversation_id") or ""),
        "request_role": str(item.get("request_role") or ""),
        "skill_name": str(item.get("skill_name") or ""),
        "tool_call_id": str(item.get("tool_call_id") or ""),
        "risk_level": str(item.get("risk_level") or "L1"),
        "status": str(item.get("status") or "pending"),
        "reason": str(item.get("reason") or ""),
        "action_key": str(item.get("action_key") or ""),
        "args": item.get("args") if isinstance(item.get("args"), dict) else {},
        "result": item.get("result") if isinstance(item.get("result"), dict) else {},
        "approved_by": int(item.get("approved_by") or 0) if item.get("approved_by") is not None else None,
        "approved_at": item.get("approved_at"),
        "executed_at": item.get("executed_at"),
        "created_at": item.get("created_at"),
        "updated_at": item.get("updated_at"),
    }


async def create_tool_approval_request(
    *,
    user_id: int,
    workspace_id: int,
    conversation_id: Optional[str],
    request_role: str,
    skill_name: str,
    tool_call_id: str,
    risk_level: str,
    args: Dict[str, Any],
    db=None,
) -> Dict[str, Any]:
    conn = db or await get_db()
    normalized_args = args if isinstance(args, dict) else {}
    args_json = json.dumps(normalized_args, ensure_ascii=False, sort_keys=True, default=str)
    args_hash = _stable_hash(normalized_args)
    action_key = _stable_hash(
        f"{int(user_id)}:{int(workspace_id)}:{str(conversation_id or '').strip()}:{str(skill_name or '').strip().lower()}:{args_hash}"
    )

    existing = await conn.execute_fetchone(
        """
        SELECT *
        FROM tool_approval_queue
        WHERE action_key = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (action_key,),
    )
    if existing:
        return {
            "id": int(existing["id"]),
            "deduped": True,
            "status": str(existing["status"] or ""),
            "action_key": action_key,
        }

    try:
        cur = await conn.execute(
            """
            INSERT INTO tool_approval_queue
            (user_id, workspace_id, conversation_id, request_role, skill_name, tool_call_id, risk_level, args_json, status, reason, action_key, result_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', '', ?, '{}')
            """,
            (
                int(user_id),
                int(workspace_id),
                str(conversation_id or "").strip(),
                str(request_role or "").strip().lower(),
                str(skill_name or "").strip(),
                str(tool_call_id or "").strip(),
                str(risk_level or "L1").strip().upper(),
                args_json,
                action_key,
            ),
        )
    except sqlite3.IntegrityError:
        existing = await conn.execute_fetchone(
            """
            SELECT id, status
            FROM tool_approval_queue
            WHERE action_key = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (action_key,),
        )
        if not existing:
            raise
        return {
            "id": int(existing["id"]),
            "deduped": True,
            "status": str(existing["status"] or ""),
            "action_key": action_key,
        }

    if db is None:
        await conn.commit()

    return {
        "id": int(cur.lastrowid),
        "deduped": False,
        "status": "pending",
        "action_key": action_key,
    }


async def list_tool_approval_requests(
    *,
    workspace_id: int,
    status: str = "",
    limit: int = 50,
    db=None,
) -> List[Dict[str, Any]]:
    conn = db or await get_db()
    status_norm = _normalize_status(status)
    params: List[Any] = [int(workspace_id)]
    where = "workspace_id = ?"
    if status_norm:
        where += " AND status = ?"
        params.append(status_norm)

    rows = await conn.execute_fetchall(
        f"""
        SELECT *
        FROM tool_approval_queue
        WHERE {where}
        ORDER BY id DESC
        LIMIT ?
        """,
        tuple([*params, max(1, min(int(limit), 500))]),
    )
    return [_row_to_item(r) for r in rows]


async def approve_tool_approval_request(
    *,
    workspace_id: int,
    approval_id: int,
    approver_user_id: int,
    db=None,
) -> Dict[str, Any]:
    conn = db or await get_db()
    row = await conn.execute_fetchone(
        """
        SELECT *
        FROM tool_approval_queue
        WHERE id = ? AND workspace_id = ?
        """,
        (int(approval_id), int(workspace_id)),
    )
    if not row:
        raise ValueError("审批记录不存在")
    current_status = str(row["status"] or "")
    if current_status != "pending":
        raise ValueError("该审批记录不是待审批状态")

    args_obj = _loads_json(row["args_json"], {})
    if not isinstance(args_obj, dict):
        args_obj = {}

    update_cur = await conn.execute(
        """
        UPDATE tool_approval_queue
        SET status = 'approved', approved_by = ?, approved_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND workspace_id = ? AND status = 'pending'
        """,
        (int(approver_user_id), int(approval_id), int(workspace_id)),
    )
    if int(getattr(update_cur, "rowcount", 0) or 0) <= 0:
        latest = await conn.execute_fetchone(
            "SELECT status FROM tool_approval_queue WHERE id = ? AND workspace_id = ?",
            (int(approval_id), int(workspace_id)),
        )
        latest_status = str(latest["status"] or "unknown") if latest else "unknown"
        raise ValueError(f"该审批记录已被处理，当前状态: {latest_status}")

    from src.skills.registry import get_registry

    exec_result: Dict[str, Any]
    exec_status = "executed"
    exec_reason = ""
    try:
        reg = get_registry()
        context = {
            "user_id": int(row["user_id"]),
            "workspace_id": int(row["workspace_id"]),
            "role": str(row["request_role"] or "ops"),
            "conversation_id": str(row["conversation_id"] or ""),
            "_approval_replay": True,
            "_tool_risk_level": str(row["risk_level"] or "L2"),
        }
        result_raw = await reg.execute(str(row["skill_name"]), args_obj, context=context)
        if isinstance(result_raw, dict):
            exec_result = result_raw
        else:
            exec_result = {"data": result_raw}
        if "error" in exec_result:
            exec_status = "failed"
            exec_reason = "skill_error"
    except Exception as exc:
        exec_result = {"error": str(exc)}
        exec_status = "failed"
        exec_reason = "skill_exception"

    await conn.execute(
        """
        UPDATE tool_approval_queue
        SET status = ?, reason = ?, result_json = ?, executed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
        WHERE id = ? AND status = 'approved'
        """,
        (
            exec_status,
            exec_reason,
            json.dumps(exec_result, ensure_ascii=False, default=str),
            int(approval_id),
        ),
    )

    await log_audit_event(
        actor_user_id=int(approver_user_id),
        workspace_id=int(workspace_id),
        action="workspace.tool_approval.approve",
        target_type="tool_approval",
        target_id=int(approval_id),
        status="success" if exec_status == "executed" else "failed",
        reason=exec_reason,
        metadata={
            "request_user_id": int(row["user_id"]),
            "skill_name": str(row["skill_name"]),
            "risk_level": str(row["risk_level"] or ""),
            "action_key": str(row["action_key"] or ""),
            "conversation_id": str(row["conversation_id"] or ""),
            "tool_call_id": str(row["tool_call_id"] or ""),
            "args_hash": _stable_hash(args_obj),
            "result_hash": _stable_hash(exec_result),
        },
        db=conn,
    )

    if db is None:
        await conn.commit()

    return {
        "approval_id": int(approval_id),
        "status": exec_status,
        "reason": exec_reason,
        "result": exec_result,
    }


async def reject_tool_approval_request(
    *,
    workspace_id: int,
    approval_id: int,
    approver_user_id: int,
    reason: str = "",
    db=None,
) -> Dict[str, Any]:
    conn = db or await get_db()
    row = await conn.execute_fetchone(
        """
        SELECT *
        FROM tool_approval_queue
        WHERE id = ? AND workspace_id = ?
        """,
        (int(approval_id), int(workspace_id)),
    )
    if not row:
        raise ValueError("审批记录不存在")
    if str(row["status"] or "") != "pending":
        raise ValueError("该审批记录不是待审批状态")

    reject_reason = str(reason or "manual_rejected").strip() or "manual_rejected"
    await conn.execute(
        """
        UPDATE tool_approval_queue
        SET status = 'rejected', reason = ?, approved_by = ?, approved_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (reject_reason, int(approver_user_id), int(approval_id)),
    )

    await log_audit_event(
        actor_user_id=int(approver_user_id),
        workspace_id=int(workspace_id),
        action="workspace.tool_approval.reject",
        target_type="tool_approval",
        target_id=int(approval_id),
        status="success",
        reason=reject_reason,
        metadata={
            "request_user_id": int(row["user_id"]),
            "skill_name": str(row["skill_name"]),
            "risk_level": str(row["risk_level"] or ""),
            "action_key": str(row["action_key"] or ""),
            "conversation_id": str(row["conversation_id"] or ""),
            "tool_call_id": str(row["tool_call_id"] or ""),
        },
        db=conn,
    )

    if db is None:
        await conn.commit()

    return {
        "approval_id": int(approval_id),
        "status": "rejected",
        "reason": reject_reason,
    }
