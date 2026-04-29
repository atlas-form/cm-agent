# Attachment + Doubao Acceptance Checklist

## 1. Purpose
This checklist validates end-to-end attachment processing and Doubao capability readiness for:
- E-commerce materials
- Non-ecommerce materials
- Supported and unsupported file formats
- Vision model stability and fallback behavior

## 2. Prerequisites
- Backend health endpoint returns 200: `/api/health`
- `server/.env` has valid `LLM_API_KEY` (or `DOUBAO_API_KEY`)
- Recommended image model is configured:
  - `DOUBAO_IMAGE_ENABLED=1`
  - `DOUBAO_IMAGE_MODEL=doubao-seed-1-6-vision-250815`
  - `DOUBAO_IMAGE_MODEL_CANDIDATES=...`

## 3. Baseline Capability Checks
Run from workspace root:

```powershell
python tools/doubao_capability_probe.py --timeout 30
python tools/discover_doubao_image_model.py --timeout 30 --max-candidates 8
```

Pass criteria:
- `chat_text` ok
- `chat_vision` ok
- `function_calling` ok
- `structured_json_object` ok
- `structured_json_schema` ok
- image model discovery returns at least one `ok=true` candidate

## 4. Multi-format Regression (Synthetic Mixed Files)
Run from workspace root:

```powershell
python tools/attachment_multiformat_probe.py --base-url http://127.0.0.1:8100
```

Pass criteria:
- Upload success ratio = 100%
- E-commerce and non-ecommerce domains are fully parsed for supported formats
- Unsupported formats return `status=partial` with clear notices
- Image files produce `vision_engine=doubao` under normal network conditions
- Chat check returns HTTP 200 and keyword hits are non-zero

## 5. Real User Directory Replay
Use your own local material directory:

```powershell
python tools/attachment_batch_replay.py --base-url http://127.0.0.1:8100 --input-dir D:\path\to\your\materials
```

Optional flags:

```powershell
python tools/attachment_batch_replay.py --input-dir D:\path\to\your\materials --max-files 50 --no-recursive
python tools/attachment_batch_replay.py --input-dir D:\path\to\your\materials --skip-chat
```

Pass criteria:
- Upload failures = 0
- `status_counts` and `parser_counts` align with expected file mix
- If chat replay is enabled, `/api/chat` returns HTTP 200 with non-empty reply preview

## 6. Failure Classification
- HTTP 413: file too large (check `UPLOAD_MAX_SIZE_MB`)
- HTTP 415 or partial with unsupported notice: format not deeply supported
- Image degraded (`vision_engine=ocr-fallback`): check Doubao model availability/network
- Chat 429: apply cooldown and retry

## 7. Artifacts and Reports
Generated reports are written to:
- `tools/reports/attachment_multiformat_probe_<ts>.json|md`
- `tools/reports/doubao_capability_probe_<ts>.json|md`
- `tools/reports/doubao_image_model_discovery_<ts>.json`
- `tools/reports/attachment_batch_replay_<ts>.json|md`

Keep reports for trend comparison across releases.
