"""
跨角色协作服务层 — 管理 handoff_queue 表，实现 Agent 间的任务移交。

handoff 工作流：create_handoff() → pending → complete_handoff() → done。
接收方 Agent 通过 get_pending_handoffs() 轮询待处理的移交请求。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def create_handoff(
    from_role: str,
    to_role: str,
    context: dict,
) -> dict:
    """创建一条跨角色任务移交记录，状态为 pending。"""
    try:
        from src.database import get_db

        db = await get_db()
        now = datetime.now(timezone.utc).isoformat()
        cursor = await db.execute(
            """
            INSERT INTO handoff_queue (from_role, to_role, context, status, created_at)
            VALUES (?, ?, ?, 'pending', ?)
            """,
            (from_role, to_role, json.dumps(context), now),
        )
        await db.commit()
        row = await db.execute_fetchone(
            "SELECT * FROM handoff_queue WHERE id = ?", (cursor.lastrowid,)
        )
        if row is None:
            return {}
        item = dict(row)
        if isinstance(item.get("context"), str):
            try:
                item["context"] = json.loads(item["context"])
            except Exception:
                item["context"] = {}
        return item
    except Exception:
        logger.exception("create_handoff failed from=%s to=%s", from_role, to_role)
        return {}


async def get_pending_handoffs(to_role: str) -> list:
    """获取目标角色的所有待处理移交请求。"""
    try:
        from src.database import get_db

        db = await get_db()
        rows = await db.execute_fetchall(
            """
            SELECT * FROM handoff_queue
            WHERE to_role = ? AND status = 'pending'
            ORDER BY created_at ASC
            """,
            (to_role,),
        )
        result = []
        for row in rows:
            item = dict(row)
            if isinstance(item.get("context"), str):
                try:
                    item["context"] = json.loads(item["context"])
                except Exception:
                    item["context"] = {}
            result.append(item)
        return result
    except Exception:
        logger.exception("get_pending_handoffs failed to_role=%s", to_role)
        return []


async def complete_handoff(handoff_id: int) -> bool:
    """将移交记录标记为 done。"""
    try:
        from src.database import get_db

        db = await get_db()
        await db.execute(
            "UPDATE handoff_queue SET status = 'done' WHERE id = ?",
            (handoff_id,),
        )
        await db.commit()
        return True
    except Exception:
        logger.exception("complete_handoff failed id=%s", handoff_id)
        return False
