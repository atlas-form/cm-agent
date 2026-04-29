from __future__ import annotations

import argparse
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = os.getenv("ROLE_SKILL_QUALITY_BASE_URL") or os.getenv("SATISFACTION_PROBE_BASE_URL") or "http://127.0.0.1:8100"
REPORT_DIR = ROOT / "tools" / "reports"

_EVIDENCE_MARKERS = [
    "证据",
    "来源",
    "数据来源",
    "根据工具",
    "工具结果",
    "检索结果",
    "搜索结果",
    "返回结果",
    "接口返回",
]


@dataclass
class ScenarioResult:
    name: str
    ok: bool
    status: int
    elapsed_ms: int
    quality_score: float
    goal_satisfaction: float
    issues: List[str]
    critical_issues: List[str]
    checks: Dict[str, Any]


def _register_or_login(session: requests.Session, base_url: str, email: str, password: str) -> str:
    r = session.post(
        f"{base_url}/api/auth/register",
        json={"email": email, "password": password, "name": "RoleSkillQualityProbe"},
        timeout=30,
    )
    if r.status_code == 200:
        return str(r.json().get("token") or "")
    if r.status_code == 409:
        r = session.post(
            f"{base_url}/api/auth/login",
            json={"email": email, "password": password},
            timeout=30,
        )
        r.raise_for_status()
        return str(r.json().get("token") or "")
    r.raise_for_status()
    return ""


def _load_quality_checker():
    import sys

    server_src = ROOT / "server"
    if str(server_src) not in sys.path:
        sys.path.insert(0, str(server_src))
    from src.core.quality_checker import check_quality

    return check_quality


def _contains_any(text: str, markers: List[str]) -> bool:
    lowered = str(text or "").lower()
    return any(str(m or "").lower() in lowered for m in markers)


def _run_scenario(session: requests.Session, base_url: str, scenario: Dict[str, Any], check_quality) -> ScenarioResult:
    payload = {
        "message": scenario["message"],
        "role": scenario.get("role", "ops"),
        "response_mode": scenario.get("response_mode", "execution"),
        "learning_level": scenario.get("learning_level", "higher_vocational"),
        "collaboration_mode": scenario.get("collaboration_mode", "auto"),
        "hired_roles": scenario.get("hired_roles", []),
    }

    timeout_sec = int(scenario.get("timeout_sec", 300))
    max_retries = int(scenario.get("retries", 1))
    t0 = time.time()
    resp = None
    last_exc: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            resp = session.post(f"{base_url}/api/chat", json=payload, timeout=timeout_sec)
            last_exc = None
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= max_retries:
                break
            time.sleep(1.0)

    elapsed_ms = int((time.time() - t0) * 1000)
    if last_exc is not None or resp is None:
        return ScenarioResult(
            name=scenario["name"],
            ok=False,
            status=0,
            elapsed_ms=elapsed_ms,
            quality_score=0.0,
            goal_satisfaction=0.0,
            issues=[f"request_error: {last_exc}"],
            critical_issues=["请求异常，无法评估岗位/Skill回答质量"],
            checks={"timeout_sec": timeout_sec, "retries": max_retries},
        )

    if resp.status_code != 200:
        return ScenarioResult(
            name=scenario["name"],
            ok=False,
            status=resp.status_code,
            elapsed_ms=elapsed_ms,
            quality_score=0.0,
            goal_satisfaction=0.0,
            issues=[f"HTTP {resp.status_code}: {(resp.text or '')[:160]}"],
            critical_issues=["接口返回非200"],
            checks={"timeout_sec": timeout_sec, "retries": max_retries},
        )

    body = resp.json() if resp.content else {}
    reply = str(body.get("reply") or "")
    meta = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}

    skills_used_raw = meta.get("skills_used")
    skills_used = [str(x) for x in skills_used_raw if str(x).strip()] if isinstance(skills_used_raw, list) else []

    quality = check_quality(
        scenario["message"],
        reply,
        scenario.get("role", "ops"),
        action=scenario.get("action", ""),
        tool_used=bool(skills_used),
    )

    qscore = float(quality.score)
    goal = float(quality.dimensions.get("goal_satisfaction", 0.0))

    required_keywords_any = [str(x).strip() for x in scenario.get("required_keywords_any", []) if str(x).strip()]
    expected_tool_use = bool(scenario.get("expected_tool_use", False))

    checks: Dict[str, Any] = {
        "skills_used": skills_used,
        "skills_used_count": len(skills_used),
        "quality_retry_applied": bool(meta.get("quality_retry_applied")),
        "response_mode": meta.get("response_mode"),
        "response_action_count": meta.get("response_action_count"),
        "reply_length": len(reply),
        "timeout_sec": timeout_sec,
        "retries": max_retries,
    }

    critical_issues: List[str] = []
    issues: List[str] = list(quality.issues[:6])

    if expected_tool_use and not skills_used:
        issues.append("期望触发Skill/工具调用，但本次未检测到skills_used")

    if skills_used:
        evidence_hit = _contains_any(reply, _EVIDENCE_MARKERS)
        checks["evidence_marker_hit"] = evidence_hit
        if not evidence_hit:
            critical_issues.append("检测到skills_used但回复缺少证据映射描述")

    if required_keywords_any and not _contains_any(reply, required_keywords_any):
        issues.append("回复未命中岗位契约关键词（建议检查结构化输出）")

    quality_floor = float(scenario.get("quality_floor", 0.74))
    goal_floor = float(scenario.get("goal_floor", 0.70))

    checks["quality_floor"] = quality_floor
    checks["goal_floor"] = goal_floor

    ok = (qscore >= quality_floor) and (goal >= goal_floor) and (len(critical_issues) == 0)

    return ScenarioResult(
        name=scenario["name"],
        ok=ok,
        status=resp.status_code,
        elapsed_ms=elapsed_ms,
        quality_score=round(qscore, 3),
        goal_satisfaction=round(goal, 3),
        issues=issues + critical_issues,
        critical_issues=critical_issues,
        checks=checks,
    )


