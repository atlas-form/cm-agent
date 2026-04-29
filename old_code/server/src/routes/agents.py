"""Agent管理路由 — Agent市场 + 激活 + 雇佣记录。"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException

from src.config import ENABLE_ENGINEERING_AGENT
from src.database import get_db
from src.models import AgentActivateRequest
from src.routes.auth import get_current_user

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("")
async def list_agents(user: dict = Depends(get_current_user)):
    """列出所有Agent（公开目录）。"""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT id, name, display_name, role, description, avatar, enabled FROM agents ORDER BY id"
    )
    agents = []
    for r in rows:
        if not ENABLE_ENGINEERING_AGENT and r["role"] == "engineering":
            continue
        agents.append(
            {
                "id": r["id"],
                "name": r["name"],
                "display_name": r["display_name"],
                "role": r["role"],
                "description": r["description"],
                "avatar": r["avatar"],
                "enabled": bool(r["enabled"]),
            }
        )
    return {"agents": agents}


@router.get("/active")
async def get_active_agent(user: dict = Depends(get_current_user)):
    """获取当前用户激活的Agent。"""
    db = await get_db()
    user_row = await db.execute_fetchone(
        "SELECT active_agent FROM users WHERE id = ?", (user["id"],)
    )
    if not user_row:
        raise HTTPException(status_code=404, detail="用户不存在")

    active_name = user_row["active_agent"] or "ops"
    if not ENABLE_ENGINEERING_AGENT and active_name == "engineering":
        active_name = "ops"
    agent_row = await db.execute_fetchone(
        "SELECT id, name, display_name, role, description, avatar, enabled FROM agents WHERE name = ?",
        (active_name,),
    )
    if not agent_row:
        return {"active_agent": active_name, "agent": None}

    return {
        "active_agent": active_name,
        "agent": {
            "id": agent_row["id"],
            "name": agent_row["name"],
            "display_name": agent_row["display_name"],
            "role": agent_row["role"],
            "description": agent_row["description"],
            "avatar": agent_row["avatar"],
            "enabled": bool(agent_row["enabled"]),
        },
    }


@router.post("/activate")
async def activate_agent(req: AgentActivateRequest, user: dict = Depends(get_current_user)):
    """切换用户激活的Agent。"""
    if not ENABLE_ENGINEERING_AGENT and req.agent_name == "engineering":
        raise HTTPException(status_code=400, detail="技术工程师Agent暂未开放")

    db = await get_db()
    agent_row = await db.execute_fetchone(
        "SELECT id, name, enabled FROM agents WHERE name = ?", (req.agent_name,)
    )
    if not agent_row:
        raise HTTPException(status_code=404, detail="Agent不存在")
    if not bool(agent_row["enabled"]):
        raise HTTPException(status_code=400, detail="该Agent已禁用")

    await db.execute(
        "UPDATE users SET active_agent = ? WHERE id = ?",
        (req.agent_name, user["id"]),
    )
    # 记录雇佣历史（忽略重复）
    existing_hire = await db.execute_fetchone(
        "SELECT id FROM agent_hires WHERE user_id = ? AND agent_name = ? AND status = 'active'",
        (user["id"], req.agent_name),
    )
    if not existing_hire:
        await db.execute(
            "INSERT INTO agent_hires (user_id, agent_name, status) VALUES (?, ?, 'active')",
            (user["id"], req.agent_name),
        )
    await db.commit()
    return {"ok": True, "active_agent": req.agent_name}


@router.get("/{agent_id}")
async def get_agent(agent_id: int, user: dict = Depends(get_current_user)):
    """获取Agent详情。"""
    db = await get_db()
    row = await db.execute_fetchone(
        "SELECT id, name, display_name, role, description, avatar, enabled, config FROM agents WHERE id = ?",
        (agent_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Agent不存在")
    result = dict(row)
    result["enabled"] = bool(result["enabled"])
    if isinstance(result.get("config"), str):
        try:
            result["config"] = json.loads(result["config"])
        except Exception:
            result["config"] = {}
    return result


@router.get("/me/hires")
async def get_my_hires(user: dict = Depends(get_current_user)):
    """获取用户的雇佣记录。"""
    db = await get_db()
    rows = await db.execute_fetchall(
        """SELECT ah.id, ah.agent_name, ah.hired_at, ah.status,
                  a.display_name, a.role, a.avatar, a.description
           FROM agent_hires ah
           LEFT JOIN agents a ON a.name = ah.agent_name
           WHERE ah.user_id = ?
           ORDER BY ah.hired_at DESC""",
        (user["id"],),
    )
    return {
        "hires": [
            {
                "id": r["id"],
                "agent_name": r["agent_name"],
                "display_name": r["display_name"],
                "role": r["role"],
                "avatar": r["avatar"],
                "description": r["description"],
                "hired_at": r["hired_at"],
                "status": r["status"],
            }
            for r in rows
        ]
    }
