"""
Episode 记忆服务层 — 管理 agent_episodes 表，存储跨对话的关键事件片段。

Episode 是比单条消息更高层的记忆单元，包含主题、摘要和关联消息列表。
主要由 core/agent_memory.py 调用写入，本服务提供查询接口。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def save_episode(
    user_id: int,
    role: str,
    topic: str,
    summary: str,
    messages: list | None = None,
) -> dict:
    """保存一条 Episode 到 agent_episodes 表。"""
    try:
        from src.database import get_db

        db = await get_db()
        now = datetime.now(timezone.utc).isoformat()
        cursor = await db.execute(
            """
            INSERT INTO agent_episodes
                (user_id, role, topic, summary, messages, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, role, topic, summary, json.dumps(messages or []), now),
        )
        await db.commit()
        row = await db.execute_fetchone(
            "SELECT * FROM agent_episodes WHERE id = ?", (cursor.lastrowid,)
        )
        if row is None:
            return {}
        item = dict(row)
        if isinstance(item.get("messages"), str):
            try:
                item["messages"] = json.loads(item["messages"])
            except Exception:
                item["messages"] = []
        return item
    except Exception:
        logger.exception("save_episode failed user_id=%s role=%s", user_id, role)
        return {}


async def get_episodes(
    user_id: int, role: str | None = None, limit: int = 20
) -> list:
    """获取用户的 Episode 列表，支持按角色过滤，按时间倒序。"""
    try:
        from src.database import get_db

        db = await get_db()
        if role:
            rows = await db.execute_fetchall(
                """
                SELECT * FROM agent_episodes
                WHERE user_id = ? AND role = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (user_id, role, limit),
            )
        else:
            rows = await db.execute_fetchall(
                """
                SELECT * FROM agent_episodes
                WHERE user_id = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (user_id, limit),
            )
        result = []
        for row in rows:
            item = dict(row)
            if isinstance(item.get("messages"), str):
                try:
                    item["messages"] = json.loads(item["messages"])
                except Exception:
                    item["messages"] = []
            result.append(item)
        return result
    except Exception:
        logger.exception("get_episodes failed user_id=%s", user_id)
        return []
