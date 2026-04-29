"""
长时间任务服务 — 支持月度审计、综合诊断等耗时分析任务。

任务生命周期：
  pending → running → completed / failed

进度通过 SSE 实时推送，前端可轮询 /api/tasks/{id} 查询状态。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Any, AsyncIterator, Callable, Dict, List, Optional

from src.database import get_db

logger = logging.getLogger(__name__)

# ── 任务执行器注册表 ─────────────────────────────────────────────────────────
_EXECUTORS: Dict[str, Callable] = {}


def register_executor(task_type: str):
    """装饰器：将异步生成器函数注册为某种任务类型的执行器。"""
    def _deco(fn):
        _EXECUTORS[task_type] = fn
        return fn
    return _deco


# ── DB 操作 ──────────────────────────────────────────────────────────────────

async def create_task(
    user_id: int,
    task_type: str,
    title: str,
    total_steps: int = 100,
    role: str = "",
    input_params: Dict[str, Any] = None,
) -> int:
    """创建长时任务记录，返回任务 ID。"""
    db = await get_db()
    params_json = json.dumps(input_params or {}, ensure_ascii=False)
    async with db.execute(
        """INSERT INTO long_running_tasks
           (user_id, task_type, title, total_steps, role, input_params)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, task_type, title, total_steps, role, params_json),
    ) as cur:
        task_id = cur.lastrowid
    await db.commit()
    return task_id


async def get_task(task_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    """获取任务详情（含权限检查）。"""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT * FROM long_running_tasks WHERE id=? AND user_id=?",
        (task_id, user_id),
    )
    if not rows:
        return None
    row = rows[0]
    keys = [
        "id", "user_id", "task_type", "status", "title",
        "progress", "total_steps", "current_step",
        "result_json", "error_message", "role", "input_params",
        "created_at", "started_at", "completed_at",
    ]
    d = dict(zip(keys, row))
    if d.get("result_json"):
        try:
            d["result"] = json.loads(d["result_json"])
        except Exception:
            d["result"] = None
    if d.get("input_params"):
        try:
            d["params"] = json.loads(d["input_params"])
        except Exception:
            d["params"] = {}
    return d


async def list_tasks(user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    """列出用户的任务（最近的）。"""
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT id, task_type, status, title, progress, total_steps,
                  current_step, role, created_at, completed_at
           FROM long_running_tasks
           WHERE user_id=?
           ORDER BY created_at DESC LIMIT ?""",
        (user_id, limit),
    )
    keys = [
        "id", "task_type", "status", "title", "progress", "total_steps",
        "current_step", "role", "created_at", "completed_at",
    ]
    return [dict(zip(keys, row)) for row in rows]


async def _update_progress(
    task_id: int, progress: int, current_step: str = ""
) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE long_running_tasks SET progress=?, current_step=? WHERE id=?",
        (progress, current_step, task_id),
    )
    await db.commit()


async def _mark_running(task_id: int) -> None:
    db = await get_db()
    await db.execute(
        "UPDATE long_running_tasks SET status='running', started_at=? WHERE id=?",
        (datetime.utcnow().isoformat(), task_id),
    )
    await db.commit()


async def _mark_completed(task_id: int, result: Dict[str, Any]) -> None:
    db = await get_db()
    await db.execute(
        """UPDATE long_running_tasks
           SET status='completed', progress=100, result_json=?, completed_at=?
           WHERE id=?""",
        (json.dumps(result, ensure_ascii=False), datetime.utcnow().isoformat(), task_id),
    )
    await db.commit()


async def _mark_failed(task_id: int, error: str) -> None:
    db = await get_db()
    await db.execute(
        """UPDATE long_running_tasks
           SET status='failed', error_message=?, completed_at=?
           WHERE id=?""",
        (error[:2000], datetime.utcnow().isoformat(), task_id),
    )
    await db.commit()


# ── 任务执行入口 ──────────────────────────────────────────────────────────────

async def run_task_stream(
    task_id: int,
    user_id: int,
    task_type: str,
    params: Dict[str, Any],
) -> AsyncIterator[str]:
    """
    执行长时任务，以 SSE 格式 yield 进度和结果。

    前端事件格式：
        data: {"type": "progress", "step": "...", "pct": 30}
        data: {"type": "result", "data": {...}}
        data: {"type": "error", "message": "..."}
        data: {"type": "done"}
    """
    executor = _EXECUTORS.get(task_type)
    if not executor:
        yield _sse({"type": "error", "message": f"未知任务类型: {task_type}"})
        await _mark_failed(task_id, f"Unknown task_type: {task_type}")
        return

    await _mark_running(task_id)

    async def _progress_cb(pct: int, step: str = "") -> None:
        await _update_progress(task_id, pct, step)

    try:
        async for event in executor(user_id=user_id, params=params, progress_cb=_progress_cb):
            yield _sse(event)
            if event.get("type") == "result":
                await _mark_completed(task_id, event.get("data", {}))
    except Exception as e:
        logger.exception("Long task %s failed: %s", task_id, e)
        err = str(e)
        await _mark_failed(task_id, err)
        yield _sse({"type": "error", "message": err})
    finally:
        yield _sse({"type": "done"})


def _sse(data: Dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
