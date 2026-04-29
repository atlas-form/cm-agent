# commerce-agents

电商多 Agent 协同平台（前后端分离，跨平台开发）。

## 20260402 统一计划入口（当前执行口径）
- 阅读顺序入口：`统一计划阅读索引_20260402_终版.md`
- 总体对照与结论：`统一交付核对报告_20260402_终版.md`
- 战略与长期方向：`统一开发主计划_20260402_终版.md`
- 需求与验收口径：`统一需求规格说明书_20260402_终版.md`
- 技术实施与模块改造：`统一技术实施计划_20260402_终版.md`
- 按周执行排期：`FR级实施推进表_20260402_按周执行版.md`
- 当前开工任务：`W1开工任务单_20260402.md`、`W2开工任务单_20260402.md`、`W3开工任务单_20260402.md`
## 目录边界（强制）
- `client/`：前端（Vite）
- `server/`：后端（FastAPI + Python）
- 约束：前端代码不放入 `server/`，后端 Python 代码不放入 `client/`。

## 开发原则（按当前项目执行）
1. 支持跨平台运行：Windows / macOS / Linux。
2. Python 开发必须使用 UV（`uv sync` / `uv run` / `uv run pytest`）。
3. 前后端分目录独立维护。
4. 前后端可各自独立 git 仓库。

## Git 初始化（前后端独立）
在项目根目录执行：

```powershell
git init
cd client; git init
cd ../server; git init
```

说明：当前仓库已完成 `client/.git` 与 `server/.git` 初始化。

## 环境要求
- Node.js 18+（建议 LTS）
- npm
- uv（必需）

## 推荐启动方式

### 方式 A：跨平台 Python 启动器（推荐）
在根目录执行：

```bash
python one_click_start.py
```

该脚本会执行：
1. 校验 `uv` / `node` / `npm`
2. `server/` 下 `uv sync`
3. `client/` 下依赖检查（缺失则 `npm install`）
4. 启动后端：`uv run python -m src.app`
5. 健康检查后启动前端：`npm run dev`

### 方式 B：系统脚本
- Windows：`one_click_start.bat`
- macOS/Linux：`bash one_click_start.sh`

说明：两套脚本均会从 `server/.env` 读取 `PORT`（默认 `8100`），并在后端健康检查通过后启动前端。

停止残留后端进程（防止 cmd/uvicorn 残留）：
- Windows：`one_click_stop.bat`
- PowerShell：`./one_click_stop.ps1`
- macOS/Linux：`bash one_click_stop.sh`

## 手动开发（推荐给调试）

### 后端（UV only）
```bash
cd server

# 首次可执行
cp .env.example .env   # Windows 可用 copy

uv python install 3.11
uv sync
uv run python -m src.app
```

后端健康检查：`http://localhost:8100/api/health`

### 前端
```bash
cd client
npm install
npm run dev
```

前端地址：
- `http://localhost:3000`
- `http://localhost:3000/pro.html`

### 前端代理目标（调试新旧后端）
前端开发服务器默认将 `/api` 代理到 `http://localhost:8100`。

如需切换到其它后端（例如 `8210`）：

```bash
# macOS/Linux
cd client
VITE_PROXY_TARGET=http://127.0.0.1:8210 npm run dev -- --host 127.0.0.1 --port 3000
```

```powershell
# Windows PowerShell
cd client
$env:VITE_PROXY_TARGET='http://127.0.0.1:8210'
npm run dev -- --host 127.0.0.1 --port 3000
```

### 前端全功能交互探测（UI Runtime Probe）
在前端启动后，可运行跨页面交互探测脚本（包含 team-chat / board / campaigns / history / knowledge / dashboard / agents / platform / alerts / packages / briefing / product/:id / workspace/:id）：

```bash
# macOS/Linux
cd client
UI_PROBE_BASE_URL=http://127.0.0.1:3000 npm run test:ui-runtime
```

```powershell
# Windows PowerShell
cd client
$env:UI_PROBE_BASE_URL='http://127.0.0.1:3000'
npm run test:ui-runtime
```

探测报告与截图输出到：
- `tools/reports/ui_runtime_probe_*.json`（结构化明细 + 路由评分）
- `tools/reports/ui_runtime_probe_summary_*.md`（可读的评分摘要）
- `tools/reports/ui_runtime_probe_shots_*`

可选严格模式（将控制台 error 也视为失败）：

```powershell
$env:UI_PROBE_STRICT='true'
npm run test:ui-runtime
```

可选账号角色探测（默认 `general`，可切到 `teacher` / `student`，并自动构造教学最小数据链路）：

```bash
# macOS/Linux
cd client
UI_PROBE_BASE_URL=http://127.0.0.1:3000 UI_PROBE_ACCOUNT_ROLE=teacher UI_PROBE_STRICT=true npm run test:ui-runtime
UI_PROBE_BASE_URL=http://127.0.0.1:3000 UI_PROBE_ACCOUNT_ROLE=student UI_PROBE_STRICT=true npm run test:ui-runtime
```

