"""
内容生成引擎 — 调用 LLM 为电商营销场景生成真实可用的内容。

Skills 层调用入口，生成：短视频脚本、种草文案、直播话术、
SEO优化标题/描述、产品软文等。

每次调用消耗一次 LLM 请求，因此只在真正需要生成内容时调用。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── 专业文案专家系统提示 ──────────────────────────────────────────────────────

_SYSTEM_CONTENT_EXPERT = (
    "你是一位资深电商营销文案专家，精通抖音、小红书、淘宝、京东等平台的内容运营。"
    "你的文案风格接地气、有感染力、能促成转化。"
    "请直接输出内容本身，不要有额外说明、前言或注释。"
    "中文创作，语言自然，符合平台调性。"
)

_SYSTEM_ANALYSIS_EXPERT = (
    "你是专业的电商数据分析师，擅长从真实业务数据中提炼洞察。"
    "分析结论必须有数据支撑，给出具体可执行的建议。"
    "用中文回答，简洁有力。"
)

_SYSTEM_SERVICE_EXPERT = (
    "你是一位资深电商客服培训专家，精通淘宝/京东/拼多多/抖音的客服规范和话术技巧。"
    "你的话术专业、有温度、能有效解决问题并维护店铺评分。"
    "请直接输出可供客服直接使用的话术内容，中文，无需说明或前言。"
)

_SYSTEM_DESIGN_EXPERT = (
    "你是一位资深电商视觉设计顾问，精通淘宝/京东/拼多多/抖音等平台的视觉规范和转化优化。"
    "你的建议具体到尺寸、颜色值、文案、布局，可以直接交给设计师执行。"
    "请直接输出设计方案，中文，数据精确，无需额外说明。"
)

_SYSTEM_ENGINEERING_EXPERT = (
    "你是一位资深电商技术架构师，精通高并发电商系统的架构设计、性能优化、技术选型和故障排查。"
    "你的建议具体到技术方案、配置参数、代码实践，不给模糊建议。"
    "请直接输出技术方案，中文，精确专业，无需前言或额外说明。"
)


def _build_product_context(product_info: Dict[str, Any]) -> str:
    """将产品信息格式化为上下文段落。"""
    lines = []
    if product_info.get("name"):
        lines.append(f"商品名称：{product_info['name']}")
    if product_info.get("category"):
        lines.append(f"品类：{product_info['category']}")
    if product_info.get("selling_price") and product_info["selling_price"] > 0:
        lines.append(f"售价：¥{product_info['selling_price']}")
    if product_info.get("description"):
        desc = product_info["description"][:300].strip()
        if desc:
            lines.append(f"商品描述：{desc}")
    if product_info.get("selling_points"):
        sp = [s for s in product_info["selling_points"][:5] if s]
        if sp:
            lines.append("核心卖点：" + " | ".join(sp))
    if product_info.get("features"):
        ft = [f for f in product_info["features"][:4] if f]
        if ft:
            lines.append("产品特性：" + " | ".join(ft))
    return "\n".join(lines) if lines else "（未提供商品信息）"


async def _call(system: str, prompt: str, max_tokens: int = 2000, temperature: float = 0.85) -> Optional[str]:
    """内部 LLM 调用封装，失败返回 None。"""
    try:
        from src.llm_client import call_llm
        result = await call_llm(
            system_prompt=system,
            user_message=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=60,
        )
        return result
    except Exception as e:
        logger.warning("content_engine LLM call failed: %s", e)
        return None


# ═══════════════════════════════════════════════════════════════════════════
# 短视频脚本
# ═══════════════════════════════════════════════════════════════════════════

async def generate_video_script(
    product_info: Dict[str, Any],
    platform: str = "抖音",
    video_type: str = "种草",
    duration: int = 30,
    extra_requirements: str = "",
) -> Dict[str, Any]:
    """
    生成完整短视频脚本（含台词/画面/字幕说明）。

    Returns {script_text, hooks, highlights, error?}
    """
    prod_ctx = _build_product_context(product_info)
    pname = product_info.get("name", "商品")

    platform_style = {
        "抖音": "快节奏、强钩子、情绪化、口播+展示，首3秒必须抓人",
        "快手": "亲民接地气、方言感、强调价格实惠",
        "小红书": "精致生活感、真实体验、图文并茂，带温度",
        "B站": "详细测评、有深度、弹幕互动感强",
    }.get(platform, "通用短视频风格")

    type_focus = {
        "种草": "强调使用后的效果和情感共鸣，让观众产生'我也需要这个'的感觉",
        "测评": "客观评测优缺点，增强可信度，结尾给出购买建议",
        "教程": "手把手演示如何使用，解决痛点，突出便捷性",
        "剧情": "设置戏剧性场景，商品作为解决方案出现，共情为主",
        "开箱": "展示包装和第一印象，真实体验，惊喜感",
    }.get(video_type, "综合展示")

    prompt = f"""请为以下商品创作一个{duration}秒的{platform}短视频完整脚本。

【商品信息】
{prod_ctx}

【平台风格】{platform_style}

【视频类型】{video_type} — {type_focus}

{f'【额外要求】{extra_requirements}' if extra_requirements else ''}

请按以下格式输出完整脚本：

═══ 开场钩子（0-3秒）═══
[画面]：（描述画面内容）
[台词]：（完整台词文字）
[字幕]：（屏幕显示的字幕）

═══ 痛点/场景（3-10秒）═══
[画面]：
[台词]：
[动作]：

═══ 产品展示（10-{min(duration-10, 25)}秒）═══
[画面]：
[台词]：（包含至少2个核心卖点的自然融入）

═══ 效果/证言（{min(duration-10, 25)}-{duration-5}秒）═══
[画面]：
[台词]：

═══ 行动号召（最后5秒）═══
[画面]：
[台词]：（含价格/引导购买）

━━━ 拍摄提示 ━━━
• BGM推荐：（推荐背景音乐风格）
• 封面建议：（封面图设计要点）
• 发布标签：（推荐话题标签，3-5个）"""

    content = await _call(_SYSTEM_CONTENT_EXPERT, prompt, max_tokens=2000)

    if not content:
        return {
            "error": "内容生成暂时不可用",
            "商品": pname,
            "平台": platform,
        }

    return {
        "商品": pname,
        "平台": platform,
        "视频类型": video_type,
        "时长": f"{duration}秒",
        "完整脚本": content,
        "生成说明": "以上为AI生成脚本，可根据实际情况调整台词和画面",
    }


# ═══════════════════════════════════════════════════════════════════════════
# 种草文案（完整）
# ═══════════════════════════════════════════════════════════════════════════

async def generate_seeding_copy(
    product_info: Dict[str, Any],
    platform: str = "小红书",
    tone: str = "闺蜜分享",
    target_audience: str = "年轻女性",
    include_title: bool = True,
) -> Dict[str, Any]:
    """
    生成完整种草文案（标题 + 正文 + 标签）。
    """
    prod_ctx = _build_product_context(product_info)
    pname = product_info.get("name", "商品")
    price = product_info.get("selling_price", 0)

    tone_guide = {
        "闺蜜分享": "像跟好朋友分享好东西，语气热情、真实、带点惊叹，适当使用感叹号和emoji",
        "专业测评": "客观理性，有数据/细节支撑，展示专业度，结尾给出综合评分",
        "生活记录": "沉浸式记录使用场景，轻松随意，生活化语言，有画面感",
        "KOL推荐": "权威感+亲和力，分享使用心得，强调性价比和独特价值",
    }.get(tone, "真实体验分享")

    platform_note = {
        "小红书": "首行要抓眼球，多换行，善用emoji，图文比例2:1最佳，字数800-1200字",
        "微博": "简洁有力，250字以内，配图3-9张，话题标签2-3个",
        "微信公众号": "标题吸引点击，正文有小标题，字数1500-2500字，排版精美",
        "抖音评论区": "简短有力，50字内，引发共鸣和讨论",
    }.get(platform, "通用平台")

    prompt = f"""请为以下商品创作一篇完整的{platform}种草文案。

【商品信息】
{prod_ctx}

【目标受众】{target_audience}
【语气风格】{tone} — {tone_guide}
【平台要求】{platform_note}

请输出：

{f'━━━ 标题（5个选项，各风格不同）━━━' if include_title else ''}
{f'1. 情感共鸣型：' if include_title else ''}
{f'2. 好奇悬念型：' if include_title else ''}
{f'3. 干货干货型：' if include_title else ''}
{f'4. 场景代入型：' if include_title else ''}
{f'5. 踩坑警示型：' if include_title else ''}

━━━ 正文（完整文案）━━━
（直接输出完整可发布的正文内容，不需要说明这是正文）

━━━ 话题标签 ━━━
（输出8-12个相关话题标签，格式：#标签1 #标签2 ...）

━━━ 最佳发布时间 ━━━
（根据平台特点推荐）"""

    content = await _call(_SYSTEM_CONTENT_EXPERT, prompt, max_tokens=2500)

    if not content:
        return {"error": "内容生成暂时不可用", "商品": pname}

    return {
        "商品": pname,
        "平台": platform,
        "语气风格": tone,
        "目标受众": target_audience,
        "完整文案": content,
        "价格参考": f"¥{price}" if price else "价格未设置",
        "生成说明": "以上为AI生成文案，请根据实际使用体验润色调整",
    }


# ═══════════════════════════════════════════════════════════════════════════
# 直播脚本（完整话术）
# ═══════════════════════════════════════════════════════════════════════════

async def generate_live_script(
    product_list: List[Dict[str, Any]],
    duration_hours: float = 2,
    live_type: str = "日常带货",
    platform: str = "抖音",
) -> Dict[str, Any]:
    """
    生成完整直播话术脚本（含每个商品的完整台词）。
    """
    products_str = ""
    for i, p in enumerate(product_list[:8], 1):
        pname = p.get("name", f"商品{i}")
        pprice = p.get("selling_price", 0)
        sp = " | ".join(p.get("selling_points", [])[:2])
        products_str += f"{i}. {pname}（¥{pprice}）{' - ' + sp if sp else ''}\n"

    if not products_str:
        products_str = "（请提供商品列表）"

    total_mins = int(duration_hours * 60)

    prompt = f"""请为以下直播场景创作完整的{platform}直播脚本，包含每个阶段的完整台词。

【直播信息】
• 类型：{live_type}
• 时长：{duration_hours}小时（共{total_mins}分钟）
• 平台：{platform}
• 商品列表：
{products_str}

请输出以下完整脚本：

═══ 第一阶段：开场暖场（0-15分钟）═══
【台词完整版】：
（输出2-3分钟的完整开场话术，包括：问候粉丝、自我介绍、今日预告、引导关注/进粉丝团）

═══ 第二阶段：福利引流款（15-30分钟）═══
【台词完整版】：
（选第一个商品，输出完整的：引出→痛点→展示→卖点→催单→秒杀话术）

═══ 第三阶段：主推款循环（30分钟-{total_mins-30}分钟）═══
【每个商品的话术框架】：
（为每个主推商品生成一套完整的标准讲解话术模板，含：引出→3个卖点展示→价格对比→限时优惠→催单）

═══ 第四阶段：返场收尾（最后30分钟）═══
【台词完整版】：
（热卖款返场话术 + 下播感谢 + 下期预告）

