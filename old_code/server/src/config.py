"""
配置中心 — 15个功能开关（3层级）+ LLM配置 + 跨平台路径。

所有路径使用 pathlib.Path，确保 macOS / Windows 兼容。
所有配置通过环境变量覆盖，默认值适合开发环境。

功能开关层级:
  Core     — 始终开启，不可关闭（质量、信任、学习、告警、指标）
  Enhanced — 默认开启，可按需关闭（工具调用、知识规则、人格、交接、主动）
  Advanced — 可选功能，部分默认关闭（工作区引擎、产品生命周期、自动驾驶、智能、外部数据）
"""

from __future__ import annotations

import os
from pathlib import Path


def _load_dotenv() -> None:
    """加载 .env 文件，不覆盖已有环境变量。无需 python-dotenv 依赖。"""
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if not env_path.exists():
        return
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val


_load_dotenv()


def _normalize_auth_mode(raw: str) -> str:
    mode = (raw or "").strip().lower()
    if mode in {"dev", "development"}:
        return "dev"
    if mode in {"staging", "stage", "test"}:
        return "staging"
    if mode in {"prod", "production"}:
        return "prod"
    return "dev"

# ═══════════════════════════════════════════════════════════════════════════
# 路径配置（跨平台）
# ═══════════════════════════════════════════════════════════════════════════

PROJECT_ROOT = Path(__file__).resolve().parent.parent          # server/
DATA_DIR = PROJECT_ROOT / "data"
CONFIG_DIR = PROJECT_ROOT / "config"
DB_PATH = Path(os.getenv("DB_PATH", str(DATA_DIR / "v4.db")))
UPLOAD_DIR = DATA_DIR / "uploads"
PRODUCTS_DIR = DATA_DIR / "products"

# ═══════════════════════════════════════════════════════════════════════════
# LLM 配置
# ═══════════════════════════════════════════════════════════════════════════

LLM_PROVIDER = os.getenv("LLM_PROVIDER", "openai_compat")          # openai_compat | anthropic
LLM_API_URL = os.getenv("LLM_API_URL", "https://api.openai.com/v1/chat/completions")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")
LLM_TIMEOUT_SECONDS = int(os.getenv("LLM_TIMEOUT_SECONDS", "30"))

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")

ENABLE_MULTI_MODEL = os.getenv("ENABLE_MULTI_MODEL", "0") == "1"

# ── 豆包 (VolcEngine Ark) 专属配置（从 .env 读取，供参考/监控用）──
# 依据火山方舟文档：Base URL 为 https://ark.cn-beijing.volces.com/api/v3
# 本项目使用 openai_compat 的 /chat/completions 调用格式，因此默认补全到该路径。
DOUBAO_API_KEY = os.getenv("DOUBAO_API_KEY", os.getenv("LLM_API_KEY", ""))
DOUBAO_API_URL = os.getenv(
    "DOUBAO_API_URL",
    "https://ark.cn-beijing.volces.com/api/v3/chat/completions",
)
DOUBAO_DEFAULT_MODEL = os.getenv("DOUBAO_DEFAULT_MODEL", "doubao-seed-2-0-pro-260215")

# ── 多级 Fallback 链 — 主模型不可用时依次切换 ────────────────────────
# Fallback 1 — 通常为本地 GPT-OSS 大模型
LLM_FALLBACK_MODEL = os.getenv("LLM_FALLBACK_MODEL", "")
LLM_FALLBACK_URL = os.getenv("LLM_FALLBACK_URL", "")
LLM_FALLBACK_KEY = os.getenv("LLM_FALLBACK_KEY", "")

# Fallback 2 — 本地中等规模模型
LLM_FALLBACK2_MODEL = os.getenv("LLM_FALLBACK2_MODEL", "")
LLM_FALLBACK2_URL = os.getenv("LLM_FALLBACK2_URL", "")
LLM_FALLBACK2_KEY = os.getenv("LLM_FALLBACK2_KEY", "")

