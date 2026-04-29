"""技能路由 — 目录/包信息/质量报告/运行历史/直接调用/反馈。"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.config import ENABLE_ENGINEERING_AGENT
from src.database import get_db
from src.llm_client import call_llm
from src.models import (
    SkillFeedbackRequest,
    SkillRunRequest,
    SkillRunResponse,
    UserSkillPackPublishRequest,
    UserSkillPackRollbackRequest,
    UserSkillPackStatusUpdateRequest,
    UserSkillPackUpsertRequest,
)
from src.routes.auth import get_current_user

router = APIRouter(prefix="/api/skills", tags=["skills"])

_USER_SKILL_STATUS_SET = {"draft", "published", "disabled"}
_USER_SKILL_OUTPUT_MODES = {"text", "json"}
_USER_SKILL_CODE_RE = re.compile(r"[^a-z0-9_]+")
_USER_SKILL_SELECT_FIELDS = (
    "id, user_id, skill_code, display_name, description, category, "
    "system_prompt, prompt_template, input_schema, output_mode, temperature, model, "
    "status, version_no, created_at, updated_at, published_at"
)


def _safe_json_loads(value: Any, *, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return default
    return default


def _safe_json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return "{}"


def _normalize_user_skill_code(raw: str, *, fallback: str = "") -> str:
    base = str(raw or "").strip().lower()
    if not base:
        base = str(fallback or "").strip().lower()
    base = base.replace("-", "_").replace(" ", "_").replace(".", "_")
    base = _USER_SKILL_CODE_RE.sub("_", base)
    base = re.sub(r"_+", "_", base).strip("_")
    if base and base[0].isdigit():
        base = f"s_{base}"
    if len(base) > 80:
        base = base[:80].rstrip("_")
    return base


def _normalize_user_skill_status(raw: str, *, default: str = "draft") -> str:
    token = str(raw or "").strip().lower()
    if token in _USER_SKILL_STATUS_SET:
        return token
    return default


def _normalize_user_skill_output_mode(raw: str, *, default: str = "text") -> str:
    token = str(raw or "").strip().lower()
    if token in _USER_SKILL_OUTPUT_MODES:
        return token
    return default


def _normalize_skill_lookup_code(skill_name: str) -> str:
    token = str(skill_name or "").strip().lower()
    for prefix in ("user.", "custom.", "user_skill.", "user:"):
        if token.startswith(prefix):
            token = token[len(prefix):]
            break
    return _normalize_user_skill_code(token)


def _serialize_user_skill_pack(row: Any) -> Dict[str, Any]:
    schema_obj = _safe_json_loads(row["input_schema"], default={})
    if not isinstance(schema_obj, dict):
        schema_obj = {}

    output_mode = _normalize_user_skill_output_mode(row["output_mode"])

    return {
        "id": int(row["id"]),
        "user_id": int(row["user_id"]),
        "skill_code": str(row["skill_code"] or ""),
        "name": f"user.{str(row['skill_code'] or '')}",
        "display_name": str(row["display_name"] or ""),
        "description": str(row["description"] or ""),
        "category": str(row["category"] or "custom"),
        "system_prompt": str(row["system_prompt"] or ""),
        "prompt_template": str(row["prompt_template"] or ""),
        "input_schema": schema_obj,
        "output_mode": output_mode,
        "temperature": float(row["temperature"] or 0.3),
        "model": str(row["model"] or ""),
        "status": _normalize_user_skill_status(row["status"]),
        "version_no": int(row["version_no"] or 1),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "published_at": row["published_at"],
        "scope": "account",
    }


def _snapshot_user_skill_pack(row: Any) -> Dict[str, Any]:
    pack = _serialize_user_skill_pack(row)
    return {
        "skill_code": pack["skill_code"],
        "display_name": pack["display_name"],
        "description": pack["description"],
        "category": pack["category"],
        "system_prompt": pack["system_prompt"],
        "prompt_template": pack["prompt_template"],
        "input_schema": pack["input_schema"],
        "output_mode": pack["output_mode"],
        "temperature": pack["temperature"],
        "model": pack["model"],
        "status": pack["status"],
        "version_no": pack["version_no"],
    }


async def _get_user_skill_pack_row(db: Any, *, user_id: int, skill_code: str) -> Any:
    return await db.execute_fetchone(
        f"SELECT {_USER_SKILL_SELECT_FIELDS} FROM user_skill_packs WHERE user_id = ? AND skill_code = ?",
        (user_id, skill_code),
    )


async def _lookup_user_skill_for_run(db: Any, *, user_id: int, skill_name: str) -> Any:
    skill_code = _normalize_skill_lookup_code(skill_name)
    if not skill_code:
        return None

    return await db.execute_fetchone(
        f"SELECT {_USER_SKILL_SELECT_FIELDS} FROM user_skill_packs "
        "WHERE user_id = ? AND skill_code = ? AND status = 'published'",
        (user_id, skill_code),
    )


async def _append_user_skill_pack_version(
    db: Any,
    *,
    row: Any,
    change_type: str,
    note: str,
) -> None:
    snapshot = _snapshot_user_skill_pack(row)
    await db.execute(
        """
        INSERT INTO user_skill_pack_versions
            (user_skill_pack_id, user_id, skill_code, version_no, change_type, note, snapshot_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            int(row["id"]),
            int(row["user_id"]),
            str(row["skill_code"] or ""),
            int(row["version_no"] or 1),
            str(change_type or "update"),
            str(note or "")[:300],
            _safe_json_dumps(snapshot),
        ),
    )