━━━ 全程高频话术库 ━━━
（列出10-15个可随时插入的高频金句：催单/互动/营造氛围）"""

    content = await _call(_SYSTEM_CONTENT_EXPERT, prompt, max_tokens=3000, temperature=0.82)

    if not content:
        return {"error": "直播脚本生成暂时不可用"}

    return {
        "直播类型": live_type,
        "平台": platform,
        "时长": f"{duration_hours}小时",
        "商品数": len(product_list),
        "完整话术脚本": content,
        "使用说明": "可打印备用，根据实际情况即兴发挥，不需要逐字照读",
    }


# ═══════════════════════════════════════════════════════════════════════════
# SEO标题 + 商品描述
# ═══════════════════════════════════════════════════════════════════════════

async def generate_seo_titles(
    product_info: Dict[str, Any],
    platform: str = "淘宝",
    count: int = 5,
) -> Dict[str, Any]:
    """
    生成 SEO 优化标题（含分析）。
    """
    prod_ctx = _build_product_context(product_info)
    pname = product_info.get("name", "商品")

    char_limits = {
        "淘宝": 60, "天猫": 60, "京东": 54, "拼多多": 60, "抖音": 20, "小红书": 30,
    }
    limit = char_limits.get(platform, 60)

    platform_rules = {
        "淘宝": "关键词权重顺序：品类词>属性词>场景词>修饰词；前6个字决定搜索权重",
        "京东": "标准化格式：品牌+系列+规格+品类；SEO关键词布局靠前",
        "拼多多": "突出价格优势和高性价比，数字具体（买1送3、9.9元等），爆款标签",
        "抖音": "标题即钩子，前5个字决定完播率，强情绪词汇，疑问或对比结构",
    }.get(platform, "关键词前置，突出核心价值")

    prompt = f"""请为以下商品生成{platform}平台的{count}个SEO优化标题。

【商品信息】
{prod_ctx}

【平台规则】{platform_rules}
【字符限制】≤{limit}字符（注意：每个标题必须在{limit}字以内）

请按以下格式输出（直接给出标题内容，无需编号说明之外的额外文字）：

【营销型-1】（字符数）
标题内容...
→ 关键词策略：解释核心关键词布局思路

【营销型-2】（字符数）
标题内容...
→ 关键词策略：

【场景型】（字符数）
标题内容...
→ 关键词策略：

【功能型】（字符数）
标题内容...
→ 关键词策略：

【促销型】（字符数）
标题内容...
→ 关键词策略：

━━━ 综合推荐 ━━━
最推荐标题：（选出最优标题并说明理由）
A/B测试建议：（建议先测试哪两个）"""

    content = await _call(_SYSTEM_CONTENT_EXPERT, prompt, max_tokens=1500, temperature=0.75)

    if not content:
        return {"error": "标题生成暂时不可用", "商品": pname}

    return {
        "商品": pname,
        "平台": platform,
        "字符限制": f"≤{limit}字符",
        "生成标题": content,
        "优化说明": "建议A/B测试后选用点击率最高的版本",
    }


async def generate_product_description(
    product_info: Dict[str, Any],
    platform: str = "淘宝",
    style: str = "营销型",
) -> Dict[str, Any]:
    """
    生成 SEO 优化产品详情描述。
    """
    prod_ctx = _build_product_context(product_info)
    pname = product_info.get("name", "商品")

    style_guide = {
        "营销型": "情感驱动，强调价值主张，多用对比和场景，以行动召唤结尾",
        "信息型": "详细参数，结构化展示，侧重功能和规格，适合理性消费者",
        "故事型": "品牌故事+产品研发背景，建立情感连接，适合高客单价产品",
    }.get(style, "综合型")

    prompt = f"""请为以下商品创作一段完整的{platform}详情页文案描述。

【商品信息】
{prod_ctx}

【文案风格】{style} — {style_guide}

请按模块输出：

═══ 首屏核心卖点（100字以内，高度概括）═══

═══ 痛点场景引入（150-200字）═══
（描述目标用户的使用痛点，引发共鸣）

═══ 产品解决方案（200-300字）═══
（展示产品如何解决痛点，结合具体卖点）

═══ 产品亮点详解（每个亮点50-80字）═══
亮点一：
亮点二：
亮点三：

═══ 使用场景（100-150字）═══
（描述适合使用的场景）

═══ 购买承诺/保障（50字以内）═══

═══ 行动召唤（30字以内）═══"""

    content = await _call(_SYSTEM_CONTENT_EXPERT, prompt, max_tokens=2000, temperature=0.8)

    if not content:
        return {"error": "描述生成暂时不可用", "商品": pname}

    return {
        "商品": pname,
        "平台": platform,
        "文案风格": style,
        "详情页文案": content,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 产品软文
# ═══════════════════════════════════════════════════════════════════════════

async def generate_product_article(
    product_info: Dict[str, Any],
    article_type: str = "测评软文",
    word_count: int = 800,
    platform: str = "微信公众号",
) -> Dict[str, Any]:
    """
    生成完整产品软文/推广文章。
    """
    prod_ctx = _build_product_context(product_info)
    pname = product_info.get("name", "商品")

    type_guide = {
        "测评软文": "以真实测评者视角，客观分析优缺点，增强可信度，结尾自然引导购买",
        "种草长文": "沉浸式种草，大量生活场景描写，情感共鸣，适合情感决策型消费",
        "对比评测": "与竞品横向对比，数据说话，给出明确购买建议",
        "使用攻略": "详细使用指南，手把手教学，展示使用后效果，适合功能型产品",
        "品牌故事": "品牌文化和产品研发故事，建立信任感，适合新品牌",
    }.get(article_type, "综合推广文")

    prompt = f"""请创作一篇{word_count}字左右的{article_type}，发布于{platform}。

【商品信息】
{prod_ctx}

【文章风格】{type_guide}

要求：
- 字数约{word_count}字
- 结构清晰，有小标题
- 语言自然，不要过于广告感
- 有真实场景和细节描写
- 结尾自然引导关注/购买

请直接输出完整文章，包含标题。"""

    content = await _call(_SYSTEM_CONTENT_EXPERT, prompt, max_tokens=2500, temperature=0.85)

    if not content:
        return {"error": "软文生成暂时不可用", "商品": pname}

    return {
        "商品": pname,
        "文章类型": article_type,
        "目标平台": platform,
        "目标字数": f"{word_count}字",
        "完整文章": content,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 数据诊断洞察（LLM辅助分析）
# ═══════════════════════════════════════════════════════════════════════════

async def generate_data_insights(
    metrics_summary: Dict[str, Any],
    anomalies: List[Dict[str, Any]],
    role: str = "data",
) -> str:
    """
    基于真实指标数据，让 LLM 生成深度诊断洞察。

    Returns: 洞察文本
    """
    if not metrics_summary.get("has_data"):
        return ""

    # 构建数据摘要
    lines = [f"近{metrics_summary['days']}天店铺数据："]
    for platform, pdata in metrics_summary.get("platforms", {}).items():
        parts = []
        for k, v in pdata.items():
            if k == "gmv":
                parts.append(f"GMV ¥{v:,.0f}")
            elif k == "orders":
                parts.append(f"订单 {v:.0f}")
            elif k == "uv":
                parts.append(f"UV {v:.0f}")
            elif k == "conversion_rate":
                parts.append(f"转化率 {v*100:.2f}%")
            elif k == "refund_rate":
                parts.append(f"退款率 {v*100:.2f}%")
            elif k == "ad_spend":
                parts.append(f"广告 ¥{v:,.0f}")
        if parts:
            lines.append(f"  {platform}: " + " | ".join(parts))

    if anomalies:
        lines.append("\n异常指标：")
        for a in anomalies[:5]:
            sign = "+" if a["direction"] == "上升" else ""
            lines.append(f"  {a['platform_display']} {a['metric_display']} {sign}{a['change_pct']}%")

    data_text = "\n".join(lines)

    prompt = f"""基于以下真实店铺数据，请提供专业的经营诊断和改善建议：

{data_text}

请从以下角度分析（每个角度2-3句话，总共控制在300字以内）：
1. 整体经营健康度评估（给出评分和评级）
2. 最值得关注的1-2个问题（具体说明）
3. 优先级最高的3个改进动作（可立即执行的具体操作）

用中文，专业简洁，直接给结论。"""

    return await _call(_SYSTEM_ANALYSIS_EXPERT, prompt, max_tokens=600, temperature=0.3) or ""


# ═══════════════════════════════════════════════════════════════════════════
# 客服话术生成
# ═══════════════════════════════════════════════════════════════════════════

async def generate_service_response(
    ticket_type: str,
    issue_details: str,
    product_info: Dict[str, Any],
    platform: str = "淘宝",
    customer_emotion: str = "普通",
) -> Dict[str, Any]:
    """
    生成完整客服回复话术（含首次回复、安抚话术、解决方案、结单话术）。
    """
    prod_ctx = _build_product_context(product_info) if product_info else "（未关联具体商品）"
    pname = product_info.get("name", "商品") if product_info else "商品"

    emotion_guide = {
        "愤怒": "客户情绪激动，优先安抚，语气要真诚道歉，给予超出预期的解决方案",
        "焦虑": "客户担心和着急，快速确认问题并给明确时间节点承诺",
        "普通": "保持专业友好，高效解决问题",
        "满意": "维系好感，引导好评和复购",
    }.get(customer_emotion, "保持专业友好")

    platform_norms = {
        "淘宝": "淘宝客服规范：平均响应<3分钟，用语规范，避免承诺平台外交易",
        "京东": "京东客服规范：POP商家需遵守京东售后政策，7天无理由退货强制执行",
        "拼多多": "拼多多规范：快速响应（<1分钟），退款极为宽松，优先避免纠纷",
        "抖音": "抖音规范：直播间客服需快速响应，避免引导私下交易",
    }.get(platform, "通用电商客服规范")

    prompt = f"""请为以下客服场景生成完整的回复话术包。

【工单类型】{ticket_type}
【问题描述】{issue_details}
【客户情绪】{customer_emotion} — {emotion_guide}
【平台规范】{platform_norms}

【关联商品】
{prod_ctx}

请生成以下话术（每条话术都是可以直接复制使用的完整文本）：

═══ 第一步：首次回复（接单确认）═══
（30字以内，快速回复，让客户知道有人处理）

═══ 第二步：安抚/同理心话术 ═══
（50-80字，表达理解和歉意，稳定情绪）

═══ 第三步：核心解决方案话术 ═══
（100-150字，明确给出解决方案，包含具体时间节点和补偿方案）

═══ 第四步：跟进确认话术 ═══
（40-60字，确认客户是否满意）

═══ 第五步：结单邀评话术 ═══
（30-50字，自然引导好评，不要过于刻意）

━━━ 注意事项 ━━━
（列出2-3个该类工单的常见雷区和应对要点）"""

    content = await _call(_SYSTEM_SERVICE_EXPERT, prompt, max_tokens=1500, temperature=0.7)

    if not content:
        return {"error": "话术生成暂时不可用", "工单类型": ticket_type}

    return {
        "工单类型": ticket_type,
        "商品": pname,
        "平台": platform,
        "客户情绪": customer_emotion,
        "完整话术包": content,
        "使用说明": "以上为参考话术，请根据实际情况灵活调整",
    }


async def generate_faq_answers(
    product_info: Dict[str, Any],
    shop_policies: Dict[str, Any],
    questions: List[str],
    platform: str = "淘宝",
) -> Dict[str, Any]:
    """
    基于真实商品信息和店铺政策，生成标准 FAQ 问答库。
    """
    prod_ctx = _build_product_context(product_info) if product_info else "（未关联具体商品）"
    pname = product_info.get("name", "商品") if product_info else "商品"
    price = product_info.get("selling_price", 0) if product_info else 0

    policy_text = ""
    if shop_policies:
        for k, v in shop_policies.items():
            policy_text += f"• {k}：{v}\n"
    else:
        policy_text = "（使用平台默认政策）"

    q_list = "\n".join([f"{i+1}. {q}" for i, q in enumerate(questions[:15])])

    prompt = f"""请基于以下商品信息和店铺政策，为这些常见问题生成标准客服回答。

【商品信息】
{prod_ctx}
{'售价：¥' + str(price) if price else ''}

【店铺政策】
{policy_text}

【需要回答的问题列表】
{q_list}

