from __future__ import annotations

import argparse
import base64
import io
import json
import os
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = os.getenv("VISION_FALLBACK_PROBE_BASE_URL") or os.getenv("SMOKE_BASE_URL") or "http://127.0.0.1:8100"
REPORT_DIR = ROOT / "tools" / "reports"

# 1x1 PNG
PNG_BYTES = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+kvwAAAABJRU5ErkJggg==")


@dataclass
class CheckRow:
    name: str
    ok: bool
    detail: str = ""


def _register_or_login(session: requests.Session, base_url: str, email: str, password: str) -> str:
    register_resp = session.post(
        f"{base_url}/api/auth/register",
        json={"email": email, "password": password, "name": "VisionFallbackProbe"},
        timeout=30,
    )
    if register_resp.status_code == 200:
        return str(register_resp.json().get("token") or "")

    if register_resp.status_code == 409:
        login_resp = session.post(
            f"{base_url}/api/auth/login",
            json={"email": email, "password": password},
            timeout=30,
        )
        login_resp.raise_for_status()
        return str(login_resp.json().get("token") or "")

    register_resp.raise_for_status()
    return ""


def _append_check(rows: List[CheckRow], name: str, ok: bool, detail: str = "") -> None:
    rows.append(CheckRow(name=name, ok=bool(ok), detail=str(detail or "")))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def _write_reports(base_url: str, rows: List[CheckRow], attachment: Dict[str, Any]) -> Dict[str, str]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())

    total = len(rows)
    passed = sum(1 for row in rows if row.ok)
    failed = total - passed

    summary = {
        "base_url": base_url,
        "total": total,
        "passed": passed,
        "failed": failed,
        "checks": [{"name": row.name, "ok": row.ok, "detail": row.detail} for row in rows],
        "attachment": {
            "id": str(attachment.get("id") or ""),
            "vision_engine": str(attachment.get("vision_engine") or ""),
            "vision_fallback_reason": str(attachment.get("vision_fallback_reason") or ""),
            "vision_warning": str(attachment.get("vision_warning") or ""),
            "vision_is_degraded": bool(attachment.get("vision_is_degraded")),
            "parser": str(attachment.get("parser") or ""),
            "status": str(attachment.get("status") or ""),
        },
    }

    json_path = REPORT_DIR / f"full_feature_vision_fallback_probe_{ts}.json"
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines: List[str] = []
    md_lines.append("# Vision Fallback Probe")
    md_lines.append("")
    md_lines.append(f"- base_url: `{base_url}`")
    md_lines.append(f"- total: **{total}**  passed: **{passed}**  failed: **{failed}**")
    md_lines.append("")
    md_lines.append("## Checks")
    md_lines.append("")
    md_lines.append("| check | ok | detail |")
    md_lines.append("|---|---:|---|")
    for row in rows:
        md_lines.append(f"| {row.name} | {'Y' if row.ok else 'N'} | {row.detail} |")

    md_lines.append("")
    md_lines.append("## Attachment")
    md_lines.append("")
    md_lines.append(f"- vision_engine: `{summary['attachment']['vision_engine']}`")
    md_lines.append(f"- vision_fallback_reason: `{summary['attachment']['vision_fallback_reason']}`")
    md_lines.append(f"- vision_is_degraded: `{summary['attachment']['vision_is_degraded']}`")
    md_lines.append(f"- vision_warning: `{summary['attachment']['vision_warning']}`")

    md_path = REPORT_DIR / f"full_feature_vision_fallback_probe_{ts}.md"
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    return {"json": str(json_path), "md": str(md_path)}


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe forced vision fallback path (DOUBAO disabled => OCR fallback)")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="API base url, e.g. http://127.0.0.1:8233")
    args = parser.parse_args(argv)

    base_url = str(args.base_url or DEFAULT_BASE_URL).rstrip("/")
    print(f"[INFO] vision_fallback_probe_target={base_url}")

    session = requests.Session()
    suffix = uuid.uuid4().hex[:8]
    email = f"vision_fallback_probe_{suffix}@example.com"
    password = "VisionFallbackProbe123!"

    token = _register_or_login(session, base_url, email, password)
    if not token:
        print("[FAIL] failed to acquire auth token")
        return 2

    session.headers.update({"Authorization": f"Bearer {token}"})

    checks: List[CheckRow] = []
    attachment: Dict[str, Any] = {}

    files = {"file": ("vision_probe.png", io.BytesIO(PNG_BYTES), "image/png")}
    t0 = time.time()
    resp = session.post(f"{base_url}/api/chat/attachments/upload", files=files, timeout=120)
    elapsed_ms = int((time.time() - t0) * 1000)

    _append_check(checks, "upload_http_200", resp.status_code == 200, detail=f"status={resp.status_code} elapsed_ms={elapsed_ms}")

    if resp.status_code == 200:
        body = resp.json() if resp.content else {}
        attachment = body.get("attachment") if isinstance(body.get("attachment"), dict) else {}
    else:
        attachment = {}

    _append_check(checks, "attachment_present", isinstance(attachment, dict) and bool(attachment), detail=f"has_attachment={bool(attachment)}")

    required_fields = ("vision_engine", "vision_fallback_reason", "vision_warning", "vision_is_degraded")
    fields_ok = all(field in attachment for field in required_fields)
    _append_check(checks, "vision_fields_present", fields_ok, detail=f"required={','.join(required_fields)}")

    vision_engine = str(attachment.get("vision_engine") or "")
    vision_fallback_reason = str(attachment.get("vision_fallback_reason") or "")
    vision_warning = str(attachment.get("vision_warning") or "")
    vision_is_degraded = bool(attachment.get("vision_is_degraded"))

    _append_check(checks, "vision_is_degraded_true", vision_is_degraded, detail=f"vision_is_degraded={vision_is_degraded}")
    _append_check(checks, "vision_engine_ocr_fallback", vision_engine == "ocr-fallback", detail=f"vision_engine={vision_engine}")
    _append_check(checks, "fallback_reason_nonempty", bool(vision_fallback_reason.strip()), detail=f"vision_fallback_reason={vision_fallback_reason}")

    # Accept both "豆包读图故障" copy and "图片分辨率过低" copy, as long as fallback semantics are explicit.
    warning_ok = ("OCR" in vision_warning) and ("兜底" in vision_warning)
    _append_check(checks, "warning_semantics_check", warning_ok, detail=f"vision_warning={vision_warning}")

    notices = attachment.get("notices") if isinstance(attachment.get("notices"), list) else []
    notices_text = " | ".join(str(item) for item in notices[:3])
    _append_check(checks, "warning_visible_in_notices", any("OCR" in str(item) for item in notices), detail=notices_text)

    out = _write_reports(base_url, checks, attachment)
    passed = sum(1 for row in checks if row.ok)

    print("")
    print(f"Vision fallback probe JSON report: {out['json']}")
    print(f"Vision fallback probe markdown report: {out['md']}")
    print(f"Summary: total={len(checks)} passed={passed} failed={len(checks) - passed}")

    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())