def _render_user_skill_prompt(pack: Dict[str, Any], args: Dict[str, Any]) -> str:
    template = str(pack.get("prompt_template") or "").strip()
    args_obj = args if isinstance(args, dict) else {}

    if template:
        rendered = template
        for key, value in args_obj.items():
            placeholder = "{{" + str(key).strip() + "}}"
            if placeholder not in rendered:
                continue
            if isinstance(value, (dict, list)):
                replace_text = _safe_json_dumps(value)
            else:
                replace_text = str(value)
            rendered = rendered.replace(placeholder, replace_text)
    else:
        rendered = "请根据技能输入参数生成可执行结果。"

    args_json = _safe_json_dumps(args_obj)
    return (
        f"{rendered}\n\n"
        "[技能输入参数(JSON)]\n"
        f"{args_json}\n\n"
        "如果有不确定信息，请明确标注假设。"
    )


def _extract_first_json_object(raw_text: str) -> Optional[Dict[str, Any]]:
    text = str(raw_text or "").strip()
    if not text:
        return None

    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        try:
            parsed = json.loads(fenced.group(1))
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

    block = re.search(r"\{[\s\S]*\}", text)
    if block:
        try:
            parsed = json.loads(block.group(0))
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

    return None


async def _execute_user_skill_pack(pack_row: Any, args: Dict[str, Any]) -> Dict[str, Any]:
    pack = _serialize_user_skill_pack(pack_row)

    system_prompt = str(pack.get("system_prompt") or "").strip() or (
        "你正在执行用户定义技能，请严格围绕技能目标给出结构化、可执行结果。"
    )
    user_prompt = _render_user_skill_prompt(pack, args)

    reply = await call_llm(
        system=system_prompt,
        messages=[{"role": "user", "content": user_prompt}],
        model=str(pack.get("model") or "").strip() or None,
        temperature=float(pack.get("temperature") or 0.3),
        max_tokens=1800,
    )
    reply_text = str(reply or "").strip()

    output_mode = _normalize_user_skill_output_mode(pack.get("output_mode"), default="text")
    if output_mode == "json":
        parsed = _extract_first_json_object(reply_text)
        if not isinstance(parsed, dict):
            return {
                "output": reply_text,
                "_skill_scope": "account",
                "_skill_code": pack.get("skill_code", ""),
                "_warning": "json_parse_failed",
            }
        parsed.setdefault("_skill_scope", "account")
        parsed.setdefault("_skill_code", pack.get("skill_code", ""))
        return parsed

    return {
        "output": reply_text,
        "_skill_scope": "account",
        "_skill_code": pack.get("skill_code", ""),
    }


