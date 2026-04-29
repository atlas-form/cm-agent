"""
FastAPI 入口 — lifespan + CORS + 限流 + 路由注册。
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from src.config import CORS_ORIGINS, HOST, PORT, PRODUCTS_DIR
from src.core.request_context import (
    reset_current_request_id,
    reset_current_trace_id,
    set_current_request_id,
    set_current_trace_id,
)
from src.database import init_db, close_db, get_db, seed_agents

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# Lifespan
# ═══════════════════════════════════════════════════════════════════════════

async def _weekly_report_loop() -> None:
    """每周一早8点自动生成周报（站内通知）。"""
    import asyncio as _asyncio
    from datetime import datetime as _dt, timedelta as _td
    while True:
        try:
            now = _dt.utcnow()
            # 等到下周一 08:05 UTC（约北京时间16:05，可按需调整）
            days_until_monday = (7 - now.weekday()) % 7 or 7
            next_monday = now.replace(hour=0, minute=5, second=0, microsecond=0) + _td(days=days_until_monday)
            wait_secs = (next_monday - now).total_seconds()
            await _asyncio.sleep(max(wait_secs, 60))

            # 找出所有用户并生成周报
            from src.database import get_db as _get_db
            from src.services.long_task_service import create_task, run_task_stream
            db = await _get_db()
            rows = await db.execute_fetchall("SELECT DISTINCT id FROM users WHERE id > 0 LIMIT 200")
            for (uid,) in rows:
                try:
                    tid = await create_task(uid, "weekly_report", "本周经营周报", total_steps=100, role="data")
                    # 后台执行，不等结果
                    _asyncio.create_task(_run_weekly_task(tid, uid))
                except Exception as _e:
                    logger.debug("Weekly report task creation failed for user %s: %s", uid, _e)
        except _asyncio.CancelledError:
            break
        except Exception as _e:
            logger.error("Weekly report loop error: %s", _e)
            import asyncio as _a2
            await _a2.sleep(3600)


async def _run_weekly_task(task_id: int, user_id: int) -> None:
    from src.services.long_task_service import run_task_stream
    try:
        async for _ in run_task_stream(task_id, user_id, "weekly_report", {}):
            pass
    except Exception as e:
        logger.debug("Weekly task %s failed: %s", task_id, e)


async def _competitor_monitor_loop() -> None:
    """每24小时扫描所有竞品，检测价格变化并写入competitor_changes + background_events。"""
    import asyncio as _asyncio
    import json as _json
    import re as _re
    _INTERVAL = 24 * 3600  # 24小时
    while True:
        try:
            await _asyncio.sleep(300)  # 启动5分钟后首次运行
            from src.database import get_db as _get_db
            from src.skills.registry import get_registry
            db = await _get_db()
            registry = get_registry()

            # 取所有距上次搜索>23h或从未搜索的竞品
            comp_rows = await db.execute_fetchall(
                """SELECT pc.*, p.user_id AS owner_id
                   FROM product_competitors pc
                   JOIN products p ON pc.product_id = p.id
                   WHERE pc.last_searched_at IS NULL
                      OR pc.last_searched_at < datetime('now', '-23 hours')
                   LIMIT 50"""
            )
            if not comp_rows:
                await _asyncio.sleep(_INTERVAL)
                continue

            for comp in comp_rows:
                try:
                    result = await registry.execute(
                        "search_competitor",
                        {"query": f"{comp['name']} {comp['platform'] or ''} 价格".strip()},
                        context={"user_id": comp["owner_id"], "_needs_fresh_data": True},
                    )
                    new_price = None
                    snippets = result.get("搜索结果", [])
                    if isinstance(snippets, list):
                        for item in snippets[:3]:
                            body = str(item.get("body", item.get("snippet", "")))
                            prices = _re.findall(r"[\u00a5¥]?\s*(\d+\.?\d*)", body)
                            if prices:
                                candidate = float(prices[0])
                                if candidate > 1:
                                    new_price = candidate
                                    break

                    if new_price and comp["price"] and abs(new_price - float(comp["price"])) / float(comp["price"]) > 0.03:
                        change_type = "price_drop" if new_price < float(comp["price"]) else "price_rise"
                        summary = f"{comp['name']} 价格从 ¥{comp['price']:.2f} 变为 ¥{new_price:.2f}"
                        await db.execute(
                            """INSERT INTO competitor_changes
                               (competitor_id, product_id, user_id, change_type, old_value, new_value, summary)
                               VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (comp["id"], comp["product_id"], comp["owner_id"],
                             change_type, str(comp["price"]), str(new_price), summary),
                        )
                        await db.execute(
                            """INSERT INTO background_events (user_id, event_type, data)
                               VALUES (?, 'competitor_change', ?)""",
                            (comp["owner_id"], _json.dumps(
                                {"product_id": comp["product_id"], "summary": summary,
                                 "change_type": change_type}, ensure_ascii=False)),
                        )

                    await db.execute(
                        "UPDATE product_competitors SET last_searched_at=CURRENT_TIMESTAMP WHERE id=?",
                        (comp["id"],),
                    )
                    await db.commit()
                    await _asyncio.sleep(5)  # 每个竞品间隔5秒，避免搜索限速
                except Exception:
                    pass

            await _asyncio.sleep(_INTERVAL)
        except _asyncio.CancelledError:
            break
        except Exception as _e:
            logger.error("Competitor monitor loop error: %s", _e)
            import asyncio as _a3
            await _a3.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up — initializing database...")
    await init_db()
    db = await get_db()
    await seed_agents(db)
    logger.info("Database ready. Server starting.")
    # 后台定时任务（暂时关闭：平台自动同步 + 周报生成）
    # _sync_task = _asyncio.create_task(_daily_sync_loop())
    # _report_task = _asyncio.create_task(_weekly_report_loop())
    import asyncio as _asyncio2
    _monitor_task = _asyncio2.create_task(_competitor_monitor_loop())
    yield
    _monitor_task.cancel()
    logger.info("Shutting down — closing database...")
    await close_db()
    logger.info("Shutdown complete.")


