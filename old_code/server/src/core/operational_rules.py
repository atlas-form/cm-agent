"""
实时运营规则触发引擎 — 基于真实指标数据的5条告警规则

规则一览：
  1. cvr_drop_20pct  — 转化率下降>20%   → 触发漏斗分析 + 智能定价
  2. low_inventory   — 库存低于安全线    → 补货告警
  3. ad_roi_low      — 广告ROI < 1.5     → 渠道策略审视
  4. refund_spike    — 退款率 > 5%       → DSR诊断
  5. gmv_cliff       — GMV断崖 > 30%    → 全Agent联合诊断

每条规则包含：触发条件（Trigger）、建议执行的技能链、派发的角色列表。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# 规则定义
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class RuleTrigger:
    rule_id: str                    # 唯一标识
    name: str                       # 可读名称
    severity: str                   # info | warning | critical
    description: str                # 触发说明（含具体数值）
    recommended_skills: List[str]   # 建议调用的技能名（按序）
    dispatch_roles: List[str]       # 建议派发的Agent角色
    metric_snapshot: Dict[str, Any] # 触发时的指标快照
    triggered_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "name": self.name,
            "severity": self.severity,
            "description": self.description,
            "recommended_skills": self.recommended_skills,
            "dispatch_roles": self.dispatch_roles,
            "metric_snapshot": self.metric_snapshot,
        }


# ──────────────────────────────────────────────────────────────────────────────
# 去重 / 冷却（内存级，重启清零）
# ──────────────────────────────────────────────────────────────────────────────

_COOLDOWN_SECONDS = 600  # 同一规则10分钟内不重复触发
_last_triggered: Dict[str, float] = {}


def _is_cooled_down(rule_id: str) -> bool:
    last = _last_triggered.get(rule_id, 0)
    return (time.time() - last) >= _COOLDOWN_SECONDS


def _mark_triggered(rule_id: str) -> None:
    _last_triggered[rule_id] = time.time()


# ──────────────────────────────────────────────────────────────────────────────
# 核心评估函数
# ──────────────────────────────────────────────────────────────────────────────

async def evaluate_rules(user_id: int) -> List[RuleTrigger]:
    """
    加载用户近期指标，逐条评估5条规则，返回已触发的 RuleTrigger 列表。

    Parameters
    ----------
    user_id : int

    Returns
    -------
    List[RuleTrigger]
        已触发（且不在冷却期内）的规则列表，按严重程度排序。
    """
    try:
        from src.core.metrics_store import get_summary, compare_periods
        summary_7d = await get_summary(user_id, days=7)
        summary_14d = await get_summary(user_id, days=14)
        period_cmp = await compare_periods(user_id, "gmv", current_days=7, compare_days=7)
    except Exception as e:
        logger.warning("operational_rules: metrics load failed: %s", e)
        return []

    if not summary_7d.get("has_data"):
        return []

    triggered: List[RuleTrigger] = []
    totals_7 = summary_7d.get("totals", {})
    totals_14 = summary_14d.get("totals", {}) if summary_14d.get("has_data") else {}

    # ── 规则1：转化率下降 >20% ──────────────────────────────────────────
    rule_id = "cvr_drop_20pct"
    if _is_cooled_down(rule_id):
        cvr_now = totals_7.get("conversion_rate", 0)
        cvr_prev = totals_14.get("conversion_rate", 0)
        if cvr_now > 0 and cvr_prev > 0:
            cvr_change = (cvr_now - cvr_prev) / cvr_prev
            if cvr_change <= -0.20:
                _mark_triggered(rule_id)
                triggered.append(RuleTrigger(
                    rule_id=rule_id,
                    name="转化率异常下降",
                    severity="warning",
                    description=(
                        f"近7天转化率 {cvr_now*100:.2f}%，较前14天 {cvr_prev*100:.2f}% "
                        f"下降 {abs(cvr_change)*100:.1f}%（超过20%阈值）"
                    ),
                    recommended_skills=["data_funnel_analysis", "ops_smart_pricing"],
                    dispatch_roles=["data", "ops"],
                    metric_snapshot={
                        "cvr_now": round(cvr_now, 4),
                        "cvr_prev": round(cvr_prev, 4),
                        "change_pct": round(cvr_change * 100, 2),
                    },
                ))

    # ── 规则2：库存低于安全库存（UV/转化>库存缓冲）──────────────────────
    rule_id = "low_inventory"
    if _is_cooled_down(rule_id):
        uv = totals_7.get("uv", 0)
        orders_7 = totals_7.get("orders", 0)
        # 估算日均销量，若 UV 提示的需求远高于当前节奏则视为低库存风险
        # 这里用 orders_7 > 0 且 uv/orders < 10（高转化高销速）作为代理指标
        if orders_7 > 0 and uv > 0:
            uv_per_order = uv / orders_7
            if uv_per_order < 8:  # 高转化 = 快速消耗库存风险
                _mark_triggered(rule_id)
                triggered.append(RuleTrigger(
                    rule_id=rule_id,
                    name="高销速库存预警",
                    severity="info",
                    description=(
                        f"近7天 UV/单量比 {uv_per_order:.1f}，显示高转化销速；"
                        "建议确认库存充裕度，防止缺货断链。"
                    ),
                    recommended_skills=["ops_execution_plan"],
                    dispatch_roles=["ops"],
                    metric_snapshot={
                        "uv": int(uv),
                        "orders_7d": int(orders_7),
                        "uv_per_order": round(uv_per_order, 1),
                    },
                ))

    # ── 规则3：广告ROI < 1.5 ─────────────────────────────────────────────
    rule_id = "ad_roi_low"
    if _is_cooled_down(rule_id):
        ad_spend = totals_7.get("ad_spend", 0)
        gmv_7 = totals_7.get("gmv", 0)
        if ad_spend > 0 and gmv_7 > 0:
            ad_roi = gmv_7 / ad_spend
            if ad_roi < 1.5:
                _mark_triggered(rule_id)
                triggered.append(RuleTrigger(
                    rule_id=rule_id,
                    name="广告ROI偏低",
                    severity="warning",
                    description=(
                        f"近7天广告ROI {ad_roi:.2f}（低于1.5安全线），"
                        f"广告花费 ¥{ad_spend:,.0f} 但GMV仅 ¥{gmv_7:,.0f}。"
                        "投入产出比不健康，需优化投放策略。"
                    ),
                    recommended_skills=["data_channel_roi", "ops_channel_strategy"],
                    dispatch_roles=["ops", "data"],
                    metric_snapshot={
                        "ad_roi": round(ad_roi, 3),
                        "ad_spend_7d": round(ad_spend, 2),
                        "gmv_7d": round(gmv_7, 2),
                    },
                ))

    # ── 规则4：退款率 > 5% ───────────────────────────────────────────────
    rule_id = "refund_spike"
    if _is_cooled_down(rule_id):
        platforms = summary_7d.get("platforms", {})
        refund_rates = [
            p.get("refund_rate", 0) for p in platforms.values()
            if p.get("refund_rate") is not None
        ]
        avg_refund = sum(refund_rates) / len(refund_rates) if refund_rates else 0
        if avg_refund > 0.05:
            _mark_triggered(rule_id)
            triggered.append(RuleTrigger(
                rule_id=rule_id,
                name="退款率异常飙升",
                severity="critical",
                description=(
                    f"近7天平均退款率 {avg_refund*100:.2f}%，超过5%警戒线。"
                    "可能存在品质/描述不符/物流问题，需立即排查。"
                ),
                recommended_skills=["service_dsr_improvement", "data_anomaly_diagnosis"],
                dispatch_roles=["service", "ops"],
                metric_snapshot={
                    "avg_refund_rate": round(avg_refund, 4),
                    "by_platform": {
                        plat: round(pdata.get("refund_rate", 0), 4)
                        for plat, pdata in platforms.items()
                        if pdata.get("refund_rate") is not None
                    },
                },
            ))

    # ── 规则5：GMV断崖下跌 > 30% ─────────────────────────────────────────
    rule_id = "gmv_cliff"
    if _is_cooled_down(rule_id):
        gmv_change = period_cmp.get("change_pct", 0) if period_cmp else 0
        if gmv_change <= -30:
            _mark_triggered(rule_id)
            gmv_now = period_cmp.get("current", 0)
            gmv_prev = period_cmp.get("previous", 0)
            triggered.append(RuleTrigger(
                rule_id=rule_id,
                name="GMV断崖式下跌",
                severity="critical",
                description=(
                    f"GMV较上期下跌 {abs(gmv_change):.1f}%（¥{gmv_prev:,.0f} → ¥{gmv_now:,.0f}），"
                    "超过30%断崖阈值，需多Agent联合诊断。"
                ),
                recommended_skills=[
                    "data_anomaly_diagnosis",
                    "data_funnel_analysis",
                    "data_channel_roi",
                    "accounting_pl_statement",
                ],
                dispatch_roles=["data", "ops", "accounting"],
                metric_snapshot={
                    "gmv_now": gmv_now,
                    "gmv_prev": gmv_prev,
                    "change_pct": round(gmv_change, 2),
                },
            ))

    # 按严重程度排序（critical > warning > info）
    severity_order = {"critical": 0, "warning": 1, "info": 2}
    triggered.sort(key=lambda r: severity_order.get(r.severity, 9))
    return triggered


# ──────────────────────────────────────────────────────────────────────────────
# 格式化输出 — 供 prompt_builder / autopilot 使用
# ──────────────────────────────────────────────────────────────────────────────

def format_triggers_for_prompt(triggers: List[RuleTrigger]) -> str:
    """将触发规则列表格式化为适合注入 system prompt 的文本。"""
    if not triggers:
        return ""
    lines = ["【实时运营预警】以下异常指标已触发，请优先响应："]
    for t in triggers:
        icon = "🔴" if t.severity == "critical" else ("🟡" if t.severity == "warning" else "🔵")
        lines.append(f"{icon} {t.name}：{t.description}")
        if t.recommended_skills:
            lines.append(f"   建议技能：{' → '.join(t.recommended_skills[:3])}")
    return "\n".join(lines)


async def get_active_triggers(user_id: int) -> Tuple[List[RuleTrigger], str]:
    """
    便捷接口：评估规则 + 格式化文本，供 chat_pipeline 调用。

    Returns
    -------
    (triggers, prompt_text)
    """
    triggers = await evaluate_rules(user_id)
    prompt_text = format_triggers_for_prompt(triggers)
    return triggers, prompt_text
