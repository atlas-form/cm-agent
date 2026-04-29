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
DEFAULT_BASE_URL = os.getenv("MULTITURN_QUALITY_BASE_URL") or os.getenv("SATISFACTION_PROBE_BASE_URL") or "http://127.0.0.1:8100"
REPORT_DIR = ROOT / "tools" / "reports"


@dataclass
class TurnResult:
    turn_index: int
    message: str
    ok: bool
    status: int
    elapsed_ms: int
    quality_score: float
    goal_satisfaction: float
    issues: List[str]
    checks: Dict[str, Any]


@dataclass
class ScenarioResult:
    name: str
    ok: bool
    elapsed_ms: int
    turns: List[TurnResult]
    critical_issues: List[str]


def _register_or_login(session: requests.Session, base_url: str, email: str, password: str) -> str:
    r = session.post(
        f"{base_url}/api/auth/register",
        json={"email": email, "password": password, "name": "MultiTurnQualityProbe"},
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
    *,
    default_turn_timeout_sec: int,
    default_retries: int,
) -> tuple[TurnResult, str, str]:
    role = str(scenario.get("role") or "ops")
    action = str(turn.get("action") or scenario.get("action") or "")
    payload: Dict[str, Any] = {
        "message": str(turn.get("message") or ""),
        "role": role,
        "response_mode": str(turn.get("response_mode") or scenario.get("response_mode") or "execution"),
        "learning_level": str(turn.get("learning_level") or scenario.get("learning_level") or "higher_vocational"),
        "collaboration_mode": str(turn.get("collaboration_mode") or scenario.get("collaboration_mode") or "auto"),
        "hired_roles": list(turn.get("hired_roles") or scenario.get("hired_roles") or []),
    }
    if conversation_id:
        payload["conversation_id"] = conversation_id
    if isinstance(turn.get("attachments"), list):
        payload["attachments"] = turn.get("attachments")
    elif isinstance(scenario.get("attachments"), list):
        payload["attachments"] = scenario.get("attachments")

    timeout_sec = int(
        turn.get(
            "timeout_sec",
            scenario.get("turn_timeout_sec", default_turn_timeout_sec),
        )
    )
    retries = int(turn.get("retries", scenario.get("retries", default_retries)))
    resp = None
    last_exc: Exception | None = None
    t0 = time.time()

    for attempt in range(retries + 1):
        try:
            resp = session.post(f"{base_url}/api/chat", json=payload, timeout=timeout_sec)
            last_exc = None
            if resp is not None and resp.status_code == 200:
                try:
                    _tmp_body = resp.json() if resp.content else {}
                except Exception:
                    _tmp_body = {}
                if not str(_tmp_body.get("reply") or "").strip() and attempt < retries:
                    last_exc = RuntimeError("empty_reply")
                    time.sleep(0.8)
                    continue
            break
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= retries:
                break
            time.sleep(0.8)

    elapsed_ms = int((time.time() - t0) * 1000)
    turn_index = int(turn.get("turn") or 1)

    if last_exc is not None or resp is None:
        return (
            TurnResult(
                turn_index=turn_index,
                message=payload["message"],
                ok=False,
                status=0,
                elapsed_ms=elapsed_ms,
                quality_score=0.0,
                goal_satisfaction=0.0,
                issues=[f"request_error: {last_exc}"],
                checks={"timeout_sec": timeout_sec, "retries": retries},
            ),
            "",
            conversation_id,
        )

    if resp.status_code != 200:
        return (
            TurnResult(
                turn_index=turn_index,
                message=payload["message"],
                ok=False,
                status=resp.status_code,
                elapsed_ms=elapsed_ms,
                quality_score=0.0,
                goal_satisfaction=0.0,
                issues=[f"HTTP {resp.status_code}: {(resp.text or '')[:160]}"],
                checks={"timeout_sec": timeout_sec, "retries": retries},
            ),
            "",
            conversation_id,
        )

    body = resp.json() if resp.content else {}
    reply = str(body.get("reply") or "")
    meta = body.get("metadata") if isinstance(body.get("metadata"), dict) else {}
    conv = str(body.get("conversation_id") or conversation_id or "")
    skills = meta.get("skills_used") if isinstance(meta.get("skills_used"), list) else []

    quality = check_quality(payload["message"], reply, role, action=action, tool_used=bool(skills))
    qscore = float(quality.score)
    goal = float(quality.dimensions.get("goal_satisfaction", 0.0))

    response_mode = str(meta.get("response_mode") or payload.get("response_mode") or "execution")
    action_points = int(meta.get("response_action_count") or 0)
    learning_interaction = bool(meta.get("learning_interaction_enabled"))

    issues: List[str] = []

    required_keywords_any = [str(x).strip() for x in turn.get("required_keywords_any", []) if str(x).strip()]
    if required_keywords_any and not _contains_any(reply, required_keywords_any):
        issues.append("未命中本轮关键内容关键词")

    forbidden_keywords_any = [str(x).strip() for x in turn.get("forbidden_keywords_any", []) if str(x).strip()]
    if forbidden_keywords_any and _contains_any(reply, forbidden_keywords_any):
        issues.append("命中本轮禁用关键词（内容形式不符合预期）")

    context_anchor_any = [str(x).strip() for x in turn.get("context_anchor_any", []) if str(x).strip()]
    if context_anchor_any and not _contains_any(reply, context_anchor_any):
        issues.append("未明显承接上轮上下文约束")

    mode_required_sections = [str(x).strip() for x in turn.get("mode_required_sections", []) if str(x).strip()]
    if mode_required_sections and not _contains_any(reply, mode_required_sections):
        issues.append("模式结构不完整（缺少关键小节）")

    min_action_points = turn.get("min_action_points")
    if response_mode == "execution" and min_action_points is not None and action_points < int(min_action_points):
        issues.append("执行模式动作点不足")

    max_action_points = turn.get("max_action_points")
    if max_action_points is not None and action_points > int(max_action_points):
        issues.append("动作点过多，不符合当前场景的轻量回复期望")

    if response_mode == "learning":
        if not learning_interaction:
            issues.append("教学模式未启用互动引导")

    expected_task_should_clarify = turn.get("expected_task_should_clarify")
    task_should_clarify = bool(meta.get("task_should_clarify"))
    if expected_task_should_clarify is not None and task_should_clarify != bool(expected_task_should_clarify):
        issues.append("task_should_clarify 与场景期望不一致")

    expected_metadata_true = [str(x).strip() for x in turn.get("expected_metadata_true", []) if str(x).strip()]
    for key in expected_metadata_true:
        if not bool(meta.get(key)):
            issues.append(f"metadata.{key} 应为 true")

    expected_metadata_false = [str(x).strip() for x in turn.get("expected_metadata_false", []) if str(x).strip()]
    for key in expected_metadata_false:
        if bool(meta.get(key)):
            issues.append(f"metadata.{key} 应为 false")

    expected_checks_min = turn.get("expected_checks_min") if isinstance(turn.get("expected_checks_min"), dict) else {}
    expected_checks_equals = turn.get("expected_checks_equals") if isinstance(turn.get("expected_checks_equals"), dict) else {}

    quality_floor = float(turn.get("quality_floor", scenario.get("quality_floor", 0.74)))
    goal_floor = float(turn.get("goal_floor", scenario.get("goal_floor", 0.70)))

    checks = {
        "response_mode": response_mode,
        "learning_level": str(meta.get("learning_level") or payload.get("learning_level") or ""),
        "collaboration_mode": str(meta.get("collaboration_mode") or payload.get("collaboration_mode") or ""),
        "response_action_count": action_points,
        "learning_interaction_enabled": learning_interaction,
        "task_should_clarify": task_should_clarify,
        "skills_used_count": len(skills),
        "quality_retry_applied": bool(meta.get("quality_retry_applied")),
        "requires_verified_sources": bool(meta.get("requires_verified_sources")),
        "verified_sources_ready": bool(meta.get("verified_sources_ready")),
        "verified_sources_count": int(meta.get("verified_sources_count") or 0),
        "attachment_degraded_count": int(meta.get("attachment_degraded_count") or 0),
        "attachment_vision_fallback_count": int(meta.get("attachment_vision_fallback_count") or 0),
        "quality_floor": quality_floor,
        "goal_floor": goal_floor,
        "reply_length": len(reply),
    }

    for key, min_val in expected_checks_min.items():
        k = str(key or "").strip()
        if not k:
            continue
        try:
            expected = float(min_val)
            actual = float(checks.get(k) or 0.0)
            if actual < expected:
                issues.append(f"checks.{k} 低于下限 {expected:g}")
        except Exception:
            pass

    for key, expected_val in expected_checks_equals.items():
        k = str(key or "").strip()
        if not k:
            continue
        actual_val = checks.get(k)
        if isinstance(expected_val, bool):
            if bool(actual_val) is not expected_val:
                issues.append(f"checks.{k} 应等于 {str(expected_val).lower()}")
            continue
        if isinstance(expected_val, (int, float)):
            try:
                actual_num = float(actual_val)
                if actual_num != float(expected_val):
                    issues.append(f"checks.{k} 应等于 {expected_val:g}")
            except Exception:
                issues.append(f"checks.{k} 非数值，无法匹配期望 {expected_val:g}")
            continue
        if str(actual_val) != str(expected_val):
            issues.append(f"checks.{k} 应等于 {expected_val}")

    ok = (qscore >= quality_floor) and (goal >= goal_floor) and (len(issues) == 0)
    return (
        TurnResult(
            turn_index=turn_index,
            message=payload["message"],
            ok=ok,
            status=resp.status_code,
            elapsed_ms=elapsed_ms,
            quality_score=round(qscore, 3),
            goal_satisfaction=round(goal, 3),
            issues=issues + list(quality.issues[:4]),
            checks=checks,
        ),
        reply,
        conv,
    )


