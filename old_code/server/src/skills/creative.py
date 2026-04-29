from __future__ import annotations

from typing import Any, Dict, List

from .base import SkillBase
from ._db_helpers import load_product_info, load_user_products
from ._content_engine import (
    generate_video_script,
    generate_seeding_copy,
    generate_live_script,
    generate_product_article,
    generate_ip_branding,
    generate_content_calendar,
    generate_trend_analysis,
)
from ._db_helpers import load_metrics_summary


class CreativeVideoScript(SkillBase):
    """短视频脚本技能 — 基于真实商品数据生成脚本"""

    def __init__(self) -> None:
        super().__init__(
            name="creative_video_script",
            display_name="短视频脚本",
            description="根据商品和平台特点，生成短视频脚本框架；传入product_id可自动加载真实商品信息",
            category="creative",
            input_schema={
                "type": "object",
                "properties": {
                    "product_name": {"type": "string", "description": "商品名称（可从product_id自动加载）"},
                    "video_type": {
                        "type": "string",
                        "description": "视频类型：种草/测评/教程/剧情/开箱",
                    },
                    "duration": {"type": "integer", "description": "目标时长（秒）"},
                    "platform": {"type": "string", "description": "平台：抖音/快手/小红书/B站"},
                    "product_id": {"type": "integer", "description": "商品ID，自动加载商品信息和卖点"},
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        name: str = kwargs.get("product_name", "")
        video_type: str = kwargs.get("video_type", "种草")
        duration: int = kwargs.get("duration", 30)
        platform: str = kwargs.get("platform", "抖音")
        product_id: int = kwargs.get("product_id", 0)
        extra: str = kwargs.get("extra_requirements", "")

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
            return {"error": "请提供 product_name 或 product_id", "提示": "可传入商品ID自动加载商品信息"}

        # 搜索当前平台爆款视频风格和趋势
        search_ctx = ""
        if kwargs.get("_needs_fresh_data", True):
            try:
                from .search import _web_search, _format_results_for_llm
                pname_q = pinfo.get("name", name or "商品")
                q = f"{platform} {pname_q} 爆款视频 {video_type} 热门内容 2026"
                results, _ = await _web_search(q, topic="general", max_results=4, days=14)
                if results:
                    search_ctx = _format_results_for_llm(results, max_per_item=250)
            except Exception:
                pass

        # 调用内容引擎生成完整真实脚本
        result = await generate_video_script(
            product_info=pinfo,
            platform=platform,
            video_type=video_type,
            duration=duration,
            extra_requirements=extra,
        )
        # 若有实时风格数据，追加到结果
        if search_ctx:
            result["实时爆款参考"] = search_ctx
        return result


class CreativeSeedingCopy(SkillBase):
    """种草文案技能 — 基于真实商品卖点生成文案"""

    def __init__(self) -> None:
        super().__init__(
            name="creative_seeding_copy",
            display_name="种草文案",
            description="生成小红书/社交媒体种草文案框架；传入product_id可自动加载商品卖点",
            category="creative",
            input_schema={
                "type": "object",
                "properties": {
                    "product_name": {"type": "string", "description": "商品名称（可从product_id自动加载）"},
                    "selling_points": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "核心卖点（可从product_id自动加载）",
                    },
                    "target_audience": {"type": "string", "description": "目标人群"},
                    "tone": {
                        "type": "string",
                        "description": "语气：闺蜜分享/专业测评/生活记录",
                    },
                    "product_id": {"type": "integer", "description": "商品ID，自动加载商品信息和卖点"},
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        name: str = kwargs.get("product_name", "")
        audience: str = kwargs.get("target_audience", "年轻女性")
        tone: str = kwargs.get("tone", "闺蜜分享")
        platform: str = kwargs.get("platform", "小红书")
        product_id: int = kwargs.get("product_id", 0)

        pinfo: Dict[str, Any] = {}

        # ── 自动加载真实商品卖点 ──
        if user_id and product_id:
            loaded = await load_product_info(user_id, product_id)
            if loaded:
                pinfo = loaded
                if not name:
                    name = pinfo.get("name", "")
        elif name:
            sp: List[str] = kwargs.get("selling_points", [])
            pinfo = {"name": name, "selling_points": sp}

        if not name and not pinfo.get("name"):
            return {"error": "请提供 product_name 或 product_id"}

        # 搜索当前平台种草文案热门风格和标签趋势
        search_ctx = ""
        if kwargs.get("_needs_fresh_data", True):
            try:
                from .search import _web_search, _format_results_for_llm
                pname_q = pinfo.get("name", name or "商品")
                q = f"{platform} {pname_q} 种草文案 热门风格 爆款话题 2026"
                results, _ = await _web_search(q, topic="general", max_results=4, days=14)
                if results:
                    search_ctx = _format_results_for_llm(results, max_per_item=250)
            except Exception:
                pass

        # 调用内容引擎生成完整真实种草文案
        result = await generate_seeding_copy(
            product_info=pinfo,
            platform=platform,
            tone=tone,
            target_audience=audience,
            include_title=True,
        )
        if search_ctx:
            result["实时风格参考"] = search_ctx
        return result


class CreativeIPBranding(SkillBase):
    """IP定位技能"""

    def __init__(self) -> None:
        super().__init__(
            name="creative_ip_branding",
            display_name="IP定位",
            description="为品牌或个人账号做IP人设定位",
            category="creative",
            input_schema={
                "type": "object",
                "properties": {
                    "brand_name": {"type": "string", "description": "品牌/账号名称"},
                    "category": {"type": "string", "description": "领域类目"},
                    "target_audience": {"type": "string", "description": "目标受众"},
                    "differentiator": {"type": "string", "description": "核心差异化点"},
                    "platform": {"type": "string", "description": "主要运营平台，如 小红书/抖音/B站"},
                },
                "required": ["brand_name", "category"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        brand = kwargs.get("brand_name", "品牌")
        category = kwargs.get("category", "通用")
        audience = kwargs.get("target_audience", "18-35岁年轻消费者")
        diff = kwargs.get("differentiator", "专业+有温度")
        platform = kwargs.get("platform", "小红书/抖音")

        return await generate_ip_branding(
            brand_name=brand,
            category=category,
            target_audience=audience,
            differentiator=diff,
            platform=platform,
        )


class CreativeContentCalendar(SkillBase):
    """内容排期技能"""

    def __init__(self) -> None:
        super().__init__(
            name="creative_content_calendar",
            display_name="内容排期",
            description="根据运营节奏生成周/月内容发布计划",
            category="creative",
            input_schema={
                "type": "object",
                "properties": {
                    "period": {"type": "string", "description": "周期：周/月"},
                    "platforms": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "发布平台列表",
                    },
                    "content_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "内容类型",
                    },
                    "weekly_posts": {"type": "integer", "description": "每周发布数量"},
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        period = kwargs.get("period", "月")
        platforms: List[str] = kwargs.get("platforms", ["抖音", "小红书"])
        content_types: List[str] = kwargs.get("content_types", ["种草测评", "干货教程", "生活日常", "互动话题"])
        weekly = kwargs.get("weekly_posts", 5)

        # 加载真实业务指标作为内容目标参考
        real_metrics: Dict = {}
        if user_id:
            try:
                real_metrics = await load_metrics_summary(user_id, days=30)
            except Exception:
                pass

        # 实时搜索近期营销节点与热点话题（仅当需要实时数据时）
        search_context = ""
        if kwargs.get("_needs_fresh_data", True):
            try:
                from .search import _web_search, _format_results_for_llm
                plat_str = "、".join(platforms[:2]) if platforms else "抖音"
                search_query = f"电商营销节日 热门话题 内容节点 {plat_str} 2026"
                results, _eng = await _web_search(search_query, topic="general", max_results=5, days=30)
                if results:
                    search_context = _format_results_for_llm(results, max_per_item=300)
            except Exception:
                pass

        result = await generate_content_calendar(
            platforms=platforms,
            period=period,
            content_types=content_types,
            weekly_posts=weekly,
            real_metrics=real_metrics,
            search_context=search_context,
        )
        result["数据增强"] = "实时营销节点搜索" if search_context else "LLM知识库"
        return result


class CreativeTrendCatch(SkillBase):
    """趋势捕捉技能"""

    def __init__(self) -> None:
        super().__init__(
            name="creative_trend_catch",
            display_name="趋势捕捉",
            description="分析当前内容趋势，输出可借鉴的创意方向",
            category="creative",
            input_schema={
                "type": "object",
                "properties": {
                    "category": {"type": "string", "description": "行业类目"},
                    "platform": {"type": "string", "description": "平台"},
                },
                "required": ["category"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        category = kwargs.get("category", "通用")
        platform = kwargs.get("platform", "抖音")

        # 加载真实业务指标供LLM个性化建议
        real_metrics: Dict = {}
        if user_id:
            try:
                real_metrics = await load_metrics_summary(user_id, days=30)
            except Exception:
                pass

        # 实时搜索最新趋势（仅当需要实时数据时）
        search_context = ""
        if kwargs.get("_needs_fresh_data", True):
            try:
                from .search import _web_search, _format_results_for_llm
                search_query = f"{category} {platform} 热门内容趋势 爆款 2026"
                results, _eng = await _web_search(search_query, topic="general", max_results=5, days=14)
                if results:
                    search_context = _format_results_for_llm(results, max_per_item=350)
            except Exception:
                pass

        result = await generate_trend_analysis(
            category=category,
            platform=platform,
            real_metrics=real_metrics,
            search_context=search_context,
        )
        result["数据增强"] = "实时网络搜索" if search_context else "LLM知识库"
        return result


class CreativeLiveScript(SkillBase):
    """直播脚本技能 — 生成完整直播话术（含开场/讲品/催单台词）"""

    def __init__(self) -> None:
        super().__init__(
            name="creative_live_script",
            display_name="直播脚本",
            description="生成完整直播话术脚本，含每个阶段的真实台词；可传入product_ids自动加载商品列表",
            category="creative",
            input_schema={
                "type": "object",
                "properties": {
                    "duration_hours": {"type": "number", "description": "直播时长（小时）"},
                    "product_count": {"type": "integer", "description": "上架商品数量（无product_ids时用）"},
                    "live_type": {
                        "type": "string",
                        "description": "直播类型：日常带货/大促专场/新品首发/清仓特卖",
                    },
                    "platform": {"type": "string", "description": "平台：抖音/快手/淘宝直播"},
                    "product_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "商品ID列表，自动加载商品信息生成更真实的话术",
                    },
                },
                "required": ["duration_hours"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        hours: float = kwargs.get("duration_hours", 3)
        live_type: str = kwargs.get("live_type", "日常带货")
        platform: str = kwargs.get("platform", "抖音")
        product_ids: List[int] = kwargs.get("product_ids", [])

        # ── 自动加载真实商品列表 ──
        product_list: List[Dict[str, Any]] = []
        if user_id and product_ids:
            for pid in product_ids[:8]:
                pinfo = await load_product_info(user_id, pid)
                if pinfo:
                    product_list.append(pinfo)
        elif user_id:
            # 自动加载最近商品（最多6个）
            products = await load_user_products(user_id, limit=6)
            product_list = products

        # 调用内容引擎生成完整真实话术脚本
        result = await generate_live_script(
            product_list=product_list,
            duration_hours=hours,
            live_type=live_type,
            platform=platform,
        )
        return result


class CreativeProductArticle(SkillBase):
    """产品软文/推广文章生成技能 — 生成完整可发布的推广文章"""

    def __init__(self) -> None:
        super().__init__(
            name="creative_product_article",
            display_name="产品软文",
            description="生成完整可发布的产品软文/推广文章，含标题和正文；传入product_id自动加载商品信息",
            category="creative",
            input_schema={
                "type": "object",
                "properties": {
                    "product_name": {"type": "string", "description": "商品名称（可从product_id自动加载）"},
                    "product_id": {"type": "integer", "description": "商品ID，自动加载商品信息"},
                    "article_type": {
                        "type": "string",
                        "description": "文章类型：测评软文/种草长文/对比评测/使用攻略/品牌故事",
                    },
                    "word_count": {"type": "integer", "description": "目标字数，默认800字"},
                    "platform": {
                        "type": "string",
                        "description": "发布平台：微信公众号/小红书/知乎/抖音",
                    },
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        name: str = kwargs.get("product_name", "")
        product_id: int = kwargs.get("product_id", 0)
        article_type: str = kwargs.get("article_type", "测评软文")
        word_count: int = kwargs.get("word_count", 800)
        platform: str = kwargs.get("platform", "微信公众号")

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
            return {"error": "请提供 product_name 或 product_id"}

        # 调用内容引擎生成完整软文
        result = await generate_product_article(
            product_info=pinfo,
            article_type=article_type,
            word_count=word_count,
            platform=platform,
        )
        return result


class CreativeTitleCTRScorer(SkillBase):
    """标题CTR预测评分 — AIDA框架 + 情感触发词密度 + 稀缺/社交证明 + 预测点击率分"""

    # AIDA四要素权重
    _AIDA_WEIGHTS = {
        "Attention": 0.30,   # 注意力捕获
        "Interest": 0.25,    # 兴趣激发
        "Desire": 0.25,      # 欲望驱动
        "Action": 0.20,      # 行动号召
    }

    # 情感触发词库（按类别）
    _TRIGGER_WORDS = {
        "urgency": {
            "words": ["限时", "今天", "最后", "仅剩", "抢购", "秒杀", "即将", "截止", "倒计时", "马上"],
            "weight": 1.8,  # 紧迫感高权重
        },
        "scarcity": {
            "words": ["限量", "仅余", "稀缺", "孤品", "断码", "最后一批", "售完为止", "库存告急"],
            "weight": 1.6,
        },
        "social_proof": {
            "words": ["爆款", "热卖", "畅销", "好评", "口碑", "网红", "明星", "万人", "口碑推荐", "人气"],
            "weight": 1.4,
        },
        "authority": {
            "words": ["官方", "正品", "认证", "权威", "专业", "国家", "医院", "大牌", "品牌"],
            "weight": 1.3,
        },
        "value": {
            "words": ["超值", "划算", "省钱", "折扣", "优惠", "打折", "特价", "白菜价", "半价"],
            "weight": 1.5,
        },
        "emotion": {
            "words": ["惊喜", "感动", "完美", "心动", "必买", "真香", "安心", "放心", "幸福", "快乐"],
            "weight": 1.2,
        },
    }

    # AIDA关键词映射
    _AIDA_INDICATORS = {
        "Attention": ["限时", "爆款", "独家", "首发", "全网", "新品", "震撼", "惊喜", "超级"],
        "Interest": ["功能", "特点", "原因", "为什么", "如何", "秘密", "揭秘", "技巧", "方法", "攻略"],
        "Desire": ["完美", "必买", "值得", "推荐", "好用", "高品质", "升级", "加强", "专业", "极致"],
        "Action": ["抢", "买", "下单", "立即", "马上", "赶快", "别错过", "收藏", "加购", "点击"],
    }

    def __init__(self) -> None:
        super().__init__(
            name="creative_title_ctr_scorer",
            display_name="标题CTR预测评分",
            description="AIDA框架×情感触发词密度评分（0-100分），预测商品标题点击率；输入product_id自动加载标题，输出各维度得分+改进建议",
            category="creative",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "待评分的商品/内容标题"},
                    "platform": {"type": "string", "description": "平台：淘宝/京东/拼多多/抖音/小红书"},
                    "target_audience": {"type": "string", "description": "目标受众描述（可选）"},
                    "product_id": {"type": "integer", "description": "商品ID，自动加载标题"},
                },
                "required": [],
            },
        )

    def _score_aida(self, title: str) -> Dict[str, Any]:
        """计算AIDA各维度得分。"""
        aida_scores: Dict[str, float] = {}
        aida_hits: Dict[str, List[str]] = {}

        for element, kws in self._AIDA_INDICATORS.items():
            hits = [kw for kw in kws if kw in title]
            # 命中1个=基础分，每多1个+额外分，上限为1
            raw = min(1.0, 0.5 + len(hits) * 0.25)
            aida_scores[element] = raw
            aida_hits[element] = hits

        weighted = sum(
            aida_scores[e] * w for e, w in self._AIDA_WEIGHTS.items()
        )
        return {"scores": aida_scores, "hits": aida_hits, "weighted": round(weighted, 3)}

    def _score_triggers(self, title: str) -> Dict[str, Any]:
        """计算情感触发词密度评分。"""
        total_trigger_score = 0.0
        trigger_details: List[Dict] = []
        found_words: List[str] = []

        for cat, cfg in self._TRIGGER_WORDS.items():
            hits = [w for w in cfg["words"] if w in title]
            if hits:
                cat_score = len(hits) * cfg["weight"]
                total_trigger_score += cat_score
                trigger_details.append({
                    "类别": cat,
                    "命中词": hits,
                    "贡献分": round(cat_score, 2),
                })
                found_words.extend(hits)

        # 归一化到0-1（满分约为3.0）
        normalized = min(1.0, total_trigger_score / 3.0)
        return {
            "raw_score": round(total_trigger_score, 2),
            "normalized": normalized,
            "found_words": found_words,
            "details": trigger_details,
        }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        title: str = kwargs.get("title", "")
        platform: str = kwargs.get("platform", "淘宝")
        target_audience: str = kwargs.get("target_audience", "通用")
        product_id: int = kwargs.get("product_id", 0)

        # 自动加载商品标题
        if user_id and product_id and not title:
            pinfo = await load_product_info(user_id, product_id)
            if pinfo:
                title = pinfo.get("name", "")

        if not title:
            return {"error": "请提供 title 或 product_id"}

        # ── AIDA评分（0-40分）──
        aida_result = self._score_aida(title)
        aida_score = round(aida_result["weighted"] * 40)

        # ── 情感触发词密度（0-35分）──
        trigger_result = self._score_triggers(title)
        trigger_score = round(trigger_result["normalized"] * 35)

        # ── 标题结构评分（0-25分）──
        # 字数适中 + 无过多重复词 + 核心卖点在前
        import re
        title_len = len(title)
        # 平台最优字数
        len_opt = {"淘宝": (25, 32), "京东": (25, 35), "拼多多": (18, 25), "抖音": (12, 20)}.get(platform, (20, 30))
        if len_opt[0] <= title_len <= len_opt[1]:
            struct_score = 25
        else:
            struct_score = max(5, 25 - abs(title_len - (len_opt[0] + len_opt[1]) // 2))

        # ── 综合CTR预测 ──
        total = aida_score + trigger_score + struct_score

        # CTR基准估算（行业平均CTR 3-5%，评分每+10分 CTR+0.3%）
        base_ctr = {"淘宝": 3.2, "京东": 2.8, "拼多多": 4.5, "抖音": 5.0}.get(platform, 3.5)
        predicted_ctr = round(base_ctr + (total - 50) * 0.03, 2)
        predicted_ctr = max(0.5, min(15.0, predicted_ctr))

        # ── 评级 ──
        grade = (
            "S 高转化" if total >= 85
            else "A 良好" if total >= 70
            else "B 中等" if total >= 55
            else "C 待改进" if total >= 40
            else "D 需重写"
        )

        # ── 改进建议 ──
        suggestions = []
        if aida_result["weighted"] < 0.5:
            missing_elements = [e for e, s in aida_result["scores"].items() if s < 0.5]
            suggestions.append(f"加强AIDA缺失维度: {', '.join(missing_elements)}")
        if trigger_result["normalized"] < 0.3:
            suggestions.append("增加情感触发词（紧迫感/稀缺性/社交证明）")
        if title_len < len_opt[0]:
            suggestions.append(f"标题偏短，可补充卖点至{len_opt[0]}字以上")
        if not suggestions:
            suggestions.append("标题CTR潜力良好，可微调紧迫感词语")

        result = {
            "平台": platform,
            "标题": title,
            "目标受众": target_audience,
            "综合CTR评分": f"{total}/100",
            "总分": total,
            "评级": grade,
            "预测CTR": f"{predicted_ctr}%（行业{platform}基准{base_ctr}%）",
            "分项评分": {
                "AIDA结构": {
                    "得分": f"{aida_score}/40",
                    "命中要素": {e: hits for e, hits in aida_result["hits"].items() if hits},
                },
                "情感触发词密度": {
                    "得分": f"{trigger_score}/35",
                    "命中词": trigger_result["found_words"],
                    "详情": trigger_result["details"],
                },
                "标题结构": {
                    "得分": f"{struct_score}/25",
                    "字数": title_len,
                    "推荐范围": f"{len_opt[0]}-{len_opt[1]}字",
                },
            },
            "优化建议": suggestions,
        }

        # ── LLM深度解读：提供具体改写版本和竞争力分析 ──
        try:
            from ._content_engine import _call, _SYSTEM_CONTENT_EXPERT
            llm_prompt = (
                f'平台：{platform}，目标受众：{target_audience}\n'
                f'原始标题：{title}\n'
                f'评分：{total}/100（AIDA:{aida_score} 触发词:{trigger_score} 结构:{struct_score}）\n'
                f'缺失维度：{", ".join(suggestions)}\n\n'
                "作为标题优化专家，请给出：\n"
                "1. **评分解读**：为什么是这个分数，主要弱点在哪（1-2句）\n"
                "2. **改写版本A**：针对性优化后的标题（重写，不超过平台推荐字数）\n"
                "3. **改写版本B**：另一个差异化方向的版本\n"
                "4. **关键改动**：最重要的改动点是什么（1-2句）\n"
                "直接输出，不要前言。"
            )
            llm_insight = await _call(_SYSTEM_CONTENT_EXPERT, llm_prompt, max_tokens=400, temperature=0.7)
            if llm_insight:
                result["AI优化方案"] = llm_insight
        except Exception:
            pass

        return result


ALL_SKILLS: list[SkillBase] = [
    CreativeVideoScript(),
    CreativeSeedingCopy(),
    CreativeIPBranding(),
    CreativeContentCalendar(),
    CreativeTrendCatch(),
    CreativeLiveScript(),
    CreativeProductArticle(),
    CreativeTitleCTRScorer(),
]