# Fallback 3 — 轻量保底模型
LLM_FALLBACK3_MODEL = os.getenv("LLM_FALLBACK3_MODEL", "")
LLM_FALLBACK3_URL = os.getenv("LLM_FALLBACK3_URL", "")
LLM_FALLBACK3_KEY = os.getenv("LLM_FALLBACK3_KEY", "")

# ═══════════════════════════════════════════════════════════════════════════
# JWT 配置
# ═══════════════════════════════════════════════════════════════════════════

JWT_SECRET = os.getenv("JWT_SECRET", "dev-secret-change-in-prod-32chars!")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_HOURS = int(os.getenv("JWT_EXPIRE_HOURS", "72"))
AUTH_MODE = _normalize_auth_mode(os.getenv("AUTH_MODE", "dev"))
# 仅在开发环境允许自动注册/免密登录捷径；staging/prod 强制真实认证边界。
AUTH_ALLOW_DEV_SHORTCUTS = AUTH_MODE == "dev"

# ═══════════════════════════════════════════════════════════════════════════
# 功能开关（15个，3层级）
# ═══════════════════════════════════════════════════════════════════════════

# --- Core tier: 始终开启，核心能力不可禁用 ---
ENABLE_QUALITY_CHECK = os.getenv("ENABLE_QUALITY_CHECK", "1") == "1"
ENABLE_TRUST_SCORING = os.getenv("ENABLE_TRUST_SCORING", "1") == "1"
ENABLE_LEARNING = os.getenv("ENABLE_LEARNING", "1") == "1"
ENABLE_ALERTS = os.getenv("ENABLE_ALERTS", "1") == "1"
ENABLE_METRICS = os.getenv("ENABLE_METRICS", "1") == "1"

# --- Enhanced tier: 默认开启，可按需关闭 ---
ENABLE_TOOL_USE = os.getenv("ENABLE_TOOL_USE", "1") == "1"
ENABLE_KNOWLEDGE_RULES = os.getenv("ENABLE_KNOWLEDGE_RULES", "1") == "1"
ENABLE_PERSONALITY = os.getenv("ENABLE_PERSONALITY", "1") == "1"
ENABLE_HANDOFF = os.getenv("ENABLE_HANDOFF", "1") == "1"
ENABLE_PROACTIVE = os.getenv("ENABLE_PROACTIVE", "1") == "1"              # 主动建议与预判
ENABLE_PROACTIVE_SEARCH = os.getenv("ENABLE_PROACTIVE_SEARCH", "0") == "1"  # 管道主动联网搜索（默认关闭，避免离线/测试超时）
ENABLE_BACKGROUND_TASKS = os.getenv("ENABLE_BACKGROUND_TASKS", "1") == "1"  # 后台闭环任务调度（测试可关闭）