@router.get("")
async def list_skills(user: dict = Depends(get_current_user)):
    """列出所有可用技能（内置 + 账号级已发布）。"""
    from src.skills.registry import get_registry

    registry = get_registry()
    skills = []
    for s in registry.list_all():
        if not ENABLE_ENGINEERING_AGENT and s.category == "engineering":
            continue
        skills.append(
            {
                "name": s.name,
                "display_name": s.display_name,
                "category": s.category,
                "description": s.description,
                "pack_id": registry.get_skill_source(s.name),
                "scope": "builtin",
            }
        )

    db = await get_db()
    rows = await db.execute_fetchall(
        f"SELECT {_USER_SKILL_SELECT_FIELDS} FROM user_skill_packs "
        "WHERE user_id = ? AND status = 'published' ORDER BY updated_at DESC, id DESC",
        (int(user["id"]),),
    )
    for row in rows:
        pack = _serialize_user_skill_pack(row)
        skills.append(
            {
                "name": pack["name"],
                "display_name": pack["display_name"] or pack["skill_code"],
                "category": pack["category"] or "custom",
                "description": pack["description"],
                "pack_id": f"user.{pack['skill_code']}",
                "scope": "account",
                "status": pack["status"],
                "version_no": pack["version_no"],
            }
        )

    return {"skills": skills}


@router.get("/packs")
async def list_skill_packs(user: dict = Depends(get_current_user)):
    """列出技能包（内置 + 账号级草稿/发布包）。"""
    from src.skills.registry import get_registry

    registry = get_registry()
    builtin_packs = list(registry.list_skill_packs())

    db = await get_db()
    rows = await db.execute_fetchall(
        f"SELECT {_USER_SKILL_SELECT_FIELDS} FROM user_skill_packs "
        "WHERE user_id = ? ORDER BY updated_at DESC, id DESC",
        (int(user["id"]),),
    )

    user_packs = []
    for row in rows:
        pack = _serialize_user_skill_pack(row)
        user_packs.append(
            {
                "id": f"user.{pack['skill_code']}",
                "name": pack["display_name"] or pack["skill_code"],
                "version": f"v{pack['version_no']}",
                "status": pack["status"],
                "module": "user_runtime",
                "attr": "dynamic_prompt_skill",
                "manifest_path": "",
                "skill_count": 1,
                "provided_roles": [],
                "scope": "account",
                "skill": pack,
            }
        )

    packs = builtin_packs + user_packs
    return {
        "packs": packs,
        "total": len(packs),
        "builtin_total": len(builtin_packs),
        "user_total": len(user_packs),
    }


@router.get("/packs/{pack_id}")
async def get_skill_pack(pack_id: str, user: dict = Depends(get_current_user)):
    """获取技能包详情。"""
    if str(pack_id).startswith("user."):
        code = _normalize_user_skill_code(str(pack_id).removeprefix("user."))
        if not code:
            raise HTTPException(status_code=404, detail=f"技能包 '{pack_id}' 不存在")

        db = await get_db()
        row = await _get_user_skill_pack_row(db, user_id=int(user["id"]), skill_code=code)
        if not row:
            raise HTTPException(status_code=404, detail=f"技能包 '{pack_id}' 不存在")

        pack = _serialize_user_skill_pack(row)
        return {
            "pack": {
                "id": f"user.{pack['skill_code']}",
                "name": pack["display_name"] or pack["skill_code"],
                "version": f"v{pack['version_no']}",
                "status": pack["status"],
                "module": "user_runtime",
                "attr": "dynamic_prompt_skill",
                "manifest_path": "",
                "skill_count": 1,
                "provided_roles": [],
                "scope": "account",
                "skill": pack,
            }
        }

    from src.skills.registry import get_registry

    registry = get_registry()
    packs = registry.list_skill_packs()
    pack = next((p for p in packs if str(p.get("id")) == pack_id), None)
    if not pack:
        raise HTTPException(status_code=404, detail=f"技能包 '{pack_id}' 不存在")

    return {"pack": pack}


