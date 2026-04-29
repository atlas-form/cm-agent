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
DEFAULT_BASE_URL = os.getenv("USER_EXPERIENCE_PROBE_BASE_URL") or "http://127.0.0.1:8100"
REPORT_DIR = ROOT / "tools" / "reports"


@dataclass
class TurnEval:
    turn_index: int
    status: int
    elapsed_ms: int
    ok: bool
    quality_score: float
    goal_satisfaction: float
    value_density: float
    issues: List[str]
    checks: Dict[str, Any]
    message: str
    reply: str


@dataclass
class ScenarioEval:
    name: str
    ok: bool
    elapsed_ms: int
    turns: List[TurnEval]
    critical_issues: List[str]


def _register_or_login(session: requests.Session, base_url: str, email: str, password: str) -> str:
    r = session.post(
        f"{base_url}/api/auth/register",
        json={"email": email, "password": password, "name": "UserExperienceProbe"},
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
    return any(str(m or "").strip().lower() in lowered for m in markers)


def _run_turn(
    session: requests.Session,
    base_url: str,
    check_quality,
    scenario: Dict[str, Any],
    turn: Dict[str, Any],
    conversation_id: str,
) -> tuple[TurnEval, str]:
    payload: Dict[str, Any] = {
        "message": str(turn.get("message") or ""),
        "role": str(turn.get("role") or scenario.get("role") or "ops"),
        "response_mode": str(turn.get("response_mode") or scenario.get("response_mode") or "execution"),
        "learning_level": str(turn.get("learning_level") or scenario.get("learning_level") or "higher_vocational"),
        "collaboration_mode": str(turn.get("collaboration_mode") or scenario.get("collaboration_mode") or "auto"),
        "hired_roles": list(turn.get("hired_roles") or scenario.get("hired_roles") or []),
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id
    if isinstance(turn.get("attachments"), list):
        payload["attachments"] = turn.get("attachments")

    t0 = time.time()
    resp = session.post(f"{base_url}/api/chat", json=payload, timeout=int(turn.get("timeout_sec", 360)))
    elapsed_ms = int((time.time() - t0) * 1000)

    reply = ""
    new_conversation_id = conversation_id
    meta: Dict[str, Any] = {}
    status = int(resp.status_code)
    issues: List[str] = []

    if status == 200:
        data = resp.json() if resp.content else {}
        reply = str(data.get("reply") or "")
        new_conversation_id = str(data.get("conversation_id") or conversation_id or "")
        meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    else:
        issues.append(f"http_status_{status}")

    q = check_quality(
        payload["message"],
        reply,
        payload["role"],
        action=str(turn.get("action") or scenario.get("action") or ""),
        tool_used=bool(meta.get("tool_used")),
    )

    quality_score = float(getattr(q, "score", 0.0))
    dims = getattr(q, "dimensions", {}) if hasattr(q, "dimensions") else {}
    goal = float(dims.get("goal_satisfaction", 0.0))
    value_density = float(dims.get("value_density", 0.0))
    issues.extend(list(getattr(q, "issues", [])))

    forbidden_markers = ["用途判断", "系统校准", "执行补全（系统保障）"]
    if _contains_any(reply, forbidden_markers):
        issues.append("reply_exposes_mechanical_system_labels")

    min_quality = float(turn.get("min_quality", scenario.get("min_quality", 0.74)))
    min_goal = float(turn.get("min_goal", scenario.get("min_goal", 0.70)))
    action_floor = int(turn.get("min_action_points", scenario.get("min_action_points", 0)))
    action_points = int(meta.get("response_action_count") or 0)

    source_mapping_request = _contains_any(payload["message"], ["来源", "日期", "每条", "对应"]) and _contains_any(payload["message"], ["继续", "刚才", "上面"])
    if action_floor > 0 and action_points < action_floor and not source_mapping_request:
        issues.append(f"response_action_count_below_floor:{action_points}<{action_floor}")

    if quality_score < min_quality:
        issues.append(f"quality_below_floor:{quality_score:.3f}<{min_quality:.2f}")
    if goal < min_goal:
        issues.append(f"goal_below_floor:{goal:.3f}<{min_goal:.2f}")

    required_keywords_any = [str(x).strip() for x in turn.get("required_keywords_any", []) if str(x).strip()]
    if required_keywords_any and not _contains_any(reply, required_keywords_any):
        issues.append("missing_required_keywords")

    ok = len(issues) == 0 and status == 200
    checks = {
        "response_mode": payload["response_mode"],
        "learning_level": payload["learning_level"],
        "collaboration_mode": payload["collaboration_mode"],
        "response_action_count": action_points,
        "task_should_clarify": bool(meta.get("task_should_clarify")),
        "quality_retry_applied": bool(meta.get("quality_retry_applied")),
        "quality_floor": min_quality,
        "goal_floor": min_goal,
        "reply_length": len(reply),
    }

    return (
        TurnEval(
            turn_index=int(turn.get("turn") or 0),
            status=status,
            elapsed_ms=elapsed_ms,
            ok=ok,
            quality_score=round(quality_score, 3),
            goal_satisfaction=round(goal, 3),
            value_density=round(value_density, 3),
            issues=issues,
            checks=checks,
            message=payload["message"],
            reply=reply,
        ),
        new_conversation_id,
    )


def _run_scenario(session: requests.Session, base_url: str, scenario: Dict[str, Any], check_quality) -> ScenarioEval:
    t0 = time.time()
    conversation_id = ""
    turns: List[TurnEval] = []
    critical: List[str] = []

    for i, t in enumerate(list(scenario.get("turns") or []), start=1):
        turn = dict(t)
        turn.setdefault("turn", i)
        result, conversation_id = _run_turn(session, base_url, check_quality, scenario, turn, conversation_id)
        turns.append(result)
        if not result.ok:
            critical.append(f"turn{result.turn_index}: {'；'.join(result.issues[:3])}")

    elapsed_ms = int((time.time() - t0) * 1000)
    return ScenarioEval(
        name=str(scenario.get("name") or "unnamed"),
        ok=(len(critical) == 0 and len(turns) > 0),
        elapsed_ms=elapsed_ms,
        turns=turns,
        critical_issues=critical,
    )


def _build_scenarios() -> List[Dict[str, Any]]:
    return [
        {
            "name": "ops_execution_multiturn_growth_plan",
            "role": "ops",
            "action": "plan",
            "response_mode": "execution",
            "collaboration_mode": "auto",
            "hired_roles": ["data", "service"],
            "turns": [
                {"message": "我是抖音小店，客单79，近7天转化1.1%，请给7天提升到1.8%的执行计划。", "min_action_points": 2},
                {"message": "继续上面方案，压缩成今天必须完成的3件事，并给明日复盘口径。", "min_action_points": 2, "required_keywords_any": ["今天", "明日", "复盘"]},
                {"message": "再继续：给每一步补上风险触发阈值和回滚条件。", "required_keywords_any": ["风险", "回滚", "阈值"]},
            ],
        },
        {
            "name": "data_realdata_evidence_multiturn",
            "role": "data",
            "action": "analysis",
            "response_mode": "execution",
            "collaboration_mode": "single",
            "turns": [
                {"message": "星巴克在国内份额下滑，请基于真实市场数据给洞察，明确口径和假设。", "required_keywords_any": ["口径", "假设", "来源"]},
                {"message": "请继续：把你每条关键结论对应的数据来源和日期逐条列出来。", "required_keywords_any": ["来源", "日期", "对应"]},
            ],
        },
        {
            "name": "service_complaint_multiturn",
            "role": "service",
            "action": "execute",
            "response_mode": "execution",
            "collaboration_mode": "manual",
            "hired_roles": ["ops"],
            "turns": [
                {"message": "出现批量延迟发货投诉，给我危机处置SOP。", "required_keywords_any": ["SOP", "时效", "升级"]},
                {"message": "继续：给我客服IM首轮安抚和二轮跟进话术，可直接复制。", "required_keywords_any": ["首轮", "跟进", "话术"]},
            ],
        },
        {
            "name": "accounting_cashflow_risk",
            "role": "accounting",
            "action": "analysis",
            "response_mode": "execution",
            "turns": [
                {"message": "下季度要打大促，帮我做现金流风险预警框架，给阈值和止损线。", "required_keywords_any": ["阈值", "止损", "预警"]},
                {"message": "继续：如果广告费超支20%，优先砍哪些预算并给回滚策略。", "required_keywords_any": ["优先", "预算", "回滚"]},
            ],
        },
        {
            "name": "engineering_release_multiturn",
            "role": "engineering",
            "action": "diagnosis",
            "response_mode": "execution",
            "turns": [
                {"message": "昨晚发布后接口超时上升，给我排障优先级和回滚门禁。", "required_keywords_any": ["优先级", "回滚", "门禁"]},
                {"message": "继续：把2小时内值班执行清单列出来。", "min_action_points": 2, "required_keywords_any": ["2小时", "清单"]},
            ],
        },
        {
            "name": "design_conversion_multiturn",
            "role": "design",
            "action": "optimize",
            "response_mode": "execution",
            "turns": [
                {"message": "请给我详情页改版方案，目标转化提升8%，要有AB测试和验收阈值。", "required_keywords_any": ["A/B", "阈值", "验收"]},
                {"message": "继续：把首屏和权益区的文案/视觉优先级再压缩成P1-P3。", "required_keywords_any": ["P1", "P2", "P3"]},
            ],
        },
        {
            "name": "web_seo_multiturn",
            "role": "web",
            "action": "plan",
            "response_mode": "execution",
            "turns": [
                {"message": "给我做30天SEO增长计划，包含关键词层级、内链和内容集群节奏。", "required_keywords_any": ["关键词", "内链", "集群"]},
                {"message": "继续：给每周复盘模板，标明保留/下线规则。", "required_keywords_any": ["复盘", "保留", "下线"]},
            ],
        },
        {
            "name": "creative_copyready_multiturn",
            "role": "creative",
            "action": "create",
            "response_mode": "execution",
            "turns": [
                {"message": "给我一版小红书种草文案，可直接复制，卖点是便携榨汁杯。", "required_keywords_any": ["标题", "正文", "标签"]},
                {"message": "继续：改成“上班妈妈通勤场景”版本，语气更有共鸣。", "required_keywords_any": ["通勤", "妈妈", "版本"]},
            ],
        },
        {
            "name": "learning_undergraduate_multiturn",
            "role": "data",
            "action": "learning",
            "response_mode": "learning",
            "learning_level": "undergraduate",
            "turns": [
                {"message": "用本科层次讲清楚动态定价和固定定价区别，给一个电商例子。", "required_keywords_any": ["为什么", "怎么做", "示例"]},
                {"message": "继续：给我1个反例和1个思考题。", "required_keywords_any": ["反例", "思考题"]},
            ],
        },
        {
            "name": "ambiguous_to_actionable",
            "role": "ops",
            "action": "optimize",
            "response_mode": "execution",
            "turns": [
                {"message": "帮我把投放效果做上去。", "required_keywords_any": ["假设", "下一步", "优先"]},
                {"message": "补充：平台是抖音，预算每天500，目标ROI>2。请给今天动作。", "required_keywords_any": ["今天", "ROI", "动作"], "min_action_points": 1},
            ],
        },
        {
            "name": "smalltalk_handoff",
            "role": "ops",
            "action": "plan",
            "response_mode": "execution",
            "turns": [
                {"message": "你好"},
                {"message": "我想做增长，但不知道从哪开始。", "required_keywords_any": ["目标", "下一步", "先做"]},
            ],
        },
        {
            "name": "vision_fallback_notice",
            "role": "ops",
            "action": "analysis",
            "response_mode": "execution",
            "turns": [
                {
                    "message": "请基于上传截图给我三条诊断结论。",
                    "attachments": [
                        {
                            "id": "ux-att-1",
                            "filename": "shop-dashboard.png",
                            "file_type": "image/png",
                            "status": "parsed",
                            "summary": "店铺看板截图",
                            "content": "曝光12600 点击132 CTR1.05% 支付转化0.7%",
                            "vision_engine": "ocr-fallback",
                            "vision_is_degraded": True,
                            "vision_fallback_reason": "network_timeout",
                            "vision_warning": "【醒目提示】豆包读图暂时网络故障，已切换为 OCR 兜底，识别质量可能下降。",
                        }
                    ],
                    "required_keywords_any": ["醒目提示", "豆包读图", "OCR", "兜底"],
                }
            ],
        },
    ]


def _write_reports(base_url: str, results: List[ScenarioEval]) -> Dict[str, str]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())

    total = len(results)
    passed = sum(1 for x in results if x.ok)
    failed = total - passed

    turns_total = 0
    avg_quality = 0.0
    avg_goal = 0.0
    avg_value = 0.0

    for scen in results:
        for t in scen.turns:
            turns_total += 1
            avg_quality += t.quality_score
            avg_goal += t.goal_satisfaction
            avg_value += t.value_density

    if turns_total > 0:
        avg_quality = round(avg_quality / turns_total, 3)
        avg_goal = round(avg_goal / turns_total, 3)
        avg_value = round(avg_value / turns_total, 3)

    summary = {
        "base_url": base_url,
        "total": total,
        "passed": passed,
        "failed": failed,
        "turns_total": turns_total,
        "avg_quality_score": avg_quality,
        "avg_goal_satisfaction": avg_goal,
        "avg_value_density": avg_value,
        "results": [
            {
                "name": scen.name,
                "ok": scen.ok,
                "elapsed_ms": scen.elapsed_ms,
                "critical_issues": scen.critical_issues,
                "turns": [
                    {
                        "turn_index": t.turn_index,
                        "ok": t.ok,
                        "status": t.status,
                        "elapsed_ms": t.elapsed_ms,
                        "quality_score": t.quality_score,
                        "goal_satisfaction": t.goal_satisfaction,
                        "value_density": t.value_density,
                        "issues": t.issues,
                        "checks": t.checks,
                        "message": t.message,
                        "reply_preview": t.reply[:1200],
                    }
                    for t in scen.turns
                ],
            }
            for scen in results
        ],
    }

    json_path = REPORT_DIR / f"full_feature_user_experience_probe_{ts}.json"
    md_path = REPORT_DIR / f"full_feature_user_experience_probe_{ts}.md"

    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines: List[str] = []
    lines.append("# Full Feature User Experience Probe")
    lines.append("")
    lines.append(f"- base_url: `{base_url}`")
    lines.append(f"- scenarios: **{total}**  passed: **{passed}**  failed: **{failed}**")
    lines.append(f"- turns_total: **{turns_total}**")
    lines.append(f"- avg_quality_score: **{avg_quality}**")
    lines.append(f"- avg_goal_satisfaction: **{avg_goal}**")
    lines.append(f"- avg_value_density: **{avg_value}**")
    lines.append("")
    lines.append("| scenario | ok | elapsed_ms | failed_turns |")
    lines.append("|---|---:|---:|---:|")
    for scen in results:
        failed_turns = sum(1 for t in scen.turns if not t.ok)
        lines.append(f"| {scen.name} | {'Y' if scen.ok else 'N'} | {scen.elapsed_ms} | {failed_turns} |")

    lines.append("")
    lines.append("## Failures")
    lines.append("")
    for scen in results:
        if scen.ok:
            continue
        lines.append(f"### {scen.name}")
        for issue in scen.critical_issues:
            lines.append(f"- {issue}")
        for t in scen.turns:
            if t.ok:
                continue
            lines.append(f"- Turn {t.turn_index}: q={t.quality_score}, goal={t.goal_satisfaction}, value={t.value_density}")
            lines.append(f"  - message: {t.message}")
            if t.issues:
                lines.append(f"  - issues: {'；'.join(t.issues[:5])}")
            lines.append(f"  - reply_preview: {t.reply[:260].replace(chr(10), ' ')}")
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Run full-feature user experience probe")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--scenarios", default="", help="Comma-separated scenario names")
    args = parser.parse_args()

    base_url = str(args.base_url or DEFAULT_BASE_URL).rstrip("/")
    print(f"[INFO] user_experience_probe_target={base_url}")

    session = requests.Session()
    seed = uuid.uuid4().hex[:10]
    email = f"user-experience-{seed}@example.com"
    password = "Passw0rd!"
    token = _register_or_login(session, base_url, email, password)
    session.headers.update({"Authorization": f"Bearer {token}"})

    check_quality = _load_quality_checker()
    scenarios = _build_scenarios()

    wanted = [x.strip() for x in str(args.scenarios or "").split(",") if x.strip()]
    if wanted:
        scenarios = [s for s in scenarios if str(s.get("name") or "") in wanted]
        if not scenarios:
            print(f"[FAIL] no scenarios matched --scenarios={wanted}")
            return 2

    results: List[ScenarioEval] = []
    for scen in scenarios:
        r = _run_scenario(session, base_url, scen, check_quality)
        results.append(r)
        failed_turns = sum(1 for x in r.turns if not x.ok)
        print(f"[{'PASS' if r.ok else 'FAIL'}] {r.name:<42} failed_turns={failed_turns}/{len(r.turns)} elapsed={r.elapsed_ms}ms")

    report_paths = _write_reports(base_url, results)

    total = len(results)
    passed = sum(1 for x in results if x.ok)
    failed = total - passed
    print(f"\nUser experience JSON report: {report_paths['json']}")
    print(f"User experience markdown report: {report_paths['md']}")
    print(f"Summary: total={total} passed={passed} failed={failed}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