# ═══════════════════════════════════════════════════════════════════════════
# App 实例
# ═══════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="电商多Agent协同智能平台",
    version="4.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ═══════════════════════════════════════════════════════════════════════════
# 简易限流中间件
# ═══════════════════════════════════════════════════════════════════════════

_rate_buckets: dict[str, list[float]] = defaultdict(list)
_RATE_LIMITS = {
    "/api/auth": (10, 60),                      # 10 req / 60s
    "/api/chat/attachments/upload": (80, 60),  # 80 req / 60s (separate bucket for batch attachments)
    "/api/chat": (20, 60),                      # 20 req / 60s
    "/api/admin": (30, 60),                     # 30 req / 60s
}


_PUBLIC_API_PATHS = {
    "/api/health",
    "/api/auth/login",
    "/api/auth/register",
}


def _is_public_api_path(path: str) -> bool:
    """仅放行必要公开 API，其余业务接口统一要求认证。"""
    if path in _PUBLIC_API_PATHS:
        return True
    if path.startswith("/api/webhook/"):
        return True
    return False




@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    incoming_request_id = str(
        request.headers.get("X-Request-Id")
        or request.headers.get("X-Correlation-Id")
        or ""
    ).strip()
    incoming_trace_id = str(
        request.headers.get("X-Trace-Id")
        or request.headers.get("X-B3-TraceId")
        or request.headers.get("traceparent")
        or ""
    ).strip()

    request_id = (incoming_request_id[:128] or uuid.uuid4().hex)
    trace_id = (incoming_trace_id[:128] or request_id)

    request.state.request_id = request_id
    request.state.trace_id = trace_id
    request_token = set_current_request_id(request_id)
    trace_token = set_current_trace_id(trace_id)

    try:
        response = await call_next(request)
    finally:
        reset_current_trace_id(trace_token)
        reset_current_request_id(request_token)

    response.headers.setdefault("X-Request-Id", request_id)
    response.headers.setdefault("X-Trace-Id", trace_id)
    return response


