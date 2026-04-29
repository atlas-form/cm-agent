"""
任务追踪服务层 — 管理 project_tasks 表，提供项目任务的 CRUD 和状态更新。

任务状态流转：pending → in_progress → done | blocked。
支持优先级、依赖关系和验收标准字段。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


async def get_project_tasks(
    project_id: int, status: str | None = None
) -> list:
    """获取项目任务列表，支持按状态过滤，按优先级倒序。"""
    try:
        from src.database import get_db

        db = await get_db()
        if status:
            rows = await db.execute_fetchall(
                """
                SELECT * FROM project_tasks
                WHERE project_id = ? AND status = ?
                ORDER BY priority DESC, created_at ASC
                """,
                (project_id, status),
            )
        else:
            rows = await db.execute_fetchall(
                """
                SELECT * FROM project_tasks
                WHERE project_id = ?
                ORDER BY priority DESC, created_at ASC
                """,
                (project_id,),
            )
        return [dict(row) for row in rows]
    except Exception:
        logger.exception("get_project_tasks failed project_id=%s", project_id)
        return []


async def create_project_task(project_id: int, data: dict) -> dict:
    """为项目创建新任务。"""
    try:
        from src.database import get_db

        db = await get_db()
        now = datetime.now(timezone.utc).isoformat()
        cursor = await db.execute(
            """
            INSERT INTO project_tasks
                (project_id, title, description, owner_role, status, priority,
                 depends_on, acceptance_criteria, result, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?)
            """,
            (
                project_id,
                data.get("title", ""),
                data.get("description", ""),
                data.get("owner_role", ""),
                data.get("status", "pending"),
                data.get("priority", 0),
                data.get("depends_on", ""),
                data.get("acceptance_criteria", ""),
                now,
                now,
            ),
        )
        await db.commit()
        row = await db.execute_fetchone(
            "SELECT * FROM project_tasks WHERE id = ?", (cursor.lastrowid,)
        )
        return dict(row) if row else {}
    except Exception:
        logger.exception("create_project_task failed project_id=%s", project_id)
        return {}


async def update_project_task(task_id: int, data: dict) -> bool:
    """更新任务字段（状态、结果、优先级等）。"""
    try:
        from src.database import get_db

        db = await get_db()
        updatable = ["title", "description", "owner_role", "status",
                     "priority", "depends_on", "acceptance_criteria", "result"]
        sets = []
        vals = []
        for f in updatable:
            if f in data and data[f] is not None:
                sets.append(f"{f} = ?")
                vals.append(data[f])
        if not sets:
            return True
        now = datetime.now(timezone.utc).isoformat()
        sets.append("updated_at = ?")
        vals.append(now)
        vals.append(task_id)
        await db.execute(
            f"UPDATE project_tasks SET {', '.join(sets)} WHERE id = ?", vals
        )
        await db.commit()
        return True
    except Exception:
        logger.exception("update_project_task failed id=%s", task_id)
        return False
