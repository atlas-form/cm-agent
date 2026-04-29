"""
执行确认流水线路由。

AI给出带数值的操作建议 → 写入execution_queue → 用户在前端确认/拒绝
→ 确认后自动调用平台API执行

端点:
    POST /api/execution/suggest       — 写入建议（内部/AI调用）
    GET  /api/execution/pending       — 获取待确认列表
    POST /api/execution/{id}/approve  — 确认并执行
    POST /api/execution/{id}/reject   — 拒绝
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from src.core.audit import log_audit_event
from src.core.rbac import require_workspace_permission
from src.database import get_db
from src.routes.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/execution", tags=["execution"])


class SuggestRequest(BaseModel):
    action_type: str
    description: str
    platform: str = ""
    payload: Dict[str, Any] = {}
    expected_impact: str = ""


class RejectRequest(BaseModel):
    reason: str = ""


def _extract_workspace_id(payload: dict) -> Optional[int]:
    """从执行 payload 中提取工作区上下文。"""
    if not isinstance(payload, dict):
        return None
    raw = payload.get("workspace_id")
    if raw is None and isinstance(payload.get("context"), dict):
        raw = payload["context"].get("workspace_id")
    if raw is None:
        return None
    try:
        return int(raw)
    except Exception:
        return None


async def _authorize_execution_decision(*, row: dict, payload: dict, actor_user_id: int) -> Optional[int]:
    """审批/拒绝 execution_queue 建议时的权限判断。"""
    owner_user_id = int(row["user_id"])
    if owner_user_id == int(actor_user_id):
        return _extract_workspace_id(payload)

    workspace_id = _extract_workspace_id(payload)
    if not workspace_id:
        raise HTTPException(status_code=403, detail="无权限处理该执行建议")

    await require_workspace_permission(
        workspace_id=workspace_id,
        user_id=actor_user_id,
        permission="approve",
        allow_global_admin=True,
    )
    return workspace_id


@router.post("/suggest")
async def suggest_action(req: SuggestRequest, user: dict = Depends(get_current_user)):
    """写入一条执行建议到队列（供 AI pipeline 内部调用）。"""
    db = await get_db()
    async with db.execute(
        """INSERT INTO execution_queue
               (user_id, action_type, description, platform, payload, expected_impact, status)
           VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
        (
            user["id"],
            req.action_type,
            req.description,
            req.platform,
            json.dumps(req.payload, ensure_ascii=False),
            req.expected_impact,
        ),
    ) as cur:
        eid = cur.lastrowid
    await db.commit()

    # 写入 SSE background_events 让前端感知
    await db.execute(
        """INSERT INTO background_events (user_id, event_type, data)
           VALUES (?, 'execution_suggested', ?)""",
        (
            user["id"],
            json.dumps({
                "id": eid,
                "action_type": req.action_type,
                "description": req.description,
                "platform": req.platform,
                "expected_impact": req.expected_impact,
            }, ensure_ascii=False),
        ),
    )
    await db.commit()
    return {"id": eid, "status": "pending"}


