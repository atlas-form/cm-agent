from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import struct
import time
import uuid
import zipfile
import zlib
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Tuple
from xml.sax.saxutils import escape as xml_escape

import requests

try:
    from PIL import Image, ImageDraw
except Exception:  # pragma: no cover - probe should still run without pillow
    Image = None
    ImageDraw = None


ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "tools" / "reports"
TMP_ROOT = ROOT / "tools" / "tmp"
DEFAULT_BASE_URL = os.getenv("ATTACHMENT_PROBE_BASE_URL") or os.getenv("SMOKE_BASE_URL") or "http://127.0.0.1:8100"

def _png_chunk(tag: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(tag + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)


def _build_solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    r, g, b = [max(0, min(255, int(x))) for x in rgb]
    row = b"\x00" + bytes([r, g, b]) * width
    raw = row * height
    compressed = zlib.compress(raw, level=9)

    signature = b"\x89PNG\r\n\x1a\n"
    ihdr = _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    idat = _png_chunk(b"IDAT", compressed)
    iend = _png_chunk(b"IEND", b"")
    return signature + ihdr + idat + iend


PNG_1X1_BYTES = _build_solid_png(64, 64, (245, 245, 245))


def _build_probe_images(lines: List[str] | None = None) -> tuple[bytes, bytes, bytes]:
    png_bytes = _build_solid_png(64, 64, (245, 245, 245))
    jpg_bytes = png_bytes
    webp_bytes = png_bytes

    if Image is not None:
        try:
            if lines:
                image = Image.new("RGB", (900, 520), (248, 250, 252))
                draw = ImageDraw.Draw(image)
                y = 24
                for raw_line in lines[:10]:
                    line = str(raw_line or "").strip()
                    if not line:
                        continue
                    draw.text((24, y), line, fill=(24, 28, 36))
                    y += 44
            else:
                image = Image.new("RGB", (64, 64), (245, 245, 245))

            png_buf = BytesIO()
            image.save(png_buf, format="PNG")
            png_bytes = png_buf.getvalue()

            jpg_buf = BytesIO()
            image.save(jpg_buf, format="JPEG", quality=90)
            jpg_bytes = jpg_buf.getvalue()

            webp_buf = BytesIO()
            image.save(webp_buf, format="WEBP", quality=90)
            webp_bytes = webp_buf.getvalue()
        except Exception:
            pass
    return png_bytes, jpg_bytes, webp_bytes


PNG_SAMPLE_BYTES, JPG_SAMPLE_BYTES, WEBP_SAMPLE_BYTES = _build_probe_images()


@dataclass
class ProbeCase:
    domain: str
    label: str
    path: Path


@dataclass
class UploadRecord:
    domain: str
    label: str
    file_name: str
    http_status: int
    ok: bool
    elapsed_ms: int
    error: str
    attachment: Dict[str, Any]


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _write_probe_images(
    dir_path: Path,
    png_name: str,
    jpg_name: str,
    webp_name: str,
    *,
    lines: List[str] | None = None,
) -> None:
    if lines:
        png_bytes, jpg_bytes, webp_bytes = _build_probe_images(lines)
    else:
        png_bytes, jpg_bytes, webp_bytes = PNG_SAMPLE_BYTES, JPG_SAMPLE_BYTES, WEBP_SAMPLE_BYTES
    _write_bytes(dir_path / png_name, png_bytes)
    _write_bytes(dir_path / jpg_name, jpg_bytes)
    _write_bytes(dir_path / webp_name, webp_bytes)


def _write_minimal_docx(path: Path, paragraphs: List[str]) -> None:
    doc_xml_lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">',
        "<w:body>",
    ]
    for text in paragraphs:
        safe = xml_escape(text)
        doc_xml_lines.append(f'<w:p><w:r><w:t xml:space="preserve">{safe}</w:t></w:r></w:p>')
    doc_xml_lines.extend(
        [
            "<w:sectPr>",
            '<w:pgSz w:w="11906" w:h="16838"/>',
            '<w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="708" w:footer="708" w:gutter="0"/>',
            "</w:sectPr>",
            "</w:body>",
            "</w:document>",
        ]
    )
    doc_xml = "\n".join(doc_xml_lines)

    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""
    rels_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels_xml)
        zf.writestr("word/document.xml", doc_xml)


