from __future__ import annotations

import argparse
import difflib
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence

import requests

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "tools" / "reports"
DEFAULT_BASE_URL = os.getenv("STRATEGY_COMBO_BASE_URL") or os.getenv("SATISFACTION_PROBE_BASE_URL") or "http://127.0.0.1:8100"
DEFAULT_MESSAGE = "近7天转化下滑，请同时给可执行诊断和教学讲解，输出口径定义、诊断假设、验证方案、优先级与24小时下一步。"

RESPONSE_MODES: Sequence[str] = ("execution", "learning")
COLLAB_MODES: Sequence[str] = ("auto", "single", "manual")
LEARNING_LEVELS: Sequence[str] = ("vocational", "higher_vocational", "undergraduate")
MANUAL_ROLE_SETS: Sequence[Sequence[str]] = (
    ("data",),
    ("ops", "data"),
    ("ops", "data", "service"),
)


@dataclass
class ComboResult:
    combo_id: str
    response_mode: str
    collaboration_mode: str
    learning_level: str
    hired_roles: List[str]
    status: int
    elapsed_ms: int
    quality_score: float
    goal_satisfaction: float
    reply_length: int
    first_line: str
    has_collab_signature: bool
    has_learning_scaffold: bool
    has_execution_markers: bool
    has_manual_role_lens: bool
    signature_label: str
    quality_issues: List[str]
    reply: str


