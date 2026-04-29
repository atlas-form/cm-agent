from __future__ import annotations

from typing import Any, Dict, List

from .base import SkillBase
from ._db_helpers import load_product_info, load_metrics_summary
from ._content_engine import (
    generate_seo_titles,
    generate_product_description,
    generate_seo_audit,
    generate_store_design_brief,
    generate_page_conversion_plan,
)


class WebSEOOptimize(SkillBase):
    """SEO优化方案技能 — LLM生成完整关键词策略+内容优化清单+提排路径"""

    def __init__(self) -> None:
        super().__init__(
            name="web_seo_optimize",
            display_name="SEO优化方案",
            description="AI生成完整SEO方案（关键词矩阵/标题优化/内容改写清单/技术SEO检查/提排路径），自动关联真实商品和平台数据",
            category="web",
            input_schema={
                "type": "object",
                "properties": {
                    "target_keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "目标关键词列表（可从product_id自动生成）",
                    },
                    "current_rank": {"type": "integer", "description": "当前排名（可选）"},
                    "product_id": {"type": "integer", "description": "商品ID，自动加载商品信息生成针对性SEO方案"},
                    "platform": {"type": "string", "description": "平台：淘宝/京东/拼多多/抖音"},
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        keywords: List[str] = kwargs.get("target_keywords", [])
        rank: int = kwargs.get("current_rank", 0)
        product_id: int = kwargs.get("product_id", 0)
        platform: str = kwargs.get("platform", "淘宝")

        pinfo: Dict[str, Any] = {}
        real_metrics: Dict[str, Any] = {}

        if user_id and product_id:
            loaded = await load_product_info(user_id, product_id)
            if loaded:
                pinfo = loaded
                if not keywords:
                    sp = loaded.get("selling_points", [])
                    keywords = [loaded.get("name", "")] + sp[:2]

        if user_id:
            summary = await load_metrics_summary(user_id, days=30)
            if summary.get("has_data"):
                real_metrics = summary

        if not keywords:
            keywords = kwargs.get("target_keywords", ["电商"])

        # 搜索当前 SEO 算法更新和关键词竞争情况
        search_ctx = ""
        if kwargs.get("_needs_fresh_data", True):
            try:
                from .search import _web_search, _format_results_for_llm
                kw_str = " ".join(keywords[:2]) if keywords else pinfo.get("name", "")
                q = f"{platform} SEO 搜索算法 {kw_str} 关键词竞争 流量 2026"
                results, _ = await _web_search(q, topic="general", max_results=4, days=30)
                if results:
                    search_ctx = _format_results_for_llm(results, max_per_item=280)
            except Exception:
                pass

        return await generate_seo_audit(
            product_info=pinfo,
            keywords=keywords,
            platform=platform,
            current_rank=rank,
            real_metrics=real_metrics if real_metrics else None,
            search_context=search_ctx,
        )


class WebTitleGenerator(SkillBase):
    """标题生成技能 — 基于真实商品数据生成标题"""

    def __init__(self) -> None:
        super().__init__(
            name="web_title_generator",
            display_name="标题生成",
            description="根据商品信息生成多种风格的SEO友好标题；传入product_id可自动加载真实商品",
            category="web",
            input_schema={
                "type": "object",
                "properties": {
                    "product_name": {"type": "string", "description": "商品名称（可自动从product_id加载）"},
                    "keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "核心关键词（可从商品卖点自动生成）",
                    },
                    "style": {
                        "type": "string",
                        "description": "风格：营销型/信息型/品牌型",
                    },
                    "product_id": {"type": "integer", "description": "商品ID，用于自动加载商品信息"},
                    "platform": {"type": "string", "description": "目标平台：淘宝/京东/拼多多/抖音"},
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        name: str = kwargs.get("product_name", "")
        product_id: int = kwargs.get("product_id", 0)
        platform: str = kwargs.get("platform", "淘宝")
        count: int = kwargs.get("count", 5)

        pinfo: Dict[str, Any] = {}

        # ── 自动加载真实商品数据 ──
        if user_id and product_id:
            loaded = await load_product_info(user_id, product_id)
            if loaded:
                pinfo = loaded
                if not name:
                    name = pinfo.get("name", "")
        elif name:
            keywords: List[str] = kwargs.get("keywords", [])
            pinfo = {"name": name, "selling_points": keywords}

        if not name and not pinfo.get("name"):
            return {
                "error": "请提供 product_name 或 product_id",
                "提示": "可传入商品ID自动加载商品信息",
            }

        # 调用内容引擎生成真实 SEO 标题
        result = await generate_seo_titles(
            product_info=pinfo,
            platform=platform,
            count=count,
        )
        return result


class WebPageConversion(SkillBase):
    """页面转化优化技能 — 结合真实转化率数据"""

    def __init__(self) -> None:
        super().__init__(
            name="web_page_conversion",
            display_name="页面转化优化",
            description="分析页面转化瓶颈，输出优化方案；可自动加载真实转化率",
            category="web",
            input_schema={
                "type": "object",
                "properties": {
                    "page_type": {
                        "type": "string",
                        "description": "页面类型：首页/商品详情/购物车/结算/列表页",
                    },
                    "current_conversion": {"type": "number", "description": "当前转化率(%)；不填则从店铺数据读取"},
                    "bounce_rate": {"type": "number", "description": "跳出率(%)"},
                    "avg_stay_time": {"type": "number", "description": "平均停留时间(秒)"},
                },
                "required": ["page_type"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        page_type: str = kwargs.get("page_type", "商品详情")
        bounce: float = kwargs.get("bounce_rate", 55.0)
        stay: int = kwargs.get("avg_stay_time", 45)
        product_id: int = kwargs.get("product_id", 0)

        # 自动加载真实转化率
        auto_conv: float = 0.0
        real_metrics: Dict[str, Any] = {}

        if user_id:
            summary = await load_metrics_summary(user_id, days=30)
            if summary.get("has_data"):
                real_metrics = summary
                conv_vals = [
                    p.get("conversion_rate", 0) * 100
                    for p in summary.get("platforms", {}).values()
                    if p.get("conversion_rate") is not None
                ]
                if conv_vals:
                    auto_conv = round(sum(conv_vals) / len(conv_vals), 2)

        conv = kwargs.get("current_conversion") if kwargs.get("current_conversion") is not None else (auto_conv or 3.0)

        benchmarks = {
            "首页": 5.0, "商品详情": 4.0, "购物车": 65.0, "结算": 80.0, "列表页": 8.0,
        }
        benchmark = benchmarks.get(page_type, 5.0)

        pinfo: Dict[str, Any] = {}
        if user_id and product_id:
            loaded = await load_product_info(user_id, product_id)
            if loaded:
                pinfo = loaded

        # 搜索行业转化率基准和优化案例
        search_ctx = ""
        if kwargs.get("_needs_fresh_data", True):
            try:
                from .search import _web_search, _format_results_for_llm
                q = f"电商 {page_type} 转化率 行业均值 优化方法 2026"
                results, _ = await _web_search(q, topic="general", max_results=4, days=60)
                if results:
                    search_ctx = _format_results_for_llm(results, max_per_item=250)
            except Exception:
                pass

        result = await generate_page_conversion_plan(
            page_type=page_type,
            current_conv=conv,
            benchmark=benchmark,
            real_metrics=real_metrics if real_metrics else None,
            product_info=pinfo if pinfo else None,
            search_context=search_ctx,
        )

        # 附加原始数据供参考
        result["当前数据"] = {
            "转化率": f"{conv}%",
            "跳出率": f"{bounce}%",
            "停留时间": f"{stay}秒",
        }
        result["行业基准转化率"] = f"{benchmark}%"
        result["数据来源"] = "真实数据（近30天）" if auto_conv else "用户提供/默认值"
        return result


class WebStoreDesign(SkillBase):
    """店铺装修方案技能 — LLM生成完整可执行的装修设计方案"""

    def __init__(self) -> None:
        super().__init__(
            name="web_store_design",
            display_name="店铺装修方案",
            description="AI生成完整店铺装修设计方案（视觉定位/模块结构/尺寸规格/文案方向/素材清单/验收标准），基于店铺真实数据个性化定制",
            category="web",
            input_schema={
                "type": "object",
                "properties": {
                    "store_type": {
                        "type": "string",
                        "description": "店铺类型：旗舰店/专营店/个人店",
                    },
                    "category": {"type": "string", "description": "主营类目"},
                    "platform": {"type": "string", "description": "平台"},
                    "brand_tone": {"type": "string", "description": "品牌调性"},
                },
                "required": ["store_type"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        store_type: str = kwargs.get("store_type", "旗舰店")
        category: str = kwargs.get("category", "通用")
        platform: str = kwargs.get("platform", "淘宝")
        tone: str = kwargs.get("brand_tone", "专业")

        real_metrics: Dict[str, Any] = {}
        if user_id:
            summary = await load_metrics_summary(user_id, days=30)
            if summary.get("has_data"):
                real_metrics = summary

        return await generate_store_design_brief(
            store_type=store_type,
            category=category,
            platform=platform,
            brand_tone=tone,
            real_metrics=real_metrics if real_metrics else None,
        )


class WebKeywordResearch(SkillBase):
    """关键词研究技能 — LLM智能拓词+搜索意图分析+出价策略"""

    def __init__(self) -> None:
        super().__init__(
            name="web_keyword_research",
            display_name="关键词研究",
            description="AI拓展关键词矩阵（核心词/长尾词/蓝海词/场景词），分析搜索意图、竞争度和出价策略，可自动关联真实商品",
            category="web",
            input_schema={
                "type": "object",
                "properties": {
                    "seed_keyword": {"type": "string", "description": "种子关键词（可从product_id自动提取）"},
                    "category": {"type": "string", "description": "商品类目"},
                    "platform": {"type": "string", "description": "平台：淘宝/京东/拼多多/抖音"},
                    "product_id": {"type": "integer", "description": "商品ID，自动以商品名为种子词"},
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        seed: str = kwargs.get("seed_keyword", "")
        category: str = kwargs.get("category", "通用")
        platform: str = kwargs.get("platform", "淘宝")
        product_id: int = kwargs.get("product_id", 0)

        pinfo: Dict[str, Any] = {}
        data_source = "用户提供"

        if user_id and product_id and not seed:
            loaded = await load_product_info(user_id, product_id)
            if loaded:
                pinfo = loaded
                seed = loaded.get("name", seed)
                category = category or loaded.get("category", "通用")
                data_source = f"真实商品（ID={product_id}）"

        if not seed:
            return {"error": "请提供 seed_keyword 或 product_id"}

        platform_algo = {
            "淘宝": "淘宝搜索：搜索词→人群匹配→质量分排序；长尾词竞争低但精准度高",
            "京东": "京东搜索：品牌词和型号词占比高，标准化描述词流量大",
            "拼多多": "拼多多：价格词和爆款词流量最大，用户决策快",
            "抖音": "抖音：场景词和问题词CTR最高，内容词比商品词转化好",
        }.get(platform, "电商平台搜索规律")

        prod_ctx = ""
        if pinfo:
            sp = " | ".join(pinfo.get("selling_points", [])[:3])
            prod_ctx = f"\n【商品信息】{pinfo.get('name',seed)} - {pinfo.get('category',category)} - ¥{pinfo.get('selling_price',0)}\n卖点：{sp}"

        # 实时搜索关键词竞争数据（仅当需要实时数据时）
        search_ctx = ""
        if kwargs.get("_needs_fresh_data", True):
            try:
                from .search import _web_search, _format_results_for_llm
                kw_query = f"{seed} {platform} 关键词 搜索热度 竞争 2026"
                kw_results, _eng = await _web_search(kw_query, topic="general", max_results=4, days=30)
                if kw_results:
                    search_ctx = f"\n\n【实时关键词竞争参考】\n{_format_results_for_llm(kw_results, max_per_item=250)}\n（请结合上述真实数据优化关键词建议）"
            except Exception:
                pass

        from ._content_engine import _SYSTEM_SEO_EXPERT, _call
        prompt = f"""请为以下种子词生成完整的{platform}关键词研究报告。

【种子词】{seed}
【商品类目】{category}
【平台算法】{platform_algo}
{prod_ctx}{search_ctx}

请输出完整关键词矩阵：

━━━ 核心词（1-3个，高流量主战场）━━━
（格式：关键词 | 预估月搜索量 | 竞争度 | 建议出价范围 | 布局位置）

━━━ 属性词/精准词（5-8个，中高转化）━━━
（包含颜色/材质/规格/功能等属性词）

━━━ 场景词（3-5个，精准人群）━━━
（使用场景/人群场景词）

━━━ 长尾词/问答词（5-8个，低竞争机会）━━━
（格式：关键词 | 搜索意图分析 | 内容营销方向）

━━━ 蓝海词（2-3个，竞争低机会大）━━━
（说明为何是蓝海，如何抢占）

━━━ 否定词建议 ━━━
（应排除的无关词，避免浪费预算）

━━━ 关键词投放策略 ━━━
（不同类型关键词的出价逻辑和投放时机建议）"""

        content = await _call(_SYSTEM_SEO_EXPERT, prompt, max_tokens=1800, temperature=0.65)

        if not content:
            return {"error": "关键词研究暂时不可用", "种子词": seed}

        return {
            "种子词": seed,
            "数据来源": data_source,
            "平台": platform,
            "商品类目": category,
            "完整关键词报告": content,
            "使用说明": "核心词用于标题优化，精准词用于详情页布局，长尾词用于内容营销",
        }


class WebProductDescription(SkillBase):
    """详情页文案生成技能 — 生成完整可用的详情页描述文案"""

    def __init__(self) -> None:
        super().__init__(
            name="web_product_description",
            display_name="详情页文案",
            description="生成完整的详情页营销文案（首屏卖点/痛点/解决方案/亮点/行动号召）；传入product_id自动加载商品信息",
            category="web",
            input_schema={
                "type": "object",
                "properties": {
                    "product_name": {"type": "string", "description": "商品名称（可从product_id自动加载）"},
                    "product_id": {"type": "integer", "description": "商品ID，自动加载商品信息"},
                    "platform": {"type": "string", "description": "目标平台：淘宝/京东/拼多多/抖音"},
                    "style": {
                        "type": "string",
                        "description": "文案风格：营销型/信息型/故事型",
                    },
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        name: str = kwargs.get("product_name", "")
        product_id: int = kwargs.get("product_id", 0)
        platform: str = kwargs.get("platform", "淘宝")
        style: str = kwargs.get("style", "营销型")

        pinfo: Dict[str, Any] = {}

        # ── 自动加载真实商品数据 ──
        if user_id and product_id:
            loaded = await load_product_info(user_id, product_id)
            if loaded:
                pinfo = loaded
                if not name:
                    name = pinfo.get("name", "")
        elif name:
            pinfo = {"name": name}

        if not name and not pinfo.get("name"):
            return {
                "error": "请提供 product_name 或 product_id",
                "提示": "可传入商品ID自动加载商品信息",
            }

        # 调用内容引擎生成完整详情页文案
        result = await generate_product_description(
            product_info=pinfo,
            platform=platform,
            style=style,
        )
        return result


class WebTitleSEOScorer(SkillBase):
    """标题SEO评分技能 — TF-IDF关键词密度 + 位置权重 + 字数评分 + 综合SEO质量分"""

    # 关键词在标题中的位置权重（越靠前权重越高）
    _POSITION_ZONES = [
        (0.0, 0.20, 30),   # 前20%: 最黄金位置, +30分
        (0.20, 0.40, 20),  # 前20-40%, +20分
        (0.40, 0.60, 10),  # 中部, +10分
        (0.60, 0.80, 5),   # 后部, +5分
        (0.80, 1.0, 0),    # 尾部, +0分
    ]

    # 电商标题常见冗余词（不计入关键词密度计算）
    _STOP_WORDS = {
        "的", "了", "在", "是", "和", "与", "或", "有", "个", "件",
        "款", "型", "新款", "正品", "直邮", "包邮", "特价", "促销",
        "旗舰", "官方", "旗舰店", "官方旗舰", "专卖", "热销",
    }

    def __init__(self) -> None:
        super().__init__(
            name="web_title_seo_scorer",
            display_name="标题SEO评分",
            description="对电商商品标题进行SEO质量评分（0-100分）：关键词密度TF-IDF + 主关键词位置权重 + 字符长度 + 可读性；输出详细扣分项和优化建议",
            category="web",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "待评分的商品标题"},
                    "target_keywords": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "目标关键词列表（主关键词放第一位）",
                    },
                    "platform": {"type": "string", "description": "平台：淘宝/京东/拼多多/抖音"},
                    "category": {"type": "string", "description": "商品类目（可选，影响字数评分标准）"},
                    "product_id": {"type": "integer", "description": "商品ID，自动加载标题和关键词"},
                },
                "required": [],
            },
        )

    def _tokenize(self, text: str) -> List[str]:
        """简单中文分词：按字符滑窗+按空格切割。"""
        # 先按空格、/切分
        import re
        parts = re.split(r"[\s/\\|&+，,。、]+", text)
        tokens = [p.strip() for p in parts if p.strip() and p.strip() not in self._STOP_WORDS]
        return tokens

    def _keyword_density(self, title: str, keyword: str) -> float:
        """计算关键词在标题中的字符密度（占比）。"""
        if not keyword or not title:
            return 0.0
        count = title.lower().count(keyword.lower())
        return count * len(keyword) / max(len(title), 1)

    def _primary_keyword_position_score(self, title: str, keyword: str) -> int:
        """主关键词出现在标题哪个区间，返回对应位置分。"""
        if not keyword or not title:
            return 0
        idx = title.lower().find(keyword.lower())
        if idx < 0:
            return -20  # 关键词不存在扣分
        relative_pos = idx / max(len(title), 1)
        for lo, hi, score in self._POSITION_ZONES:
            if lo <= relative_pos < hi:
                return score
        return 0

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        title: str = kwargs.get("title", "")
        keywords: List[str] = kwargs.get("target_keywords", [])
        platform: str = kwargs.get("platform", "淘宝")
        category: str = kwargs.get("category", "")
        product_id: int = kwargs.get("product_id", 0)

        # 自动从商品加载标题和关键词
        if user_id and product_id and not title:
            pinfo = await load_product_info(user_id, product_id)
            if pinfo:
                title = pinfo.get("name", "")
                if not keywords and pinfo.get("keywords"):
                    keywords = pinfo["keywords"][:5] if isinstance(pinfo["keywords"], list) else []

        if not title:
            return {"error": "请提供 title 或 product_id"}

        title_len = len(title)
        scores: Dict[str, Any] = {}
        deductions: List[str] = []

        # ── 1. 字符长度评分（满分25分）──
        # 淘宝/京东最优30字，拼多多20-25字，抖音15-20字
        optimal_ranges = {
            "淘宝": (28, 34), "京东": (28, 36),
            "拼多多": (20, 28), "抖音": (15, 22),
        }
        lo, hi = optimal_ranges.get(platform, (24, 34))

        if lo <= title_len <= hi:
            len_score = 25
        elif title_len < lo:
            len_score = max(0, 25 - (lo - title_len) * 2)
            deductions.append(f"标题偏短({title_len}字 < 推荐{lo}字)，损失{25 - len_score}分")
        else:
            len_score = max(5, 25 - (title_len - hi) * 1)
            deductions.append(f"标题偏长({title_len}字 > 推荐{hi}字)，损失{25 - len_score}分")
        scores["字符长度"] = {"得分": len_score, "满分": 25, "实际字数": title_len, "推荐范围": f"{lo}-{hi}字"}

        # ── 2. 主关键词位置评分（满分30分）──
        primary_kw = keywords[0] if keywords else ""
        if primary_kw:
            pos_bonus = self._primary_keyword_position_score(title, primary_kw)
            position_score = min(30, max(0, 30 + pos_bonus - 30 + pos_bonus))
            # 重新设计：基础10分 + 位置加分
            base_pos = 10 if primary_kw.lower() in title.lower() else 0
            position_score = min(30, base_pos + pos_bonus)
            if pos_bonus < 0:
                deductions.append(f"主关键词「{primary_kw}」未在标题中出现，扣20分")
            elif pos_bonus < 20:
                deductions.append(f"主关键词「{primary_kw}」位置靠后，建议移至前30%")
            scores["主关键词位置"] = {
                "得分": position_score, "满分": 30,
                "关键词": primary_kw,
                "位置分": pos_bonus,
                "建议": "前30%最优" if pos_bonus < 20 else "位置良好",
            }
        else:
            position_score = 15  # 无关键词给中间分
            scores["主关键词位置"] = {"得分": 15, "满分": 30, "说明": "未提供目标关键词，无法精确评分"}

        # ── 3. 关键词覆盖率（满分25分）──
        if keywords:
            covered = sum(1 for kw in keywords if kw.lower() in title.lower())
            coverage_pct = covered / len(keywords)
            kw_score = round(coverage_pct * 25)
            if coverage_pct < 0.6:
                missing = [kw for kw in keywords if kw.lower() not in title.lower()]
                deductions.append(f"关键词覆盖率{coverage_pct:.0%}，未包含: {', '.join(missing[:3])}")
            scores["关键词覆盖"] = {
                "得分": kw_score, "满分": 25,
                "覆盖率": f"{coverage_pct:.0%}",
                "已覆盖": covered, "总关键词": len(keywords),
            }
        else:
            kw_score = 15
            scores["关键词覆盖"] = {"得分": 15, "满分": 25, "说明": "未提供关键词列表"}

        # ── 4. 可读性评分（满分20分）──
        # 检查重复字、特殊符号滥用、断句自然性
        import re
        special_chars = len(re.findall(r"[！？!?★☆▶◆●■□\[\]【】《》<>~～]", title))
        repeat_penalty = 0
        for i in range(1, len(title)):
            if title[i] == title[i - 1] and title[i] not in "0123456789":
                repeat_penalty += 2

        readable_score = max(0, 20 - special_chars * 2 - repeat_penalty)
        if special_chars > 3:
            deductions.append(f"特殊符号过多({special_chars}个)，影响可读性，扣{special_chars*2}分")
        scores["可读性"] = {"得分": readable_score, "满分": 20, "特殊符号数": special_chars}

        # ── 综合评分 ──
        total = len_score + position_score + kw_score + readable_score
        grade = (
            "S (优秀)" if total >= 85
            else "A (良好)" if total >= 70
            else "B (及格)" if total >= 55
            else "C (需改进)" if total >= 40
            else "D (较差)"
        )

        result = {
            "平台": platform,
            "标题": title,
            "综合SEO评分": f"{total}/100",
            "总分": total,
            "评级": grade,
            "分项评分": scores,
            "扣分项": deductions if deductions else ["无明显扣分项"],
            "优化建议": (
                [
                    f"将主关键词「{primary_kw}」移至标题最前30%字符",
                ] if primary_kw and pos_bonus < 20 else []
            ) + (
                [f"补充关键词: {', '.join([kw for kw in keywords if kw.lower() not in title.lower()][:2])}"]
                if keywords and coverage_pct < 0.6 else []
            ) + (
                ["控制字符数在推荐范围内"] if title_len < lo or title_len > hi else []
            ) or ["标题质量良好，维持现有结构"],
        }

        # ── LLM深度诊断：评分解读 + 改写方案 ──
        try:
            from ._content_engine import _call, _SYSTEM_SEO_EXPERT
            kw_hint = f"目标关键词：{', '.join(keywords[:4])}" if keywords else "（未提供目标关键词）"
            llm_prompt = (
                f"你是电商SEO标题优化专家。对以下标题SEO评分结果做深度解读，并给出2个改写版本。\n\n"
                f"平台：{platform}\n原标题：{title}\n{kw_hint}\n"
                f"SEO评分：{total}/100（{grade}）\n扣分点：{'; '.join(deductions) if deductions else '无'}\n\n"
                f"请输出：\n"
                f"**评分解读**（为什么得{total}分，核心问题在哪）\n\n"
                f"**改写版本A**（优先保留关键词密度，修复主要扣分点）：[标题]\n理由：[说明改动点]\n\n"
                f"**改写版本B**（更激进优化，最大化SEO权重）：[标题]\n理由：[说明改动点]"
            )
            llm_insight = await _call(_SYSTEM_SEO_EXPERT, llm_prompt, max_tokens=450, temperature=0.6)
            if llm_insight:
                result["AI改写方案"] = llm_insight
        except Exception:
            pass

        return result


ALL_SKILLS: list[SkillBase] = [
    WebSEOOptimize(),
    WebTitleGenerator(),
    WebPageConversion(),
    WebStoreDesign(),
    WebKeywordResearch(),
    WebProductDescription(),
    WebTitleSEOScorer(),
]