@router.post("/packs/reload")
async def reload_skill_packs(user: dict = Depends(get_current_user)):
    """热重载技能包（无需重启服务）。"""
    from src.skills.registry import get_registry

    registry = get_registry()
    result = registry.reload()
    return {
        "ok": True,
        "skill_count": result["skill_count"],
        "pack_count": result["pack_count"],
    }


@router.get("/user/packs")
async def list_user_skill_packs(
    status: str = Query("all", description="all/draft/published/disabled"),
    limit: int = Query(100, ge=1, le=300),
    user: dict = Depends(get_current_user),
):
    status_token = str(status or "all").strip().lower()
    if status_token != "all" and status_token not in _USER_SKILL_STATUS_SET:
        raise HTTPException(status_code=400, detail="status 仅支持 all/draft/published/disabled")

    db = await get_db()
    if status_token == "all":
        rows = await db.execute_fetchall(
            f"SELECT {_USER_SKILL_SELECT_FIELDS} FROM user_skill_packs "
            "WHERE user_id = ? ORDER BY updated_at DESC, id DESC LIMIT ?",
            (int(user["id"]), int(limit)),
        )
    else:
        rows = await db.execute_fetchall(
            f"SELECT {_USER_SKILL_SELECT_FIELDS} FROM user_skill_packs "
            "WHERE user_id = ? AND status = ? ORDER BY updated_at DESC, id DESC LIMIT ?",
            (int(user["id"]), status_token, int(limit)),
        )

    packs = [_serialize_user_skill_pack(row) for row in rows]
    return {"packs": packs, "count": len(packs)}


@router.post("/user/packs")
async def create_user_skill_pack(req: UserSkillPackUpsertRequest, user: dict = Depends(get_current_user)):
    db = await get_db()
    user_id = int(user["id"])

    skill_code = _normalize_user_skill_code(req.skill_code, fallback=req.display_name)
    if not skill_code:
        raise HTTPException(status_code=400, detail="skill_code 为空或无有效字符")

    exists = await _get_user_skill_pack_row(db, user_id=user_id, skill_code=skill_code)
    if exists:
        raise HTTPException(status_code=409, detail=f"技能包 '{skill_code}' 已存在")

    output_mode = _normalize_user_skill_output_mode(req.output_mode)
    input_schema_json = _safe_json_dumps(req.input_schema if isinstance(req.input_schema, dict) else {})

    await db.execute(
        """
        INSERT INTO user_skill_packs
            (user_id, skill_code, display_name, description, category, system_prompt,
             prompt_template, input_schema, output_mode, temperature, model,
             status, version_no, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """,
        (
            user_id,
            skill_code,
            str(req.display_name or "").strip(),
            str(req.description or ""),
            str(req.category or "custom").strip() or "custom",
            str(req.system_prompt or ""),
            str(req.prompt_template or ""),
            input_schema_json,
            output_mode,
            float(req.temperature),
            str(req.model or ""),
        ),
    )

    row = await _get_user_skill_pack_row(db, user_id=user_id, skill_code=skill_code)
    if not row:
        raise HTTPException(status_code=500, detail="创建后读取技能包失败")

    await _append_user_skill_pack_version(
        db,
        row=row,
        change_type="create",
        note=req.note or "create_draft",
    )
    await db.commit()

    return {"ok": True, "pack": _serialize_user_skill_pack(row)}