请为每个问题生成：
1. 简短版回答（适合快捷回复，30字以内）
2. 详细版回答（完整解释，80-120字）

格式：
Q1: [问题]
简短版：[回答]
详细版：[回答]

Q2: [问题]
...（以此类推）

━━━ 高频风险问题预防话术 ━━━
（列出2-3个容易引发差评的场景和预防性话术）"""

    content = await _call(_SYSTEM_SERVICE_EXPERT, prompt, max_tokens=2500, temperature=0.65)

    if not content:
        return {"error": "FAQ生成暂时不可用", "商品": pname}

    return {
        "商品": pname,
        "平台": platform,
        "覆盖问题数": len(questions),
        "FAQ话术库": content,
        "使用建议": "导入客服系统快捷回复库，定期根据新问题更新",
    }


async def generate_escalation_script(
    issue_type: str,
    escalation_level: int,
    product_info: Dict[str, Any],
    customer_history: str = "",
) -> Dict[str, Any]:
    """
    生成升级处理脚本（主管接管/赔偿方案/纠纷处理）。
    """
    pname = product_info.get("name", "商品") if product_info else "商品"
    price = product_info.get("selling_price", 0) if product_info else 0

    level_context = {
        1: "一线客服可处理，标准流程",
        2: "需主管介入，给出超出常规的补偿方案",
        3: "纠纷/投诉/差评威胁，需要最高优先级处理",
    }.get(escalation_level, "一线处理")

    prompt = f"""请生成以下升级处理场景的完整处理脚本。

【问题类型】{issue_type}
【升级等级】Level {escalation_level} — {level_context}
【关联商品】{pname}（{'¥' + str(price) if price else '价格未知'}）
{f'【客户历史】{customer_history}' if customer_history else ''}

请生成完整的升级处理方案，包含：

═══ 主管接管开场白 ═══
（自我介绍+表达重视，40-60字）

═══ 核心补偿/解决方案 ═══
（根据升级等级给出具体的补偿方案，包含金额/时效/操作步骤）

═══ 纠纷/差评预防话术 ═══
（如何在解决问题的同时避免纠纷申请或差评）

═══ 内部处理笔记模板 ═══
（供客服填写的工单记录模板，含：问题描述/解决方案/赔偿金额/跟进时间）

━━━ 该场景注意事项 ━━━
（2-3条关键提醒）"""

    content = await _call(_SYSTEM_SERVICE_EXPERT, prompt, max_tokens=1500, temperature=0.7)

    if not content:
        return {"error": "升级处理脚本生成暂时不可用"}

    return {
        "问题类型": issue_type,
        "升级等级": f"Level {escalation_level}",
        "商品": pname,
        "完整处理脚本": content,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 设计方案生成
# ═══════════════════════════════════════════════════════════════════════════

async def generate_design_brief(
    design_type: str,
    product_info: Dict[str, Any],
    platform: str = "淘宝",
    style: str = "简约现代",
    extra_requirements: str = "",
    search_context: str = "",
) -> Dict[str, Any]:
    """
    生成完整设计执行方案（含尺寸/色值/文案/构图/素材清单）。
    """
    prod_ctx = _build_product_context(product_info) if product_info else "（未关联具体商品）"
    pname = product_info.get("name", "商品") if product_info else "商品"
    category = product_info.get("category", "") if product_info else ""
    price = product_info.get("selling_price", 0) if product_info else 0
    selling_points = product_info.get("selling_points", []) if product_info else []

    platform_specs = {
        "淘宝": "主图800×800px，详情页宽度750px，首图白底，视频750×750px",
        "京东": "主图800×800px，详情页宽度750px，必须有3C认证/白底图",
        "拼多多": "主图800×800px，详情页宽度480px，强调价格，禁止夸大宣传词",
        "抖音": "竖版9:16，1080×1920px，封面图1:1，短视频时长15-60秒",
        "小红书": "1:1或3:4，1080×1080px，生活感强，避免明显广告感",
    }.get(platform, "通用电商规格")

    design_type_guide = {
        "主图": "提高点击率为核心目标，首图突出核心卖点，场景图展示使用场景，白底图满足平台要求",
        "详情页": "引导购买为核心目标，遵循：首屏钩子→痛点→解决方案→卖点详解→使用场景→信任背书→行动号召",
        "活动海报": "快速传达活动信息为目标，层次清晰：主标题→副标题→商品→优惠信息→行动号召",
        "色彩方案": "建立品牌识别度，基于商品品类心理和目标受众制定主色/辅色/点缀色体系",
        "素材规格": "提供各平台完整素材尺寸清单和技术规格，减少返工",
    }.get(design_type, "通用设计")

    price_tier = "高端" if price > 500 else ("中端" if price > 100 else "亲民") if price else "未知"

    design_trend_section = (
        f"\n\n【实时设计趋势参考（请优先采信这些当前市场数据）】\n{search_context}"
        "\n注：以上为当前爆款设计的实时数据，请据此制定视觉方向，而非依赖通用经验。"
    ) if search_context else ""

    prompt = f"""请为以下商品生成一份完整的{design_type}设计执行方案。

【商品信息】
{prod_ctx}
价格定位：{price_tier}（¥{price}）

【设计目标】{design_type_guide}
【目标平台】{platform}
【平台规格】{platform_specs}
【设计风格】{style}
{f'【额外要求】{extra_requirements}' if extra_requirements else ''}{design_trend_section}

请生成完整设计方案：

═══ 视觉定调 ═══
• 主色调：（给出具体HEX色值 #XXXXXX，并说明选色理由）
• 辅助色：（2-3个，给出HEX值）
• 字体选择：（中文/英文字体，字号规范）
• 整体氛围：（2-3个关键词描述视觉感受）

═══ 构图方案 ═══
（按图片数量/页面模块，逐一描述每张图/每个模块的构图方式、主角位置、文案层级）

═══ 文案内容 ═══
（直接给出每张图/每个模块应该写的具体文案，包含：主标题/副标题/卖点文案/按钮文案）

═══ 素材清单 ═══
（需要拍摄或准备的图片/视频素材，逐条列出：类型/数量/拍摄要求）

═══ 技术规格 ═══
（尺寸/分辨率/格式/文件大小限制，按输出物逐一列出）

━━━ 设计重点提醒 ━━━
（2-3条最容易做错的地方）"""

    content = await _call(_SYSTEM_DESIGN_EXPERT, prompt, max_tokens=2500, temperature=0.75)

    if not content:
        return {"error": "设计方案生成暂时不可用", "商品": pname}

    return {
        "商品": pname,
        "设计类型": design_type,
        "目标平台": platform,
        "设计风格": style,
        "完整设计方案": content,
        "使用说明": "将此方案发给设计师作为执行依据，所有尺寸和文案均可直接使用",
    }


# ═══════════════════════════════════════════════════════════════════════════
# 技术方案生成（工程师 Agent）
# ═══════════════════════════════════════════════════════════════════════════

async def generate_architecture_review(
    tech_stack: str,
    daily_orders: int,
    pain_points: List[str],
    current_metrics: Dict[str, Any],
) -> Dict[str, Any]:
    """生成系统架构评审报告和优化方案。"""
    metrics_text = ""
    if current_metrics:
        for k, v in current_metrics.items():
            metrics_text += f"• {k}：{v}\n"

    pain_text = "\n".join([f"• {p}" for p in pain_points]) if pain_points else "• （未指定）"

    prompt = f"""请为以下电商系统进行架构评审，并给出具体改造方案。

【当前技术栈】{tech_stack or "未提供（按通用电商架构评估）"}
【日均订单量】{daily_orders:,} 单/天（峰值约 {daily_orders * 5:,} 单/小时）
【主要痛点】
{pain_text}

【当前系统指标】
{metrics_text or "（未提供）"}

请输出完整架构评审报告：

═══ 当前架构评分 ═══
（从可用性/性能/扩展性/安全性/成本 5个维度打分，每项1-10分，给出具体扣分理由）

═══ 核心风险识别 ═══
（按严重程度列出3-5个最紧迫的架构风险，每条说明：问题/影响/触发条件）

═══ 优先级改造方案 ═══
（分P0/P1/P2三级，每条包含：改造目标/具体技术方案/预期效果/实施成本）

═══ 大促备战清单 ═══
（针对{daily_orders * 10:,}单/天的峰值场景，列出必须完成的技术准备项）

═══ 技术债清理计划 ═══
（分3-6个月/6-12个月/12个月以上三个阶段，逐步架构升级路径）"""

    content = await _call(_SYSTEM_ENGINEERING_EXPERT, prompt, max_tokens=2500, temperature=0.5)

    if not content:
        return {"error": "架构评审生成暂时不可用"}

    return {
        "日均订单": daily_orders,
        "技术栈": tech_stack or "通用架构",
        "完整评审报告": content,
        "评审说明": "基于提供的信息生成，建议结合实际系统监控数据进行验证",
    }


async def generate_bug_diagnosis(
    error_type: str,
    error_message: str,
    tech_stack: str,
    frequency: str,
    context: str = "",
    search_context: str = "",
) -> Dict[str, Any]:
    """生成完整 Bug 诊断方案（含根因分析/排查步骤/修复代码/预防措施）。"""
    bug_reference_section = (
        f"\n\n【已知解决方案参考（来自实时技术资料）】\n{search_context}"
        "\n注：以上为当前最新的技术解决方案，优先采用上述方法，若与通用做法冲突以上述为准。"
    ) if search_context else ""

    prompt = f"""请为以下 Bug/异常进行深度诊断，给出完整排查和修复方案。

【错误类型】{error_type}
【错误信息】{error_message or "（未提供具体错误信息）"}
【技术栈】{tech_stack or "通用电商系统"}
【发生频率】{frequency}
{f'【上下文】{context}' if context else ''}{bug_reference_section}

请输出完整诊断报告：

═══ 根因分析 ═══
（列出3-5个最可能的根本原因，每条给出出现概率评估）

═══ 快速诊断步骤 ═══
（10步以内可以定位问题的排查流程，每步包含：执行命令/查看位置/预期结果）

═══ 常见修复方案 ═══
（针对最可能的2-3个根因，给出具体的代码修复方案或配置修改，包含代码示例）

═══ 临时止血方案 ═══
（如果无法立即修复，有什么临时降级/熔断/限流措施可以降低影响）

═══ 预防措施 ═══
（如何避免同类问题再次发生：监控告警/代码规范/测试用例）

━━━ 相关工具命令 ━━━
（给出直接可用的诊断命令，如 grep/awk/curl/redis-cli 等）"""

    content = await _call(_SYSTEM_ENGINEERING_EXPERT, prompt, max_tokens=2500, temperature=0.45)

    if not content:
        return {"error": "Bug诊断方案生成暂时不可用"}

    return {
        "错误类型": error_type,
        "发生频率": frequency,
        "完整诊断方案": content,
    }


async def generate_performance_plan(
    bottleneck: str,
    current_qps: float,
    target_qps: float,
    tech_stack: str,
    metrics: Dict[str, Any],
) -> Dict[str, Any]:
    """生成具体的性能优化实施方案（含代码/配置/SQL示例）。"""
    metrics_text = "\n".join([f"• {k}：{v}" for k, v in metrics.items()]) if metrics else "（未提供）"
    gap_ratio = round(target_qps / max(current_qps, 1), 1)

    prompt = f"""请为以下性能问题生成完整的优化方案。

【性能瓶颈】{bottleneck}
【当前QPS/TPS】{current_qps}
【目标QPS/TPS】{target_qps}（需提升 {gap_ratio}x）
【技术栈】{tech_stack or "通用Web应用"}
【当前系统指标】
{metrics_text}

请生成完整性能优化方案：

═══ 瓶颈根因定位 ═══
（针对"{bottleneck}"，分析最可能的根本原因，给出具体诊断命令）

