from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import requests


ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "server" / "data" / "v4.db"
DEFAULT_BASE_URL = os.getenv("RUNTIME_PROBE_BASE_URL") or os.getenv("SMOKE_BASE_URL") or "http://127.0.0.1:8100"


@dataclass
class StepResult:
    name: str
    method: str
    path: str
    status: int
    ok: bool
    elapsed_ms: int
    detail: str = ""


class RuntimeProbeRunner:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.results: list[StepResult] = []

    def call(
        self,
        name: str,
        method: str,
        path: str,
        *,
        expected: Iterable[int] = (200,),
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        files: Any = None,
        data: dict[str, Any] | None = None,
        timeout: int = 60,
    ) -> requests.Response | None:
        url = f"{self.base_url}{path}"
        expected_set = set(expected)
        t0 = time.time()
        try:
            resp = self.session.request(
                method=method,
                url=url,
                json=json_body,
                params=params,
                files=files,
                data=data,
                timeout=timeout,
            )
            elapsed = int((time.time() - t0) * 1000)
            ok = resp.status_code in expected_set
            detail = ""
            if not ok:
                text = (resp.text or "")[:360].replace("\n", " ")
                detail = f"unexpected_status expected={sorted(expected_set)} body={text}"
            self.results.append(
                StepResult(
                    name=name,
                    method=method,
                    path=path,
                    status=resp.status_code,
                    ok=ok,
                    elapsed_ms=elapsed,
                    detail=detail,
                )
            )
            print(f"[{'PASS' if ok else 'FAIL'}] {method:<6} {path:<90} -> {resp.status_code} ({elapsed}ms)")
            return resp
        except Exception as exc:  # noqa: BLE001
            elapsed = int((time.time() - t0) * 1000)
            detail = f"exception={exc}"
            self.results.append(
                StepResult(
                    name=name,
                    method=method,
                    path=path,
                    status=0,
                    ok=False,
                    elapsed_ms=elapsed,
                    detail=detail,
                )
            )
            print(f"[FAIL] {method:<6} {path:<90} -> {detail}")
            return None

    def set_bearer(self, token: str) -> None:
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def ensure_admin(self, email: str) -> None:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "UPDATE users SET is_admin = 1, account_role = 'admin' WHERE email = ?",
                (email,),
            )
            conn.commit()

    def summary(self) -> dict[str, Any]:
        total = len(self.results)
        passed = sum(1 for row in self.results if row.ok)
        failed = total - passed
        return {
            "base_url": self.base_url,
            "total": total,
            "passed": passed,
            "failed": failed,
            "results": [row.__dict__ for row in self.results],
        }


def _must_json(resp: requests.Response | None) -> dict[str, Any]:
    if resp is None:
        return {}
    try:
        return resp.json() if resp.content else {}
    except Exception:
        return {}


def _register_account(
    runner: RuntimeProbeRunner,
    *,
    email: str,
    password: str,
    name: str,
    account_role: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "email": email,
        "password": password,
        "name": name,
    }
    if account_role:
        body["account_role"] = account_role

    r = runner.call(
        f"register_{name}",
        "POST",
        "/api/auth/register",
        expected=(200, 409),
        json_body=body,
    )

    if r is None:
        return {}

    if r.status_code == 409:
        r_login = runner.call(
            f"login_{name}",
            "POST",
            "/api/auth/login",
            expected=(200,),
            json_body={"email": email, "password": password},
        )
        return _must_json(r_login)

    return _must_json(r)


def _create_chat_conversation(runner: RuntimeProbeRunner, message: str, role: str = "ops") -> str:
    conversation_id = f"runtime_conv_{uuid.uuid4().hex[:12]}"
    r = runner.call(
        "chat_create_conversation",
        "POST",
        "/api/chat",
        expected=(200,),
        json_body={"message": message, "role": role, "conversation_id": conversation_id},
        timeout=120,
    )
    if r is None or r.status_code != 200:
        return ""
    return conversation_id


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run full feature runtime probe")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Target server base URL, e.g. http://127.0.0.1:8210",
    )
    return parser.parse_args(argv)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default