@router.put("/user/packs/{skill_code}")
async def update_user_skill_pack(
    skill_code: str,
    req: UserSkillPackUpsertRequest,
    user: dict = Depends(get_current_user),
):
    db = await get_db()
    user_id = int(user["id"])

    code = _normalize_user_skill_code(skill_code)
    if not code:
        raise HTTPException(status_code=400, detail="skill_code 非法")

    row = await _get_user_skill_pack_row(db, user_id=user_id, skill_code=code)
    if not row:
        raise HTTPException(status_code=404, detail=f"技能包 '{code}' 不存在")

    requires_republish = str(row["status"] or "draft").lower() == "published"
    next_status = "draft" if requires_republish else _normalize_user_skill_status(row["status"])
    output_mode = _normalize_user_skill_output_mode(req.output_mode)
    input_schema_json = _safe_json_dumps(req.input_schema if isinstance(req.input_schema, dict) else {})

    await db.execute(
        """
        UPDATE user_skill_packs
        SET display_name = ?,
            description = ?,
            category = ?,
            system_prompt = ?,
            prompt_template = ?,
            input_schema = ?,
            output_mode = ?,
            temperature = ?,
            model = ?,
            status = ?,
            published_at = CASE WHEN ? = 'draft' THEN NULL ELSE published_at END,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            str(req.display_name or "").strip(),
            str(req.description or ""),
            str(req.category or "custom").strip() or "custom",
            str(req.system_prompt or ""),
            str(req.prompt_template or ""),
            input_schema_json,
            output_mode,
            float(req.temperature),
            str(req.model or ""),
            next_status,
            next_status,
            int(row["id"]),
        ),
    )

    updated_row = await _get_user_skill_pack_row(db, user_id=user_id, skill_code=code)
    if not updated_row:
        raise HTTPException(status_code=500, detail="更新后读取技能包失败")

    await _append_user_skill_pack_version(
        db,
        row=updated_row,
        change_type="update",
        note=req.note or ("update_requires_republish" if requires_republish else "update_draft"),
    )
    await db.commit()

    return {
        "ok": True,
        "pack": _serialize_user_skill_pack(updated_row),
        "requires_republish": requires_republish,
    }


@router.post("/user/packs/{skill_code}/publish")
async def publish_user_skill_pack(
    skill_code: str,
    req: UserSkillPackPublishRequest = UserSkillPackPublishRequest(),
    user: dict = Depends(get_current_user),
):
    db = await get_db()
    user_id = int(user["id"])

    code = _normalize_user_skill_code(skill_code)
    if not code:
        raise HTTPException(status_code=400, detail="skill_code 非法")

    row = await _get_user_skill_pack_row(db, user_id=user_id, skill_code=code)
    if not row:
        raise HTTPException(status_code=404, detail=f"技能包 '{code}' 不存在")

    prompt_template = str(row["prompt_template"] or "").strip()
    if not prompt_template:
        raise HTTPException(status_code=400, detail="发布前请先填写 prompt_template")

    next_version = int(row["version_no"] or 1) + 1
    await db.execute(
        """
        UPDATE user_skill_packs
        SET status = 'published',
            version_no = ?,
            published_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (next_version, int(row["id"])),
    )

    updated_row = await _get_user_skill_pack_row(db, user_id=user_id, skill_code=code)
    if not updated_row:
        raise HTTPException(status_code=500, detail="发布后读取技能包失败")

    await _append_user_skill_pack_version(
        db,
        row=updated_row,
        change_type="publish",
        note=req.note or "manual_publish",
    )
    await db.commit()

    return {"ok": True, "pack": _serialize_user_skill_pack(updated_row)}