@router.get("/pending")
async def get_pending(user: dict = Depends(get_current_user)):
    """获取当前用户的待确认执行建议列表。"""
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT id, action_type, description, platform, payload,
                  expected_impact, status, created_at
           FROM execution_queue
           WHERE user_id = ? AND status = 'pending'
           ORDER BY created_at DESC LIMIT 20""",
        (user["id"],),
    )
    items = []
    for r in rows:
        payload = {}
        try:
            payload = json.loads(r["payload"] or "{}")
        except Exception:
            pass
        items.append({
            "id": r["id"],
            "action_type": r["action_type"],
            "description": r["description"],
            "platform": r["platform"],
            "payload": payload,
            "expected_impact": r["expected_impact"],
            "status": r["status"],
            "created_at": r["created_at"],
        })
    return {"items": items}


@router.post("/{execution_id}/approve")
async def approve_action(execution_id: int, user: dict = Depends(get_current_user)):
    """确认执行建议 — 调用平台API执行，更新状态为 executed。"""
    db = await get_db()
    row = await db.execute_fetchone(
        "SELECT * FROM execution_queue WHERE id=? AND status='pending'",
        (execution_id,),
    )
    if not row:
        raise HTTPException(404, "执行建议不存在或已处理")

    action_type = row["action_type"]
    platform = row["platform"]
    payload = {}
    try:
        payload = json.loads(row["payload"] or "{}")
    except Exception:
        pass

    workspace_id = await _authorize_execution_decision(
        row=dict(row),
        payload=payload if isinstance(payload, dict) else {},
        actor_user_id=user["id"],
    )

    owner_user_id = int(row["user_id"])

    # 调用平台API（使用建议所有者的上下文）
    exec_result = await _execute_action(owner_user_id, action_type, platform, payload)

    new_status = "executed" if exec_result.get("success") else "failed"
    await db.execute(
        "UPDATE execution_queue SET status=?, executed_at=CURRENT_TIMESTAMP WHERE id=?",
        (new_status, execution_id),
    )
    await db.execute(
        """INSERT INTO background_events (user_id, event_type, data)
           VALUES (?, 'execution_completed', ?)""",
        (
            owner_user_id,
            json.dumps(
                {
                    "id": execution_id,
                    "status": new_status,
                    "message": exec_result.get("message", ""),
                },
                ensure_ascii=False,
            ),
        ),
    )
    await log_audit_event(
        actor_user_id=user["id"],
        workspace_id=workspace_id,
        action="execution_queue.approve",
        target_type="execution_queue",
        target_id=execution_id,
        status="success" if new_status == "executed" else "failed",
        reason="" if new_status == "executed" else "execution_failed",
        metadata={
            "owner_user_id": owner_user_id,
            "action_type": action_type,
            "platform": platform,
            "result": exec_result,
        },
        db=db,
    )
    await db.commit()
    return {"id": execution_id, "status": new_status, **exec_result}


@router.post("/{execution_id}/reject")
async def reject_action(
    execution_id: int,
    body: RejectRequest = RejectRequest(),
    user: dict = Depends(get_current_user),
):
    """拒绝执行建议。"""
    db = await get_db()
    row = await db.execute_fetchone(
        "SELECT * FROM execution_queue WHERE id=? AND status='pending'",
        (execution_id,),
    )
    if not row:
        raise HTTPException(404, "执行建议不存在或已处理")

    payload = {}
    try:
        payload = json.loads(row["payload"] or "{}")
    except Exception:
        pass

    workspace_id = await _authorize_execution_decision(
        row=dict(row),
        payload=payload if isinstance(payload, dict) else {},
        actor_user_id=user["id"],
    )

    reject_reason = body.reason or "manual_rejected"
    await db.execute(
        "UPDATE execution_queue SET status='rejected', executed_at=CURRENT_TIMESTAMP WHERE id=?",
        (execution_id,),
    )
    await log_audit_event(
        actor_user_id=user["id"],
        workspace_id=workspace_id,
        action="execution_queue.reject",
        target_type="execution_queue",
        target_id=execution_id,
        status="success",
        reason=reject_reason,
        metadata={
            "owner_user_id": int(row["user_id"]),
            "action_type": row["action_type"],
            "platform": row["platform"],
        },
        db=db,
    )
    await db.commit()
    return {"id": execution_id, "status": "rejected"}


async def _execute_action(user_id: int, action_type: str, platform: str, payload: dict) -> dict:
    """根据 action_type 调用对应的平台适配器。"""
    try:
        if action_type == "update_price":
            from src.services.platform_adapters import get_platform_registry
            import src.database as db_module
            registry = await get_platform_registry(user_id, db_module)
            adapter = registry.get(platform)
            if not adapter:
                return {"success": False, "message": f"平台 {platform} 未配置"}
            result = await adapter.update_price(
                payload.get("product_id", ""),
                float(payload.get("new_price", 0)),
                payload.get("sku_id", ""),
            )
            return {"success": result.success, "message": result.message}

        elif action_type == "update_inventory":
            from src.services.platform_adapters import get_platform_registry
            import src.database as db_module
            registry = await get_platform_registry(user_id, db_module)
            adapter = registry.get(platform)
            if not adapter:
                return {"success": False, "message": f"平台 {platform} 未配置"}
            result = await adapter.update_inventory(
                payload.get("product_id", ""),
                int(payload.get("quantity", 0)),
                payload.get("sku_id", ""),
            )
            return {"success": result.success, "message": result.message}

        else:
            # 未知操作类型 — 返回成功（用于模拟/扩展）
            return {"success": True, "message": f"操作 {action_type} 已标记为已执行（模拟）"}

    except Exception as e:
        logger.exception("执行操作失败: %s", e)
        return {"success": False, "message": str(e)}
