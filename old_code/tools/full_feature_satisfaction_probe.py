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
DEFAULT_BASE_URL = os.getenv("SATISFACTION_PROBE_BASE_URL") or os.getenv("SMOKE_BASE_URL") or "http://127.0.0.1:8100"
REPORT_DIR = ROOT / "tools" / "reports"

_DEFAULT_RISK_MARKERS = [
    "风险",
    "止损",
    "阈值",
    "回滚",
    "前提",
    "假设",
    "需人工",
    "复核",
    "免责声明",
    "仅供参考",
    "告警",
    "兜底",
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
        json={"email": email, "password": password, "name": "SatisfactionProbe"},
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
    content = str(text or "").strip().lower()
    if not content:
        return False
    for marker in markers:
        token = str(marker or "").strip().lower()
        if token and token in content:
            return True
    return False


def _is_string_list(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    return all(isinstance(item, str) for item in value)


def _parse_scenario_filters(raw_filters: List[str]) -> List[str]:
    normalized: List[str] = []
    for raw in raw_filters:
        for token in str(raw or "").split(","):
            name = token.strip()
            if name:
                normalized.append(name)
    # Keep order while de-duplicating.
    return list(dict.fromkeys(normalized))


def _filter_scenarios(scenarios: List[Dict[str, Any]], selected_names: List[str]) -> List[Dict[str, Any]]:
    if not selected_names:
        return scenarios
    allowed = set(selected_names)
    return [item for item in scenarios if str(item.get("name") or "") in allowed]


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
        except Exception as exc:
            last_exc = exc
            if attempt >= max_retries:
                break

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
            critical_issues=["请求异常，无法评估结果满意度"],
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
            critical_issues=["接口返回非200，场景不可用"],
            checks={},
        )

    body = resp.json() if resp.content else {}
    reply = str(body.get("reply") or "")
    meta = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}

    quality = check_quality(
        scenario["message"],
        reply,
        scenario.get("role", "ops"),
        action=scenario.get("action", ""),
    )

    goal = float(quality.dimensions.get("goal_satisfaction", 0.0))
    qscore = float(quality.score)
    response_mode = str(meta.get("response_mode") or scenario.get("response_mode") or "execution")
    action_points = int(meta.get("response_action_count") or 0)
    learning_interaction_enabled = bool(meta.get("learning_interaction_enabled"))
    learning_guard_applied = meta.get("learning_delivery_guard_applied")
    learning_guard_sources = meta.get("learning_delivery_guard_sources")
    learning_guard_gaps = meta.get("learning_delivery_guard_gaps")
    analysis_guard_applied = meta.get("analysis_delivery_guard_applied")
    analysis_guard_sources = meta.get("analysis_delivery_guard_sources")
    analysis_guard_gaps = meta.get("analysis_delivery_guard_gaps")
    design_guard_applied = meta.get("design_delivery_guard_applied")
    design_guard_sources = meta.get("design_delivery_guard_sources")
    design_guard_gaps = meta.get("design_delivery_guard_gaps")

    checks: Dict[str, Any] = {
        "response_mode": response_mode,
        "response_action_count": action_points,
        "quality_retry_applied": bool(meta.get("quality_retry_applied")),
        "quality_score_before_retry": meta.get("quality_score_before_retry"),
        "quality_score_after_retry": meta.get("quality_score_after_retry"),
        "learning_interaction_enabled": learning_interaction_enabled,
        "reply_length": len(reply),
        "learning_delivery_guard_applied": learning_guard_applied,
        "learning_delivery_guard_sources_count": len(learning_guard_sources) if isinstance(learning_guard_sources, list) else None,
        "learning_delivery_guard_gap_keys": sorted(learning_guard_gaps.keys()) if isinstance(learning_guard_gaps, dict) else None,
        "analysis_delivery_guard_applied": analysis_guard_applied,
        "analysis_delivery_guard_sources_count": len(analysis_guard_sources) if isinstance(analysis_guard_sources, list) else None,
        "analysis_delivery_guard_gap_keys": sorted(analysis_guard_gaps.keys()) if isinstance(analysis_guard_gaps, dict) else None,
        "design_delivery_guard_applied": design_guard_applied,
        "design_delivery_guard_sources_count": len(design_guard_sources) if isinstance(design_guard_sources, list) else None,
        "design_delivery_guard_gap_keys": sorted(design_guard_gaps.keys()) if isinstance(design_guard_gaps, dict) else None,
    }

    noncritical_issues: List[str] = []
    critical_issues: List[str] = []

    min_action_points = int(scenario.get("min_action_points", 2))
    min_reply_chars = int(scenario.get("min_reply_chars", 120))

    if response_mode == "execution" and action_points < min_action_points:
        critical_issues.append(f"执行模式 action points 少于{min_action_points}，可执行性不足")

    if response_mode == "learning" and not learning_interaction_enabled:
        critical_issues.append("教学模式未启用互动引导")

    if bool(scenario.get("require_learning_guard_observability", False)):
        if not isinstance(learning_guard_applied, bool):
            critical_issues.append("学习补全守卫元数据缺失：learning_delivery_guard_applied 非布尔值")
        if not _is_string_list(learning_guard_sources):
            critical_issues.append("学习补全守卫元数据缺失：learning_delivery_guard_sources 非字符串列表")
        if not isinstance(learning_guard_gaps, dict):
            critical_issues.append("学习补全守卫元数据缺失：learning_delivery_guard_gaps 非字典")

    if bool(scenario.get("require_analysis_guard_observability", False)):
        if not isinstance(analysis_guard_applied, bool):
            critical_issues.append("诊断补全守卫元数据缺失：analysis_delivery_guard_applied 非布尔值")
        if not _is_string_list(analysis_guard_sources):
            critical_issues.append("诊断补全守卫元数据缺失：analysis_delivery_guard_sources 非字符串列表")
        if not isinstance(analysis_guard_gaps, dict):
            critical_issues.append("诊断补全守卫元数据缺失：analysis_delivery_guard_gaps 非字典")

    if bool(scenario.get("require_design_guard_observability", False)):
        if not isinstance(design_guard_applied, bool):
            critical_issues.append("设计补全守卫元数据缺失：design_delivery_guard_applied 非布尔值")
        if not _is_string_list(design_guard_sources):
            critical_issues.append("设计补全守卫元数据缺失：design_delivery_guard_sources 非字符串列表")
        if not isinstance(design_guard_gaps, dict):
            critical_issues.append("设计补全守卫元数据缺失：design_delivery_guard_gaps 非字典")

    if len(reply) < min_reply_chars:
        noncritical_issues.append(f"回复长度不足（{len(reply)}<{min_reply_chars}），信息密度偏低")

    required_keywords_any = [str(x).strip() for x in scenario.get("required_keywords_any", []) if str(x).strip()]
    if required_keywords_any and not _contains_any(reply, required_keywords_any):
        noncritical_issues.append("回复缺少关键主题词（未命中预期关键词）")

    if bool(scenario.get("risk_guardrails_required", False)):
        risk_markers = [str(x).strip() for x in scenario.get("risk_markers", _DEFAULT_RISK_MARKERS) if str(x).strip()]
        if not _contains_any(reply, risk_markers):
            critical_issues.append("涉及风险议题，但未识别到风险边界/免责声明提示")
        checks["risk_markers_hit"] = _contains_any(reply, risk_markers)

    quality_floor = float(scenario.get("quality_floor", 0.72))
    goal_floor = float(scenario.get("goal_floor", 0.68))

    checks["quality_floor"] = quality_floor
    checks["goal_floor"] = goal_floor
    checks["critical_issue_count"] = len(critical_issues)

    ok = (qscore >= quality_floor) and (goal >= goal_floor) and (len(critical_issues) == 0) and (len(noncritical_issues) == 0)
    issues = list(quality.issues[:6]) + noncritical_issues + critical_issues

    return ScenarioResult(
        name=scenario["name"],
        ok=ok,
        status=resp.status_code,
        elapsed_ms=elapsed_ms,
        quality_score=round(qscore, 3),
        goal_satisfaction=round(goal, 3),
        issues=issues,
        critical_issues=critical_issues,
        checks=checks,
    )