@router.post("/user/packs/{skill_code}/status")
async def update_user_skill_pack_status(
    skill_code: str,
    req: UserSkillPackStatusUpdateRequest,
    user: dict = Depends(get_current_user),
):
    target_status = _normalize_user_skill_status(req.status, default="")
    if target_status not in _USER_SKILL_STATUS_SET:
        raise HTTPException(status_code=400, detail="status 仅支持 draft/published/disabled")

    if target_status == "published":
        publish_req = UserSkillPackPublishRequest(note=req.note)
        return await publish_user_skill_pack(skill_code=skill_code, req=publish_req, user=user)

    db = await get_db()
    user_id = int(user["id"])
    code = _normalize_user_skill_code(skill_code)
    if not code:
        raise HTTPException(status_code=400, detail="skill_code 非法")

    row = await _get_user_skill_pack_row(db, user_id=user_id, skill_code=code)
    if not row:
        raise HTTPException(status_code=404, detail=f"技能包 '{code}' 不存在")

    if target_status == "draft":
        await db.execute(
            """
            UPDATE user_skill_packs
            SET status = 'draft',
                published_at = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (int(row["id"]),),
        )
    else:
        await db.execute(
            """
            UPDATE user_skill_packs
            SET status = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (target_status, int(row["id"])),
        )

    updated_row = await _get_user_skill_pack_row(db, user_id=user_id, skill_code=code)
    if not updated_row:
        raise HTTPException(status_code=500, detail="状态更新后读取技能包失败")

    await _append_user_skill_pack_version(
        db,
        row=updated_row,
        change_type=f"status_{target_status}",
        note=req.note or f"set_status_{target_status}",
    )
    await db.commit()

    return {"ok": True, "pack": _serialize_user_skill_pack(updated_row)}


