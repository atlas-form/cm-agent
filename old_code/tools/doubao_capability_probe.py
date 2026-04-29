from __future__ import annotations

import argparse
import json
import os
import struct
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "tools" / "reports"
DEFAULT_CHAT_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"
DEFAULT_IMAGE_URL = "https://ark-project.tos-cn-beijing.volces.com/images/view.jpeg"


@dataclass
class CapabilityResult:
    capability: str
    endpoint: str
    model: str
    ok: bool
    status: int
    elapsed_ms: int
    detail: str
    error_code: str
    error_message: str


def _tiny_png_bytes() -> bytes:
    sig = b"\x89PNG\r\n\x1a\n"

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw = b"\x00\xff\xff\xff"
    idat = chunk(b"IDAT", zlib.compress(raw))
    iend = chunk(b"IEND", b"")
    return sig + ihdr + idat + iend


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _normalize_chat_url(raw: str) -> str:
    token = str(raw or "").strip() or DEFAULT_CHAT_URL
    if token.endswith("/"):
        token = token[:-1]
    if token.endswith("/chat/completions"):
        return token
    if token.endswith("/v3"):
        return token + "/chat/completions"
    return token


def _derive_endpoint(chat_url: str, suffix: str) -> str:
    base = chat_url
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    return base + suffix


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text[:500]}


def _extract_error(body: Any) -> tuple[str, str]:
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return str(err.get("code") or ""), str(err.get("message") or "")[:320]
    return "", str(body)[:320]


def _call_json(*, method: str, url: str, api_key: str, payload: dict[str, Any] | None = None, timeout_seconds: int = 35) -> tuple[int, int, Any]:
    headers = {"Authorization": f"Bearer {api_key}"}
    if payload is not None:
        headers["Content-Type"] = "application/json"

    t0 = time.time()
    with httpx.Client(timeout=timeout_seconds, trust_env=False) as client:
        if method.upper() == "GET":
            resp = client.get(url, headers=headers)
        else:
            resp = client.post(url, headers=headers, json=payload)
    elapsed_ms = int((time.time() - t0) * 1000)
    return int(resp.status_code or 0), elapsed_ms, _safe_json(resp)


def _call_multipart(*, url: str, api_key: str, files: dict[str, Any], data: dict[str, Any], timeout_seconds: int = 35) -> tuple[int, int, Any]:
    headers = {"Authorization": f"Bearer {api_key}"}
    t0 = time.time()
    with httpx.Client(timeout=timeout_seconds, trust_env=False) as client:
        resp = client.post(url, headers=headers, files=files, data=data)
    elapsed_ms = int((time.time() - t0) * 1000)
    return int(resp.status_code or 0), elapsed_ms, _safe_json(resp)


def _first_model(
    models: list[dict[str, Any]],
    *,
    preferred: list[str] | None = None,
    must_include: list[str] | None = None,
    forbid_include: list[str] | None = None,
) -> str:
    preferred = preferred or []
    must_include = must_include or []
    forbid_include = forbid_include or []

    id_map = {str(m.get("id") or "").strip(): m for m in models if isinstance(m, dict)}
    for item in preferred:
        mid = str(item or "").strip()
        if not mid or mid not in id_map:
            continue
        st = str(id_map[mid].get("status") or "").lower()
        if "shutdown" in st:
            continue
        return mid

    for m in models:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or "").strip()
        if not mid:
            continue
        st = str(m.get("status") or "").lower()
        if "shutdown" in st:
            continue
        low = mid.lower()
        if not all(tok.lower() in low for tok in must_include):
            continue
        if any(tok.lower() in low for tok in forbid_include):
            continue
        return mid
    return ""


def _first_structured_output_model(models: list[dict[str, Any]]) -> str:
    preferred = ["doubao-seed-1-6-flash-250615", "doubao-seed-1-6-250615", "doubao-seed-1-6-flash-250828"]
    id_map = {str(m.get("id") or "").strip(): m for m in models if isinstance(m, dict)}

    for mid in preferred:
        row = id_map.get(mid)
        if not row:
            continue
        st = str(row.get("status") or "").lower()
        if "shutdown" in st:
            continue
        return mid

    for row in models:
        if not isinstance(row, dict):
            continue
        st = str(row.get("status") or "").lower()
        if "shutdown" in st:
            continue
        mid = str(row.get("id") or "").strip()
        feats = row.get("features") if isinstance(row.get("features"), dict) else {}
        so = feats.get("structured_outputs") if isinstance(feats.get("structured_outputs"), dict) else {}
        if bool(so.get("json_object") or so.get("json_schema")):
            return mid
    return ""