═══ 优化方案（按ROI排序）═══
（列出5-8个优化措施，每条包含：
  - 优化方向
  - 具体实现（含代码/配置示例）
  - 预期提升幅度
  - 实施工作量）

═══ 分阶段实施计划 ═══
第一阶段（1周内，收益最大的2-3项）：
第二阶段（1个月内）：
第三阶段（持续优化）：

═══ 压测方案 ═══
（给出达到 {target_qps} QPS 的压测脚本框架，用 locust 或 k6）

━━━ 性能监控指标 ━━━
（建议监控的关键指标和告警阈值）"""

    content = await _call(_SYSTEM_ENGINEERING_EXPERT, prompt, max_tokens=2500, temperature=0.45)

    if not content:
        return {"error": "性能优化方案生成暂时不可用"}

    return {
        "瓶颈类型": bottleneck,
        "提升目标": f"{current_qps} → {target_qps} QPS（{gap_ratio}x）",
        "完整优化方案": content,
    }


async def generate_tech_selection(
    domain: str,
    requirements: List[str],
    constraints: List[str],
    team_size: int,
    current_stack: str,
    search_context: str = "",
) -> Dict[str, Any]:
    """生成技术选型深度分析报告（含真实对比数据和迁移成本评估）。"""
    req_text = "\n".join([f"• {r}" for r in requirements]) if requirements else "（通用需求）"
    con_text = "\n".join([f"• {c}" for c in constraints]) if constraints else "（无特殊约束）"

    tech_intel_section = (
        f"\n\n【实时技术情报（最新开源生态数据，选型时请优先参考）】\n{search_context}"
        "\n注：以上为当前实时技术对比数据，请以上述数据更新您的评估维度。"
    ) if search_context else ""

    prompt = f"""请为以下场景生成技术选型分析报告，包含真实的性能数据对比和迁移成本评估。

【选型场景】{domain}
【团队规模】{team_size}人，现有技术栈：{current_stack or "未提供"}

【核心需求】
{req_text}

【约束条件】
{con_text}{tech_intel_section}

请输出完整选型报告：

═══ 候选方案对比矩阵 ═══
（列出3-5个主流候选方案，从以下维度对比：
  性能基准/社区活跃度/学习曲线/运维成本/License/与现有栈兼容性/国内使用情况
  给出真实的基准测试数据引用，如 TechEmpower/官方 benchmark）

═══ 深度评测 TOP2 ═══
（对排名前2的方案，分别给出：
  优势/劣势/适用场景/生产环境案例/踩坑记录/配置示例）

═══ 最终推荐 ═══
（明确给出推荐方案及理由，针对该团队的具体情况分析）

═══ 迁移方案 ═══
（从现有 {current_stack or "当前技术栈"} 迁移的路径：
  迁移策略（大爆炸/绞杀者模式/蓝绿）/预估工期/风险点/回滚方案）

═══ POC验证清单 ═══
（技术选型前需要验证的5-8个关键技术点，及验证方法）"""

    content = await _call(_SYSTEM_ENGINEERING_EXPERT, prompt, max_tokens=2500, temperature=0.5)

    if not content:
        return {"error": "技术选型报告生成暂时不可用"}

    return {
        "选型场景": domain,
        "团队规模": team_size,
        "完整选型报告": content,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 运营策略生成（运营师 Agent）
# ═══════════════════════════════════════════════════════════════════════════

_SYSTEM_OPS_EXPERT = (
    "你是一位资深电商运营专家，有10年以上淘宝/京东/拼多多/抖音平台实战运营经验。"
    "你的方案基于真实数据，具体到执行步骤、KPI目标、时间节点，能直接落地执行。"
    "不给泛泛而谈的建议，每条输出必须包含：做什么 + 怎么做 + 效果预期。"
    "中文输出，专业实战，不废话。"
)

_SYSTEM_FINANCE_EXPERT = (
    "你是一位资深电商财务分析师，精通电商利润核算、现金流管理、税务筹划。"
    "你的分析有数据支撑，给出具体可执行的财务改善建议。"
    "用中文，专业简洁，结论先行，配上具体数字。"
)


async def generate_listing_copy(
    product_info: Dict[str, Any],
    platform: str = "淘宝",
    competitors: Optional[List[str]] = None,
    search_context: str = "",
) -> Dict[str, Any]:
    """
    生成真实的商品上架文案（标题/五点描述/关键词/A+内容）。
    """
    prod_ctx = _build_product_context(product_info)
    pname = product_info.get("name", "商品")
    price = product_info.get("selling_price", 0)

    char_limit = {"淘宝": 60, "京东": 54, "拼多多": 60, "抖音": 20}.get(platform, 60)

    platform_rules = {
        "淘宝": "核心词前置，属性词+场景词+修饰词；前6个字决定搜索权重，禁止虚假宣传词",
        "京东": "品牌+系列+规格+品类标准格式；需含3C认证/官方认可词；强调正品",
        "拼多多": "突出价格和数量优势，买X送X，数字具体（¥9.9/买1送3）；禁用最高级词",
        "抖音": "前5字是钩子，强情绪词汇或疑问句，诱导停留；主推视觉化卖点",
    }.get(platform, "关键词前置，突出核心价值")

    competitor_ctx = ""
    if competitors:
        competitor_ctx = f"\n【参考竞品标题】\n" + "\n".join([f"• {c}" for c in competitors[:5]])

    search_section_listing = (
        f"\n\n【实时竞品/关键词参考（请优先采信这些实时数据）】\n{search_context}"
        "\n注：以上为当前市场实际数据，请据此优化关键词选择和差异化卖点。"
    ) if search_context else ""
    prompt = f"""请为以下商品生成完整的{platform}平台上架文案。

【商品信息】
{prod_ctx}

【平台规则】{platform_rules}
【字符限制】标题≤{char_limit}字符
{competitor_ctx}{search_section_listing}

请输出：

━━━ 商品标题（5个版本，风格各异）━━━
【版本1-流量型】（字符数）：[突出搜索核心词，流量优先]
【版本2-卖点型】（字符数）：[突出最强差异化卖点]
【版本3-场景型】（字符数）：[描述使用场景，精准触达需求]
【版本4-活动型】（字符数）：[含促销信息，适合大促期使用]
【版本5-品牌型】（字符数）：[适合品牌升级阶段使用]
→ 推荐版本：（选最优版本并说明理由）

━━━ 商品卖点五行描述 ━━━
（每行30-80字，从不同角度展示价值，可直接用于详情页五大亮点）
1.
2.
3.
4.
5.

━━━ 核心关键词矩阵 ━━━
• 核心词（高搜索量，必须布局）：
• 属性词（精准描述，提升转化）：
• 长尾词（竞争低，精准流量）：
• 场景词（触达特定需求）：
• 促销词（活动期间使用）：

━━━ 买家常见问题预设回答 ━━━
（3-5个高频问题的简短标准答复，可放入商品详情）"""

    content = await _call(_SYSTEM_CONTENT_EXPERT, prompt, max_tokens=2000, temperature=0.75)

    if not content:
        return {"error": "上架文案生成暂时不可用", "商品": pname}

    return {
        "商品": pname,
        "平台": platform,
        "字符限制": f"≤{char_limit}字符",
        "完整上架文案": content,
        "使用说明": "直接复制标题版本，关键词可导入平台推广工具",
    }


async def generate_channel_mix(
    product_type: str,
    budget: float,
    real_metrics: Optional[Dict[str, Any]] = None,
    target_audience: str = "",
    search_context: str = "",
) -> Dict[str, Any]:
    """
    基于真实数据生成个性化渠道策略和内容计划。
    """
    metrics_ctx = ""
    if real_metrics and real_metrics.get("has_data"):
        platforms = real_metrics.get("platforms", {})
        metrics_ctx = "【真实平台数据（近30天）】\n"
        for pname, pdata in platforms.items():
            gmv = pdata.get("gmv", 0)
            roi = pdata.get("ad_roi", 0)
            conv = pdata.get("conversion_rate", 0)
            metrics_ctx += f"• {pname}: GMV ¥{gmv:,.0f} | 广告ROI {roi:.1f} | 转化率 {conv*100:.2f}%\n"

    channel_search = (
        f"\n\n【实时平台算法/渠道动态（据此调整预算分配）】\n{search_context}"
        "\n注：平台算法和流量分发规则频繁变化，以上实时数据反映当前状态，预算分配优先级应以此为据。"
    ) if search_context else ""
    prompt = f"""请为以下电商场景生成一套个性化的多渠道营销策略。

【商品类型】{product_type}
【月预算】¥{budget:,.0f}
【目标人群】{target_audience or '未指定，请根据品类推断'}
{metrics_ctx}{channel_search}

请生成完整的渠道策略方案：

═══ 渠道优先级排序 ═══
（基于商品特性和{'真实ROI数据' if metrics_ctx else '行业经验'}，对各平台进行优先级排序，说明排序理由）

═══ 预算分配方案 ═══
（逐一列出每个渠道：
  • 预算金额和占比
  • 主要投放形式（搜索/信息流/直播/KOL等）
  • 预期ROI区间
  • 关键成功因素）

═══ 内容生产计划 ═══
（每个渠道需要什么内容，频率是多少，需要配合什么资源）

═══ 月度节奏安排 ═══
第1周：（重点做什么）
第2周：（重点做什么）
第3周：（重点做什么）
第4周：（重点做什么）

═══ 风险控制 ═══
（哪个渠道ROI最不确定？如果某渠道表现不佳，如何快速切换预算？）

━━━ 关键执行提醒 ━━━
（2-3条最容易踩坑的地方）"""

    content = await _call(_SYSTEM_OPS_EXPERT, prompt, max_tokens=2000, temperature=0.72)

    if not content:
        return {"error": "渠道策略生成暂时不可用"}

    return {
        "商品类型": product_type,
        "月预算": f"¥{budget:,.0f}",
        "目标人群": target_audience or "根据品类自动推断",
        "数据依据": "真实平台数据" if metrics_ctx else "行业经验估算",
        "完整渠道策略": content,
    }


async def generate_ops_execution_plan(
    goal: str,
    stage: str,
    real_metrics: Optional[Dict[str, Any]] = None,
    product_info: Optional[Dict[str, Any]] = None,
    search_context: str = "",
) -> Dict[str, Any]:
    """
    基于真实数据生成个性化运营执行计划。
    """
    metrics_ctx = ""
    if real_metrics and real_metrics.get("has_data"):
        totals = real_metrics.get("totals", {})
        metrics_ctx = f"""【当前真实数据（近30天）】
• GMV: ¥{totals.get('gmv', 0):,.0f}
• 订单数: {totals.get('orders', 0):.0f}
• UV: {totals.get('uv', 0):.0f}
• 转化率: {totals.get('conversion_rate', 0)*100:.2f}%
• 广告ROI: {totals.get('ad_roi', 0):.1f}
"""

    prod_ctx = ""
    if product_info:
        prod_ctx = f"【主推商品】{product_info.get('name', '')}（¥{product_info.get('selling_price', 0)}）"

    stage_context = {
        "冷启动": "从0开始建立流量，首要任务是跑通流量闭环和找到核心用户群",
        "成长期": "已有基础流量，重点是规模化复制和多渠道拓展",
        "成熟期": "稳定增长，重点是精细化运营和提升利润率",
        "衰退期": "销量下滑，重点是清库存和寻找新增长点",
    }.get(stage, "按当前数据判断所处阶段并制定策略")

    exec_search = (
        f"\n\n【实时行业运营案例（以此为执行计划的外部参照）】\n{search_context}"
        "\n注：运营方法论需结合当前市场实际，请以上实时数据影响任务优先级和执行细节设定。"
    ) if search_context else ""

    prompt = f"""请为以下电商运营场景制定一份可立即执行的运营计划。