def _build_scenarios() -> List[Dict[str, Any]]:
    return [
        {
            "name": "ops_tool_evidence_plan",
            "role": "ops",
            "action": "plan",
            "response_mode": "execution",
            "message": "请调用工具检索近30天抖音家清类目ROI趋势，并给我P1/P2/P3执行动作和止损线。",
            "expected_tool_use": True,
            "required_keywords_any": ["P1", "P2", "P3", "止损", "复盘"],
            "quality_floor": 0.75,
            "goal_floor": 0.72,
            "retries": 2,
        },
        {
            "name": "data_diagnosis_contract",
            "role": "data",
            "action": "analysis",
            "response_mode": "execution",
            "message": "近7天转化下滑，请输出口径定义、诊断假设、验证方案和优先级。",
            "required_keywords_any": ["口径", "假设", "验证", "优先级"],
            "quality_floor": 0.74,
            "goal_floor": 0.70,
            "retries": 1,
        },
        {
            "name": "service_playbook_contract",
            "role": "service",
            "action": "execute",
            "response_mode": "execution",
            "message": "给我客服差评危机处置SOP：先安抚再解决，含升级触发和时效约束。",
            "required_keywords_any": ["话术", "升级", "时效", "SLA"],
            "quality_floor": 0.74,
            "goal_floor": 0.70,
            "retries": 1,
        },
        {
            "name": "accounting_risk_contract",
            "role": "accounting",
            "action": "optimize",
            "response_mode": "execution",
            "message": "请给预算控损方案：固定/可变成本拆解，利润敏感性分析，风控阈值与合规提示。",
            "required_keywords_any": ["固定", "可变", "阈值", "合规"],
            "quality_floor": 0.75,
            "goal_floor": 0.72,
            "retries": 2,
        },
        {
            "name": "engineering_release_contract",
            "role": "engineering",
            "action": "optimize",
            "response_mode": "execution",
            "message": "给一版发布方案：变更影响面、监控验收门槛、故障回滚流程。",
            "required_keywords_any": ["影响", "监控", "验收", "回滚"],
            "quality_floor": 0.74,
            "goal_floor": 0.70,
            "retries": 1,
        },
        {
            "name": "design_ab_contract",
            "role": "design",
            "action": "create",
            "response_mode": "execution",
            "message": "请输出详情页改版方案：P1/P2/P3优先级、A/B测试矩阵、阈值和回滚条件。",
            "required_keywords_any": ["P1", "A/B", "阈值", "回滚"],
            "quality_floor": 0.74,
            "goal_floor": 0.70,
            "retries": 1,
        },
    ]


