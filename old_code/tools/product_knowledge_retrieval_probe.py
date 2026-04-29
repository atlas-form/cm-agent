from __future__ import annotations

import argparse
import json
import time
import uuid
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "tools" / "reports"
DEFAULT_BASE_URL = "http://127.0.0.1:8100"


def _register_or_login(session: requests.Session, base_url: str, email: str, password: str) -> str:
    reg = session.post(
        f"{base_url}/api/auth/register",
        json={"email": email, "password": password, "name": "KnowledgeProbe"},
        timeout=30,
    )
    if reg.status_code == 200:
        return str((reg.json() or {}).get("token") or "")
    if reg.status_code == 409:
        login = session.post(
            f"{base_url}/api/auth/login",
            json={"email": email, "password": password},
            timeout=30,
        )
        login.raise_for_status()
        return str((login.json() or {}).get("token") or "")
    reg.raise_for_status()
    return ""


def _write_reports(report: dict[str, Any]) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(report.get("timestamp") or time.time())
    json_path = REPORT_DIR / f"product_knowledge_retrieval_probe_{ts}.json"
    md_path = REPORT_DIR / f"product_knowledge_retrieval_probe_{ts}.md"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines: list[str] = []
    lines.append("# Product Knowledge Retrieval Probe")
    lines.append("")
    lines.append(f"- base_url: `{report.get('base_url')}`")
    lines.append(f"- product_id: `{report.get('product_id')}`")
    lines.append(f"- checks: `{report.get('summary')}`")
    lines.append("")
    lines.append("## Checks")
    lines.append("")
    lines.append("| check | ok | detail |")
    lines.append("|---|---:|---|")
    for row in report.get("checks") or []:
        lines.append(f"| {row.get('name')} | {'Y' if row.get('ok') else 'N'} | {row.get('detail')} |")
    lines.append("")
    lines.append("## Reply Preview")
    lines.append("")
    lines.append(str(report.get("reply_preview") or ""))

    md_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe product knowledge retrieval quality under embedding fallback")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args()

    base_url = str(args.base_url or DEFAULT_BASE_URL).rstrip("/")
    session = requests.Session()

    email = f"knowledge_probe_{uuid.uuid4().hex[:8]}@example.com"
    password = "KnowledgeProbe123!"
    token = _register_or_login(session, base_url, email, password)
    if not token:
        print(json.dumps({"ok": False, "reason": "auth_failed"}, ensure_ascii=False))
        return 2
    session.headers.update({"Authorization": f"Bearer {token}"})

    checks: list[dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail or "")})
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    create_payload = {
        "name": f"检索探针商品-{uuid.uuid4().hex[:6]}",
        "category": "小家电",
        "sku": f"KB-{uuid.uuid4().hex[:6].upper()}",
        "cost_price": 69.0,
        "selling_price": 149.0,
        "description": "用于验证 product_id 检索与知识注入",
    }
    r_prod = session.post(f"{base_url}/api/products", json=create_payload, timeout=40)
    ok_prod = r_prod.status_code == 200
    body_prod = r_prod.json() if r_prod.content else {}
    product_id = int(body_prod.get("id") or 0) if isinstance(body_prod, dict) else 0
    check("create_product_200", ok_prod, f"status={r_prod.status_code}")
    check("create_product_has_id", product_id > 0, f"product_id={product_id}")

    if product_id <= 0:
        report = {
            "timestamp": int(time.time()),
            "base_url": base_url,
            "product_id": product_id,
            "checks": checks,
            "summary": {
                "total": len(checks),
                "passed": sum(1 for x in checks if x["ok"]),
                "failed": sum(1 for x in checks if not x["ok"]),
            },
        }
        jp, mp = _write_reports(report)
        print(json.dumps({"ok": False, "report_json": str(jp), "report_md": str(mp)}, ensure_ascii=False))
        return 1

    kb_entries = [
        "库存安全阈值固定为19，低于19件必须在2小时内补货。",
        "价格带策略：主力成交区间保持在89-129元，避免超过129元导致转化下降。",
        "主图优先卖点：静音、便携、续航，文案要包含“48小时发货”。",
    ]
    for text in kb_entries:
        r_kb = session.post(
            f"{base_url}/api/products/{product_id}/knowledge",
            json={"content": text, "content_type": "manual", "confidence": 0.95},
            timeout=30,
        )
        check("insert_knowledge_200", r_kb.status_code == 200, f"status={r_kb.status_code}")

    chat_payload = {
        "message": "请基于该商品输出3个本周执行动作，必须体现库存阈值和价格带策略。",
        "role": "ops",
        "product_id": product_id,
        "conversation_id": f"kb_probe_{uuid.uuid4().hex[:10]}",
    }
    t0 = time.time()
    r_chat = session.post(f"{base_url}/api/chat", json=chat_payload, timeout=240)
    elapsed_ms = int((time.time() - t0) * 1000)
    body_chat = r_chat.json() if r_chat.content else {}
    reply = str(body_chat.get("reply") or "") if isinstance(body_chat, dict) else ""
    metadata = body_chat.get("metadata") if isinstance(body_chat, dict) and isinstance(body_chat.get("metadata"), dict) else {}

    check("chat_200", r_chat.status_code == 200, f"status={r_chat.status_code} elapsed_ms={elapsed_ms}")
    check("chat_reply_nonempty", bool(reply.strip()), f"reply_len={len(reply)}")

    hits = {
        "库存": "库存" in reply,
        "19": "19" in reply,
        "89": "89" in reply,
        "129": "129" in reply,
    }
    hit_count = sum(1 for v in hits.values() if v)
    check("reply_contains_key_knowledge", hit_count >= 2, f"hits={hits}")

    report = {
        "timestamp": int(time.time()),
        "base_url": base_url,
        "product_id": product_id,
        "checks": checks,
        "summary": {
            "total": len(checks),
            "passed": sum(1 for x in checks if x["ok"]),
            "failed": sum(1 for x in checks if not x["ok"]),
        },
        "knowledge_entries": kb_entries,
        "reply_preview": reply[:1200],
        "reply_keyword_hits": hits,
        "chat_metadata_keys": list(metadata.keys()),
    }

    jp, mp = _write_reports(report)
    print(
        json.dumps(
            {
                "ok": report["summary"]["failed"] == 0,
                "summary": report["summary"],
                "report_json": str(jp),
                "report_md": str(mp),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