def _run_scenario(
    session: requests.Session,
    base_url: str,
    check_quality,
    scenario: Dict[str, Any],
    *,
    default_turn_timeout_sec: int,
    default_retries: int,
) -> ScenarioResult:
    t0 = time.time()
    conversation_id = ""
    turn_results: List[TurnResult] = []
    critical_issues: List[str] = []

    for i, turn in enumerate(list(scenario.get("turns") or []), start=1):
        turn = dict(turn)
        turn.setdefault("turn", i)
        result, _, conversation_id = _run_turn(
            session=session,
            base_url=base_url,
            check_quality=check_quality,
            scenario=scenario,
            turn=turn,
            conversation_id=conversation_id,
            default_turn_timeout_sec=default_turn_timeout_sec,
            default_retries=default_retries,
        )
        turn_results.append(result)
        if not result.ok:
            critical_issues.append(f"turn{result.turn_index}: {'；'.join(result.issues[:2])}")

    elapsed_ms = int((time.time() - t0) * 1000)
    ok = len(critical_issues) == 0 and len(turn_results) > 0
    return ScenarioResult(
        name=str(scenario.get("name") or "unnamed"),
        ok=ok,
        elapsed_ms=elapsed_ms,
        turns=turn_results,
        critical_issues=critical_issues,
    )


