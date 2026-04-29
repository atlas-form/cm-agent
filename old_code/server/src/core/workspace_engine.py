"""
工作区协作引擎 — Workspace范围的记忆、任务与跨角色上下文，0 LLM 调用。

负责：
- workspace_memory 表：读写工作区记忆事实
- workspace_tasks 表：任务 CRUD 与状态流转
- role_shared_context 表：跨角色洞察共享
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 允许的任务状态集合，防止写入脏数据
_VALID_TASK_STATUSES = frozenset({"pending", "in_progress", "done", "blocked", "cancelled"})

# 记忆类型白名单
_VALID_MEMORY_TYPES = frozenset({"fact", "insight", "constraint", "goal", "risk"})


# ═══════════════════════════════════════════════════════════════════════════
# 工作区上下文
# ═══════════════════════════════════════════════════════════════════════════

async def get_workspace_context(workspace_id: int) -> Dict[str, Any]:
    """
    加载指定工作区的完整协作上下文。

    返回最近记忆（最多20条）、进行中任务（最多10条）、最近共享上下文（最多10条）。

    Returns
    -------
    dict
        {memories: list, tasks: list, shared_context: list}
    """
    try:
        from src.database import get_db
        db = await get_db()

        # 工作区记忆：按创建时间倒序，排除已过期
        mem_rows = await db.execute_fetchall(
            """SELECT id, role, key, content, memory_type, created_at
               FROM workspace_memory
               WHERE workspace_id = ?
                 AND (expires_at IS NULL OR expires_at > datetime('now'))
               ORDER BY created_at DESC
               LIMIT 20""",
            (workspace_id,),
        )
        memories = [
            {
                "id": r["id"],
                "role": r["role"],
                "key": r["key"],
                "content": r["content"],
                "memory_type": r["memory_type"],
                "created_at": r["created_at"],
            }
            for r in (mem_rows or [])
        ]

        # 活跃任务：pending / in_progress / blocked
        task_rows = await db.execute_fetchall(
            """SELECT id, title, description, owner_role, status, priority,
                      acceptance_criteria, result, created_at, updated_at
               FROM workspace_tasks
               WHERE workspace_id = ?
                 AND status IN ('pending', 'in_progress', 'blocked')
               ORDER BY priority DESC, created_at ASC
               LIMIT 10""",
            (workspace_id,),
        )
        tasks = [
            {
                "id": r["id"],
                "title": r["title"],
                "description": r["description"],
                "owner_role": r["owner_role"],
                "status": r["status"],
                "priority": r["priority"],
                "acceptance_criteria": r["acceptance_criteria"],
                "result": r["result"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in (task_rows or [])
        ]

        # 跨角色共享上下文
        ctx_rows = await db.execute_fetchall(
            """SELECT id, from_role, to_role, content, created_at
               FROM role_shared_context
               WHERE workspace_id = ?
               ORDER BY created_at DESC
               LIMIT 10""",
            (workspace_id,),
        )
        shared_context = [
            {
                "id": r["id"],
                "from_role": r["from_role"],
                "to_role": r["to_role"],
                "content": r["content"],
                "created_at": r["created_at"],
            }
            for r in (ctx_rows or [])
        ]

        return {
            "memories": memories,
            "tasks": tasks,
            "shared_context": shared_context,
        }

    except Exception as e:
        logger.warning("get_workspace_context(%d) failed: %s", workspace_id, e)
        return {"memories": [], "tasks": [], "shared_context": []}


# ═══════════════════════════════════════════════════════════════════════════
# 工作区记忆
# ═══════════════════════════════════════════════════════════════════════════

async def save_workspace_memory(
    workspace_id: int,
    role: str,
    key: str,
    content: str,
    memory_type: str = "fact",
) -> int:
    """
    保存一条事实/洞察到工作区记忆。

    若同一 workspace_id + role + key 已存在记录，则覆盖 content 与 memory_type
    （UPSERT 语义），避免重复堆积。

    Returns
    -------
    int
        插入或更新后的记录 ID；失败返回 -1。
    """
    if not key or not content:
        logger.debug("save_workspace_memory: empty key or content, skipped")
        return -1

    # 归一化 memory_type
    mem_type = memory_type if memory_type in _VALID_MEMORY_TYPES else "fact"

    try:
        from src.database import get_db
        db = await get_db()

        # 先查是否已有相同 key
        existing = await db.execute_fetchone(
            """SELECT id FROM workspace_memory
               WHERE workspace_id = ? AND role = ? AND key = ?""",
            (workspace_id, role, key),
        )
        if existing:
            await db.execute(
                """UPDATE workspace_memory
                   SET content = ?, memory_type = ?, created_at = datetime('now')
                   WHERE id = ?""",
                (content, mem_type, existing["id"]),
            )
            await db.commit()
            return int(existing["id"])

        cursor = await db.execute(
            """INSERT INTO workspace_memory (workspace_id, role, key, content, memory_type)
               VALUES (?, ?, ?, ?, ?)""",
            (workspace_id, role, key, content, mem_type),
        )
        await db.commit()
        return cursor.lastrowid or -1

    except Exception as e:
        logger.warning("save_workspace_memory(%d, %s, %s) failed: %s", workspace_id, role, key, e)
        return -1


# ═══════════════════════════════════════════════════════════════════════════
# 工作区任务
# ═══════════════════════════════════════════════════════════════════════════

async def get_workspace_tasks(workspace_id: int, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    获取工作区任务列表，可选按状态过滤。

    Parameters
    ----------
    workspace_id : int
        目标工作区 ID。
    status : str | None
        可选的状态过滤，如 "pending"、"done" 等。

    Returns
    -------
    list[dict]
        按优先级倒序、创建时间正序排列的任务列表。
    """
    try:
        from src.database import get_db
        db = await get_db()

        if status and status in _VALID_TASK_STATUSES:
            rows = await db.execute_fetchall(
                """SELECT id, title, description, owner_role, status, priority,
                          acceptance_criteria, result, created_at, updated_at
                   FROM workspace_tasks
                   WHERE workspace_id = ? AND status = ?
                   ORDER BY priority DESC, created_at ASC""",
                (workspace_id, status),
            )
        else:
            rows = await db.execute_fetchall(
                """SELECT id, title, description, owner_role, status, priority,
                          acceptance_criteria, result, created_at, updated_at
                   FROM workspace_tasks
                   WHERE workspace_id = ?
                   ORDER BY priority DESC, created_at ASC""",
                (workspace_id,),
            )

        return [
            {
                "id": r["id"],
                "title": r["title"],
                "description": r["description"],
                "owner_role": r["owner_role"],
                "status": r["status"],
                "priority": r["priority"],
                "acceptance_criteria": r["acceptance_criteria"],
                "result": r["result"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
            }
            for r in (rows or [])
        ]

    except Exception as e:
        logger.warning("get_workspace_tasks(%d) failed: %s", workspace_id, e)
        return []


async def create_workspace_task(
    workspace_id: int,
    title: str,
    owner_role: str = "",
    priority: int = 0,
    description: str = "",
    acceptance_criteria: str = "",
) -> Dict[str, Any]:
    """
    在工作区中创建一个新任务。

    priority 数值越大越优先（建议范围 0–10）。

    Returns
    -------
    dict
        新建任务完整信息；若失败则返回含 "error" 键的 dict。
    """
    if not title or not title.strip():
        return {"error": "title 不能为空"}

    # priority 范围夹紧
    priority = max(0, min(10, priority))

    try:
        from src.database import get_db
        db = await get_db()

        cursor = await db.execute(
            """INSERT INTO workspace_tasks
               (workspace_id, title, description, owner_role, status, priority, acceptance_criteria)
               VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
            (workspace_id, title.strip(), description, owner_role, priority, acceptance_criteria),
        )
        await db.commit()

        row = await db.execute_fetchone(
            """SELECT id, title, description, owner_role, status, priority,
                      acceptance_criteria, result, created_at, updated_at
               FROM workspace_tasks WHERE id = ?""",
            (cursor.lastrowid,),
        )
        if row:
            return dict(row)
        return {"error": "插入后未能查到记录"}

    except Exception as e:
        logger.warning("create_workspace_task(%d, %s) failed: %s", workspace_id, title, e)
        return {"error": str(e)}


async def update_workspace_task(
    task_id: int,
    status: Optional[str] = None,
    result: Optional[str] = None,
) -> bool:
    """
    更新任务状态和/或执行结果。

    只有传入非 None 的参数才会被更新，避免意外清空字段。

    Returns
    -------
    bool
        更新是否成功。
    """
    if status is None and result is None:
        return False

    # 校验状态合法性
    if status is not None and status not in _VALID_TASK_STATUSES:
        logger.warning("update_workspace_task: invalid status '%s'", status)
        return False

    try:
        from src.database import get_db
        db = await get_db()

        if status is not None and result is not None:
            await db.execute(
                """UPDATE workspace_tasks
                   SET status = ?, result = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (status, result, task_id),
            )
        elif status is not None:
            await db.execute(
                """UPDATE workspace_tasks
                   SET status = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (status, task_id),
            )
        else:
            await db.execute(
                """UPDATE workspace_tasks
                   SET result = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (result, task_id),
            )

        await db.commit()
        return True

    except Exception as e:
        logger.warning("update_workspace_task(%d) failed: %s", task_id, e)
        return False


# ═══════════════════════════════════════════════════════════════════════════
# 跨角色共享上下文
# ═══════════════════════════════════════════════════════════════════════════

async def get_shared_context(workspace_id: int, role: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    获取工作区内角色间共享的洞察。

    Parameters
    ----------
    workspace_id : int
        目标工作区 ID。
    role : str | None
        若指定，则只返回发给该角色（to_role）的条目；
        None 表示返回全部。

    Returns
    -------
    list[dict]
        按时间倒序排列，最多50条。
    """
    try:
        from src.database import get_db
        db = await get_db()

        if role:
            rows = await db.execute_fetchall(
                """SELECT id, from_role, to_role, content, created_at
                   FROM role_shared_context
                   WHERE workspace_id = ? AND to_role = ?
                   ORDER BY created_at DESC
                   LIMIT 50""",
                (workspace_id, role),
            )
        else:
            rows = await db.execute_fetchall(
                """SELECT id, from_role, to_role, content, created_at
                   FROM role_shared_context
                   WHERE workspace_id = ?
                   ORDER BY created_at DESC
                   LIMIT 50""",
                (workspace_id,),
            )

        return [
            {
                "id": r["id"],
                "from_role": r["from_role"],
                "to_role": r["to_role"],
                "content": r["content"],
                "created_at": r["created_at"],
            }
            for r in (rows or [])
        ]

    except Exception as e:
        logger.warning("get_shared_context(%d, %s) failed: %s", workspace_id, role, e)
        return []


async def share_context(
    workspace_id: int,
    from_role: str,
    to_role: str,
    content: str,
) -> int:
    """
    将一条洞察从 from_role 分享给 to_role。

    Returns
    -------
    int
        新插入记录的 ID；失败返回 -1。
    """
    if not content or not content.strip():
        return -1
    if not from_role or not to_role:
        return -1

    try:
        from src.database import get_db
        db = await get_db()

        cursor = await db.execute(
            """INSERT INTO role_shared_context (workspace_id, from_role, to_role, content)
               VALUES (?, ?, ?, ?)""",
            (workspace_id, from_role, to_role, content.strip()),
        )
        await db.commit()
        return cursor.lastrowid or -1

    except Exception as e:
        logger.warning("share_context(%d, %s→%s) failed: %s", workspace_id, from_role, to_role, e)
        return -1