# --- Advanced tier: 可选功能，部分默认关闭 ---
ENABLE_WORKSPACE_ENGINE = os.getenv("ENABLE_WORKSPACE_ENGINE", "1") == "1"    # 工作区文件引擎
ENABLE_PRODUCT_LIFECYCLE = os.getenv("ENABLE_PRODUCT_LIFECYCLE", "1") == "1"  # 产品生命周期管理
ENABLE_AUTOPILOT = os.getenv("ENABLE_AUTOPILOT", "0") == "1"                  # 自动驾驶模式（谨慎）
ENABLE_INTELLIGENCE = os.getenv("ENABLE_INTELLIGENCE", "1") == "1"            # 增强智能分析
ENABLE_EXTERNAL_DATA = os.getenv("ENABLE_EXTERNAL_DATA", "0") == "1"          # 外部数据接入（谨慎）
ENABLE_ENGINEERING_AGENT = os.getenv("ENABLE_ENGINEERING_AGENT", "0") == "1"  # 技术工程师Agent（默认隐藏）
ENABLE_EXECUTION_ORCHESTRATION = os.getenv("ENABLE_EXECUTION_ORCHESTRATION", "1") == "1"  # 强执行编排
ENABLE_EXECUTION_ACTIONS = os.getenv("ENABLE_EXECUTION_ACTIONS", "1") == "1"  # 自动动作执行
EXECUTION_ACTION_REQUIRE_APPROVAL = os.getenv("EXECUTION_ACTION_REQUIRE_APPROVAL", "0") == "1"  # 动作执行前需审批
EXECUTION_ACTION_MAX_RETRIES = int(os.getenv("EXECUTION_ACTION_MAX_RETRIES", "2"))  # 动作失败重试次数
_EXECUTION_PACKAGE_LOCK_POLICY_RAW = os.getenv("EXECUTION_PACKAGE_LOCK_POLICY", "strict").strip().lower()
EXECUTION_PACKAGE_LOCK_POLICY = _EXECUTION_PACKAGE_LOCK_POLICY_RAW if _EXECUTION_PACKAGE_LOCK_POLICY_RAW in {"strict", "warn"} else "strict"  # strict=漂移阻断, warn=记录告警继续执行
ENABLE_ACTION_WEBHOOK_ADAPTER = os.getenv("ENABLE_ACTION_WEBHOOK_ADAPTER", "0") == "1"  # 外部Webhook适配器
ACTION_WEBHOOK_ALLOWLIST = [x.strip() for x in os.getenv("ACTION_WEBHOOK_ALLOWLIST", "").split(",") if x.strip()]  # 允许的Webhook前缀
ACTION_WEBHOOK_TIMEOUT_SECONDS = int(os.getenv("ACTION_WEBHOOK_TIMEOUT_SECONDS", "15"))

# ═══════════════════════════════════════════════════════════════════════════
# 运行时限制
# ═══════════════════════════════════════════════════════════════════════════