【运营目标】{goal}
【当前阶段】{stage} — {stage_context}
{metrics_ctx}
{prod_ctx}{exec_search}

请生成完整可执行运营方案：

═══ 现状诊断 ═══
（{'基于真实数据' if metrics_ctx else '基于阶段特征'}，诊断当前最核心的问题是什么，1-2句话）

═══ 30天执行路径 ═══
第1-7天（基础工作）：
→ 具体任务1：（负责人/完成标准/工具）
→ 具体任务2：
→ 具体任务3：

第8-14天（启动测试）：
→ 具体任务1：
→ 具体任务2：

第15-21天（优化放大）：
→ 具体任务1：
→ 具体任务2：

第22-30天（冲刺收割）：
→ 具体任务1：
→ 具体任务2：

═══ 核心KPI体系 ═══
（针对"{goal}"，列出5-7个最关键的过程指标和结果指标，含目标值和衡量频率）

═══ 风险预案 ═══
（列出3个最可能遇到的问题，每个给出应对预案）

═══ 资源需求清单 ═══
（人力/工具/预算/物料，逐项列出必需的资源）"""

    content = await _call(_SYSTEM_OPS_EXPERT, prompt, max_tokens=2500, temperature=0.7)

    if not content:
        return {"error": "运营计划生成暂时不可用"}

    return {
        "运营目标": goal,
        "当前阶段": stage,
        "数据依据": "真实数据" if metrics_ctx else "阶段经验",
        "完整执行方案": content,
        "使用说明": "可直接拆解为每日任务卡，导入项目管理工具",
    }


async def generate_assortment_strategy(
    category: str,
    budget: float,
    real_metrics: Optional[Dict[str, Any]] = None,
    existing_products: Optional[List[Dict]] = None,
    search_context: str = "",
) -> Dict[str, Any]:
    """
    基于真实销售数据生成选品策略和商品组合方案。
    """
    metrics_ctx = ""
    if real_metrics and real_metrics.get("has_data"):
        totals = real_metrics.get("totals", {})
        aov = totals.get("avg_order_value", 0)
        metrics_ctx = f"【真实店铺数据】客单价 ¥{aov:.0f} | 转化率 {totals.get('conversion_rate',0)*100:.2f}% | GMV ¥{totals.get('gmv',0):,.0f}"

    products_ctx = ""
    if existing_products:
        top = existing_products[:5]
        products_ctx = "【现有主要商品】\n" + "\n".join(
            [f"• {p.get('name','')} ¥{p.get('selling_price',0)} ({p.get('lifecycle_status','')})" for p in top]
        )

    trend_search = (
        f"\n\n【实时选品趋势（据此识别当前爆款方向）】\n{search_context}"
        "\n注：选品成败七分靠趋势，请从以上实时数据中提取当前热销品类方向，作为商品组合决策的核心依据。"
    ) if search_context else ""
    prompt = f"""请为以下电商场景制定选品策略和商品组合优化方案。

【商品类目】{category}
【选品预算】¥{budget:,.0f}
{metrics_ctx}
{products_ctx}{trend_search}

请生成完整选品策略：

═══ 市场机会分析 ═══
（{category}类目当前的市场趋势、核心消费需求、竞争格局判断）

═══ 商品组合策略 ═══
（根据真实数据/类目特性，制定个性化的商品角色分配，不一定是固定比例）

各角色商品定义：
① 引流款（低价爆款）：
  - 定价策略：（给出具体价格区间）
  - 选品标准：（至少3个具体筛选标准）
  - 预算分配：（金额和占比）
  - 选品方向：（具体品类方向3-5个）

② 利润款（核心盈利）：
  - 定价策略：
  - 选品标准：
  - 预算分配：
  - 选品方向：

③ 形象款/战略款：
  - 定价策略：
  - 选品标准：
  - 预算分配：
  - 选品方向：

═══ 选品执行标准 ═══
（具体的数据化筛选标准，含：月搜索量下限/竞品数量区间/毛利率要求/评分要求/生命周期判断）

═══ 测品验证方法 ═══
（如何用最小成本验证新品是否值得加码，包含：测试周期/测试预算/判断指标/决策标准）

━━━ 选品雷区 ━━━
（针对{category}类目，列出3-5个常见的选品陷阱）"""

    content = await _call(_SYSTEM_OPS_EXPERT, prompt, max_tokens=2000, temperature=0.72)

    if not content:
        return {"error": "选品策略生成暂时不可用"}

    return {
        "商品类目": category,
        "选品预算": f"¥{budget:,.0f}",
        "数据依据": "真实数据辅助" if metrics_ctx else "类目经验",
        "完整选品策略": content,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Web / SEO 方案生成
# ═══════════════════════════════════════════════════════════════════════════

_SYSTEM_SEO_EXPERT = (
    "你是一位资深电商SEO和店铺运营专家，精通淘宝/京东/拼多多/抖音的搜索算法和流量机制。"
    "你的优化方案具体到关键词、代码、内容，每条建议都有执行标准和预期效果。"
    "中文输出，实操性强，直接可用。"
)


async def generate_seo_audit(
    product_info: Dict[str, Any],
    keywords: List[str],
    platform: str = "淘宝",
    current_rank: int = 0,
    real_metrics: Optional[Dict[str, Any]] = None,
    search_context: str = "",
) -> Dict[str, Any]:
    """
    生成完整的SEO优化方案（含诊断/关键词策略/内容改写建议）。
    """
    prod_ctx = _build_product_context(product_info)
    pname = product_info.get("name", "商品")

    metrics_ctx = ""
    if real_metrics and real_metrics.get("has_data"):
        plat_map = {"淘宝": "taobao", "京东": "jd", "拼多多": "pdd", "抖音": "douyin"}
        plat_key = plat_map.get(platform, "")
        pdata = real_metrics.get("platforms", {}).get(plat_key, {})
        if pdata:
            metrics_ctx = f"【真实数据】UV {pdata.get('uv',0):.0f} | 转化率 {pdata.get('conversion_rate',0)*100:.2f}% | 广告ROI {pdata.get('ad_roi',0):.1f}"

    platform_algo = {
        "淘宝": "淘宝搜索算法：商品质量分×流量分。质量分=转化率+DSR+退款率+点击率；流量分=关键词匹配度+出价+历史成交",
        "京东": "京东搜索：转化率权重最高，其次是评价数量和质量，品牌加权，3C类目需要认证加持",
        "拼多多": "拼多多：以销量和价格为核心，CTR极其重要，主图决定流量，DSR影响搜索权重",
        "抖音": "抖音电商：完播率>互动率>转化率，内容质量决定流量分发，商品体验分影响搜索排名",
    }.get(platform, "通用电商搜索算法")

    kw_list = "、".join(keywords[:10]) if keywords else "（未指定关键词）"

    seo_search = (
        f"\n\n【实时关键词竞争与算法动态（请优先依据这些数据）】\n{search_context}"
        "\n注：关键词竞争度和算法权重持续变化，请从以上实时数据中提取具体搜索量和竞争度数字，替代估算值。"
    ) if search_context else ""
    prompt = f"""请为以下商品进行完整的{platform}SEO诊断和优化方案输出。

【商品信息】
{prod_ctx}

【目标关键词】{kw_list}
【当前排名】{f'第{current_rank}名' if current_rank > 0 else '未知/未入排名'}
【平台算法】{platform_algo}
{metrics_ctx}{seo_search}

请输出完整SEO优化方案：

═══ 关键词策略 ═══
核心词（1-2个，高搜索量主战场）：（具体词+月搜索量估算+竞争度评估）
精准词（3-5个，高转化长尾词）：（具体词，说明为何精准）
布局词（5-8个，内容矩阵用词）：（覆盖不同搜索场景）
蓝海词（2-3个，竞争低机会大）：（说明发现依据）

═══ 标题/商品名优化 ═══
（给出1个最优化的完整标题，附上每个词的选择理由和布局逻辑）

═══ 内容SEO优化清单 ═══
（逐条列出每个可优化的内容元素：主图/详情页/属性/规格/评价引导，每条说明优化方向和预期效果）

═══ 技术SEO检查项 ═══
（平台规则合规检查，含：图片规范/视频要求/属性完整度/类目精准度）

═══ 提排路径 ═══
（从当前状态到目标排名的具体步骤，含时间预期）

━━━ 数据监控指标 ━━━
（哪些指标每天必须看，正常值范围是多少）"""

    content = await _call(_SYSTEM_SEO_EXPERT, prompt, max_tokens=2000, temperature=0.65)

    if not content:
        return {"error": "SEO优化方案生成暂时不可用", "商品": pname}

    return {
        "商品": pname,
        "平台": platform,
        "目标关键词": keywords,
        "完整SEO方案": content,
        "使用说明": "关键词矩阵可直接导入推广工具，内容优化清单按优先级逐项执行",
    }


async def generate_store_design_brief(
    store_type: str,
    category: str,
    platform: str,
    brand_tone: str,
    real_metrics: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    生成完整的店铺装修设计执行方案。
    """
    metrics_ctx = ""
    if real_metrics and real_metrics.get("has_data"):
        totals = real_metrics.get("totals", {})
        metrics_ctx = f"【店铺真实数据】月GMV ¥{totals.get('gmv',0):,.0f} | 转化率 {totals.get('conversion_rate',0)*100:.2f}%"

    prompt = f"""请为以下店铺生成完整的{platform}装修设计执行方案。

【店铺类型】{store_type}
【主营类目】{category}
【品牌调性】{brand_tone}
【目标平台】{platform}
{metrics_ctx}

请生成完整可交付的设计方案：

═══ 视觉定位 ═══
（基于{brand_tone}调性和{category}类目心理，确定：主色系HEX值/字体选择/整体视觉风格3个关键词）

═══ 店铺首页结构设计 ═══
（针对{platform}平台规格，逐模块设计：
  模块名称 | 建议尺寸 | 设计重点 | 核心文案方向 | 图片/视频素材需求）

═══ 各功能页设计方向 ═══
商品详情页模板：（统一的模板结构，包含几个模块，每模块设计要点）
活动页面：（大促/活动页面的设计规律）
分类页：（商品列表页的展示逻辑）

═══ 交互与用户体验 ═══
（移动端优化重点/关键转化节点的体验优化/引导复购的设计细节）

═══ 素材制作清单 ═══
（需要准备的所有设计物料，含格式/尺寸/数量/优先级）

━━━ 装修验收标准 ━━━
（上线前必须检查的10个关键点）"""

    content = await _call(_SYSTEM_DESIGN_EXPERT, prompt, max_tokens=2500, temperature=0.72)

    if not content:
        return {"error": "店铺装修方案生成暂时不可用"}

    return {
        "店铺类型": store_type,
        "主营类目": category,
        "平台": platform,
        "品牌调性": brand_tone,
        "完整装修方案": content,
        "使用说明": "可直接作为设计师交付物，所有尺寸均为执行标准",
    }