def _write_reports(base_url: str, results: List[ScenarioResult]) -> Dict[str, str]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())

    passed = sum(1 for r in results if r.ok)
    total = len(results)
    summary = {
        "base_url": base_url,
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "critical_failures": int(sum(len(r.critical_issues) for r in results)),
        "avg_quality_score": round(sum(r.quality_score for r in results) / max(total, 1), 3),
        "avg_goal_satisfaction": round(sum(r.goal_satisfaction for r in results) / max(total, 1), 3),
        "results": [
            {
                "name": r.name,
                "ok": r.ok,
                "status": r.status,
                "elapsed_ms": r.elapsed_ms,
                "quality_score": r.quality_score,
                "goal_satisfaction": r.goal_satisfaction,
                "issues": r.issues,
                "critical_issues": r.critical_issues,
                "checks": r.checks,
            }
            for r in results
        ],
    }

    json_path = REPORT_DIR / f"role_skill_quality_probe_{ts}.json"
    md_path = REPORT_DIR / f"role_skill_quality_probe_{ts}.md"

    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines: List[str] = []
    md_lines.append("# Role + Skill Quality Probe")
    md_lines.append("")
    md_lines.append(f"- base_url: `{base_url}`")
    md_lines.append(f"- total: **{total}**  passed: **{passed}**  failed: **{total - passed}**")
    md_lines.append(f"- critical_failures: **{summary['critical_failures']}**")
    md_lines.append(f"- avg_quality_score: **{summary['avg_quality_score']}**")
    md_lines.append(f"- avg_goal_satisfaction: **{summary['avg_goal_satisfaction']}**")
    md_lines.append("")
    md_lines.append("| scenario | ok | status | quality | goal | skills_used | key_issues |")
    md_lines.append("|---|---:|---:|---:|---:|---:|---|")
    for r in results:
        issue = "；".join(r.issues[:2]) if r.issues else ""
        skills_used_count = int((r.checks or {}).get("skills_used_count") or 0)
        md_lines.append(
            f"| {r.name} | {'Y' if r.ok else 'N'} | {r.status} | {r.quality_score:.3f} | {r.goal_satisfaction:.3f} | {skills_used_count} | {issue} |"
        )

    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run role+skill answer quality probe")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API base url, e.g. http://127.0.0.1:8233")
    args = parser.parse_args(argv)

    base_url = str(args.base_url or DEFAULT_BASE_URL).rstrip("/")
    print(f"[INFO] role_skill_quality_probe_target={base_url}")

    session = requests.Session()
    suffix = uuid.uuid4().hex[:8]
    email = f"role_skill_quality_probe_{suffix}@example.com"
    password = "RoleSkillQualityProbe123!"

    token = _register_or_login(session, base_url, email, password)
    if not token:
        print("[FAIL] failed to acquire auth token")
        return 2
    session.headers.update({"Authorization": f"Bearer {token}"})

    check_quality = _load_quality_checker()
    results: List[ScenarioResult] = []

    for scenario in _build_scenarios():
        result = _run_scenario(session, base_url, scenario, check_quality)
        results.append(result)
        print(
            f"[{'PASS' if result.ok else 'FAIL'}] {result.name:<30} "
            f"quality={result.quality_score:.3f} goal={result.goal_satisfaction:.3f} "
            f"critical={len(result.critical_issues)} status={result.status} elapsed={result.elapsed_ms}ms"
        )

    outputs = _write_reports(base_url, results)
    passed = sum(1 for item in results if item.ok)

    print("")
    print(f"Role-skill quality JSON report: {outputs['json']}")
    print(f"Role-skill quality markdown report: {outputs['md']}")
    print(f"Summary: total={len(results)} passed={passed} failed={len(results) - passed}")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
