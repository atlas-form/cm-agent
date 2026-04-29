"""
自动驾驶服务层 — 管理 store_autopilot_runs、store_autopilot_schedules、store_daily_reports 表。

trigger_run() 创建一次执行记录。
execute_autopilot_run() 执行实际 AI 分析 + 动作决策。
run_due_schedules() 检查是否有到期的定时任务并触发。

执行流程：
1. 加载用户产品/工作区状态作为上下文
2. 调用 LLM（ops 角色）生成运营建议列表
3. 解析建议中的可执行动作（create_task / create_campaign / save_memory）
4. ENABLE_AUTOPILOT=True + EXECUTION_ACTION_REQUIRE_APPROVAL=False 时自动执行
5. 写入 store_autopilot_runs.result 记录执行结果
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


async def trigger_run(user_id: int, run_type: str = "manual") -> dict:
    """
    创建一次自动驾驶执行记录。
    若 ENABLE_AUTOPILOT=True，自动在后台启动 execute_autopilot_run。
    """
    try:
        from src.database import get_db

        db = await get_db()
        now = datetime.now(timezone.utc).isoformat()
        cursor = await db.execute(
            """
            INSERT INTO store_autopilot_runs
                (user_id, run_type, status, result, risk_snapshot, created_at)
            VALUES (?, ?, 'pending', '{}', '{}', ?)
            """,
            (user_id, run_type, now),
        )
        await db.commit()
        run_id = cursor.lastrowid

        # ★ 若 ENABLE_AUTOPILOT 开启，后台异步执行
        from src.config import ENABLE_AUTOPILOT
        if ENABLE_AUTOPILOT:
            import asyncio
            asyncio.create_task(execute_autopilot_run(run_id, user_id))

        row = await db.execute_fetchone(
            "SELECT * FROM store_autopilot_runs WHERE id = ?", (run_id,)
        )
        item = dict(row)
        for f in ("result", "risk_snapshot"):
            if isinstance(item.get(f), str):
                try:
                    item[f] = json.loads(item[f])
                except Exception:
                    item[f] = {}
        return item
    except Exception:
        logger.exception("trigger_run failed user_id=%s", user_id)
        return {}


async def list_runs(user_id: int, limit: int = 20) -> list:
    """列出用户的执行历史，按时间倒序。"""
    try:
        from src.database import get_db

        db = await get_db()
        rows = await db.execute_fetchall(
            """
            SELECT * FROM store_autopilot_runs
            WHERE user_id = ?
            ORDER BY created_at DESC LIMIT ?
            """,
            (user_id, limit),
        )
        result = []
        for row in rows:
            item = dict(row)
            for f in ("result", "risk_snapshot"):
                if isinstance(item.get(f), str):
                    try:
                        item[f] = json.loads(item[f])
                    except Exception:
                        item[f] = {}
            result.append(item)
        return result
    except Exception:
        logger.exception("list_runs failed user_id=%s", user_id)
        return []


async def get_run(run_id: int) -> dict | None:
    """获取单次执行记录详情。"""
    try:
        from src.database import get_db

        db = await get_db()
        row = await db.execute_fetchone(
            "SELECT * FROM store_autopilot_runs WHERE id = ?", (run_id,)
        )
        if row is None:
            return None
        item = dict(row)
        for f in ("result", "risk_snapshot"):
            if isinstance(item.get(f), str):
                try:
                    item[f] = json.loads(item[f])
                except Exception:
                    item[f] = {}
        return item
    except Exception:
        logger.exception("get_run failed id=%s", run_id)
        return None


async def get_schedule(user_id: int) -> dict | None:
    """获取用户的定时自动驾驶配置。"""
    try:
        from src.database import get_db

        db = await get_db()
        row = await db.execute_fetchone(
            "SELECT * FROM store_autopilot_schedules WHERE user_id = ? LIMIT 1",
            (user_id,),
        )
        return dict(row) if row else None
    except Exception:
        logger.exception("get_schedule failed user_id=%s", user_id)
        return None


async def update_schedule(user_id: int, data: dict) -> dict:
    """插入或更新用户的定时配置（UPSERT by user_id + schedule_type）。"""
    try:
        from src.database import get_db

        db = await get_db()
        now = datetime.now(timezone.utc).isoformat()
        schedule_type = data.get("schedule_type", "daily")
        cron_expr = data.get("cron_expr", "0 9 * * *")
        enabled = 1 if data.get("enabled", True) else 0
        await db.execute(
            """
            INSERT INTO store_autopilot_schedules
                (user_id, schedule_type, cron_expr, enabled, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(user_id, schedule_type) DO UPDATE SET
                cron_expr = excluded.cron_expr,
                enabled   = excluded.enabled
            """,
            (user_id, schedule_type, cron_expr, enabled, now),
        )
        await db.commit()
        return await get_schedule(user_id) or {}
    except Exception:
        logger.exception("update_schedule failed user_id=%s", user_id)
        return {}


async def execute_autopilot_run(run_id: int, user_id: int) -> dict:
    """
    执行一次自动驾驶：加载状态 → LLM分析 → 动作决策 → 执行/排队 → 写入结果。

    这是真正做事的函数，trigger_run 只是创建记录，需要手动或定时调用此函数。
    """
    from src.config import ENABLE_AUTOPILOT, EXECUTION_ACTION_REQUIRE_APPROVAL
    if not ENABLE_AUTOPILOT:
        return {"error": "ENABLE_AUTOPILOT 未开启，请在 .env 中设置 ENABLE_AUTOPILOT=1"}

    db = None
    try:
        from src.database import get_db
        db = await get_db()

        # 更新状态为 running
        await db.execute(
            "UPDATE store_autopilot_runs SET status = 'running' WHERE id = ?", (run_id,)
        )
        await db.commit()

        # ── 加载上下文 ──
        context_parts: List[str] = []

        # 1. 真实平台指标数据（最近 7 天，来自 store_metrics）
        try:
            metrics_rows = await db.execute_fetchall(
                """
                SELECT platform, metric_key,
                       SUM(metric_value) as sum_val, AVG(metric_value) as avg_val,
                       COUNT(*) as days
                FROM store_metrics
                WHERE user_id = ? AND metric_date >= date('now', '-7 days')
                GROUP BY platform, metric_key
                ORDER BY platform, metric_key
                """,
                (user_id,),
            )
            if metrics_rows:
                # 按平台整理
                platform_metrics: Dict[str, Dict[str, float]] = {}
                sum_keys = {"gmv", "orders", "uv", "pv", "new_buyers", "ad_spend"}
                for row in metrics_rows:
                    p = row["platform"]
                    k = row["metric_key"]
                    if p not in platform_metrics:
                        platform_metrics[p] = {}
                    platform_metrics[p][k] = (
                        round(row["sum_val"], 2) if k in sum_keys else round(row["avg_val"], 4)
                    )
                for platform, pdata in platform_metrics.items():
                    parts = []
                    if "gmv" in pdata:
                        parts.append(f"GMV ¥{pdata['gmv']:,.0f}")
                    if "orders" in pdata:
                        parts.append(f"订单 {pdata['orders']:.0f} 单")
                    if "uv" in pdata:
                        parts.append(f"UV {pdata['uv']:.0f}")
                    if "conversion_rate" in pdata:
                        parts.append(f"转化率 {pdata['conversion_rate']*100:.2f}%")
                    if "ad_spend" in pdata:
                        parts.append(f"广告花费 ¥{pdata['ad_spend']:,.0f}")
                    if parts:
                        display = {"taobao":"淘宝","jd":"京东","pdd":"拼多多","douyin":"抖音"}.get(platform, platform)
                        context_parts.append(f"【{display} 近7天】" + "，".join(parts))
            else:
                context_parts.append("【提示】暂无真实平台指标数据（可在「平台连接」页面配置API或上传CSV导入数据）")
        except Exception as e:
            logger.warning(f"加载 store_metrics 失败: {e}")

        # 2. 已连接平台列表
        try:
            conn_rows = await db.execute_fetchall(
                "SELECT platform FROM platform_connections WHERE user_id = ? AND enabled = 1",
                (user_id,),
            )
            if conn_rows:
                platforms_connected = "、".join(r["platform"] for r in conn_rows)
                context_parts.append(f"已连接平台：{platforms_connected}（可直接同步操作）")
        except Exception:
            pass

        # 3. 产品状态
        product_rows = await db.execute_fetchall(
            "SELECT name, category, lifecycle_status, sku FROM products WHERE id IN (SELECT id FROM products ORDER BY updated_at DESC LIMIT 5)",
            (),
        )
        if product_rows:
            product_summary = "；".join(
                f"{r['name']}({r['lifecycle_status']})" for r in product_rows
            )
            context_parts.append(f"当前产品（最近5个）：{product_summary}")

        # 4. 工作区待办任务
        pending_tasks = await db.execute_fetchall(
            "SELECT title, owner_role, priority FROM workspace_tasks WHERE status = 'pending' ORDER BY priority DESC LIMIT 5",
            (),
        )
        if pending_tasks:
            tasks_summary = "；".join(
                f"{r['title']}[{r['owner_role']}]" for r in pending_tasks
            )
            context_parts.append(f"待处理任务（{len(pending_tasks)}个）：{tasks_summary}")

        # 5. 近期告警
        alert_rows = await db.execute_fetchall(
            "SELECT alert_type, message FROM alerts WHERE resolved = 0 ORDER BY created_at DESC LIMIT 3",
            (),
        )
        if alert_rows:
            alert_summary = "；".join(f"[{r['alert_type']}]{r['message'][:50]}" for r in alert_rows)
            context_parts.append(f"未处理告警：{alert_summary}")

        context_text = "\n".join(context_parts) if context_parts else "暂无数据"

        # ── LLM 分析 ──
        system_prompt = (
            "You are a proactive e-commerce operations AI. "
            "CRITICAL: Always respond in Chinese (中文). "
            "You are running in autopilot mode. Analyze the current store state and generate "
            "3-5 specific, actionable recommendations based on the REAL metrics data provided. "
            "When making recommendations about price/campaign/inventory, be specific with numbers. "
            "For each recommendation that can be immediately executed, specify one of: "
            "CREATE_TASK(title, owner_role, priority, acceptance_criteria), "
            "CREATE_CAMPAIGN(name, budget, platform), or SAVE_MEMORY(key, content). "
            "Format each action on its own line with the prefix 'ACTION:'."
        )
        user_message = (
            f"当前店铺状态（真实数据）：\n{context_text}\n\n"
            "请基于以上真实数据生成今日运营建议，并指明哪些可以立即执行。\n"
            "每条可执行动作请以 ACTION: 开头，格式：\n"
            "  ACTION:CREATE_TASK(标题, 角色, 优先级, 验收标准)\n"
            "  ACTION:CREATE_CAMPAIGN(活动名, 预算, 平台)\n"
            "  ACTION:SAVE_MEMORY(键名, 内容摘要)"
        )

        from src.llm_client import call_llm
        llm_reply = await call_llm(
            system=system_prompt,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.5,
            max_tokens=2000,
        )

        if not llm_reply:
            await db.execute(
                "UPDATE store_autopilot_runs SET status = 'failed', result = ? WHERE id = ?",
                (json.dumps({"error": "LLM未响应"}, ensure_ascii=False), run_id),
            )
            await db.commit()
            return {"error": "LLM未响应"}

        # ── 解析动作 ──
        actions_executed: List[Dict[str, Any]] = []
        actions_queued: List[Dict[str, Any]] = []

        action_lines = [line.strip() for line in llm_reply.split("\n") if line.strip().startswith("ACTION:")]

        for action_line in action_lines[:5]:  # 最多执行5个动作
            raw = action_line[7:].strip()  # 去掉 "ACTION:" 前缀

            # 解析 CREATE_TASK(...)
            m = re.match(r"CREATE_TASK\((.+)\)", raw, re.DOTALL)
            if m:
                args_raw = [a.strip() for a in m.group(1).split(",")]
                action_data = {
                    "type": "create_task",
                    "title": args_raw[0] if len(args_raw) > 0 else "自动驾驶任务",
                    "owner_role": args_raw[1] if len(args_raw) > 1 else "ops",
                    "priority": int(args_raw[2]) if len(args_raw) > 2 and args_raw[2].isdigit() else 0,
                    "acceptance_criteria": args_raw[3] if len(args_raw) > 3 else "",
                }
            else:
                m = re.match(r"CREATE_CAMPAIGN\((.+)\)", raw, re.DOTALL)
                if m:
                    args_raw = [a.strip() for a in m.group(1).split(",")]
                    budget_str = args_raw[1].strip() if len(args_raw) > 1 else "0"
                    budget_match = re.search(r"[\d.]+", budget_str)
                    platform_arg = args_raw[2].strip() if len(args_raw) > 2 else ""
                    action_data = {
                        "type": "create_campaign",
                        "name": args_raw[0] if args_raw else "自动驾驶活动",
                        "budget": float(budget_match.group()) if budget_match else 0,
                        "platform": platform_arg,
                    }
                else:
                    m = re.match(r"SAVE_MEMORY\((.+)\)", raw, re.DOTALL)
                    if m:
                        args_raw = [a.strip() for a in m.group(1).split(",", 1)]
                        action_data = {
                            "type": "save_memory",
                            "key": args_raw[0] if args_raw else "autopilot_note",
                            "content": args_raw[1] if len(args_raw) > 1 else raw,
                        }
                    else:
                        continue  # 无法解析的动作跳过

            if EXECUTION_ACTION_REQUIRE_APPROVAL:
                actions_queued.append(action_data)
            else:
                # 直接执行
                from src.services.action_adapters import execute_adapter_action
                exec_context = {"user_id": user_id, "workspace_id": None}
                success, exec_result = await execute_adapter_action(
                    action_type=action_data["type"].replace("_", ":").replace("create:task", "create_workspace_task")
                    .replace("create:campaign", "create_campaign")
                    .replace("save:memory", "save_workspace_memory"),
                    payload=action_data,
                    context=exec_context,
                )
                action_data["executed"] = success
                action_data["result"] = exec_result
                actions_executed.append(action_data)

        # ── 写入结果 ──
        result = {
            "analysis": llm_reply[:1500],
            "actions_executed": actions_executed,
            "actions_queued": actions_queued,
            "context_summary": context_text[:500],
            "executed_at": datetime.now(timezone.utc).isoformat(),
        }
        await db.execute(
            "UPDATE store_autopilot_runs SET status = 'completed', result = ? WHERE id = ?",
            (json.dumps(result, ensure_ascii=False), run_id),
        )
        await db.commit()
        return result

    except Exception as e:
        logger.exception("execute_autopilot_run failed run_id=%s", run_id)
        if db:
            try:
                await db.execute(
                    "UPDATE store_autopilot_runs SET status = 'failed', result = ? WHERE id = ?",
                    (json.dumps({"error": str(e)}, ensure_ascii=False), run_id),
                )
                await db.commit()
            except Exception:
                pass
        return {"error": str(e)}


async def run_due_schedules(user_id: int) -> dict | None:
    """执行用户到期的自动驾驶定时任务（复用策略化重试/降级逻辑）。"""
    try:
        from src.database import get_db
        from src.routes.intelligence import (
            _execute_scheduled_autopilot_with_retry,
            _get_autopilot_schedule_policy,
        )

        db = await get_db()
        schedules = await db.execute_fetchall(
            """
            SELECT id, schedule_type, last_run_at
            FROM store_autopilot_schedules
            WHERE user_id = ? AND enabled = 1
            ORDER BY id DESC
            """,
            (int(user_id),),
        )
        if not schedules:
            return None

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        triggered_runs: List[int] = []
        run_results: List[Dict[str, Any]] = []
        degraded_count = 0
        skipped_today_count = 0

        for sched in schedules:
            last_run = str(sched["last_run_at"] or "").strip()
            if last_run and last_run[:10] == today:
                skipped_today_count += 1
                continue

            schedule_type = str(sched["schedule_type"] or "daily").strip() or "daily"
            cursor = await db.execute(
                "INSERT INTO store_autopilot_runs (user_id, run_type, status) VALUES (?, ?, 'pending')",
                (int(user_id), f"scheduled_{schedule_type}"),
            )
            run_id = int(cursor.lastrowid)
            triggered_runs.append(run_id)

            schedule_policy = await _get_autopilot_schedule_policy(
                db,
                int(user_id),
                schedule_type,
            )
            run_outcome = await _execute_scheduled_autopilot_with_retry(
                db,
                user_id=int(user_id),
                run_id=run_id,
                schedule_type=schedule_type,
                policy=schedule_policy,
            )
            run_results.append(run_outcome)
            if str(run_outcome.get("status") or "").lower() == "degraded":
                degraded_count += 1

            await db.execute(
                "UPDATE store_autopilot_schedules SET last_run_at = CURRENT_TIMESTAMP WHERE id = ?",
                (int(sched["id"]),),
            )

        if not triggered_runs:
            return None

        await db.commit()
        return {
            "ok": True,
            "triggered_runs": triggered_runs,
            "run_results": run_results,
            "degraded_count": degraded_count,
            "skipped_today_count": skipped_today_count,
        }
    except Exception:
        logger.exception("run_due_schedules failed user_id=%s", user_id)
        return None


async def get_daily_report(user_id: int) -> dict | None:
    """获取今日日报（最新一条未归档记录）。"""
    try:
        from src.database import get_db

        db = await get_db()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = await db.execute_fetchone(
            """
            SELECT * FROM store_daily_reports
            WHERE user_id = ? AND report_date = ? AND archived = 0
            ORDER BY created_at DESC LIMIT 1
            """,
            (user_id, today),
        )
        if row is None:
            return None
        item = dict(row)
        if isinstance(item.get("data"), str):
            try:
                item["data"] = json.loads(item["data"])
            except Exception:
                item["data"] = {}
        return item
    except Exception:
        logger.exception("get_daily_report failed user_id=%s", user_id)
        return None


async def archive_daily_report(user_id: int) -> bool:
    """将今日日报标记为已归档。"""
    try:
        from src.database import get_db

        db = await get_db()
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        await db.execute(
            """
            UPDATE store_daily_reports SET archived = 1
            WHERE user_id = ? AND report_date = ? AND archived = 0
            """,
            (user_id, today),
        )
        await db.commit()
        return True
    except Exception:
        logger.exception("archive_daily_report failed user_id=%s", user_id)
        return False
