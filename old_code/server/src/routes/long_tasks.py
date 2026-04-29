"""
长时任务路由 — 提交、查询、流式进度。

端点：
  POST /api/tasks/           提交任务，返回 task_id
  GET  /api/tasks/           列出我的任务
  GET  /api/tasks/{id}       查询任务状态/结果
  GET  /api/tasks/{id}/stream  SSE流式进度（实时）
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Any, Dict, Optional

from src.routes.auth import get_current_user
from src.services.long_task_service import (
    create_task, get_task, list_tasks, run_task_stream,
)

# 确保所有执行器已注册
import src.services.long_task_executors  # noqa: F401

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/tasks", tags=["long_tasks"])


class TaskSubmitRequest(BaseModel):
    task_type: str
    title: str = ""
    params: Dict[str, Any] = {}
    role: str = ""


@router.post("/")
async def submit_task(
    req: TaskSubmitRequest,
    user: dict = Depends(get_current_user),
):
    """提交长时任务，返回任务ID。"""
    title = req.title or f"{req.task_type} 任务"
    task_id = await create_task(
        user_id=user["id"],
        task_type=req.task_type,
        title=title,
        role=req.role,
        input_params=req.params,
    )
    return {"task_id": task_id, "status": "pending"}


@router.get("/")
async def list_my_tasks(user: dict = Depends(get_current_user)):
    """列出当前用户的任务（最近20条）。"""
    tasks = await list_tasks(user["id"])
    return {"tasks": tasks}


@router.get("/{task_id}")
async def get_task_status(task_id: int, user: dict = Depends(get_current_user)):
    """查询任务状态/结果。"""
    task = await get_task(task_id, user["id"])
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.get("/{task_id}/stream")
async def stream_task(task_id: int, user: dict = Depends(get_current_user)):
    """
    SSE流式执行任务，实时推送进度和结果。

    流式事件格式：
        data: {"type": "progress", "pct": 30, "step": "正在分析..."}
        data: {"type": "result", "data": {...}}
        data: {"type": "error", "message": "..."}
        data: {"type": "done"}
    """
    task = await get_task(task_id, user["id"])
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    if task["status"] in ("completed", "failed"):
        # 已完成，直接返回结果
        async def _done_gen():
            if task["status"] == "completed" and task.get("result"):
                yield f"data: {json.dumps({'type': 'result', 'data': task['result']}, ensure_ascii=False)}\n\n"
            elif task["status"] == "failed":
                yield f"data: {json.dumps({'type': 'error', 'message': task.get('error_message', '')}, ensure_ascii=False)}\n\n"
            yield "data: {\"type\": \"done\"}\n\n"

        return StreamingResponse(_done_gen(), media_type="text/event-stream")

    async def _stream_gen():
        async for chunk in run_task_stream(
            task_id=task_id,
            user_id=user["id"],
            task_type=task["task_type"],
            params=task.get("params", {}),
        ):
            yield chunk

    return StreamingResponse(
        _stream_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