def _build_scenarios() -> List[Dict[str, Any]]:
    return [
        {
            "name": "execution_multiturn_student_mode",
            "role": "ops",
            "action": "plan",
            "response_mode": "execution",
            "quality_floor": 0.75,
            "goal_floor": 0.72,
            "turns": [
                {
                    "message": "我是中职学生，帮我做抖音小店从0到1的7天计划，先给简版步骤。",
                    "required_keywords_any": ["7天", "步骤", "先做", "下一步"],
                    "mode_required_sections": ["30秒", "下一步", "P1"],
                    "min_action_points": 2,
                },
                {
                    "message": "我的预算只有3000元，产品是云南鲜花饼，请把上面的计划改成更省钱版本。",
                    "context_anchor_any": ["3000", "鲜花饼", "预算"],
                    "required_keywords_any": ["缩减", "优先级", "止损", "回滚"],
                    "min_action_points": 2,
                },
                {
                    "message": "我执行第1天后点击率只有1.1%，请你按上面方案继续追问我关键数据并调整第2天动作。",
                    "context_anchor_any": ["1.1%", "第2天", "追问"],
                    "required_keywords_any": ["追问", "点击率", "动作", "验证"],
                    "min_action_points": 2,
                },
            ],
        },
        {
            "name": "learning_multiturn_undergrad",
            "role": "ops",
            "action": "learning",
            "response_mode": "learning",
            "learning_level": "undergraduate",
            "quality_floor": 0.74,
            "goal_floor": 0.70,
            "turns": [
                {
                    "message": "请用本科层级教我理解ROI和ROAS，先给30秒结论，再展开为什么和怎么做。",
                    "mode_required_sections": ["为什么", "怎么做", "常见误区", "自检"],
                    "required_keywords_any": ["ROI", "ROAS", "区别"],
                },
                {
                    "message": "用我这个场景继续讲：我做的是淘宝零食店，客单价35元，广告预算每天500元。",
                    "context_anchor_any": ["淘宝", "零食", "35", "500"],
                    "required_keywords_any": ["场景", "计算", "假设", "边界"],
                },
                {
                    "message": "请出一道思考题，并告诉我答错时如何纠偏。",
                    "required_keywords_any": ["思考题", "纠偏", "误区"],
                    "mode_required_sections": ["思考题", "误区"],
                },
            ],
        },
        {
            "name": "learning_multiturn_vocational",
            "role": "ops",
            "action": "learning",
            "response_mode": "learning",
            "learning_level": "vocational",
            "quality_floor": 0.73,
            "goal_floor": 0.69,
            "turns": [
                {
                    "message": "请用中职层级讲清楚什么是转化率，先给最简单定义，再给1个电商例子。",
                    "required_keywords_any": ["转化率", "定义", "例子"],
                    "expected_checks_equals": {
                        "response_mode": "learning",
                        "learning_interaction_enabled": True
                    },
                },
                {
                    "message": "我的场景是零食店，今天访客200、下单10，请继续上面内容，用一步一步方式让我算出来。",
                    "required_keywords_any": ["200", "10", "一步", "怎么算"],
                    "context_anchor_any": ["零食店", "200", "10"],
                    "expected_checks_equals": {
                        "response_mode": "learning"
                    },
                },
            ],
        },
        {
            "name": "learning_multiturn_higher_vocational",
            "role": "ops",
            "action": "learning",
            "response_mode": "learning",
            "learning_level": "higher_vocational",
            "quality_floor": 0.74,
            "goal_floor": 0.70,
            "turns": [
                {
                    "message": "请用高职层级教我区分UV价值和转化价值，给一个可执行的店铺诊断框架。",
                    "required_keywords_any": ["UV", "转化", "诊断", "框架"],
                    "expected_checks_equals": {
                        "response_mode": "learning",
                        "learning_interaction_enabled": True
                    },
                },
                {
                    "message": "承接上面框架，给我一个三步排查顺序，适合今天就执行。",
                    "required_keywords_any": ["三步", "排查", "今天"],
                    "context_anchor_any": ["框架", "排查"],
                    "expected_checks_equals": {
                        "response_mode": "learning"
                    },
                },
            ],
        },
        {
            "name": "manual_collab_multiturn",
            "role": "ops",
            "action": "plan",
            "response_mode": "execution",
            "collaboration_mode": "manual",
            "hired_roles": ["data", "service"],
            "quality_floor": 0.74,
            "goal_floor": 0.70,
            "turns": [
                {
                    "message": "按运营+数据+客服协作，给我做618预热的分工方案。",
                    "required_keywords_any": ["分工", "运营", "数据", "客服"],
                    "min_action_points": 2,
                },
                {
                    "message": "现在补充约束：客服只有2人，且每天只能处理80条消息。请改版。",
                    "context_anchor_any": ["2人", "80条"],
                    "required_keywords_any": ["容量", "SLA", "优先级"],
                    "min_action_points": 2,
                },
                {
                    "message": "请把最终方案压缩成学生能执行的今日清单（不超过5条）。",
                    "required_keywords_any": ["今日", "清单", "不超过", "下一步"],
                    "min_action_points": 2,
                },
            ],
        },
        {
            "name": "collaboration_mode_single_differentiation",
            "role": "ops",
            "action": "analysis",
            "response_mode": "execution",
            "collaboration_mode": "single",
            "quality_floor": 0.74,
            "goal_floor": 0.70,
            "turns": [
                {
                    "message": "单岗位模式下，请你独立给出我的淘宝女装店复盘框架，不要分派其他岗位。",
                    "required_keywords_any": ["复盘", "框架", "单岗位", "独立"],
                    "forbidden_keywords_any": ["运营岗", "数据岗", "客服岗", "协同分工"],
                    "expected_checks_equals": {
                        "response_mode": "execution",
                        "collaboration_mode": "single"
                    },
                    "min_action_points": 1,
                },
                {
                    "message": "继续上面，给我今天先做的2件事。",
                    "required_keywords_any": ["今天", "2", "先做", "优先"],
                    "expected_checks_equals": {
                        "collaboration_mode": "single"
                    },
                    "min_action_points": 1,
                },
            ],
        },
        {
            "name": "collaboration_mode_manual_differentiation",
            "role": "ops",
            "action": "plan",
            "response_mode": "execution",
            "collaboration_mode": "manual",
            "hired_roles": ["ops", "data", "service"],
            "quality_floor": 0.74,
            "goal_floor": 0.70,
            "turns": [
                {
                    "message": "手动协作模式下，请按运营/数据/客服三个岗位分工制定双11预热清单。",
                    "required_keywords_any": ["运营", "数据", "客服", "分工"],
                    "expected_checks_equals": {
                        "collaboration_mode": "manual"
                    },
                    "min_action_points": 2,
                },
                {
                    "message": "补充约束：客服只有2人，每天80条会话。继续在手动协作框架里改版。",
                    "required_keywords_any": ["2人", "80条", "SLA", "优先级"],
                    "context_anchor_any": ["手动", "分工", "2人", "80条"],
                    "expected_checks_equals": {
                        "collaboration_mode": "manual"
                    },
                    "min_action_points": 2,
                },
            ],
        },
        {
            "name": "collaboration_mode_auto_differentiation",
            "role": "ops",
            "action": "plan",
            "response_mode": "execution",
            "collaboration_mode": "auto",
            "quality_floor": 0.74,
            "goal_floor": 0.70,
            "turns": [
                {
                    "message": "自动协作模式下，给我一个618预热方案，并说明哪些事项建议自动拉起多岗位配合。",
                    "required_keywords_any": ["自动", "多岗位", "配合", "预热"],
                    "expected_checks_equals": {
                        "collaboration_mode": "auto"
                    },
                    "min_action_points": 2,
                },
                {
                    "message": "继续上面，优先保留自动协作高收益动作，压缩到今天可做的3件事。",
                    "required_keywords_any": ["今天", "3", "先做", "优先"],
                    "expected_checks_equals": {
                        "collaboration_mode": "auto"
                    },
                    "min_action_points": 2,
                },
            ],
        },
        {
            "name": "mode_differentiation_execution",
            "role": "ops",
            "action": "plan",
            "response_mode": "execution",
            "quality_floor": 0.74,
            "goal_floor": 0.70,
            "turns": [
                {
                    "message": "同一个问题：帮我做抖音店铺转化提升方案。请用执行模式，先短后长，今天可执行。",
                    "required_keywords_any": ["30秒", "今日", "下一步", "P1"],
                    "forbidden_keywords_any": ["思考题", "为什么/怎么做"],
                    "expected_checks_equals": {
                        "response_mode": "execution"
                    },
                    "min_action_points": 2,
                },
                {
                    "message": "继续上面执行方案，压缩成今天必须完成的3件事。",
                    "required_keywords_any": ["今天", "3", "先做", "优先"],
                    "expected_checks_equals": {
                        "response_mode": "execution"
                    },
                    "min_action_points": 2,
                },
            ],
        },
        {
            "name": "mode_differentiation_learning",
            "role": "ops",
            "action": "learning",
            "response_mode": "learning",
            "learning_level": "higher_vocational",
            "quality_floor": 0.74,
            "goal_floor": 0.70,
            "turns": [
                {
                    "message": "同一个问题：帮我做抖音店铺转化提升方案。请用教学模式，先讲为什么，再讲怎么做。",
                    "required_keywords_any": ["为什么", "怎么做", "思考题", "误区"],
                    "forbidden_keywords_any": ["今日先做"],
                    "expected_checks_equals": {
                        "response_mode": "learning",
                        "learning_interaction_enabled": True
                    },
                },
                {
                    "message": "继续上面教学方案，请给我一个自检题并说明答错怎么纠偏。",
                    "required_keywords_any": ["自检", "纠偏", "误区"],
                    "expected_checks_equals": {
                        "response_mode": "learning"
                    },
                },
            ],
        },
        {
            "name": "copy_ready_xiaohongshu",
            "role": "creative",
            "action": "create",
            "response_mode": "execution",
            "quality_floor": 0.74,
            "goal_floor": 0.70,
            "turns": [
                {
                    "message": "帮我写一篇小红书图文笔记，主题是春季女装上新，要求可直接复制发出。",
                    "required_keywords_any": ["可直接复制", "标题", "正文", "标签"],
                    "expected_checks_equals": {
                        "response_mode": "execution"
                    },
                    "min_action_points": 1,
                }
            ],
        },
        {
            "name": "copy_ready_customer_email",
            "role": "service",
            "action": "create",
            "response_mode": "execution",
            "quality_floor": 0.74,
            "goal_floor": 0.70,
            "turns": [
                {
                    "message": "帮我写一封客服回复邮件，场景是清关慢，要求可直接复制发送。",
                    "required_keywords_any": ["可直接复制", "主题", "正文", "Dear"],
                    "expected_checks_equals": {
                        "response_mode": "execution"
                    },
                    "min_action_points": 1,
                }
            ],
        },
        {
            "name": "copy_ready_douyin_script",
            "role": "creative",
            "action": "create",
            "response_mode": "execution",
            "quality_floor": 0.74,
            "goal_floor": 0.70,
            "turns": [
                {
                    "message": "帮我写一个抖音短视频口播脚本，主题是女装上新，要求可直接复制。",
                    "required_keywords_any": ["可直接复制", "开场钩子", "口播正文", "结尾CTA"],
                    "expected_checks_equals": {
                        "response_mode": "execution"
                    },
                    "min_action_points": 1,
                }
            ],
        },
        {
            "name": "copy_ready_customer_im",
            "role": "service",
            "action": "create",
            "response_mode": "execution",
            "quality_floor": 0.74,
            "goal_floor": 0.70,
            "turns": [
                {
                    "message": "给我一版客服IM在线回复话术，场景是清关慢，要求可直接复制。",
                    "required_keywords_any": ["可直接复制", "首轮回复", "跟进回复", "升级处理"],
                    "expected_checks_equals": {
                        "response_mode": "execution"
                    },
                    "min_action_points": 1,
                }
            ],
        },
        {
            "name": "smalltalk_handoff_multiturn",
            "role": "ops",
            "action": "plan",
            "response_mode": "execution",
            "quality_floor": 0.75,
            "goal_floor": 0.70,
            "turns": [
                {
                    "message": "你好",
                    "forbidden_keywords_any": ["执行补全（系统保障）", "下一步（24小时内）", "量化KPI"],
                    "required_keywords_any": ["目标", "现状", "30秒", "动作"],
                    "max_action_points": 3,
                    "min_action_points": 1,
                    "quality_floor": 0.75,
                    "goal_floor": 0.60,
                    "expected_task_should_clarify": False,
                },
                {
                    "message": "我预算3000元，帮我做抖音小店7天计划。",
                    "required_keywords_any": ["7天", "预算", "步骤"],
                    "min_action_points": 2,
                },
                {
                    "message": "继续上面方案，今天我先做哪3件事？",
                    "required_keywords_any": ["今天", "先做", "3", "优先级"],
                    "min_action_points": 2,
                },
            ],
        },
        {
            "name": "realdata_evidence_multiturn",
            "role": "data",
            "action": "analysis",
            "response_mode": "execution",
            "collaboration_mode": "single",
            "quality_floor": 0.74,
            "goal_floor": 0.70,
            "turns": [
                {
                    "message": "星巴克咖啡在国内市场份额持续下滑，请用真实市场数据分析并给洞察报告，务必用真实数据说话。",
                    "required_keywords_any": ["证据来源", "时间", "假设", "口径"],
                    "expected_metadata_true": ["requires_verified_sources"],
                    "min_action_points": 1,
                    "retries": 2,
                },
                {
                    "message": "请继续：把你刚才每条关键结论对应的数据来源和日期列出来。",
                    "required_keywords_any": ["来源", "日期", "结论", "对应"],
                    "context_anchor_any": ["结论", "来源"],
                    "min_action_points": 1,
                },
            ],
        },
        {
            "name": "attachment_vision_fallback_notice",
            "role": "ops",
            "action": "analysis",
            "response_mode": "execution",
            "quality_floor": 0.74,
            "goal_floor": 0.70,
            "turns": [
                {
                    "message": "请基于我上传的店铺截图，先给我3条关键诊断结论。",
                    "attachments": [
                        {
                            "id": "att-vision-1",
                            "filename": "shop-screen.png",
                            "file_type": "image/png",
                            "status": "parsed",
                            "summary": "店铺看板截图",
                            "content": "曝光 12600，点击 132，点击率 1.05%，支付转化 0.7%",
                            "vision_engine": "ocr-fallback",
                            "vision_is_degraded": True,
                            "vision_fallback_reason": "network_timeout",
                            "vision_warning": "【醒目提示】豆包读图暂时网络故障，已切换为 OCR 兜底，识别质量可能下降。"
                        }
                    ],
                    "required_keywords_any": ["醒目提示", "豆包读图", "OCR", "兜底"],
                    "expected_checks_min": {
                        "attachment_vision_fallback_count": 1,
                        "attachment_degraded_count": 1
                    },
                    "min_action_points": 1
                }
            ]
        },
    ]