@router.get("/user/packs/{skill_code}/versions")
async def list_user_skill_pack_versions(
    skill_code: str,
    limit: int = Query(20, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    db = await get_db()
    user_id = int(user["id"])

    code = _normalize_user_skill_code(skill_code)
    if not code:
        raise HTTPException(status_code=400, detail="skill_code 非法")

    row = await _get_user_skill_pack_row(db, user_id=user_id, skill_code=code)
    if not row:
        raise HTTPException(status_code=404, detail=f"技能包 '{code}' 不存在")

    rows = await db.execute_fetchall(
        """
        SELECT id, user_skill_pack_id, user_id, skill_code, version_no, change_type, note, snapshot_json, created_at
        FROM user_skill_pack_versions
        WHERE user_id = ? AND skill_code = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, code, int(limit)),
    )

    versions = []
    for item in rows:
        snapshot = _safe_json_loads(item["snapshot_json"], default={})
        if not isinstance(snapshot, dict):
            snapshot = {}
        versions.append(
            {
                "id": int(item["id"]),
                "version_no": int(item["version_no"] or 0),
                "change_type": str(item["change_type"] or ""),
                "note": str(item["note"] or ""),
                "snapshot": snapshot,
                "created_at": item["created_at"],
            }
        )

    return {
        "skill_code": code,
        "versions": versions,
        "count": len(versions),
    }


@router.post("/user/packs/{skill_code}/rollback")
async def rollback_user_skill_pack(
    skill_code: str,
    req: UserSkillPackRollbackRequest,
    user: dict = Depends(get_current_user),
):
    db = await get_db()
    user_id = int(user["id"])

    code = _normalize_user_skill_code(skill_code)
    if not code:
        raise HTTPException(status_code=400, detail="skill_code 非法")

    row = await _get_user_skill_pack_row(db, user_id=user_id, skill_code=code)
    if not row:
        raise HTTPException(status_code=404, detail=f"技能包 '{code}' 不存在")

    target = await db.execute_fetchone(
        """
        SELECT id, version_no, snapshot_json
        FROM user_skill_pack_versions
        WHERE user_id = ? AND skill_code = ? AND version_no = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id, code, int(req.version_no)),
    )
    if not target:
        raise HTTPException(status_code=404, detail=f"目标版本 v{int(req.version_no)} 不存在")

    snapshot = _safe_json_loads(target["snapshot_json"], default={})
    if not isinstance(snapshot, dict):
        raise HTTPException(status_code=400, detail="目标版本快照损坏，无法回滚")

    input_schema = snapshot.get("input_schema")
    if not isinstance(input_schema, dict):
        input_schema = _safe_json_loads(row["input_schema"], default={})
        if not isinstance(input_schema, dict):
            input_schema = {}

    output_mode = _normalize_user_skill_output_mode(snapshot.get("output_mode"), default="text")
    try:
        temperature = float(snapshot.get("temperature", row["temperature"] or 0.3))
    except Exception:
        temperature = 0.3

    await db.execute(
        """
        UPDATE user_skill_packs
        SET display_name = ?,
            description = ?,
            category = ?,
            system_prompt = ?,
            prompt_template = ?,
            input_schema = ?,
            output_mode = ?,
            temperature = ?,
            model = ?,
            status = 'draft',
            published_at = NULL,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (
            str(snapshot.get("display_name") or row["display_name"] or ""),
            str(snapshot.get("description") or row["description"] or ""),
            str(snapshot.get("category") or row["category"] or "custom"),
            str(snapshot.get("system_prompt") or row["system_prompt"] or ""),
            str(snapshot.get("prompt_template") or row["prompt_template"] or ""),
            _safe_json_dumps(input_schema),
            output_mode,
            temperature,
            str(snapshot.get("model") or row["model"] or ""),
            int(row["id"]),
        ),
    )

    updated_row = await _get_user_skill_pack_row(db, user_id=user_id, skill_code=code)
    if not updated_row:
        raise HTTPException(status_code=500, detail="回滚后读取技能包失败")

    await _append_user_skill_pack_version(
        db,
        row=updated_row,
        change_type="rollback",
        note=req.note or f"rollback_to_v{int(req.version_no)}",
    )
    await db.commit()

    return {
        "ok": True,
        "pack": _serialize_user_skill_pack(updated_row),
        "target_version_no": int(req.version_no),
    }


@router.get("/quality")
async def get_skill_quality_reports(user: dict = Depends(get_current_user)):
    """获取技能质量报告（聚合统计）。"""
    db = await get_db()
    rows = await db.execute_fetchall(
        "SELECT skill_name, avg_rating, total_runs, top_tags, updated_at FROM skill_quality_reports ORDER BY total_runs DESC"
    )
    reports = []
    for r in rows:
        item = dict(r)
        if isinstance(item.get("top_tags"), str):
            try:
                item["top_tags"] = json.loads(item["top_tags"])
            except Exception:
                item["top_tags"] = {}
        reports.append(item)

    # 若quality_reports表为空，则从skill_runs实时计算
    if not reports:
        agg_rows = await db.execute_fetchall(
            """SELECT skill_name,
                      AVG(NULLIF(rating, 0)) as avg_rating,
                      COUNT(*) as total_runs,
                      SUM(success) as success_count
               FROM skill_runs
               GROUP BY skill_name
               ORDER BY total_runs DESC"""
        )
        reports = [
            {
                "skill_name": r["skill_name"],
                "avg_rating": round(r["avg_rating"] or 0, 2),
                "total_runs": r["total_runs"],
                "success_count": r["success_count"],
                "top_tags": {},
                "updated_at": None,
            }
            for r in agg_rows
        ]

    return {"quality_reports": reports}


@router.get("/runs")
async def get_skill_runs(
    user: dict = Depends(get_current_user),
    skill_name: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    success_only: bool = Query(False),
):
    """获取技能运行历史（支持过滤 + 分页）。"""
    db = await get_db()
    offset = (page - 1) * page_size

    conditions = ["user_id = ?"]
    params: list = [user["id"]]

    if skill_name:
        conditions.append("skill_name = ?")
        params.append(skill_name)
    if success_only:
        conditions.append("success = 1")

    where = " AND ".join(conditions)
    total_row = await db.execute_fetchone(
        f"SELECT COUNT(*) as cnt FROM skill_runs WHERE {where}", params
    )
    rows = await db.execute_fetchall(
        f"""SELECT id, skill_name, conversation_id, input, output, duration_ms,
                   success, rating, feedback_tags, created_at
            FROM skill_runs WHERE {where}
            ORDER BY id DESC LIMIT ? OFFSET ?""",
        params + [page_size, offset],
    )

    runs = []
    for r in rows:
        item = dict(r)
        for field in ("input", "output"):
            if isinstance(item.get(field), str):
                try:
                    item[field] = json.loads(item[field])
                except Exception:
                    pass
        runs.append(item)

    return {
        "runs": runs,
        "total": total_row["cnt"] if total_row else 0,
        "page": page,
        "page_size": page_size,
    }


@router.post("/run", response_model=SkillRunResponse)
async def run_skill(req: SkillRunRequest, user: dict = Depends(get_current_user)):
    """直接调用指定技能（支持账号级已发布自建技能）。"""
    from src.skills.registry import get_registry

    registry = get_registry()
    all_skills = registry.list_all()
    skill = next((s for s in all_skills if s.name == req.skill_name), None)

    db = await get_db()
    user_id = int(user["id"])

    custom_pack_row = None
    if skill is None:
        custom_pack_row = await _lookup_user_skill_for_run(
            db,
            user_id=user_id,
            skill_name=req.skill_name,
        )
        if custom_pack_row is None:
            raise HTTPException(status_code=404, detail=f"技能 '{req.skill_name}' 不存在")

    if skill is not None and (not ENABLE_ENGINEERING_AGENT and skill.category == "engineering"):
        raise HTTPException(status_code=400, detail="技术工程师技能暂未开放")

    start_ms = int(time.time() * 1000)
    success = True
    result: Dict[str, Any] = {}
    run_skill_name = req.skill_name

    try:
        if custom_pack_row is not None:
            run_skill_name = f"user.{str(custom_pack_row['skill_code'] or '')}"
            result = await _execute_user_skill_pack(custom_pack_row, req.args)
        else:
            result = await registry.execute(req.skill_name, req.args)
    except Exception as e:
        success = False
        result = {"error": str(e)}

    duration_ms = int(time.time() * 1000) - start_ms

    await db.execute(
        """INSERT INTO skill_runs (skill_name, user_id, input, output, duration_ms, success)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            run_skill_name,
            user_id,
            json.dumps(req.args, ensure_ascii=False),
            json.dumps(result, ensure_ascii=False),
            duration_ms,
            int(success),
        ),
    )
    await db.commit()

    if not success:
        raise HTTPException(status_code=500, detail=result.get("error", "技能执行失败"))

    return SkillRunResponse(
        skill_name=run_skill_name,
        result=result,
        duration_ms=duration_ms,
    )


