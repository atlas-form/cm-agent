# 模型接口配置说明（同事交接版）

## 0. 推荐直接使用的模板文件

- 通用模板：`server/.env.example`
- 豆包专用模板（推荐同事直接复制）：`server/.env.doubao.example`

建议同事优先执行：

```bash
cd server
cp .env.doubao.example .env
# Windows: copy .env.doubao.example .env
```

## 1. 代码入口（以代码为准）

- 配置加载：`server/src/config.py`
- 模型调用：`server/src/llm_client.py`
- 模型列表/运行时切换接口：`server/src/routes/health.py`
- 前端模型下拉：`client/src/main.js`（`/api/llm/models` + `/api/llm/model`）

## 2. 必填最小配置（文本对话）

在 `server/.env` 中至少保证：

```env
LLM_PROVIDER=openai_compat
LLM_API_URL=https://ark.cn-beijing.volces.com/api/v3/chat/completions
LLM_API_KEY=<你的ARK_API_KEY>
LLM_MODEL=doubao-seed-2-0-pro-260215
LLM_TIMEOUT_SECONDS=120
```

说明：
- 系统真正用于主对话的是 `LLM_API_URL + LLM_API_KEY + LLM_MODEL`。
- `LLM_PROVIDER` 当前主用 `openai_compat`。

## 3. 豆包专属建议配置（推荐）

为确保前端模型切换和图像解析链路一致，建议同时配置：

```env
# 豆包专属（用于切换辅助和图像链路）
DOUBAO_API_URL=https://ark.cn-beijing.volces.com/api/v3/chat/completions
DOUBAO_API_KEY=<你的ARK_API_KEY>
DOUBAO_DEFAULT_MODEL=doubao-seed-2-0-pro-260215

# 图片理解（上传图片/附件解析）
DOUBAO_IMAGE_ENABLED=1
DOUBAO_IMAGE_MODEL=doubao-seed-1-6-vision-250815
DOUBAO_IMAGE_MODEL_CANDIDATES=doubao-seed-1-6-vision-250815,doubao-1-5-vision-pro-32k-250115,doubao-seed-2-0-pro-260215
DOUBAO_IMAGE_TIMEOUT_SECONDS=18
DOUBAO_IMAGE_RETRY_PER_MODEL=1
DOUBAO_IMAGE_TOTAL_BUDGET_SECONDS=90
DOUBAO_IMAGE_MAX_MODELS_PER_REQUEST=3
```

## 4. Fallback（主模型失败自动切换）

`llm_client.py` 按顺序使用最多 3 级 fallback：

```env
LLM_FALLBACK_MODEL=gpt-oss:120b
LLM_FALLBACK_URL=http://127.0.0.1:11434/v1/chat/completions
LLM_FALLBACK_KEY=sk-local

LLM_FALLBACK2_MODEL=qwen3.5:latest
LLM_FALLBACK2_URL=http://127.0.0.1:11434/v1/chat/completions
LLM_FALLBACK2_KEY=sk-local

LLM_FALLBACK3_MODEL=gpt-oss:20b
LLM_FALLBACK3_URL=http://127.0.0.1:11434/v1/chat/completions
LLM_FALLBACK3_KEY=sk-local
```

留空即不启用对应 fallback 级别。

## 5. 运行时模型接口（前端会调用）

- `GET /api/llm/models`：获取当前模型与候选模型
- `POST /api/llm/model`：运行时切换模型（仅当前进程生效，不改 `.env`）
- `GET /api/health`：检查服务/模型配置状态

示例：

```bash
curl -X POST http://127.0.0.1:8100/api/llm/model \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d '{"model":"doubao-seed-2-0-pro-260215"}'
```

## 6. 同事落地步骤（建议）

1. 复制 `server/.env.doubao.example` 为 `server/.env`。
2. 填写 `LLM_API_KEY`（建议同时填写 `DOUBAO_API_KEY`）。
3. 保持 `LLM_API_URL` 与 `DOUBAO_API_URL` 指向 Ark chat/completions。
4. 启动后访问 `http://127.0.0.1:8100/api/health`，确认 `llm` 不是 `not_configured`。
5. 前端登录后检查模型下拉（`/api/llm/models`）是否可见并可切换。

## 7. 常见误配与修正

- 误配：只配了 `DOUBAO_*`，没配 `LLM_*`
  - 现象：主对话提示未配置或主回复为空。
  - 修正：至少补齐 `LLM_API_URL / LLM_API_KEY / LLM_MODEL`。

- 误配：`LLM_API_URL` 填成根地址而非 `chat/completions`
  - 现象：调用报 404 或协议不匹配。
  - 修正：使用完整 chat-completions 地址。

- 误配：模型切换后预期持久化
  - 现象：重启后恢复旧模型。
  - 说明：`/api/llm/model` 是运行时切换；持久化要改 `.env`。

## 8. 安全约束（必须）

- 不要把真实 API Key 提交到 Git。
- 对外分享仓库时，只分享模板文件，不分享本地 `server/.env`。
