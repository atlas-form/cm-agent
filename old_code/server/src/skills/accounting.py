from __future__ import annotations

from typing import Any, Dict, List

from .base import SkillBase
from ._db_helpers import load_metrics_summary
from ._content_engine import generate_financial_narrative, generate_budget_intelligence


class AccountingCostCalc(SkillBase):
    """成本核算技能"""

    def __init__(self) -> None:
        super().__init__(
            name="accounting_cost_calc",
            display_name="成本核算",
            description="根据各项成本明细，计算单品综合成本和成本结构",
            category="accounting",
            input_schema={
                "type": "object",
                "properties": {
                    "purchase_cost": {"type": "number", "description": "采购成本（元）"},
                    "shipping_cost": {"type": "number", "description": "物流成本（元）"},
                    "packaging_cost": {"type": "number", "description": "包装成本（元）"},
                    "platform_fee_rate": {"type": "number", "description": "平台佣金率(%)"},
                    "ad_cost_per_unit": {"type": "number", "description": "单品推广成本（元）"},
                    "selling_price": {"type": "number", "description": "售价（元）"},
                },
                "required": ["purchase_cost", "selling_price"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        purchase = kwargs.get("purchase_cost", 0)
        shipping = kwargs.get("shipping_cost", 5)
        packaging = kwargs.get("packaging_cost", 2)
        fee_rate = kwargs.get("platform_fee_rate", 5)
        ad_cost = kwargs.get("ad_cost_per_unit", 3)
        price = kwargs.get("selling_price", 100)

        platform_fee = round(price * fee_rate / 100, 2)
        total_cost = round(purchase + shipping + packaging + platform_fee + ad_cost, 2)
        profit = round(price - total_cost, 2)
        margin = round(profit / price * 100, 2) if price > 0 else 0

        return {
            "售价": price,
            "成本明细": {
                "采购成本": purchase,
                "物流成本": shipping,
                "包装成本": packaging,
                "平台佣金": platform_fee,
                "推广成本": ad_cost,
            },
            "综合成本": total_cost,
            "单品利润": profit,
            "利润率": f"{margin}%",
            "成本占比": {
                "采购": f"{round(purchase / total_cost * 100, 1)}%" if total_cost > 0 else "0%",
                "物流": f"{round(shipping / total_cost * 100, 1)}%" if total_cost > 0 else "0%",
                "包装": f"{round(packaging / total_cost * 100, 1)}%" if total_cost > 0 else "0%",
                "平台": f"{round(platform_fee / total_cost * 100, 1)}%" if total_cost > 0 else "0%",
                "推广": f"{round(ad_cost / total_cost * 100, 1)}%" if total_cost > 0 else "0%",
            },
            "建议": "利润率偏低，建议优化采购或提价" if margin < 15 else "利润率健康",
        }


class AccountingProfitAnalysis(SkillBase):
    """利润分析技能 — 自动加载真实店铺数据"""

    def __init__(self) -> None:
        super().__init__(
            name="accounting_profit_analysis",
            display_name="利润分析",
            description="分析店铺利润构成；如有真实数据自动加载，也可手动传入参数",
            category="accounting",
            input_schema={
                "type": "object",
                "properties": {
                    "revenue": {"type": "number", "description": "总营收（元）；不填则从店铺数据自动读取"},
                    "cogs": {"type": "number", "description": "商品成本（元）；不填则估算为营收×40%"},
                    "operating_expenses": {"type": "number", "description": "运营费用（元）"},
                    "ad_spend": {"type": "number", "description": "广告支出（元）；不填则从店铺数据自动读取"},
                    "refund_amount": {"type": "number", "description": "退款金额（元）"},
                    "days": {"type": "integer", "description": "统计天数，默认30"},
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        days: int = kwargs.get("days", 30)

        # ── 自动加载真实店铺数据 ──
        data_source = "用户提供"
        auto_revenue: float = 0.0
        auto_ad_spend: float = 0.0
        auto_refund_rate: float = 0.0

        summary = await load_metrics_summary(user_id, days=days)
        if summary.get("has_data"):
            totals = summary.get("totals", {})
            auto_revenue = totals.get("gmv", 0)
            auto_ad_spend = totals.get("ad_spend", 0)
            # 退款率从各平台平均
            refund_rates = [
                p.get("refund_rate", 0)
                for p in summary.get("platforms", {}).values()
                if p.get("refund_rate") is not None
            ]
            auto_refund_rate = sum(refund_rates) / len(refund_rates) if refund_rates else 0
            data_source = f"真实数据（近{days}天）"

        # 优先使用用户明确传入的参数，否则用真实数据
        revenue = kwargs.get("revenue") if kwargs.get("revenue") is not None else auto_revenue
        ad = kwargs.get("ad_spend") if kwargs.get("ad_spend") is not None else auto_ad_spend
        opex = kwargs.get("operating_expenses", round(revenue * 0.10, 2) if revenue else 0)

        # 退款金额
        if kwargs.get("refund_amount") is not None:
            refund = kwargs["refund_amount"]
        elif revenue and auto_refund_rate:
            refund = round(revenue * auto_refund_rate, 2)
        else:
            refund = 0

        cogs = kwargs.get("cogs") if kwargs.get("cogs") is not None else round(revenue * 0.40, 2)

        if not revenue:
            return {
                "has_data": False,
                "提示": "暂无真实营收数据。请先通过数据导入或平台同步上传店铺数据，或手动传入 revenue 参数。",
                "数据来源": data_source,
            }

        net_revenue = revenue - refund
        gross_profit = net_revenue - cogs
        gross_margin = round(gross_profit / net_revenue * 100, 2) if net_revenue > 0 else 0
        operating_profit = gross_profit - opex - ad
        operating_margin = round(operating_profit / net_revenue * 100, 2) if net_revenue > 0 else 0

        return {
            "统计周期": f"最近{days}天",
            "数据来源": data_source,
            "营收分析": {
                "总营收": revenue,
                "退款": refund,
                "净营收": net_revenue,
                "退款率": f"{round(refund / revenue * 100, 2)}%" if revenue > 0 else "0%",
            },
            "利润分析": {
                "毛利润": gross_profit,
                "毛利率": f"{gross_margin}%",
                "营业利润": operating_profit,
                "营业利润率": f"{operating_margin}%",
            },
            "费用结构": {
                "商品成本": cogs,
                "运营费用": opex,
                "广告支出": ad,
                "费用率": f"{round((cogs + opex + ad) / net_revenue * 100, 2)}%" if net_revenue > 0 else "0%",
            },
            "健康度": {
                "毛利率": "健康" if gross_margin > 40 else ("偏低" if gross_margin > 20 else "亏损风险"),
                "营业利润率": "健康" if operating_margin > 10 else ("偏低" if operating_margin > 0 else "亏损"),
                "广告占比": "合理" if (ad / net_revenue * 100 if net_revenue > 0 else 0) < 15 else "偏高",
            },
        }


class AccountingBudgetPlan(SkillBase):
    """预算编制技能 — 基于真实历史GMV设定合理目标"""

    def __init__(self) -> None:
        super().__init__(
            name="accounting_budget_plan",
            display_name="预算编制",
            description="根据历史GMV（自动加载）或目标GMV，编制月度预算计划",
            category="accounting",
            input_schema={
                "type": "object",
                "properties": {
                    "target_gmv": {"type": "number", "description": "目标GMV（元）；不填则基于历史数据自动计算"},
                    "target_margin": {"type": "number", "description": "目标利润率(%)，默认15"},
                    "month": {"type": "string", "description": "预算月份"},
                    "growth_rate": {"type": "number", "description": "目标增长率(%)，默认20"},
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        target_margin: float = kwargs.get("target_margin", 15)
        month: str = kwargs.get("month", "下月")
        growth_rate: float = kwargs.get("growth_rate", 20)

        # ── 自动加载历史数据 ──
        data_source = "用户指定"
        historical_gmv: float = 0.0
        historical_ad_roi: float = 0.0

        summary = await load_metrics_summary(user_id, days=30)
        if summary.get("has_data"):
            historical_gmv = summary.get("totals", {}).get("gmv", 0)
            roi_vals = [
                p.get("ad_roi", 0)
                for p in summary.get("platforms", {}).values()
                if p.get("ad_roi")
            ]
            historical_ad_roi = sum(roi_vals) / len(roi_vals) if roi_vals else 3.0
            data_source = "真实历史数据（近30天）"

        # 确定目标GMV
        if kwargs.get("target_gmv") is not None:
            gmv = kwargs["target_gmv"]
            basis = "用户指定目标"
        elif historical_gmv > 0:
            gmv = round(historical_gmv * (1 + growth_rate / 100), 2)
            basis = f"历史GMV ¥{historical_gmv:,.0f} × (1 + {growth_rate}%)"
        else:
            gmv = 500000  # 无数据时的保守默认值
            basis = "默认值（建议先导入历史数据）"

        target_profit = round(gmv * target_margin / 100, 2)
        max_cost = round(gmv - target_profit, 2)

        budget = {
            "采购成本": round(gmv * 0.40, 2),
            "物流费用": round(gmv * 0.08, 2),
            "平台佣金": round(gmv * 0.05, 2),
            "广告投放": round(gmv * 0.12, 2),
            "人力成本": round(gmv * 0.10, 2),
            "包装耗材": round(gmv * 0.03, 2),
            "其他费用": round(gmv * 0.02, 2),
        }

        total_budget = sum(budget.values())
        gap = round(max_cost - total_budget, 2)

        result = {
            "预算月份": month,
            "数据来源": data_source,
            "目标依据": basis,
            "目标GMV": gmv,
            "历史GMV参考": f"¥{historical_gmv:,.0f}" if historical_gmv else "暂无数据",
            "目标利润率": f"{target_margin}%",
            "目标利润": target_profit,
            "最大可用成本": max_cost,
            "预算分配": budget,
            "预算合计": total_budget,
            "预算余量": gap,
            "关键假设": {
                "退款率": "≤ 5%",
                "广告ROI": f"≥ {historical_ad_roi:.1f}" if historical_ad_roi else "≥ 3.0",
                "增长目标": f"{growth_rate}%",
            },
        }

        # LLM预算智能诊断
        try:
            intelligence = await generate_budget_intelligence(
                budget_data=budget,
                historical_gmv=historical_gmv,
                target_gmv=gmv,
            )
            if intelligence:
                result["AI预算诊断"] = intelligence
        except Exception:
            pass

        return result


class AccountingROICalc(SkillBase):
    """ROI计算技能 — 自动加载真实广告数据"""

    def __init__(self) -> None:
        super().__init__(
            name="accounting_roi_calc",
            display_name="ROI计算",
            description="计算广告投放ROI；如有真实数据自动加载，也可手动传入",
            category="accounting",
            input_schema={
                "type": "object",
                "properties": {
                    "investment": {"type": "number", "description": "广告投入（元）；不填则从店铺数据读取"},
                    "revenue_generated": {"type": "number", "description": "产出营收（元）；不填则从店铺数据读取"},
                    "cost_of_goods": {"type": "number", "description": "商品成本（元）"},
                    "period": {"type": "string", "description": "统计周期描述"},
                    "days": {"type": "integer", "description": "统计天数，默认30"},
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        days: int = kwargs.get("days", 30)
        period: str = kwargs.get("period", f"最近{days}天")

        # ── 自动加载真实数据 ──
        data_source = "用户提供"
        auto_investment: float = 0.0
        auto_revenue: float = 0.0
        auto_roi: float = 0.0

        summary = await load_metrics_summary(user_id, days=days)
        if summary.get("has_data"):
            totals = summary.get("totals", {})
            auto_investment = totals.get("ad_spend", 0)
            auto_revenue = totals.get("gmv", 0)
            roi_vals = [
                p.get("ad_roi", 0)
                for p in summary.get("platforms", {}).values()
                if p.get("ad_roi")
            ]
            auto_roi = sum(roi_vals) / len(roi_vals) if roi_vals else 0
            data_source = f"真实数据（近{days}天）"

        investment = kwargs.get("investment") if kwargs.get("investment") is not None else auto_investment
        revenue = kwargs.get("revenue_generated") if kwargs.get("revenue_generated") is not None else auto_revenue

        if not investment and not revenue:
            return {
                "has_data": False,
                "提示": "暂无真实广告数据。请先同步平台数据或手动传入 investment 和 revenue_generated。",
                "数据来源": data_source,
            }

        if not investment:
            investment = 1  # 防止除0
        if not revenue:
            revenue = 0

        cogs = kwargs.get("cost_of_goods") if kwargs.get("cost_of_goods") is not None else round(revenue * 0.4, 2)

        roi = round(revenue / investment, 2) if investment > 0 else 0
        profit_roi = round((revenue - cogs - investment) / investment, 2) if investment > 0 else 0
        acos = round(investment / revenue * 100, 2) if revenue > 0 else 0
        # 估算CPA（假设客单价150元）
        avg_order_vals = [
            p.get("avg_order_value", 0)
            for p in summary.get("platforms", {}).values()
            if p.get("avg_order_value")
        ] if summary.get("has_data") else []
        aov = sum(avg_order_vals) / len(avg_order_vals) if avg_order_vals else 150
        cpa = round(investment / (revenue / aov), 2) if revenue > 0 else 0

        return {
            "统计周期": period,
            "数据来源": data_source,
            "投入": investment,
            "产出营收": revenue,
            "ROI指标": {
                "ROAS(广告回报率)": roi,
                "真实广告ROI": auto_roi if auto_roi else roi,
                "利润ROI": profit_roi,
                "ACOS(广告成本占比)": f"{acos}%",
                "CPA(获客成本)": cpa,
            },
            "盈亏分析": {
                "毛利润": round(revenue - cogs, 2),
                "净利润": round(revenue - cogs - investment, 2),
                "是否盈利": "是" if revenue - cogs - investment > 0 else "否",
            },
            "评价": {
                "ROAS": "优秀" if roi > 5 else ("良好" if roi > 3 else ("及格" if roi > 1 else "亏损")),
                "建议": "加大投放" if roi > 5 else ("维持投放" if roi > 3 else ("优化素材" if roi > 1 else "暂停投放排查")),
            },
        }


class AccountingComplianceCheck(SkillBase):
    """合规检查技能 — 自动加载真实月营收"""

    def __init__(self) -> None:
        super().__init__(
            name="accounting_compliance_check",
            display_name="合规检查",
            description="检查财务数据的合规性和风险点；月营收可自动从店铺数据加载",
            category="accounting",
            input_schema={
                "type": "object",
                "properties": {
                    "monthly_revenue": {"type": "number", "description": "月营收（元）；不填则自动加载"},
                    "tax_type": {"type": "string", "description": "纳税类型：小规模/一般纳税人"},
                    "invoice_rate": {"type": "number", "description": "开票率(%)"},
                    "cash_ratio": {"type": "number", "description": "现金收款占比(%)"},
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)

        # ── 自动加载月营收 ──
        data_source = "用户提供"
        auto_revenue: float = 0.0

        if not kwargs.get("monthly_revenue"):
            summary = await load_metrics_summary(user_id, days=30)
            if summary.get("has_data"):
                auto_revenue = summary.get("totals", {}).get("gmv", 0)
                data_source = "真实数据（近30天）"

        revenue = kwargs.get("monthly_revenue") if kwargs.get("monthly_revenue") is not None else auto_revenue
        tax_type = kwargs.get("tax_type", "小规模")
        invoice_rate = kwargs.get("invoice_rate", 80)
        cash_ratio = kwargs.get("cash_ratio", 10)

        if not revenue:
            return {
                "has_data": False,
                "提示": "暂无营收数据，请先导入店铺数据或传入 monthly_revenue。",
            }

        risks = []
        if tax_type == "小规模" and revenue * 12 > 5000000:
            risks.append({"风险": "年营收超500万需转为一般纳税人", "等级": "高", "建议": "提前做好税务筹划"})
        if invoice_rate < 70:
            risks.append({"风险": "开票率偏低，存在税务风险", "等级": "中", "建议": "规范开票流程"})
        if cash_ratio > 30:
            risks.append({"风险": "现金收款占比过高", "等级": "中", "建议": "增加线上支付渠道"})

        tax_rate = 0.03 if tax_type == "小规模" else 0.13
        est_tax = round(revenue * tax_rate, 2)

        return {
            "月营收": revenue,
            "数据来源": data_source,
            "纳税类型": tax_type,
            "预估税额": {
                "增值税": est_tax,
                "附加税": round(est_tax * 0.12, 2),
                "企业所得税": round(revenue * 0.05 * 0.25, 2),
                "合计": round(est_tax + est_tax * 0.12 + revenue * 0.05 * 0.25, 2),
            },
            "合规检查": {
                "开票率": f"{invoice_rate}%",
                "现金占比": f"{cash_ratio}%",
                "风险点数": len(risks),
            },
            "风险详情": risks if risks else [{"提示": "暂未发现合规风险"}],
            "建议": [
                "按月申报纳税",
                "保留所有交易凭证",
                "定期对账核实",
            ],
        }


class AccountingPLStatement(SkillBase):
    """完整利润表（P&L Statement）— GMV → 毛利 → 贡献利润 → EBITDA → 净利润"""

    def __init__(self) -> None:
        super().__init__(
            name="accounting_pl_statement",
            display_name="完整利润表",
            description="生成完整多层次利润表（GMV→净营收→毛利→贡献利润→EBITDA→税前/税后净利），自动加载真实数据",
            category="accounting",
            input_schema={
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "统计天数，默认30"},
                    "cogs_rate": {"type": "number", "description": "商品成本率(%)，默认40"},
                    "variable_cost_rate": {"type": "number", "description": "变动成本率(%)，默认10（物流+包装）"},
                    "fixed_costs": {"type": "number", "description": "月固定成本（元），默认按营收3%估算"},
                    "tax_rate": {"type": "number", "description": "综合税率(%)，默认25"},
                    "depreciation": {"type": "number", "description": "月折旧摊销（元），默认0"},
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        days: int = int(kwargs.get("days", 30))
        cogs_rate: float = kwargs.get("cogs_rate", 40.0) / 100
        var_rate: float = kwargs.get("variable_cost_rate", 10.0) / 100
        tax_rate: float = kwargs.get("tax_rate", 25.0) / 100
        depreciation: float = kwargs.get("depreciation", 0)

        summary = await load_metrics_summary(user_id, days=days)
        if not summary.get("has_data"):
            return {
                "has_data": False,
                "提示": "暂无真实数据，请先导入店铺数据或配置平台 API。",
            }

        totals = summary.get("totals", {})
        gmv: float = totals.get("gmv", 0)
        ad_spend: float = totals.get("ad_spend", 0)
        orders: float = totals.get("orders", 0)

        # 退款率计算
        refund_rates = [
            p.get("refund_rate", 0)
            for p in summary.get("platforms", {}).values()
            if p.get("refund_rate") is not None
        ]
        avg_refund_rate = sum(refund_rates) / len(refund_rates) if refund_rates else 0.03
        refund_amount = round(gmv * avg_refund_rate, 2)

        # 平台佣金（估算5%）
        platform_fee = round(gmv * 0.05, 2)

        # 各层利润计算
        net_revenue = round(gmv - refund_amount, 2)                    # 净营收
        cogs = round(net_revenue * cogs_rate, 2)                       # 商品成本
        gross_profit = round(net_revenue - cogs, 2)                    # 毛利润
        gross_margin = round(gross_profit / net_revenue * 100, 2) if net_revenue else 0

        variable_costs = round(net_revenue * var_rate, 2)              # 变动费用（物流/包装）
        contribution_margin = round(gross_profit - variable_costs - platform_fee - ad_spend, 2)  # 贡献利润
        cm_rate = round(contribution_margin / net_revenue * 100, 2) if net_revenue else 0

        fixed_costs = kwargs.get("fixed_costs") or round(net_revenue * 0.03, 2)  # 固定成本（人工/租金）
        ebitda = round(contribution_margin - fixed_costs, 2)           # EBITDA
        ebitda_margin = round(ebitda / net_revenue * 100, 2) if net_revenue else 0

        ebit = round(ebitda - depreciation, 2)                         # EBIT
        tax_amount = round(max(ebit * tax_rate, 0), 2)
        net_profit = round(ebit - tax_amount, 2)                       # 税后净利润
        net_margin = round(net_profit / net_revenue * 100, 2) if net_revenue else 0

        aov = round(gmv / orders, 2) if orders else 0

        result = {
            "统计周期": f"最近{days}天",
            "数据来源": "真实数据 + 参数估算",
            "利润表": {
                "① GMV（总销售额）": round(gmv, 2),
                "  (-) 退款": refund_amount,
                "② 净营收": net_revenue,
                "  (-) 商品成本（COGS）": cogs,
                "③ 毛利润": gross_profit,
                "   毛利率": f"{gross_margin}%",
                "  (-) 物流/包装（变动）": variable_costs,
                "  (-) 平台佣金": platform_fee,
                "  (-) 广告投放": ad_spend,
                "④ 贡献利润（CM）": contribution_margin,
                "   贡献利润率": f"{cm_rate}%",
                "  (-) 固定成本（人工/租金等）": fixed_costs,
                "⑤ EBITDA": ebitda,
                "   EBITDA率": f"{ebitda_margin}%",
                "  (-) 折旧摊销": depreciation,
                "⑥ EBIT（税前利润）": ebit,
                "  (-) 所得税": tax_amount,
                "⑦ 税后净利润": net_profit,
                "   净利率": f"{net_margin}%",
            },
            "关键运营指标": {
                "订单量": int(orders),
                "客单价(AOV)": f"¥{aov}",
                "退款率": f"{avg_refund_rate*100:.2f}%",
                "广告ROI": round(gmv / max(ad_spend, 1), 2) if ad_spend else "无广告数据",
            },
            "健康度评估": {
                "毛利率": "优秀" if gross_margin > 50 else ("正常" if gross_margin > 30 else "偏低"),
                "贡献利润率": "优秀" if cm_rate > 25 else ("正常" if cm_rate > 10 else "偏低"),
                "净利率": "优秀" if net_margin > 15 else ("正常" if net_margin > 5 else ("亏损" if net_margin < 0 else "偏低")),
            },
        }

        # LLM财务叙事诊断
        try:
            pl_summary = {
                "GMV": f"¥{gmv:,.0f}",
                "净营收": f"¥{net_revenue:,.0f}",
                "毛利率": f"{gross_margin}%",
                "贡献利润率": f"{cm_rate}%",
                "EBITDA": f"¥{ebitda:,.0f}",
                "净利率": f"{net_margin}%",
                "广告ROI": round(gmv / max(ad_spend, 1), 2) if ad_spend else None,
                "健康度": {
                    "毛利率": "优秀" if gross_margin > 50 else ("正常" if gross_margin > 30 else "偏低"),
                    "净利率": "优秀" if net_margin > 15 else ("正常" if net_margin > 5 else ("亏损" if net_margin < 0 else "偏低")),
                },
            }
            narrative = await generate_financial_narrative(pl_summary, period=f"近{days}天")
            if narrative:
                result["AI财务诊断"] = narrative
        except Exception:
            pass

        return result


class AccountingBreakEvenCalc(SkillBase):
    """盈亏平衡分析 — 含敏感性分析表"""

    def __init__(self) -> None:
        super().__init__(
            name="accounting_break_even_calc",
            display_name="盈亏平衡分析",
            description="计算盈亏平衡点（BEP），生成价格×销量敏感性矩阵，自动加载真实数据",
            category="accounting",
            input_schema={
                "type": "object",
                "properties": {
                    "fixed_costs": {"type": "number", "description": "月固定成本（元）"},
                    "unit_variable_cost": {"type": "number", "description": "单品变动成本（元）；不填则从数据推算"},
                    "selling_price": {"type": "number", "description": "单品售价（元）；不填则从客单价推算"},
                    "days": {"type": "integer", "description": "统计天数（用于自动加载），默认30"},
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        days: int = int(kwargs.get("days", 30))

        # 尝试加载真实数据
        data_source = "用户提供"
        real_aov: float = 0.0
        real_cogs_rate: float = 0.40
        real_fixed: float = 0.0

        summary = await load_metrics_summary(user_id, days=days)
        if summary.get("has_data"):
            totals = summary.get("totals", {})
            gmv = totals.get("gmv", 0)
            orders = totals.get("orders", 0)
            real_aov = round(gmv / orders, 2) if orders else 0
            ad_spend = totals.get("ad_spend", 0)
            # 估算固定成本（广告之外的运营费用约占GMV的13%）
            real_fixed = round(gmv * 0.13, 2) if gmv else 0
            data_source = f"真实数据（近{days}天）推算"

        price = kwargs.get("selling_price") or real_aov or 100.0
        unit_var = kwargs.get("unit_variable_cost") or round(price * (0.40 + 0.10 + 0.05), 2)  # cogs+物流+平台
        fixed = kwargs.get("fixed_costs") or real_fixed or round(price * 200 * 0.13, 2)  # fallback

        contribution_per_unit = price - unit_var
        if contribution_per_unit <= 0:
            return {
                "error": "单品变动成本高于售价，无法达到盈亏平衡",
                "建议": "降低成本或提高售价",
            }

        bep_units = round(fixed / contribution_per_unit, 0)
        bep_revenue = round(bep_units * price, 2)
        margin_of_safety_pct = round((1 - 1 / (price / unit_var)) * 100, 1) if price > unit_var else 0

        # 敏感性分析矩阵（价格±10%，销量±20%）
        price_variants = [round(price * r, 2) for r in [0.8, 0.9, 1.0, 1.1, 1.2]]
        sales_targets = [int(bep_units * r) for r in [0.5, 0.75, 1.0, 1.5, 2.0]]

        sensitivity: List[Dict] = []
        for pv in price_variants:
            cpu = pv - unit_var
            row: Dict[str, Any] = {"售价": f"¥{pv}"}
            for sv in sales_targets:
                profit = round(cpu * sv - fixed, 2)
                row[f"{sv}单"] = f"¥{profit:,.0f}" + ("✓" if profit >= 0 else "✗")
            sensitivity.append(row)

        return {
            "数据来源": data_source,
            "定价": price,
            "单品变动成本": unit_var,
            "月固定成本": fixed,
            "单位贡献利润": round(contribution_per_unit, 2),
            "贡献利润率": f"{round(contribution_per_unit / price * 100, 1)}%",
            "盈亏平衡点": {
                "需销售订单数": int(bep_units),
                "需实现营收": f"¥{bep_revenue:,.2f}",
                "安全边际": f"{margin_of_safety_pct}%",
            },
            "敏感性分析(利润矩阵)": sensitivity,
            "说明": "✓=盈利 ✗=亏损",
        }


class AccountingCashFlowForecast(SkillBase):
    """现金流预测 — 3个月滚动预测"""

    def __init__(self) -> None:
        super().__init__(
            name="accounting_cash_flow_forecast",
            display_name="现金流预测",
            description="基于真实数据生成3个月现金流滚动预测，含经营/投资/筹资三类现金流",
            category="accounting",
            input_schema={
                "type": "object",
                "properties": {
                    "growth_assumptions": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "3个月GMV增长假设(%)，例如 [10, 15, 20]，默认保守增长",
                    },
                    "capex": {"type": "number", "description": "月度资本性支出（元），默认0"},
                    "loan_repayment": {"type": "number", "description": "月度还款（元），默认0"},
                    "opening_cash": {"type": "number", "description": "期初现金余额（元）"},
                    "days": {"type": "integer", "description": "基期统计天数，默认30"},
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        days: int = int(kwargs.get("days", 30))
        growth_assumptions: List[float] = kwargs.get("growth_assumptions", [5.0, 8.0, 12.0])
        capex: float = kwargs.get("capex", 0)
        loan_repayment: float = kwargs.get("loan_repayment", 0)
        opening_cash: float = kwargs.get("opening_cash", 0)

        # 自动加载基期数据
        summary = await load_metrics_summary(user_id, days=days)
        if not summary.get("has_data"):
            return {
                "has_data": False,
                "提示": "暂无真实数据，请先导入店铺数据。",
            }

        totals = summary.get("totals", {})
        base_gmv: float = totals.get("gmv", 0)
        base_ad: float = totals.get("ad_spend", 0)
        base_orders: float = totals.get("orders", 0)

        # 基期月化（如果 days != 30，做归一化）
        factor = 30 / days
        monthly_gmv = base_gmv * factor
        monthly_ad = base_ad * factor

        # 估算各成本占比
        cogs_r = 0.40
        logistics_r = 0.08
        platform_r = 0.05
        opex_r = 0.10  # 固定运营（人工等）
        tax_r = 0.03

        months_labels = ["第1个月", "第2个月", "第3个月"]
        if len(growth_assumptions) < 3:
            growth_assumptions = (growth_assumptions + [10.0, 10.0, 10.0])[:3]

        months_forecast: List[Dict[str, Any]] = []
        running_cash = opening_cash

        for i, growth_pct in enumerate(growth_assumptions):
            gmv_m = round(monthly_gmv * (1 + growth_pct / 100) ** (i + 1), 2)
            ad_m = round(monthly_ad * (1 + growth_pct / 100) ** (i + 1), 2)
            refund_m = round(gmv_m * 0.03, 2)
            net_revenue = round(gmv_m - refund_m, 2)

            # 经营现金流
            cash_in = net_revenue
            cash_out_cogs = round(gmv_m * cogs_r, 2)
            cash_out_logistics = round(gmv_m * logistics_r, 2)
            cash_out_platform = round(gmv_m * platform_r, 2)
            cash_out_ad = ad_m
            cash_out_opex = round(net_revenue * opex_r, 2)
            cash_out_tax = round(net_revenue * tax_r, 2)
            operating_cf = round(cash_in - cash_out_cogs - cash_out_logistics - cash_out_platform - cash_out_ad - cash_out_opex - cash_out_tax, 2)

            # 投资现金流
            investing_cf = round(-capex, 2)

            # 筹资现金流
            financing_cf = round(-loan_repayment, 2)

            net_cf = round(operating_cf + investing_cf + financing_cf, 2)
            running_cash = round(running_cash + net_cf, 2)

            months_forecast.append({
                "月份": months_labels[i],
                "GMV预测": f"¥{gmv_m:,.2f}",
                "增长假设": f"+{growth_pct}%",
                "经营现金流入": f"¥{cash_in:,.2f}",
                "经营现金流出明细": {
                    "商品成本": f"¥{cash_out_cogs:,.2f}",
                    "物流费": f"¥{cash_out_logistics:,.2f}",
                    "平台佣金": f"¥{cash_out_platform:,.2f}",
                    "广告投放": f"¥{cash_out_ad:,.2f}",
                    "运营费用": f"¥{cash_out_opex:,.2f}",
                    "税费": f"¥{cash_out_tax:,.2f}",
                },
                "经营现金流净额": f"¥{operating_cf:,.2f}",
                "投资现金流": f"¥{investing_cf:,.2f}",
                "筹资现金流": f"¥{financing_cf:,.2f}",
                "月净现金流": f"¥{net_cf:,.2f}",
                "期末现金余额": f"¥{running_cash:,.2f}",
                "经营现金流状态": "正常" if operating_cf > 0 else "预警",
            })

        total_net_cf = sum(
            float(m["月净现金流"].replace("¥", "").replace(",", ""))
            for m in months_forecast
        )

        return {
            "has_data": True,
            "数据来源": f"真实数据（近{days}天）月化 + 增长假设",
            "基期月GMV": f"¥{monthly_gmv:,.2f}",
            "期初现金余额": f"¥{opening_cash:,.2f}",
            "3个月现金流预测": months_forecast,
            "3个月累计净现金流": f"¥{total_net_cf:,.2f}",
            "预测说明": "基于线性增长假设，实际受促销/季节性等因素影响",
            "风险提示": [
                m["月份"] + "经营现金流为负，需关注资金缺口"
                for m in months_forecast
                if "预警" in m["经营现金流状态"]
            ] or ["未发现明显资金缺口"],
        }


class AccountingFinancialNarrative(SkillBase):
    """财务健康叙事分析 — LLM基于真实P&L数据生成深度财务诊断报告"""

    def __init__(self) -> None:
        super().__init__(
            name="accounting_financial_narrative",
            display_name="财务健康诊断",
            description="AI基于真实财务数据生成完整财务健康诊断报告（评级/风险识别/改善路径），自动加载P&L数据",
            category="accounting",
            input_schema={
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "统计天数，默认30"},
                    "context": {"type": "string", "description": "额外背景信息（如大促期/新品上市等）"},
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        days: int = int(kwargs.get("days", 30))
        context: str = kwargs.get("context", "")

        summary = await load_metrics_summary(user_id, days=days)
        if not summary.get("has_data"):
            return {
                "has_data": False,
                "提示": "暂无真实财务数据，请先导入店铺数据或配置平台 API。",
            }

        totals = summary.get("totals", {})
        gmv = totals.get("gmv", 0)
        ad_spend = totals.get("ad_spend", 0)
        orders = totals.get("orders", 0)

        refund_rates = [
            p.get("refund_rate", 0)
            for p in summary.get("platforms", {}).values()
            if p.get("refund_rate") is not None
        ]
        avg_refund = sum(refund_rates) / len(refund_rates) if refund_rates else 0.03

        net_revenue = round(gmv * (1 - avg_refund), 2)
        cogs = round(net_revenue * 0.40, 2)
        gross_profit = net_revenue - cogs
        gross_margin = round(gross_profit / net_revenue * 100, 2) if net_revenue else 0
        contribution = round(gross_profit - net_revenue * 0.10 - gmv * 0.05 - ad_spend, 2)
        cm_rate = round(contribution / net_revenue * 100, 2) if net_revenue else 0
        net_profit = round(contribution - net_revenue * 0.03, 2)
        net_margin = round(net_profit / net_revenue * 100, 2) if net_revenue else 0
        ad_roi = round(gmv / max(ad_spend, 1), 2) if ad_spend else 0
        aov = round(gmv / orders, 2) if orders else 0

        pl_data = {
            "GMV": f"¥{gmv:,.0f}",
            "净营收": f"¥{net_revenue:,.0f}",
            "毛利率": f"{gross_margin}%",
            "贡献利润率": f"{cm_rate}%",
            "净利率": f"{net_margin}%",
            "广告ROI": ad_roi if ad_spend else None,
            "退款率": f"{avg_refund*100:.2f}%",
            "客单价(AOV)": f"¥{aov}",
            "健康度": {
                "毛利率": "优秀" if gross_margin > 50 else ("正常" if gross_margin > 30 else "偏低"),
                "净利率": "优秀" if net_margin > 15 else ("正常" if net_margin > 5 else ("亏损" if net_margin < 0 else "偏低")),
                "广告ROI": "优秀" if ad_roi > 5 else ("正常" if ad_roi > 2 else "偏低") if ad_spend else "无广告数据",
            },
        }

        # 搜索行业财务基准（始终执行，财务健康诊断必须对标行业真实数据）
        finance_benchmark_ctx = ""
        if True:
            try:
                from .search import _web_search, _format_results_for_llm
                q = "电商行业 毛利率 净利率 广告ROI 财务基准 2026"
                results, _ = await _web_search(q, topic="general", max_results=4, days=60)
                if results:
                    finance_benchmark_ctx = _format_results_for_llm(results, max_per_item=250)
            except Exception:
                pass

        narrative = await generate_financial_narrative(
            pl_data, period=f"近{days}天", context=context,
            search_context=finance_benchmark_ctx,
        )

        return {
            "has_data": True,
            "统计周期": f"近{days}天",
            "关键财务指标": {
                "GMV": f"¥{gmv:,.0f}",
                "毛利率": f"{gross_margin}%",
                "贡献利润率": f"{cm_rate}%",
                "净利率": f"{net_margin}%",
                "广告ROI": ad_roi if ad_spend else "无广告数据",
                "客单价": f"¥{aov}",
                "退款率": f"{avg_refund*100:.2f}%",
            },
            "AI财务健康诊断": narrative or "（诊断报告生成失败，请检查LLM配置）",
        }


class AccountingBudgetVsActual(SkillBase):
    """预算vs实际差异分析 — 逐项对比预算与真实执行，识别超支/节省科目"""

    def __init__(self) -> None:
        super().__init__(
            name="accounting_budget_vs_actual",
            display_name="预算vs实际分析",
            description=(
                "将预算计划与真实店铺数据逐项对比，计算绝对差异和百分比偏差，"
                "标记超支/节省科目，提供执行质量评分和AI改善建议。"
            ),
            category="accounting",
            input_schema={
                "type": "object",
                "properties": {
                    "budget_gmv": {"type": "number", "description": "预算GMV目标（元）"},
                    "budget_ad_spend": {"type": "number", "description": "预算广告投入（元）"},
                    "budget_cogs_rate": {"type": "number", "description": "预算商品成本率(%)，默认40"},
                    "budget_opex_rate": {"type": "number", "description": "预算运营费用率(%)，默认10"},
                    "budget_target_margin": {"type": "number", "description": "预算目标利润率(%)，默认15"},
                    "days": {"type": "integer", "description": "统计天数，默认30"},
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        days: int = int(kwargs.get("days", 30))

        budget_gmv: float = float(kwargs.get("budget_gmv", 0))
        budget_ad: float = float(kwargs.get("budget_ad_spend", 0))
        budget_cogs_rate: float = float(kwargs.get("budget_cogs_rate", 40)) / 100
        budget_opex_rate: float = float(kwargs.get("budget_opex_rate", 10)) / 100
        budget_margin_rate: float = float(kwargs.get("budget_target_margin", 15)) / 100

        # ── 加载真实数据 ──
        summary = await load_metrics_summary(user_id, days=days)
        if not summary.get("has_data"):
            return {
                "has_data": False,
                "提示": "暂无真实数据。请先导入店铺数据，或手动传入预算参数后重试。",
            }

        totals = summary.get("totals", {})
        actual_gmv: float = totals.get("gmv", 0)
        actual_ad: float = totals.get("ad_spend", 0)
        actual_orders: float = totals.get("orders", 0)
        actual_refund_rate: float = 0.0
        refund_rates = [
            p.get("refund_rate", 0)
            for p in summary.get("platforms", {}).values()
            if p.get("refund_rate") is not None
        ]
        if refund_rates:
            actual_refund_rate = sum(refund_rates) / len(refund_rates)

        # 如果没有传入预算，基于历史自动生成（上月同期×120%作为预算目标）
        data_note = "（用户提供预算）"
        if budget_gmv == 0:
            # 用历史2个月数据作为基准
            prev_summary = await load_metrics_summary(user_id, days=60)
            if prev_summary.get("has_data"):
                prev_gmv = prev_summary.get("totals", {}).get("gmv", 0)
                # 60天数据取前30天的估算
                budget_gmv = round((prev_gmv / 2) * 1.20, 2)
                data_note = f"（预算自动生成：近60天GMV÷2×120%增长目标）"
            else:
                budget_gmv = actual_gmv * 1.20  # 降级：以实际×1.2为预算
                data_note = "（预算降级生成：实际GMV×120%）"

        if budget_ad == 0 and actual_ad > 0:
            budget_ad = round(budget_gmv * 0.12, 2)  # 预算广告率默认12%

        # ── 计算预算各科目 ──
        budget_cogs = round(budget_gmv * budget_cogs_rate, 2)
        budget_opex = round(budget_gmv * budget_opex_rate, 2)
        budget_net_rev = round(budget_gmv * (1 - 0.03), 2)  # 假设退款率3%
        budget_gross_profit = round(budget_net_rev - budget_cogs, 2)
        budget_op_profit = round(budget_gross_profit - budget_opex - budget_ad, 2)
        budget_margin = round(budget_op_profit / budget_gmv * 100, 2) if budget_gmv > 0 else 0

        # ── 计算实际各科目 ──
        actual_net_rev = round(actual_gmv * (1 - actual_refund_rate), 2)
        actual_cogs = round(actual_net_rev * 0.40, 2)  # 估算成本率40%
        actual_opex = round(actual_gmv * 0.10, 2)
        actual_gross_profit = round(actual_net_rev - actual_cogs, 2)
        actual_op_profit = round(actual_gross_profit - actual_opex - actual_ad, 2)
        actual_margin = round(actual_op_profit / actual_gmv * 100, 2) if actual_gmv > 0 else 0

        def _variance(budget: float, actual: float) -> Dict[str, Any]:
            """计算差异：正=超额完成/节省，负=未达标/超支"""
            abs_var = round(actual - budget, 2)
            pct_var = round(abs_var / max(abs(budget), 1) * 100, 1)
            if budget > 0:
                # 对于收入类：actual>budget是好事；对于费用类：actual<budget是好事
                is_revenue_metric = budget in [budget_gmv, budget_gross_profit, budget_op_profit, budget_net_rev]
                favorable = (abs_var >= 0) if is_revenue_metric else (abs_var <= 0)
            else:
                favorable = True
            return {
                "预算": round(budget, 2),
                "实际": round(actual, 2),
                "差异": abs_var,
                "差异率": f"{'+' if pct_var >= 0 else ''}{pct_var}%",
                "状态": "有利" if favorable else "不利",
            }

        variance_table = {
            "GMV": _variance(budget_gmv, actual_gmv),
            "净营收": _variance(budget_net_rev, actual_net_rev),
            "商品成本": _variance(budget_cogs, actual_cogs),
            "广告投入": _variance(budget_ad, actual_ad) if budget_ad > 0 else {"说明": "未设广告预算"},
            "运营费用": _variance(budget_opex, actual_opex),
            "毛利润": _variance(budget_gross_profit, actual_gross_profit),
            "营业利润": _variance(budget_op_profit, actual_op_profit),
            "利润率": {
                "预算": f"{budget_margin}%",
                "实际": f"{actual_margin}%",
                "差异": f"{'+' if actual_margin >= budget_margin else ''}{round(actual_margin - budget_margin, 1)}ppt",
                "状态": "有利" if actual_margin >= budget_margin else "不利",
            },
        }

        # 执行质量评分（满分100）
        gmv_ach = actual_gmv / max(budget_gmv, 1)
        margin_ach = actual_margin / max(budget_margin, 0.1)
        ad_eff = (budget_ad / max(actual_ad, 1)) if actual_ad > 0 and budget_ad > 0 else 1.0  # 广告节省=更高效
        exec_score = round(min(100, (gmv_ach * 0.50 + margin_ach * 0.35 + min(ad_eff, 1.5) * 0.15) * 100), 1)

        # 超支/未达标科目识别
        risk_items = []
        if actual_gmv < budget_gmv * 0.90:
            risk_items.append(f"GMV未达标（完成率{gmv_ach*100:.0f}%，差{budget_gmv-actual_gmv:,.0f}元）")
        if actual_ad > budget_ad * 1.15 and budget_ad > 0:
            risk_items.append(f"广告超支（超出{actual_ad-budget_ad:,.0f}元）")
        if actual_margin < budget_margin * 0.85:
            risk_items.append(f"利润率低于预算（实{actual_margin}% vs 预算{budget_margin}%）")

        result: Dict[str, Any] = {
            "has_data": True,
            "统计周期": f"近{days}天",
            "数据说明": data_note,
            "执行质量评分": f"{exec_score}/100",
            "GMV完成率": f"{round(gmv_ach * 100, 1)}%",
            "预算vs实际对比": variance_table,
            "风险项": risk_items or ["各科目执行符合预算，无重大风险"],
        }

        # LLM差异分析
        try:
            from ._content_engine import _SYSTEM_FINANCE_EXPERT, _call
            unfav = [k for k, v in variance_table.items() if isinstance(v, dict) and v.get("状态") == "不利"]
            fav = [k for k, v in variance_table.items() if isinstance(v, dict) and v.get("状态") == "有利"]
            prompt = f"""预算执行分析（近{days}天）：
执行评分：{exec_score}/100，GMV完成率：{gmv_ach*100:.0f}%
预算GMV：{budget_gmv:,.0f}元 → 实际GMV：{actual_gmv:,.0f}元
预算利润率：{budget_margin}% → 实际利润率：{actual_margin}%
不利科目：{', '.join(unfav) or '无'}
有利科目：{', '.join(fav) or '无'}

请给出（180字内）：
1. 主要差异原因判断（结合电商运营场景）
2. 最需关注的2个科目及改善措施
3. 下期预算调整方向
中文，数据驱动，直接给建议。"""
            narrative = await _call(_SYSTEM_FINANCE_EXPERT, prompt, max_tokens=450, temperature=0.5)
            if narrative:
                result["AI差异解读"] = narrative
        except Exception:
            pass

        return result


class AccountingGMVWaterfall(SkillBase):
    """GMV→净利润瀑布分解 — 11步拆解每一元GMV的最终归宿，诊断利润漏损点"""

    def __init__(self) -> None:
        super().__init__(
            name="accounting_gmv_waterfall",
            display_name="GMV利润瀑布分析",
            description=(
                "11步瀑布图分解 GMV → 退款 → 净营收 → COGS → 毛利 → 物流 → 平台佣金 → 广告 → "
                "贡献利润 → 固定成本 → EBITDA → 税后净利润，"
                "计算每步漏损率，识别最大利润耗损环节，输出诊断健康评级。"
            ),
            category="accounting",
            input_schema={
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "统计天数，默认30"},
                    "cogs_rate": {"type": "number", "description": "商品成本率(%)，默认40"},
                    "logistics_rate": {"type": "number", "description": "物流费率(%)，默认8"},
                    "platform_fee_rate": {"type": "number", "description": "平台佣金率(%)，默认5"},
                    "fixed_opex": {"type": "number", "description": "固定运营成本（元/月），不填则估算"},
                    "tax_rate": {"type": "number", "description": "综合税率(%)，默认3"},
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        days: int = int(kwargs.get("days", 30))
        cogs_rate: float = kwargs.get("cogs_rate", 40) / 100
        logistics_rate: float = kwargs.get("logistics_rate", 8) / 100
        platform_fee_rate: float = kwargs.get("platform_fee_rate", 5) / 100
        tax_rate: float = kwargs.get("tax_rate", 3) / 100
        user_fixed_opex: float = kwargs.get("fixed_opex", 0)

        summary = await load_metrics_summary(user_id, days=days)
        if not summary.get("has_data"):
            return {"has_data": False, "提示": "暂无真实数据，请先导入店铺数据。"}

        totals = summary.get("totals", {})
        gmv: float = totals.get("gmv", 0)
        ad_spend: float = totals.get("ad_spend", 0)
        orders: float = totals.get("orders", 0)

        if gmv == 0:
            return {"has_data": False, "提示": "GMV为0，无法计算瀑布。"}

        # 退款率（从各平台均值）
        platforms = summary.get("platforms", {})
        refund_rates = [p.get("refund_rate", 0) for p in platforms.values() if p.get("refund_rate") is not None]
        avg_refund_rate = sum(refund_rates) / len(refund_rates) if refund_rates else 0.03

        # ── 11步瀑布计算 ──
        step1_gmv = round(gmv, 2)
        step2_refund = round(gmv * avg_refund_rate, 2)
        step3_net_rev = round(step1_gmv - step2_refund, 2)
        step4_cogs = round(step3_net_rev * cogs_rate, 2)
        step5_gross_profit = round(step3_net_rev - step4_cogs, 2)
        step6_logistics = round(step3_net_rev * logistics_rate, 2)
        step7_platform_fee = round(step1_gmv * platform_fee_rate, 2)
        step8_ad = round(ad_spend, 2)
        step9_contribution = round(step5_gross_profit - step6_logistics - step7_platform_fee - step8_ad, 2)
        step10_fixed = user_fixed_opex if user_fixed_opex > 0 else round(step3_net_rev * 0.03, 2)
        step11_ebitda = round(step9_contribution - step10_fixed, 2)
        step12_tax = round(max(step11_ebitda * tax_rate, 0), 2)
        step13_net_profit = round(step11_ebitda - step12_tax, 2)

        # 辅助：每步占GMV百分比（负值表示扣减）
        def pct(val: float) -> str:
            return f"{val / step1_gmv * 100:+.1f}%" if step1_gmv else "0%"

        def margin(val: float) -> str:
            return f"{val / step1_gmv * 100:.1f}%" if step1_gmv else "0%"

        waterfall = [
            {"步骤": "① GMV（总销售额）",      "金额": step1_gmv,       "占GMV比":  "100.0%",      "方向": "基准"},
            {"步骤": "  (-) 退款/退货",        "金额": -step2_refund,   "占GMV比": pct(-step2_refund), "方向": "扣减"},
            {"步骤": "② 净营收",               "金额": step3_net_rev,   "占GMV比": margin(step3_net_rev), "方向": "小计"},
            {"步骤": "  (-) 商品成本(COGS)",   "金额": -step4_cogs,     "占GMV比": pct(-step4_cogs), "方向": "扣减"},
            {"步骤": "③ 毛利润",               "金额": step5_gross_profit, "占GMV比": margin(step5_gross_profit), "方向": "小计"},
            {"步骤": "  (-) 物流/包装费",      "金额": -step6_logistics, "占GMV比": pct(-step6_logistics), "方向": "扣减"},
            {"步骤": "  (-) 平台佣金",         "金额": -step7_platform_fee, "占GMV比": pct(-step7_platform_fee), "方向": "扣减"},
            {"步骤": "  (-) 广告投放费",       "金额": -step8_ad,       "占GMV比": pct(-step8_ad), "方向": "扣减"},
            {"步骤": "④ 贡献利润(CM)",        "金额": step9_contribution, "占GMV比": margin(step9_contribution), "方向": "小计"},
            {"步骤": "  (-) 固定运营成本",     "金额": -step10_fixed,   "占GMV比": pct(-step10_fixed), "方向": "扣减"},
            {"步骤": "⑤ EBITDA",              "金额": step11_ebitda,   "占GMV比": margin(step11_ebitda), "方向": "小计"},
            {"步骤": "  (-) 税费",             "金额": -step12_tax,     "占GMV比": pct(-step12_tax), "方向": "扣减"},
            {"步骤": "⑥ 税后净利润",          "金额": step13_net_profit, "占GMV比": margin(step13_net_profit), "方向": "终值"},
        ]

        # 格式化金额为可读字符串
        for row in waterfall:
            row["金额"] = f"¥{row['金额']:,.2f}"

        # ── 漏损诊断（找最大扣减步骤）──
        deductions = [
            ("退款", step2_refund, step2_refund / step1_gmv),
            ("商品成本", step4_cogs, step4_cogs / step1_gmv),
            ("物流", step6_logistics, step6_logistics / step1_gmv),
            ("平台佣金", step7_platform_fee, step7_platform_fee / step1_gmv),
            ("广告", step8_ad, step8_ad / step1_gmv),
            ("固定成本", step10_fixed, step10_fixed / step1_gmv),
        ]
        deductions.sort(key=lambda x: x[2], reverse=True)
        top_leak = deductions[0]

        net_margin = round(step13_net_profit / step1_gmv * 100, 2)
        gross_margin = round(step5_gross_profit / step1_gmv * 100, 2)
        cm_margin = round(step9_contribution / step1_gmv * 100, 2)

        health = {
            "净利率": f"{net_margin}%",
            "评级": "优秀" if net_margin >= 15 else ("正常" if net_margin >= 5 else ("亏损" if net_margin < 0 else "偏低")),
            "最大漏损项": f"{top_leak[0]}（占GMV {top_leak[2]*100:.1f}%）",
            "改善建议": (
                f"当前最大成本耗损来自「{top_leak[0]}」（¥{top_leak[1]:,.0f}，占GMV {top_leak[2]*100:.1f}%）；"
                + (
                    "建议优先优化广告ROI，降低单次获客成本" if top_leak[0] == "广告"
                    else "建议与供应链谈判降低采购成本或提升商品附加值" if top_leak[0] == "商品成本"
                    else "建议提升包装效率或与物流谈量价" if top_leak[0] == "物流"
                    else "建议排查退款根因，优化商品描述和品控" if top_leak[0] == "退款"
                    else f"建议精细化管控{top_leak[0]}支出"
                )
            ),
        }

        result = {
            "has_data": True,
            "统计周期": f"近{days}天",
            "数据来源": "真实数据 + 参数估算",
            "GMV利润瀑布": waterfall,
            "核心利润率": {
                "毛利率": f"{gross_margin}%",
                "贡献利润率": f"{cm_margin}%",
                "净利率": f"{net_margin}%",
                "广告ROI": round(gmv / max(ad_spend, 1), 2) if ad_spend else "无广告数据",
                "客单价": f"¥{round(gmv / orders, 2)}" if orders else "N/A",
            },
            "健康度诊断": health,
        }

        # LLM深度解读
        try:
            from ._content_engine import _SYSTEM_FINANCE_EXPERT, _call
            leak_summary = "、".join([f"{d[0]}({d[2]*100:.1f}%)" for d in deductions[:3]])
            prompt = f"""GMV利润瀑布分析（近{days}天）：
GMV: ¥{gmv:,.0f}
主要成本耗损：{leak_summary}
毛利率: {gross_margin}% | 贡献利润率: {cm_margin}% | 净利率: {net_margin}%
{"广告ROI: " + str(round(gmv/ad_spend,2)) if ad_spend else ""}

请给出（200字内，中文）：
1. 从GMV到净利润的核心漏损路径分析
2. 与行业标准对比（毛利率30-50%正常，净利率5-15%正常）
3. 最优先的2个利润改善方向（含具体可行措施）
数据驱动，直接给结论。"""
            narrative = await _call(_SYSTEM_FINANCE_EXPERT, prompt, max_tokens=500, temperature=0.5)
            if narrative:
                result["AI利润诊断"] = narrative
        except Exception:
            pass

        return result


class AccountingScenarioAnalysis(SkillBase):
    """多维情景财务分析 — 乐观/基准/悲观三场景P&L + 敏感性矩阵"""

    def __init__(self) -> None:
        super().__init__(
            name="accounting_scenario_analysis",
            display_name="情景财务分析",
            description=(
                "构建乐观/基准/悲观三个情景的完整P&L预测，生成关键变量敏感性矩阵（价格×流量×转化率），"
                "量化每个情景下的净利润和现金流，为决策提供数字支撑。"
            ),
            category="accounting",
            input_schema={
                "type": "object",
                "properties": {
                    "base_gmv":        {"type": "number", "description": "基准月GMV（元）"},
                    "base_margin_pct": {"type": "number", "description": "基准毛利率(%)，默认30"},
                    "base_ad_pct":     {"type": "number", "description": "基准广告费率(%)，默认8"},
                    "base_refund_pct": {"type": "number", "description": "基准退款率(%)，默认5"},
                    "fixed_cost":      {"type": "number", "description": "固定成本/月（元）"},
                    "optimistic_growth": {"type": "number", "description": "乐观情景GMV增幅(%)，默认+30"},
                    "pessimistic_growth":{"type": "number", "description": "悲观情景GMV降幅(%)，默认-20"},
                    "sensitivity_vars": {
                        "type": "array", "items": {"type": "string"},
                        "description": "敏感性分析变量列表，如['price','traffic','cvr']",
                    },
                },
                "required": ["base_gmv"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id = kwargs.get("_user_id", 0)
        base_gmv = float(kwargs.get("base_gmv", 0))
        base_margin = float(kwargs.get("base_margin_pct", 30)) / 100
        base_ad = float(kwargs.get("base_ad_pct", 8)) / 100
        base_refund = float(kwargs.get("base_refund_pct", 5)) / 100
        fixed_cost = float(kwargs.get("fixed_cost", 0))
        opt_growth = float(kwargs.get("optimistic_growth", 30)) / 100
        pes_growth = float(kwargs.get("pessimistic_growth", -20)) / 100

        # 从真实数据自动填充（如果有）
        if user_id:
            try:
                summary = await load_metrics_summary(user_id, days=30)
                if summary.get("has_data"):
                    totals = summary.get("totals", {})
                    if not kwargs.get("base_gmv") and totals.get("gmv"):
                        base_gmv = float(totals["gmv"])
            except Exception:
                pass

        if base_gmv <= 0:
            return {"error": "请提供基准GMV（月销售额）"}

        def calc_scenario(gmv: float, label: str) -> Dict[str, Any]:
            net_revenue = gmv * (1 - base_refund)
            gross_profit = net_revenue * base_margin
            ad_spend = gmv * base_ad
            platform_fee = gmv * 0.04
            logistics = gmv * 0.06
            contribution = gross_profit - ad_spend - platform_fee - logistics
            operating_profit = contribution - fixed_cost
            tax = max(0, operating_profit * 0.25)
            net_profit = operating_profit - tax
            margin_pct = round(net_profit / gmv * 100, 1) if gmv > 0 else 0
            return {
                "情景": label,
                "GMV": round(gmv),
                "退款后净收入": round(net_revenue),
                "毛利润": round(gross_profit),
                "广告费": round(ad_spend),
                "平台佣金": round(platform_fee),
                "物流成本": round(logistics),
                "贡献利润": round(contribution),
                "固定成本": round(fixed_cost),
                "营业利润": round(operating_profit),
                "税后净利润": round(net_profit),
                "净利率": f"{margin_pct}%",
                "盈亏状态": "盈利" if net_profit > 0 else "亏损",
            }

        scenarios = [
            calc_scenario(base_gmv * (1 + opt_growth), f"乐观 (+{int(opt_growth*100)}% GMV)"),
            calc_scenario(base_gmv, "基准 (当前水平)"),
            calc_scenario(base_gmv * (1 + pes_growth), f"悲观 ({int(pes_growth*100)}% GMV)"),
        ]

        # 5×5 敏感性矩阵：价格变动(-20%到+20%) × 流量变动(-20%到+20%)
        sensitivity_matrix = []
        price_changes = [-0.20, -0.10, 0, 0.10, 0.20]
        traffic_changes = [-0.20, -0.10, 0, 0.10, 0.20]
        for pc in price_changes:
            row = []
            for tc in traffic_changes:
                adj_gmv = base_gmv * (1 + pc) * (1 + tc)
                r = calc_scenario(adj_gmv, "")
                row.append({
                    "价格变动": f"{'+' if pc>=0 else ''}{int(pc*100)}%",
                    "流量变动": f"{'+' if tc>=0 else ''}{int(tc*100)}%",
                    "净利润": r["税后净利润"],
                    "净利率": r["净利率"],
                })
            sensitivity_matrix.append(row)

        # 盈亏平衡点GMV
        bep_gmv = 0.0
        if (base_margin - base_ad - 0.04 - 0.06) * (1 - base_refund) > 0:
            net_contribution_rate = (base_margin - base_ad - 0.04 - 0.06) * (1 - base_refund)
            bep_gmv = (fixed_cost / net_contribution_rate) if net_contribution_rate > 0 else 0

        # ── 蒙特卡洛模拟（1000次） ──────────────────────────────
        import random
        import math
        mc_results: List[float] = []
        random.seed(42)
        for _ in range(1000):
            # 每个参数加正态分布噪声（std = 当前值×不确定系数）
            mc_gmv = base_gmv * max(0.1, random.gauss(1.0, 0.15))       # GMV ±15% std
            mc_margin = max(0.01, random.gauss(base_margin, 0.04))       # 毛利率 ±4pp std
            mc_ad = max(0.0, random.gauss(base_ad, 0.02))                # 广告费率 ±2pp std
            mc_refund = max(0.0, random.gauss(base_refund, 0.02))        # 退款率 ±2pp std
            # 计算净利润
            mc_net_rev = mc_gmv * (1 - mc_refund)
            mc_gross = mc_net_rev * mc_margin
            mc_ad_spend = mc_gmv * mc_ad
            mc_fee = mc_gmv * 0.04
            mc_logistics = mc_gmv * 0.06
            mc_contribution = mc_gross - mc_ad_spend - mc_fee - mc_logistics
            mc_op = mc_contribution - fixed_cost
            mc_tax = max(0, mc_op * 0.25)
            mc_net = mc_op - mc_tax
            mc_results.append(mc_net)

        mc_sorted = sorted(mc_results)
        n = len(mc_sorted)
        def pct(p: float) -> int:
            return round(mc_sorted[int(n * p / 100)])

        mc_summary = {
            "模拟次数": n,
            "P10（最差10%情景净利润）": pct(10),
            "P25（下四分位净利润）": pct(25),
            "P50（中位净利润）": pct(50),
            "P75（上四分位净利润）": pct(75),
            "P90（最优10%情景净利润）": pct(90),
            "亏损概率": f"{round(sum(1 for x in mc_results if x < 0) / n * 100, 1)}%",
            "期望净利润": round(sum(mc_results) / n),
            "净利润标准差": round(math.sqrt(sum((x - sum(mc_results)/n)**2 for x in mc_results) / n)),
        }

        # LLM情景解读
        interpretation = ""
        try:
            from ._content_engine import _call, _SYSTEM_FINANCE_EXPERT
            scenarios_text = "\n".join([
                f"• {s['情景']}：GMV ¥{s['GMV']:,} → 净利润 ¥{s['税后净利润']:,}（净利率{s['净利率']}）[{s['盈亏状态']}]"
                for s in scenarios
            ])
            prompt = (
                f"基于以下三情景P&L分析和蒙特卡洛风险评估，给出战略性诊断和决策建议（直接输出）：\n\n"
                f"## 三情景P&L\n{scenarios_text}\n\n"
                f"## 蒙特卡洛模拟（1000次随机情景）\n"
                f"• 期望净利润：¥{mc_summary['期望净利润']:,}\n"
                f"• 亏损概率：{mc_summary['亏损概率']}\n"
                f"• P10/P50/P90：¥{mc_summary['P10（最差10%情景净利润）']:,} / ¥{mc_summary['P50（中位净利润）']:,} / ¥{mc_summary['P90（最优10%情景净利润）']:,}\n"
                f"盈亏平衡GMV：¥{bep_gmv:,.0f}\n\n"
                "请分析：\n"
                "1. 当前财务韧性和尾部风险（基于蒙特卡洛）\n"
                "2. 从悲观到乐观的关键驱动因素\n"
                "3. 最需要保护的利润驱动器\n"
                "4. 2-3条具体优化行动（含量化目标）"
            )
            interpretation = await _call(_SYSTEM_FINANCE_EXPERT, prompt, max_tokens=700, temperature=0.3)
        except Exception:
            pass

        return {
            "三情景P&L对比": scenarios,
            "盈亏平衡GMV": round(bep_gmv),
            "敏感性矩阵（价格×流量）": sensitivity_matrix,
            "蒙特卡洛风险分析": mc_summary,
            "AI情景解读": interpretation or "（情景分析完成，LLM解读暂不可用）",
            "分析参数": {
                "基准毛利率": f"{base_margin*100:.0f}%",
                "广告费率": f"{base_ad*100:.0f}%",
                "退款率": f"{base_refund*100:.0f}%",
                "固定成本/月": round(fixed_cost),
            },
        }


ALL_SKILLS: list[SkillBase] = [
    AccountingCostCalc(),
    AccountingProfitAnalysis(),
    AccountingBudgetPlan(),
    AccountingROICalc(),
    AccountingComplianceCheck(),
    AccountingPLStatement(),
    AccountingBreakEvenCalc(),
    AccountingCashFlowForecast(),
    AccountingFinancialNarrative(),
    AccountingBudgetVsActual(),
    AccountingGMVWaterfall(),
    AccountingScenarioAnalysis(),
]
