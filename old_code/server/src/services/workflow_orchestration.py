from __future__ import annotations

import json
import logging
import re
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.config import (
    EXECUTION_ACTION_MAX_RETRIES,
    EXECUTION_ACTION_REQUIRE_APPROVAL,
    EXECUTION_PACKAGE_LOCK_POLICY,
)
from src.core.audit import log_audit_event
from src.core.domain_router import classify_domain
from src.core.rbac import require_workspace_permission
from src.core.role_router import build_runtime_role_context, normalize_runtime_role
from src.database import get_db
from src.llm_client import call_llm_json
from src.services.action_adapters import execute_adapter_action
from src.services.package_runtime import (
    RunPackageLockDriftError,
    check_run_package_lock_drift,
    save_run_package_lock,
)

logger = logging.getLogger(__name__)


def _load_runtime_role_context() -> Dict[str, Any]:
    ctx = build_runtime_role_context()
    runtime_roles = [
        str(x).strip().lower()
        for x in (ctx.get("runtime_roles") or [])
        if str(x).strip()
    ]
    if not runtime_roles:
        runtime_roles = ["ops"]

    default_role = str(ctx.get("default_role") or "ops").strip().lower() or "ops"
    if default_role not in runtime_roles:
        default_role = runtime_roles[0]

    runtime_set = set(runtime_roles)
    alias_map = ctx.get("alias_map") if isinstance(ctx.get("alias_map"), dict) else {}
    normalized_alias_map: Dict[str, str] = {}

    for key, value in alias_map.items():
        alias_key = str(key or "").strip().lower()
        if not alias_key:
            continue
        mapped = str(value or "").strip().lower()
        normalized_alias_map[alias_key] = mapped if mapped in runtime_set else default_role

    return {
        "runtime_roles": runtime_roles,
        "runtime_set": runtime_set,
        "alias_map": normalized_alias_map,
        "default_role": default_role,
    }


def _norm_role(value: str | None, context: Optional[Dict[str, Any]] = None) -> str:
    ctx = context or _load_runtime_role_context()
    return normalize_runtime_role(value, context=ctx)


def _normalize_lock_policy(value: str | None) -> str:
    policy = str(value or "").strip().lower()
    if policy in {"strict", "warn"}:
        return policy
    return "strict"


def _extract_dep_seq(depends_on: str) -> List[int]:
    if not depends_on:
        return []
    nums = re.findall(r"\d+", depends_on)
    return [int(n) for n in nums if int(n) > 0]


def _default_acceptance(owner_role: str, title: str) -> str:
    role_requirements = {
        "ops": "给出明确执行动作、时间窗口、优先级，至少3条。",
        "data": "给出关键指标、口径、趋势结论，至少包含1个风险点。",
        "service": "给出可直接使用的话术或处理流程，覆盖异常分支。",
        "design": "给出视觉方案要点、版式与素材要求，含尺寸/场景建议。",
        "accounting": "给出成本/收益测算逻辑，包含假设与敏感性说明。",
        "engineering": "给出可执行技术方案、风险、回滚或监控建议。",
        "web": "给出关键词与页面优化动作，含预期指标。",
        "creative": "给出可直接产出的文案/脚本结构，含CTA。",
    }
    return f"任务《{title}》输出必须可执行；{role_requirements.get(owner_role, '给出结构化可执行结果。')}"


