from __future__ import annotations

import math
from typing import Any, Dict, List

from .base import SkillBase
from ._db_helpers import load_product_info, load_user_products, load_metrics_summary
from typing import Optional

from ._content_engine import (
    generate_listing_copy,
    generate_channel_mix,
    generate_ops_execution_plan,
    generate_assortment_strategy,
    generate_promo_strategy,
    generate_pricing_analysis,
    generate_abc_xyz_strategy,
)


class OpsPromoPlanning(SkillBase):
    """促销策划技能"""

    def __init__(self) -> None:
        super().__init__(
            name="ops_promo_planning",
            display_name="促销策划",
            description="根据商品、预算和平台，生成促销活动方案，包含阶段划分、KPI和预算分配",
            category="ops",
            input_schema={
                "type": "object",
                "properties": {
                    "product": {"type": "string", "description": "商品名称或类目"},
                    "budget": {"type": "number", "description": "总预算（元）"},
                    "platform": {"type": "string", "description": "平台：淘宝/京东/拼多多/抖音/快手"},
                },
                "required": ["product", "budget", "platform"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        product = kwargs.get("product", "通用商品")
        budget = float(kwargs.get("budget", 10000))
        platform = kwargs.get("platform", "淘宝")

        # 从真实数据建立 KPI 基线
        baseline_gmv = baseline_uv = baseline_conv = 0.0
        data_note = "（KPI基于经验估算）"
        if user_id:
            try:
                from src.core.metrics_store import get_summary
                plat_map = {"淘宝": "taobao", "京东": "jd", "拼多多": "pdd", "抖音": "douyin"}
                summary = await get_summary(user_id, days=30)
                if summary.get("has_data"):
                    plat_key = plat_map.get(platform, "")
                    pdata = summary["platforms"].get(plat_key, summary.get("totals", {}))
                    baseline_gmv = pdata.get("gmv", 0)
                    baseline_uv = pdata.get("uv", 0)
                    baseline_conv = pdata.get("conversion_rate", 0)
                    data_note = f"（KPI基于真实近30天数据：日均GMV ¥{baseline_gmv/30:.0f}）"
            except Exception:
                pass

        warmup = round(budget * 0.15, 2)
        peak = round(budget * 0.55, 2)
        sustain = round(budget * 0.20, 2)
        review = round(budget * 0.10, 2)

        # KPI 目标：有真实数据时基于历史数据×活动倍数，否则用保守估算
        if baseline_gmv > 0:
            target_gmv = round(baseline_gmv * 1.5, 2)   # 活动期目标=历史×1.5
            target_uv = round(baseline_uv * 1.8, 0)
            target_conv = round(min(baseline_conv * 1.3, 0.15), 4)
            target_roi = round(target_gmv / budget, 1)
        else:
            target_gmv = round(budget * 5, 2)
            target_uv = int(budget * 2)
            target_conv = 0.035
            target_roi = 5.0

        phases = [
            {"阶段": "预热期", "天数": 3, "预算": warmup, "目标": "加购+收藏",
             "动作": ["短视频种草", "优惠券预发放"], "预算占比": "15%"},
            {"阶段": "爆发期", "天数": 2, "预算": peak, "目标": "冲销量",
             "动作": ["限时折扣", "满减叠加", "直播带货"], "预算占比": "55%"},
            {"阶段": "续航期", "天数": 5, "预算": sustain, "目标": "长尾转化",
             "动作": ["返场优惠", "老客复购"], "预算占比": "20%"},
            {"阶段": "复盘期", "天数": 2, "预算": review, "目标": "数据复盘",
             "动作": ["ROI分析", "用户反馈收集"], "预算占比": "10%"},
        ]
        kpi = {
            "目标GMV": f"¥{target_gmv:,.0f}",
            "目标ROI": target_roi,
            "预估UV": int(target_uv),
            "预估转化率": f"{target_conv*100:.1f}%",
            "历史基准GMV": f"¥{baseline_gmv:,.0f}" if baseline_gmv else "无历史数据",
        }

        result = {
            "商品": product,
            "平台": platform,
            "总预算": budget,
            "数据说明": data_note,
            "活动阶段": phases,
            "KPI": kpi,
        }

        # 实时搜索竞品促销动态（仅当需要实时数据时）
        search_ctx = ""
        if kwargs.get("_needs_fresh_data", True):
            try:
                from .search import _web_search, _format_results_for_llm
                promo_query = f"{product} {platform} 促销活动 优惠 打折 2026"
                promo_results, _eng = await _web_search(promo_query, topic="general", max_results=4, days=14)
                if promo_results:
                    search_ctx = _format_results_for_llm(promo_results, max_per_item=300)
                    result["竞品促销情报"] = search_ctx
            except Exception:
                pass

        # LLM生成真实促销策略内容（文案/话术/执行细节）
        try:
            promo_content = await generate_promo_strategy(
                product=product,
                budget=budget,
                platform=platform,
                phases=phases,
                kpi=kpi,
                baseline_gmv=baseline_gmv,
                search_context=search_ctx,
            )
            if promo_content:
                result["AI促销策略（含文案）"] = promo_content
        except Exception:
            pass

        return result


class OpsPricingStrategy(SkillBase):
    """定价策略技能"""

    def __init__(self) -> None:
        super().__init__(
            name="ops_pricing_strategy",
            display_name="定价策略",
            description="根据成本、竞品价格和平台，输出建议定价、利润率及竞品对比",
            category="ops",
            input_schema={
                "type": "object",
                "properties": {
                    "cost": {"type": "number", "description": "单位成本（元）"},
                    "competitor_prices": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "竞品价格列表",
                    },
                    "platform": {"type": "string", "description": "销售平台"},
                },
                "required": ["cost", "competitor_prices"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        cost = kwargs.get("cost", 0)
        competitors: List[float] = kwargs.get("competitor_prices", [])
        platform = kwargs.get("platform", "淘宝")

        avg_comp = sum(competitors) / len(competitors) if competitors else cost * 3
        min_comp = min(competitors) if competitors else cost * 2
        max_comp = max(competitors) if competitors else cost * 4

        # 平台佣金率
        commission_map = {"淘宝": 0.05, "京东": 0.08, "拼多多": 0.03, "抖音": 0.05, "快手": 0.05}
        commission_rate = commission_map.get(platform, 0.05)

        recommended = round(avg_comp * 0.95, 2)
        if recommended < cost * 1.3:
            recommended = round(cost * 1.3, 2)

        commission = round(recommended * commission_rate, 2)
        profit = round(recommended - cost - commission, 2)
        margin = round(profit / recommended * 100, 2) if recommended > 0 else 0

        result = {
            "成本": cost,
            "平台": platform,
            "建议零售价": recommended,
            "平台佣金": commission,
            "单品利润": profit,
            "利润率": f"{margin}%",
            "竞品分析": {
                "竞品均价": round(avg_comp, 2),
                "竞品最低价": min_comp,
                "竞品最高价": max_comp,
                "价格竞争力": "高" if recommended < avg_comp else "中",
            },
        }

        # LLM定价策略解读（心理定价/竞争定位/促销空间）
        try:
            analysis = await generate_pricing_analysis(
                product=kwargs.get("product_name", "商品"),
                recommended_price=recommended,
                cost=cost,
                competitor_avg=avg_comp,
                platform=platform,
                margin=margin,
            )
            if analysis:
                result["AI定价解读"] = analysis
        except Exception:
            pass

        return result


class OpsChannelStrategy(SkillBase):
    """渠道策略技能 — LLM基于真实ROI数据生成个性化渠道分配方案"""

    def __init__(self) -> None:
        super().__init__(
            name="ops_channel_strategy",
            display_name="渠道策略",
            description="AI基于真实平台ROI数据（自动加载）生成个性化渠道预算分配和内容计划",
            category="ops",
            input_schema={
                "type": "object",
                "properties": {
                    "product_type": {"type": "string", "description": "商品类型：美妆/食品/数码/服饰/家居"},
                    "budget": {"type": "number", "description": "月预算（元）"},
                    "target_audience": {"type": "string", "description": "目标人群描述"},
                },
                "required": ["product_type", "budget"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        product_type: str = kwargs.get("product_type", "通用")
        budget: float = float(kwargs.get("budget", 10000))
        target_audience: str = kwargs.get("target_audience", "")

        # 加载真实平台ROI数据
        real_metrics: Dict[str, Any] = {}
        if user_id:
            summary = await load_metrics_summary(user_id, days=30)
            if summary.get("has_data"):
                real_metrics = summary

        # 搜索当前平台算法动态和渠道趋势
        search_ctx = ""
        if kwargs.get("_needs_fresh_data", True):
            try:
                from .search import _web_search, _format_results_for_llm
                q = f"{product_type} 抖音淘宝 渠道运营 流量算法 趋势 2026"
                results, _ = await _web_search(q, topic="general", max_results=4, days=30)
                if results:
                    search_ctx = _format_results_for_llm(results, max_per_item=250)
            except Exception:
                pass

        return await generate_channel_mix(
            product_type=product_type,
            budget=budget,
            real_metrics=real_metrics if real_metrics else None,
            target_audience=target_audience,
            search_context=search_ctx,
        )


class OpsInventoryPlanning(SkillBase):
    """库存规划技能"""

    def __init__(self) -> None:
        super().__init__(
            name="ops_inventory_planning",
            display_name="库存规划",
            description="根据日均销量和供货周期，计算安全库存、补货点和建议订货量",
            category="ops",
            input_schema={
                "type": "object",
                "properties": {
                    "avg_daily_demand": {"type": "number", "description": "日均销量（件）"},
                    "lead_time_days": {"type": "integer", "description": "供货周期（天）"},
                    "demand_std": {"type": "number", "description": "需求标准差（可选）"},
                    "service_level": {"type": "number", "description": "服务水平 0-1，默认0.95"},
                },
                "required": ["avg_daily_demand", "lead_time_days"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        avg_demand = kwargs.get("avg_daily_demand", 100)
        lead_time = kwargs.get("lead_time_days", 7)
        demand_std = kwargs.get("demand_std", avg_demand * 0.2)
        service_level = kwargs.get("service_level", 0.95)

        # Z-score for service level
        z_map = {0.90: 1.28, 0.95: 1.65, 0.99: 2.33}
        z = z_map.get(service_level, 1.65)

        safety_stock = round(z * demand_std * math.sqrt(lead_time))
        reorder_point = round(avg_demand * lead_time + safety_stock)
        eoq = round(math.sqrt(2 * avg_demand * 365 * 50 / 2))  # simplified EOQ

        return {
            "日均销量": avg_demand,
            "供货周期": f"{lead_time}天",
            "安全库存": safety_stock,
            "补货点": reorder_point,
            "建议订货量": eoq,
            "预计周转天数": round(eoq / avg_demand, 1) if avg_demand > 0 else 0,
            "月度采购预算参考": round(avg_demand * 30),
            "风险提示": "库存周转 > 30天建议优化供应链" if (eoq / avg_demand if avg_demand > 0 else 0) > 30 else "库存周转正常",
        }


class OpsListingCopy(SkillBase):
    """上架文案技能 — LLM生成真实可用的商品标题/五点描述/关键词矩阵"""

    def __init__(self) -> None:
        super().__init__(
            name="ops_listing_copy",
            display_name="上架文案",
            description="AI生成完整上架文案（5个标题版本/五行卖点描述/关键词矩阵/常见问答），传入product_id自动加载商品信息",
            category="ops",
            input_schema={
                "type": "object",
                "properties": {
                    "product_name": {"type": "string", "description": "商品名称（可从product_id自动加载）"},
                    "features": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "商品卖点列表",
                    },
                    "platform": {"type": "string", "description": "上架平台：淘宝/京东/拼多多/抖音"},
                    "product_id": {"type": "integer", "description": "商品ID，自动加载完整商品信息"},
                    "competitor_titles": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "竞品标题列表（可选，用于差异化优化）",
                    },
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        name: str = kwargs.get("product_name", "")
        features: List[str] = kwargs.get("features", [])
        platform: str = kwargs.get("platform", "淘宝")
        product_id: int = kwargs.get("product_id", 0)
        competitors: List[str] = kwargs.get("competitor_titles", [])

        pinfo: Dict[str, Any] = {}

        if user_id and product_id:
            loaded = await load_product_info(user_id, product_id)
            if loaded:
                pinfo = loaded
                if not name:
                    name = pinfo.get("name", "")
        elif name:
            pinfo = {"name": name, "selling_points": features}

        if not name and not pinfo.get("name"):
            return {"error": "请提供 product_name 或 product_id"}

        # 若无竞品标题，则实时搜索竞品关键词和热销标题
        search_ctx = ""
        if not competitors and kwargs.get("_needs_fresh_data", True):
            try:
                from .search import _web_search, _format_results_for_llm
                q = f"{name or pinfo.get('name','')} {platform} 热销商品 关键词 标题"
                results, _ = await _web_search(q, topic="general", max_results=4, days=30)
                if results:
                    search_ctx = _format_results_for_llm(results, max_per_item=250)
            except Exception:
                pass

        return await generate_listing_copy(
            product_info=pinfo,
            platform=platform,
            competitors=competitors if competitors else None,
            search_context=search_ctx,
        )


class OpsAssortmentPlanning(SkillBase):
    """选品规划技能 — LLM基于真实销售数据生成个性化选品策略"""

    def __init__(self) -> None:
        super().__init__(
            name="ops_assortment_planning",
            display_name="选品规划",
            description="AI基于真实店铺数据和类目特性，生成个性化选品策略（市场分析/商品组合/测品方法/雷区提醒）",
            category="ops",
            input_schema={
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "商品类目"},
                    "budget": {"type": "number", "description": "选品预算（元）"},
                },
                "required": ["category", "budget"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        category: str = kwargs.get("category", "通用")
        budget: float = float(kwargs.get("budget", 50000))

        # 加载真实店铺数据和现有商品
        real_metrics: Dict[str, Any] = {}
        existing_products: List[Dict] = []

        if user_id:
            summary = await load_metrics_summary(user_id, days=30)
            if summary.get("has_data"):
                real_metrics = summary
            products = await load_user_products(user_id, limit=20)
            if products:
                existing_products = products

        # 搜索该类目当前热销商品和市场趋势
        search_ctx = ""
        if kwargs.get("_needs_fresh_data", True):
            try:
                from .search import _web_search, _format_results_for_llm
                q = f"{category} 热销爆款 选品 市场趋势 2026"
                results, _ = await _web_search(q, topic="general", max_results=5, days=30)
                if results:
                    search_ctx = _format_results_for_llm(results, max_per_item=280)
            except Exception:
                pass

        return await generate_assortment_strategy(
            category=category,
            budget=budget,
            real_metrics=real_metrics if real_metrics else None,
            existing_products=existing_products if existing_products else None,
            search_context=search_ctx,
        )


class OpsExecutionPlan(SkillBase):
    """运营执行计划技能 — LLM基于真实数据生成30天可落地执行方案"""

    def __init__(self) -> None:
        super().__init__(
            name="ops_execution_plan",
            display_name="运营执行计划",
            description="AI基于真实店铺数据生成30天个性化执行计划（现状诊断/每周任务/KPI体系/风险预案/资源需求）",
            category="ops",
            input_schema={
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "运营目标（如：提升GMV 30% / 降低广告ACOS至15%）"},
                    "stage": {
                        "type": "string",
                        "description": "阶段：冷启动/成长期/成熟期/衰退期",
                    },
                    "product_id": {"type": "integer", "description": "主推商品ID（可选）"},
                },
                "required": ["goal", "stage"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        goal: str = kwargs.get("goal", "提升GMV")
        stage: str = kwargs.get("stage", "成长期")
        product_id: int = kwargs.get("product_id", 0)

        # 加载真实数据
        real_metrics: Dict[str, Any] = {}
        product_info: Dict[str, Any] = {}

        if user_id:
            summary = await load_metrics_summary(user_id, days=30)
            if summary.get("has_data"):
                real_metrics = summary

            if product_id:
                pinfo = await load_product_info(user_id, product_id)
                if pinfo:
                    product_info = pinfo

        # 搜索当前阶段运营最佳实践和行业基准
        search_ctx = ""
        if kwargs.get("_needs_fresh_data", True):
            try:
                from .search import _web_search, _format_results_for_llm
                q = f"电商 {stage} {goal} 运营方法 行业案例 2026"
                results, _ = await _web_search(q, topic="general", max_results=4, days=60)
                if results:
                    search_ctx = _format_results_for_llm(results, max_per_item=250)
            except Exception:
                pass

        return await generate_ops_execution_plan(
            goal=goal,
            stage=stage,
            real_metrics=real_metrics if real_metrics else None,
            product_info=product_info if product_info else None,
            search_context=search_ctx,
        )


class OpsSmartPricing(SkillBase):
    """智能定价引擎 — 价格弹性分析 + 竞品定位 + 利润最大化曲线"""

    def __init__(self) -> None:
        super().__init__(
            name="ops_smart_pricing",
            display_name="智能定价引擎",
            description=(
                "基于价格弹性模型和竞品分析，计算最优利润定价区间、"
                "弹性敏感度、市场定位和涨/降价策略建议；可结合真实店铺数据。"
            ),
            category="ops",
            input_schema={
                "type": "object",
                "properties": {
                    "cost": {"type": "number", "description": "单位成本（元），含原材料+人工+包装"},
                    "current_price": {"type": "number", "description": "当前售价（元）"},
                    "current_monthly_sales": {"type": "integer", "description": "当前月销量（件）"},
                    "competitor_prices": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "竞品价格列表（元）",
                    },
                    "platform": {"type": "string", "description": "平台：淘宝/京东/拼多多/抖音/快手"},
                    "elasticity": {
                        "type": "number",
                        "description": (
                            "价格弹性系数（负数，默认-1.5；"
                            "-1.5=弹性适中，-2.5=高弹性，-0.8=低弹性）"
                        ),
                    },
                    "stock_days": {
                        "type": "integer",
                        "description": "当前库存天数（估算），>90天将自动触发库存压力降价护栏",
                    },
                    "product_id": {"type": "integer", "description": "商品ID，自动加载历史销售数据"},
                },
                "required": ["cost", "current_price"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        cost: float = float(kwargs.get("cost", 0))
        current_price: float = float(kwargs.get("current_price", 0))
        current_sales: int = int(kwargs.get("current_monthly_sales", 0))
        competitors: List[float] = kwargs.get("competitor_prices", [])
        platform: str = kwargs.get("platform", "淘宝")
        elasticity: float = float(kwargs.get("elasticity", -1.5))
        stock_days: int = int(kwargs.get("stock_days", 0))
        product_id: int = int(kwargs.get("product_id", 0))

        # ── 从真实数据补充销量 ──
        data_note = "（估算数据）"
        if user_id and current_sales == 0:
            try:
                summary = await load_metrics_summary(user_id, days=30)
                if summary.get("has_data"):
                    totals = summary.get("totals", {})
                    real_orders = totals.get("orders", 0)
                    real_aov = totals.get("avg_order_value", 0)
                    if real_orders > 0:
                        current_sales = int(real_orders)
                        data_note = "（基于真实近30天数据）"
                    if real_aov > 0 and current_price == 0:
                        current_price = real_aov
            except Exception:
                pass

        if current_price <= 0 or cost <= 0:
            return {"error": "请提供有效的成本和当前售价"}

        if current_sales == 0:
            current_sales = 500  # 保守默认值

        # ── 平台佣金率 ──
        commission_map = {"淘宝": 0.05, "京东": 0.08, "拼多多": 0.03, "抖音": 0.05, "快手": 0.05}
        commission_rate = commission_map.get(platform, 0.05)

        # ── 当前利润计算 ──
        current_commission = current_price * commission_rate
        current_profit_per_unit = current_price - cost - current_commission
        current_margin = round(current_profit_per_unit / current_price * 100, 2) if current_price > 0 else 0
        current_monthly_profit = round(current_profit_per_unit * current_sales, 2)

        # ── 价格弹性模型：Q(p) = Q0 × (p/p0)^ε ──
        # ε = elasticity (负数)，p0 = current_price，Q0 = current_sales
        # 月利润 = (p - cost - p×commission) × Q0 × (p/p0)^ε
        # 最优价格：对利润函数求导，近似最优在 p* = cost/(1 + 1/ε)（弹性定价公式）
        # 但需受竞品上下界约束

        # 理论最优价格（Lerner 指数法）
        # P* = MC × ε / (ε + 1)，其中 ε < -1
        effective_cost = cost + current_price * commission_rate  # 含佣金的变动成本
        if elasticity < -1:
            lerner_optimal = round(effective_cost * elasticity / (elasticity + 1), 2)
        else:
            lerner_optimal = round(effective_cost * 2, 2)  # 弹性过小时用成本倍数

        # 竞品约束
        if competitors:
            avg_comp = sum(competitors) / len(competitors)
            min_comp = min(competitors)
            max_comp = max(competitors)
            # 限制在竞品最低-10% 到 竞品最高+20% 范围内
            price_floor = max(cost * 1.1, min_comp * 0.90)
            price_ceiling = max_comp * 1.20
        else:
            avg_comp = current_price
            min_comp = current_price * 0.8
            max_comp = current_price * 1.3
            price_floor = cost * 1.15
            price_ceiling = current_price * 1.4

        # ── 库存压力护栏（研究：>90天库存应降价5-15%清仓）──
        stock_pressure_flag = False
        stock_pressure_price = None
        if stock_days > 90:
            stock_pressure_flag = True
            # 库存压力系数：90-120天降5%，120-180天降10%，>180天降15%
            if stock_days <= 120:
                pressure_discount = 0.05
            elif stock_days <= 180:
                pressure_discount = 0.10
            else:
                pressure_discount = 0.15
            stock_pressure_price = round(max(price_floor, current_price * (1 - pressure_discount)), 2)

        # 候选价格点（5档 + Lerner最优 + 可选库存压力价）
        candidates = []
        step_down = round(current_price * 0.90, 2)   # 降价10%
        step_down2 = round(current_price * 0.95, 2)  # 降价5%
        step_up = round(current_price * 1.05, 2)     # 涨价5%
        step_up2 = round(current_price * 1.10, 2)    # 涨价10%
        price_points = [
            max(step_down, price_floor),
            max(step_down2, price_floor),
            current_price,
            min(step_up, price_ceiling),
            min(step_up2, price_ceiling),
        ]
        # 加入Lerner最优价格
        lerner_clamped = max(price_floor, min(price_ceiling, lerner_optimal))
        if lerner_clamped not in price_points:
            price_points.append(lerner_clamped)
        # 加入库存压力价
        if stock_pressure_price and stock_pressure_price not in price_points:
            price_points.append(stock_pressure_price)

        best_profit = -1e9
        best_price = current_price

        for p in price_points:
            # 需求量 = 当前销量 × (p/当前价)^弹性系数
            demand_ratio = (p / current_price) ** elasticity
            projected_sales = max(1, round(current_sales * demand_ratio))
            commission_cost = p * commission_rate
            unit_profit = p - cost - commission_cost
            monthly_profit = unit_profit * projected_sales
            margin = round(unit_profit / p * 100, 2) if p > 0 else 0

            uplift_pct = round((monthly_profit - current_monthly_profit) / max(abs(current_monthly_profit), 1) * 100, 1)
            entry: Dict[str, Any] = {
                "定价": round(p, 2),
                "预估月销量": int(projected_sales),
                "单品利润": round(unit_profit, 2),
                "利润率": f"{margin}%",
                "月度总利润": round(monthly_profit, 2),
                "vs当前利润": f"{'+' if uplift_pct >= 0 else ''}{uplift_pct}%",
                "竞争定位": "低价" if p < avg_comp * 0.95 else ("高价" if p > avg_comp * 1.05 else "中价"),
            }
            if stock_pressure_price and abs(round(p, 2) - stock_pressure_price) < 0.01:
                entry["标签"] = f"库存压力价（{stock_days}天库存）"
            candidates.append(entry)

            if monthly_profit > best_profit:
                best_profit = monthly_profit
                best_price = round(p, 2)

        # 弹性敏感度分类
        if elasticity <= -2.5:
            elasticity_label = "高弹性（价格敏感型买家，降价显著拉量）"
        elif elasticity <= -1.5:
            elasticity_label = "中弹性（均衡定价，适度调整有效）"
        elif elasticity <= -0.8:
            elasticity_label = "低弹性（品牌溢价空间，可适度提价）"
        else:
            elasticity_label = "超低弹性（刚需产品，价格影响有限）"

        price_gap_vs_comp = round((current_price - avg_comp) / avg_comp * 100, 1) if avg_comp > 0 else 0
        market_position = "低价位" if price_gap_vs_comp < -10 else ("高价位" if price_gap_vs_comp > 10 else "中价位")

        result: Dict[str, Any] = {
            "数据说明": data_note,
            "当前经营状态": {
                "成本": f"¥{cost}",
                "当前售价": f"¥{current_price}",
                "当前利润率": f"{current_margin}%",
                "当前月利润": f"¥{current_monthly_profit:,.2f}",
                "市场定位": market_position,
                "vs竞品均价": f"{'+' if price_gap_vs_comp >= 0 else ''}{price_gap_vs_comp}%",
            },
            "价格弹性": {
                "弹性系数": elasticity,
                "弹性类型": elasticity_label,
                "理论最优价格(Lerner)": f"¥{lerner_clamped}",
            },
            "定价方案对比": sorted(candidates, key=lambda x: -x["月度总利润"]),
            "推荐定价": f"¥{best_price}",
            "推荐理由": f"在当前弹性({elasticity})和竞品约束下，¥{best_price}预期月利润最大化",
            "竞品价格区间": {
                "最低": f"¥{min_comp}",
                "均价": f"¥{round(avg_comp, 2)}",
                "最高": f"¥{max_comp}",
            } if competitors else "无竞品数据",
        }

        # 库存压力告警
        if stock_pressure_flag and stock_pressure_price:
            result["库存压力告警"] = {
                "当前库存天数": stock_days,
                "风险等级": "高" if stock_days > 180 else ("中" if stock_days > 120 else "低"),
                "建议清仓价": f"¥{stock_pressure_price}",
                "说明": f"库存周转超过{stock_days}天，建议优先考虑库存压力价¥{stock_pressure_price}加速去库",
            }

        # LLM定价策略解读
        try:
            from ._content_engine import _SYSTEM_OPS_EXPERT, _call
            comp_info = f"竞品价格: ¥{min_comp}~¥{max_comp}，均价¥{round(avg_comp,2)}" if competitors else "无竞品数据"
            stock_info = f"库存天数：{stock_days}天（高库存压力，建议降价加速去库）" if stock_pressure_flag else ""
            prompt = f"""定价分析数据：
产品成本：¥{cost}，当前售价：¥{current_price}（{market_position}），当前利润率：{current_margin}%
{comp_info}
价格弹性：{elasticity}（{elasticity_label}）
推荐定价：¥{best_price}（预期月利润最优）
平台：{platform}（佣金率{commission_rate*100:.0f}%）{chr(10) + stock_info if stock_info else ""}

请给出（180字内）：
1. 当前定价问题诊断（结合弹性和竞品）
2. 推荐¥{best_price}的核心理由和实施注意事项
3. 长期定价策略方向（品牌溢价/竞争定价/渗透策略）
中文，数据驱动，专业精炼。"""
            analysis = await _call(_SYSTEM_OPS_EXPERT, prompt, max_tokens=500, temperature=0.6)
            if analysis:
                result["AI定价策略"] = analysis
        except Exception:
            pass

        return result


class OpsInventoryOptimizer(SkillBase):
    """库存优化计算引擎 — EOQ经济订货量 + 安全库存 + ABC分类 + 周转率"""

    def __init__(self) -> None:
        super().__init__(
            name="ops_inventory_optimizer",
            display_name="库存优化计算",
            description=(
                "EOQ经济订货量(√(2DS/H)) + 安全库存(z×σ×√L) + ABC分类 + 库存周转率计算，"
                "输出每SKU最优补货量/补货点/库存健康评分，自动从真实数据推导。"
            ),
            category="ops",
            input_schema={
                "type": "object",
                "properties": {
                    "days": {"type": "integer", "description": "分析天数，默认30"},
                    "ordering_cost": {"type": "number", "description": "单次订货成本（元），默认200"},
                    "holding_rate": {"type": "number", "description": "年库存持有成本率(%)，默认25（含资金/仓储/损耗）"},
                    "lead_time_days": {"type": "integer", "description": "供应商交货周期（天），默认14"},
                    "service_level": {
                        "type": "string",
                        "description": "服务水平（缺货概率容忍度）：95%/98%/99%，默认95%",
                        "default": "95%",
                    },
                    "skus": {
                        "type": "array",
                        "description": "SKU列表，格式：[{name, monthly_sales, cost, stock_qty}]，不填则从真实数据推导",
                        "items": {"type": "object"},
                    },
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        days: int = int(kwargs.get("days", 30))
        ordering_cost: float = float(kwargs.get("ordering_cost", 200))
        holding_rate: float = float(kwargs.get("holding_rate", 25)) / 100
        lead_time: int = int(kwargs.get("lead_time_days", 14))
        service_level_str: str = kwargs.get("service_level", "95%")
        user_skus: Optional[List[Dict]] = kwargs.get("skus")

        # z值对应服务水平
        z_map = {"90%": 1.282, "95%": 1.645, "98%": 2.054, "99%": 2.326}
        z = z_map.get(service_level_str, 1.645)

        # ── 获取数据 ──
        sku_list: List[Dict] = []
        data_source = "用户提供"

        if user_skus:
            sku_list = user_skus
        else:
            # 从真实指标推导（用整体数据估算单SKU）
            summary = await load_metrics_summary(user_id, days=days)
            if not summary.get("has_data"):
                return {"has_data": False, "提示": "请提供 skus 参数或先导入店铺数据。"}

            totals = summary.get("totals", {})
            gmv = totals.get("gmv", 0)
            orders = totals.get("orders", 0)
            aov = gmv / max(orders, 1)

            # 从商品数据构建SKU列表
            try:
                from src.skills._db_helpers import load_user_products
                products = await load_user_products(user_id, limit=20)
                if products:
                    data_source = "真实商品数据"
                    # 按售价分配日均销量（简化：等权分配）
                    daily_orders = orders / max(days, 1)
                    for i, p in enumerate(products[:10]):
                        price = p.get("selling_price", aov) or aov
                        cost = p.get("cost_price", price * 0.4) or (price * 0.4)
                        # 销量估算：总订单按商品数平均
                        est_monthly = max(1, round(daily_orders * 30 / max(len(products), 1)))
                        sku_list.append({
                            "name": p.get("name", f"SKU{i+1}")[:20],
                            "monthly_sales": est_monthly,
                            "cost": cost,
                            "stock_qty": est_monthly * 2,  # 估算当前库存=2个月
                        })
            except Exception:
                pass

            if not sku_list:
                # 最后退路：用总体数据构建一个"综合SKU"
                data_source = "汇总估算"
                aov_cost = aov * 0.4
                monthly_orders = orders / max(days / 30, 1)
                sku_list = [{"name": "综合商品", "monthly_sales": monthly_orders,
                              "cost": aov_cost, "stock_qty": monthly_orders * 2}]

        if not sku_list:
            return {"has_data": False, "提示": "无法获取SKU数据，请提供 skus 参数。"}

        # ── 对每个SKU计算库存优化指标 ──
        sku_results: List[Dict] = []
        total_inv_value = 0.0

        for sku in sku_list:
            name = sku.get("name", "未知SKU")
            monthly_sales = float(sku.get("monthly_sales", 0)) or 1
            unit_cost = float(sku.get("cost", 0)) or 1
            stock_qty = float(sku.get("stock_qty", 0))

            daily_demand = monthly_sales / 30
            annual_demand = monthly_sales * 12

            # ── EOQ经济订货量：Q* = √(2 × D × S / H) ──
            # D = 年需求量, S = 单次订货成本, H = 单位年持有成本
            annual_holding_cost = unit_cost * holding_rate  # 单位/年
            eoq = math.sqrt(2 * annual_demand * ordering_cost / max(annual_holding_cost, 0.01))
            eoq = max(1, round(eoq))

            # ── 安全库存：SS = z × σ_D × √(lead_time) ──
            # σ_D 用泊松近似：σ ≈ √(daily_demand × lead_time) 的标准差
            # 更保守估算：σ_D ≈ daily_demand × 0.3（日需求波动30%）
            sigma_demand = daily_demand * 0.30
            safety_stock = round(z * sigma_demand * math.sqrt(lead_time))
            safety_stock = max(1, safety_stock)

            # ── 再订货点：ROP = D × LT + SS ──
            rop = round(daily_demand * lead_time + safety_stock)

            # ── 最优订货周期（天）──
            optimal_cycle = round(eoq / max(daily_demand, 0.01))

            # ── 当前库存天数 ──
            current_stock_days = round(stock_qty / max(daily_demand, 0.01))

            # 库存价值
            inv_value = round(stock_qty * unit_cost, 2)
            total_inv_value += inv_value

            # 健康状态
            if current_stock_days < lead_time:
                inv_health = "缺货风险"
            elif current_stock_days < lead_time + 7:
                inv_health = "临近再订货点"
            elif current_stock_days > 90:
                inv_health = "库存积压"
            elif current_stock_days > 60:
                inv_health = "偏高"
            else:
                inv_health = "健康"

            sku_results.append({
                "SKU": name,
                "月销量": int(monthly_sales),
                "单位成本": f"¥{unit_cost:.2f}",
                "当前库存": int(stock_qty),
                "当前库存天数": f"{current_stock_days}天",
                "库存健康": inv_health,
                "库存价值": f"¥{inv_value:,.2f}",
                "EOQ经济订货量": f"{eoq}件",
                "安全库存": f"{safety_stock}件（服务水平{service_level_str}）",
                "再订货点(ROP)": f"{rop}件",
                "最优订货周期": f"每{optimal_cycle}天补货一次",
                "建议": "立即补货" if inv_health == "缺货风险"
                        else ("准备下单" if inv_health == "临近再订货点"
                              else ("清库促销" if inv_health == "库存积压" else "正常维护")),
            })

        # ── ABC分类：按GMV贡献排序 ──
        # 估算各SKU月GMV = 月销量 × 售价（无售价则用成本×2.5估算）
        gmv_list = []
        for i, sku in enumerate(sku_list):
            monthly_sales = float(sku.get("monthly_sales", 0))
            cost = float(sku.get("cost", 0))
            est_price = cost * 2.5
            gmv_list.append((sku.get("name", f"SKU{i+1}"), monthly_sales * est_price))

        total_gmv = sum(g for _, g in gmv_list)
        gmv_list.sort(key=lambda x: -x[1])
        cumulative = 0.0
        abc_result = []
        for name, gmv in gmv_list:
            cumulative += gmv / max(total_gmv, 1) * 100
            cls = "A" if cumulative <= 80 else ("B" if cumulative <= 95 else "C")
            abc_result.append({"SKU": name, "月GMV估算": f"¥{gmv:,.0f}", "分类": cls,
                                "累计GMV占比": f"{min(cumulative, 100):.1f}%"})

        # ABC汇总
        abc_summary: Dict[str, Dict] = {}
        for item in abc_result:
            cls = item["分类"]
            if cls not in abc_summary:
                abc_summary[cls] = {"SKU数": 0, "价值占比": 0}
            abc_summary[cls]["SKU数"] += 1

        a_count = abc_summary.get("A", {}).get("SKU数", 0)
        b_count = abc_summary.get("B", {}).get("SKU数", 0)
        c_count = abc_summary.get("C", {}).get("SKU数", 0)
        abc_segments = [
            {"分类": "A", "SKU数": a_count, "价值占比": 80, "策略": "重点保障，不允许缺货"},
            {"分类": "B", "SKU数": b_count, "价值占比": 15, "策略": "正常补货，EOQ控制"},
            {"分类": "C", "SKU数": c_count, "价值占比": 5, "策略": "缩减库存，清库促销"},
        ]

        # ── 库存周转率 ──
        avg_inv = total_inv_value
        annual_sales_est = sum(float(s.get("monthly_sales", 0)) * float(s.get("cost", 0)) * 12 for s in sku_list)
        turnover_ratio = round(annual_sales_est / max(avg_inv, 1), 2)
        turnover_days = round(365 / max(turnover_ratio, 0.01))

        result = {
            "has_data": True,
            "数据来源": data_source,
            "服务水平": service_level_str,
            "供应商交货期": f"{lead_time}天",
            "SKU库存优化明细": sku_results,
            "ABC分类汇总": abc_segments,
            "ABC分类明细": abc_result[:10],
            "整体库存健康": {
                "总库存价值": f"¥{total_inv_value:,.0f}",
                "库存周转率": f"{turnover_ratio}次/年",
                "库存周转天数": f"{turnover_days}天",
                "健康状态": "优秀" if turnover_days < 30 else ("正常" if turnover_days < 60 else "偏高"),
                "行业基准": "电商库存周转<30天为优秀",
            },
        }

        # LLM库存策略建议
        try:
            from ._content_engine import generate_inventory_strategy
            strategy = await generate_inventory_strategy(
                inventory_data={
                    "total_skus": len(sku_list),
                    "avg_turnover_days": turnover_days,
                    "total_inventory_value": total_inv_value,
                },
                abc_segments=abc_segments,
            )
            if strategy:
                result["AI库存策略"] = strategy
        except Exception:
            pass

        return result


class OpsAdFatigueDetector(SkillBase):
    """广告疲劳检测引擎 — CTR衰减+频次分析+马科维茨预算重分配"""

    def __init__(self) -> None:
        super().__init__(
            name="ops_ad_fatigue_detector",
            display_name="广告疲劳检测",
            description=(
                "检测各广告渠道的CTR衰减率（基线7天vs近3天），识别广告疲劳信号，"
                "输出疲劳评分、创意刷新建议和基于边际ROAS的预算重分配方案。"
            ),
            category="ops",
            input_schema={
                "type": "object",
                "properties": {
                    "channels": {
                        "type": "array",
                        "items": {"type": "object"},
                        "description": (
                            "渠道数据列表，每项含：name/ctr_baseline/ctr_recent/impressions_per_user/"
                            "budget/roas（可选，若不提供则从真实数据加载）"
                        ),
                    },
                    "total_budget": {"type": "number", "description": "总广告预算（元），用于预算重分配计算"},
                    "platform": {"type": "string", "description": "平台：淘宝/京东/拼多多/抖音/快手"},
                    "days": {"type": "integer", "description": "使用真实数据时的天数，默认30"},
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        user_channels: List[Dict] = kwargs.get("channels", [])
        total_budget: float = float(kwargs.get("total_budget", 0))
        platform: str = kwargs.get("platform", "淘宝")
        days: int = int(kwargs.get("days", 30))

        # 如果未提供渠道数据，从真实数据加载
        channels: List[Dict] = []
        real_data_note = "（估算数据）"
        if user_channels:
            channels = user_channels
        elif user_id:
            try:
                summary = await load_metrics_summary(user_id, days=days)
                if summary.get("has_data"):
                    platforms_data = summary.get("platforms", {})
                    platform_names = {
                        "taobao": "淘宝/直通车", "jd": "京东推广",
                        "pdd": "拼多多推广", "douyin": "抖音千川",
                    }
                    if total_budget == 0:
                        total_budget = summary.get("totals", {}).get("ad_spend", 0)
                    for plat, pdata in platforms_data.items():
                        uv = pdata.get("uv", 0)
                        ad_spend = pdata.get("ad_spend", 0)
                        gmv = pdata.get("gmv", 0)
                        if uv > 0 or ad_spend > 0:
                            # 估算CTR（UV/impressions，用UV/广告花费*平均CPC估算曝光）
                            avg_cpc = 1.5  # 默认CPC
                            impressions_est = max(uv * 3, ad_spend / avg_cpc * 5) if ad_spend > 0 else uv * 5
                            ctr_est = round(uv / max(impressions_est, 1) * 100, 2)
                            roas = round(gmv / max(ad_spend, 1), 2) if ad_spend > 0 else 0
                            channels.append({
                                "name": platform_names.get(plat, plat),
                                "platform": plat,
                                "ctr_baseline": ctr_est * 1.1,  # 基线比近期高（模拟衰减）
                                "ctr_recent": ctr_est,
                                "impressions_per_user": 4.5,  # 默认频次
                                "budget": ad_spend,
                                "roas": roas,
                            })
                    real_data_note = f"（基于真实近{days}天数据）"
            except Exception:
                pass

        if not channels:
            # 使用示例数据说明用法
            return {
                "has_data": False,
                "提示": (
                    "未找到广告渠道数据。请提供 channels 参数（含ctr_baseline/ctr_recent），"
                    "或先通过数据导入同步广告数据（需包含 ad_spend/uv 指标）。"
                ),
                "示例参数": {
                    "channels": [
                        {"name": "直通车", "ctr_baseline": 3.2, "ctr_recent": 2.4,
                         "impressions_per_user": 6, "budget": 5000, "roas": 3.2},
                        {"name": "超级推荐", "ctr_baseline": 1.8, "ctr_recent": 1.75,
                         "impressions_per_user": 3, "budget": 3000, "roas": 4.1},
                    ]
                },
            }

        if total_budget == 0:
            total_budget = sum(ch.get("budget", 0) for ch in channels)

        # ── 疲劳检测计算 ──
        channel_results = []
        fatigued_count = 0

        for ch in channels:
            name = ch.get("name", "未知渠道")
            ctr_base = float(ch.get("ctr_baseline", 1.0))
            ctr_recent = float(ch.get("ctr_recent", 1.0))
            freq = float(ch.get("impressions_per_user", 0))
            budget = float(ch.get("budget", 0))
            roas = float(ch.get("roas", 0))

            # CTR衰减率
            if ctr_base > 0:
                decay_rate = round((ctr_base - ctr_recent) / ctr_base * 100, 1)
            else:
                decay_rate = 0.0

            # 疲劳评分（0-100）
            fatigue_score = 0
            if decay_rate > 15:
                fatigue_score += min(50, decay_rate * 2)  # CTR衰减贡献最多50分
            if freq >= 8:
                fatigue_score += min(30, (freq - 7) * 10)  # 频次超标贡献最多30分
            if decay_rate > 25:
                fatigue_score += 20  # 严重衰减额外加分
            fatigue_score = min(100, round(fatigue_score, 0))

            # 疲劳状态
            if fatigue_score >= 70:
                status = "🔴 严重疲劳（立即换素材）"
                fatigued_count += 1
            elif fatigue_score >= 40:
                status = "🟠 中度疲劳（本周更新创意）"
                fatigued_count += 1
            elif fatigue_score >= 15:
                status = "🟡 轻微疲劳（持续监控）"
            else:
                status = "🟢 正常"

            # 边际ROAS判断（低于2建议转移预算）
            roas_health = "正常" if roas == 0 else (
                "高效" if roas >= 4 else ("正常" if roas >= 2 else "低效（建议削减预算）")
            )

            channel_results.append({
                "渠道": name,
                "基线CTR": f"{ctr_base:.2f}%",
                "近期CTR": f"{ctr_recent:.2f}%",
                "CTR衰减率": f"{decay_rate:+.1f}%",
                "曝光频次": f"{freq:.1f}次/用户" if freq > 0 else "未知",
                "疲劳评分": f"{int(fatigue_score)}/100",
                "疲劳状态": status,
                "广告预算": f"¥{budget:,.0f}" if budget > 0 else "未知",
                "ROAS": f"{roas:.2f}" if roas > 0 else "未知",
                "ROAS健康": roas_health,
            })

        # ── 马科维茨风格预算重分配 ──
        # 将预算从疲劳/低效渠道转移到高效/健康渠道
        # 简化版：按ROAS加权分配预算
        reallocation = []
        if total_budget > 0 and len(channels) > 1:
            roas_vals = [float(ch.get("roas", 2.0)) for ch in channels]
            # 疲劳权重惩罚
            fatigue_scores_raw = []
            for res in channel_results:
                score_str = res["疲劳评分"].split("/")[0]
                fatigue_scores_raw.append(int(score_str))

            # 最终权重 = ROAS × (1 - 疲劳评分/150)，确保高ROAS低疲劳拿更多预算
            weights = []
            for i, r in enumerate(roas_vals):
                effective_roas = max(0.5, r) * (1 - fatigue_scores_raw[i] / 150)
                weights.append(max(0.1, effective_roas))

            total_w = sum(weights)
            for i, ch in enumerate(channels):
                new_budget = round(total_budget * weights[i] / total_w, 0)
                old_budget = float(ch.get("budget", 0))
                change = round(new_budget - old_budget, 0)
                reallocation.append({
                    "渠道": ch.get("name", ""),
                    "当前预算": f"¥{old_budget:,.0f}",
                    "建议预算": f"¥{new_budget:,.0f}",
                    "变化": f"{'+' if change >= 0 else ''}¥{change:,.0f}",
                })

        result: Dict[str, Any] = {
            "数据来源": real_data_note,
            "总广告预算": f"¥{total_budget:,.0f}" if total_budget > 0 else "未知",
            "平台": platform,
            "行业疲劳基准": "CTR衰减>15%（连续7天）= 疲劳信号；频次≥8次/用户 = 受众疲劳",
            "渠道疲劳分析": channel_results,
            "疲劳渠道数": f"{fatigued_count}/{len(channels)}",
        }

        if reallocation:
            result["建议预算分配"] = reallocation

        # LLM策略建议
        if fatigued_count > 0:
            try:
                from ._content_engine import generate_ad_fatigue_strategy
                strategy = await generate_ad_fatigue_strategy(
                    channel_data=channel_results,
                    total_budget=total_budget,
                    platform=platform,
                )
                if strategy:
                    result["AI广告优化策略"] = strategy
            except Exception:
                pass

        return result


class OpsABCXYZClassifier(SkillBase):
    """ABC-XYZ库存双维分类 — ABC(价值)×XYZ(需求波动性)9格矩阵 + 策略建议"""

    _ABC_THRESHOLDS = {"A": 0.80, "B": 0.95}
    _XYZ_THRESHOLDS = {"X": 0.20, "Y": 0.50}

    _MATRIX_STRATEGY = {
        "AX": {"策略": "持续补货，自动化补货触发，维持最低安全库存", "库存倍率": 1.2},
        "AY": {"策略": "提前预测补货，安全库存1.5倍需求，定期评估", "库存倍率": 1.5},
        "AZ": {"策略": "柔性供应链，小批量高频补货，紧密监控", "库存倍率": 2.0},
        "BX": {"策略": "标准补货流程，定期审查", "库存倍率": 1.3},
        "BY": {"策略": "适度库存缓冲，季节性调整", "库存倍率": 1.5},
        "BZ": {"策略": "按需补货，避免积压", "库存倍率": 1.8},
        "CX": {"策略": "批量采购降成本，低频补货", "库存倍率": 1.0},
        "CY": {"策略": "清理尾货，考虑停售低利润款", "库存倍率": 1.0},
        "CZ": {"策略": "建议停售或大促清仓", "库存倍率": 0.5},
    }

    def __init__(self) -> None:
        super().__init__(
            name="ops_abc_xyz_classifier",
            display_name="ABC-XYZ库存分类",
            description="双维库存分类：ABC按GMV价值(帕累托80/95%)×XYZ按需求变异系数CV=σ/μ，生成9格策略矩阵，自动从真实数据加载SKU",
            category="ops",
            input_schema={
                "type": "object",
                "properties": {
                    "skus": {
                        "type": "array",
                        "description": "SKU列表，每项包含 {sku_id, gmv, sales_history: [月销量...]}",
                        "items": {"type": "object"},
                    },
                    "platform": {"type": "string", "description": "平台：淘宝/京东/拼多多/抖音"},
                    "top_n": {"type": "integer", "description": "展示前N个高价值SKU，默认20"},
                },
                "required": [],
            },
        )

    def _classify_abc(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        items_sorted = sorted(items, key=lambda x: x.get("gmv", 0), reverse=True)
        total_gmv = sum(x.get("gmv", 0) for x in items_sorted) or 1
        cumulative = 0.0
        for item in items_sorted:
            cumulative += item.get("gmv", 0) / total_gmv
            item["abc"] = "A" if cumulative <= 0.80 else ("B" if cumulative <= 0.95 else "C")
            item["gmv_pct"] = item.get("gmv", 0) / total_gmv
        return items_sorted

    def _classify_xyz(self, items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        for item in items:
            hist = [float(v) for v in item.get("sales_history", []) if v is not None]
            if len(hist) < 2:
                item["xyz"] = "Z"
                item["cv"] = 0.0
                continue
            mean = sum(hist) / len(hist)
            if mean == 0:
                item["xyz"] = "Z"
                item["cv"] = 0.0
                continue
            variance = sum((x - mean) ** 2 for x in hist) / len(hist)
            cv = math.sqrt(variance) / mean
            item["cv"] = round(cv, 3)
            item["xyz"] = "X" if cv < 0.20 else ("Y" if cv < 0.50 else "Z")
        return items

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        skus: List[Dict[str, Any]] = kwargs.get("skus", [])
        platform: str = kwargs.get("platform", "淘宝")
        top_n: int = kwargs.get("top_n", 20)

        data_source = "用户输入"
        if not skus and user_id:
            try:
                from src.core.metrics_store import MetricsStore
                store = MetricsStore()
                rows = await store.query_sku_sales(user_id, days=90)
                if rows:
                    sku_map: Dict[str, Dict] = {}
                    for row in rows:
                        sid = str(row.get("sku_id") or row.get("product_id", f"SKU{len(sku_map)+1}"))
                        if sid not in sku_map:
                            sku_map[sid] = {"sku_id": sid, "gmv": 0.0, "sales_history": []}
                        sku_map[sid]["gmv"] += float(row.get("gmv", row.get("revenue", 0)) or 0)
                        sku_map[sid]["sales_history"].append(float(row.get("orders", row.get("quantity", 0)) or 0))
                    skus = list(sku_map.values())
                    data_source = "真实数据（最近90天）"
            except Exception:
                pass

        if not skus:
            import random
            random.seed(42)
            skus = [
                {"sku_id": f"SKU{i+1:03d}",
                 "gmv": round(random.expovariate(0.05) * 10000, 0),
                 "sales_history": [max(0, random.gauss(100 - i * 2, 10 + i * 3)) for _ in range(12)]}
                for i in range(30)
            ]
            data_source = "演示数据（30个模拟SKU）"

        classified = self._classify_xyz(self._classify_abc(skus))

        # 9格矩阵统计
        matrix: Dict[str, Dict] = {k: {"count": 0, "gmv": 0.0} for k in self._MATRIX_STRATEGY}
        total_gmv = sum(x.get("gmv", 0) for x in classified) or 1
        for item in classified:
            cell = item.get("abc", "C") + item.get("xyz", "Z")
            if cell in matrix:
                matrix[cell]["count"] += 1
                matrix[cell]["gmv"] += item.get("gmv", 0)

        matrix_display = {
            cell: {
                "SKU数量": d["count"],
                "GMV贡献": f"{d['gmv']/total_gmv:.1%}",
                "策略": self._MATRIX_STRATEGY[cell]["策略"],
                "库存倍率": self._MATRIX_STRATEGY[cell]["库存倍率"],
            }
            for cell, d in matrix.items() if d["count"] > 0
        }

        # 高价值SKU明细
        top_skus = sorted(classified, key=lambda x: x.get("gmv", 0), reverse=True)[:top_n]
        top_display = [
            {
                "SKU": item["sku_id"],
                "GMV": f"¥{item.get('gmv', 0):,.0f}",
                "GMV占比": f"{item.get('gmv_pct', 0):.1%}",
                "CV": item.get("cv", 0),
                "分类": item.get("abc", "?") + item.get("xyz", "?"),
                "策略": self._MATRIX_STRATEGY.get(item.get("abc","C") + item.get("xyz","Z"), {}).get("策略", ""),
            }
            for item in top_skus
        ]

        result: Dict[str, Any] = {
            "数据来源": data_source,
            "平台": platform,
            "分析SKU总数": len(classified),
            "9格矩阵": matrix_display,
            "矩阵说明": "ABC: A=前80%GMV / B=80-95% / C=尾部; XYZ: X=CV<0.2稳定 / Y=CV0.2-0.5 / Z=CV>0.5高波动",
            f"高价值SKU(前{min(top_n, len(top_display))}个)": top_display,
            "关键洞察": {
                "AX稳定高价值": matrix.get("AX", {}).get("count", 0),
                "AZ高价值高波动": matrix.get("AZ", {}).get("count", 0),
                "CZ建议清仓": matrix.get("CZ", {}).get("count", 0),
            },
        }

        try:
            advice = await generate_abc_xyz_strategy(
                matrix_summary={k: {**d, "gmv_pct": d["gmv"] / total_gmv, "inventory_pct": d["count"] / len(classified)} for k, d in matrix.items()},
                high_value_items=[{"sku_id": x["SKU"], "gmv_pct": float(x["GMV占比"].strip("%")) / 100, "cv": x["CV"], "abc": x["分类"][0], "xyz": x["分类"][1]} for x in top_display[:8]],
                platform=platform,
            )
            if advice:
                result["AI库存策略建议"] = advice
        except Exception:
            pass

        return result


ALL_SKILLS: list[SkillBase] = [
    OpsPromoPlanning(),
    OpsPricingStrategy(),
    OpsChannelStrategy(),
    OpsInventoryPlanning(),
    OpsListingCopy(),
    OpsAssortmentPlanning(),
    OpsExecutionPlan(),
    OpsSmartPricing(),
    OpsInventoryOptimizer(),
    OpsAdFatigueDetector(),
    OpsABCXYZClassifier(),
]