def _register_or_login(session: requests.Session, base_url: str, email: str, password: str) -> str:
    register = session.post(
        f"{base_url}/api/auth/register",
        json={"email": email, "password": password, "name": "StrategyComboProbe"},
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


def _load_quality_checker():
    import sys

    server_src = ROOT / "server"
    if str(server_src) not in sys.path:
        sys.path.insert(0, str(server_src))

    from src.core.quality_checker import check_quality

    return check_quality


def _normalize_text(text: str) -> str:
    return " ".join(str(text or "").replace("\r", "").split())


def _first_line(text: str) -> str:
    for line in str(text or "").splitlines():
        line = line.strip()
        if line:
            return line[:120]
    return ""


def _signature_label(reply: str) -> str:
    first = _first_line(reply)
    if first.startswith("回答策略："):
        return first
    if "回答策略：" in reply:
        idx = reply.index("回答策略：")
        tail = reply[idx:].splitlines()[0].strip()
        return tail[:120]
    return ""


def _has_learning_scaffold(reply: str) -> bool:
    body = str(reply or "")
    return ("为什么" in body and "怎么做" in body) or ("补充学习路径" in body)


def _has_execution_markers(reply: str) -> bool:
    body = str(reply or "")
    markers = ("30秒结论", "P1", "P2", "P3", "下一步", "今日先做", "行动项")
    return any(m in body for m in markers)


def _build_combos() -> List[Dict[str, Any]]:
    combos: List[Dict[str, Any]] = []
    for response_mode in RESPONSE_MODES:
        level_pool = LEARNING_LEVELS if response_mode == "learning" else ("higher_vocational",)
        for learning_level in level_pool:
            for collaboration_mode in COLLAB_MODES:
                if collaboration_mode == "manual":
                    for hired_roles in MANUAL_ROLE_SETS:
                        combos.append(
                            {
                                "response_mode": response_mode,
                                "collaboration_mode": collaboration_mode,
                                "learning_level": learning_level,
                                "hired_roles": list(hired_roles),
                            }
                        )
                else:
                    combos.append(
                        {
                            "response_mode": response_mode,
                            "collaboration_mode": collaboration_mode,
                            "learning_level": learning_level,
                            "hired_roles": [],
                        }
                    )

    for idx, combo in enumerate(combos, start=1):
        roles_token = "+".join(combo["hired_roles"]) if combo["hired_roles"] else "none"
        combo["combo_id"] = (
            f"C{idx:02d}"
            f"_{combo['response_mode']}"
            f"_{combo['collaboration_mode']}"
            f"_{combo['learning_level']}"
            f"_{roles_token}"
        )
    return combos


def _run_combo(
    session: requests.Session,
    base_url: str,
    combo: Dict[str, Any],
    *,
    message: str,
    role: str,
    action: str,
    check_quality,
    timeout_sec: int,
) -> ComboResult:
    payload = {
        "message": message,
        "role": role,
        "response_mode": combo["response_mode"],
        "learning_level": combo["learning_level"],
        "collaboration_mode": combo["collaboration_mode"],
        "hired_roles": combo["hired_roles"],
    }

    t0 = time.time()
    try:
        resp = session.post(f"{base_url}/api/chat", json=payload, timeout=timeout_sec)
    except Exception as exc:
        elapsed_ms = int((time.time() - t0) * 1000)
        return ComboResult(
            combo_id=combo["combo_id"],
            response_mode=combo["response_mode"],
            collaboration_mode=combo["collaboration_mode"],
            learning_level=combo["learning_level"],
            hired_roles=list(combo["hired_roles"]),
            status=0,
            elapsed_ms=elapsed_ms,
            quality_score=0.0,
            goal_satisfaction=0.0,
            reply_length=0,
            first_line="",
            has_collab_signature=False,
            has_learning_scaffold=False,
            has_execution_markers=False,
            has_manual_role_lens=False,
            signature_label="",
            quality_issues=[f"REQUEST_ERROR: {str(exc)[:260]}"],
            reply="",
        )

    elapsed_ms = int((time.time() - t0) * 1000)

    if resp.status_code != 200:
        text = (resp.text or "")[:300]
        return ComboResult(
            combo_id=combo["combo_id"],
            response_mode=combo["response_mode"],
            collaboration_mode=combo["collaboration_mode"],
            learning_level=combo["learning_level"],
            hired_roles=list(combo["hired_roles"]),
            status=resp.status_code,
            elapsed_ms=elapsed_ms,
            quality_score=0.0,
            goal_satisfaction=0.0,
            reply_length=0,
            first_line="",
            has_collab_signature=False,
            has_learning_scaffold=False,
            has_execution_markers=False,
            has_manual_role_lens=False,
            signature_label="",
            quality_issues=[f"HTTP {resp.status_code}: {text}"],
            reply="",
        )

    body = resp.json() if resp.content else {}
    reply = str(body.get("reply") or "")
    quality = check_quality(message, reply, role, action=action, tool_used=False)

    signature = _signature_label(reply)

    return ComboResult(
        combo_id=combo["combo_id"],
        response_mode=combo["response_mode"],
        collaboration_mode=combo["collaboration_mode"],
        learning_level=combo["learning_level"],
        hired_roles=list(combo["hired_roles"]),
        status=resp.status_code,
        elapsed_ms=elapsed_ms,
        quality_score=round(float(quality.score), 3),
        goal_satisfaction=round(float(quality.dimensions.get("goal_satisfaction", 0.0)), 3),
        reply_length=len(reply),
        first_line=_first_line(reply),
        has_collab_signature=bool(signature),
        has_learning_scaffold=_has_learning_scaffold(reply),
        has_execution_markers=_has_execution_markers(reply),
        has_manual_role_lens=("角色补位讲解" in reply),
        signature_label=signature,
        quality_issues=list(quality.issues[:5]),
        reply=reply,
    )


def _pairwise_similarity(results: Sequence[ComboResult]) -> Dict[str, Any]:
    matrix: Dict[str, Dict[str, float]] = {}
    near_duplicate_pairs: List[Dict[str, Any]] = []

    normalized = {r.combo_id: _normalize_text(r.reply) for r in results}

    for i, left in enumerate(results):
        lid = left.combo_id
        matrix[lid] = {}
        for j, right in enumerate(results):
            rid = right.combo_id
            if i == j:
                matrix[lid][rid] = 1.0
                continue
            ratio = difflib.SequenceMatcher(None, normalized[lid], normalized[rid]).ratio()
            ratio = round(float(ratio), 4)
            matrix[lid][rid] = ratio
            if i < j and ratio >= 0.92:
                near_duplicate_pairs.append(
                    {
                        "left": lid,
                        "right": rid,
                        "similarity": ratio,
                    }
                )

    max_similarity_per_combo: Dict[str, float] = {}
    for r in results:
        sid = r.combo_id
        peer_scores = [score for tid, score in matrix[sid].items() if tid != sid]
        max_similarity_per_combo[sid] = round(max(peer_scores) if peer_scores else 0.0, 4)

    avg_max_similarity = round(
        sum(max_similarity_per_combo.values()) / max(len(max_similarity_per_combo), 1),
        4,
    )
    distinguishability = round(max(0.0, 1.0 - avg_max_similarity), 4)

    return {
        "max_similarity_per_combo": max_similarity_per_combo,
        "avg_max_similarity": avg_max_similarity,
        "distinguishability_score": distinguishability,
        "near_duplicate_pairs": near_duplicate_pairs,
    }


def _write_reports(base_url: str, message: str, role: str, action: str, results: Sequence[ComboResult], similarity: Dict[str, Any]) -> Dict[str, str]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())

    passed = sum(1 for r in results if r.status == 200)
    total = len(results)

    summary = {
        "base_url": base_url,
        "message": message,
        "role": role,
        "action": action,
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "avg_quality_score": round(sum(r.quality_score for r in results) / max(total, 1), 3),
        "avg_goal_satisfaction": round(sum(r.goal_satisfaction for r in results) / max(total, 1), 3),
        "avg_reply_length": int(sum(r.reply_length for r in results) / max(total, 1)),
        "signature_coverage": round(sum(1 for r in results if r.has_collab_signature) / max(total, 1), 3),
        "learning_scaffold_coverage": round(sum(1 for r in results if r.has_learning_scaffold) / max(total, 1), 3),
        "execution_marker_coverage": round(sum(1 for r in results if r.has_execution_markers) / max(total, 1), 3),
        "manual_role_lens_coverage": round(sum(1 for r in results if r.has_manual_role_lens) / max(total, 1), 3),
        "similarity": similarity,
        "results": [
            {
                "combo_id": r.combo_id,
                "response_mode": r.response_mode,
                "collaboration_mode": r.collaboration_mode,
                "learning_level": r.learning_level,
                "hired_roles": r.hired_roles,
                "status": r.status,
                "elapsed_ms": r.elapsed_ms,
                "quality_score": r.quality_score,
                "goal_satisfaction": r.goal_satisfaction,
                "reply_length": r.reply_length,
                "first_line": r.first_line,
                "has_collab_signature": r.has_collab_signature,
                "has_learning_scaffold": r.has_learning_scaffold,
                "has_execution_markers": r.has_execution_markers,
                "has_manual_role_lens": r.has_manual_role_lens,
                "signature_label": r.signature_label,
                "quality_issues": r.quality_issues,
            }
            for r in results
        ],
    }

    json_path = REPORT_DIR / f"strategy_combo_probe_{ts}.json"
    md_path = REPORT_DIR / f"strategy_combo_probe_{ts}.md"

    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines: List[str] = []
    md_lines.append("# Strategy Combo Probe")
    md_lines.append("")
    md_lines.append(f"- base_url: `{base_url}`")
    md_lines.append(f"- total: **{total}**  passed: **{passed}**  failed: **{total - passed}**")
    md_lines.append(f"- avg_quality_score: **{summary['avg_quality_score']}**")
    md_lines.append(f"- avg_goal_satisfaction: **{summary['avg_goal_satisfaction']}**")
    md_lines.append(f"- avg_reply_length: **{summary['avg_reply_length']}**")
    md_lines.append(f"- distinguishability_score: **{similarity.get('distinguishability_score', 0.0)}**")
    md_lines.append(f"- avg_max_similarity: **{similarity.get('avg_max_similarity', 0.0)}**")
    md_lines.append("")
    md_lines.append("| combo | mode | collab | level | roles | quality | goal | len | max_sim | signature | learning | exec |")
    md_lines.append("|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|")

    max_sim = similarity.get("max_similarity_per_combo") if isinstance(similarity.get("max_similarity_per_combo"), dict) else {}
    for r in results:
        roles = "+".join(r.hired_roles) if r.hired_roles else "-"
        md_lines.append(
            "| "
            + " | ".join(
                [
                    r.combo_id,
                    r.response_mode,
                    r.collaboration_mode,
                    r.learning_level,
                    roles,
                    f"{r.quality_score:.3f}",
                    f"{r.goal_satisfaction:.3f}",
                    str(r.reply_length),
                    f"{float(max_sim.get(r.combo_id, 0.0)):.4f}",
                    "Y" if r.has_collab_signature else "N",
                    "Y" if r.has_learning_scaffold else "N",
                    "Y" if r.has_execution_markers else "N",
                ]
            )
            + " |"
        )

    near_dupes = similarity.get("near_duplicate_pairs") if isinstance(similarity.get("near_duplicate_pairs"), list) else []
    md_lines.append("")
    md_lines.append("## Near Duplicates (similarity >= 0.92)")
    if near_dupes:
        for item in near_dupes[:20]:
            md_lines.append(f"- {item.get('left')} vs {item.get('right')}: {item.get('similarity')}")
    else:
        md_lines.append("- none")

    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    return {
        "json": str(json_path),
        "md": str(md_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run all strategy combinations against one prompt and evaluate distinguishability")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API base url")
    parser.add_argument("--message", default=DEFAULT_MESSAGE, help="Same prompt used for all combinations")
    parser.add_argument("--role", default="ops", help="chat role")
    parser.add_argument("--action", default="analysis", help="quality action")
    parser.add_argument("--timeout-sec", type=int, default=240, help="request timeout seconds")
    args = parser.parse_args()

    print(f"[INFO] strategy_combo_probe_target={args.base_url}")
    print(f"[INFO] prompt={args.message}")

    check_quality = _load_quality_checker()

    session = requests.Session()
    email = f"strategy_combo_probe_{uuid.uuid4().hex[:10]}@example.com"
    password = "Passw0rd!"
    token = _register_or_login(session, args.base_url, email, password)
    session.headers.update({"Authorization": f"Bearer {token}"})

    combos = _build_combos()
    results: List[ComboResult] = []

    for combo in combos:
        roles = "+".join(combo["hired_roles"]) if combo["hired_roles"] else "-"
        print(
            f"[RUN] {combo['combo_id']} mode={combo['response_mode']} collab={combo['collaboration_mode']} "
            f"level={combo['learning_level']} roles={roles}"
        )
        result = _run_combo(
            session,
            args.base_url,
            combo,
            message=args.message,
            role=args.role,
            action=args.action,
            check_quality=check_quality,
            timeout_sec=max(10, int(args.timeout_sec or 240)),
        )
        results.append(result)

        print(
            f"[{'PASS' if result.status == 200 else 'FAIL'}] {result.combo_id} "
            f"quality={result.quality_score:.3f} goal={result.goal_satisfaction:.3f} "
            f"len={result.reply_length} status={result.status} elapsed={result.elapsed_ms}ms"
        )

    similarity = _pairwise_similarity(results)
    report = _write_reports(args.base_url, args.message, args.role, args.action, results, similarity)

    print(f"\nStrategy combo JSON report: {report['json']}")
    print(f"Strategy combo markdown report: {report['md']}")
    print(
        "Summary: "
        f"total={len(results)} passed={sum(1 for r in results if r.status == 200)} "
        f"distinguishability={similarity.get('distinguishability_score', 0.0)} "
        f"near_duplicates={len(similarity.get('near_duplicate_pairs', []))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
