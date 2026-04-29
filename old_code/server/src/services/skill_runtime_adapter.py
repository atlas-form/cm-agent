from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from src.config import PROJECT_ROOT
from src.skills.base import SkillBase


def _runtime_mode() -> str:
    raw = str(os.getenv("SKILL_RUNTIME_MODE", "inprocess") or "inprocess").strip().lower()
    if raw in {"subprocess", "inprocess"}:
        return raw
    return "inprocess"


def _runtime_timeout_seconds() -> float:
    raw = str(os.getenv("SKILL_RUNTIME_TIMEOUT_SECONDS", "20") or "20").strip()
    try:
        value = float(raw)
        if value <= 0:
            return 20.0
        return value
    except Exception:
        return 20.0


def _merge_skill_args(args: Dict[str, Any], context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    merged = dict(args or {})
    if context:
        merged["_user_id"] = context.get("user_id", 1)
        merged["_workspace_id"] = context.get("workspace_id")
        merged["_role"] = context.get("role", "ops")
        merged["_needs_fresh_data"] = context.get("_needs_fresh_data", True)
    return merged


async def _execute_inprocess(skill: SkillBase, merged_args: Dict[str, Any]) -> Dict[str, Any]:
    return await skill.execute(**merged_args)


async def _execute_subprocess(skill_name: str, merged_args: Dict[str, Any]) -> Dict[str, Any]:
    worker = (Path(__file__).resolve().parent / "skill_runtime_worker.py").resolve()
    payload = {
        "skill_name": skill_name,
        "args": merged_args,
    }

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            str(worker),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(PROJECT_ROOT),
        )
    except Exception as exc:
        return {
            "_adapter_failed": True,
            "reason": f"subprocess spawn failed: {exc}",
        }

    try:
        out, err = await asyncio.wait_for(
            proc.communicate(json.dumps(payload, ensure_ascii=False).encode("utf-8")),
            timeout=_runtime_timeout_seconds(),
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return {
            "_adapter_failed": True,
            "reason": "subprocess timed out",
        }

    stdout_text = out.decode("utf-8", errors="replace").strip()
    stderr_text = err.decode("utf-8", errors="replace").strip()

    if not stdout_text:
        return {
            "_adapter_failed": True,
            "reason": f"subprocess empty output (rc={proc.returncode}): {stderr_text}",
        }

    try:
        payload_obj = json.loads(stdout_text)
    except Exception as exc:
        return {
            "_adapter_failed": True,
            "reason": f"subprocess output is not json: {exc}",
            "stderr": stderr_text,
        }

    if not isinstance(payload_obj, dict):
        return {
            "_adapter_failed": True,
            "reason": "subprocess output should be object",
            "stderr": stderr_text,
        }

    if payload_obj.get("ok") is not True:
        return {
            "_adapter_failed": True,
            "reason": str(payload_obj.get("error") or "subprocess runtime error"),
            "stderr": stderr_text,
        }

    result = payload_obj.get("result")
    if isinstance(result, dict):
        result = dict(result)
        result.setdefault("_runtime_mode", "subprocess")
        return result

    return {
        "result": result,
        "_runtime_mode": "subprocess",
    }


async def execute_skill_runtime(
    skill: SkillBase,
    *,
    skill_name: str,
    args: Dict[str, Any],
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    merged_args = _merge_skill_args(args, context)
    mode = _runtime_mode()

    if mode == "subprocess":
        sub_result = await _execute_subprocess(skill_name, merged_args)
        if not sub_result.get("_adapter_failed"):
            return sub_result

        fallback_reason = str(sub_result.get("reason") or "subprocess failed")
        fallback_result = await _execute_inprocess(skill, merged_args)
        if isinstance(fallback_result, dict):
            fallback_payload = dict(fallback_result)
        else:
            fallback_payload = {"result": fallback_result}
        fallback_payload.setdefault("_runtime_mode", "inprocess_fallback")
        fallback_payload.setdefault("_runtime_fallback_reason", fallback_reason)
        return fallback_payload

    result = await _execute_inprocess(skill, merged_args)
    if isinstance(result, dict):
        return result
    return {"result": result}