async def generate_page_conversion_plan(
    page_type: str,
    current_conv: float,
    benchmark: float,
    real_metrics: Optional[Dict[str, Any]] = None,
    product_info: Optional[Dict[str, Any]] = None,
    search_context: str = "",
) -> Dict[str, Any]:
    """
    生成个性化页面转化率优化方案。
    """
    gap = round(benchmark - current_conv, 2)
    metrics_ctx = ""
    if real_metrics and real_metrics.get("has_data"):
        anomalies = real_metrics.get("anomalies", [])
        if anomalies:
            metrics_ctx = "【近期异常】" + "；".join(
                [f"{a.get('metric_display','')} {a.get('change_pct','')}%" for a in anomalies[:3]]
            )

    prod_ctx = ""
    if product_info:
        prod_ctx = f"【关联商品】{product_info.get('name','')} ¥{product_info.get('selling_price',0)}"

    cvr_search = (
        f"\n\n【实时行业转化率基准（以实时数据校准分析）】\n{search_context}"
        "\n注：行业CVR基准因品类和季节波动显著，以上实时数据应替代静态基准值，请从中提取具体数字进行差距分析。"
    ) if search_context else ""
    prompt = f"""请为以下页面生成详细的转化率优化方案。

【页面类型】{page_type}
【当前转化率】{current_conv}%（行业基准：{benchmark}%，差距：{gap}pp）
{prod_ctx}
{metrics_ctx}{cvr_search}

请输出完整的转化优化方案：

═══ 转化障碍诊断 ═══
（分析{page_type}在{current_conv}%转化率下最可能的卡点，每个卡点说明原因和影响量级）

═══ 高优先级优化项（本周执行）═══
（预期提升转化率最大的3-5项改动，每项包含：
  - 具体改动内容（越具体越好）
  - 实施难度（低/中/高）
  - 预期转化率提升幅度
  - A/B测试方案）

═══ 中期优化项（1个月内）═══
（3-5项需要更多资源但效果显著的优化）

═══ 文案优化方向 ═══
（页面上关键文案的具体改写建议：标题/按钮/卖点描述/信任状）

═══ 技术与体验优化 ═══
（加载速度/移动端适配/支付流程/视觉优化等具体改进点）

━━━ 测量方法 ━━━
（如何验证优化效果，AB测试方法论，统计显著性要求）"""

    content = await _call(_SYSTEM_SEO_EXPERT, prompt, max_tokens=2000, temperature=0.68)

    if not content:
        return {"error": "转化优化方案生成暂时不可用"}

    return {
        "页面类型": page_type,
        "当前转化率": f"{current_conv}%",
        "行业基准": f"{benchmark}%",
        "差距": f"{gap}pp",
        "完整优化方案": content,
    }


# ═══════════════════════════════════════════════════════════════════════════
# 财务叙事分析
# ═══════════════════════════════════════════════════════════════════════════

async def generate_financial_narrative(
    pl_data: Dict[str, Any],
    period: str = "近30天",
    context: str = "",
    search_context: str = "",
) -> str:
    """
    基于真实P&L数据，生成LLM财务健康分析叙事报告。
    Returns: 叙事文本
    """
    if not pl_data:
        return ""

    # 提取关键财务数据构建摘要
    lines = [f"财务数据摘要（{period}）："]
    if pl_data.get("GMV"):
        lines.append(f"• GMV: {pl_data['GMV']}")
    if pl_data.get("净营收"):
        lines.append(f"• 净营收: {pl_data['净营收']}")
    if pl_data.get("毛利率"):
        lines.append(f"• 毛利率: {pl_data['毛利率']}")
    if pl_data.get("营业利润率"):
        lines.append(f"• 营业利润率: {pl_data['营业利润率']}")
    if pl_data.get("EBITDA"):
        lines.append(f"• EBITDA: {pl_data['EBITDA']}")
    if pl_data.get("广告ROI"):
        lines.append(f"• 广告ROI: {pl_data['广告ROI']}")
    if pl_data.get("健康度"):
        for k, v in pl_data["健康度"].items():
            lines.append(f"• {k}: {v}")

    if context:
        lines.append(f"\n背景：{context}")

    data_text = "\n".join(lines)

    benchmark_section = (
        f"\n\n【行业财务基准（实时对标数据，诊断时请与之对比）】\n{search_context}"
        "\n注：以上为当前行业真实数据，请用于判断客户指标偏离度，给出差距量化。"
    ) if search_context else ""

    prompt = f"""{data_text}{benchmark_section}

请基于以上财务数据，给出一份简洁的财务健康诊断报告（300字以内）：
1. 整体财务健康评级（S/A/B/C/D）及1句话总结
2. 最值得关注的2个财务风险点（具体数据支撑）
3. 立即可执行的3个改善动作（优先级排序）

中文，专业简洁，结论优先。"""

    return await _call(_SYSTEM_FINANCE_EXPERT, prompt, max_tokens=600, temperature=0.3) or ""


# ═══════════════════════════════════════════════════════════════════════════
# 客户分群洞察
# ═══════════════════════════════════════════════════════════════════════════

async def generate_rfm_insights(
    rfm_segments: Dict[str, Any],
    total_customers: int,
) -> str:
    """
    基于RFM分群结果，生成客户运营策略洞察。
    Returns: 洞察文本
    """
    if not rfm_segments:
        return ""

    seg_text = f"客户总数：{total_customers}\nRFM分群结果：\n"
    for seg_name, data in rfm_segments.items():
        count = data.get("数量", 0)
        pct = round(count / max(total_customers, 1) * 100, 1)
        seg_text += f"• {seg_name}：{count}人（{pct}%）- {data.get('特征', '')}\n"

    prompt = f"""{seg_text}

请基于以上RFM客户分群，给出差异化运营策略（250字以内）：
1. 最值得重点投入的客户群体（说明原因）
2. 每个主要分群的1个核心运营动作
3. 提升整体LTV（客户生命周期价值）的关键杠杆

中文，实操性强。"""

    return await _call(_SYSTEM_OPS_EXPERT, prompt, max_tokens=500, temperature=0.5) or ""


# ─────────────────────────────────────────────────────────────────────────────
# 创意内容生成引擎扩展：IP定位 / 内容排期 / 趋势分析
# ─────────────────────────────────────────────────────────────────────────────

_SYSTEM_CREATIVE_STRATEGIST = (
    "你是一位资深内容营销战略家，精通品牌IP打造、内容运营和社交媒体增长策略。"
    "你的建议具体到人设标签、内容选题、发布节奏、互动策略，每条输出都能直接落地执行。"
    "中文输出，实战派，不给泛泛建议，每条建议都有可量化的预期效果。"
)

_SYSTEM_DSR_EXPERT = (
    "你是一位资深电商客服运营专家，精通淘宝/京东/拼多多/抖音的DSR评分机制和提升策略。"
    "你的方案具体到SOP流程、话术模板、KPI指标，每条改善措施都有执行时间和预期效果。"
    "中文输出，操作性强，结论先行。"
)


async def generate_ip_branding(
    brand_name: str,
    category: str,
    target_audience: str,
    differentiator: str,
    platform: str = "小红书/抖音",
) -> Dict[str, Any]:
    """生成品牌/个人账号的完整IP人设定位方案（LLM真实生成）。"""
    prompt = f"""请为以下品牌/账号生成完整IP定位方案：

账号/品牌：{brand_name}
行业类目：{category}
目标受众：{target_audience or '18-35岁年轻消费者'}
核心差异化点：{differentiator or '专业+有温度'}
主要平台：{platform}

请输出以下内容（用JSON格式，key用中文）：

1. "IP定位标签"：3-4个能精准描述这个账号的标签词（例如："成分党美妆博主 | 护肤实验室 | 科普达人"）
2. "人设核心主张"：一句话的账号灵魂（价值主张，20字以内）
3. "内容四大支柱"：数组，每项包含 支柱名/内容比例/代表选题/预期效果
4. "差异化竞争策略"：与同类账号的本质区别，以及如何在内容上体现
5. "视觉识别体系"：主色调HEX色值/封面风格/头像建议/统一素材模板方向
6. "3个月成长路径"：按月分阶段的具体运营目标和执行重点
7. "变现模式建议"：最适合此IP的3种变现路径，按可行性排序
8. "风险规避"：这个IP定位最容易踩的坑和规避建议

直接输出JSON，不要markdown格式，不要额外说明。"""

    raw = await _call(_SYSTEM_CREATIVE_STRATEGIST, prompt, max_tokens=1500, temperature=0.75)
    if not raw:
        return {"error": "IP定位生成失败"}

    # 尝试解析JSON，失败则包装为文本
    import json
    try:
        data = json.loads(raw)
        data["账号"] = brand_name
        data["类目"] = category
        return data
    except Exception:
        return {
            "账号": brand_name,
            "类目": category,
            "平台": platform,
            "IP定位方案": raw,
        }


async def generate_content_calendar(
    platforms: List[str],
    period: str = "月",
    content_types: List[str] = None,
    weekly_posts: int = 5,
    real_metrics: Optional[Dict] = None,
    product_info: Optional[Dict] = None,
    search_context: str = "",
) -> Dict[str, Any]:
    """生成有战略价值的内容排期规划（LLM真实生成+实时营销节点搜索）。"""
    if content_types is None:
        content_types = ["种草测评", "干货教程", "生活日常", "互动话题"]

    platforms_str = "、".join(platforms) if platforms else "抖音、小红书"
    types_str = "、".join(content_types)

    metrics_ctx = ""
    if real_metrics and real_metrics.get("has_data"):
        totals = real_metrics.get("totals", {})
        gmv = totals.get("gmv", 0)
        uv = totals.get("uv", 0)
        metrics_ctx = f"\n当前店铺数据：GMV ¥{gmv:,.0f}，UV {uv:,}，可据此制定内容目标。"

    product_ctx = ""
    if product_info and product_info.get("name"):
        product_ctx = f"\n主推商品：{product_info['name']}（{product_info.get('category', '')}），售价¥{product_info.get('selling_price', '')}。"

    search_section = (
        f"\n\n【实时营销节点与热门话题（据此规划内容时机）】\n{search_context}"
        "\n注：借势热点是内容传播的乘数效应，以上实时数据应驱动内容主题和发布节奏决策。"
    ) if search_context else ""

    prompt = f"""请为以下情况制定{period}度内容运营排期方案：

平台：{platforms_str}
内容类型：{types_str}
每周发布量：{weekly_posts}条{metrics_ctx}{product_ctx}{search_section}

请输出一个战略性内容日历，包含：

1. "内容战略概述"：这个周期的核心内容主题和运营目标（非机械排表，要有战略思考）

2. "各平台发布节奏"：对象，每个平台包含：
   - 发布频率（每周X条）
   - 最佳发布时段（具体时间段，附原因）
   - 该平台最优内容形态

3. "内容配比策略"：各类内容比例及原因（不要平均分配，要有优先级）

4. "一周排期示例"：数组，7天，每天包含：平台/内容类型/选题方向/发布时间/预期目标指标

5. "热点借势时机"：当前或近期可以借势的营销节点和内容创意方向

6. "内容效果评估标准"：各类内容的关键成功指标（KSI）和复盘节奏

7. "内容SOP"：从选题到发布的标准化流程（确保团队能执行）

中文输出，有实战价值，不要模板化表述。"""

    raw = await _call(_SYSTEM_CREATIVE_STRATEGIST, prompt, max_tokens=1800, temperature=0.7)
    if not raw:
        return {
            "排期周期": period,
            "平台": platforms,
            "提示": "内容排期生成失败，请重试",
        }

    return {
        "排期周期": period,
        "平台": platforms,
        "每周发布量": weekly_posts,
        "AI内容排期方案": raw,
    }