MAX_TOOL_ROUNDS = int(os.getenv("MAX_TOOL_ROUNDS", "3"))
MAX_TOOL_ENABLED_ROUNDS = int(os.getenv("MAX_TOOL_ENABLED_ROUNDS", "2"))  # 允许触发 tool_call 的最大轮数
SINGLE_TIER_TOOL_ENABLED_ROUNDS = int(os.getenv("SINGLE_TIER_TOOL_ENABLED_ROUNDS", "1"))  # 单岗/快速任务的 tool_call 轮数上限
MAX_TOOL_CALLS_PER_ROUND = int(os.getenv("MAX_TOOL_CALLS_PER_ROUND", "3"))  # 每轮最多执行的 tool_call 数（超出将截断）
SINGLE_TIER_MAX_TOOL_CALLS_PER_ROUND = int(os.getenv("SINGLE_TIER_MAX_TOOL_CALLS_PER_ROUND", "2"))  # 单岗/快速任务每轮工具调用上限
TOOL_EXECUTION_TOTAL_BUDGET_SECONDS = int(os.getenv("TOOL_EXECUTION_TOTAL_BUDGET_SECONDS", "90"))  # 主Agent工具总预算
SINGLE_TIER_TOOL_EXECUTION_TOTAL_BUDGET_SECONDS = int(os.getenv("SINGLE_TIER_TOOL_EXECUTION_TOTAL_BUDGET_SECONDS", "45"))  # 单岗/快速任务工具总预算
TOOL_EXECUTION_TIMEOUT_SECONDS = int(os.getenv("TOOL_EXECUTION_TIMEOUT_SECONDS", "45"))
SEARCH_TOOL_TIMEOUT_SECONDS = int(os.getenv("SEARCH_TOOL_TIMEOUT_SECONDS", "28"))
PROACTIVE_SEARCH_QUERY_TIMEOUT_SECONDS = float(os.getenv("PROACTIVE_SEARCH_QUERY_TIMEOUT_SECONDS", "8"))
PROACTIVE_SEARCH_TOTAL_BUDGET_SECONDS = float(os.getenv("PROACTIVE_SEARCH_TOTAL_BUDGET_SECONDS", "20"))
CHAT_SIMPLE_TIMEOUT_SECONDS = int(os.getenv("CHAT_SIMPLE_TIMEOUT_SECONDS", "60"))
WORKSPACE_TASK_EXECUTION_TIMEOUT_SECONDS = int(os.getenv("WORKSPACE_TASK_EXECUTION_TIMEOUT_SECONDS", "35"))
SUPPORT_AGENT_MAX_ROLES = int(os.getenv("SUPPORT_AGENT_MAX_ROLES", "32"))
SUPPORT_AGENT_MAX_ROLES_MANUAL = int(os.getenv("SUPPORT_AGENT_MAX_ROLES_MANUAL", "32"))  # 手动协作独立上限（不受自动协作预算收缩影响）
SUPPORT_AGENT_TIMEOUT_SECONDS = int(os.getenv("SUPPORT_AGENT_TIMEOUT_SECONDS", "28"))
SUPPORT_AGENT_TOTAL_TIMEOUT_SECONDS = int(os.getenv("SUPPORT_AGENT_TOTAL_TIMEOUT_SECONDS", "70"))
SUPPORT_AGENT_MAX_CONCURRENCY = int(os.getenv("SUPPORT_AGENT_MAX_CONCURRENCY", "6"))
SUPPORT_AGENT_ENABLE_TOOL_USE = os.getenv("SUPPORT_AGENT_ENABLE_TOOL_USE", "1") == "1"
SUPPORT_AGENT_MAX_TOOL_ROUNDS = int(os.getenv("SUPPORT_AGENT_MAX_TOOL_ROUNDS", "1"))
SUPPORT_AGENT_TOOL_TIMEOUT_SECONDS = int(os.getenv("SUPPORT_AGENT_TOOL_TIMEOUT_SECONDS", "10"))
SUPPORT_AGENT_PRIMARY_REPLY_MAX_CHARS = int(os.getenv("SUPPORT_AGENT_PRIMARY_REPLY_MAX_CHARS", "1200"))
SUPPORT_AGENT_TOOL_LIMIT = int(os.getenv("SUPPORT_AGENT_TOOL_LIMIT", "6"))
BACKGROUND_TASK_TIMEOUT = int(os.getenv("BACKGROUND_TASK_TIMEOUT", "10"))
MAX_HISTORY_MESSAGES = int(os.getenv("MAX_HISTORY_MESSAGES", "10"))
MAX_PROMPT_CHARS = int(os.getenv("MAX_PROMPT_CHARS", "2500"))
FEATURE_BUDGET_MAX = int(os.getenv("FEATURE_BUDGET_MAX", "3"))
UPLOAD_MAX_SIZE_MB = int(os.getenv("UPLOAD_MAX_SIZE_MB", "20"))
TRASH_EXPIRE_DAYS = int(os.getenv("TRASH_EXPIRE_DAYS", "30"))
ALLOWED_UPLOAD_EXTENSIONS = os.getenv(
    "ALLOWED_UPLOAD_EXTENSIONS",
    ".pdf,.png,.jpg,.jpeg,.gif,.xlsx,.csv,.docx,.txt,.md",
).split(",")

# ═══════════════════════════════════════════════════════════════════════════
# 服务器配置
# ═══════════════════════════════════════════════════════════════════════════

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8100"))

# ═══════════════════════════════════════════════════════════════════════════
# A2A 外部 Agent 白名单（SSRF防护）
# ═══════════════════════════════════════════════════════════════════════════

# 逗号分隔的允许调用的外部Agent端点前缀，留空则禁用A2A功能
EXTERNAL_AGENT_WHITELIST: list[str] = [
    x.strip() for x in os.getenv("EXTERNAL_AGENT_WHITELIST", "").split(",") if x.strip()
]
CORS_ORIGINS = os.getenv("CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000,http://localhost:5173,http://127.0.0.1:5173").split(",")