@router.post("/feedback")
async def submit_skill_feedback(req: SkillFeedbackRequest, user: dict = Depends(get_current_user)):
    """提交技能运行反馈。"""
    db = await get_db()
    run_row = await db.execute_fetchone(
        "SELECT id, skill_name FROM skill_runs WHERE id = ? AND user_id = ?",
        (req.run_id, user["id"]),
    )
    if not run_row:
        raise HTTPException(status_code=404, detail="运行记录不存在")

    tags_str = ",".join(req.tags)
    await db.execute(
        "UPDATE skill_runs SET rating = ?, feedback_tags = ? WHERE id = ?",
        (req.rating, tags_str, req.run_id),
    )

    # 更新 skill_quality_reports 聚合
    skill_name = run_row["skill_name"]
    existing_report = await db.execute_fetchone(
        "SELECT id, avg_rating, total_runs, top_tags FROM skill_quality_reports WHERE skill_name = ?",
        (skill_name,),
    )
    if existing_report:
        old_avg = existing_report["avg_rating"] or 0
        rated_runs_row = await db.execute_fetchone(
            "SELECT COUNT(*) as cnt, AVG(rating) as avg FROM skill_runs WHERE skill_name = ? AND rating > 0",
            (skill_name,),
        )
        new_avg = rated_runs_row["avg"] if rated_runs_row and rated_runs_row["avg"] else old_avg
        await db.execute(
            "UPDATE skill_quality_reports SET avg_rating = ?, updated_at = CURRENT_TIMESTAMP WHERE skill_name = ?",
            (round(new_avg, 2), skill_name),
        )
    else:
        await db.execute(
            "INSERT INTO skill_quality_reports (skill_name, avg_rating, total_runs) VALUES (?, ?, 1)",
            (skill_name, req.rating),
        )

    await db.commit()
    return {"ok": True, "run_id": req.run_id}