def _ensure_conversation_owned(conversation_id: str, user_id: int, *, title: str = "Runtime Conversation") -> None:
    conv_id = str(conversation_id or "").strip()
    if not conv_id or int(user_id) <= 0:
        return
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO conversations (id, user_id, title, agent_role) VALUES (?, ?, ?, 'ops')",
            (conv_id, int(user_id), str(title or "Runtime Conversation")[:120]),
        )
        conn.commit()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    base_url = str(args.base_url or DEFAULT_BASE_URL).rstrip("/")
    print(f"[INFO] runtime_probe_target={base_url}")

    runner = RuntimeProbeRunner(base_url)

    # 0) basic reachability
    runner.call("frontend_root", "GET", "/", expected=(200,))
    runner.call("health", "GET", "/api/health", expected=(200,))

    # 1) bootstrap accounts
    suffix = uuid.uuid4().hex[:10]
    password = "RuntimeProbe123!"

    probe_email = f"runtime_probe_{suffix}@example.com"
    teacher_email = f"runtime_teacher_{suffix}@example.com"
    student_email = f"runtime_student_{suffix}@example.com"

    probe_payload = _register_account(
        runner,
        email=probe_email,
        password=password,
        name="RuntimeProbe",
        account_role="general",
    )
    teacher_payload = _register_account(
        runner,
        email=teacher_email,
        password=password,
        name="RuntimeTeacher",
        account_role="teacher",
    )
    student_payload = _register_account(
        runner,
        email=student_email,
        password=password,
        name="RuntimeStudent",
        account_role="student",
    )

    probe_token = str(probe_payload.get("token") or "")
    teacher_token = str(teacher_payload.get("token") or "")
    student_token = str(student_payload.get("token") or "")

    if not probe_token or not teacher_token or not student_token:
        print("[FAIL] token bootstrap missing")
        return 2

    # keep main user admin-capable for package/kernel-level operations
    runner.ensure_admin(probe_email)
    r_refresh = runner.call(
        "probe_login_refresh",
        "POST",
        "/api/auth/login",
        expected=(200,),
        json_body={"email": probe_email, "password": password},
    )
    refreshed_token = str(_must_json(r_refresh).get("token") or probe_token)
    probe_user_id = _safe_int(probe_payload.get("user_id"))

    # 2) teaching workflows (teacher + student)
    teacher_session = requests.Session()
    teacher_session.headers.update({"Authorization": f"Bearer {teacher_token}"})
    student_session = requests.Session()
    student_session.headers.update({"Authorization": f"Bearer {student_token}"})

    def teacher_call(
        name: str,
        method: str,
        path: str,
        *,
        expected: Iterable[int] = (200,),
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: int = 60,
    ) -> requests.Response | None:
        old = runner.session
        runner.session = teacher_session
        try:
            return runner.call(name, method, path, expected=expected, json_body=json_body, params=params, timeout=timeout)
        finally:
            runner.session = old

    def student_call(
        name: str,
        method: str,
        path: str,
        *,
        expected: Iterable[int] = (200,),
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: int = 60,
    ) -> requests.Response | None:
        old = runner.session
        runner.session = student_session
        try:
            return runner.call(name, method, path, expected=expected, json_body=json_body, params=params, timeout=timeout)
        finally:
            runner.session = old

    # teaching submit baseline
    student_call("teaching_teachers", "GET", "/api/conversations/teaching/teachers", expected=(200,))

    r_class = teacher_call(
        "teaching_class_create",
        "POST",
        "/api/conversations/teaching/classes",
        json_body={"name": f"Runtime Class {suffix}", "description": "runtime probe"},
    )
    class_id = _safe_int(_must_json(r_class).get("id"))
    if class_id <= 0:
        print("[FAIL] class id missing")
        return 3

    student_user_id = _safe_int(student_payload.get("user_id"))
    teacher_call(
        "teaching_class_add_member",
        "POST",
        f"/api/conversations/teaching/classes/{class_id}/members",
        json_body={"student_user_id": student_user_id},
    )
    teacher_call("teaching_class_members", "GET", f"/api/conversations/teaching/classes/{class_id}/members")

    r_assignment = teacher_call(
        "teaching_assignment_create",
        "POST",
        f"/api/conversations/teaching/classes/{class_id}/assignments",
        json_body={"title": "Runtime Assignment", "description": "submit and review"},
    )
    assignment_id = _safe_int(_must_json(r_assignment).get("id"))
    if assignment_id <= 0:
        print("[FAIL] assignment id missing")
        return 3

    teacher_call("teaching_assignment_list_teacher", "GET", f"/api/conversations/teaching/classes/{class_id}/assignments")
    student_call("teaching_assignment_list_student", "GET", f"/api/conversations/teaching/classes/{class_id}/assignments")

    # create student conversation by real chat
    student_conv_id = f"runtime_teaching_conv_{uuid.uuid4().hex[:12]}"
    r_student_chat = student_call(
        "teaching_student_chat",
        "POST",
        "/api/chat",
        json_body={
            "message": "这是作业回答，请老师评阅。",
            "role": "ops",
            "conversation_id": student_conv_id,
        },
        timeout=120,
    )
    if r_student_chat is None or r_student_chat.status_code != 200:
        print("[FAIL] student conversation missing")
        return 3

    r_submit = student_call(
        "teaching_assignment_submit",
        "POST",
        f"/api/conversations/{student_conv_id}/teaching/assignments/{assignment_id}/submit",
        json_body={"note": "请关注逻辑链和证据"},
    )
    submit_payload = _must_json(r_submit)
    teaching_submission_id = _safe_int(submit_payload.get("teaching_submission_id") or submit_payload.get("id"))
    if teaching_submission_id <= 0:
        print("[FAIL] teaching submission id missing")
        return 3

    teacher_call("teaching_submissions", "GET", "/api/conversations/teaching/submissions")
    student_call("teaching_my_submissions", "GET", "/api/conversations/teaching/my-submissions")
    teacher_call("teaching_submission_messages", "GET", f"/api/conversations/teaching/submissions/{teaching_submission_id}/messages")

    teacher_call(
        "teaching_evaluation_create",
        "POST",
        f"/api/conversations/teaching/submissions/{teaching_submission_id}/evaluations",
        json_body={
            "score": 88,
            "feedback": "结构完整，建议补充反例。",
            "rubric": {"logic": 44, "evidence": 44},
        },
    )
    teacher_call("teaching_evaluation_list_teacher", "GET", f"/api/conversations/teaching/submissions/{teaching_submission_id}/evaluations")
    student_call("teaching_evaluation_list_student", "GET", f"/api/conversations/teaching/submissions/{teaching_submission_id}/evaluations")

    teacher_call("teaching_dashboard", "GET", f"/api/conversations/teaching/classes/{class_id}/dashboard")

    # goals / intervention / experiments / term archives
    r_goal = teacher_call(
        "teaching_goal_upsert",
        "POST",
        f"/api/conversations/teaching/classes/{class_id}/goals",
        json_body={
            "goal_code": f"runtime_goal_{suffix}",
            "goal_name": "提交率达标",
            "metric_type": "submission_rate",
            "target_value": 0.8,
            "due_at": "2026-12-31",
        },
    )
    goal_id = _safe_int((_must_json(r_goal).get("goal") or {}).get("id"))

    teacher_call("teaching_goals_list", "GET", f"/api/conversations/teaching/classes/{class_id}/goals")
    if goal_id > 0:
        teacher_call(
            "teaching_goal_status",
            "POST",
            f"/api/conversations/teaching/classes/{class_id}/goals/{goal_id}/status",
            json_body={"status": "completed", "note": "runtime probe complete"},
        )

    teacher_call("teaching_goals_trend", "GET", f"/api/conversations/teaching/classes/{class_id}/goals/trend")
    teacher_call("teaching_intervention_recommend", "GET", f"/api/conversations/teaching/classes/{class_id}/interventions/recommendations")

    r_exp = teacher_call(
        "teaching_experiment_plan",
        "POST",
        f"/api/conversations/teaching/classes/{class_id}/interventions/experiments/plan",
        json_body={
            "intervention_code": "submission_at_risk",
            "intervention_title": "提交风险实验",
            "experiment_id": f"runtime_exp_{suffix}",
            "recommended_variant": "A",
            "variants": ["A", "B"],
            "window_days": 7,
            "target_metric": "submission_rate",
        },
    )
    experiment_code = str((_must_json(r_exp).get("experiment") or {}).get("experiment_code") or "")
    teacher_call("teaching_experiment_list", "GET", f"/api/conversations/teaching/classes/{class_id}/interventions/experiments")
    if experiment_code:
        teacher_call(
            "teaching_experiment_status_running",
            "POST",
            f"/api/conversations/teaching/classes/{class_id}/interventions/experiments/{experiment_code}/status",
            json_body={"status": "running", "note": "runtime start"},
        )
        teacher_call(
            "teaching_experiment_status_completed",
            "POST",
            f"/api/conversations/teaching/classes/{class_id}/interventions/experiments/{experiment_code}/status",
            json_body={"status": "completed", "note": "runtime done"},
        )

    teacher_call(
        "teaching_experiment_results",
        "GET",
        f"/api/conversations/teaching/classes/{class_id}/interventions/experiments/results",
        params={"experiment_code": experiment_code} if experiment_code else None,
    )

    teacher_call(
        "teaching_intervention_action_create",
        "POST",
        f"/api/conversations/teaching/classes/{class_id}/interventions/actions",
        json_body={
            "intervention_code": "submission_at_risk",
            "intervention_title": "提交风险实验",
            "intervention_severity": "high",
            "note": "runtime action",
            "metadata": {"source": "runtime_probe"},
        },
    )
    teacher_call("teaching_intervention_action_list", "GET", f"/api/conversations/teaching/classes/{class_id}/interventions/actions")

    teacher_call(
        "teaching_term_archive_base",
        "POST",
        f"/api/conversations/teaching/classes/{class_id}/goals/term-archives",
        json_body={"term_code": "2026_spring", "term_name": "2026春季学期"},
    )
    teacher_call(
        "teaching_term_archive_target",
        "POST",
        f"/api/conversations/teaching/classes/{class_id}/goals/term-archives",
        json_body={"term_code": "2026_summer", "term_name": "2026夏季学期"},
    )
    teacher_call(
        "teaching_term_archive_list",
        "GET",
        f"/api/conversations/teaching/classes/{class_id}/goals/term-archives",
        params={"include_detail": "true"},
    )
    teacher_call(
        "teaching_term_archive_compare",
        "GET",
        f"/api/conversations/teaching/classes/{class_id}/goals/term-archives/compare",
        params={"base": "2026_spring", "target": "2026_summer"},
    )

    # template lifecycle
    r_tpl_assignment = teacher_call(
        "teaching_template_assignment_create",
        "POST",
        "/api/conversations/teaching/templates",
        json_body={
            "template_type": "assignment",
            "name": f"Runtime Assignment Template {suffix}",
            "initial_status": "draft",
            "payload": {
                "title": "Template Assignment",
                "description": "runtime assignment template",
                "due_at": "2026-12-31",
            },
        },
    )
    assignment_template_id = _safe_int(_must_json(r_tpl_assignment).get("id"))

    r_tpl_class = teacher_call(
        "teaching_template_class_create",
        "POST",
        "/api/conversations/teaching/templates",
        json_body={
            "template_type": "class",
            "name": f"Runtime Class Template {suffix}",
            "payload": {
                "name": f"Class By Template {suffix}",
                "description": "runtime template class",
            },
        },
    )
    class_template_id = _safe_int(_must_json(r_tpl_class).get("id"))

    if assignment_template_id > 0:
        teacher_call(
            "teaching_template_status_review",
            "POST",
            f"/api/conversations/teaching/templates/{assignment_template_id}/status",
            json_body={"target_status": "review", "note": "runtime review"},
        )
        teacher_call(
            "teaching_template_status_approved",
            "POST",
            f"/api/conversations/teaching/templates/{assignment_template_id}/status",
            json_body={"target_status": "approved", "note": "runtime approve"},
        )
        teacher_call(
            "teaching_template_versions",
            "GET",
            f"/api/conversations/teaching/templates/{assignment_template_id}/versions",
            params={"limit": 10},
        )
        teacher_call(
            "teaching_template_rollback",
            "POST",
            f"/api/conversations/teaching/templates/{assignment_template_id}/rollback",
            json_body={"version_no": 1, "note": "runtime rollback"},
            expected=(200, 404),
        )

    if class_template_id > 0:
        teacher_call(
            "teaching_template_instantiate_class",
            "POST",
            f"/api/conversations/teaching/templates/{class_template_id}/instantiate-class",
            json_body={},
            expected=(200, 409),
        )

    if assignment_template_id > 0:
        teacher_call(
            "teaching_template_apply_assignment",
            "POST",
            f"/api/conversations/teaching/classes/{class_id}/templates/{assignment_template_id}/assignments/apply",
            json_body={"title_override": "Runtime Applied Assignment"},
            expected=(200, 409),
        )

    teacher_call("teaching_templates_list", "GET", "/api/conversations/teaching/templates", params={"limit": 50})
    teacher_call("teaching_templates_insights", "GET", "/api/conversations/teaching/templates/insights", params={"top_limit": 20})

    # 3) switch to main probe user
    runner.set_bearer(refreshed_token)
    r_probe_me = runner.call("probe_auth_me", "GET", "/api/auth/me", expected=(200,))
    if probe_user_id <= 0:
        probe_user_id = _safe_int(_must_json(r_probe_me).get("id"))

    # 4) longterm memory governance chain
    conv_a = _create_chat_conversation(runner, "请记录我的长期偏好：主攻抖音。")
    conv_b = _create_chat_conversation(runner, "请记录我的长期偏好：尝试小红书。")
    if not conv_a or not conv_b:
        print("[FAIL] longterm conversations missing")
        return 4

    if probe_user_id > 0:
        _ensure_conversation_owned(conv_a, probe_user_id, title="Runtime LTM A")
        _ensure_conversation_owned(conv_b, probe_user_id, title="Runtime LTM B")

    conflict_key = f"runtime_conflict_key_{suffix}"

    runner.call(
        "ltm_profile_patch",
        "PATCH",
        "/api/intelligence/longterm-memory/profile",
        expected=(200,),
        json_body={
            "persona": "偏执行导向，关注增长效率",
            "platforms": {"douyin": 5},
            "interests": {"roi": 4},
        },
    )
    runner.call(
        "ltm_context_patch_a",
        "PATCH",
        f"/api/intelligence/longterm-memory/context/{conv_a}",
        expected=(200,),
        json_body={
            "summary": "Runtime A",
            "facts": {conflict_key: "douyin", "goals": ["增长"]},
        },
    )
    runner.call(
        "ltm_context_patch_b",
        "PATCH",
        f"/api/intelligence/longterm-memory/context/{conv_b}",
        expected=(200,),
        json_body={
            "summary": "Runtime B",
            "facts": {conflict_key: "xiaohongshu", "goals": ["增长"]},
        },
    )

    runner.call("ltm_list", "GET", "/api/intelligence/longterm-memory", expected=(200,))
    runner.call("ltm_conflicts", "GET", "/api/intelligence/longterm-memory/conflicts", expected=(200,))
    runner.call("ltm_conflict_suggestions", "GET", "/api/intelligence/longterm-memory/conflict-suggestions", expected=(200,))
    runner.call(
        "ltm_conflict_resolve_preview",
        "POST",
        "/api/intelligence/longterm-memory/conflicts/resolve",
        expected=(200,),
        json_body={"apply": False, "min_confidence": 0.5, "fact_keys": [conflict_key]},
    )
    runner.call(
        "ltm_conflict_resolve_apply",
        "POST",
        "/api/intelligence/longterm-memory/conflicts/resolve",
        expected=(200,),
        json_body={"apply": True, "min_confidence": 0.5, "fact_keys": [conflict_key]},
    )

    runner.call(
        "ltm_batch_update",
        "POST",
        "/api/intelligence/longterm-memory/contexts/batch-update",
        expected=(200,),
        json_body={
            "items": [
                {
                    "conversation_id": conv_a,
                    "summary": "Runtime A updated",
                    "facts": {conflict_key: "douyin", "goals": ["转化"]},
                },
                {
                    "conversation_id": conv_b,
                    "summary": "Runtime B updated",
                    "facts": {conflict_key: "douyin", "goals": ["转化"]},
                },
            ]
        },
    )

    runner.call("ltm_policy_get", "GET", "/api/intelligence/longterm-memory/policy", expected=(200,))
    runner.call(
        "ltm_policy_patch",
        "PATCH",
        "/api/intelligence/longterm-memory/policy",
        expected=(200,),
        json_body={"retention_days": 180, "max_contexts": 2, "auto_prune": False},
    )

    runner.call(
        "ltm_policy_prune_preview",
        "POST",
        "/api/intelligence/longterm-memory/policy/prune",
        expected=(200,),
        json_body={"apply": False, "max_contexts": 1},
    )
    r_prune_apply = runner.call(
        "ltm_policy_prune_apply",
        "POST",
        "/api/intelligence/longterm-memory/policy/prune",
        expected=(200,),
        json_body={"apply": True, "max_contexts": 1},
    )
    prune_run_id = _safe_int(_must_json(r_prune_apply).get("prune_run_id"))

    runner.call("ltm_prune_runs", "GET", "/api/intelligence/longterm-memory/policy/prune-runs", expected=(200,), params={"limit": 20})
    if prune_run_id > 0:
        runner.call(
            "ltm_prune_rollback_preview",
            "POST",
            f"/api/intelligence/longterm-memory/policy/prune-runs/{prune_run_id}/rollback",
            expected=(200,),
            json_body={"apply": False},
        )
        runner.call(
            "ltm_prune_rollback_apply",
            "POST",
            f"/api/intelligence/longterm-memory/policy/prune-runs/{prune_run_id}/rollback",
            expected=(200,),
            json_body={"apply": True},
        )

    # 5) platform touch channels
    runner.call("touch_channels_meta", "GET", "/api/platform-connect/channels", expected=(200,))
    runner.call(
        "touch_channels_connect_wecom",
        "POST",
        "/api/platform-connect/channels/connect",
        expected=(200,),
        json_body={
            "channel": "wecom",
            "credentials": {
                "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=runtime_probe",
                "secret": "runtime_secret",
            },
            "enabled": True,
        },
    )
    runner.call("touch_channels_connections", "GET", "/api/platform-connect/channels/connections", expected=(200,))
    runner.call("touch_channels_test", "POST", "/api/platform-connect/channels/test/wecom", expected=(200,))
    runner.call(
        "touch_channels_deliver",
        "POST",
        "/api/platform-connect/channels/deliver",
        expected=(200,),
        json_body={
            "channels": ["wecom"],
            "title": "Runtime Probe",
            "content": "观测链路触达测试",
        },
    )
    runner.call("touch_channels_logs", "GET", "/api/platform-connect/channels/logs", expected=(200,), params={"limit": 20})

    # 6) workspace observability chain
    r_ws = runner.call(
        "workspace_create_for_observability",
        "POST",
        "/api/workspaces",
        expected=(200,),
        json_body={"title": f"Runtime Observability {suffix}", "workspace_type": "general"},
    )
    ws_id = _safe_int(_must_json(r_ws).get("id"))
    if ws_id <= 0:
        print("[FAIL] workspace id missing")
        return 5

    runner.call("workspace_members", "GET", f"/api/workspaces/{ws_id}/members", expected=(200,))
    runner.call("workspace_audit", "GET", f"/api/workspaces/{ws_id}/audit", expected=(200,))

    runner.call(
        "workspace_runtime_thresholds_get",
        "GET",
        f"/api/workspaces/{ws_id}/observability/runtime-thresholds",
        expected=(200,),
    )
    runner.call(
        "workspace_runtime_thresholds_patch",
        "PATCH",
        f"/api/workspaces/{ws_id}/observability/runtime-thresholds",
        expected=(200,),
        json_body={
            "thresholds": {
                "failure_rate_high": 0.85,
                "failure_rate_medium": 0.45,
                "p95_duration_ms_high": 12000,
                "total_estimated_cost_usd_high": 18.0,
            }
        },
    )

    runner.call(
        "workspace_runtime_alert_policy_get",
        "GET",
        f"/api/workspaces/{ws_id}/observability/runtime-alert-policy",
        expected=(200,),
    )
    runner.call(
        "workspace_runtime_alert_policy_patch",
        "PATCH",
        f"/api/workspaces/{ws_id}/observability/runtime-alert-policy",
        expected=(200,),
        json_body={
            "alert_policy": {
                "enable_silence": True,
                "silence_window_minutes": 60,
                "enable_escalation": True,
                "escalation_window_minutes": 180,
                "escalation_repeat_count": 2,
                "default_route": "ops-center",
                "metric_routes": {"failure_rate": "reliability-team"},
            }
        },
    )

    runner.call(
        "workspace_runtime_summary_1",
        "GET",
        f"/api/workspaces/{ws_id}/observability/runtime",
        expected=(200,),
        params={"hours": 24},
    )
    synthetic_alert_key = f"runtime.synthetic.{suffix}"
    runner.call(
        "workspace_runtime_ack",
        "POST",
        f"/api/workspaces/{ws_id}/observability/runtime-alerts/{synthetic_alert_key}/ack",
        expected=(200,),
        json_body={"idempotency_key": f"ack-{suffix}", "note": "runtime probe ack"},
    )
    runner.call(
        "workspace_runtime_recover",
        "POST",
        f"/api/workspaces/{ws_id}/observability/runtime-alerts/{synthetic_alert_key}/recover",
        expected=(200,),
        json_body={"idempotency_key": f"ack-{suffix}", "note": "runtime probe recover"},
    )
    runner.call(
        "workspace_runtime_summary_2",
        "GET",
        f"/api/workspaces/{ws_id}/observability/runtime",
        expected=(200,),
        params={"hours": 24, "dispatch": "false"},
    )

    # 7) packages + skills user packs + skill run
    runner.call("packages_catalog", "GET", "/api/packages/catalog", expected=(200,), params={"include_disabled": "true"})
    r_catalog = runner.call("packages_catalog_runtime", "GET", "/api/packages/catalog", expected=(200,))
    catalog_payload = _must_json(r_catalog)

    capability_id = "cap.alerts"
    sections = catalog_payload.get("sections") if isinstance(catalog_payload.get("sections"), list) else []
    for section in sections:
        if str(section.get("type") or "") != "capability":
            continue
        items = section.get("items") if isinstance(section.get("items"), list) else []
        if not items:
            continue
        picked = next((item for item in items if str(item.get("id") or "") == "cap.alerts"), items[0])
        capability_id = str(picked.get("id") or capability_id)
        break

    runner.call("packages_validate_capability", "GET", f"/api/packages/capability/{capability_id}/validate", expected=(200, 404))
    runner.call(
        "packages_deactivate_capability",
        "POST",
        f"/api/packages/capability/{capability_id}/deactivate",
        expected=(200, 409),
        json_body={"reason": "runtime_probe_deactivate"},
    )
    runner.call(
        "packages_history_capability",
        "GET",
        f"/api/packages/capability/{capability_id}/history",
        expected=(200,),
        params={"limit": 20},
    )
    runner.call(
        "packages_rollback_capability",
        "POST",
        f"/api/packages/capability/{capability_id}/rollback",
        expected=(200, 409),
        json_body={"reason": "runtime_probe_rollback"},
    )
    runner.call(
        "packages_activate_capability",
        "POST",
        f"/api/packages/capability/{capability_id}/activate",
        expected=(200, 409),
        json_body={"reason": "runtime_probe_restore"},
    )

    # skill pack lifecycle
    runner.call("skills_list", "GET", "/api/skills", expected=(200,))
    r_skill_packs = runner.call("skills_packs_list", "GET", "/api/skills/packs", expected=(200,))
    runner.call("skills_packs_reload", "POST", "/api/skills/packs/reload", expected=(200,))

    first_pack_id = "builtin.ops"
    packs = _must_json(r_skill_packs).get("packs") if isinstance(_must_json(r_skill_packs).get("packs"), list) else []
    if packs:
        first_pack_id = str((packs[0] or {}).get("id") or first_pack_id)
    runner.call("skills_pack_detail", "GET", f"/api/skills/packs/{first_pack_id}", expected=(200, 404))

    runner.call("skills_user_packs_list_before", "GET", "/api/skills/user/packs", expected=(200,))
    user_skill_code = f"runtime_skill_{suffix}"

    create_payload = {
        "skill_code": user_skill_code,
        "display_name": "Runtime账号技能",
        "description": "runtime probe user skill",
        "category": "custom",
        "system_prompt": "你是账号级技能执行器",
        "prompt_template": "请根据 {{question}} 输出结论",
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
        "output_mode": "json",
        "temperature": 0.2,
        "model": "",
        "note": "runtime_create",
    }

    runner.call(
        "skills_user_pack_create",
        "POST",
        "/api/skills/user/packs",
        expected=(200,),
        json_body=create_payload,
    )

    update_payload = dict(create_payload)
    update_payload["display_name"] = "Runtime账号技能V2"
    update_payload["note"] = "runtime_update"
    runner.call(
        "skills_user_pack_update",
        "PUT",
        f"/api/skills/user/packs/{user_skill_code}",
        expected=(200,),
        json_body=update_payload,
    )

    runner.call(
        "skills_user_pack_publish",
        "POST",
        f"/api/skills/user/packs/{user_skill_code}/publish",
        expected=(200,),
        json_body={"note": "runtime_publish"},
    )
    runner.call(
        "skills_user_pack_status_disable",
        "POST",
        f"/api/skills/user/packs/{user_skill_code}/status",
        expected=(200,),
        json_body={"status": "disabled", "note": "runtime_disable"},
    )
    runner.call(
        "skills_user_pack_versions",
        "GET",
        f"/api/skills/user/packs/{user_skill_code}/versions",
        expected=(200,),
    )
    runner.call(
        "skills_user_pack_rollback",
        "POST",
        f"/api/skills/user/packs/{user_skill_code}/rollback",
        expected=(200,),
        json_body={"version_no": 1, "note": "runtime_rollback"},
    )

    # run deterministic built-in skill
    r_skills = runner.call("skills_list_for_run", "GET", "/api/skills", expected=(200,))
    skill_name_candidates = [
        "coordination_agent_handoff",
        "coordination_status_query",
        "coordination_task_orchestration",
    ]
    listed_skills = _must_json(r_skills).get("skills") if isinstance(_must_json(r_skills).get("skills"), list) else []
    listed_names = {str(item.get("name") or "") for item in listed_skills}
    run_skill_name = next((name for name in skill_name_candidates if name in listed_names), "")
    if run_skill_name:
        runner.call(
            "skills_run_builtin",
            "POST",
            "/api/skills/run",
            expected=(200,),
            json_body={
                "skill_name": run_skill_name,
                "args": {"task_description": "请做一次多角色任务交接建议"},
            },
        )
        r_runs = runner.call("skills_runs", "GET", "/api/skills/runs", expected=(200,), params={"page": 1, "page_size": 10})
        runs = _must_json(r_runs).get("runs") if isinstance(_must_json(r_runs).get("runs"), list) else []
        run_id = _safe_int((runs[0] if runs else {}).get("id"))
        if run_id > 0:
            runner.call(
                "skills_feedback",
                "POST",
                "/api/skills/feedback",
                expected=(200,),
                json_body={"run_id": run_id, "rating": 5, "tags": ["runtime", "probe"]},
            )

    # 8) cleanup channel connection created for probe user
    runner.call("touch_channels_delete_wecom", "DELETE", "/api/platform-connect/channels/wecom", expected=(200,))

    # 9) report
    summary = runner.summary()
    report_dir = ROOT / "tools" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"full_feature_runtime_probe_{int(time.time())}.json"
    report_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 96)
    print(f"Runtime probe report: {report_path}")
    print(f"Total={summary['total']} Passed={summary['passed']} Failed={summary['failed']}")

    if summary["failed"]:
        print("\nFailed steps:")
        for item in summary["results"]:
            if not item.get("ok"):
                print(f"- {item['method']} {item['path']} status={item['status']} detail={item.get('detail', '')}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
