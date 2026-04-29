"""
主动智能引擎 — 0 LLM调用，纯规则驱动的主动通知和建议。

场景:
1. 产品素材缺失检测 — 根据生命周期阶段检测缺失素材
2. 素材过期提醒 — 超过7天未更新
3. 活动倒计时 — 紧迫截止日期提醒
4. 数据异常检测 — 销售/退货/投诉异常，自适应阈值
5. 跨Agent级联通知 — 异常自动触发相关角色关注
6. 任务超期提醒 — 按优先级的超期天数限制
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

import aiosqlite

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Lifecycle stage -> required material types
LIFECYCLE_MATERIALS: Dict[str, List[str]] = {
    "选品中": ["选品报告"],
    "上架中": ["选品报告", "商品文案", "主图"],
    "运营中": ["选品报告", "商品文案", "主图", "详情页"],
}

# Priority (integer in DB) -> max allowed days before considered overdue
PRIORITY_OVERDUE_DAYS: Dict[int, int] = {
    4: 1,   # urgent
    3: 3,   # high
    2: 5,   # medium
    1: 7,   # normal
    0: 14,  # low
}

# Material type -> suggested agent role for remediation
MATERIAL_ROLE_MAP: Dict[str, str] = {
    "选品报告": "data",
    "商品文案": "ops",
    "主图": "design",
    "详情页": "design",
    "视频脚本": "creative",
}

# Anomaly type -> cascade target roles + message template
CASCADE_RULES: Dict[str, List[Dict[str, str]]] = {
    "sales_anomaly": [
        {"role": "ops", "msg": "销售数据异常，建议检查推广投放和库存"},
        {"role": "accounting", "msg": "销售波动较大，建议关注现金流和利润影响"},
    ],
    "return_anomaly": [
        {"role": "service", "msg": "退货率异常升高，建议排查客诉和质量问题"},
        {"role": "ops", "msg": "退货增多，建议检查选品策略和供应商质量"},
    ],
    "complaint_anomaly": [
        {"role": "service", "msg": "投诉量异常，建议启动危机响应预案"},
        {"role": "ops", "msg": "客诉集中爆发，建议排查是否为系统性问题"},
    ],
}

# Stale material threshold in days
STALE_DAYS = 7

# Data anomaly: how many days of history to compute baseline
ANOMALY_BASELINE_DAYS = 56
# Recent window for comparison
ANOMALY_RECENT_DAYS = 7
# Default Z-score threshold for anomaly detection
ANOMALY_Z_THRESHOLD = 2.0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def generate_notifications(user_id: int) -> List[Dict[str, Any]]:
    """Generate all proactive notifications for a user.

    Returns a list of notification dicts sorted by priority (high first).
    Each dict has keys: type, priority, message, action_role, action_description.
    """
    notifications: List[Dict[str, Any]] = []
    try:
        from src.database import get_db

        db = await get_db()

        # 1. Product material gaps
        notifications.extend(await _check_material_gaps(db, user_id))
        # 2. Stale materials
        notifications.extend(await _check_stale_materials(db, user_id))
        # 3. Campaign deadlines
        notifications.extend(await _check_campaign_deadlines(db, user_id))
        # 4. Data anomalies + cascade
        anomaly_notifs = await _check_data_anomalies(db, user_id)
        notifications.extend(anomaly_notifs)
        # 5. Overdue tasks
        notifications.extend(await _check_overdue_tasks(db, user_id))

    except Exception as e:
        logger.warning("Proactive engine error: %s", e)

    # Sort: high > medium > low
    _priority_order = {"high": 0, "medium": 1, "low": 2}
    notifications.sort(key=lambda n: _priority_order.get(n.get("priority", "low"), 2))
    return notifications


# ---------------------------------------------------------------------------
# 1. Material gap detection
# ---------------------------------------------------------------------------

async def _check_material_gaps(
    db: aiosqlite.Connection, user_id: int
) -> List[Dict[str, Any]]:
    """Detect missing material types for products based on lifecycle stage."""
    results: List[Dict[str, Any]] = []

    rows = await db.execute_fetchall(
        "SELECT id, name, lifecycle_status FROM products WHERE user_id = ?",
        (user_id,),
    )

    for row in rows:
        product_id = row[0]
        product_name = row[1]
        lifecycle = row[2] or "draft"

        required = LIFECYCLE_MATERIALS.get(lifecycle)
        if not required:
            continue

        # Fetch existing material types for this product
        mat_rows = await db.execute_fetchall(
            "SELECT DISTINCT material_type FROM product_materials WHERE product_id = ?",
            (product_id,),
        )
        existing_types = {r[0] for r in mat_rows}

        for mat_type in required:
            if mat_type not in existing_types:
                role = MATERIAL_ROLE_MAP.get(mat_type, "ops")
                results.append({
                    "type": "material_gap",
                    "priority": "high" if lifecycle == "运营中" else "medium",
                    "message": (
                        f"产品「{product_name}」处于{lifecycle}阶段，"
                        f"缺少必需素材：{mat_type}"
                    ),
                    "action_role": role,
                    "action_description": f"请为产品「{product_name}」创建{mat_type}",
                })

    return results


# ---------------------------------------------------------------------------
# 2. Stale material detection
# ---------------------------------------------------------------------------

async def _check_stale_materials(
    db: aiosqlite.Connection, user_id: int
) -> List[Dict[str, Any]]:
    """Detect materials that haven't been updated in more than STALE_DAYS days."""
    results: List[Dict[str, Any]] = []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=STALE_DAYS)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    rows = await db.execute_fetchall(
        """
        SELECT pm.id, pm.material_type, pm.title, pm.created_at,
               p.name AS product_name
        FROM product_materials pm
        JOIN products p ON pm.product_id = p.id
        WHERE p.user_id = ?
          AND pm.created_at < ?
        ORDER BY pm.created_at ASC
        LIMIT 50
        """,
        (user_id, cutoff),
    )

    for row in rows:
        mat_type = row[1]
        title = row[2] or mat_type
        created_at_str = row[3]
        product_name = row[4]

        # Calculate days since last update
        try:
            created_dt = datetime.strptime(created_at_str[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            days_old = (datetime.now(timezone.utc) - created_dt).days
        except (ValueError, TypeError):
            days_old = STALE_DAYS + 1

        role = MATERIAL_ROLE_MAP.get(mat_type, "ops")
        results.append({
            "type": "stale_material",
            "priority": "high" if days_old > 14 else "medium",
            "message": (
                f"产品「{product_name}」的素材「{title}」已{days_old}天未更新"
            ),
            "action_role": role,
            "action_description": f"请检查并更新素材「{title}」",
        })

    return results


# ---------------------------------------------------------------------------
# 3. Campaign deadline urgency
# ---------------------------------------------------------------------------

async def _check_campaign_deadlines(
    db: aiosqlite.Connection, user_id: int
) -> List[Dict[str, Any]]:
    """Check campaigns with approaching or overdue deadlines."""
    results: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc)
    today_str = now.strftime("%Y-%m-%d")

    # Fetch active / draft campaigns with an end_date
    rows = await db.execute_fetchall(
        """
        SELECT id, name, end_date, status
        FROM campaigns
        WHERE user_id = ?
          AND status IN ('draft', 'active', 'scheduled')
          AND end_date IS NOT NULL
          AND end_date != ''
        ORDER BY end_date ASC
        LIMIT 50
        """,
        (user_id,),
    )

    for row in rows:
        campaign_name = row[1]
        end_date_str = row[2]
        status = row[3]

        try:
            # end_date may be "YYYY-MM-DD" or "YYYY-MM-DD HH:MM:SS"
            end_dt = datetime.strptime(end_date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue

        delta_days = (end_dt - now).days

        if delta_days < 0:
            # Overdue
            results.append({
                "type": "campaign_overdue",
                "priority": "high",
                "message": (
                    f"活动「{campaign_name}」已过期{abs(delta_days)}天，"
                    f"状态仍为{status}"
                ),
                "action_role": "ops",
                "action_description": f"请结束或更新活动「{campaign_name}」",
            })
        elif delta_days < 3:
            results.append({
                "type": "campaign_urgent",
                "priority": "high",
                "message": (
                    f"活动「{campaign_name}」将在{delta_days}天内截止，请抓紧推进"
                ),
                "action_role": "ops",
                "action_description": f"确认活动「{campaign_name}」的准备工作已完成",
            })
        elif delta_days < 7:
            results.append({
                "type": "campaign_approaching",
                "priority": "medium",
                "message": (
                    f"活动「{campaign_name}」将在{delta_days}天后截止"
                ),
                "action_role": "ops",
                "action_description": f"提前检查活动「{campaign_name}」的素材和预算",
            })

    return results


# ---------------------------------------------------------------------------
# 4. Data anomaly detection (adaptive thresholds) + cascade
# ---------------------------------------------------------------------------

async def _check_data_anomalies(
    db: aiosqlite.Connection, user_id: int
) -> List[Dict[str, Any]]:
    """Detect anomalies in sales, returns, and complaints using adaptive
    thresholds based on historical standard deviation.

    Uses the metrics table. metric_type identifies the category (e.g.
    'sales', 'returns', 'complaints') and metric_value stores the numeric
    value. We compare the recent ANOMALY_RECENT_DAYS average against the
    baseline of ANOMALY_BASELINE_DAYS to detect spikes.
    """
    results: List[Dict[str, Any]] = []

    checks = [
        {
            "metric_type": "sales",
            "anomaly_key": "sales_anomaly",
            "label": "销售额",
            "direction": "both",  # spike or drop
        },
        {
            "metric_type": "returns",
            "anomaly_key": "return_anomaly",
            "label": "退货率",
            "direction": "up",  # only spike matters
        },
        {
            "metric_type": "complaints",
            "anomaly_key": "complaint_anomaly",
            "label": "投诉量",
            "direction": "up",
        },
    ]

    now = datetime.now(timezone.utc)
    baseline_start = (now - timedelta(days=ANOMALY_BASELINE_DAYS)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    recent_start = (now - timedelta(days=ANOMALY_RECENT_DAYS)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    for check in checks:
        mt = check["metric_type"]

        # Baseline stats (mean + stddev) over last ANOMALY_BASELINE_DAYS
        baseline_row = await db.execute_fetchall(
            """
            SELECT AVG(metric_value), COUNT(metric_value),
                   TOTAL(metric_value * metric_value) AS sum_sq,
                   TOTAL(metric_value) AS total_val
            FROM metrics
            WHERE metric_type = ?
              AND created_at >= ?
            """,
            (mt, baseline_start),
        )

        if not baseline_row or not baseline_row[0]:
            continue

        avg_val = baseline_row[0][0]
        count = baseline_row[0][1]
        if avg_val is None or count < 2:
            continue

        sum_sq = baseline_row[0][2] or 0.0
        total_val = baseline_row[0][3] or 0.0
        mean = total_val / count
        # Population variance then stddev
        variance = (sum_sq / count) - (mean * mean)
        stddev = variance ** 0.5 if variance > 0 else 0.0

        # Recent average
        recent_row = await db.execute_fetchall(
            """
            SELECT AVG(metric_value), COUNT(metric_value)
            FROM metrics
            WHERE metric_type = ?
              AND created_at >= ?
            """,
            (mt, recent_start),
        )

        if not recent_row or not recent_row[0] or recent_row[0][0] is None:
            continue

        recent_avg = recent_row[0][0]
        recent_count = recent_row[0][1]
        if recent_count < 1:
            continue

        # Compute z-score
        if stddev < 1e-9:
            # No variance -> skip (or flag if recent differs from mean)
            if abs(recent_avg - mean) > 1e-9 and mean > 0:
                z_score = 3.0  # artificial high z-score
            else:
                continue
        else:
            z_score = (recent_avg - mean) / stddev

        is_anomaly = False
        direction_label = ""

        if check["direction"] == "up" and z_score > ANOMALY_Z_THRESHOLD:
            is_anomaly = True
            direction_label = "异常升高"
        elif check["direction"] == "both":
            if z_score > ANOMALY_Z_THRESHOLD:
                is_anomaly = True
                direction_label = "异常升高"
            elif z_score < -ANOMALY_Z_THRESHOLD:
                is_anomaly = True
                direction_label = "异常下降"

        if not is_anomaly:
            continue

        # Calculate percentage change
        if mean > 0:
            pct_change = ((recent_avg - mean) / mean) * 100
            pct_str = f"{pct_change:+.1f}%"
        else:
            pct_str = "N/A"

        # Primary anomaly notification
        results.append({
            "type": check["anomaly_key"],
            "priority": "high",
            "message": (
                f"{check['label']}{direction_label}：近{ANOMALY_RECENT_DAYS}天"
                f"均值较基线变化{pct_str}（z={z_score:.1f}）"
            ),
            "action_role": "data",
            "action_description": f"请分析{check['label']}波动原因并出具报告",
        })

        # 5. Cross-agent cascade notifications
        cascade_targets = CASCADE_RULES.get(check["anomaly_key"], [])
        for target in cascade_targets:
            results.append({
                "type": "cascade_" + check["anomaly_key"],
                "priority": "medium",
                "message": target["msg"],
                "action_role": target["role"],
                "action_description": target["msg"],
            })

    return results


# ---------------------------------------------------------------------------
# 6. Overdue task detection
# ---------------------------------------------------------------------------

async def _check_overdue_tasks(
    db: aiosqlite.Connection, user_id: int
) -> List[Dict[str, Any]]:
    """Check workspace_tasks that are overdue based on their priority level.

    A task is overdue when it has been in 'pending' or 'in_progress' status
    longer than the allowed number of days for its priority.
    """
    results: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    # We join workspace_tasks -> workspaces to filter by user_id
    rows = await db.execute_fetchall(
        """
        SELECT wt.id, wt.title, wt.owner_role, wt.priority, wt.status,
               wt.created_at, wt.updated_at
        FROM workspace_tasks wt
        JOIN workspaces w ON wt.workspace_id = w.id
        WHERE w.user_id = ?
          AND wt.status IN ('pending', 'in_progress')
        ORDER BY wt.priority DESC, wt.created_at ASC
        LIMIT 100
        """,
        (user_id,),
    )

    for row in rows:
        task_id = row[0]
        title = row[1]
        owner_role = row[2] or "ops"
        priority = row[3] if row[3] is not None else 0
        status = row[4]
        created_at_str = row[5]

        # Determine max allowed days for this priority
        max_days = PRIORITY_OVERDUE_DAYS.get(priority, 14)

        try:
            created_dt = datetime.strptime(created_at_str[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue

        age_days = (now - created_dt).days
        if age_days <= max_days:
            continue

        overdue_days = age_days - max_days

        if overdue_days > 7:
            prio_label = "high"
        elif overdue_days > 3:
            prio_label = "medium"
        else:
            prio_label = "low"

        results.append({
            "type": "overdue_task",
            "priority": prio_label,
            "message": (
                f"任务「{title}」已超期{overdue_days}天"
                f"（状态：{status}，允许{max_days}天）"
            ),
            "action_role": owner_role,
            "action_description": f"请尽快处理或更新任务「{title}」的进度",
        })

    return results
