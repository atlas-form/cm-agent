"""
日报引擎 — 汇总近期活动，生成结构化 Briefing，0 LLM 调用。

各 section 的数据来源：
- active_alerts      → alerts 表（status='active'）
- product_updates    → products 表（近期 lifecycle_status 变更）
- quality_summary    → quality_checks 表（近7天均分 + 通过率）
- trust_summary      → trust_scores 表（各角色最新信任等级）
- task_summary       → workspace_tasks 表（待完成 vs 已完成数量）
- learning_highlights → learnings 表（近3天新增学习记忆）

通知列表来自 background_events 表。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════════════

# 严重程度排序权重，critical 优先展示
_SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}

# 信任等级中文映射
_TRUST_LEVEL_ZH = {
    "HIGH":     "高信任",
    "MODERATE": "中等",
    "LOW":      "偏低",
    "NONE":     "极低",
}

# 任务状态分组
_PENDING_STATUSES = frozenset({"pending", "in_progress", "blocked"})
_DONE_STATUSES = frozenset({"done"})

# 学习类别中文映射
_LEARNING_CATEGORY_ZH = {
    "success":     "成功模式",
    "blindspot":   "盲点",
    "improvement": "改进方向",
}


# ═══════════════════════════════════════════════════════════════════════════
# 主接口
# ═══════════════════════════════════════════════════════════════════════════

async def generate_daily_briefing(user_id: int) -> Dict[str, Any]:
    """
    生成当日综合 Briefing，包含 6 个 section。

    Parameters
    ----------
    user_id : int
        目标用户 ID。

    Returns
    -------
    dict
        {
            "sections": [
                {"title": str, "items": list[str]},
                ...
            ],
            "generated_at": str   # ISO 8601
        }
    """
    sections: List[Dict[str, Any]] = []

    # ── 1. 活跃告警 ───────────────────────────────────────────────────────
    alert_section = await _build_alert_section(user_id)
    sections.append(alert_section)

    # ── 2. 产品生命周期近期变更 ──────────────────────────────────────────
    product_section = await _build_product_section(user_id)
    sections.append(product_section)

    # ── 3. 质量摘要（近7天） ─────────────────────────────────────────────
    quality_section = await _build_quality_section()
    sections.append(quality_section)

    # ── 4. 信任等级摘要 ──────────────────────────────────────────────────
    trust_section = await _build_trust_section()
    sections.append(trust_section)

    # ── 5. 任务摘要 ──────────────────────────────────────────────────────
    task_section = await _build_task_section(user_id)
    sections.append(task_section)

    # ── 6. 近期学习亮点 ──────────────────────────────────────────────────
    learning_section = await _build_learning_section()
    sections.append(learning_section)

    return {
        "sections": sections,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Section 构建函数
# ═══════════════════════════════════════════════════════════════════════════

async def _build_alert_section(user_id: int) -> Dict[str, Any]:
    """活跃告警：总数 + 优先展示的 Top3。"""
    items: List[str] = []
    try:
        from src.database import get_db
        db = await get_db()

        rows = await db.execute_fetchall(
            """SELECT alert_type, severity, message, role
               FROM alerts
               WHERE status = 'active'
                 AND (user_id IS NULL OR user_id = ?)
               ORDER BY created_at DESC
               LIMIT 50""",
            (user_id,),
        )
        if not rows:
            items.append("暂无活跃告警")
        else:
            # 按严重程度排序后取 top3
            sorted_rows = sorted(
                rows,
                key=lambda r: _SEVERITY_ORDER.get(r["severity"], 9),
            )
            total = len(rows)
            items.append(f"共 {total} 条活跃告警")

            for r in sorted_rows[:3]:
                badge = {"critical": "🔴", "warning": "🟡", "info": "🔵"}.get(r["severity"], "⚪")
                role_tag = f"[{r['role']}] " if r["role"] else ""
                items.append(f"{badge} {role_tag}{r['message']}")

            if total > 3:
                items.append(f"... 还有 {total - 3} 条，请前往告警中心查看")

    except Exception as e:
        logger.warning("_build_alert_section failed: %s", e)
        items.append("告警数据获取失败")

    return {"title": "活跃告警", "items": items}


async def _build_product_section(user_id: int) -> Dict[str, Any]:
    """近7天产品状态变更摘要。"""
    items: List[str] = []
    try:
        from src.database import get_db
        db = await get_db()

        rows = await db.execute_fetchall(
            """SELECT name, lifecycle_status, updated_at
               FROM products
               WHERE user_id = ?
                 AND updated_at >= datetime('now', '-7 days')
               ORDER BY updated_at DESC
               LIMIT 10""",
            (user_id,),
        )
        if not rows:
            items.append("近7天无产品状态变更")
        else:
            items.append(f"近7天共 {len(rows)} 件产品有状态更新")
            for r in rows[:5]:
                items.append(f"《{r['name']}》→ {r['lifecycle_status']}（{r['updated_at'][:10]}）")
            if len(rows) > 5:
                items.append(f"... 还有 {len(rows) - 5} 件")

    except Exception as e:
        logger.warning("_build_product_section failed: %s", e)
        items.append("产品数据获取失败")

    return {"title": "产品动态", "items": items}


async def _build_quality_section() -> Dict[str, Any]:
    """近7天全局质量检查均分与通过率，按角色细分。"""
    items: List[str] = []
    try:
        from src.database import get_db
        db = await get_db()

        rows = await db.execute_fetchall(
            """SELECT role,
                      AVG(score)  AS avg_score,
                      COUNT(*)    AS total,
                      SUM(passed) AS passed_cnt
               FROM quality_checks
               WHERE created_at >= datetime('now', '-7 days')
               GROUP BY role
               ORDER BY avg_score DESC""",
            (),
        )
        if not rows:
            items.append("近7天暂无质量检查记录")
        else:
            total_checks = sum(int(r["total"]) for r in rows)
            total_passed = sum(int(r["passed_cnt"]) for r in rows)
            overall_rate = round(total_passed / total_checks * 100, 1) if total_checks else 0.0
            items.append(f"共 {total_checks} 次检查，总通过率 {overall_rate}%")

            for r in rows:
                avg = round(float(r["avg_score"]), 2)
                rate = round(int(r["passed_cnt"]) / int(r["total"]) * 100, 1)
                items.append(f"[{r['role']}] 均分 {avg}，通过率 {rate}%")

    except Exception as e:
        logger.warning("_build_quality_section failed: %s", e)
        items.append("质量数据获取失败")

    return {"title": "质量摘要（近7天）", "items": items}


async def _build_trust_section() -> Dict[str, Any]:
    """各角色最新信任等级快照。"""
    items: List[str] = []
    try:
        from src.database import get_db
        db = await get_db()

        # 每个角色取最新一条
        rows = await db.execute_fetchall(
            """SELECT role, score, trust_level
               FROM trust_scores
               WHERE id IN (
                   SELECT MAX(id) FROM trust_scores GROUP BY role
               )
               ORDER BY score DESC""",
            (),
        )
        if not rows:
            items.append("暂无信任评分记录，使用默认值（MODERATE）")
        else:
            for r in rows:
                level_zh = _TRUST_LEVEL_ZH.get(r["trust_level"], r["trust_level"])
                score_pct = round(float(r["score"]) * 100, 1)
                badge = {"HIGH": "✅", "MODERATE": "🟦", "LOW": "🟧", "NONE": "🟥"}.get(
                    r["trust_level"], "⬜"
                )
                items.append(f"{badge} [{r['role']}] {level_zh}（{score_pct}分）")

    except Exception as e:
        logger.warning("_build_trust_section failed: %s", e)
        items.append("信任数据获取失败")

    return {"title": "各角色信任等级", "items": items}


async def _build_task_section(user_id: int) -> Dict[str, Any]:
    """用户名下所有 workspace 的任务待完成 vs 已完成统计。"""
    items: List[str] = []
    try:
        from src.database import get_db
        db = await get_db()

        # 先拿到用户的所有 workspace id
        ws_rows = await db.execute_fetchall(
            "SELECT id FROM workspaces WHERE user_id = ?",
            (user_id,),
        )
        if not ws_rows:
            items.append("当前无工作区")
            return {"title": "任务概览", "items": items}

        ws_ids = tuple(r["id"] for r in ws_rows)

        # 用 IN 子句统计（aiosqlite 需手动展开占位符）
        placeholders = ",".join("?" * len(ws_ids))
        rows = await db.execute_fetchall(
            f"""SELECT status, COUNT(*) AS cnt
                FROM workspace_tasks
                WHERE workspace_id IN ({placeholders})
                GROUP BY status""",
            ws_ids,
        )

        status_counts: Dict[str, int] = {}
        for r in (rows or []):
            status_counts[r["status"]] = int(r["cnt"])

        pending_cnt = sum(status_counts.get(s, 0) for s in _PENDING_STATUSES)
        done_cnt = sum(status_counts.get(s, 0) for s in _DONE_STATUSES)
        cancelled_cnt = status_counts.get("cancelled", 0)
        total = sum(status_counts.values())

        if total == 0:
            items.append("当前无任务记录")
        else:
            items.append(f"共 {total} 个任务（跨 {len(ws_ids)} 个工作区）")
            items.append(f"待处理 / 进行中 / 阻塞：{pending_cnt} 个")
            items.append(f"已完成：{done_cnt} 个")
            if cancelled_cnt:
                items.append(f"已取消：{cancelled_cnt} 个")
            if pending_cnt > 0:
                items.append("⚠️  有待处理任务，请及时跟进")

    except Exception as e:
        logger.warning("_build_task_section failed: %s", e)
        items.append("任务数据获取失败")

    return {"title": "任务概览", "items": items}


async def _build_learning_section() -> Dict[str, Any]:
    """近3天新增学习记忆亮点，优先展示成功模式与盲点。"""
    items: List[str] = []
    try:
        from src.database import get_db
        db = await get_db()

        rows = await db.execute_fetchall(
            """SELECT role, category, content, confidence
               FROM learnings
               WHERE created_at >= datetime('now', '-3 days')
               ORDER BY confidence DESC, created_at DESC
               LIMIT 8""",
            (),
        )
        if not rows:
            items.append("近3天暂无新增学习记忆")
        else:
            items.append(f"近3天新增 {len(rows)} 条学习记忆")
            for r in rows[:5]:
                cat_zh = _LEARNING_CATEGORY_ZH.get(r["category"], r["category"])
                conf_pct = round(float(r["confidence"]) * 100)
                # content 截断展示
                content_short = r["content"][:60] + ("…" if len(r["content"]) > 60 else "")
                items.append(f"[{r['role']}·{cat_zh}·置信{conf_pct}%] {content_short}")

    except Exception as e:
        logger.warning("_build_learning_section failed: %s", e)
        items.append("学习记忆数据获取失败")

    return {"title": "学习亮点（近3天）", "items": items}


# ═══════════════════════════════════════════════════════════════════════════
# 通知 & 告警摘要
# ═══════════════════════════════════════════════════════════════════════════

async def get_notifications(user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    """
    从 background_events 表获取最近的后台事件作为通知列表。

    Parameters
    ----------
    user_id : int
        目标用户 ID。
    limit : int
        返回条数上限，默认 20，最大 100。

    Returns
    -------
    list[dict]
        按时间倒序排列的通知列表，每项含 id/event_type/data/consumed/created_at。
    """
    limit = max(1, min(limit, 100))
    try:
        from src.database import get_db
        db = await get_db()

        rows = await db.execute_fetchall(
            """SELECT id, event_type, data, consumed, created_at
               FROM background_events
               WHERE user_id = ?
               ORDER BY created_at DESC
               LIMIT ?""",
            (user_id, limit),
        )
        notifications = []
        for r in (rows or []):
            try:
                data_obj = json.loads(r["data"] or "{}")
            except (json.JSONDecodeError, TypeError):
                data_obj = {}

            notifications.append({
                "id": r["id"],
                "event_type": r["event_type"],
                "data": data_obj,
                "consumed": bool(r["consumed"]),
                "created_at": r["created_at"],
            })
        return notifications

    except Exception as e:
        logger.warning("get_notifications(%d) failed: %s", user_id, e)
        return []


async def get_alert_summary(user_id: int) -> Dict[str, Any]:
    """
    按严重程度与类型汇总活跃告警数量。

    Parameters
    ----------
    user_id : int
        目标用户 ID（同时包含无 user_id 的全局告警）。

    Returns
    -------
    dict
        {
            "total": int,
            "by_severity": {"critical": int, "warning": int, "info": int},
            "by_type": {"emotion": int, "urgency": int, ...},
            "has_critical": bool,
        }
    """
    by_severity: Dict[str, int] = {"critical": 0, "warning": 0, "info": 0}
    by_type: Dict[str, int] = {}

    try:
        from src.database import get_db
        db = await get_db()

        rows = await db.execute_fetchall(
            """SELECT alert_type, severity, COUNT(*) AS cnt
               FROM alerts
               WHERE status = 'active'
                 AND (user_id IS NULL OR user_id = ?)
               GROUP BY alert_type, severity""",
            (user_id,),
        )
        for r in (rows or []):
            sev = r["severity"]
            atype = r["alert_type"]
            cnt = int(r["cnt"])

            if sev in by_severity:
                by_severity[sev] += cnt
            else:
                # 未知严重程度归入 info
                by_severity["info"] += cnt

            by_type[atype] = by_type.get(atype, 0) + cnt

    except Exception as e:
        logger.warning("get_alert_summary(%d) failed: %s", user_id, e)

    total = sum(by_severity.values())
    return {
        "total": total,
        "by_severity": by_severity,
        "by_type": by_type,
        "has_critical": by_severity["critical"] > 0,
    }
