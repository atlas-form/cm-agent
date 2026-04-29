from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "tools" / "reports"
DEFAULT_CHAT_URL = "https://ark.cn-beijing.volces.com/api/v3/chat/completions"

KNOWN_EMBED_MODELS = [
    "doubao-embedding-large-text-250515",
    "doubao-embedding-large-text-240915",
    "doubao-embedding-text-240715",
    "doubao-embedding-text-240515",
    "doubao-embedding-vision-250615",
    "doubao-embedding-vision-250328",
    "doubao-embedding-vision-241215",
]


def _read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        k, v = raw.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _normalize_chat_url(raw: str) -> str:
    token = str(raw or "").strip() or DEFAULT_CHAT_URL
    return token.rstrip("/")


def _derive_models_url(chat_url: str) -> str:
    if chat_url.endswith("/chat/completions"):
        return chat_url[: -len("/chat/completions")] + "/models"
    return chat_url.rstrip("/") + "/models"


def _derive_embedding_endpoints(chat_url: str) -> list[str]:
    token = chat_url.rstrip("/")
    out: list[str] = []

    if token.endswith("/v1/chat/completions"):
        p = token[: -len("/v1/chat/completions")]
        out.extend([f"{p}/v1/embeddings", f"{p}/embeddings"])
    elif token.endswith("/chat/completions"):
        p = token[: -len("/chat/completions")]
        out.append(f"{p}/embeddings")
        if p.endswith("/api/v3"):
            out.append(f"{p[: -len('/api/v3')]}/v1/embeddings")
        else:
            out.append(f"{p}/v1/embeddings")
    else:
        out.extend([f"{token}/embeddings", f"{token}/v1/embeddings"])

    dedup: list[str] = []
    for x in out:
        if x not in dedup:
            dedup.append(x)
    return dedup


def _extract_error(body: Any) -> tuple[str, str]:
    if isinstance(body, dict):
        err = body.get("error")
        if isinstance(err, dict):
            return str(err.get("code") or ""), str(err.get("message") or "")[:280]
    return "", str(body)[:280]


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return {"raw": resp.text[:500]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover working embedding endpoint/model pair for current key")
    parser.add_argument("--chat-url", default="", help="Chat completion URL override")
    parser.add_argument("--timeout", type=int, default=20)
    parser.add_argument("--max-candidates", type=int, default=40)
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

    models_url = _derive_models_url(chat_url)
    endpoints = _derive_embedding_endpoints(chat_url)

    headers = {"Authorization": f"Bearer {api_key}"}

    models: list[dict[str, Any]] = []
    with httpx.Client(timeout=args.timeout, trust_env=False) as client:
        resp = client.get(models_url, headers=headers)
        body = _safe_json(resp)
        if int(resp.status_code or 0) == 200 and isinstance(body, dict) and isinstance(body.get("data"), list):
            models = [m for m in body.get("data") if isinstance(m, dict)]

    from_models = [
        str(m.get("id") or "")
        for m in models
        if "embedding" in str(m.get("id") or "").lower() and "shutdown" not in str(m.get("status") or "").lower()
    ]

    candidates: list[str] = []
    for item in [
        env.get("DOUBAO_EMBEDDING_MODEL", ""),
        env.get("LLM_EMBEDDING_MODEL", ""),
        *KNOWN_EMBED_MODELS,
        *from_models,
    ]:
        token = str(item or "").strip()
        if not token or token in candidates:
            continue
        candidates.append(token)

    candidates = candidates[: max(1, int(args.max_candidates))]

    matrix: list[dict[str, Any]] = []
    first_success: dict[str, str] | None = None

    with httpx.Client(timeout=args.timeout, trust_env=False) as client:
        for endpoint in endpoints:
            for model in candidates:
                payload = {"model": model, "input": ["电商运营诊断", "库存周转"]}
                h = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
                t0 = time.time()
                try:
                    resp = client.post(endpoint, headers=h, json=payload)
                    elapsed = int((time.time() - t0) * 1000)
                    body = _safe_json(resp)
                    status = int(resp.status_code or 0)
                except Exception as exc:  # noqa: BLE001
                    elapsed = int((time.time() - t0) * 1000)
                    status = 0
                    body = {"error": {"code": "client_exception", "message": str(exc)[:220]}}

                ok = False
                dim = 0
                if status == 200 and isinstance(body, dict):
                    rows = body.get("data") if isinstance(body.get("data"), list) else []
                    if rows and isinstance(rows[0], dict) and isinstance(rows[0].get("embedding"), list):
                        dim = len(rows[0].get("embedding") or [])
                        ok = dim > 0

                code, msg = _extract_error(body)
                row = {
                    "endpoint": endpoint,
                    "model": model,
                    "ok": ok,
                    "status": status,
                    "elapsed_ms": elapsed,
                    "dim": dim,
                    "error_code": code,
                    "error_message": msg,
                }
                matrix.append(row)

                if ok and first_success is None:
                    first_success = {"endpoint": endpoint, "model": model}

    summary = {
        "total": len(matrix),
        "passed": sum(1 for x in matrix if x.get("ok")),
        "failed": sum(1 for x in matrix if not x.get("ok")),
    }

    report = {
        "timestamp": int(time.time()),
        "chat_url": chat_url,
        "models_url": models_url,
        "endpoint_candidates": endpoints,
        "candidate_count": len(candidates),
        "candidate_preview": candidates[:20],
        "models_count": len(models),
        "first_success": first_success or {},
        "summary": summary,
        "matrix": matrix,
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    ts = report["timestamp"]
    json_path = REPORT_DIR / f"doubao_embedding_discovery_probe_{ts}.json"
    md_path = REPORT_DIR / f"doubao_embedding_discovery_probe_{ts}.md"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    lines: list[str] = []
    lines.append("# Doubao Embedding Discovery Probe")
    lines.append("")
    lines.append(f"- chat_url: `{chat_url}`")
    lines.append(f"- models_url: `{models_url}`")
    lines.append(f"- endpoints: `{', '.join(endpoints)}`")
    lines.append(f"- candidates: `{len(candidates)}`")
    lines.append(f"- summary: `{summary}`")
    lines.append(f"- first_success: `{first_success or {}}`")
    lines.append("")
    lines.append("## Top Results")
    lines.append("")
    lines.append("| endpoint | model | ok | status | dim | error_code |")
    lines.append("|---|---|---:|---:|---:|---|")
    for row in matrix[:80]:
        lines.append(
            f"| {row['endpoint']} | {row['model']} | {'Y' if row['ok'] else 'N'} | {row['status']} | {row['dim']} | {row['error_code']} |"
        )
    md_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "ok": bool(first_success),
                "first_success": first_success or {},
                "summary": summary,
                "report_json": str(json_path),
                "report_md": str(md_path),
            },
            ensure_ascii=False,
        )
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