```powershell
# Windows PowerShell
cd client
$env:UI_PROBE_BASE_URL='http://127.0.0.1:3000'
$env:UI_PROBE_ACCOUNT_ROLE='teacher'
$env:UI_PROBE_STRICT='true'
npm run test:ui-runtime

$env:UI_PROBE_ACCOUNT_ROLE='student'
npm run test:ui-runtime
```

支持的环境变量：
- `UI_PROBE_BASE_URL`：前端地址（默认 `http://127.0.0.1:3000`）
- `UI_PROBE_STRICT`：严格模式（`true/false`）
- `UI_PROBE_ACCOUNT_ROLE`：探测账号角色（`general/teacher/student`）
- `UI_PROBE_EMAIL` + `UI_PROBE_PASSWORD`：指定登录账号；若角色与 `UI_PROBE_ACCOUNT_ROLE` 不一致，报告会记录 mismatch 警告

一键顺序跑三角色并生成汇总报告：

```bash
# macOS/Linux
cd client
UI_PROBE_BASE_URL=http://127.0.0.1:3000 UI_PROBE_STRICT=true npm run test:ui-runtime:all-roles
```

```powershell
# Windows PowerShell
cd client
$env:UI_PROBE_BASE_URL='http://127.0.0.1:3000'
$env:UI_PROBE_STRICT='true'
npm run test:ui-runtime:all-roles
```

汇总产物：
- `tools/reports/ui_runtime_probe_all_roles_*.json`
- `tools/reports/ui_runtime_probe_all_roles_*.md`

## 一键安装环境（不启动）
- Windows：`one_click_install.bat`
- macOS/Linux：`bash one_click_install.sh`

安装脚本会：
- 校验项目目录结构（必须有 `client/` 与 `server/`）
- 校验 `uv` / `node` / `npm`
- 按 `server/.python-version` 安装 Python（若存在）
- 同步后端依赖（`uv sync`）
- 安装前端依赖（`npm install`）

## 项目规范自检
在项目根目录执行：

```bash
python tools/verify_conventions.py
python tools/verify_conventions.py --json
```

该脚本会检查：
- 前后端目录边界是否存在（`client/` 与 `server/`）
- 后端 UV 元数据是否完整（`pyproject.toml` + `uv.lock`）
- 前后端是否已各自 `git init`（`client/.git` 与 `server/.git`）
- 启动/安装脚本是否符合 UV-only 关键规则

一键检查入口：
- Windows：`one_click_check.bat`
- macOS/Linux：`bash one_click_check.sh`

可选全功能探测参数：
- 仅规范检查（默认）：`python tools/verify_conventions.py`
- Windows：规范 + 运行态全功能探测：`one_click_check.bat --runtime-probe --base-url http://127.0.0.1:8210`
- Windows：规范 + Smoke 回归：`one_click_check.bat --smoke --base-url http://127.0.0.1:8210`
- Windows：规范 + RuntimeProbe + Smoke：`one_click_check.bat --all --base-url http://127.0.0.1:8210`
- macOS/Linux：规范 + 运行态全功能探测：`bash one_click_check.sh --runtime-probe --base-url http://127.0.0.1:8210`
- macOS/Linux：规范 + Smoke 回归：`bash one_click_check.sh --smoke --base-url http://127.0.0.1:8210`
- macOS/Linux：规范 + RuntimeProbe + Smoke：`bash one_click_check.sh --all --base-url http://127.0.0.1:8210`
- Windows：一键全量回归（自动拉起前后端 + RuntimeProbe + Smoke + UI三角色）：`one_click_check.bat --full-regression --backend-port 8233 --frontend-port 3000`
- macOS/Linux：一键全量回归（自动拉起前后端 + RuntimeProbe + Smoke + UI三角色）：`bash one_click_check.sh --full-regression --backend-port 8233 --frontend-port 3000`

全量回归脚本也可直接运行：
- `python tools/full_regression_suite.py --backend-port 8233 --frontend-port 3000`
- 可选参数：`--ui-retries 1`（默认 1，UI 全角色探针失败时自动重试一次）
- 输出报告：`tools/reports/full_regression_suite_*.json`、`tools/reports/full_regression_suite_*.md`


## CI 全量回归
- 工作流文件：`.github/workflows/full-regression.yml`
- 触发方式：`workflow_dispatch`、`push(main/master)`、`pull_request(main/master)`
- 运行内容：安装 `uv` + `node` 依赖，执行 `python tools/full_regression_suite.py --backend-port 8233 --frontend-port 3000`
- 工件归档：`tools/reports/**`

## 后端环境变量
文件：`server/.env`

模型接口配置说明（同事交接）：`introduction/MODEL_INTERFACE_SETUP.md`
豆包部署模板：`server/.env.doubao.example`

常用项：
- `PORT`（默认 `8100`）
- `HOST`（默认 `0.0.0.0`）
- `LLM_API_KEY`
- `LLM_API_URL`
- `LLM_MODEL`

## 常见问题
- 前端可打开但接口报错：确认后端是否运行在 `8100`。
- 端口冲突：修改 `server/.env` 中 `PORT`，并同步前端代理。
- Python 环境不一致：统一使用 `uv run ...`，不要直接用 `pip install` 改全局环境。