async def generate_trend_analysis(
    category: str,
    platform: str = "抖音",
    real_metrics: Optional[Dict] = None,
    search_context: str = "",
) -> Dict[str, Any]:
    """基于LLM知识+实时网络搜索生成电商内容趋势分析与应用策略（含个性化建议）。"""
    metrics_ctx = ""
    if real_metrics and real_metrics.get("has_data"):
        totals = real_metrics.get("totals", {})
        gmv = totals.get("gmv", 0)
        conv = totals.get("conversion_rate", 0)
        metrics_ctx = f"\n店铺当前数据参考：GMV ¥{gmv:,.0f}，转化率 {conv*100:.2f}%。"

    search_section = (
        f"\n\n【实时趋势数据（请以这些数据为分析核心）】\n{search_context}"
        "\n注：趋势分析的价值在于当下，请从以上实时数据提炼具体结论，而非用通用模板描述趋势方向。"
    ) if search_context else ""

    prompt = f"""请针对以下场景，提供当前电商内容趋势深度分析：

行业类目：{category}
主要平台：{platform}{metrics_ctx}{search_section}

请分析以下内容：

1. "当前3大热门趋势"：数组，每项包含：
   - 趋势名称
   - 热度评级（1-5星）
   - 趋势本质（用户为什么会为之买单）
   - 代表案例（具体可参考的内容形式）
   - 适合品类
   - 借势时间窗口（还能跟多久）

2. "即将兴起的1个趋势"：先行者的机会窗口

3. "该类目的内容特殊性"：{category}类目在内容营销中的独特规律和注意事项

4. "可立即借势的3个创意方向"：针对{platform}平台，可以直接执行的具体创意方向（非泛泛建议）

5. "需要规避的内容陷阱"：当前常见但效果差或有风险的内容类型

6. "竞争格局判断"：当前{category}内容赛道的竞争状况，以及差异化突破口

中文，有实战价值，结合2025-2026年电商内容趋势。"""

    raw = await _call(_SYSTEM_CREATIVE_STRATEGIST, prompt, max_tokens=1500, temperature=0.7)
    if not raw:
        return {"类目": category, "平台": platform, "提示": "趋势分析生成失败"}

    return {
        "类目": category,
        "平台": platform,
        "AI趋势分析": raw,
    }


async def generate_dsr_improvement_plan(
    dsr_scores: Dict[str, float],
    data_source: str = "用户提供",
    platform: str = "淘宝",
    search_context: str = "",
) -> str:
    """基于真实DSR数据生成LLM深度改善方案（非固定措施列表）。"""
    desc = dsr_scores.get("描述相符", 4.6)
    svc = dsr_scores.get("服务态度", 4.7)
    logi = dsr_scores.get("物流服务", 4.5)
    overall = round((desc + svc + logi) / 3, 2)

    # 识别短板
    scores_list = [("描述相符", desc), ("服务态度", svc), ("物流服务", logi)]
    scores_list.sort(key=lambda x: x[1])
    weakest = scores_list[0]

    dsr_benchmark_section = (
        f"\n\n【行业DSR基准（实时对标，请据此修正优秀线判断）】\n{search_context}"
        "\n注：若上述实时数据中有平台特定基准，以实时数据为准，而非固定4.8优秀线。"
    ) if search_context else ""

    prompt = f"""请基于以下真实DSR评分数据，制定专业的DSR提升改善方案：

平台：{platform}
数据来源：{data_source}

当前DSR评分：
- 描述相符：{desc}（行业优秀线：4.8）
- 服务态度：{svc}（行业优秀线：4.8）
- 物流服务：{logi}（行业优秀线：4.8）
- 综合DSR：{overall}

最大短板：{weakest[0]}（{weakest[1]}分，距目标差 {round(4.8 - weakest[1], 2)} 分）{dsr_benchmark_section}

请给出：

1. 根因诊断：基于评分模式，推断最可能的问题根因（非泛泛列举，要有逻辑推导）

2. 优先级排序：按提分效果最大化原则，给出3个维度的改善优先级

3. 针对最短板"{weakest[0]}"的完整SOP：
   - 具体改善步骤（含执行人/时间节点）
   - 可复用的话术/流程模板
   - 30天内可见成效的KPI目标

4. 其他两个维度各1个高影响力改善动作（精准有效的单点突破）

5. 防止反弹的长效机制：评分提升后如何保持

中文，方案要能直接交给客服主管执行，非通用建议。"""

    return await _call(_SYSTEM_DSR_EXPERT, prompt, max_tokens=1200, temperature=0.65) or ""


# ─────────────────────────────────────────────────────────────────────────────
# 数据分析引擎扩展：LTV计算 / 队列留存 / 预算智能诊断
# ─────────────────────────────────────────────────────────────────────────────

async def generate_ltv_insights(
    ltv_data: Dict[str, Any],
    total_customers: int,
) -> str:
    """基于LTV计算结果生成LLM运营洞察。"""
    prompt = f"""请基于以下客户生命周期价值（LTV）数据，给出深度运营洞察：

客户总量：{total_customers}
LTV分析结果：{ltv_data}

请给出（200字以内）：
1. LTV健康度判断（与电商行业基准对比）
2. 提升LTV最有效的2个运营杠杆（要有具体策略）
3. 资源投入建议：哪类客户应该重点投入

中文，数据驱动，直接给结论。"""
    return await _call(_SYSTEM_ANALYSIS_EXPERT, prompt, max_tokens=400, temperature=0.6) or ""


async def generate_cohort_insights(
    cohort_data: Dict[str, Any],
    metric: str = "留存率",
) -> str:
    """基于队列分析结果生成LLM洞察。"""
    prompt = f"""请基于以下队列分析数据，给出深度运营洞察：

分析指标：{metric}
队列数据：{cohort_data}

请给出（200字以内）：
1. 留存曲线的核心问题（关键流失发生在哪个阶段）
2. 提升留存的1个高杠杆动作（要具体可执行）
3. 哪个时间段的客户留存最好（值得研究复制）

中文，实操性强。"""
    return await _call(_SYSTEM_ANALYSIS_EXPERT, prompt, max_tokens=400, temperature=0.6) or ""


async def generate_budget_intelligence(
    budget_data: Dict[str, Any],
    historical_gmv: float,
    target_gmv: float,
) -> str:
    """对预算编制方案进行LLM智能诊断，识别风险和优化空间。"""
    prompt = f"""请对以下预算方案进行专业财务诊断：

历史月GMV：¥{historical_gmv:,.0f}
目标月GMV：¥{target_gmv:,.0f}（增长{round((target_gmv/max(historical_gmv,1)-1)*100,1)}%）

预算分配方案：{budget_data}

请给出（200字以内）：
1. 该预算方案的最大风险点（数据支撑）
2. 预算分配是否合理（与行业基准对比，指出1个明显偏差）
3. 若GMV目标未达成80%，建议优先削减哪项费用
4. 1个能在不增加预算前提下提升GMV的运营建议

中文，财务视角，结论先行。"""
    return await _call(_SYSTEM_FINANCE_EXPERT, prompt, max_tokens=400, temperature=0.6) or ""


# ═══════════════════════════════════════════════════════════════════════════
# 促销策略 LLM 生成
# ═══════════════════════════════════════════════════════════════════════════

async def generate_promo_strategy(
    product: str,
    budget: float,
    platform: str,
    phases: List[Dict[str, Any]],
    kpi: Dict[str, Any],
    baseline_gmv: float = 0,
    search_context: str = "",
) -> str:
    """
    生成真实可执行的促销策略内容（含竞品促销情报）：
    - 具体活动主题/口号
    - 各阶段精准内容策略（而非泛指"投广告"）
    - 节点话术/优惠券文案/直播脚本钩子
    """
    phase_text = "\n".join([
        f"  {p['阶段']}（{p['天数']}天，预算¥{p['预算']:,.0f}）：目标={p['目标']}"
        for p in phases
    ])
    baseline_text = f"历史月GMV：¥{baseline_gmv:,.0f}（活动目标增长至¥{kpi.get('目标GMV', 'N/A')}）" if baseline_gmv else ""
    competitor_section = (
        f"\n\n【竞品促销实时情报（请据此制定差异化，而非通用方案）】\n{search_context}"
        "\n注：促销策略成败取决于竞品动态，请逐条分析以上实时数据，给出具体折扣力度差异化建议。"
    ) if search_context else ""

    prompt = f"""请为以下促销活动制定完整的策略内容执行方案：

【促销商品】{product}
【销售平台】{platform}
【总预算】¥{budget:,.0f}
{baseline_text}{competitor_section}

【活动阶段规划】
{phase_text}

【KPI目标】{kpi}

请输出：

═══ 活动主题与核心卖点 ═══
（设计1个能在{platform}引爆的活动主题，包含：主题名称、核心口号（5-10字）、情感诉求点）

═══ 各阶段执行内容 ═══
预热期：
→ 内容策略：（具体发什么/在哪发/发几条，不要泛泛说"种草"）
→ 优惠钩子：（具体券面值/满减门槛/优惠叠加方式）
→ 示例文案：（给出1条可直接使用的种草文案或短视频开头钩子）

爆发期：
→ 直播话术钩子：（直播间开头15秒话术/催单话术/报价话术各1条）
→ 限时文案：（倒计时/稀缺性/社会认同 3种触发器文案各1条）
→ 广告创意方向：（具体建议什么样的主图/视频封面）

续航期：
→ 复购召回文案：（1条针对已购客户的返场文案）
→ 口碑运营：（如何利用买家秀/评价放大口碑）

═══ 5个高转化文案素材 ═══
（直接输出5条可用的短文案，涵盖：标题优化/种草笔记开头/朋友圈/短信通知/买家秀引导语）

中文，实战文案风格，可直接复制使用。"""

    return await _call(_SYSTEM_CONTENT_EXPERT, prompt, max_tokens=2000, temperature=0.8) or ""


async def generate_pricing_analysis(
    product: str,
    recommended_price: float,
    cost: float,
    competitor_avg: float,
    platform: str,
    margin: float,
) -> str:
    """生成定价策略的LLM专业解读 — 包含心理定价、价格锚点、促销空间分析。"""
    prompt = f"""请对以下定价方案进行专业分析：

商品：{product or "待分析商品"}
平台：{platform}
成本：¥{cost}
建议零售价：¥{recommended_price}
竞品均价：¥{competitor_avg}
利润率：{margin}%

请分析（200字以内）：
1. 定价心理评估：¥{recommended_price}的价格锚点效果（用具体心理定价原则说明）
2. 竞争定位：相对竞品¥{competitor_avg}的策略意图（价格战/差异化/溢价）
3. 促销空间：当前价位还有多少折扣空间（维持盈亏平衡的最低价格）
4. 1个可立即执行的定价优化建议（如：改为X9结尾/设置阶梯价/组合定价）

中文，专业简洁。"""

    return await _call(_SYSTEM_OPS_EXPERT, prompt, max_tokens=500, temperature=0.6) or ""


async def generate_return_resolution(
    reason: str,
    amount: float,
    days_since_receipt: int,
    seller_fault: bool,
    product_info: Optional[Dict[str, Any]] = None,
    customer_history: str = "",
) -> str:
    """
    LLM生成复杂退换货场景的完整处理话术和决策树。
    适用于：边缘case/高额订单/情绪激动客户。
    """
    pname = product_info.get("name", "商品") if product_info else "商品"
    fault_str = "卖家责任" if seller_fault else "买家原因（非质量问题）"
    history_note = f"\n客户历史：{customer_history}" if customer_history else ""

    prompt = f"""请为以下退换货场景生成专业处理方案：

商品：{pname}
退货原因：{reason}
订单金额：¥{amount}
收货后天数：{days_since_receipt}天（7天无理由期限{'内' if days_since_receipt <= 7 else '外'}）
责任判定：{fault_str}{history_note}

请输出：

═══ 情况研判 ═══
（本案最可能的风险等级：低/中/高，判断依据）

═══ 推荐处理方案 ═══
（具体退款/补发/赔偿金额，运费承担，处理时限）

═══ 客服话术（直接使用）═══
首次回复（50字以内，安抚+确认）：
（此处给出可直接发送的话术）

处理方案告知（100字以内）：
（此处给出可直接发送的话术）

结单话术（30字以内，引导好评/维护关系）：
（此处给出可直接发送的话术）

═══ 风险预防 ═══
（1个防止此类退货再次发生的具体改善措施）

中文，话术可直接复制使用。"""

    return await _call(_SYSTEM_SERVICE_EXPERT, prompt, max_tokens=800, temperature=0.7) or ""


# ═══════════════════════════════════════════════════════════════════════════
# NPS / 客户满意度计算解读
# ═══════════════════════════════════════════════════════════════════════════