def _build_md_report(*, report: dict[str, Any], md_path: Path) -> None:
    lines: list[str] = []
    lines.append("# Doubao API Key Capability Probe")
    lines.append("")
    lines.append(f"- timestamp: `{report.get('timestamp')}`")
    lines.append(f"- chat_url: `{report.get('chat_url')}`")
    lines.append(f"- models_api: `{report.get('models_api')}`")
    lines.append(f"- model_count: `{report.get('model_count')}`")
    lines.append("")

    lines.append("## Model Picks")
    lines.append("")
    picks = report.get("picked_models") or {}
    for k, v in picks.items():
        lines.append(f"- {k}: `{v}`")
    lines.append("")

    lines.append("## Capability Matrix")
    lines.append("")
    lines.append("| capability | ok | status | model | endpoint | elapsed_ms | detail | error |")
    lines.append("|---|---|---:|---|---|---:|---|---|")
    for row in report.get("results") or []:
        err = str(row.get("error_code") or "")
        if row.get("error_message"):
            err = f"{err}:{str(row.get('error_message'))[:90]}" if err else str(row.get("error_message"))[:90]
        lines.append(
            f"| {row.get('capability')} | {row.get('ok')} | {row.get('status')} | {row.get('model')} | "
            f"{row.get('endpoint')} | {row.get('elapsed_ms')} | {str(row.get('detail') or '')[:100]} | {err} |"
        )

    lines.append("")
    lines.append("## Top Available Models (first 50)")
    lines.append("")
    lines.append("| id | status | name |")
    lines.append("|---|---|---|")
    for row in (report.get("models") or [])[:50]:
        lines.append(f"| {row.get('id')} | {row.get('status')} | {row.get('name')} |")

    md_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe what capabilities are callable with a given Doubao API key.")
    parser.add_argument("--chat-url", default="", help="Ark chat completions URL")
    parser.add_argument("--timeout", type=int, default=35, help="HTTP timeout seconds")
    parser.add_argument("--image-url", default=DEFAULT_IMAGE_URL, help="Image URL for vision probe")
    args = parser.parse_args()

    env = _read_env_file(ROOT / "server" / ".env")
    api_key = (
        env.get("DOUBAO_API_KEY")
        or env.get("LLM_API_KEY")
        or os.getenv("DOUBAO_API_KEY")
        or os.getenv("LLM_API_KEY")
        or ""
    ).strip()
    if not api_key:
        print(json.dumps({"ok": False, "reason": "missing_api_key"}, ensure_ascii=False))
        return 2

    chat_url = _normalize_chat_url(
        args.chat_url
        or env.get("DOUBAO_API_URL")
        or env.get("LLM_API_URL")
        or os.getenv("DOUBAO_API_URL")
        or os.getenv("LLM_API_URL")
        or DEFAULT_CHAT_URL
    )

    models_url = _derive_endpoint(chat_url, "/models")
    embeddings_url = _derive_endpoint(chat_url, "/embeddings")
    image_gen_url = _derive_endpoint(chat_url, "/images/generations")
    files_url = _derive_endpoint(chat_url, "/files")
    content_tasks_url = _derive_endpoint(chat_url, "/contents/generations/tasks")
    legacy_video_url = _derive_endpoint(chat_url, "/video/generations")

    results: list[CapabilityResult] = []

    # 1) models list
    status, elapsed, body = _call_json(method="GET", url=models_url, api_key=api_key, timeout_seconds=args.timeout)
    models: list[dict[str, Any]] = []
    if status == 200 and isinstance(body, dict) and isinstance(body.get("data"), list):
        models = [x for x in body.get("data") if isinstance(x, dict)]
        results.append(CapabilityResult("models_list", models_url, "", True, status, elapsed, f"models={len(models)}", "", ""))
    else:
        code, msg = _extract_error(body)
        results.append(CapabilityResult("models_list", models_url, "", False, status, elapsed, "", code, msg))

    models = sorted(models, key=lambda x: str(x.get("id") or ""))

    text_model = _first_model(
        models,
        preferred=[
            str(env.get("LLM_MODEL") or ""),
            "doubao-seed-2-0-pro-260215",
            "doubao-seed-1-6-251015",
            "doubao-seed-1-6-250615",
        ],
        forbid_include=["embedding", "seedream", "seedance", "i2i", "t2i", "i2v", "t2v"],
    )
    vision_model = _first_model(
        models,
        preferred=[
            str(env.get("DOUBAO_IMAGE_MODEL") or ""),
            "doubao-seed-1-6-vision-250815",
            "doubao-1-5-vision-pro-32k-250115",
            "doubao-seed-2-0-pro-260215",
        ],
    )
    structured_model = _first_structured_output_model(models)
    embedding_candidates_from_models = [
        str(m.get("id") or "")
        for m in models
        if isinstance(m, dict) and "embedding" in str(m.get("id") or "").lower() and "shutdown" not in str(m.get("status") or "").lower()
    ]
    embedding_candidates: list[str] = []
    for candidate in [
        str(env.get("DOUBAO_EMBEDDING_MODEL") or ""),
        str(env.get("LLM_EMBEDDING_MODEL") or ""),
        "doubao-embedding-large-text-250515",
        "doubao-embedding-large-text-240915",
        "doubao-embedding-text-240715",
        "doubao-embedding-text-240515",
        "doubao-embedding-vision-250615",
        "doubao-embedding-vision-250328",
        "doubao-embedding-vision-241215",
        *embedding_candidates_from_models,
    ]:
        token = str(candidate or "").strip()
        if not token or token in embedding_candidates:
            continue
        embedding_candidates.append(token)
    image_model = _first_model(models, must_include=["seedream"])
    video_model = _first_model(models, must_include=["seedance"])

    # 2) text chat basic
    if text_model:
        payload = {
            "model": text_model,
            "temperature": 0.1,
            "max_tokens": 200,
            "messages": [{"role": "user", "content": "请用一句话说明你已可用。"}],
        }
        status, elapsed, body = _call_json(method="POST", url=chat_url, api_key=api_key, payload=payload, timeout_seconds=args.timeout)
        if status == 200:
            txt = str(((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "") if isinstance(body, dict) else ""
            results.append(CapabilityResult("chat_text", chat_url, text_model, True, status, elapsed, txt[:120], "", ""))
        else:
            code, msg = _extract_error(body)
            results.append(CapabilityResult("chat_text", chat_url, text_model, False, status, elapsed, "", code, msg))

    # 3) vision chat
    if vision_model:
        payload = {
            "model": vision_model,
            "temperature": 0.1,
            "max_tokens": 200,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "请20字内描述图像主体。"},
                        {"type": "image_url", "image_url": {"url": str(args.image_url)}},
                    ],
                }
            ],
        }
        status, elapsed, body = _call_json(method="POST", url=chat_url, api_key=api_key, payload=payload, timeout_seconds=args.timeout)
        if status == 200:
            txt = str(((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "") if isinstance(body, dict) else ""
            results.append(CapabilityResult("chat_vision", chat_url, vision_model, True, status, elapsed, txt[:120], "", ""))
        else:
            code, msg = _extract_error(body)
            results.append(CapabilityResult("chat_vision", chat_url, vision_model, False, status, elapsed, "", code, msg))

    # 4) tool calling
    if text_model:
        payload = {
            "model": text_model,
            "temperature": 0,
            "max_tokens": 200,
            "messages": [{"role": "user", "content": "请调用 add 工具计算 19 + 23，并只调用工具。"}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": "add",
                        "description": "Add two integers",
                        "parameters": {
                            "type": "object",
                            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                            "required": ["a", "b"],
                        },
                    },
                }
            ],
            "tool_choice": "auto",
        }
        status, elapsed, body = _call_json(method="POST", url=chat_url, api_key=api_key, payload=payload, timeout_seconds=args.timeout)
        if status == 200 and isinstance(body, dict):
            msg_obj = ((body.get("choices") or [{}])[0].get("message") or {})
            tool_calls = msg_obj.get("tool_calls") if isinstance(msg_obj, dict) else None
            has_tool = isinstance(tool_calls, list) and len(tool_calls) > 0
            detail = f"tool_calls={len(tool_calls) if isinstance(tool_calls, list) else 0}"
            results.append(CapabilityResult("function_calling", chat_url, text_model, has_tool, status, elapsed, detail, "", ""))
        else:
            code, msg = _extract_error(body)
            results.append(CapabilityResult("function_calling", chat_url, text_model, False, status, elapsed, "", code, msg))

    # 5) structured output: json_object
    if structured_model:
        payload = {
            "model": structured_model,
            "temperature": 0,
            "max_tokens": 200,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": "返回JSON对象，字段: ok(boolean)=true, score(integer)=7"}],
        }
        status, elapsed, body = _call_json(method="POST", url=chat_url, api_key=api_key, payload=payload, timeout_seconds=args.timeout)
        if status == 200 and isinstance(body, dict):
            txt = str(((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
            ok = False
            try:
                parsed = json.loads(txt)
                ok = isinstance(parsed, dict)
            except Exception:
                ok = False
            results.append(CapabilityResult("structured_json_object", chat_url, structured_model, ok, status, elapsed, txt[:120], "", ""))
        else:
            code, msg = _extract_error(body)
            results.append(CapabilityResult("structured_json_object", chat_url, structured_model, False, status, elapsed, "", code, msg))

    # 6) structured output: json_schema
    if structured_model:
        payload = {
            "model": structured_model,
            "temperature": 0,
            "max_tokens": 200,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "topic_score",
                    "schema": {
                        "type": "object",
                        "properties": {
                            "topic": {"type": "string"},
                            "score": {"type": "integer"},
                        },
                        "required": ["topic", "score"],
                        "additionalProperties": False,
                    },
                },
            },
            "messages": [{"role": "user", "content": "输出topic=demo, score=9"}],
        }
        status, elapsed, body = _call_json(method="POST", url=chat_url, api_key=api_key, payload=payload, timeout_seconds=args.timeout)
        if status == 200 and isinstance(body, dict):
            txt = str(((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "")
            ok = False
            try:
                parsed = json.loads(txt)
                ok = isinstance(parsed, dict) and isinstance(parsed.get("topic"), str) and isinstance(parsed.get("score"), int)
            except Exception:
                ok = False
            results.append(CapabilityResult("structured_json_schema", chat_url, structured_model, ok, status, elapsed, txt[:120], "", ""))
        else:
            code, msg = _extract_error(body)
            results.append(CapabilityResult("structured_json_schema", chat_url, structured_model, False, status, elapsed, "", code, msg))
    # 7) embeddings: try candidate models until first success
    embedding_ok = False
    tried_embedding: list[str] = []
    last_failure_status = 0
    last_failure_code = ""
    last_failure_msg = ""
    max_embed_candidates = max(12, min(32, len(embedding_candidates)))

    for candidate in embedding_candidates[:max_embed_candidates]:
        tried_embedding.append(candidate)
        payload = {"model": candidate, "input": ["电商运营诊断", "库存周转"]}
        status, elapsed, body = _call_json(method="POST", url=embeddings_url, api_key=api_key, payload=payload, timeout_seconds=args.timeout)

        if status == 200 and isinstance(body, dict):
            rows = body.get("data") if isinstance(body.get("data"), list) else []
            dim = 0
            if rows and isinstance(rows[0], dict) and isinstance(rows[0].get("embedding"), list):
                dim = len(rows[0].get("embedding") or [])
            ok = len(rows) > 0 and dim > 0
            if ok:
                results.append(CapabilityResult("embeddings", embeddings_url, candidate, True, status, elapsed, f"vectors={len(rows)}, dim={dim}", "", ""))
                embedding_ok = True
                break

        code, msg = _extract_error(body)
        last_failure_status = int(status or 0)
        last_failure_code = code
        last_failure_msg = msg

        code_low = str(code or "").lower()
        msg_low = str(msg or "").lower()
        invalid_model = (
            "invalidparameter" in code_low
            or "invalidendpointormodel.notfound" in code_low
            or "requested model" in msg_low
            or "does not exist" in msg_low
            or ("model" in msg_low and int(status or 0) in {400, 404})
        )
        # 模型无效时继续尝试其他候选
        if invalid_model:
            continue
        # 其他错误（鉴权/网络/端点异常）直接结束，避免无效重试
        break

    if not embedding_ok:
        if tried_embedding:
            candidate = tried_embedding[-1]
            detail = f"tried={len(tried_embedding)} top={','.join(tried_embedding[:6])}"
            results.append(
                CapabilityResult(
                    "embeddings",
                    embeddings_url,
                    candidate,
                    False,
                    last_failure_status,
                    0,
                    detail,
                    last_failure_code,
                    last_failure_msg,
                )
            )
        else:
            results.append(CapabilityResult("embeddings", embeddings_url, "", False, 0, 0, "no embedding model candidates", "", ""))

    # 8) image generationif image_model:
        payload = {
            "model": image_model,
            "prompt": "A clean ecommerce dashboard illustration in orange theme",
            "size": "1024x1024",
        }
        status, elapsed, body = _call_json(method="POST", url=image_gen_url, api_key=api_key, payload=payload, timeout_seconds=max(args.timeout, 60))
        if status == 200 and isinstance(body, dict):
            data = body.get("data") if isinstance(body.get("data"), list) else []
            ok = len(data) > 0
            detail = ""
            if ok and isinstance(data[0], dict):
                if data[0].get("url"):
                    detail = "url"
                elif data[0].get("b64_json"):
                    detail = "b64_json"
                else:
                    detail = "data_present"
            results.append(CapabilityResult("image_generation", image_gen_url, image_model, ok, status, elapsed, detail, "", ""))
        else:
            code, msg = _extract_error(body)
            results.append(CapabilityResult("image_generation", image_gen_url, image_model, False, status, elapsed, "", code, msg))
    else:
        results.append(CapabilityResult("image_generation", image_gen_url, "", False, 0, 0, "no seedream model found in /models", "", ""))

    # 9) file API upload/list/get
    status, elapsed, body = _call_json(method="GET", url=files_url, api_key=api_key, timeout_seconds=args.timeout)
    if status == 200:
        detail = "list_ok"
        if body is None:
            detail = "list_ok(null)"
        results.append(CapabilityResult("files_list", files_url, "", True, status, elapsed, detail, "", ""))
    else:
        code, msg = _extract_error(body)
        results.append(CapabilityResult("files_list", files_url, "", False, status, elapsed, "", code, msg))

    file_id = ""
    png_bytes = _tiny_png_bytes()
    status, elapsed, body = _call_multipart(
        url=files_url,
        api_key=api_key,
        files={"file": ("probe.png", png_bytes, "image/png")},
        data={"purpose": "user_data"},
        timeout_seconds=args.timeout,
    )
    if status == 200 and isinstance(body, dict):
        file_id = str(body.get("id") or "")
        results.append(CapabilityResult("files_upload_user_data", files_url, "", True, status, elapsed, f"file_id={file_id}", "", ""))
    else:
        code, msg = _extract_error(body)
        results.append(CapabilityResult("files_upload_user_data", files_url, "", False, status, elapsed, "", code, msg))

    if file_id:
        get_url = files_url.rstrip("/") + f"/{file_id}"
        status, elapsed, body = _call_json(method="GET", url=get_url, api_key=api_key, timeout_seconds=args.timeout)
        if status == 200 and isinstance(body, dict):
            fstatus = str(body.get("status") or "")
            results.append(CapabilityResult("files_get", get_url, "", True, status, elapsed, f"status={fstatus}", "", ""))
        else:
            code, msg = _extract_error(body)
            results.append(CapabilityResult("files_get", get_url, "", False, status, elapsed, "", code, msg))
    # 10) legacy openai-like video endpoint (optional for modern Ark accounts)
    if video_model:
        payload = {"model": video_model, "prompt": "A cat walks in office", "duration": 5}
        status, elapsed, body = _call_json(method="POST", url=legacy_video_url, api_key=api_key, payload=payload, timeout_seconds=args.timeout)
        if status in {200, 202}:
            results.append(CapabilityResult("video_generation_legacy", legacy_video_url, video_model, True, status, elapsed, "accepted", "", ""))
        elif status in {404, 405}:
            results.append(CapabilityResult("video_generation_legacy", legacy_video_url, video_model, True, status, elapsed, "not_supported_expected", "", ""))
        else:
            code, msg = _extract_error(body)
            results.append(CapabilityResult("video_generation_legacy", legacy_video_url, video_model, False, status, elapsed, "", code, msg))

    # 11) content generation tasks API for video (actual route)content_task_id = ""
    if video_model:
        payload = {
            "model": video_model,
            "content": [
                {"type": "text", "text": "A blue-green bird transforms into a human --rs 720p --dur 5 --cf false"},
                {"type": "image_url", "image_url": {"url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/seelite_first_frame.png"}, "role": "first_frame"},
                {"type": "image_url", "image_url": {"url": "https://ark-project.tos-cn-beijing.volces.com/doc_image/seelite_last_frame.png"}, "role": "last_frame"},
            ],
        }
        status, elapsed, body = _call_json(method="POST", url=content_tasks_url, api_key=api_key, payload=payload, timeout_seconds=max(args.timeout, 60))
        if status in {200, 202} and isinstance(body, dict):
            content_task_id = str(body.get("id") or body.get("task_id") or "")
            results.append(CapabilityResult("video_generation_task_create", content_tasks_url, video_model, bool(content_task_id), status, elapsed, f"task_id={content_task_id}", "", ""))
        else:
            code, msg = _extract_error(body)
            results.append(CapabilityResult("video_generation_task_create", content_tasks_url, video_model, False, status, elapsed, "", code, msg))

    if content_task_id:
        poll_url = content_tasks_url.rstrip("/") + f"/{content_task_id}"
        status, elapsed, body = _call_json(method="GET", url=poll_url, api_key=api_key, timeout_seconds=args.timeout)
        if status == 200 and isinstance(body, dict):
            task_status = str(body.get("status") or "")
            results.append(CapabilityResult("video_generation_task_poll", poll_url, video_model, True, status, elapsed, f"status={task_status}", "", ""))
        else:
            code, msg = _extract_error(body)
            results.append(CapabilityResult("video_generation_task_poll", poll_url, video_model, False, status, elapsed, "", code, msg))

    report = {
        "timestamp": int(time.time()),
        "chat_url": chat_url,
        "models_api": models_url,
        "model_count": len(models),
        "picked_models": {
            "text_model": text_model,
            "vision_model": vision_model,
            "structured_output_model": structured_model,
            "embedding_candidates_top": embedding_candidates[:8],
            "image_generation_model": image_model,
            "video_generation_model": video_model,
        },
        "results": [
            {
                "capability": r.capability,
                "endpoint": r.endpoint,
                "model": r.model,
                "ok": r.ok,
                "status": r.status,
                "elapsed_ms": r.elapsed_ms,
                "detail": r.detail,
                "error_code": r.error_code,
                "error_message": r.error_message,
            }
            for r in results
        ],
        "models": [
            {
                "id": str(m.get("id") or ""),
                "name": str(m.get("name") or ""),
                "status": str(m.get("status") or ""),
                "features": m.get("features") if isinstance(m.get("features"), dict) else {},
            }
            for m in models
        ],
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(report["timestamp"])
    json_path = REPORT_DIR / f"doubao_capability_probe_{ts}.json"
    md_path = REPORT_DIR / f"doubao_capability_probe_{ts}.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _build_md_report(report=report, md_path=md_path)

    ok_count = sum(1 for r in report["results"] if r.get("ok"))
    total = len(report["results"])
    print(
        json.dumps(
            {
                "ok": ok_count > 0,
                "ok_count": ok_count,
                "total": total,
                "report_json": str(json_path),
                "report_md": str(md_path),
                "picked_models": report["picked_models"],
            },
            ensure_ascii=False,
        )
    )

    return 0 if ok_count > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

