"""
全局事实服务层 — 管理 global_facts 表，存储跨对话的持久化知识点。

每个事实由 (user_id, fact_key) 唯一标识，支持 confidence 置信度评分。
可由 Agent 在对话中自动发现并写入，也可通过 API 手动管理。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def get_facts(user_id: int, limit: int = 50) -> list:
    """获取用户的全局事实列表，按置信度降序。"""
    try:
        from src.database import get_db

        db = await get_db()
        rows = await db.execute_fetchall(
            """
            SELECT * FROM global_facts
            WHERE user_id = ?
            ORDER BY confidence DESC, updated_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [dict(row) for row in rows]
    except Exception:
        logger.exception("get_facts failed user_id=%s", user_id)
        return []


async def save_fact(
    user_id: int,
    key: str,
    value: str,
    confidence: float = 0.5,
    source: str = "",
) -> dict:
    """插入或更新一条全局事实（UPSERT by user_id + fact_key）。"""
    try:
        from src.database import get_db

        db = await get_db()
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            """
            INSERT INTO global_facts
                (user_id, fact_key, fact_value, confidence, source, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id, fact_key) DO UPDATE SET
                fact_value = excluded.fact_value,
                confidence = excluded.confidence,
                source     = excluded.source,
                updated_at = excluded.updated_at
            """,
            (user_id, key, value, confidence, source, now, now),
        )
        await db.commit()
        row = await db.execute_fetchone(
            "SELECT * FROM global_facts WHERE user_id = ? AND fact_key = ?",
            (user_id, key),
        )
        return dict(row) if row else {}
    except Exception:
        logger.exception("save_fact failed user_id=%s key=%s", user_id, key)
        return {}


async def update_fact(
    fact_id: int,
    value: str,
    confidence: float | None = None,
) -> bool:
    """按 ID 更新事实的值和置信度。"""
    try:
        from src.database import get_db

        db = await get_db()
        now = datetime.now(timezone.utc).isoformat()
        if confidence is not None:
            await db.execute(
                "UPDATE global_facts SET fact_value = ?, confidence = ?, updated_at = ? WHERE id = ?",
                (value, confidence, now, fact_id),
            )
        else:
            await db.execute(
                "UPDATE global_facts SET fact_value = ?, updated_at = ? WHERE id = ?",
                (value, now, fact_id),
            )
        await db.commit()
        return True
    except Exception:
        logger.exception("update_fact failed id=%s", fact_id)
        return False