def _normalize_plan_items(raw_tasks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    role_ctx = _load_runtime_role_context()
    for idx, t in enumerate(raw_tasks, start=1):
        seq = int(t.get("序号") or t.get("seq") or idx)
        title = str(t.get("任务") or t.get("title") or "").strip() or f"子任务{seq}"
        owner = _norm_role(t.get("Agent") or t.get("owner_role"), context=role_ctx)
        dep_seq = _extract_dep_seq(str(t.get("依赖") or t.get("depends_on") or ""))
        acceptance = str(t.get("验收标准") or t.get("acceptance_criteria") or "").strip()
        if not acceptance:
            acceptance = _default_acceptance(owner, title)
        items.append(
            {
                "seq": seq,
                "title": title,
                "description": str(t.get("说明") or t.get("description") or "").strip(),
                "owner_role": owner,
                "priority": max(0, 100 - seq),
                "dep_seq": dep_seq,
                "acceptance_criteria": acceptance,
            }
        )
    items.sort(key=lambda x: x["seq"])
    return items


async def _build_plan(goal: str, requirements: List[str]) -> List[Dict[str, Any]]:
    from src.skills.registry import get_registry

    reg = get_registry()
    result = await reg.execute("coordination_task_orchestration", {"goal": goal, "requirements": requirements})
    raw_tasks = result.get("任务编排") if isinstance(result, dict) else []
    if not isinstance(raw_tasks, list) or not raw_tasks:
        raw_tasks = [
            {"序号": 1, "任务": "目标拆解与执行计划", "Agent": "ops", "依赖": "无"},
            {"序号": 2, "任务": "数据核验与监控指标定义", "Agent": "data", "依赖": "任务1"},
            {"序号": 3, "任务": "执行并复盘", "Agent": "ops", "依赖": "任务2"},
        ]
    return _normalize_plan_items(raw_tasks)


async def create_execution_plan(
    *,
    workspace_id: int,
    user_id: int,
    goal: str,
    requirements: Optional[List[str]] = None,
    source: str = "manual",
) -> Dict[str, Any]:
    reqs = [r.strip() for r in (requirements or []) if str(r).strip()]
    plan_items = await _build_plan(goal, reqs)

    db = await get_db()
    await require_workspace_permission(
        workspace_id=workspace_id,
        user_id=user_id,
        permission="write",
        allow_global_admin=True,
        db=db,
    )

    now = datetime.now(timezone.utc).isoformat()
    run_cur = await db.execute(
        """
        INSERT INTO workspace_flow_runs (workspace_id, user_id, goal, requirements, status, source, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'planned', ?, ?, ?)
        """,
        (workspace_id, user_id, goal, json.dumps(reqs, ensure_ascii=False), source, now, now),
    )
    run_id = run_cur.lastrowid

    seq_to_task_id: Dict[int, int] = {}
    created_tasks: List[Dict[str, Any]] = []

    for item in plan_items:
        cur = await db.execute(
            """
            INSERT INTO workspace_tasks
            (workspace_id, title, description, owner_role, status, priority, depends_on, acceptance_criteria, result, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pending', ?, '', ?, '', ?, ?)
            """,
            (
                workspace_id,
                item["title"],
                item["description"],
                item["owner_role"],
                item["priority"],
                item["acceptance_criteria"],
                now,
                now,
            ),
        )
        task_id = cur.lastrowid
        seq_to_task_id[item["seq"]] = task_id
        created_tasks.append({**item, "task_id": task_id})

    for item in created_tasks:
        dep_ids = [seq_to_task_id[s] for s in item["dep_seq"] if s in seq_to_task_id]
        depends_on = ",".join(str(i) for i in dep_ids)
        await db.execute("UPDATE workspace_tasks SET depends_on = ? WHERE id = ?", (depends_on, item["task_id"]))
        await db.execute(
            """
            INSERT INTO workspace_flow_tasks
            (run_id, workspace_task_id, seq, owner_role, status, attempts, acceptance_score, acceptance_passed, acceptance_notes, execution_notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'pending', 0, 0, 0, '', '', ?, ?)
            """,
            (run_id, item["task_id"], item["seq"], item["owner_role"], now, now),
        )

    runtime_roles: List[str] = []
    for item in created_tasks:
        owner_role = str(item.get("owner_role") or "").strip()
        if owner_role and owner_role not in runtime_roles:
            runtime_roles.append(owner_role)

    try:
        domain_match = classify_domain(goal)
        domain_id = str(domain_match.domain_id or "domain.general")
    except Exception:
        domain_id = "domain.general"

    package_lock = await save_run_package_lock(
        run_id=run_id,
        workspace_id=workspace_id,
        user_id=user_id,
        goal=goal,
        runtime_roles=runtime_roles,
        domain_id=domain_id,
        db=db,
    )

    await db.commit()

    return {
        "run_id": run_id,
        "workspace_id": workspace_id,
        "goal": goal,
        "requirements": reqs,
        "status": "planned",
        "package_lock": package_lock,
        "tasks": [
            {
                "id": t["task_id"],
                "seq": t["seq"],
                "title": t["title"],
                "owner_role": t["owner_role"],
                "depends_on": [seq_to_task_id[s] for s in t["dep_seq"] if s in seq_to_task_id],
                "acceptance_criteria": t["acceptance_criteria"],
            }
            for t in created_tasks
        ],
    }


async def _evaluate_acceptance(acceptance_criteria: str, execution_result: str) -> Dict[str, Any]:
    criteria = (acceptance_criteria or "").strip()
    result = (execution_result or "").strip()
    if not result:
        return {"passed": False, "score": 0.0, "notes": "未生成任何执行结果"}

    if not criteria:
        score = 0.75 if len(result) >= 120 else 0.55
        return {"passed": score >= 0.7, "score": score, "notes": "未配置验收标准，按内容完整度评估"}

    prompt = (
        "你是任务验收器。请严格返回JSON:\n"
        '{"passed": bool, "score": 0-1, "notes": "简短中文说明", "missing": ["缺失点"]}\n'
        "只输出JSON，不要其他文本。\n\n"
        f"验收标准:\n{criteria}\n\n执行结果:\n{result[:2500]}"
    )
    judged = await call_llm_json(
        messages=[{"role": "user", "content": prompt}],
        system="你是严格的任务验收代理。",
        temperature=0.1,
        max_tokens=500,
    )
    if judged and isinstance(judged, dict):
        passed = bool(judged.get("passed"))
        try:
            score = float(judged.get("score", 0.0))
        except Exception:
            score = 0.0
        notes = str(judged.get("notes") or "")
        missing = judged.get("missing") or []
        if missing:
            notes = (notes + "；缺失: " + ", ".join(str(m) for m in missing[:4])).strip("；")
        return {"passed": passed, "score": max(0.0, min(1.0, score)), "notes": notes or "LLM验收完成"}

    # fallback：关键词覆盖 + 长度
    keywords = [k for k in re.split(r"[，,。;；\n ]+", criteria) if len(k) >= 2][:8]
    hit = sum(1 for k in keywords if k in result)
    coverage = hit / max(1, len(keywords))
    length_bonus = 0.2 if len(result) >= 200 else 0.0
    score = min(1.0, 0.5 * coverage + length_bonus + 0.2)
    return {"passed": score >= 0.7, "score": score, "notes": f"规则验收: 关键词覆盖{hit}/{len(keywords)}"}


async def evaluate_task_acceptance(acceptance_criteria: str, execution_result: str) -> Dict[str, Any]:
    """对外暴露：单任务执行结果验收。"""
    return await _evaluate_acceptance(acceptance_criteria, execution_result)


def _deps_ready(depends_on: str, task_status: Dict[int, str]) -> bool:
    dep_ids = [int(x) for x in re.findall(r"\d+", depends_on or "")]
    return all(task_status.get(i) == "done" for i in dep_ids)


def _extract_actions_from_text(text: str) -> List[Dict[str, Any]]:
    """
    从执行文本中提取动作:
    1) ```json ...``` 中的 {"actions":[...]}
    2) 纯文本中的 {"actions":[...]}
    """
    if not text:
        return []
    # 1) 优先解析 ```json ... ``` 整块
    m = re.search(r"```json\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if m:
        raw = m.group(1).strip()
        try:
            obj = json.loads(raw)
            actions = obj.get("actions")
            if isinstance(actions, list):
                return [a for a in actions if isinstance(a, dict)]
        except Exception:
            pass

    # 2) 兜底：从第一个{"actions":...到最后一个}尝试
    idx = text.find('{"actions"')
    if idx >= 0:
        tail = text[idx:]
        end = tail.rfind("}")
        if end > 0:
            raw = tail[: end + 1]
            try:
                obj = json.loads(raw)
                actions = obj.get("actions")
                if isinstance(actions, list):
                    return [a for a in actions if isinstance(a, dict)]
            except Exception:
                pass
    return []


async def _log_action(
    *,
    run_id: int,
    workspace_id: int,
    task_id: int,
    action_type: str,
    payload: Dict[str, Any],
    status: str,
    result: Dict[str, Any],
    action_key: str = "",
) -> int:
    db = await get_db()
    if not action_key:
        key_src = json.dumps(
            {
                "run_id": run_id,
                "workspace_id": workspace_id,
                "task_id": task_id,
                "action_type": action_type,
                "payload": payload,
                "status": status,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        action_key = hashlib.sha1(key_src.encode("utf-8")).hexdigest()
    if action_key:
        existed = await db.execute_fetchone(
            "SELECT id FROM workspace_action_logs WHERE action_key = ? LIMIT 1",
            (action_key,),
        )
        if existed:
            return int(existed["id"])

    cur = await db.execute(
        """
        INSERT INTO workspace_action_logs
        (run_id, workspace_id, task_id, action_type, payload, status, action_key, result)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            workspace_id,
            task_id,
            action_type,
            json.dumps(payload, ensure_ascii=False),
            status,
            action_key,
            json.dumps(result, ensure_ascii=False),
        ),
    )
    await db.commit()
    return int(cur.lastrowid)


def _business_action_key(*, workspace_id: int, task_id: int, action_type: str, payload: Dict[str, Any]) -> str:
    """
    业务幂等键:
    - 若 payload.idempotency_key 存在，优先使用（推荐业务侧显式传入）
    - 否则返回空字符串（不启用强幂等）
    """
    user_key = str(payload.get("idempotency_key") or "").strip()
    if not user_key:
        return ""
    base = f"{workspace_id}:{task_id}:{action_type}:{user_key}"
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


async def _execute_single_action(
    *,
    run_id: int,
    workspace_id: int,
    task_id: int,
    user_id: int,
    action: Dict[str, Any],
) -> Dict[str, Any]:
    """
    支持动作（真实落库）:
    - create_campaign
    - save_workspace_memory
    - create_workspace_task
    """
    action_type = str(action.get("type") or "").strip()
    payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
    biz_key = _business_action_key(
        workspace_id=workspace_id,
        task_id=task_id,
        action_type=action_type,
        payload=payload,
    )
    db = await get_db()
    if biz_key:
        existed = await db.execute_fetchone(
            "SELECT id, status, result FROM workspace_action_logs WHERE action_key = ? LIMIT 1",
            (biz_key,),
        )
        if existed and existed["status"] == "done":
            result = existed["result"]
            if isinstance(result, str):
                try:
                    result = json.loads(result)
                except Exception:
                    result = {"raw": result}
            return {"ok": True, "type": action_type, "result": result, "attempt": 0, "deduped": True}

    retries = max(0, int(EXECUTION_ACTION_MAX_RETRIES))
    last_err = {"error": "unknown"}
    for attempt in range(retries + 1):
        ok, result = await execute_adapter_action(
            action_type=action_type,
            payload=payload,
            context={
                "run_id": run_id,
                "workspace_id": workspace_id,
                "task_id": task_id,
                "user_id": user_id,
            },
        )
        if ok:
            await _log_action(
                run_id=run_id,
                workspace_id=workspace_id,
                task_id=task_id,
                action_type=action_type,
                payload=payload,
                status="done",
                result={"attempt": attempt + 1, **result},
                action_key=biz_key,
            )
            return {"ok": True, "type": action_type, "result": result, "attempt": attempt + 1}
        last_err = result if isinstance(result, dict) else {"error": str(result)}

    await _log_action(
        run_id=run_id,
        workspace_id=workspace_id,
        task_id=task_id,
        action_type=action_type or "unknown",
        payload=payload,
        status="error",
        result={"attempt": retries + 1, **last_err},
        action_key=biz_key,
    )
    return {"ok": False, "type": action_type, "error": last_err.get("error", "执行失败"), "attempt": retries + 1}


async def execute_run(*, workspace_id: int, user_id: int, run_id: int, max_steps: int = 30, lock_policy: Optional[str] = None) -> Dict[str, Any]:
    from src.core.chat_pipeline import chat_simple

    db = await get_db()
    await require_workspace_permission(
        workspace_id=workspace_id,
        user_id=user_id,
        permission="write",
        allow_global_admin=True,
        db=db,
    )
    run = await db.execute_fetchone(
        "SELECT * FROM workspace_flow_runs WHERE id = ? AND workspace_id = ?",
        (run_id, workspace_id),
    )
    if not run:
        raise ValueError("执行编排不存在")

    run_owner_user_id = int(run["user_id"])
    lock_state = await check_run_package_lock_drift(
        run_id=run_id,
        workspace_id=workspace_id,
        user_id=run_owner_user_id,
        db=db,
    )
    effective_lock_policy = _normalize_lock_policy(lock_policy or EXECUTION_PACKAGE_LOCK_POLICY)
    lock_state["policy"] = effective_lock_policy
    package_lock_warning = ""

    if not lock_state.get("ok", True):
        if effective_lock_policy == "strict":
            raise RunPackageLockDriftError(lock_state)
        package_lock_warning = (
            f"package lock drift detected ({lock_state.get('blocking_count', 0)} blocking), "
            f"policy=warn, continue execution"
        )
        logger.warning(
            "Run %s package drift ignored by warn policy: drifts=%s",
            run_id,
            lock_state.get("drifts", []),
        )

    await db.execute(
        "UPDATE workspace_flow_runs SET status = 'running', updated_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), run_id),
    )
    await db.commit()

    # 任务状态快照
    task_rows = await db.execute_fetchall(
        """
        SELECT wt.id, wt.title, wt.description, wt.owner_role, wt.status, wt.depends_on, wt.acceptance_criteria, wt.result
        FROM workspace_tasks wt
        JOIN workspace_flow_tasks ft ON ft.workspace_task_id = wt.id
        WHERE ft.run_id = ?
        ORDER BY ft.seq ASC
        """,
        (run_id,),
    )
    task_status = {int(r["id"]): r["status"] for r in task_rows}
    task_by_id = {int(r["id"]): dict(r) for r in task_rows}

    executed = 0
    accepted = 0
    blocked = 0
    actions_done = 0
    actions_error = 0

    for _ in range(max_steps):
        pending_ids = [
            int(r["workspace_task_id"])
            for r in await db.execute_fetchall(
                "SELECT workspace_task_id FROM workspace_flow_tasks WHERE run_id = ? AND status = 'pending' ORDER BY seq ASC",
                (run_id,),
            )
        ]
        if not pending_ids:
            break

        ready = [tid for tid in pending_ids if tid in task_by_id and _deps_ready(task_by_id[tid]["depends_on"], task_status)]
        if not ready:
            break

        for tid in ready:
            t = task_by_id[tid]
            now = datetime.now(timezone.utc).isoformat()
            await db.execute("UPDATE workspace_tasks SET status='in_progress', updated_at=? WHERE id=?", (now, tid))
            await db.execute("UPDATE workspace_flow_tasks SET status='running', attempts=attempts+1, updated_at=? WHERE run_id=? AND workspace_task_id=?", (now, run_id, tid))
            await db.commit()

            ctx_rows = await db.execute_fetchall(
                """
                SELECT wt.title, wt.result
                FROM workspace_tasks wt
                JOIN workspace_flow_tasks ft ON ft.workspace_task_id = wt.id
                WHERE ft.run_id = ? AND wt.status = 'done' AND wt.id != ?
                ORDER BY ft.seq ASC
                LIMIT 6
                """,
                (run_id, tid),
            )
            context_text = "\n".join([f"- {r['title']}: {str(r['result'])[:220]}" for r in ctx_rows]) or "无"
            prompt = (
                f"[执行任务]\n目标: {run['goal']}\n"
                f"任务: {t['title']}\n"
                f"描述: {t['description']}\n"
                f"验收标准: {t['acceptance_criteria']}\n"
                f"上游结果:\n{context_text}\n\n"
                "请直接给出可执行结果，要求结构化、可落地。\n"
                "如果需要系统执行真实动作，请在末尾追加JSON代码块，格式:\n"
                "```json\n"
                "{\"actions\":[{\"type\":\"create_campaign|save_workspace_memory|create_workspace_task\",\"payload\":{...}}]}\n"
                "```\n"
                "没有动作可执行时返回 actions 为空数组。"
            )

            try:
                rs = await chat_simple(
                    message=prompt,
                    user_id=user_id,
                    role=t["owner_role"] or None,
                    conversation_id=None,
                    workspace_id=workspace_id,
                )
                output = rs.get("reply", "")
                acc = await _evaluate_acceptance(t["acceptance_criteria"] or "", output)
                final_status = "done" if acc["passed"] else "blocked"

                await db.execute(
                    "UPDATE workspace_tasks SET status=?, result=?, updated_at=? WHERE id=?",
                    (final_status, output, datetime.now(timezone.utc).isoformat(), tid),
                )
                await db.execute(
                    """
                    UPDATE workspace_flow_tasks
                    SET status=?, acceptance_score=?, acceptance_passed=?, acceptance_notes=?, execution_notes=?, updated_at=?
                    WHERE run_id=? AND workspace_task_id=?
                    """,
                    (
                        final_status,
                        float(acc["score"]),
                        1 if acc["passed"] else 0,
                        acc["notes"],
                        output[:1200],
                        datetime.now(timezone.utc).isoformat(),
                        run_id,
                        tid,
                    ),
                )
                await db.commit()

                task_status[tid] = final_status
                task_by_id[tid]["result"] = output
                task_by_id[tid]["status"] = final_status
                executed += 1
                if final_status == "done":
                    accepted += 1
                else:
                    blocked += 1

                # 仅对验收通过的任务执行动作，避免错误放大
                if final_status == "done":
                    actions = _extract_actions_from_text(output)
                    for action in actions[:5]:
                        if EXECUTION_ACTION_REQUIRE_APPROVAL:
                            payload = action.get("payload") if isinstance(action.get("payload"), dict) else {}
                            biz_key = _business_action_key(
                                workspace_id=workspace_id,
                                task_id=tid,
                                action_type=str(action.get("type") or "unknown"),
                                payload=payload,
                            )
                            await _log_action(
                                run_id=run_id,
                                workspace_id=workspace_id,
                                task_id=tid,
                                action_type=str(action.get("type") or "unknown"),
                                payload=payload,
                                status="pending_approval",
                                result={"note": "等待人工审批"},
                                action_key=biz_key,
                            )
                            continue
                        ar = await _execute_single_action(
                            run_id=run_id,
                            workspace_id=workspace_id,
                            task_id=tid,
                            user_id=user_id,
                            action=action,
                        )
                        if ar.get("ok"):
                            actions_done += 1
                        else:
                            actions_error += 1
            except Exception as e:
                msg = f"执行失败: {e}"
                await db.execute(
                    "UPDATE workspace_tasks SET status='blocked', result=?, updated_at=? WHERE id=?",
                    (msg, datetime.now(timezone.utc).isoformat(), tid),
                )
                await db.execute(
                    """
                    UPDATE workspace_flow_tasks
                    SET status='blocked', acceptance_score=0, acceptance_passed=0, acceptance_notes=?, execution_notes=?, updated_at=?
                    WHERE run_id=? AND workspace_task_id=?
                    """,
                    ("执行异常", msg[:1200], datetime.now(timezone.utc).isoformat(), run_id, tid),
                )
                await db.commit()
                task_status[tid] = "blocked"
                blocked += 1
                executed += 1

    pending_left = await db.execute_fetchone(
        "SELECT COUNT(*) AS cnt FROM workspace_flow_tasks WHERE run_id = ? AND status IN ('pending','running')",
        (run_id,),
    )
    pending_cnt = int(pending_left["cnt"]) if pending_left else 0
    if pending_cnt == 0 and blocked == 0:
        run_status = "completed"
    elif accepted > 0:
        run_status = "partial_failed"
    else:
        run_status = "blocked"

    summary = (
        f"执行{executed}项，验收通过{accepted}项，阻塞{blocked}项，剩余{pending_cnt}项；"
        f"动作执行成功{actions_done}项，失败{actions_error}项。"
    )
    await db.execute(
        "UPDATE workspace_flow_runs SET status=?, summary=?, updated_at=? WHERE id=?",
        (run_status, summary, datetime.now(timezone.utc).isoformat(), run_id),
    )
    await db.commit()

    return {
        "run_id": run_id,
        "workspace_id": workspace_id,
        "status": run_status,
        "summary": summary,
        "executed": executed,
        "accepted": accepted,
        "blocked": blocked,
        "pending": pending_cnt,
        "actions_done": actions_done,
        "actions_error": actions_error,
        "package_lock_ok": bool(lock_state.get("ok", True)) or effective_lock_policy == "warn",
        "package_lock_policy": effective_lock_policy,
        "package_lock_warning": package_lock_warning,
        "package_lock_drift_count": int(lock_state.get("drift_count", 0) or 0),
        "package_lock_blocking_count": int(lock_state.get("blocking_count", 0) or 0),
        "package_lock_max_severity": str(lock_state.get("max_severity") or "info"),
        "package_lock_risk_score": int(lock_state.get("risk_score", 0) or 0),
        "package_lock_revision": str(lock_state.get("revision") or ""),
    }


async def get_run_detail(*, workspace_id: int, user_id: int, run_id: int) -> Dict[str, Any]:
    db = await get_db()
    await require_workspace_permission(
        workspace_id=workspace_id,
        user_id=user_id,
        permission="read",
        allow_global_admin=True,
        db=db,
    )
    run = await db.execute_fetchone(
        "SELECT * FROM workspace_flow_runs WHERE id = ? AND workspace_id = ?",
        (run_id, workspace_id),
    )
    if not run:
        raise ValueError("执行编排不存在")
    task_rows = await db.execute_fetchall(
        """
        SELECT ft.id, ft.seq, ft.status, ft.attempts, ft.acceptance_score, ft.acceptance_passed,
               ft.acceptance_notes, wt.id AS task_id, wt.title, wt.owner_role, wt.depends_on, wt.result
        FROM workspace_flow_tasks ft
        JOIN workspace_tasks wt ON wt.id = ft.workspace_task_id
        WHERE ft.run_id = ?
        ORDER BY ft.seq ASC
        """,
        (run_id,),
    )
    package_lock = await check_run_package_lock_drift(
        run_id=run_id,
        workspace_id=workspace_id,
        user_id=int(run["user_id"]),
        db=db,
    )
    return {"run": dict(run), "tasks": [dict(r) for r in task_rows], "package_lock": package_lock}


async def list_runs(*, workspace_id: int, user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    db = await get_db()
    await require_workspace_permission(
        workspace_id=workspace_id,
        user_id=user_id,
        permission="read",
        allow_global_admin=True,
        db=db,
    )
    rows = await db.execute_fetchall(
        """
        SELECT id, workspace_id, user_id, goal, status, source, summary, created_at, updated_at
        FROM workspace_flow_runs
        WHERE workspace_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (workspace_id, limit),
    )
    return [dict(r) for r in rows]


async def build_run_report(*, workspace_id: int, user_id: int, run_id: int) -> Dict[str, Any]:
    detail = await get_run_detail(workspace_id=workspace_id, user_id=user_id, run_id=run_id)
    run = detail["run"]
    tasks = detail["tasks"]
    total = len(tasks)
    done = sum(1 for t in tasks if t.get("status") == "done")
    blocked = sum(1 for t in tasks if t.get("status") == "blocked")
    avg_score = 0.0
    if total:
        try:
            avg_score = sum(float(t.get("acceptance_score") or 0.0) for t in tasks) / total
        except Exception:
            avg_score = 0.0
    action_logs = await list_run_actions(workspace_id=workspace_id, user_id=user_id, run_id=run_id, limit=200)

    lines: List[str] = []
    lines.append(f"# 执行编排报告 #{run_id}")
    lines.append("")
    lines.append(f"- 工作区ID: {workspace_id}")
    lines.append(f"- 目标: {run.get('goal', '')}")
    lines.append(f"- 状态: {run.get('status', '')}")
    lines.append(f"- 概要: {run.get('summary', '')}")
    lines.append(f"- 任务统计: 总计{total} / 通过{done} / 阻塞{blocked}")
    lines.append(f"- 平均验收分: {round(avg_score * 100, 1)}%")
    lines.append(f"- 自动动作执行: 成功{sum(1 for a in action_logs if a.get('status') == 'done')} / 失败{sum(1 for a in action_logs if a.get('status') == 'error')}")
    lines.append("")
    lines.append("## 任务明细")
    lines.append("")
    for t in tasks:
        score = round(float(t.get("acceptance_score") or 0.0) * 100, 1)
        lines.append(f"### [{t.get('status','')}] #{t.get('seq')} {t.get('title','')}")
        lines.append(f"- 角色: {t.get('owner_role','')}")
        lines.append(f"- 验收: {'通过' if int(t.get('acceptance_passed') or 0) == 1 else '未通过'} ({score}%)")
        notes = str(t.get("acceptance_notes") or "").strip()
        if notes:
            lines.append(f"- 备注: {notes}")
        result = str(t.get("result") or "").strip()
        if result:
            lines.append("- 输出摘要:")
            lines.append("")
            lines.append("```text")
            lines.append(result[:1200])
            lines.append("```")
        lines.append("")

    if action_logs:
        lines.append("## 动作执行日志")
        lines.append("")
        for a in action_logs:
            lines.append(f"- [{a.get('status','')}] 任务#{a.get('task_id')} -> {a.get('action_type')}")
        lines.append("")

    markdown = "\n".join(lines).strip() + "\n"
    return {
        "run_id": run_id,
        "workspace_id": workspace_id,
        "status": run.get("status", ""),
        "summary": run.get("summary", ""),
        "task_total": total,
        "task_done": done,
        "task_blocked": blocked,
        "avg_acceptance_score": round(avg_score, 4),
        "actions": action_logs,
        "report_markdown": markdown,
    }


async def create_plan_from_collaboration(
    *,
    workspace_id: int,
    user_id: int,
    user_message: str,
    primary_role: str,
    contributions: List[Dict[str, Any]],
    action: str = "",
) -> Dict[str, Any]:
    reqs = [f"{c.get('name', c.get('role', 'agent'))}补充: {str(c.get('reply', ''))[:120]}" for c in contributions[:4]]
    if action:
        reqs.insert(0, f"动作类型: {action}")
    reqs.insert(0, f"主角色: {primary_role}")
    goal = f"围绕用户诉求执行闭环: {user_message[:120]}"
    return await create_execution_plan(
        workspace_id=workspace_id,
        user_id=user_id,
        goal=goal,
        requirements=reqs,
        source="chat_collab",
    )


async def list_run_actions(
    *,
    workspace_id: int,
    user_id: int,
    run_id: int,
    limit: int = 100,
) -> List[Dict[str, Any]]:
    db = await get_db()
    await require_workspace_permission(
        workspace_id=workspace_id,
        user_id=user_id,
        permission="read",
        allow_global_admin=True,
        db=db,
    )
    run = await db.execute_fetchone(
        "SELECT id FROM workspace_flow_runs WHERE id = ? AND workspace_id = ?",
        (run_id, workspace_id),
    )
    if not run:
        raise ValueError("执行编排不存在")
    rows = await db.execute_fetchall(
        """
        SELECT id, run_id, workspace_id, task_id, action_type, payload, status, result, created_at
        FROM workspace_action_logs
        WHERE run_id = ? AND workspace_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (run_id, workspace_id, limit),
    )
    logs: List[Dict[str, Any]] = []
    for r in rows:
        item = dict(r)
        for k in ("payload", "result"):
            if isinstance(item.get(k), str):
                try:
                    item[k] = json.loads(item[k])
                except Exception:
                    pass
        logs.append(item)
    return logs


async def approve_and_execute_action(
    *,
    workspace_id: int,
    user_id: int,
    action_log_id: int,
) -> Dict[str, Any]:
    db = await get_db()
    await require_workspace_permission(
        workspace_id=workspace_id,
        user_id=user_id,
        permission="approve",
        allow_global_admin=True,
        db=db,
    )
    row = await db.execute_fetchone(
        """
        SELECT al.*, wr.user_id AS owner_user_id
        FROM workspace_action_logs al
        JOIN workspace_flow_runs wr ON wr.id = al.run_id
        WHERE al.id = ? AND al.workspace_id = ?
        """,
        (action_log_id, workspace_id),
    )
    if not row:
        raise ValueError("动作日志不存在")
    if row["status"] != "pending_approval":
        raise ValueError("该动作不是待审批状态")

    payload = row["payload"]
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            payload = {}
    ok, result = await execute_adapter_action(
        action_type=row["action_type"],
        payload=payload if isinstance(payload, dict) else {},
        context={
            "run_id": row["run_id"],
            "workspace_id": row["workspace_id"],
            "task_id": row["task_id"],
            "user_id": user_id,
        },
    )
    status = "done" if ok else "error"
    await db.execute(
        """
        UPDATE workspace_action_logs
        SET status = ?, result = ?, approved_by = ?, approved_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (status, json.dumps(result, ensure_ascii=False), user_id, action_log_id),
    )
    await log_audit_event(
        actor_user_id=user_id,
        workspace_id=workspace_id,
        action="workspace.autoflow.action.approve",
        target_type="workspace_action_log",
        target_id=action_log_id,
        status="success" if status == "done" else "failed",
        reason="" if status == "done" else "adapter_execution_failed",
        run_id=int(row["run_id"]),
        action_log_id=action_log_id,
        metadata={
            "action_type": row["action_type"],
            "task_id": row["task_id"],
            "result": result,
        },
        db=db,
    )
    await db.commit()
    return {"action_id": action_log_id, "status": status, "result": result}


async def reject_action(
    *,
    workspace_id: int,
    user_id: int,
    action_log_id: int,
    reason: str = "",
) -> Dict[str, Any]:
    db = await get_db()
    await require_workspace_permission(
        workspace_id=workspace_id,
        user_id=user_id,
        permission="approve",
        allow_global_admin=True,
        db=db,
    )
    row = await db.execute_fetchone(
        """
        SELECT al.id, al.status, al.run_id
        FROM workspace_action_logs al
        JOIN workspace_flow_runs wr ON wr.id = al.run_id
        WHERE al.id = ? AND al.workspace_id = ?
        """,
        (action_log_id, workspace_id),
    )
    if not row:
        raise ValueError("动作日志不存在")
    if row["status"] != "pending_approval":
        raise ValueError("该动作不是待审批状态")
    reject_reason = reason or "manual_rejected"
    await db.execute(
        "UPDATE workspace_action_logs SET status = 'rejected', result = ?, approved_by = ?, approved_at = CURRENT_TIMESTAMP WHERE id = ?",
        (json.dumps({"reason": reject_reason}, ensure_ascii=False), user_id, action_log_id),
    )
    await log_audit_event(
        actor_user_id=user_id,
        workspace_id=workspace_id,
        action="workspace.autoflow.action.reject",
        target_type="workspace_action_log",
        target_id=action_log_id,
        status="success",
        reason=reject_reason,
        run_id=int(row["run_id"]),
        action_log_id=action_log_id,
        metadata={"reason": reject_reason},
        db=db,
    )
    await db.commit()
    return {"action_id": action_log_id, "status": "rejected", "reason": reject_reason}
