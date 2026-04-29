from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Sequence

import requests

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "tools" / "reports"
DEFAULT_BASE_URL = (
    os.getenv("DISPATCH_MATRIX_BASE_URL")
    or os.getenv("SATISFACTION_PROBE_BASE_URL")
    or "http://127.0.0.1:8100"
)


@dataclass
class Scenario:
    scenario_id: str
    message: str
    expected_primary: List[str]
    expected_support_any: List[str]
    expected_tool_use: bool = False
    manual_primary: str = "ops"
    manual_support: List[str] | None = None


@dataclass
class Strategy:
    strategy_id: str
    response_mode: str
    collaboration_mode: str
    learning_level: str


@dataclass
class MatrixRow:
    scenario_id: str
    strategy_id: str
    status: int
    elapsed_ms: int
    primary_role: str
    support_roles: List[str]
    skills_used: List[str]
    tool_calls_executed: int
    collab_input_requested_roles: int
    collab_scheduled_roles: int
    collab_completed_roles: int
    manual_support_schedule_match: bool
    manual_support_completion_match: bool
    ok_primary: bool
    ok_support: bool
    ok_collab: bool
    ok_tool: bool
    ok: bool
    fail_reasons: List[str]


def _register_or_login(session: requests.Session, base_url: str, email: str, password: str) -> str:
    register = session.post(
        f"{base_url}/api/auth/register",
        json={"email": email, "password": password, "name": "DispatchMatrixProbe"},
        timeout=30,
    )
    if register.status_code == 200:
        return str(register.json().get("token") or "")
    if register.status_code == 409:
        login = session.post(
            f"{base_url}/api/auth/login",
            json={"email": email, "password": password},
            timeout=30,
        )
        login.raise_for_status()
        return str(login.json().get("token") or "")
    register.raise_for_status()
    return ""


def _norm_roles(items: Sequence[str] | None) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for item in items or []:
        token = str(item or "").strip().lower()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _build_scenarios() -> List[Scenario]:
    return [
        Scenario(
            scenario_id="marketing_campaign",
            message="请给我一份直播间转化提升的营销方案，重点是投放、人群和素材。",
            expected_primary=["ops"],
            expected_support_any=["creative", "design"],
            manual_primary="ops",
            manual_support=["creative", "design", "data"],
        ),
        Scenario(
            scenario_id="data_diagnosis",
            message="请分析近14天漏斗转化率下降原因，给出分层诊断与验证SQL口径。",
            expected_primary=["data"],
            expected_support_any=["data", "accounting"],
            manual_primary="data",
            manual_support=["ops", "accounting", "service"],
        ),
        Scenario(
            scenario_id="service_crisis",
            message="最近差评和退款增多，请给客服SOP、安抚话术和升级处理流程。",
            expected_primary=["service"],
            expected_support_any=["service"],
            manual_primary="service",
            manual_support=["ops", "data", "creative"],
        ),
        Scenario(
            scenario_id="finance_risk",
            message="本月利润下滑且广告费超支，请做损益拆解和预算止损方案。",
            expected_primary=["accounting"],
            expected_support_any=["accounting", "data"],
            manual_primary="accounting",
            manual_support=["ops", "data", "service"],
        ),
        Scenario(
            scenario_id="design_ab",
            message="要做A/B素材测试，请给视觉方向、文案钩子和实验设计。",
            expected_primary=["design", "creative"],
            expected_support_any=["design", "creative"],
            manual_primary="design",
            manual_support=["creative", "ops", "data"],
        ),
        Scenario(
            scenario_id="seo_growth",
            message="店铺自然流量下滑，请给SEO关键词与标题优化方案，并给监控指标。",
            expected_primary=["web", "ops"],
            expected_support_any=["web"],
            manual_primary="web",
            manual_support=["ops", "creative", "data"],
        ),
        Scenario(
            scenario_id="tool_evidence",
            message="请调用工具检索近30天抖音家清类目ROI趋势，并给我P1/P2/P3执行动作和止损线。",
            expected_primary=["ops", "data"],
            expected_support_any=["data", "accounting", "web"],
            expected_tool_use=True,
            manual_primary="ops",
            manual_support=["data", "accounting", "web"],
        ),
    ]


def _build_strategies() -> List[Strategy]:
    return [
        Strategy(
            strategy_id="auto_execution",
            response_mode="execution",
            collaboration_mode="auto",
            learning_level="higher_vocational",
        ),
        Strategy(
            strategy_id="single_execution",
            response_mode="execution",
            collaboration_mode="single",
            learning_level="higher_vocational",
        ),
        Strategy(
            strategy_id="auto_learning",
            response_mode="learning",
            collaboration_mode="auto",
            learning_level="higher_vocational",
        ),
        Strategy(
            strategy_id="manual_execution",
            response_mode="execution",
            collaboration_mode="manual",
            learning_level="higher_vocational",
        ),
    ]


