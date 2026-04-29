from __future__ import annotations

import asyncio
import json
import os
import traceback
from typing import Any, Dict


def _read_payload() -> Dict[str, Any]:
    raw = os.sys.stdin.read()
    if not raw.strip():
        return {}
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        return {}
    return parsed


async def _run(payload: Dict[str, Any]) -> Dict[str, Any]:
    skill_name = str(payload.get("skill_name") or "").strip()
    args = payload.get("args") if isinstance(payload.get("args"), dict) else {}

    if not skill_name:
        return {"ok": False, "error": "skill_name is required"}

    # 子进程内强制使用 inprocess，避免递归启动子进程。
    os.environ["SKILL_RUNTIME_MODE"] = "inprocess"

    from src.skills.registry import get_registry

    registry = get_registry()
    result = await registry.execute(skill_name, args, context=None)
    return {"ok": True, "result": result}


if __name__ == "__main__":
    try:
        payload = _read_payload()
        output = asyncio.run(_run(payload))
        print(json.dumps(output, ensure_ascii=False))
        if output.get("ok") is not True:
            raise SystemExit(1)
    except Exception as exc:
        error_payload = {
            "ok": False,
            "error": str(exc),
            "traceback": traceback.format_exc(limit=8),
        }
        print(json.dumps(error_payload, ensure_ascii=False))
        raise SystemExit(1)
