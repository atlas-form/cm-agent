from __future__ import annotations

import argparse
import json
import os
import statistics
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Sequence

import requests

import strategy_combo_probe as probe

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "tools" / "reports"
DEFAULT_BASE_URL = (
    os.getenv("STRATEGY_COMBO_BASE_URL")
    or os.getenv("SATISFACTION_PROBE_BASE_URL")
    or "http://127.0.0.1:8100"
)


def _safe_mean(values: Sequence[float]) -> float:
    nums = [float(v) for v in values]
    if not nums:
        return 0.0
    return float(sum(nums) / len(nums))


def _safe_pstdev(values: Sequence[float]) -> float:
    nums = [float(v) for v in values]
    if len(nums) <= 1:
        return 0.0
    return float(statistics.pstdev(nums))


def _preview(text: str, limit: int = 56) -> str:
    normalized = " ".join(str(text or "").replace("\r", "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1] + "…"


def _extract_messages_from_file(path: Path) -> List[str]:
    raw = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()

    if suffix == ".json":
        payload = json.loads(raw)
        if isinstance(payload, list):
            return [str(item).strip() for item in payload if str(item).strip()]
        if isinstance(payload, dict):
            values = payload.get("messages")
            if isinstance(values, list):
                return [str(item).strip() for item in values if str(item).strip()]
            single = payload.get("message")
            if single is not None and str(single).strip():
                return [str(single).strip()]
        raise ValueError("JSON messages file should be a list or contain 'messages' field")

    if suffix == ".jsonl":
        messages: List[str] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if isinstance(obj, str):
                value = obj.strip()
            elif isinstance(obj, dict):
                value = str(obj.get("message") or "").strip()
            else:
                value = ""
            if value:
                messages.append(value)
        return messages

    messages = []
    for line in raw.splitlines():
        value = line.strip()
        if not value:
            continue
        if value.startswith("#"):
            continue
        messages.append(value)
    return messages


def _load_messages(cli_messages: Sequence[str], messages_file: str | None) -> List[str]:
    items: List[str] = []

    for msg in cli_messages:
        value = str(msg or "").strip()
        if value:
            items.append(value)

    if messages_file:
        file_path = Path(messages_file).expanduser().resolve()
        if not file_path.exists():
            raise FileNotFoundError(f"messages file not found: {file_path}")
        items.extend(_extract_messages_from_file(file_path))

    if not items:
        items = [probe.DEFAULT_MESSAGE]

    # De-duplicate while preserving order.
    deduped: List[str] = []
    seen = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _result_to_dict(result: probe.ComboResult) -> Dict[str, Any]:
    return {
        "combo_id": result.combo_id,
        "response_mode": result.response_mode,
        "collaboration_mode": result.collaboration_mode,
        "learning_level": result.learning_level,
        "hired_roles": list(result.hired_roles),
        "status": int(result.status),
        "elapsed_ms": int(result.elapsed_ms),
        "quality_score": float(result.quality_score),
        "goal_satisfaction": float(result.goal_satisfaction),
        "reply_length": int(result.reply_length),
        "first_line": str(result.first_line or ""),
        "has_collab_signature": bool(result.has_collab_signature),
        "has_learning_scaffold": bool(result.has_learning_scaffold),
        "has_execution_markers": bool(result.has_execution_markers),
        "has_manual_role_lens": bool(result.has_manual_role_lens),
        "signature_label": str(result.signature_label or ""),
        "quality_issues": list(result.quality_issues or []),
    }


def _summarize_prompt(prompt_id: str, message: str, results: Sequence[probe.ComboResult]) -> Dict[str, Any]:
    total = len(results)
    passed = sum(1 for item in results if item.status == 200)
    similarity = probe._pairwise_similarity(results)

    return {
        "prompt_id": prompt_id,
        "message": message,
        "message_preview": _preview(message, 80),
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "avg_quality_score": round(_safe_mean([r.quality_score for r in results]), 4),
        "avg_goal_satisfaction": round(_safe_mean([r.goal_satisfaction for r in results]), 4),
        "avg_reply_length": int(round(_safe_mean([float(r.reply_length) for r in results]))),
        "similarity": similarity,
        "results": [_result_to_dict(r) for r in results],
    }


def _build_combo_rollups(prompt_summaries: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rollups: Dict[str, Dict[str, Any]] = {}

    for prompt_summary in prompt_summaries:
        similarity = prompt_summary.get("similarity") if isinstance(prompt_summary.get("similarity"), dict) else {}
        max_sim_map = similarity.get("max_similarity_per_combo") if isinstance(similarity.get("max_similarity_per_combo"), dict) else {}

        for item in prompt_summary.get("results") or []:
            combo_id = str(item.get("combo_id") or "")
            if not combo_id:
                continue

            entry = rollups.setdefault(
                combo_id,
                {
                    "combo_id": combo_id,
                    "response_mode": str(item.get("response_mode") or ""),
                    "collaboration_mode": str(item.get("collaboration_mode") or ""),
                    "learning_level": str(item.get("learning_level") or ""),
                    "hired_roles": list(item.get("hired_roles") or []),
                    "run_total": 0,
                    "pass_total": 0,
                    "quality_scores": [],
                    "goal_scores": [],
                    "reply_lengths": [],
                    "elapsed_ms": [],
                    "max_similarity_scores": [],
                    "signature_hits": 0,
                    "learning_hits": 0,
                    "execution_hits": 0,
                    "manual_role_lens_hits": 0,
                    "first_lines": [],
                    "signature_labels": [],
                },
            )

            status = int(item.get("status") or 0)
            entry["run_total"] += 1
            if status == 200:
                entry["pass_total"] += 1

            quality = float(item.get("quality_score") or 0.0)
            goal = float(item.get("goal_satisfaction") or 0.0)
            reply_len = float(item.get("reply_length") or 0.0)
            elapsed = float(item.get("elapsed_ms") or 0.0)
            max_similarity = float(max_sim_map.get(combo_id, 0.0))

            entry["quality_scores"].append(quality)
            entry["goal_scores"].append(goal)
            entry["reply_lengths"].append(reply_len)
            entry["elapsed_ms"].append(elapsed)
            entry["max_similarity_scores"].append(max_similarity)

            if bool(item.get("has_collab_signature")):
                entry["signature_hits"] += 1
            if bool(item.get("has_learning_scaffold")):
                entry["learning_hits"] += 1
            if bool(item.get("has_execution_markers")):
                entry["execution_hits"] += 1
            if bool(item.get("has_manual_role_lens")):
                entry["manual_role_lens_hits"] += 1

            first_line = str(item.get("first_line") or "").strip()
            if first_line and first_line not in entry["first_lines"] and len(entry["first_lines"]) < 4:
                entry["first_lines"].append(first_line)

            signature_label = str(item.get("signature_label") or "").strip()
            if signature_label and signature_label not in entry["signature_labels"] and len(entry["signature_labels"]) < 4:
                entry["signature_labels"].append(signature_label)

    rows: List[Dict[str, Any]] = []
    for combo_id in sorted(rollups.keys()):
        entry = rollups[combo_id]
        run_total = max(1, int(entry["run_total"]))
        pass_total = int(entry["pass_total"])

        row = {
            "combo_id": combo_id,
            "response_mode": entry["response_mode"],
            "collaboration_mode": entry["collaboration_mode"],
            "learning_level": entry["learning_level"],
            "hired_roles": entry["hired_roles"],
            "run_total": run_total,
            "pass_total": pass_total,
            "pass_rate": round(pass_total / run_total, 4),
            "avg_quality_score": round(_safe_mean(entry["quality_scores"]), 4),
            "quality_stddev": round(_safe_pstdev(entry["quality_scores"]), 4),
            "avg_goal_satisfaction": round(_safe_mean(entry["goal_scores"]), 4),
            "goal_stddev": round(_safe_pstdev(entry["goal_scores"]), 4),
            "avg_reply_length": int(round(_safe_mean(entry["reply_lengths"]))),
            "avg_elapsed_ms": int(round(_safe_mean(entry["elapsed_ms"]))),
            "avg_max_similarity": round(_safe_mean(entry["max_similarity_scores"]), 4),
            "signature_coverage": round(entry["signature_hits"] / run_total, 4),
            "learning_coverage": round(entry["learning_hits"] / run_total, 4),
            "execution_coverage": round(entry["execution_hits"] / run_total, 4),
            "manual_role_lens_coverage": round(entry["manual_role_lens_hits"] / run_total, 4),
            "first_line_samples": entry["first_lines"],
            "signature_samples": entry["signature_labels"],
        }
        rows.append(row)

    return rows


def _aggregate_summary(
    prompt_summaries: Sequence[Dict[str, Any]],
    combo_rollups: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    prompt_count = len(prompt_summaries)
    combo_count = len(combo_rollups)
    total_runs = sum(int(item.get("total") or 0) for item in prompt_summaries)
    total_pass = sum(int(item.get("passed") or 0) for item in prompt_summaries)

    dist_scores = [
        float((item.get("similarity") or {}).get("distinguishability_score") or 0.0)
        for item in prompt_summaries
    ]
    avg_max_similarity = [
        float((item.get("similarity") or {}).get("avg_max_similarity") or 0.0)
        for item in prompt_summaries
    ]

    overall_quality = [float(item.get("avg_quality_score") or 0.0) for item in prompt_summaries]
    overall_goal = [float(item.get("avg_goal_satisfaction") or 0.0) for item in prompt_summaries]

    return {
        "prompt_count": prompt_count,
        "combo_count": combo_count,
        "total_runs": total_runs,
        "total_pass": total_pass,
        "total_fail": max(0, total_runs - total_pass),
        "pass_rate": round((total_pass / total_runs) if total_runs else 0.0, 4),
        "avg_prompt_quality": round(_safe_mean(overall_quality), 4),
        "avg_prompt_goal_satisfaction": round(_safe_mean(overall_goal), 4),
        "avg_prompt_distinguishability": round(_safe_mean(dist_scores), 4),
        "min_prompt_distinguishability": round(min(dist_scores) if dist_scores else 0.0, 4),
        "max_prompt_distinguishability": round(max(dist_scores) if dist_scores else 0.0, 4),
        "avg_prompt_max_similarity": round(_safe_mean(avg_max_similarity), 4),
        "least_distinguishable_prompt": min(
            (
                {
                    "prompt_id": item.get("prompt_id"),
                    "message_preview": item.get("message_preview"),
                    "distinguishability_score": float((item.get("similarity") or {}).get("distinguishability_score") or 0.0),
                }
                for item in prompt_summaries
            ),
            key=lambda x: x["distinguishability_score"],
            default=None,
        ),
        "most_similar_combo": max(
            (
                {
                    "combo_id": item.get("combo_id"),
                    "avg_max_similarity": float(item.get("avg_max_similarity") or 0.0),
                }
                for item in combo_rollups
            ),
            key=lambda x: x["avg_max_similarity"],
            default=None,
        ),
    }


def _collect_recurring_near_duplicates(prompt_summaries: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counter: Dict[str, Dict[str, Any]] = {}

    for prompt_summary in prompt_summaries:
        prompt_id = str(prompt_summary.get("prompt_id") or "")
        similarity = prompt_summary.get("similarity") if isinstance(prompt_summary.get("similarity"), dict) else {}
        pairs = similarity.get("near_duplicate_pairs") if isinstance(similarity.get("near_duplicate_pairs"), list) else []

        for pair in pairs:
            left = str(pair.get("left") or "").strip()
            right = str(pair.get("right") or "").strip()
            if not left or not right:
                continue

            ordered = sorted((left, right))
            key = " <-> ".join(ordered)
            sim_value = float(pair.get("similarity") or 0.0)

            bucket = counter.setdefault(
                key,
                {
                    "left": ordered[0],
                    "right": ordered[1],
                    "occurrences": 0,
                    "max_similarity": 0.0,
                    "prompt_ids": [],
                },
            )
            bucket["occurrences"] += 1
            bucket["max_similarity"] = max(float(bucket["max_similarity"]), sim_value)
            if prompt_id and prompt_id not in bucket["prompt_ids"]:
                bucket["prompt_ids"].append(prompt_id)

    rows = list(counter.values())
    rows.sort(key=lambda x: (-int(x["occurrences"]), -float(x["max_similarity"]), str(x["left"])))
    for item in rows:
        item["max_similarity"] = round(float(item["max_similarity"]), 4)
    return rows


def _write_reports(report: Dict[str, Any]) -> Dict[str, str]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    json_path = REPORT_DIR / f"strategy_combo_multisim_{ts}.json"
    md_path = REPORT_DIR / f"strategy_combo_multisim_{ts}.md"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    prompt_summaries = report.get("prompt_summaries") if isinstance(report.get("prompt_summaries"), list) else []
    combo_rollups = report.get("combo_rollups") if isinstance(report.get("combo_rollups"), list) else []
    recurring_dupes = report.get("recurring_near_duplicates") if isinstance(report.get("recurring_near_duplicates"), list) else []
    aggregate = report.get("aggregate") if isinstance(report.get("aggregate"), dict) else {}

    md_lines: List[str] = []
    md_lines.append("# Strategy Combo Multi-Sim")
    md_lines.append("")
    md_lines.append(f"- base_url: `{report.get('base_url')}`")
    md_lines.append(f"- role/action: `{report.get('role')}` / `{report.get('action')}`")
    md_lines.append(f"- prompts: **{aggregate.get('prompt_count', 0)}**  combos: **{aggregate.get('combo_count', 0)}**")
    md_lines.append(f"- total runs: **{aggregate.get('total_runs', 0)}**  pass: **{aggregate.get('total_pass', 0)}**  fail: **{aggregate.get('total_fail', 0)}**")
    md_lines.append(f"- avg_prompt_quality: **{aggregate.get('avg_prompt_quality', 0.0)}**")
    md_lines.append(f"- avg_prompt_goal_satisfaction: **{aggregate.get('avg_prompt_goal_satisfaction', 0.0)}**")
    md_lines.append(f"- avg_prompt_distinguishability: **{aggregate.get('avg_prompt_distinguishability', 0.0)}**")
    md_lines.append("")

    md_lines.append("## Prompt-Level Distinguishability")
    md_lines.append("")
    md_lines.append("| prompt | preview | passed | avg_quality | avg_goal | distinguishability | avg_max_similarity | near_dupes |")
    md_lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for item in prompt_summaries:
        sim = item.get("similarity") if isinstance(item.get("similarity"), dict) else {}
        near_count = len(sim.get("near_duplicate_pairs") or [])
        md_lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("prompt_id") or ""),
                    str(item.get("message_preview") or "").replace("|", "\\|"),
                    str(item.get("passed") or 0),
                    f"{float(item.get('avg_quality_score') or 0.0):.4f}",
                    f"{float(item.get('avg_goal_satisfaction') or 0.0):.4f}",
                    f"{float(sim.get('distinguishability_score') or 0.0):.4f}",
                    f"{float(sim.get('avg_max_similarity') or 0.0):.4f}",
                    str(near_count),
                ]
            )
            + " |"
        )

    md_lines.append("")
    md_lines.append("## Combo Stability Across Prompts")
    md_lines.append("")
    md_lines.append("| combo | mode | collab | level | roles | pass_rate | avg_quality | avg_goal | avg_len | avg_max_sim | quality_std |")
    md_lines.append("|---|---|---|---|---|---:|---:|---:|---:|---:|---:|")

    combo_rows = sorted(
        combo_rollups,
        key=lambda x: (-float(x.get("avg_max_similarity") or 0.0), str(x.get("combo_id") or "")),
    )
    for item in combo_rows:
        roles = "+".join(item.get("hired_roles") or []) if item.get("hired_roles") else "-"
        md_lines.append(
            "| "
            + " | ".join(
                [
                    str(item.get("combo_id") or ""),
                    str(item.get("response_mode") or ""),
                    str(item.get("collaboration_mode") or ""),
                    str(item.get("learning_level") or ""),
                    roles,
                    f"{float(item.get('pass_rate') or 0.0):.4f}",
                    f"{float(item.get('avg_quality_score') or 0.0):.4f}",
                    f"{float(item.get('avg_goal_satisfaction') or 0.0):.4f}",
                    str(int(item.get("avg_reply_length") or 0)),
                    f"{float(item.get('avg_max_similarity') or 0.0):.4f}",
                    f"{float(item.get('quality_stddev') or 0.0):.4f}",
                ]
            )
            + " |"
        )

    md_lines.append("")
    md_lines.append("## Recurring Near-Duplicate Pairs")
    md_lines.append("")
    if recurring_dupes:
        for pair in recurring_dupes[:30]:
            md_lines.append(
                "- "
                f"{pair.get('left')} vs {pair.get('right')} | occurrences={pair.get('occurrences')} "
                f"| max_similarity={pair.get('max_similarity')} | prompts={','.join(pair.get('prompt_ids') or [])}"
            )
    else:
        md_lines.append("- none")

    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    return {"json": str(json_path), "md": str(md_path)}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run strategy combo probe across multiple prompts and summarize stability/distinguishability"
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API base url")
    parser.add_argument(
        "--message",
        action="append",
        default=[],
        help="Prompt text. Can be used multiple times for multiple prompts",
    )
    parser.add_argument("--messages-file", default="", help="Path to txt/json/jsonl prompts file")
    parser.add_argument("--combo-limit", type=int, default=0, help="Optional combo cap for smoke run (0 = all)")
    parser.add_argument("--role", default="ops", help="chat role")
    parser.add_argument("--action", default="analysis", help="quality action")
    parser.add_argument("--timeout-sec", type=int, default=240, help="request timeout seconds")
    args = parser.parse_args()

    messages = _load_messages(args.message, args.messages_file or None)
    combos = probe._build_combos()
    if int(args.combo_limit or 0) > 0:
        combos = combos[: int(args.combo_limit)]

    print(f"[INFO] strategy_combo_multisim_target={args.base_url}")
    print(f"[INFO] prompts={len(messages)} combos={len(combos)}")

    check_quality = probe._load_quality_checker()

    session = requests.Session()
    email = f"strategy_combo_multisim_{uuid.uuid4().hex[:10]}@example.com"
    password = "Passw0rd!"
    token = probe._register_or_login(session, args.base_url, email, password)
    session.headers.update({"Authorization": f"Bearer {token}"})

    prompt_summaries: List[Dict[str, Any]] = []

    for idx, message in enumerate(messages, start=1):
        prompt_id = f"P{idx:02d}"
        print(f"\n[PROMPT] {prompt_id} {message}")

        prompt_results: List[probe.ComboResult] = []
        for combo in combos:
            roles = "+".join(combo["hired_roles"]) if combo["hired_roles"] else "-"
            print(
                f"[RUN] {prompt_id} {combo['combo_id']} mode={combo['response_mode']} "
                f"collab={combo['collaboration_mode']} level={combo['learning_level']} roles={roles}"
            )

            result = probe._run_combo(
                session,
                args.base_url,
                combo,
                message=message,
                role=args.role,
                action=args.action,
                check_quality=check_quality,
                timeout_sec=max(10, int(args.timeout_sec or 240)),
            )
            prompt_results.append(result)

            print(
                f"[{'PASS' if result.status == 200 else 'FAIL'}] {prompt_id} {result.combo_id} "
                f"quality={result.quality_score:.3f} goal={result.goal_satisfaction:.3f} "
                f"len={result.reply_length} status={result.status} elapsed={result.elapsed_ms}ms"
            )

        prompt_summary = _summarize_prompt(prompt_id, message, prompt_results)
        prompt_summaries.append(prompt_summary)

        sim = prompt_summary.get("similarity") if isinstance(prompt_summary.get("similarity"), dict) else {}
        print(
            f"[PROMPT-SUMMARY] {prompt_id} "
            f"distinguishability={float(sim.get('distinguishability_score') or 0.0):.4f} "
            f"avg_max_similarity={float(sim.get('avg_max_similarity') or 0.0):.4f} "
            f"near_duplicates={len(sim.get('near_duplicate_pairs') or [])}"
        )

    combo_rollups = _build_combo_rollups(prompt_summaries)
    aggregate = _aggregate_summary(prompt_summaries, combo_rollups)
    recurring_near_duplicates = _collect_recurring_near_duplicates(prompt_summaries)

    report_payload: Dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "base_url": args.base_url,
        "role": args.role,
        "action": args.action,
        "messages": messages,
        "combo_count": len(combos),
        "prompt_summaries": prompt_summaries,
        "combo_rollups": combo_rollups,
        "aggregate": aggregate,
        "recurring_near_duplicates": recurring_near_duplicates,
    }

    report_paths = _write_reports(report_payload)

    print(f"\nStrategy combo multi-sim JSON report: {report_paths['json']}")
    print(f"Strategy combo multi-sim markdown report: {report_paths['md']}")
    print(
        "Summary: "
        f"prompts={aggregate.get('prompt_count')} combos={aggregate.get('combo_count')} "
        f"runs={aggregate.get('total_runs')} pass={aggregate.get('total_pass')} "
        f"avg_distinguishability={aggregate.get('avg_prompt_distinguishability')} "
        f"recurring_near_dupes={len(recurring_near_duplicates)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