def _run_case(
    session: requests.Session,
    base_url: str,
    scenario: Scenario,
    strategy: Strategy,
    *,
    timeout_sec: int,
) -> MatrixRow:
    hired_roles: List[str] = []
    role = "ops"
    if strategy.collaboration_mode == "manual":
        role = str(scenario.manual_primary or "ops").strip().lower() or "ops"
        manual_support = _norm_roles(scenario.manual_support or [])
        hired_roles = _norm_roles([role, *manual_support])

    payload: Dict[str, Any] = {
        "message": scenario.message,
        "role": role,
        "response_mode": strategy.response_mode,
        "learning_level": strategy.learning_level,
        "collaboration_mode": strategy.collaboration_mode,
        "hired_roles": hired_roles,
    }

    t0 = time.time()
    status = 0
    elapsed_ms = 0
    body: Dict[str, Any] = {}
    fail_reasons: List[str] = []

    last_exc: Exception | None = None
    for attempt in range(3):
        try:
            resp = session.post(f"{base_url}/api/chat", json=payload, timeout=timeout_sec)
            elapsed_ms = int((time.time() - t0) * 1000)
            status = int(resp.status_code)
            if resp.status_code == 200:
                body = resp.json() if resp.content else {}
            else:
                fail_reasons.append(f"http_{resp.status_code}")
            last_exc = None
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= 2:
                elapsed_ms = int((time.time() - t0) * 1000)
                fail_reasons.append(f"request_error:{str(exc)[:180]}")
            else:
                time.sleep(1.2)

    meta = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    primary_role = str(meta.get("role") or "").strip().lower()
    support_roles = _norm_roles(meta.get("support_roles") if isinstance(meta.get("support_roles"), list) else [])
    role_set = set([primary_role, *support_roles])

    skills_used = _norm_roles(meta.get("skills_used") if isinstance(meta.get("skills_used"), list) else [])
    tool_calls_executed = int(meta.get("tool_calls_executed") or 0)

    collab_input = int(meta.get("collab_input_requested_roles") or 0)
    collab_scheduled = int(meta.get("collab_scheduled_roles") or 0)
    collab_completed = int(meta.get("collab_completed_roles") or 0)

    manual_schedule_match = bool(meta.get("manual_support_schedule_match"))
    manual_completion_match = bool(meta.get("manual_support_completion_match"))

    if strategy.collaboration_mode == "single":
        # single 模式默认以用户指定主角色直答，优先验证“稳定直答 + 不触发协作”。
        ok_primary = bool(primary_role)
    else:
        ok_primary = primary_role in _norm_roles(scenario.expected_primary)
    if strategy.collaboration_mode in {"single", "manual"}:
        ok_support = True
    else:
        ok_support = bool(role_set.intersection(set(_norm_roles(scenario.expected_support_any))))

    if strategy.collaboration_mode == "single":
        ok_collab = collab_scheduled == 0 and collab_completed == 0
    elif strategy.collaboration_mode == "manual":
        ok_collab = (
            collab_input >= 1
            and collab_scheduled >= 1
            and collab_completed >= 1
            and collab_completed <= collab_scheduled
            and manual_schedule_match
            and manual_completion_match
        )
    else:
        if collab_input > 0:
            ok_collab = collab_scheduled >= 1 and collab_completed >= 1 and collab_completed <= collab_scheduled
        else:
            ok_collab = collab_scheduled == 0 and collab_completed == 0

    if scenario.expected_tool_use:
        ok_tool = bool(tool_calls_executed > 0 or len(skills_used) > 0)
    else:
        ok_tool = True

    if last_exc is None and status != 200 and "http_" not in " ".join(fail_reasons):
        fail_reasons.append(f"http_{status}")
    if not ok_primary:
        fail_reasons.append(f"primary_mismatch:{primary_role}")
    if not ok_support:
        fail_reasons.append("support_mismatch")
    if not ok_collab:
        fail_reasons.append(
            f"collab_mismatch:input={collab_input},scheduled={collab_scheduled},completed={collab_completed}"
        )
    if not ok_tool:
        fail_reasons.append("tool_or_skill_not_triggered")

    ok = status == 200 and ok_primary and ok_support and ok_collab and ok_tool

    return MatrixRow(
        scenario_id=scenario.scenario_id,
        strategy_id=strategy.strategy_id,
        status=status,
        elapsed_ms=elapsed_ms,
        primary_role=primary_role,
        support_roles=support_roles,
        skills_used=skills_used,
        tool_calls_executed=tool_calls_executed,
        collab_input_requested_roles=collab_input,
        collab_scheduled_roles=collab_scheduled,
        collab_completed_roles=collab_completed,
        manual_support_schedule_match=manual_schedule_match,
        manual_support_completion_match=manual_completion_match,
        ok_primary=ok_primary,
        ok_support=ok_support,
        ok_collab=ok_collab,
        ok_tool=ok_tool,
        ok=ok,
        fail_reasons=fail_reasons,
    )


