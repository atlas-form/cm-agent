from __future__ import annotations

from typing import Any, Dict, List

from .base import SkillBase
from ._db_helpers import load_product_info
from ._content_engine import generate_design_brief


class DesignMainImage(SkillBase):
    """主图设计方案技能 — LLM生成完整可执行的主图设计方案"""

    def __init__(self) -> None:
        super().__init__(
            name="design_main_image",
            display_name="主图设计方案",
            description="AI生成完整主图设计执行方案（色值/构图/文案/素材清单），传入product_id自动加载商品信息",
            category="design",
            input_schema={
                "type": "object",
                "properties": {
                    "product_name": {"type": "string", "description": "商品名称（可从product_id自动加载）"},
                    "platform": {"type": "string", "description": "平台：淘宝/京东/拼多多/抖音"},
                    "style": {"type": "string", "description": "视觉风格：简约现代/高端奢华/年轻活力/国潮复古/科技感"},
                    "product_id": {"type": "integer", "description": "商品ID，自动加载商品信息和卖点"},
                    "extra_requirements": {"type": "string", "description": "额外要求（如指定背景色/必须有价格/A/B测试）"},
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        name: str = kwargs.get("product_name", "")
        platform: str = kwargs.get("platform", "淘宝")
        style: str = kwargs.get("style", "简约现代")
        product_id: int = kwargs.get("product_id", 0)
        extra: str = kwargs.get("extra_requirements", "")

        pinfo: Dict[str, Any] = {}

        if user_id and product_id:
            loaded = await load_product_info(user_id, product_id)
            if loaded:
                pinfo = loaded
                if not name:
                    name = pinfo.get("name", "")
        elif name:
            pinfo = {"name": name}

        if not name and not pinfo.get("name"):
            return {"error": "请提供 product_name 或 product_id"}

        # 搜索当前爆款主图设计趋势
        design_trend_ctx = ""
        if kwargs.get("_needs_fresh_data", True):
            try:
                from .search import _web_search, _format_results_for_llm
                q = f"{platform} {name or '电商'} 主图设计 爆款 高点击率 视觉趋势 2026"
                results, _ = await _web_search(q, topic="general", max_results=4, days=60)
                if results:
                    design_trend_ctx = _format_results_for_llm(results, max_per_item=250)
            except Exception:
                pass

        return await generate_design_brief(
            design_type="主图",
            product_info=pinfo,
            platform=platform,
            style=style,
            extra_requirements=extra,
            search_context=design_trend_ctx,
        )


class DesignDetailPage(SkillBase):
    """详情页设计方案技能 — LLM生成完整详情页内容策划和设计执行方案"""

    def __init__(self) -> None:
        super().__init__(
            name="design_detail_page",
            display_name="详情页设计方案",
            description="AI生成完整详情页设计执行方案（模块文案/构图/色彩/素材清单），传入product_id自动加载商品信息",
            category="design",
            input_schema={
                "type": "object",
                "properties": {
                    "product_name": {"type": "string", "description": "商品名称（可从product_id自动加载）"},
                    "platform": {"type": "string", "description": "平台：淘宝/京东/拼多多/抖音"},
                    "style": {"type": "string", "description": "风格：简约现代/高端奢华/年轻活力/国潮"},
                    "product_id": {"type": "integer", "description": "商品ID，自动加载商品信息"},
                    "extra_requirements": {"type": "string", "description": "额外要求"},
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        name: str = kwargs.get("product_name", "")
        platform: str = kwargs.get("platform", "淘宝")
        style: str = kwargs.get("style", "简约现代")
        product_id: int = kwargs.get("product_id", 0)
        extra: str = kwargs.get("extra_requirements", "")

        pinfo: Dict[str, Any] = {}

        if user_id and product_id:
            loaded = await load_product_info(user_id, product_id)
            if loaded:
                pinfo = loaded
                if not name:
                    name = pinfo.get("name", "")
        elif name:
            pinfo = {"name": name}

        if not name and not pinfo.get("name"):
            return {"error": "请提供 product_name 或 product_id"}

        return await generate_design_brief(
            design_type="详情页",
            product_info=pinfo,
            platform=platform,
            style=style,
            extra_requirements=extra,
        )


class DesignColorScheme(SkillBase):
    """配色方案技能 — LLM生成含HEX色值的专业品牌配色体系"""

    def __init__(self) -> None:
        super().__init__(
            name="design_color_scheme",
            display_name="配色方案",
            description="AI生成完整品牌配色方案，含具体HEX色值/使用比例/心理分析/Figma变量命名规范",
            category="design",
            input_schema={
                "type": "object",
                "properties": {
                    "brand_tone": {
                        "type": "string",
                        "description": "品牌调性：高端奢华/年轻活力/清新自然/科技感/国潮复古/可爱萌系",
                    },
                    "category": {"type": "string", "description": "商品类目（如美妆/食品/数码）"},
                    "product_id": {"type": "integer", "description": "商品ID，自动加载类目信息"},
                    "platform": {"type": "string", "description": "目标平台"},
                    "extra_requirements": {"type": "string", "description": "特殊要求（如指定主色/品牌色）"},
                },
                "required": ["brand_tone"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        tone: str = kwargs.get("brand_tone", "年轻活力")
        category: str = kwargs.get("category", "")
        product_id: int = kwargs.get("product_id", 0)
        platform: str = kwargs.get("platform", "淘宝")
        extra: str = kwargs.get("extra_requirements", "")

        pinfo: Dict[str, Any] = {"name": tone}

        if user_id and product_id:
            loaded = await load_product_info(user_id, product_id)
            if loaded:
                pinfo = loaded
                if not category:
                    category = pinfo.get("category", "")

        if category:
            pinfo["category"] = category

        return await generate_design_brief(
            design_type="色彩方案",
            product_info=pinfo,
            platform=platform,
            style=tone,
            extra_requirements=extra or f"品牌调性：{tone}，类目：{category or '通用'}",
        )


class DesignMaterialSpec(SkillBase):
    """素材规范技能 — LLM生成完整多平台素材规格清单"""

    def __init__(self) -> None:
        super().__init__(
            name="design_material_spec",
            display_name="素材规范",
            description="AI生成各平台完整素材技术规格清单，含尺寸/格式/文件大小/命名规范/验收标准",
            category="design",
            input_schema={
                "type": "object",
                "properties": {
                    "usage": {
                        "type": "string",
                        "description": "用途：主图套组/详情页/直通车/信息流广告/社交媒体/直播封面",
                    },
                    "platforms": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "目标平台列表（如 [淘宝, 京东, 抖音]）",
                    },
                    "product_id": {"type": "integer", "description": "商品ID（提供更精准的素材建议）"},
                },
                "required": ["usage"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        usage: str = kwargs.get("usage", "主图套组")
        platforms: List[str] = kwargs.get("platforms", ["淘宝", "京东", "抖音"])
        product_id: int = kwargs.get("product_id", 0)

        pinfo: Dict[str, Any] = {}
        if user_id and product_id:
            loaded = await load_product_info(user_id, product_id)
            if loaded:
                pinfo = loaded

        platforms_str = "、".join(platforms)

        return await generate_design_brief(
            design_type="素材规格",
            product_info=pinfo,
            platform=platforms_str,
            style="规范文档",
            extra_requirements=f"用途：{usage}，目标平台：{platforms_str}，请提供完整的技术规格清单",
        )


class DesignCampaignPoster(SkillBase):
    """活动海报设计技能 — LLM生成完整可执行的海报设计方案"""

    def __init__(self) -> None:
        super().__init__(
            name="design_campaign_poster",
            display_name="活动海报设计",
            description="AI生成完整活动海报设计方案，含色值/文案/构图/各尺寸版本规格，传入product_id自动加载商品信息",
            category="design",
            input_schema={
                "type": "object",
                "properties": {
                    "campaign_name": {"type": "string", "description": "活动名称（如 618大促/新品首发/年货节）"},
                    "discount_info": {"type": "string", "description": "优惠信息（如 全场5折/满300减50）"},
                    "campaign_date": {"type": "string", "description": "活动日期"},
                    "style": {"type": "string", "description": "风格：热闹促销/简约高端/国潮节庆/科技感"},
                    "platform": {"type": "string", "description": "主要展示平台"},
                    "product_id": {"type": "integer", "description": "商品ID，关联主推商品信息"},
                },
                "required": ["campaign_name"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        campaign_name: str = kwargs.get("campaign_name", "年度大促")
        discount: str = kwargs.get("discount_info", "全场5折起")
        date: str = kwargs.get("campaign_date", "")
        style: str = kwargs.get("style", "热闹促销")
        platform: str = kwargs.get("platform", "淘宝")
        product_id: int = kwargs.get("product_id", 0)

        pinfo: Dict[str, Any] = {}
        if user_id and product_id:
            loaded = await load_product_info(user_id, product_id)
            if loaded:
                pinfo = loaded

        extra = f"活动名称：{campaign_name}，优惠信息：{discount}"
        if date:
            extra += f"，活动时间：{date}"

        # 搜索同类活动海报设计趋势
        poster_trend_ctx = ""
        if kwargs.get("_needs_fresh_data", True):
            try:
                from .search import _web_search, _format_results_for_llm
                q = f"{campaign_name} {platform} 活动海报 设计趋势 爆款 2026"
                results, _ = await _web_search(q, topic="general", max_results=4, days=30)
                if results:
                    poster_trend_ctx = _format_results_for_llm(results, max_per_item=250)
            except Exception:
                pass

        return await generate_design_brief(
            design_type="活动海报",
            product_info=pinfo,
            platform=platform,
            style=style,
            extra_requirements=extra,
            search_context=poster_trend_ctx,
        )


ALL_SKILLS: list[SkillBase] = [
    DesignMainImage(),
    DesignDetailPage(),
    DesignColorScheme(),
    DesignMaterialSpec(),
    DesignCampaignPoster(),
]