def _column_label(index: int) -> str:
    # 1-based
    result = []
    n = index
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result.append(chr(65 + rem))
    return "".join(reversed(result))


def _write_minimal_xlsx(path: Path, rows: List[List[str]]) -> None:
    sheet_lines = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        "<sheetData>",
    ]
    for r_idx, row in enumerate(rows, start=1):
        sheet_lines.append(f'<row r="{r_idx}">')
        for c_idx, cell in enumerate(row, start=1):
            ref = f"{_column_label(c_idx)}{r_idx}"
            safe = xml_escape(str(cell))
            sheet_lines.append(f'<c r="{ref}" t="inlineStr"><is><t>{safe}</t></is></c>')
        sheet_lines.append("</row>")
    sheet_lines.extend(["</sheetData>", "</worksheet>"])
    sheet_xml = "\n".join(sheet_lines)

    workbook_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>
    <sheet name="Sheet1" sheetId="1" r:id="rId1"/>
  </sheets>
</workbook>
"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>
"""
    root_rels = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>
"""
    content_types = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
  <Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
</Types>
"""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", root_rels)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def _build_minimal_pptx_slide_xml(lines: List[str]) -> str:
    nodes: List[str] = []
    for raw in lines:
        line = str(raw or "").strip()
        if not line:
            continue
        nodes.append(f"<text>{xml_escape(line)}</text>")
    if not nodes:
        nodes.append("<text>Slide</text>")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<root><shape>'
        + ''.join(nodes)
        + '</shape></root>'
    )


def _write_minimal_pptx(path: Path, slides: List[List[str]]) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for idx, lines in enumerate(slides, start=1):
            zf.writestr(f"ppt/slides/slide{idx}.xml", _build_minimal_pptx_slide_xml(lines))



def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _build_minimal_pdf(lines: List[str]) -> bytes:
    text_ops: List[str] = ["BT", "/F1 12 Tf", "50 760 Td"]
    first = True
    for raw in lines:
        line = _pdf_escape(str(raw))
        if not first:
            text_ops.append("0 -16 Td")
        text_ops.append(f"({line}) Tj")
        first = False
    text_ops.append("ET")
    stream_content = "\n".join(text_ops).encode("latin-1", errors="replace")

    objects: List[bytes] = []
    objects.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    objects.append(b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n")
    objects.append(
        b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>\nendobj\n"
    )
    objects.append(
        f"4 0 obj\n<< /Length {len(stream_content)} >>\nstream\n".encode("ascii")
        + stream_content
        + b"\nendstream\nendobj\n"
    )
    objects.append(b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n")

    header = b"%PDF-1.4\n%\xE2\xE3\xCF\xD3\n"
    out = bytearray(header)
    offsets = [0]
    for obj in objects:
        offsets.append(len(out))
        out.extend(obj)

    xref_start = len(out)
    out.extend(f"xref\n0 {len(offsets)}\n".encode("ascii"))
    out.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        out.extend(f"{off:010d} 00000 n \n".encode("ascii"))

    trailer = (
        f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode("ascii")
    )
    out.extend(trailer)
    return bytes(out)


def _write_minimal_pdf(path: Path, lines: List[str]) -> None:
    _write_bytes(path, _build_minimal_pdf(lines))


def _build_cases(tmp_dir: Path, *, images_only: bool = False) -> List[ProbeCase]:
    cases: List[ProbeCase] = []

    # E-commerce related
    ecom_dir = tmp_dir / "ecommerce"
    if not images_only:
        _write_text(
            ecom_dir / "ecom_brief.txt",
            "项目: 618主推款\nSKU-A1: 便携静音风扇\n目标ROI: 2.4\n库存周转天数: 18\n",
        )
        _write_text(
            ecom_dir / "ecom_campaign.md",
            "# 618投放节奏\n- 预热期: 提升点击率\n- 爆发期: 控制CPA\n- 复盘期: 关注ROI与复购\n",
        )
        _write_text(
            ecom_dir / "ecom_metrics.csv",
            "sku,uv,orders,roi,inventory_days\nSKU-A1,12000,420,2.4,18\nSKU-B2,9000,250,1.8,31\n",
        )
        _write_text(
            ecom_dir / "ecom_ops.json",
            json.dumps(
                {
                    "shop": "旗舰店A",
                    "priority_actions": ["优化主图", "提升客服首响", "夜间加预算"],
                    "core_metric": {"roi_target": 2.4, "inventory_days_target": 20},
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        _write_text(
            ecom_dir / "ecom_faq.yaml",
            "faq:\n  - q: 为什么ROI下降?\n    a: 新流量质量波动，需要分时段控投。\n",
        )
        _write_text(
            ecom_dir / "ecom_rules.sql",
            "SELECT sku, roi, inventory_days FROM ad_daily WHERE dt >= '2026-06-01' ORDER BY roi DESC;\n",
        )
        _write_text(
            ecom_dir / "ecom_landing.html",
            "<html><body><h1>618 Landing</h1><p>主推 SKU-A1，目标 ROI 2.4。</p></body></html>\n",
        )
        _write_minimal_xlsx(
            ecom_dir / "ecom_sheet.xlsx",
            [
                ["sku", "roi", "inventory_days"],
                ["SKU-A1", "2.4", "18"],
                ["SKU-B2", "1.8", "31"],
            ],
        )
        _write_minimal_docx(
            ecom_dir / "ecom_service_guide.docx",
            [
                "客服话术要点: 先确认订单号，再给出补偿方案。",
                "主推 SKU-A1，强调静音与便携。",
            ],
        )
        _write_minimal_pdf(
            ecom_dir / "ecom_report.pdf",
            [
                "June Campaign Summary",
                "SKU-A1 ROI 2.4",
                "Inventory days 18",
                "Priority: image CTR optimization",
            ],
        )
        _write_minimal_pptx(
            ecom_dir / "ecom_briefing.pptx",
            [
                ["618 复盘", "SKU-A1 ROI 2.4"],
                ["库存周转天数 18", "优先动作: 主图点击率优化"],
            ],
        )

        with zipfile.ZipFile(ecom_dir / "ecom_text_bundle.zip", "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("brief.txt", "主推 SKU-A1，目标 ROI 2.4")
            zf.writestr("metrics.csv", "sku,roi,inventory_days\nSKU-A1,2.4,18")

        docx_bytes = (ecom_dir / "ecom_service_guide.docx").read_bytes()
        with zipfile.ZipFile(ecom_dir / "ecom_docx_bundle.zip", "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("service_guide.docx", docx_bytes)
            zf.writestr("notes.txt", "ZIP 内含 Docx 与说明")

        nested_zip_stream = BytesIO()
        with zipfile.ZipFile(nested_zip_stream, "w", compression=zipfile.ZIP_DEFLATED) as nested_zf:
            nested_zf.writestr("inside.txt", "嵌套 ZIP 内文本：库存周转 18")
        with zipfile.ZipFile(ecom_dir / "ecom_nested_bundle.zip", "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("nested_bundle.zip", nested_zip_stream.getvalue())
            zf.writestr("notes.txt", "外层 ZIP 说明")
    _write_probe_images(
        ecom_dir,
        "ecom_poster.png",
        "ecom_scene.jpg",
        "ecom_banner.webp",
        lines=[
            "SKU-A1 ROI 2.4",
            "库存周转天数 18",
            "优先动作: 主图点击率优化",
        ],
    )

    for p in sorted(ecom_dir.iterdir()):
        cases.append(ProbeCase(domain="ecommerce", label=p.stem, path=p))

    # Non e-commerce related
    non_dir = tmp_dir / "non_ecommerce"
    if not images_only:
        _write_text(
            non_dir / "travel_notes.txt",
            "行程: 成都 -> 稻城亚丁 -> 四姑娘山\n重点: 高反预防、徒步路线、补给计划。\n",
        )
        _write_text(
            non_dir / "study_plan.md",
            "# 备考计划\n- 早上: 线性代数\n- 下午: 英语阅读\n- 晚上: 编程练习\n",
        )
        _write_text(
            non_dir / "recipe.csv",
            "dish,main_ingredient,time_minutes\n番茄炖牛腩,牛腩+番茄,90\n清炒时蔬,青菜,15\n",
        )
        _write_text(
            non_dir / "movie_list.json",
            json.dumps(
                {
                    "weekend": ["Interstellar", "The Martian"],
                    "note": "科幻题材，适合晚间观看",
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        _write_minimal_pdf(
            non_dir / "non_ecom_report.pdf",
            [
                "Travel checklist",
                "Destination: Daocheng Yading",
                "Meal: tomato beef stew",
            ],
        )
        _write_minimal_pptx(
            non_dir / "trip_briefing.pptx",
            [
                ["稻城亚丁行程", "徒步 3 天"],
                ["番茄炖牛腩", "预计耗时 90 分钟"],
            ],
        )
        with zipfile.ZipFile(non_dir / "non_ecom_bundle.zip", "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("travel_notes.txt", "行程: 成都 -> 稻城亚丁")
            zf.writestr("recipe.csv", "dish,time_minutes\n番茄炖牛腩,90")
    _write_probe_images(
        non_dir,
        "notes.png",
        "travel_scene.jpg",
        "sketch.webp",
        lines=[
            "稻城亚丁 徒步 3天",
            "番茄炖牛腩 90分钟",
            "行程: 成都 -> 稻城亚丁",
        ],
    )

    for p in sorted(non_dir.iterdir()):
        cases.append(ProbeCase(domain="non_ecommerce", label=p.stem, path=p))

    if not images_only:
        # Unsupported / fallback expectation
        unsupported_dir = tmp_dir / "unsupported"
        unsupported_dir.mkdir(parents=True, exist_ok=True)
        _write_bytes(
            unsupported_dir / "archive.7z",
            b"7z\xBC\xAF\x27\x1Cunsupported-binary-payload",
        )
        _write_bytes(
            unsupported_dir / "slides.ppt",
            b"\xD0\xCF\x11\xE0legacy-ppt-binary-payload",
        )

        for p in sorted(unsupported_dir.iterdir()):
            cases.append(ProbeCase(domain="unsupported", label=p.stem, path=p))

    return cases


def _register_or_login(session: requests.Session, base_url: str, email: str, password: str) -> str:
    reg = session.post(
        f"{base_url}/api/auth/register",
        json={"email": email, "password": password, "name": "AttachmentProbe"},
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


def _upload_one(session: requests.Session, base_url: str, case: ProbeCase) -> UploadRecord:
    mime = mimetypes.guess_type(str(case.path))[0] or "application/octet-stream"
    max_attempts = 4
    backoff_seconds = 15

    for attempt in range(1, max_attempts + 1):
        t0 = time.time()
        try:
            with case.path.open("rb") as fh:
                resp = session.post(
                    f"{base_url}/api/chat/attachments/upload",
                    files={"file": (case.path.name, fh, mime)},
                    timeout=180,
                )
            elapsed = int((time.time() - t0) * 1000)

            if resp.status_code == 429 and attempt < max_attempts:
                print(
                    f"[WARN] upload_429_retry file={case.path.name} "
                    f"attempt={attempt}/{max_attempts} wait={backoff_seconds}s"
                )
                time.sleep(backoff_seconds)
                continue

            body = resp.json() if resp.content else {}
            attachment = body.get("attachment") if isinstance(body, dict) and isinstance(body.get("attachment"), dict) else {}
            ok = resp.status_code == 200 and bool(attachment)
            err = "" if ok else (str(body)[:320] if body else (resp.text or "")[:320])
            return UploadRecord(
                domain=case.domain,
                label=case.label,
                file_name=case.path.name,
                http_status=resp.status_code,
                ok=ok,
                elapsed_ms=elapsed,
                error=err,
                attachment=attachment if isinstance(attachment, dict) else {},
            )
        except Exception as exc:  # noqa: BLE001
            elapsed = int((time.time() - t0) * 1000)
            if attempt < max_attempts:
                print(
                    f"[WARN] upload_exception_retry file={case.path.name} "
                    f"attempt={attempt}/{max_attempts} wait={backoff_seconds}s err={type(exc).__name__}"
                )
                time.sleep(backoff_seconds)
                continue
            return UploadRecord(
                domain=case.domain,
                label=case.label,
                file_name=case.path.name,
                http_status=0,
                ok=False,
                elapsed_ms=elapsed,
                error=f"exception={type(exc).__name__}:{exc}",
                attachment={},
            )

    return UploadRecord(
        domain=case.domain,
        label=case.label,
        file_name=case.path.name,
        http_status=0,
        ok=False,
        elapsed_ms=0,
        error="upload_failed_after_retries",
        attachment={},
    )


def _pick_chat_attachments(records: List[UploadRecord], domain: str, limit: int = 5) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for rec in records:
        if rec.domain != domain or not rec.ok:
            continue
        item = dict(rec.attachment or {})
        if not item:
            continue
        candidates.append(item)
    candidates.sort(key=lambda it: int(it.get("content_chars") or 0), reverse=True)
    return candidates[: max(1, limit)]


def _chat_once(
    session: requests.Session,
    base_url: str,
    *,
    message: str,
    attachments: List[Dict[str, Any]],
    role: str = "ops",
) -> Dict[str, Any]:
    payload = {
        "message": message,
        "role": role,
        "conversation_id": f"attachment_probe_{uuid.uuid4().hex[:10]}",
        "attachments": attachments,
    }

    max_attempts = 4
    backoff_seconds = 20
    for attempt in range(1, max_attempts + 1):
        t0 = time.time()
        try:
            resp = session.post(f"{base_url}/api/chat", json=payload, timeout=240)
            elapsed = int((time.time() - t0) * 1000)

            if resp.status_code == 429 and attempt < max_attempts:
                print(f"[WARN] chat_429_retry attempt={attempt}/{max_attempts} wait={backoff_seconds}s")
                time.sleep(backoff_seconds)
                continue

            body = resp.json() if resp.content else {}
            return {
                "http_status": resp.status_code,
                "elapsed_ms": elapsed,
                "ok": resp.status_code == 200,
                "reply": str(body.get("reply") or ""),
                "role": str(body.get("role") or ""),
                "metadata": body.get("metadata") if isinstance(body.get("metadata"), dict) else {},
                "error": "" if resp.status_code == 200 else (resp.text or "")[:480],
            }
        except Exception as exc:  # noqa: BLE001
            elapsed = int((time.time() - t0) * 1000)
            if attempt < max_attempts:
                print(f"[WARN] chat_exception_retry attempt={attempt}/{max_attempts} wait={backoff_seconds}s err={type(exc).__name__}")
                time.sleep(backoff_seconds)
                continue
            return {
                "http_status": 0,
                "elapsed_ms": elapsed,
                "ok": False,
                "reply": "",
                "role": "",
                "metadata": {},
                "error": f"exception={type(exc).__name__}:{exc}",
            }

    return {
        "http_status": 0,
        "elapsed_ms": 0,
        "ok": False,
        "reply": "",
        "role": "",
        "metadata": {},
        "error": "chat_failed_after_retries",
    }


def _keyword_hit_count(text: str, keywords: List[str]) -> int:
    src = str(text or "")
    return sum(1 for kw in keywords if kw and kw in src)


def _summarize_upload(records: List[UploadRecord]) -> Dict[str, Any]:
    by_domain: Dict[str, Dict[str, int]] = {}
    for rec in records:
        domain_bucket = by_domain.setdefault(rec.domain, {"total": 0, "ok": 0, "parsed": 0, "partial": 0, "failed": 0})
        domain_bucket["total"] += 1
        if rec.ok:
            domain_bucket["ok"] += 1
            status = str((rec.attachment or {}).get("status") or "")
            if status == "parsed":
                domain_bucket["parsed"] += 1
            elif status == "partial":
                domain_bucket["partial"] += 1
        else:
            domain_bucket["failed"] += 1
    return by_domain


def _build_report(
    *,
    base_url: str,
    tmp_dir: Path,
    records: List[UploadRecord],
    chat_ecom: Dict[str, Any],
    chat_non: Dict[str, Any],
    chat_unsupported: Dict[str, Any],
    ecom_hits: int,
    non_hits: int,
) -> Dict[str, Any]:
    uploads = []
    for rec in records:
        att = rec.attachment or {}
        uploads.append(
            {
                "domain": rec.domain,
                "label": rec.label,
                "file_name": rec.file_name,
                "http_status": rec.http_status,
                "ok": rec.ok,
                "elapsed_ms": rec.elapsed_ms,
                "error": rec.error,
                "attachment": {
                    "id": str(att.get("id") or ""),
                    "filename": str(att.get("filename") or ""),
                    "status": str(att.get("status") or ""),
                    "parser": str(att.get("parser") or ""),
                    "summary": str(att.get("summary") or ""),
                    "content_chars": int(att.get("content_chars") or 0),
                    "notices": att.get("notices") if isinstance(att.get("notices"), list) else [],
                    "vision_engine": str(att.get("vision_engine") or ""),
                    "vision_fallback_reason": str(att.get("vision_fallback_reason") or ""),
                    "vision_warning": str(att.get("vision_warning") or ""),
                    "vision_is_degraded": bool(att.get("vision_is_degraded") or False),
                    "layout_source": str(att.get("layout_source") or ""),
                    "stored": bool(att.get("stored") or False),
                },
            }
        )

    return {
        "base_url": base_url,
        "timestamp": int(time.time()),
        "tmp_dir": str(tmp_dir),
        "upload_summary": _summarize_upload(records),
        "uploads": uploads,
        "chat_checks": {
            "ecommerce": {
                **chat_ecom,
                "keyword_hits": ecom_hits,
                "keywords": ["SKU-A1", "ROI", "库存", "18"],
            },
            "non_ecommerce": {
                **chat_non,
                "keyword_hits": non_hits,
                "keywords": ["稻城亚丁", "番茄炖牛腩", "徒步"],
            },
            "unsupported": chat_unsupported,
        },
    }


def _write_reports(report: Dict[str, Any]) -> Tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(report.get("timestamp") or time.time())
    json_path = REPORT_DIR / f"attachment_multiformat_probe_{ts}.json"
    md_path = REPORT_DIR / f"attachment_multiformat_probe_{ts}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines: List[str] = []
    lines.append("# Attachment Multi-format Probe")
    lines.append("")
    lines.append(f"- base_url: `{report.get('base_url')}`")
    lines.append(f"- tmp_dir: `{report.get('tmp_dir')}`")
    lines.append("")
    lines.append("## Upload Summary")
    lines.append("")
    lines.append("| domain | total | ok | parsed | partial | failed |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    for domain, row in (report.get("upload_summary") or {}).items():
        lines.append(
            f"| {domain} | {int(row.get('total', 0))} | {int(row.get('ok', 0))} | "
            f"{int(row.get('parsed', 0))} | {int(row.get('partial', 0))} | {int(row.get('failed', 0))} |"
        )

    lines.append("")
    lines.append("## Upload Details")
    lines.append("")
    lines.append("| file | domain | http | status | parser | content_chars | vision_engine | notices |")
    lines.append("|---|---|---:|---|---|---:|---|---|")
    for row in report.get("uploads") or []:
        att = row.get("attachment") or {}
        notices = " / ".join(str(x) for x in (att.get("notices") or [])[:2])
        lines.append(
            f"| {row.get('file_name')} | {row.get('domain')} | {row.get('http_status')} | "
            f"{att.get('status', '')} | {att.get('parser', '')} | {att.get('content_chars', 0)} | "
            f"{att.get('vision_engine', '')} | {notices} |"
        )

    lines.append("")
    lines.append("## Chat Checks")
    lines.append("")
    chat_checks = report.get("chat_checks") or {}
    for key in ("ecommerce", "non_ecommerce", "unsupported"):
        row = chat_checks.get(key) or {}
        lines.append(f"### {key}")
        lines.append(f"- http_status: `{row.get('http_status')}`")
        lines.append(f"- ok: `{row.get('ok')}`")
        lines.append(f"- elapsed_ms: `{row.get('elapsed_ms')}`")
        if "keyword_hits" in row:
            lines.append(f"- keyword_hits: `{row.get('keyword_hits')}` / keywords={row.get('keywords')}")
        reply = str(row.get("reply") or "").strip()
        if reply:
            lines.append(f"- reply_preview: `{reply[:220].replace('`', ' ')}`")
        if row.get("error"):
            lines.append(f"- error: `{str(row.get('error'))[:220]}`")
        lines.append("")

    md_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    return json_path, md_path


def _parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe attachment upload + chat usage for multiple file formats")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Target API base URL, e.g. http://127.0.0.1:8100")
    parser.add_argument(
        "--images-only",
        action="store_true",
        help="Only upload image samples (png/jpeg/webp) for quick vision regression checks.",
    )
    parser.add_argument(
        "--skip-chat",
        action="store_true",
        help="Only validate attachment upload parsing and skip /api/chat checks.",
    )
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = _parse_args(argv)
    base_url = str(args.base_url or DEFAULT_BASE_URL).rstrip("/")
    ts = int(time.time())
    tmp_dir = TMP_ROOT / f"attachment_multiformat_probe_{ts}"
    tmp_dir.mkdir(parents=True, exist_ok=True)

    print(f"[INFO] target={base_url}")
    print(f"[INFO] tmp_dir={tmp_dir}")

    cases = _build_cases(tmp_dir, images_only=bool(args.images_only))
    print(f"[INFO] prepared_cases={len(cases)}")

    session = requests.Session()
    suffix = uuid.uuid4().hex[:8]
    email = f"attachment_probe_{suffix}@example.com"
    password = "AttachmentProbe123!"
    token = _register_or_login(session, base_url, email, password)
    if not token:
        print("[FAIL] unable to acquire token")
        return 2
    session.headers.update({"Authorization": f"Bearer {token}"})

    records: List[UploadRecord] = []
    for case in cases:
        rec = _upload_one(session, base_url, case)
        records.append(rec)
        att = rec.attachment or {}
        print(
            f"[{'PASS' if rec.ok else 'FAIL'}] {case.path.name:<28} "
            f"http={rec.http_status:<3} status={str(att.get('status') or ''):<7} "
            f"parser={str(att.get('parser') or ''):<24} chars={int(att.get('content_chars') or 0)}"
        )

    ecom_attachments = _pick_chat_attachments(records, "ecommerce", limit=5)
    non_attachments = _pick_chat_attachments(records, "non_ecommerce", limit=5)
    unsupported_attachments = _pick_chat_attachments(records, "unsupported", limit=2)

    chat_ecom: Dict[str, Any] = {}
    chat_non: Dict[str, Any] = {}
    chat_unsupported: Dict[str, Any] = {}
    ecom_hits = 0
    non_hits = 0

    if not args.skip_chat:
        cooldown_seconds = 15 if args.images_only else 65
        print(f"[INFO] cooldown_before_chat={cooldown_seconds}s (avoid /api/chat rate limit)")
        time.sleep(cooldown_seconds)

        if ecom_attachments:
            chat_ecom = _chat_once(
                session,
                base_url,
                message="请基于我上传的电商附件，给出3个优先动作，并明确提到 SKU-A1、ROI 和库存周转天数。",
                attachments=ecom_attachments,
                role="ops",
            )
            ecom_hits = _keyword_hit_count(str(chat_ecom.get("reply") or ""), ["SKU-A1", "ROI", "库存", "18"])

        if non_attachments:
            chat_non = _chat_once(
                session,
                base_url,
                message="请基于我上传的非电商附件，总结旅行与食谱要点，并明确提到 稻城亚丁、番茄炖牛腩、徒步。",
                attachments=non_attachments,
                role="ops",
            )
            non_hits = _keyword_hit_count(str(chat_non.get("reply") or ""), ["稻城亚丁", "番茄炖牛腩", "徒步"])

        if unsupported_attachments:
            chat_unsupported = _chat_once(
                session,
                base_url,
                message="请说明我上传的这批附件哪些可解析、哪些不可解析，并给出限制说明。",
                attachments=unsupported_attachments,
                role="ops",
            )

    report = _build_report(
        base_url=base_url,
        tmp_dir=tmp_dir,
        records=records,
        chat_ecom=chat_ecom,
        chat_non=chat_non,
        chat_unsupported=chat_unsupported,
        ecom_hits=ecom_hits,
        non_hits=non_hits,
    )
    json_path, md_path = _write_reports(report)

    print("")
    print(f"JSON report: {json_path}")
    print(f"MD report:   {md_path}")
    print(f"Ecom keyword hits: {ecom_hits}")
    print(f"Non-ecom keyword hits: {non_hits}")

    total = len(records)
    upload_ok = sum(1 for rec in records if rec.ok)
    if upload_ok < total:
        return 1
    if args.skip_chat:
        return 0
    chat_results = [chat_ecom, chat_non]
    if unsupported_attachments:
        chat_results.append(chat_unsupported)
    if any(not row.get("ok") for row in chat_results if row):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