def _write_reports(base_url: str, rows: List[MatrixRow]) -> Dict[str, str]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    json_path = REPORT_DIR / f"dispatch_role_skill_matrix_{ts}.json"
    md_path = REPORT_DIR / f"dispatch_role_skill_matrix_{ts}.md"

    total = len(rows)
    passed = sum(1 for r in rows if r.ok)
    failed = total - passed

    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "base_url": base_url,
        "total": total,
        "passed": passed,
        "failed": failed,
        "rows": [asdict(r) for r in rows],
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines: List[str] = []
    lines.append("# Dispatch Role+Skill Matrix Probe")
    lines.append("")
    lines.append(f"- base_url: `{base_url}`")
    lines.append(f"- total: **{total}**")
    lines.append(f"- passed: **{passed}**")
    lines.append(f"- failed: **{failed}**")
    lines.append("")
    lines.append("| scenario | strategy | status | primary | support | sched/done | tool_calls | checks |")
    lines.append("|---|---|---:|---|---|---|---:|---|")
    for row in rows:
        checks = []
        checks.append("P" if row.ok_primary else "p")
        checks.append("S" if row.ok_support else "s")
        checks.append("C" if row.ok_collab else "c")
        checks.append("T" if row.ok_tool else "t")
        status_tag = "PASS" if row.ok else "FAIL"
        support = "+".join(row.support_roles) if row.support_roles else "-"
        lines.append(
            f"| {row.scenario_id} | {row.strategy_id} | {row.status} | {row.primary_role or '-'} | {support} | "
            f"{row.collab_scheduled_roles}/{row.collab_completed_roles} | {row.tool_calls_executed} | {status_tag} ({''.join(checks)}) |"
        )

    failures = [r for r in rows if not r.ok]
    lines.append("")
    lines.append("## Failures")
    if not failures:
        lines.append("- none")
    else:
        for row in failures:
            lines.append(
                f"- {row.scenario_id} / {row.strategy_id}: "
                f"{'; '.join(row.fail_reasons) if row.fail_reasons else 'unknown'}"
            )

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Dispatch role+skill matrix probe")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API base url")
    parser.add_argument("--timeout-sec", type=int, default=420, help="request timeout seconds")
    args = parser.parse_args()

    base_url = str(args.base_url or DEFAULT_BASE_URL).rstrip("/")
    timeout_sec = max(60, int(args.timeout_sec or 420))

    print(f"[INFO] dispatch_matrix_target={base_url}")

    session = requests.Session()
    email = f"dispatch_matrix_{uuid.uuid4().hex[:10]}@example.com"
    password = "Passw0rd!"
    token = _register_or_login(session, base_url, email, password)
    session.headers.update({"Authorization": f"Bearer {token}"})

    scenarios = _build_scenarios()
    strategies = _build_strategies()

    rows: List[MatrixRow] = []
    for scenario in scenarios:
        for strategy in strategies:
            print(
                f"[RUN] {scenario.scenario_id} / {strategy.strategy_id} "
                f"mode={strategy.response_mode} collab={strategy.collaboration_mode}"
            )
            row = _run_case(
                session,
                base_url,
                scenario,
                strategy,
                timeout_sec=timeout_sec,
            )
            rows.append(row)
            print(
                f"[{'PASS' if row.ok else 'FAIL'}] {scenario.scenario_id} / {strategy.strategy_id} "
                f"status={row.status} primary={row.primary_role or '-'} "
                f"sched={row.collab_scheduled_roles} done={row.collab_completed_roles} "
                f"tools={row.tool_calls_executed} elapsed={row.elapsed_ms}ms"
            )

    report_paths = _write_reports(base_url, rows)
    total = len(rows)
    passed = sum(1 for r in rows if r.ok)
    failed = total - passed

    print(f"Dispatch matrix JSON report: {report_paths['json']}")
    print(f"Dispatch matrix markdown report: {report_paths['md']}")
    print(f"Summary: total={total} passed={passed} failed={failed}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