def _build_scenarios() -> List[Dict[str, Any]]:
    return [
        {
            "name": "ops_execution_week_plan",
            "role": "ops",
            "action": "plan",
            "response_mode": "execution",
            "message": "请给我一个本周可执行的抖音店铺增长计划，预算2万元，目标是GMV提升20%。",
            "quality_floor": 0.75,
            "goal_floor": 0.72,
            "min_action_points": 3,
            "min_reply_chars": 180,
        },
        {
            "name": "data_execution_diagnosis",
            "role": "data",
            "action": "analysis",
            "response_mode": "execution",
            "message": "近7天点击率下降了，帮我做一个可验证的诊断框架，要求有优先级和验证指标。",
            "quality_floor": 0.74,
            "goal_floor": 0.70,
            "min_action_points": 3,
            "min_reply_chars": 170,
            "require_analysis_guard_observability": True,
        },
        {
            "name": "learning_undergraduate_explain",
            "role": "ops",
            "action": "learning",
            "response_mode": "learning",
            "learning_level": "undergraduate",
            "message": "请教学解释ROI和ROAS的区别，给我一个电商场景例子和常见误区。",
            "quality_floor": 0.74,
            "goal_floor": 0.68,
            "min_reply_chars": 180,
            "require_learning_guard_observability": True,
        },
        {
            "name": "manual_collaboration_strategy",
            "role": "ops",
            "action": "plan",
            "response_mode": "execution",
            "collaboration_mode": "manual",
            "hired_roles": ["data", "service"],
            "message": "我准备做618预热，请你按运营+数据+客服协同给一个分工执行方案。",
            "quality_floor": 0.74,
            "goal_floor": 0.70,
            "min_action_points": 3,
            "min_reply_chars": 180,
        },
        {
            "name": "accounting_risk_control",
            "role": "accounting",
            "action": "optimize",
            "response_mode": "execution",
            "message": "请给我一个降低投放亏损风险的预算控制方案，要求有止损规则与回滚条件。",
            "quality_floor": 0.76,
            "goal_floor": 0.72,
            "min_action_points": 3,
            "risk_guardrails_required": True,
            "min_reply_chars": 180,
        },
        {
            "name": "service_complaint_playbook",
            "role": "service",
            "action": "execute",
            "response_mode": "execution",
            "message": "遇到连续3条差评，给我一个可直接执行的话术与处理SOP。",
            "quality_floor": 0.74,
            "goal_floor": 0.70,
            "min_action_points": 3,
            "min_reply_chars": 160,
        },
        {
            "name": "engineering_stability_fix_plan",
            "role": "engineering",
            "action": "optimize",
            "response_mode": "execution",
            "message": "大促前夜系统偶发超时，请给出可执行的稳定性治理方案，包含观测、限流、回滚。",
            "quality_floor": 0.73,
            "goal_floor": 0.69,
            "min_action_points": 3,
            "risk_guardrails_required": True,
            "min_reply_chars": 170,
        },
        {
            "name": "design_conversion_refresh",
            "role": "design",
            "action": "create",
            "response_mode": "execution",
            "message": "请给我一个电商详情页改版方案，目标是提升转化，输出版块优先级和AB测试建议。",
            "quality_floor": 0.73,
            "goal_floor": 0.69,
            "min_action_points": 3,
            "min_reply_chars": 170,
            "require_design_guard_observability": True,
        },
        {
            "name": "web_seo_topic_cluster",
            "role": "web",
            "action": "plan",
            "response_mode": "execution",
            "message": "为新品做一个30天SEO内容集群计划，要求包含关键词层级与内链策略。",
            "quality_floor": 0.73,
            "goal_floor": 0.69,
            "min_action_points": 3,
            "min_reply_chars": 170,
        },
        {
            "name": "creative_short_video_hooks",
            "role": "creative",
            "action": "create",
            "response_mode": "execution",
            "message": "给我10条短视频开场钩子，面向家居清洁类目，并按转化潜力分级。",
            "quality_floor": 0.72,
            "goal_floor": 0.68,
            "min_action_points": 2,
            "min_reply_chars": 140,
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

    json_path = REPORT_DIR / f"full_feature_satisfaction_probe_{ts}.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines: List[str] = []
    md_lines.append("# Full Feature Satisfaction Probe")
    md_lines.append("")
    md_lines.append(f"- base_url: `{base_url}`")
    md_lines.append(f"- total: **{total}**  passed: **{passed}**  failed: **{total - passed}**")
    md_lines.append(f"- critical_failures: **{summary['critical_failures']}**")
    md_lines.append(f"- avg_quality_score: **{summary['avg_quality_score']}**")
    md_lines.append(f"- avg_goal_satisfaction: **{summary['avg_goal_satisfaction']}**")
    md_lines.append("")
    md_lines.append("## Scenario Results")
    md_lines.append("")
    md_lines.append("| scenario | ok | status | elapsed_ms | quality | goal_satisfaction | key_issues |")
    md_lines.append("|---|---:|---:|---:|---:|---:|---|")
    for r in results:
        issue = "；".join(r.issues[:2]) if r.issues else ""
        md_lines.append(
            f"| {r.name} | {'Y' if r.ok else 'N'} | {r.status} | {r.elapsed_ms} | {r.quality_score:.3f} | {r.goal_satisfaction:.3f} | {issue} |"
        )

    md_path = REPORT_DIR / f"full_feature_satisfaction_probe_{ts}.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    return {"json": str(json_path), "md": str(md_path)}


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run feature satisfaction probe on /api/chat scenarios")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API base url, e.g. http://127.0.0.1:8233")
    parser.add_argument(
        "--scenario",
        action="append",
        default=[],
        help="Only run specific scenario name(s); repeat option or pass comma-separated values",
    )
    parser.add_argument("--list-scenarios", action="store_true", help="List available scenario names and exit")
    args = parser.parse_args(argv)

    base_url = str(args.base_url or DEFAULT_BASE_URL).rstrip("/")
    print(f"[INFO] satisfaction_probe_target={base_url}")

    scenarios = _build_scenarios()
    all_names = [str(item.get("name") or "") for item in scenarios]
    if bool(args.list_scenarios):
        print("Available scenarios:")
        for name in all_names:
            print(f"- {name}")
        return 0

    selected_names = _parse_scenario_filters(list(args.scenario or []))
    scenarios = _filter_scenarios(scenarios, selected_names)
    if not scenarios:
        print(f"[FAIL] no scenario selected; requested={selected_names or ['<none>']}")
        print(f"[INFO] available_scenarios={all_names}")
        return 2
    if selected_names:
        print(f"[INFO] selected_scenarios={selected_names}")

    session = requests.Session()
    suffix = uuid.uuid4().hex[:8]
    email = f"satisfaction_probe_{suffix}@example.com"
    password = "SatisfactionProbe123!"

    token = _register_or_login(session, base_url, email, password)
    if not token:
        print("[FAIL] failed to acquire auth token")
        return 2
    session.headers.update({"Authorization": f"Bearer {token}"})

    check_quality = _load_quality_checker()
    results: List[ScenarioResult] = []

    for item in scenarios:
        result = _run_scenario(session, base_url, item, check_quality)
        results.append(result)
        print(
            f"[{'PASS' if result.ok else 'FAIL'}] {result.name:<34} "
            f"quality={result.quality_score:.3f} goal={result.goal_satisfaction:.3f} "
            f"critical={len(result.critical_issues)} status={result.status} elapsed={result.elapsed_ms}ms"
        )

    out = _write_reports(base_url, results)
    passed = sum(1 for r in results if r.ok)
    print("")
    print(f"Satisfaction probe JSON report: {out['json']}")
    print(f"Satisfaction probe markdown report: {out['md']}")
    print(f"Summary: total={len(results)} passed={passed} failed={len(results) - passed}")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
