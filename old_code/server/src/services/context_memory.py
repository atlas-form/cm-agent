"""
对话上下文记忆服务层 — 管理 conversation_contexts 表。

存储对话摘要和结构化事实（facts），供后续对话注入使用。
每条对话仅保留一条记录（UPSERT by conversation_id）。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def get_context(conversation_id: str) -> dict | None:
    """获取对话上下文摘要与事实，不存在则返回 None。"""
    try:
        from src.database import get_db

        db = await get_db()
        row = await db.execute_fetchone(
            "SELECT * FROM conversation_contexts WHERE conversation_id = ?",
            (conversation_id,),
        )
        if row is None:
            return None
        item = dict(row)
        if isinstance(item.get("facts"), str):
            try:
                item["facts"] = json.loads(item["facts"])
            except Exception:
                item["facts"] = {}
        return item
    except Exception:
        logger.exception("get_context failed conv=%s", conversation_id)
        return None


async def save_context(
    conversation_id: str,
    summary: str,
    facts: dict | None = None,
) -> dict:
    """插入或更新对话上下文（按 conversation_id 软 UPSERT）。"""
    try:
        from src.database import get_db

        db = await get_db()
        now = datetime.now(timezone.utc).isoformat()
        facts_str = json.dumps(facts or {})

        existing = await db.execute_fetchone(
            """
            SELECT id
            FROM conversation_contexts
            WHERE conversation_id = ?
            ORDER BY updated_at DESC, id DESC
            LIMIT 1
            """,
            (conversation_id,),
        )

        if existing:
            await db.execute(
                """
                UPDATE conversation_contexts
                SET summary = ?, facts = ?, updated_at = ?
                WHERE id = ?
                """,
                (summary, facts_str, now, int(existing["id"])),
            )
        else:
            await db.execute(
                """
                INSERT INTO conversation_contexts
                    (conversation_id, summary, facts, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (conversation_id, summary, facts_str, now),
            )

        await db.commit()
        return await get_context(conversation_id) or {}
    except Exception:
        logger.exception("save_context failed conv=%s", conversation_id)
        return {}
