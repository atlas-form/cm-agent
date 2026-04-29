from __future__ import annotations

import argparse
import json
import struct
import time
import zlib
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "tools" / "reports"
DEFAULT_BASE_URL = "http://127.0.0.1:8100"

def _probe_png_bytes(size: int = 32) -> bytes:
    side = max(16, int(size))
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", side, side, 8, 2, 0, 0, 0))
    row = b"\x00" + (b"\xCC\xE2\xFF" * side)
    raw = row * side
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


@dataclass
class CheckRow:
    name: str
    ok: bool
    detail: str = ""


def _append(rows: list[CheckRow], name: str, ok: bool, detail: str = "") -> None:
    rows.append(CheckRow(name=name, ok=bool(ok), detail=str(detail or "")))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")


def _register_or_login(session: requests.Session, base_url: str, email: str, password: str) -> str:
    reg = session.post(
        f"{base_url}/api/auth/register",
        json={"email": email, "password": password, "name": "DoubaoAngleProbe"},
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


def _post_with_retry(session: requests.Session, url: str, *, json_body: dict[str, Any] | None = None, files: dict[str, Any] | None = None, retries: int = 4) -> requests.Response:
    for i in range(retries):
        if files is not None:
            resp = session.post(url, files=files, timeout=240)
        else:
            resp = session.post(url, json=json_body or {}, timeout=240)
        if resp.status_code != 429:
            return resp
        if i < retries - 1:
            wait_s = 15 * (i + 1)
            print(f"[WARN] 429 retry url={url} attempt={i + 1}/{retries} wait={wait_s}s")
            time.sleep(wait_s)
    return resp


def _write_reports(report: dict[str, Any]) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(report.get("timestamp") or time.time())
    json_path = REPORT_DIR / f"doubao_product_angles_probe_{ts}.json"
    md_path = REPORT_DIR / f"doubao_product_angles_probe_{ts}.md"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines: list[str] = []
    lines.append("# Doubao Product Angles Probe")
    lines.append("")
    lines.append(f"- base_url: `{report.get('base_url')}`")
    lines.append(f"- timestamp: `{report.get('timestamp')}`")
    lines.append("")

    summary = report.get("summary") or {}
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- total: `{summary.get('total', 0)}`")
    lines.append(f"- passed: `{summary.get('passed', 0)}`")
    lines.append(f"- failed: `{summary.get('failed', 0)}`")
    lines.append("")

    lines.append("## Checks")
    lines.append("")
    lines.append("| check | ok | detail |")
    lines.append("|---|---:|---|")
    for row in report.get("checks") or []:
        lines.append(f"| {row.get('name')} | {'Y' if row.get('ok') else 'N'} | {str(row.get('detail') or '')[:220]} |")

    lines.append("")
    lines.append("## Key Outputs")
    lines.append("")
    key = report.get("key_outputs") or {}
    lines.append(f"- product_id: `{key.get('product_id')}`")
    lines.append(f"- llm_models_count: `{key.get('llm_models_count')}`")
    lines.append(f"- material_gen_image_engine: `{key.get('material_gen_image_engine')}`")
    lines.append(f"- material_gen_force_fallback_engine: `{key.get('material_gen_force_fallback_engine')}`")
    lines.append(f"- attachment_vision_engine: `{key.get('attachment_vision_engine')}`")
    lines.append(f"- chat_http_status: `{key.get('chat_http_status')}`")

    md_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Doubao from product-level business angles")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    args = parser.parse_args()

    base_url = str(args.base_url or DEFAULT_BASE_URL).rstrip("/")
    print(f"[INFO] target={base_url}")

    checks: list[CheckRow] = []
    out: dict[str, Any] = {
        "timestamp": int(time.time()),
        "base_url": base_url,
        "checks": [],
        "key_outputs": {},
    }

    session = requests.Session()

    # 0) health
    health = session.get(f"{base_url}/api/health", timeout=15)
    _append(checks, "health_200", health.status_code == 200, f"status={health.status_code}")

    # 1) auth
    email = f"doubao_angle_{uuid.uuid4().hex[:8]}@example.com"
    password = "DoubaoAngleProbe123!"
    token = _register_or_login(session, base_url, email, password)
    _append(checks, "auth_token_obtained", bool(token), f"token_len={len(token)}")
    if not token:
        out["checks"] = [row.__dict__ for row in checks]
        out["summary"] = {
            "total": len(checks),
            "passed": sum(1 for row in checks if row.ok),
            "failed": sum(1 for row in checks if not row.ok),
        }
        json_path, md_path = _write_reports(out)
        print(f"JSON report: {json_path}")
        print(f"MD report:   {md_path}")
        return 2

    session.headers.update({"Authorization": f"Bearer {token}"})

    # 2) llm models endpoint (product may rely on runtime model selection)
    llm_models_resp = session.get(f"{base_url}/api/llm/models", timeout=30)
    llm_models_body = llm_models_resp.json() if llm_models_resp.content else {}
    models = llm_models_body.get("models") if isinstance(llm_models_body, dict) else []
    model_ids = [str(m.get("id") or "") for m in models if isinstance(m, dict)] if isinstance(models, list) else []
    has_doubao = any(mid.startswith("doubao") for mid in model_ids)
    _append(checks, "llm_models_200", llm_models_resp.status_code == 200, f"status={llm_models_resp.status_code}")
    _append(checks, "llm_models_contains_doubao", has_doubao, f"count={len(model_ids)}")

    # 3) create product
    product_payload = {
        "name": f"豆包角度探针商品-{uuid.uuid4().hex[:6]}",
        "category": "小家电",
        "sku": f"SKU-{uuid.uuid4().hex[:6].upper()}",
        "cost_price": 59.0,
        "selling_price": 129.0,
        "supplier": "探针供应商",
        "description": "用于豆包多角度能力验证",
    }
    create_prod_resp = _post_with_retry(session, f"{base_url}/api/products", json_body=product_payload)
    create_prod_body = create_prod_resp.json() if create_prod_resp.content else {}
    product_id = int(create_prod_body.get("id") or 0) if isinstance(create_prod_body, dict) else 0
    _append(checks, "create_product_200", create_prod_resp.status_code == 200, f"status={create_prod_resp.status_code}")
    _append(checks, "create_product_has_id", product_id > 0, f"product_id={product_id}")

    if product_id <= 0:
        out["checks"] = [row.__dict__ for row in checks]
        out["summary"] = {
            "total": len(checks),
            "passed": sum(1 for row in checks if row.ok),
            "failed": sum(1 for row in checks if not row.ok),
        }
        json_path, md_path = _write_reports(out)
        print(f"JSON report: {json_path}")
        print(f"MD report:   {md_path}")
        return 1

    # 4) material generation with doubao image enabled
    gen_payload = {
        "material_type": "主图",
        "title": "主图-豆包生图",
        "brief": "突出静音、便携、续航",
        "platform": "淘宝",
        "style": "简约现代",
        "output_format": "auto",
        "save_to_files": True,
        "prefer_doubao_image": True,
    }
    gen_resp = _post_with_retry(session, f"{base_url}/api/products/{product_id}/materials/generate", json_body=gen_payload)
    gen_body = gen_resp.json() if gen_resp.content else {}
    engines = gen_body.get("engines") if isinstance(gen_body, dict) and isinstance(gen_body.get("engines"), dict) else {}
    image_engine = str(engines.get("image") or "")
    generated_files = gen_body.get("generated_files") if isinstance(gen_body, dict) and isinstance(gen_body.get("generated_files"), list) else []
    has_image_asset = any(str(x.get("file_type") or "") == "images" for x in generated_files if isinstance(x, dict))

    _append(checks, "material_generate_200", gen_resp.status_code == 200, f"status={gen_resp.status_code}")
    _append(checks, "material_generate_has_image_asset", has_image_asset, f"generated_files={len(generated_files)}")
    _append(checks, "material_generate_engine_doubao_or_fallback", image_engine in {"doubao", "svg_fallback"}, f"image_engine={image_engine}")

    # 5) force fallback by request
    force_payload = dict(gen_payload)
    force_payload["title"] = "主图-强制兜底"
    force_payload["prefer_doubao_image"] = False
    force_resp = _post_with_retry(session, f"{base_url}/api/products/{product_id}/materials/generate", json_body=force_payload)
    force_body = force_resp.json() if force_resp.content else {}
    force_engines = force_body.get("engines") if isinstance(force_body, dict) and isinstance(force_body.get("engines"), dict) else {}
    force_image_engine = str(force_engines.get("image") or "")
    _append(checks, "material_force_fallback_200", force_resp.status_code == 200, f"status={force_resp.status_code}")
    _append(checks, "material_force_fallback_engine_svg", force_image_engine == "svg_fallback", f"image_engine={force_image_engine}")

    # 6) attachment image vision parse
    upload_resp = _post_with_retry(
        session,
        f"{base_url}/api/chat/attachments/upload",
        files={"file": ("probe.png", _probe_png_bytes(32), "image/png")},
    )
    upload_body = upload_resp.json() if upload_resp.content else {}
    attachment = upload_body.get("attachment") if isinstance(upload_body, dict) and isinstance(upload_body.get("attachment"), dict) else {}
    att_vision_engine = str(attachment.get("vision_engine") or "")
    att_status = str(attachment.get("status") or "")
    _append(checks, "attachment_upload_200", upload_resp.status_code == 200, f"status={upload_resp.status_code}")
    _append(checks, "attachment_upload_parsed_or_partial", att_status in {"parsed", "partial"}, f"status={att_status}")
    _append(checks, "attachment_vision_engine_valid", att_vision_engine in {"doubao", "ocr-fallback", ""}, f"vision_engine={att_vision_engine}")

    # 7) /api/chat with attachment context
    chat_payload = {
        "message": "请基于附件给出3个执行动作，并明确指出处理结果是否来自图片识别。",
        "role": "ops",
        "conversation_id": f"doubao_angle_chat_{uuid.uuid4().hex[:10]}",
        "attachments": [attachment] if attachment else [],
    }
    chat_resp = _post_with_retry(session, f"{base_url}/api/chat", json_body=chat_payload)
    chat_body = chat_resp.json() if chat_resp.content else {}
    chat_reply = str(chat_body.get("reply") or "") if isinstance(chat_body, dict) else ""
    _append(checks, "chat_with_attachment_200", chat_resp.status_code == 200, f"status={chat_resp.status_code}")
    _append(checks, "chat_with_attachment_nonempty_reply", bool(chat_reply.strip()), f"reply_len={len(chat_reply)}")

    out["checks"] = [row.__dict__ for row in checks]
    out["summary"] = {
        "total": len(checks),
        "passed": sum(1 for row in checks if row.ok),
        "failed": sum(1 for row in checks if not row.ok),
    }
    out["key_outputs"] = {
        "product_id": product_id,
        "llm_models_count": len(model_ids),
        "material_gen_image_engine": image_engine,
        "material_gen_force_fallback_engine": force_image_engine,
        "attachment_vision_engine": att_vision_engine,
        "attachment_status": att_status,
        "chat_http_status": chat_resp.status_code,
        "chat_reply_preview": chat_reply[:260],
    }
    out["raw"] = {
        "llm_models": llm_models_body,
        "material_generate": gen_body,
        "material_generate_force_fallback": force_body,
        "attachment_upload": upload_body,
        "chat": chat_body,
    }

    json_path, md_path = _write_reports(out)
    print("")
    print(f"JSON report: {json_path}")
    print(f"MD report:   {md_path}")

    return 0 if out["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

