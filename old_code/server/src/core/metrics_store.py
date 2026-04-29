"""
店铺指标查询引擎 — 从 store_metrics 表提取结构化数据。

所有方法均为异步，供 chat_pipeline、skills、autopilot 使用。
当 store_metrics 中无数据时返回空结构（不编造数字）。

公开 API:
    get_summary(user_id, days=30)               → Dict  整体汇总
    get_time_series(user_id, metric, ...)        → List  时序数组
    detect_anomalies(user_id, days=14)           → List  异常点
    compare_periods(user_id, metric, days=7)     → Dict  环比数据
    format_for_prompt(summary)                   → str   适合注入 prompt 的文本
    has_data(user_id)                            → bool  是否有真实数据
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_SUM_KEYS = {"gmv", "orders", "uv", "pv", "new_buyers", "ad_spend"}
_AVG_KEYS = {"conversion_rate", "avg_order_value", "refund_rate", "ad_roi", "ctr", "cpc"}

_METRIC_DISPLAY = {
    "gmv": "GMV",
    "orders": "订单数",
    "uv": "访客数(UV)",
    "pv": "浏览量(PV)",
    "conversion_rate": "转化率",
    "avg_order_value": "客单价",
    "new_buyers": "新买家",
    "refund_rate": "退款率",
    "ad_spend": "广告花费",
    "ad_roi": "广告ROI",
    "ctr": "点击率",
    "cpc": "CPC",
}

_PLATFORM_DISPLAY = {
    "taobao": "淘宝/天猫",
    "jd": "京东",
    "pdd": "拼多多",
    "douyin": "抖音",
    "general": "综合",
}


async def has_data(user_id: int) -> bool:
    """检查用户是否有真实指标数据。"""
    try:
        from src.database import get_db
        db = await get_db()
        row = await db.execute_fetchone(
            "SELECT 1 FROM store_metrics WHERE user_id = ? LIMIT 1",
            (user_id,),
        )
        return row is not None
    except Exception:
        return False


async def get_summary(user_id: int, days: int = 30) -> Dict[str, Any]:
    """
    获取最近 N 天各平台指标汇总。

    Returns
    -------
    {
      "has_data": bool,
      "days": int,
      "platforms": {
          "taobao": {"gmv": 12345, "orders": 200, "uv": 5000, "conversion_rate": 0.04, ...},
          ...
      },
      "totals": {"gmv": ..., "orders": ..., "uv": ...},
      "date_range": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"},
    }
    """
    try:
        from src.database import get_db
        db = await get_db()
        rows = await db.execute_fetchall(
            """
            SELECT platform, metric_key,
                   SUM(metric_value)  AS sum_val,
                   AVG(metric_value)  AS avg_val,
                   MIN(metric_date)   AS min_date,
                   MAX(metric_date)   AS max_date,
                   COUNT(DISTINCT metric_date) AS day_count
            FROM store_metrics
            WHERE user_id = ?
              AND metric_date >= date('now', ? || ' days')
            GROUP BY platform, metric_key
            """,
            (user_id, f"-{days}"),
        )
    except Exception as e:
        logger.warning(f"get_summary query failed: {e}")
        return {"has_data": False, "days": days, "platforms": {}, "totals": {}}

    if not rows:
        return {"has_data": False, "days": days, "platforms": {}, "totals": {}}

    platforms: Dict[str, Dict[str, float]] = {}
    date_range: Dict[str, str] = {}

    for row in rows:
        p = row["platform"]
        k = row["metric_key"]
        if p not in platforms:
            platforms[p] = {}
        platforms[p][k] = round(row["sum_val"] if k in _SUM_KEYS else row["avg_val"], 4)
        # 收集日期范围
        if "start" not in date_range or row["min_date"] < date_range["start"]:
            date_range["start"] = row["min_date"]
        if "end" not in date_range or row["max_date"] > date_range["end"]:
            date_range["end"] = row["max_date"]

    # 合计（仅 sum 类指标）
    totals: Dict[str, float] = {}
    for pdata in platforms.values():
        for k, v in pdata.items():
            if k in _SUM_KEYS:
                totals[k] = round(totals.get(k, 0) + v, 2)

    return {
        "has_data": True,
        "days": days,
        "platforms": platforms,
        "totals": totals,
        "date_range": date_range,
    }


async def get_time_series(
    user_id: int,
    metric: str,
    platform: Optional[str] = None,
    days: int = 30,
) -> List[Dict[str, Any]]:
    """
    获取某指标的每日时序数据。

    Returns [{date, value}, ...]  按日期升序
    """
    try:
        from src.database import get_db
        db = await get_db()
        params: list = [user_id, f"-{days}", metric]
        sql = """
            SELECT metric_date AS date,
                   SUM(metric_value) AS value
            FROM store_metrics
            WHERE user_id = ?
              AND metric_date >= date('now', ? || ' days')
              AND metric_key = ?
        """
        if platform:
            sql += " AND platform = ?"
            params.append(platform)
        sql += " GROUP BY metric_date ORDER BY metric_date ASC"
        rows = await db.execute_fetchall(sql, tuple(params))
        return [{"date": r["date"], "value": round(r["value"], 4)} for r in rows]
    except Exception as e:
        logger.warning(f"get_time_series failed: {e}")
        return []


async def detect_anomalies(user_id: int, days: int = 14) -> List[Dict[str, Any]]:
    """
    检测最近 days 天内的指标异常（与前一周期均值比较）。

    Returns list of {metric, platform, date, value, baseline, change_pct, direction}
    """
    anomalies = []
    try:
        from src.database import get_db
        db = await get_db()
        half = days // 2

        # 当前周期 vs 前一周期
        rows = await db.execute_fetchall(
            f"""
            SELECT platform, metric_key,
                   AVG(CASE WHEN metric_date >= date('now', '-{half} days') THEN metric_value END) AS cur_avg,
                   AVG(CASE WHEN metric_date < date('now', '-{half} days') THEN metric_value END) AS prev_avg
            FROM store_metrics
            WHERE user_id = ?
              AND metric_date >= date('now', '-{days} days')
              AND metric_key IN ('gmv', 'orders', 'uv', 'conversion_rate', 'refund_rate')
            GROUP BY platform, metric_key
            HAVING cur_avg IS NOT NULL AND prev_avg IS NOT NULL AND prev_avg > 0
            """,
            (user_id,),
        )
        for row in rows:
            cur = row["cur_avg"]
            prev = row["prev_avg"]
            change_pct = (cur - prev) / prev * 100
            # 超过 20% 变动才算异常
            if abs(change_pct) >= 20:
                anomalies.append({
                    "metric": row["metric_key"],
                    "metric_display": _METRIC_DISPLAY.get(row["metric_key"], row["metric_key"]),
                    "platform": row["platform"],
                    "platform_display": _PLATFORM_DISPLAY.get(row["platform"], row["platform"]),
                    "current_avg": round(cur, 4),
                    "baseline_avg": round(prev, 4),
                    "change_pct": round(change_pct, 1),
                    "direction": "上升" if change_pct > 0 else "下降",
                    "severity": "高" if abs(change_pct) >= 40 else "中",
                })
    except Exception as e:
        logger.warning(f"detect_anomalies failed: {e}")
    return sorted(anomalies, key=lambda x: abs(x["change_pct"]), reverse=True)


async def compare_periods(
    user_id: int,
    metric: str,
    current_days: int = 7,
    compare_days: int = 7,
) -> Dict[str, Any]:
    """
    环比：当前 current_days 天 vs 前 compare_days 天。

    Returns {"metric", "current", "previous", "change", "change_pct", "direction"}
    """
    try:
        from src.database import get_db
        db = await get_db()
        row = await db.execute_fetchone(
            f"""
            SELECT
                SUM(CASE WHEN metric_date >= date('now', '-{current_days} days')
                         THEN metric_value ELSE 0 END) AS cur,
                SUM(CASE WHEN metric_date < date('now', '-{current_days} days')
                         AND metric_date >= date('now', '-{current_days + compare_days} days')
                         THEN metric_value ELSE 0 END) AS prev
            FROM store_metrics
            WHERE user_id = ? AND metric_key = ?
            """,
            (user_id, metric),
        )
        if not row or row["prev"] in (None, 0):
            return {"metric": metric, "has_data": False}
        cur = row["cur"] or 0
        prev = row["prev"]
        change = cur - prev
        change_pct = change / prev * 100
        return {
            "metric": metric,
            "metric_display": _METRIC_DISPLAY.get(metric, metric),
            "current": round(cur, 2),
            "previous": round(prev, 2),
            "change": round(change, 2),
            "change_pct": round(change_pct, 1),
            "direction": "上升" if change > 0 else "下降",
            "has_data": True,
        }
    except Exception as e:
        logger.warning(f"compare_periods failed: {e}")
        return {"metric": metric, "has_data": False}


def format_for_prompt(summary: Dict[str, Any], anomalies: Optional[List] = None) -> str:
    """
    将指标汇总格式化为适合注入 system prompt 的紧凑文本。

    Example output:
        [真实店铺数据 · 最近30天]
        淘宝/天猫: GMV ¥45,320 | 订单 312 | UV 8,500 | 转化率 3.67%
        拼多多: GMV ¥12,100 | 订单 98 | UV 3,200 | 转化率 3.06%
        合计: GMV ¥57,420 | 订单 410
        ⚠ 异常: 拼多多转化率近7天下降32%（需关注）
    """
    if not summary.get("has_data"):
        return ""

    lines = [f"[真实店铺数据 · 最近{summary['days']}天]"]

    platforms = summary.get("platforms", {})
    for platform, pdata in platforms.items():
        parts = []
        if "gmv" in pdata:
            parts.append(f"GMV ¥{pdata['gmv']:,.0f}")
        if "orders" in pdata:
            parts.append(f"订单 {pdata['orders']:.0f}")
        if "uv" in pdata:
            parts.append(f"UV {pdata['uv']:.0f}")
        if "conversion_rate" in pdata:
            parts.append(f"转化率 {pdata['conversion_rate']*100:.2f}%")
        if "avg_order_value" in pdata:
            parts.append(f"客单价 ¥{pdata['avg_order_value']:.0f}")
        if "ad_spend" in pdata:
            parts.append(f"广告 ¥{pdata['ad_spend']:,.0f}")
        if parts:
            display = _PLATFORM_DISPLAY.get(platform, platform)
            lines.append(f"{display}: " + " | ".join(parts))

    totals = summary.get("totals", {})
    if totals:
        total_parts = []
        if "gmv" in totals:
            total_parts.append(f"GMV ¥{totals['gmv']:,.0f}")
        if "orders" in totals:
            total_parts.append(f"订单 {totals['orders']:.0f}")
        if "uv" in totals:
            total_parts.append(f"UV {totals['uv']:.0f}")
        if total_parts and len(platforms) > 1:
            lines.append("合计: " + " | ".join(total_parts))

    if anomalies:
        for a in anomalies[:3]:
            sign = "+" if a["direction"] == "上升" else ""
            lines.append(
                f"⚠ {a['platform_display']}{a['metric_display']} "
                f"{sign}{a['change_pct']}%（{a['severity']}风险）"
            )

    return "\n".join(lines)