@app.middleware("http")
async def api_auth_guard_middleware(request: Request, call_next):
    path = request.url.path

    if path.startswith("/api") and not _is_public_api_path(path):
        try:
            from src.routes.auth import get_current_user

            request.state.current_user = await get_current_user(request)
        except HTTPException as exc:
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    return await call_next(request)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    path = request.url.path
    now = time.time()

    # /api/auth/me 由前端高频探测登录态，若与登录接口共享限流桶容易误伤正常请求。
    if path == "/api/auth/me":
        return await call_next(request)

    for prefix, (limit, window) in _RATE_LIMITS.items():
        if path.startswith(prefix):
            key = f"{request.client.host}:{prefix}" if request.client else prefix
            bucket = _rate_buckets[key]
            # 清理过期记录
            cutoff = now - window
            _rate_buckets[key] = [t for t in bucket if t > cutoff]
            bucket = _rate_buckets[key]

            if len(bucket) >= limit:
                retry_after = max(1, int((bucket[0] + window) - now)) if bucket else int(window)
                return Response(
                    content='{"detail":"请求过于频繁，请稍后再试"}',
                    status_code=429,
                    media_type="application/json",
                    headers={"Retry-After": str(retry_after)},
                )
            bucket.append(now)
            break

    return await call_next(request)


# ═══════════════════════════════════════════════════════════════════════════
# 路由注册
# ═══════════════════════════════════════════════════════════════════════════

from src.routes.health import router as health_router  # noqa: E402
from src.routes.auth import router as auth_router  # noqa: E402
from src.routes.chat import router as chat_router, conv_router  # noqa: E402
from src.routes.agents import router as agents_router  # noqa: E402
from src.routes.products import router as products_router  # noqa: E402
from src.routes.workspaces import router as workspaces_router  # noqa: E402
from src.routes.skills import router as skills_router  # noqa: E402
from src.routes.capabilities import router as capabilities_router  # noqa: E402
from src.routes.packages import router as packages_router  # noqa: E402
from src.routes.admin import router as admin_router  # noqa: E402
from src.routes.user import router as user_router  # noqa: E402
from src.routes.campaigns import router as campaigns_router  # noqa: E402
from src.routes.intelligence import router as intelligence_router  # noqa: E402
from src.routes.briefing import router as briefing_router  # noqa: E402
from src.routes.assets import router as assets_router  # noqa: E402
from src.routes.trash import router as trash_router  # noqa: E402
from src.routes.kernel import router as kernel_router  # noqa: E402
from src.routes.external_data import router as external_data_router  # noqa: E402
from src.routes.platform_connect import router as platform_connect_router  # noqa: E402
from src.routes.data_import import router as data_import_router  # noqa: E402
from src.routes.long_tasks import router as long_tasks_router  # noqa: E402
from src.routes.execution import router as execution_router  # noqa: E402
from src.routes.orchestration import router as orchestration_router  # noqa: E402
from src.routes.static import router as static_router  # noqa: E402

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(conv_router)
app.include_router(agents_router)
app.include_router(products_router)
app.include_router(workspaces_router)
app.include_router(skills_router)
app.include_router(capabilities_router)
app.include_router(packages_router)
app.include_router(admin_router)
app.include_router(user_router)
app.include_router(campaigns_router)
app.include_router(intelligence_router)
app.include_router(briefing_router)
app.include_router(assets_router)
app.include_router(trash_router)
app.include_router(kernel_router)
app.include_router(external_data_router)
app.include_router(platform_connect_router)
app.include_router(data_import_router)
app.include_router(long_tasks_router)
app.include_router(execution_router)
app.include_router(orchestration_router)
# 产品文件目录（静态服务）
PRODUCTS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/product-files", StaticFiles(directory=str(PRODUCTS_DIR)), name="product-files")

# 静态资源目录（Vite 构建产物）
_CLIENT_DIST_ASSETS = Path(__file__).parent.parent.parent / "client" / "dist" / "assets"
if _CLIENT_DIST_ASSETS.exists():
    app.mount("/assets", StaticFiles(directory=str(_CLIENT_DIST_ASSETS)), name="static-assets")

# static must be last (catch-all routes)
app.include_router(static_router)


# ═══════════════════════════════════════════════════════════════════════════
# 入口
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.app:app", host=HOST, port=PORT, reload=True)