async def generate_nps_interpretation(
    nps_score: float,
    promoters_pct: float,
    detractors_pct: float,
    passives_pct: float,
    platform: str = "综合",
    top_issues: Optional[List[str]] = None,
) -> str:
    """基于NPS计算结果生成LLM专业解读与改善方向。"""
    health = "极优秀" if nps_score >= 70 else ("优秀" if nps_score >= 50 else ("正常" if nps_score >= 30 else ("需改善" if nps_score >= 0 else "危险")))
    issues_text = "\n".join([f"• {iss}" for iss in (top_issues or [])]) or "（未收集具体问题）"

    prompt = f"""请基于以下NPS（净推荐值）数据给出专业分析：

平台：{platform}
NPS分数：{nps_score}（健康状态：{health}）
推荐者(9-10分)：{promoters_pct:.1f}%
被动者(7-8分)：{passives_pct:.1f}%
批评者(0-6分)：{detractors_pct:.1f}%

主要负面反馈：
{issues_text}

电商行业NPS基准：优秀≥50，正常≥30，危险<0

请给出（180字以内）：
1. NPS诊断：当前分数在电商行业的竞争位置
2. 最值得关注的改善点（从批评者占比推断主因）
3. 将被动者转化为推荐者的1个高效策略
4. 如果重点维护推荐者，最有效的1个留存动作

中文，数据驱动，结论先行。"""

    return await _call(_SYSTEM_SERVICE_EXPERT, prompt, max_tokens=450, temperature=0.5) or ""


async def generate_inventory_strategy(
    inventory_data: Dict[str, Any],
    abc_segments: List[Dict[str, Any]],
) -> str:
    """基于库存优化计算结果生成采购和库存管理策略。"""
    total_skus = inventory_data.get("total_skus", 0)
    avg_turnover = inventory_data.get("avg_turnover_days", 0)
    total_inventory_value = inventory_data.get("total_inventory_value", 0)

    abc_text = ""
    for seg in abc_segments[:3]:
        abc_text += f"• {seg['分类']}类：{seg['SKU数']}个SKU，占库存价值{seg['价值占比']}%\n"

    prompt = f"""请基于以下库存分析数据，给出专业的库存优化建议：

库存概况：
- 总SKU数：{total_skus}
- 平均库存周转天数：{avg_turnover}天（行业优秀线：<30天）
- 库存总价值：¥{total_inventory_value:,.0f}

ABC分类结果：
{abc_text}

请给出（200字以内）：
1. 库存健康度判断（和行业基准对比）
2. A类SKU管理重点（不能缺货的关键商品）
3. C类SKU处置方案（如何清理滞销库存）
4. 降低库存周转天数的1个最有效措施

中文，可执行，直接给结论。"""

    return await _call(_SYSTEM_OPS_EXPERT, prompt, max_tokens=500, temperature=0.5) or ""


async def generate_sentiment_interpretation(
    intensity_score: float,
    urgency_level: str,
    triggered_keywords: List[str],
    raw_text: str,
    platform: str = "淘宝",
) -> str:
    """基于情感强度评分生成客服处理建议和回复策略。"""
    kw_text = "、".join(triggered_keywords[:5]) if triggered_keywords else "无"
    prompt = f"""客服情感分析结果：

平台：{platform}
情绪强度：{intensity_score:.1f}/10（0=极度负面，5=中性，10=极度正面）
紧急等级：{urgency_level}
触发关键词：{kw_text}
客户原文（摘要）：{raw_text[:120]}

请给出（150字以内）：
1. 客户当前情绪状态判断和风险等级
2. 建议的响应优先级和响应时限（如：2分钟内/1小时内）
3. 开场白建议（直接给1句话，专业有温度）
4. 核心处理策略（1-2个关键动作）

中文，直接给可操作的客服指导。"""

    return await _call(_SYSTEM_SERVICE_EXPERT, prompt, max_tokens=400, temperature=0.5) or ""


async def generate_ad_fatigue_strategy(
    channel_data: List[Dict[str, Any]],
    total_budget: float,
    platform: str = "淘宝",
) -> str:
    """基于广告疲劳检测结果，生成创意刷新和预算重分配建议。"""
    channels_text = ""
    for ch in channel_data[:5]:
        channels_text += (
            f"• {ch.get('渠道', '未知')}: CTR={ch.get('当前CTR', 0):.2f}%，"
            f"衰减率={ch.get('CTR衰减率', 0):.1f}%，状态={ch.get('疲劳状态', '正常')}\n"
        )

    prompt = f"""广告疲劳检测报告：

平台：{platform}
总广告预算：¥{total_budget:,.0f}

各渠道CTR趋势：
{channels_text}
行业基准：CTR衰减>15%（连续7天）= 疲劳信号；频次≥8次/用户 = 受众疲劳

请给出（200字以内）：
1. 当前广告疲劳程度评估（哪个渠道最严重）
2. 创意素材刷新建议（具体到换什么元素：主图/标题/人群包）
3. 预算重分配方案（把疲劳渠道的预算转移到哪里，比例是多少）
4. 未来7天的广告优化重点

中文，数据驱动，直接给执行方案。"""

    return await _call(_SYSTEM_OPS_EXPERT, prompt, max_tokens=500, temperature=0.6) or ""


async def generate_demand_forecast_insights(
    metric: str,
    alpha: float,
    mape: float,
    forecast_7d: float,
    forecast_14d: float,
    forecast_30d: float,
    current_avg: float,
    trend_direction: str,
) -> str:
    """基于SES需求预测结果生成库存备货和运营建议。"""
    metric_display = {"gmv": "GMV", "orders": "订单量", "uv": "访客量"}.get(metric, metric)
    prompt = f"""需求预测结果（SES指数平滑模型）：

预测指标：{metric_display}
模型参数：α={alpha:.2f}（越接近1表示越依赖近期数据）
预测精度：MAPE={mape:.1f}%（<10%优秀，<20%良好）
当前均值：{current_avg:.2f}
趋势方向：{trend_direction}

预测结果：
- 未来7天均值：{forecast_7d:.2f}（vs当前{'+' if forecast_7d >= current_avg else ''}{(forecast_7d-current_avg)/max(current_avg,1)*100:.1f}%）
- 未来14天均值：{forecast_14d:.2f}
- 未来30天均值：{forecast_30d:.2f}

请给出（180字以内）：
1. 需求趋势判断（增长/下降/平稳）及可能原因
2. 基于预测的备货建议（重点指明安全库存倍率）
3. 运营策略建议（结合趋势方向）
4. 预测置信度说明（MAPE的业务含义）

中文，数据支撑，实际可执行。"""

    return await _call(_SYSTEM_OPS_EXPERT, prompt, max_tokens=450, temperature=0.5) or ""





async def generate_nps_driver_analysis(
    promoter_pct: float,
    detractor_pct: float,
    category_data: list,
    platform: str = "淘宝",
    search_context: str = "",
) -> str:
    """NPS驱动因素分析 — 识别影响NPS的核心满意度驱动因素（含行业基准搜索）。"""
    nps_score = round(promoter_pct * 100 - detractor_pct * 100, 1)
    cats_text = ""
    for cat in category_data[:6]:
        cats_text += (
            "• " + cat.get("category", "") + ": 投诉率=" + f"{cat.get('complaint_rate', 0):.1%}" +
            "，NPS系数=" + f"{cat.get('nps_coefficient', 0):+.2f}" +
            "，NPS贡献=" + f"{cat.get('nps_impact', 0):+.1f}" + "\n"
        )
    benchmark_section = (
        f"\n\n【行业NPS/服务基准（实时数据，请以此替代静态行业均值）】\n{search_context}"
        "\n注：NPS基准因行业和季节差异显著，请从以上实时数据提取具体数字用于比较分析。"
    ) if search_context else ""
    prompt = (
        "NPS驱动因素诊断：\n\n"
        f"平台：{platform}\n"
        f"NPS分数：{nps_score}（推荐者{promoter_pct:.0%} - 贬低者{detractor_pct:.0%}）\n\n"
        f"各类别影响分析：\n{cats_text}"
        f"{benchmark_section}\n\n"
        "请给出（300字以内）：\n"
        "1. 当前NPS的业务解读（对比行业基准，给出准确定位）\n"
        "2. 拖累NPS最严重的2个问题类别及其根因\n"
        "3. 如果解决最大痛点，NPS可提升多少（量化估算）\n"
        "4. 优先级最高的3个改善行动（可2周内落地）\n\n"
        "中文，聚焦可执行，数据驱动。"
    )
    return await _call(_SYSTEM_ANALYSIS_EXPERT, prompt, max_tokens=600, temperature=0.5) or ""


async def generate_sla_interpretation(
    p50: float, p90: float, p99: float,
    availability: float,
    error_budget_pct: float,
    slo_target: float,
    violations: list,
    platform: str = "电商系统",
) -> str:
    """SLA监控报告 — 解读P50/P90/P99延迟与错误预算消耗情况。"""
    viol_text = ""
    for v in violations[:5]:
        viol_text += (
            "• " + v.get("name", "") + ": "
            "目标<" + str(v.get("target_ms", 0)) + "ms，"
            "实际" + f"{v.get('actual_ms', 0):.0f}" + "ms\n"
        )
    prompt = (
        "SLA健康度报告：\n\n"
        f"系统：{platform}\n"
        f"延迟分布：P50={p50:.0f}ms / P90={p90:.0f}ms / P99={p99:.0f}ms\n"
        f"可用性：{availability:.3f}%（SLO目标：{slo_target:.3f}%）\n"
        f"错误预算剩余：{error_budget_pct:.1f}%\n"
        + (f"SLO违规项：\n{viol_text}" if viol_text else "暂无SLO违规项。")
        + "\n\n请给出（200字以内）：\n"
        "1. 当前延迟健康度评估（P99偏高说明什么问题）\n"
        "2. 错误预算消耗速率是否危险（按月换算）\n"
        "3. 大促/流量峰值时的风险预判\n"
        "4. 最紧急的3个优化建议（延迟/可用性/容量方向）\n\n"
        "中文，技术视角，给出优先级。"
    )
    return await _call(_SYSTEM_ANALYSIS_EXPERT, prompt, max_tokens=500, temperature=0.5) or ""


async def generate_abc_xyz_strategy(
    matrix_summary: dict,
    high_value_items: list,
    platform: str = "淘宝",
) -> str:
    """ABC-XYZ分类策略建议 — 针对9格矩阵给出库存/运营策略。"""
    summary_text = ""
    for cell, data in matrix_summary.items():
        if data.get("count", 0) > 0:
            summary_text += (
                "• " + cell + ": " + str(data.get("count", 0)) + "个SKU，"
                "占GMV " + f"{data.get('gmv_pct', 0):.1%}" + "\n"
            )
    top_items_text = ""
    for item in high_value_items[:5]:
        top_items_text += (
            "• " + item.get("sku_id", "") + ": "
            "GMV=" + f"{item.get('gmv_pct', 0):.1%}" + "，"
            "CV=" + f"{item.get('cv', 0):.2f}" + "，"
            "类别=" + item.get("abc", "") + item.get("xyz", "") + "\n"
        )
    prompt = (
        "ABC-XYZ库存分类分析：\n\n"
        f"平台：{platform}\n\n"
        f"9格矩阵分布：\n{summary_text}\n"
        f"高价值SKU举例：\n{top_items_text}\n"
        "请给出（200字以内）：\n"
        "1. AX类（高价值+稳定需求）：持续补货策略要点\n"
        "2. AZ类（高价值+波动需求）：安全库存如何设置\n"
        "3. CX/CY类（低价值）：是否需要清仓或停售建议\n"
        "4. 整体库存健康度评分（0-10）及改善重点\n\n"
        "中文，库存管理视角，可直接执行。"
    )
    return await _call(_SYSTEM_OPS_EXPERT, prompt, max_tokens=500, temperature=0.5) or ""