def _write_reports(base_url: str, results: List[ScenarioResult]) -> Dict[str, str]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())

    total = len(results)
    passed = sum(1 for x in results if x.ok)
    failed = total - passed

    avg_quality = 0.0
    avg_goal = 0.0
    turns_total = 0
    for scen in results:
        for t in scen.turns:
            turns_total += 1
            avg_quality += float(t.quality_score)
            avg_goal += float(t.goal_satisfaction)
    if turns_total > 0:
        avg_quality = round(avg_quality / turns_total, 3)
        avg_goal = round(avg_goal / turns_total, 3)

    summary = {
        "base_url": base_url,
        "total": total,
        "passed": passed,
        "failed": failed,
        "turns_total": turns_total,
        "avg_quality_score": avg_quality,
        "avg_goal_satisfaction": avg_goal,
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
                        "issues": t.issues,
                        "checks": t.checks,
                        "message": t.message,
                    }
                    for t in scen.turns
                ],
            }
            for scen in results
        ],
    }

    json_path = REPORT_DIR / f"multi_turn_answer_quality_probe_{ts}.json"
    md_path = REPORT_DIR / f"multi_turn_answer_quality_probe_{ts}.md"

    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines: List[str] = []
    lines.append("# Multi-turn Answer Quality Probe")
    lines.append("")
    lines.append(f"- base_url: `{base_url}`")
    lines.append(f"- scenarios: **{total}**  passed: **{passed}**  failed: **{failed}**")
    lines.append(f"- turns_total: **{turns_total}**")
    lines.append(f"- avg_quality_score: **{avg_quality}**")
    lines.append(f"- avg_goal_satisfaction: **{avg_goal}**")
    lines.append("")
    lines.append("| scenario | ok | elapsed_ms | failed_turns |")
    lines.append("|---|---:|---:|---:|")
    for scen in results:
        failed_turns = sum(1 for t in scen.turns if not t.ok)
        lines.append(f"| {scen.name} | {'Y' if scen.ok else 'N'} | {scen.elapsed_ms} | {failed_turns} |")
    lines.append("")

    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run multi-turn answer quality probe")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API base url, e.g. http://127.0.0.1:8233")
    parser.add_argument("--scenarios", default="", help="Comma-separated scenario names to run (default: all)")
    parser.add_argument("--default-turn-timeout", type=int, default=180, help="Default per-turn request timeout seconds")
    parser.add_argument("--default-retries", type=int, default=1, help="Default retries per turn when not specified")
    args = parser.parse_args(argv)

    base_url = str(args.base_url or DEFAULT_BASE_URL).rstrip("/")
    print(f"[INFO] multiturn_quality_probe_target={base_url}")

    session = requests.Session()
    suffix = uuid.uuid4().hex[:8]
    email = f"multiturn_probe_{suffix}@example.com"
    password = "MultiTurnProbe123!"

    token = _register_or_login(session, base_url, email, password)
    if not token:
        print("[FAIL] failed to acquire auth token")
        return 2
    session.headers.update({"Authorization": f"Bearer {token}"})

    check_quality = _load_quality_checker()
    scenarios = _build_scenarios()
    wanted = [x.strip() for x in str(args.scenarios or "").split(",") if x.strip()]
    if wanted:
        scenarios = [s for s in scenarios if str(s.get("name") or "") in wanted]
        if not scenarios:
            print(f"[FAIL] no scenarios matched --scenarios={wanted}")
            return 2
    results: List[ScenarioResult] = []

    for scen in scenarios:
        scen_result = _run_scenario(
            session,
            base_url,
            check_quality,
            scen,
            default_turn_timeout_sec=max(30, int(args.default_turn_timeout or 180)),
            default_retries=max(0, int(args.default_retries or 1)),
        )
        results.append(scen_result)
        failed_turns = sum(1 for t in scen_result.turns if not t.ok)
        print(
            f"[{'PASS' if scen_result.ok else 'FAIL'}] {scen_result.name:<32} "
            f"failed_turns={failed_turns}/{len(scen_result.turns)} elapsed={scen_result.elapsed_ms}ms"
        )

    outputs = _write_reports(base_url, results)
    passed = sum(1 for s in results if s.ok)
    print("")
    print(f"Multi-turn quality JSON report: {outputs['json']}")
    print(f"Multi-turn quality markdown report: {outputs['md']}")
    print(f"Summary: total={len(results)} passed={passed} failed={len(results)-passed}")

    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
