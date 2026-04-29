"""
search.py — 网络搜索技能（技能层）

为所有岗位 Agent 提供实时互联网搜索能力。
任何角色均可调用，获取最新市场信息、竞品动态、行业趋势。

搜索引擎实现已完全剥离至 _search_providers 子包，此文件只包含：
  - _web_search()     统一搜索入口（薄封装，委托给当前激活的提供商）
  - _format_results_for_llm()   结果格式化工具
  - 6 个 SearchXxx skill 类    业务技能

替换搜索后端（热插拔，无需修改此文件）：
    from src.skills._search_providers import set_provider, FreeWebSearchProvider
    set_provider(FreeWebSearchProvider())

智能调度：仅当问题需要实时数据时触发搜索（由 _needs_fresh_data 标志控制）。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

from .base import SkillBase

logger = logging.getLogger(__name__)


async def _web_search(
    query: str,
    topic: str = "general",
    max_results: int = 5,
    days: int = 30,
    bypass_cache: bool = False,
) -> Tuple[List[Dict[str, Any]], str]:
    """
    统一搜索入口 — 委托给当前激活的搜索提供商。

    返回 (results, engine_name)。
    bypass_cache=True 时跳过 TTL 缓存，强制获取最新数据。
    提供商可运行时替换：from ._search_providers import set_provider
    """
    from ._search_providers import get_provider
    return await get_provider().search(query, topic, max_results, days, bypass_cache=bypass_cache)


def _format_results_for_llm(results: List[Dict[str, Any]], max_per_item: int = 400) -> str:
    """将搜索结果格式化为 LLM 可读文本（带分析指令）。"""
    if not results:
        return "（实时搜索未找到结果，请基于行业经验给出保守估计，并注明'数据不足'）"
    lines = [f"（共{len(results[:5])}条实时结果，优先从中提取具体数字和结论）"]
    for i, r in enumerate(results[:5], 1):
        title = r.get("title", "无标题")
        body = r.get("body", "")[:max_per_item]
        url = r.get("url", "")
        lines.append(f"\n[{i}] {title}")
        if body:
            lines.append(body)
        if url:
            lines.append(f"    ↳ {url}")
    lines.append("\n注：遇到上述数据与通用认知不同时，以实时搜索数据为准，并在分析中注明'据实时数据'。")
    return "\n".join(lines)

def _compact_search_sources(results: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, str]]:
    compact: List[Dict[str, str]] = []
    seen: set[str] = set()
    for r in results:
        if not isinstance(r, dict):
            continue
        title = str(r.get("title") or "").strip()
        url = str(r.get("url") or "").strip()
        snippet = str(r.get("body") or r.get("snippet") or r.get("summary") or "").strip()
        published_at = str(r.get("published_at") or r.get("publishedAt") or r.get("pubDate") or "").strip()
        if not title and not url:
            continue
        key = (url or title).rstrip("/").lower()
        if key in seen:
            continue
        seen.add(key)
        item: Dict[str, str] = {
            "title": title[:120] or "未命名来源",
            "url": url[:300],
            "snippet": snippet[:220],
        }
        if published_at:
            item["published_at"] = published_at[:64]
        compact.append(item)
        if len(compact) >= limit:
            break
    return compact



# ── 技能实现 ─────────────────────────────────────────────────────────────────

class SearchTrends(SkillBase):
    """市场趋势搜索 — 实时获取热卖商品、爆款话题、消费趋势"""

    def __init__(self) -> None:
        super().__init__(
            name="search_trends",
            display_name="市场趋势搜索",
            description="实时搜索热卖商品、爆款话题、消费趋势、流行风格。获取平台当前热门动向，辅助选品、内容创作和运营决策。",
            category="search",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词，如商品类目、品类名称、热门话题",
                    },
                    "platform": {
                        "type": "string",
                        "description": "平台（淘宝/抖音/小红书/京东/拼多多，可选）",
                    },
                    "days": {
                        "type": "integer",
                        "description": "搜索最近N天内信息（默认30天）",
                    },
                },
                "required": ["query"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        query: str = kwargs.get("query", "")
        platform: str = kwargs.get("platform", "")
        days: int = int(kwargs.get("days", 30))
        bypass_cache: bool = bool(kwargs.get("_needs_fresh_data", False))

        if not query:
            return {"error": "请提供搜索关键词"}

        # 构建针对性查询
        platform_tag = f"{platform} " if platform else "电商 "
        search_query = f"{query} {platform_tag}热销 趋势 爆款 2026"

        results, engine = await _web_search(search_query, topic="general", max_results=6, days=days, bypass_cache=bypass_cache)
        results_text = _format_results_for_llm(results)

        from ._content_engine import _call, _SYSTEM_CONTENT_EXPERT

        prompt = (
            f'你是资深电商市场研究专家。基于以下实时网络搜索结果，分析"{query}"在'
            f'{"「" + platform + "」" if platform else "电商市场"}的最新趋势。\n\n'
            f"搜索结果：\n{results_text}\n\n"
            "请给出专业分析（直接输出，不要前言）：\n"
            "## 趋势概述\n（当前市场热度和主要方向，2-3句）\n\n"
            "## 热门卖点\n（消费者最关注的核心需求，列3-5条）\n\n"
            "## 内容方向\n（适合营销内容的话题角度，列3条具体方向）\n\n"
            "## 竞争格局\n（市场竞争程度和差异化机会）\n\n"
            "## 选品/运营建议\n（基于趋势的2-3条具体可执行建议）"
        )

        analysis = await _call(_SYSTEM_CONTENT_EXPERT, prompt, max_tokens=900, temperature=0.5)

        return {
            "搜索词": query,
            "平台": platform or "全平台",
            "时间范围": f"最近{days}天",
            "趋势分析": analysis or "（搜索结果有限，建议扩展关键词重试）",
            "原始结果数": len(results),
            "sources": _compact_search_sources(results, limit=5),
            "_search_engine": engine,
        }


class SearchCompetitor(SkillBase):
    """竞品搜索 — 实时获取竞争对手定价、促销、商品信息"""

    def __init__(self) -> None:
        super().__init__(
            name="search_competitor",
            display_name="竞品信息搜索",
            description="实时搜索竞争对手商品信息、定价策略、促销活动、用户评价和差异化卖点，帮助制定有竞争力的运营策略。",
            category="search",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "竞品关键词，如商品名称、品牌名、品类名",
                    },
                    "platform": {
                        "type": "string",
                        "description": "平台（淘宝/京东/拼多多/抖音，可选）",
                    },
                    "focus": {
                        "type": "string",
                        "description": "分析重点：price（定价）/ promotion（促销）/ reviews（用户评价）/ features（核心卖点）",
                    },
                },
                "required": ["query"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        query: str = kwargs.get("query", "")
        platform: str = kwargs.get("platform", "")
        focus: str = kwargs.get("focus", "")
        bypass_cache: bool = bool(kwargs.get("_needs_fresh_data", False))

        if not query:
            return {"error": "请提供竞品关键词"}

        focus_map = {
            "price": "价格 定价 价位 售价",
            "promotion": "促销 活动 优惠 折扣 限时",
            "reviews": "用户评价 口碑 差评 真实体验",
            "features": "卖点 功能特点 优势 核心参数",
        }
        focus_text = focus_map.get(focus, "价格 卖点 评价 优惠 竞品对比")
        plat_tag = f"{platform} " if platform else ""
        search_query = f"{query} {plat_tag}{focus_text} 2026"

        results, engine = await _web_search(search_query, topic="general", max_results=6, days=60, bypass_cache=bypass_cache)
        results_text = _format_results_for_llm(results)

        from ._content_engine import _call, _SYSTEM_ANALYSIS_EXPERT

        prompt = (
            f'你是电商竞争情报分析专家。基于以下实时搜索结果，对"{query}"进行竞品分析。\n\n'
            f"搜索结果：\n{results_text}\n\n"
            "请输出竞品分析报告（直接输出）：\n"
            "## 市场定价区间\n（竞品价格分布和主流定价策略）\n\n"
            "## 竞品核心卖点\n（竞争对手主打的3-5个卖点）\n\n"
            "## 促销手段\n（竞品常用促销形式）\n\n"
            "## 用户真实反馈\n（消费者的主要评价，含正负面）\n\n"
            "## 差异化机会\n（市场空白和可差异化的方向）\n\n"
            "## 应对建议\n（针对竞品的3条具体运营策略）"
        )

        analysis = await _call(_SYSTEM_ANALYSIS_EXPERT, prompt, max_tokens=900, temperature=0.4)

        return {
            "竞品关键词": query,
            "平台": platform or "全平台",
            "分析重点": {"price": "定价", "promotion": "促销", "reviews": "评价", "features": "卖点"}.get(focus, "综合"),
            "竞品分析报告": analysis or "（搜索结果不足，建议提供更具体的品牌名或商品名）",
            "数据来源数": len(results),
            "sources": _compact_search_sources(results, limit=5),
            "_search_engine": engine,
        }


class SearchMarketInfo(SkillBase):
    """市场行情搜索 — 实时获取行业报告、政策变化、市场数据"""

    def __init__(self) -> None:
        super().__init__(
            name="search_market_info",
            display_name="市场行情搜索",
            description="实时搜索行业市场报告、政策法规变化、平台规则更新、行业新闻和市场数据，为战略决策提供实时行情依据。",
            category="search",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "行业/话题关键词，如行业名、政策名、市场主题",
                    },
                    "topic": {
                        "type": "string",
                        "description": "搜索类型：general（通用）/ news（新闻资讯）/ finance（财经数据）",
                    },
                    "days": {
                        "type": "integer",
                        "description": "搜索最近N天内信息（默认7天）",
                    },
                },
                "required": ["query"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        query: str = kwargs.get("query", "")
        topic: str = kwargs.get("topic", "news")
        days: int = int(kwargs.get("days", 7))
        bypass_cache: bool = bool(kwargs.get("_needs_fresh_data", False))

        if not query:
            return {"error": "请提供搜索关键词"}

        recency = "最新" if days <= 7 else f"{days}天内"
        search_query = f"{query} {recency} 行业动态 市场分析 2026"

        results, engine = await _web_search(search_query, topic=topic, max_results=6, days=days, bypass_cache=bypass_cache)
        results_text = _format_results_for_llm(results)

        from ._content_engine import _call, _SYSTEM_ANALYSIS_EXPERT

        prompt = (
            f'你是行业研究分析师。基于以下实时搜索结果，提炼"{query}"的市场行情要点。\n\n'
            f"搜索结果：\n{results_text}\n\n"
            "请输出行情分析（直接输出）：\n"
            "## 核心动态\n（最重要的市场动态，3-5条要点）\n\n"
            "## 数据与规模\n（关键市场数据或趋势数字，如有）\n\n"
            "## 政策/规则影响\n（相关政策或平台规则的影响分析）\n\n"
            "## 机会与风险\n（对电商经营的机会和潜在风险）\n\n"
            "## 行动建议\n（基于当前行情的2-3条具体建议）"
        )

        analysis = await _call(_SYSTEM_ANALYSIS_EXPERT, prompt, max_tokens=800, temperature=0.3)

        return {
            "查询主题": query,
            "搜索类型": {"general": "通用", "news": "新闻", "finance": "财经"}.get(topic, topic),
            "时间范围": f"最近{days}天",
            "市场行情报告": analysis or "（未能获取有效行情信息，建议更换关键词）",
            "信息来源数": len(results),
            "sources": _compact_search_sources(results, limit=5),
            "_search_engine": engine,
        }


class SearchProductReviews(SkillBase):
    """用户评价搜索 — 实时获取真实用户口碑、好评/差评、使用反馈"""

    def __init__(self) -> None:
        super().__init__(
            name="search_product_reviews",
            display_name="用户评价搜索",
            description="实时搜索指定商品或品牌的真实用户评价、口碑反馈、好差评分布；用于洞察消费者真实体验、发现改善机会和内容素材。",
            category="search",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "商品名称、品牌名或品类关键词",
                    },
                    "platform": {
                        "type": "string",
                        "description": "平台（淘宝/京东/拼多多/抖音，可选）",
                    },
                    "focus": {
                        "type": "string",
                        "description": "关注点：positive（好评亮点）/ negative（差评痛点）/ overall（综合口碑）",
                    },
                },
                "required": ["query"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        query: str = kwargs.get("query", "")
        platform: str = kwargs.get("platform", "")
        focus: str = kwargs.get("focus", "overall")
        bypass_cache: bool = bool(kwargs.get("_needs_fresh_data", False))

        if not query:
            return {"error": "请提供商品/品牌关键词"}

        focus_map = {
            "positive": "好评 亮点 优点 推荐 满意 值得买",
            "negative": "差评 缺点 投诉 退货 踩雷 槽点",
            "overall": "用户评价 口碑 真实反馈 购买体验 评测",
        }
        focus_text = focus_map.get(focus, focus_map["overall"])
        plat_tag = f"{platform} " if platform else ""
        search_query = f"{query} {plat_tag}{focus_text} 2026"

        results, engine = await _web_search(search_query, topic="general", max_results=6, days=90, bypass_cache=bypass_cache)
        results_text = _format_results_for_llm(results)

        from ._content_engine import _call, _SYSTEM_ANALYSIS_EXPERT

        prompt = (
            f'你是消费者洞察专家。基于以下真实网络搜索结果，分析\"{query}\"的用户口碑。\n\n'
            f"搜索结果：\n{results_text}\n\n"
            "请输出用户评价分析（直接输出）：\n"
            "## 整体口碑\n（用户评价总体倾向和情感分布）\n\n"
            "## 核心好评点\n（用户最常提及的3-5个满意点，附典型评价摘录）\n\n"
            "## 主要差评点\n（用户最常投诉的3个痛点，附真实反馈描述）\n\n"
            "## 高频关键词\n（评价中出现频率最高的词汇，正负各5个）\n\n"
            "## 内容/产品优化建议\n（基于口碑分析的2-3条具体改进方向）"
        )

        analysis = await _call(_SYSTEM_ANALYSIS_EXPERT, prompt, max_tokens=800, temperature=0.4)

        return {
            "搜索词": query,
            "平台": platform or "全平台",
            "关注维度": {"positive": "好评亮点", "negative": "差评痛点", "overall": "综合口碑"}.get(focus, "综合口碑"),
            "用户口碑分析": analysis or "（未能获取足够评价数据，建议更换关键词重试）",
            "原始数据数": len(results),
            "sources": _compact_search_sources(results, limit=5),
            "_search_engine": engine,
        }


class SearchPlatformPolicy(SkillBase):
    """平台政策搜索 — 实时获取平台规则变化、算法更新、政策调整"""

    def __init__(self) -> None:
        super().__init__(
            name="search_platform_policy",
            display_name="平台政策搜索",
            description="实时搜索电商平台最新规则变化、算法调整、流量政策、佣金费率、大促规则等，确保运营策略符合最新平台要求。",
            category="search",
            input_schema={
                "type": "object",
                "properties": {
                    "platform": {
                        "type": "string",
                        "description": "平台名称（淘宝/京东/拼多多/抖音/小红书等）",
                    },
                    "policy_type": {
                        "type": "string",
                        "description": "政策类型：algorithm（搜索算法）/ fee（佣金费率）/ promotion（大促规则）/ content（内容规范）/ general（综合政策）",
                    },
                    "days": {
                        "type": "integer",
                        "description": "搜索最近N天内信息（默认30天）",
                    },
                },
                "required": ["platform"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        platform: str = kwargs.get("platform", "")
        policy_type: str = kwargs.get("policy_type", "general")
        days: int = int(kwargs.get("days", 30))
        bypass_cache: bool = bool(kwargs.get("_needs_fresh_data", False))

        if not platform:
            return {"error": "请提供平台名称"}

        type_map = {
            "algorithm": "搜索算法 流量规则 排名机制 权重调整",
            "fee": "佣金 费率 扣点 服务费 收费标准",
            "promotion": "大促规则 活动报名 双11 618 商家政策",
            "content": "内容规范 违禁词 审核标准 发布规定 违规处罚",
            "general": "规则变化 政策更新 新规 公告",
        }
        type_text = type_map.get(policy_type, type_map["general"])
        search_query = f"{platform} {type_text} 最新 2026"

        results, engine = await _web_search(search_query, topic="news", max_results=6, days=days, bypass_cache=bypass_cache)
        results_text = _format_results_for_llm(results)

        from ._content_engine import _call, _SYSTEM_ANALYSIS_EXPERT

        prompt = (
            f'你是电商平台运营专家。基于以下实时搜索结果，分析\"{platform}\"平台的最新政策动态。\n\n'
            f"搜索结果：\n{results_text}\n\n"
            "请输出政策解读（直接输出）：\n"
            "## 关键政策变化\n（最重要的规则调整，3-5条要点）\n\n"
            "## 对卖家的直接影响\n（规则变化如何影响运营、流量、成本）\n\n"
            "## 合规要求\n（需要注意避免违规的关键事项）\n\n"
            "## 应对建议\n（针对最新政策的2-3条具体调整建议）\n\n"
            "## 时效性提示\n（政策生效时间或重要时间节点）"
        )

        analysis = await _call(_SYSTEM_ANALYSIS_EXPERT, prompt, max_tokens=800, temperature=0.3)

        return {
            "平台": platform,
            "政策类型": {"algorithm": "搜索算法", "fee": "佣金费率", "promotion": "大促规则",
                        "content": "内容规范", "general": "综合政策"}.get(policy_type, "综合政策"),
            "时间范围": f"最近{days}天",
            "政策解读": analysis or "（未能获取有效政策信息，建议直接访问平台官方公告）",
            "信息来源数": len(results),
            "sources": _compact_search_sources(results, limit=5),
            "_search_engine": engine,
        }


class SearchIndustryBenchmarks(SkillBase):
    """行业基准搜索 — 实时获取行业KPI基准、均值数据、竞争水平参照"""

    def __init__(self) -> None:
        super().__init__(
            name="search_industry_benchmarks",
            display_name="行业基准搜索",
            description="实时搜索行业转化率、客单价、NPS、DSR、退款率、广告ROI等KPI基准数据，为绩效评估和目标设定提供行业参照系。",
            category="search",
            input_schema={
                "type": "object",
                "properties": {
                    "industry": {
                        "type": "string",
                        "description": "行业类目（如：美妆/服装/3C/食品/家居等）",
                    },
                    "metric": {
                        "type": "string",
                        "description": "关注指标：conversion（转化率）/ aov（客单价）/ nps（NPS）/ refund（退款率）/ roi（广告ROI）/ general（综合基准）",
                    },
                    "platform": {
                        "type": "string",
                        "description": "平台（可选，不填则返回全平台基准）",
                    },
                },
                "required": ["industry"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        industry: str = kwargs.get("industry", "")
        metric: str = kwargs.get("metric", "general")
        platform: str = kwargs.get("platform", "")
        bypass_cache: bool = bool(kwargs.get("_needs_fresh_data", False))

        if not industry:
            return {"error": "请提供行业类目"}

        metric_map = {
            "conversion": "转化率 成交转化 CVR 行业均值 基准",
            "aov": "客单价 平均订单金额 消费水平 均值",
            "nps": "NPS 净推荐值 客户满意度 行业标准 平均分",
            "refund": "退款率 退货率 售后率 行业水平 均值",
            "roi": "广告ROI 投产比 ROAS 营销效率 基准",
            "general": "行业数据 KPI基准 平均水平 运营指标 数据报告",
        }
        metric_text = metric_map.get(metric, metric_map["general"])
        plat_tag = f"{platform} " if platform else "电商 "
        search_query = f"{industry} {plat_tag}{metric_text} 2025 2026"

        results, engine = await _web_search(search_query, topic="general", max_results=6, days=180, bypass_cache=bypass_cache)
        results_text = _format_results_for_llm(results)

        from ._content_engine import _call, _SYSTEM_ANALYSIS_EXPERT

        prompt = (
            f'你是行业数据分析专家。基于以下搜索结果，提取\"{industry}\"行业的KPI基准数据。\n\n'
            f"搜索结果：\n{results_text}\n\n"
            "请输出行业基准报告（直接输出）：\n"
            "## 核心KPI基准\n（主要指标的行业均值/优秀线/头部水平，用数字说话）\n\n"
            "## 平台差异\n（不同平台间的基准差异，如有数据）\n\n"
            "## 所处位置判断\n（如何利用这些基准判断自己的运营水平）\n\n"
            "## 提升优先级\n（哪些指标提升空间最大，ROI最高）\n\n"
            "## 数据可信度说明\n（来源及时效性评估）"
        )

        analysis = await _call(_SYSTEM_ANALYSIS_EXPERT, prompt, max_tokens=800, temperature=0.3)

        return {
            "行业": industry,
            "平台": platform or "全平台",
            "关注指标": {"conversion": "转化率", "aov": "客单价", "nps": "NPS",
                        "refund": "退款率", "roi": "广告ROI", "general": "综合基准"}.get(metric, "综合基准"),
            "行业基准报告": analysis or "（未能获取行业数据，建议更换关键词或查阅平台官方数据报告）",
            "数据来源数": len(results),
            "sources": _compact_search_sources(results, limit=5),
            "_search_engine": engine,
        }


ALL_SKILLS: List[SkillBase] = [
    SearchTrends(),
    SearchCompetitor(),
    SearchMarketInfo(),
    SearchProductReviews(),
    SearchPlatformPolicy(),
    SearchIndustryBenchmarks(),
]
