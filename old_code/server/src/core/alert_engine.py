"""
告警引擎 — 6种告警类型 + 去重 + 冷却，0 LLM 调用。

告警类型：emotion, urgency, quality, trust, anomaly, compliance
去重：同一 alert_type + role 5分钟内只触发一次
冷却：同一 dedup_key 10分钟内不重复触发
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════
# 数据类
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Alert:
    alert_type: str       # emotion | urgency | quality | trust | anomaly | compliance
    severity: str         # info | warning | critical
    message: str
    role: str
    dedup_key: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "alert_type": self.alert_type,
            "severity": self.severity,
            "message": self.message,
            "role": self.role,
            "dedup_key": self.dedup_key,
        }


# ═══════════════════════════════════════════════════════════════════════════
# 关键词配置
# ═══════════════════════════════════════════════════════════════════════════

EMOTION_KEYWORDS: Dict[str, str] = {
    # keyword -> severity
    "愤怒": "critical",
    "生气": "warning",
    "焦虑": "warning",
    "急": "info",
    "崩溃": "critical",
    "太差": "warning",
    "垃圾": "critical",
    "投诉": "warning",
    "差评": "warning",
    "不满意": "warning",
    "失望": "warning",
    "恼火": "warning",
}

URGENCY_KEYWORDS: Dict[str, str] = {
    "紧急": "critical",
    "立即": "critical",
    "马上": "warning",
    "deadline": "critical",
    "bug": "warning",
    "故障": "critical",
    "宕机": "critical",
    "超时": "warning",
    "报错": "warning",
    "异常": "info",
}

ANOMALY_KEYWORDS: List[str] = [
    "ROI下降", "转化率暴跌", "流量骤降", "成本暴涨", "GMV下滑",
    "订单骤减", "退货率飙升", "差评激增", "库存告急", "预算超支",
    "DSR下降", "搜索排名下跌",
]

COMPLIANCE_KEYWORDS: Dict[str, str] = {
    "刷单": "critical",
    "虚假宣传": "critical",
    "违规": "warning",
    "侵权": "critical",
    "假货": "critical",
    "诈骗": "critical",
    "灰色": "warning",
    "黑产": "critical",
    "违法": "critical",
    "处罚": "warning",
    "扣分": "info",
    "封店": "critical",
}

# ═══════════════════════════════════════════════════════════════════════════
# 去重状态（进程内缓存）
# ═══════════════════════════════════════════════════════════════════════════

# {dedup_key: timestamp}
_dedup_cache: Dict[str, float] = {}

DEDUP_WINDOW = 300.0      # 同类型+角色去重：5分钟
COOLDOWN_WINDOW = 600.0   # 同dedup_key冷却：10分钟


def _check_dedup(dedup_key: str) -> bool:
    """检查是否在冷却期内。返回 True 表示应该跳过。"""
    now = time.monotonic()

    # 清理过期条目
    expired = [k for k, t in _dedup_cache.items() if now - t > COOLDOWN_WINDOW]
    for k in expired:
        del _dedup_cache[k]

    if dedup_key in _dedup_cache:
        elapsed = now - _dedup_cache[dedup_key]
        if elapsed < COOLDOWN_WINDOW:
            return True  # 在冷却期，跳过

    return False


def _record_dedup(dedup_key: str) -> None:
    """记录告警触发时间。"""
    _dedup_cache[dedup_key] = time.monotonic()


# ═══════════════════════════════════════════════════════════════════════════
# 扫描函数
# ═══════════════════════════════════════════════════════════════════════════

def scan_alerts(
    message: str,
    reply: str,
    role: str,
    quality_score: float,
    trust_level: str,
) -> List[Alert]:
    """
    扫描消息和回复，生成告警列表（已去重）。

    Parameters
    ----------
    message : str
        用户消息。
    reply : str
        LLM 回复。
    role : str
        当前角色。
    quality_score : float
        本轮质量评分。
    trust_level : str
        当前信任等级。

    Returns
    -------
    list[Alert]
        去重后的告警列表。
    """
    alerts: List[Alert] = []
    combined = message + " " + reply

    # 1. 情绪告警
    for kw, severity in EMOTION_KEYWORDS.items():
        if kw in message:  # 只检测用户消息中的情绪
            dedup_key = f"emotion:{role}:{kw}"
            if not _check_dedup(dedup_key):
                alerts.append(Alert(
                    alert_type="emotion",
                    severity=severity,
                    message=f"检测到用户情绪关键词「{kw}」",
                    role=role,
                    dedup_key=dedup_key,
                ))
                _record_dedup(dedup_key)
                break  # 每种类型只取一个

    # 2. 紧急告警
    for kw, severity in URGENCY_KEYWORDS.items():
        if kw.lower() in combined.lower():
            dedup_key = f"urgency:{role}:{kw}"
            if not _check_dedup(dedup_key):
                alerts.append(Alert(
                    alert_type="urgency",
                    severity=severity,
                    message=f"检测到紧急关键词「{kw}」",
                    role=role,
                    dedup_key=dedup_key,
                ))
                _record_dedup(dedup_key)
                break

    # 3. 质量告警
    if quality_score < 0.5:
        dedup_key = f"quality:{role}"
        if not _check_dedup(dedup_key):
            alerts.append(Alert(
                alert_type="quality",
                severity="warning" if quality_score >= 0.3 else "critical",
                message=f"质量评分过低：{quality_score:.2f}",
                role=role,
                dedup_key=dedup_key,
            ))
            _record_dedup(dedup_key)

    # 4. 信任告警
    if trust_level in ("LOW", "NONE"):
        dedup_key = f"trust:{role}:{trust_level}"
        if not _check_dedup(dedup_key):
            alerts.append(Alert(
                alert_type="trust",
                severity="warning" if trust_level == "LOW" else "critical",
                message=f"角色{role}信任等级为{trust_level}",
                role=role,
                dedup_key=dedup_key,
            ))
            _record_dedup(dedup_key)

    # 5. 业务异常告警
    for kw in ANOMALY_KEYWORDS:
        if kw in combined:
            dedup_key = f"anomaly:{role}:{kw}"
            if not _check_dedup(dedup_key):
                alerts.append(Alert(
                    alert_type="anomaly",
                    severity="warning",
                    message=f"检测到业务异常关键词「{kw}」",
                    role=role,
                    dedup_key=dedup_key,
                ))
                _record_dedup(dedup_key)
                break

    # 6. 合规告警
    for kw, severity in COMPLIANCE_KEYWORDS.items():
        if kw in combined:
            dedup_key = f"compliance:{role}:{kw}"
            if not _check_dedup(dedup_key):
                alerts.append(Alert(
                    alert_type="compliance",
                    severity=severity,
                    message=f"检测到合规风险关键词「{kw}」",
                    role=role,
                    dedup_key=dedup_key,
                ))
                _record_dedup(dedup_key)
                break

    return alerts


# ═══════════════════════════════════════════════════════════════════════════
# 持久化
# ═══════════════════════════════════════════════════════════════════════════

async def save_alerts(alerts: List[Alert]) -> None:
    """将告警保存到数据库。"""
    if not alerts:
        return

    try:
        from src.database import get_db
        db = await get_db()
        for alert in alerts:
            await db.execute(
                """INSERT INTO alerts (alert_type, severity, message, role, dedup_key)
                   VALUES (?, ?, ?, ?, ?)""",
                (alert.alert_type, alert.severity, alert.message,
                 alert.role, alert.dedup_key),
            )
        await db.commit()
    except Exception as e:
        logger.warning("Failed to save alerts: %s", e)
