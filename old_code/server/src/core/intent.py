"""
意图分析器 — 纯规则关键词匹配，0 LLM 调用。

根据用户消息提取：primary_role, support_roles, action, platform, tier, confidence。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ═══════════════════════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════════════════════

TIER_QUICK = 0   # 打招呼、简单状态查询
TIER_SINGLE = 1  # 单角色可完成
TIER_MULTI = 2   # 需要多角色协作

ROLES = ("ops", "data", "service", "design", "accounting", "engineering", "web", "creative")

ACTIONS = ("analyze", "create", "optimize", "query", "plan", "execute")

PLATFORMS = (
    "taobao", "tmall", "jd", "pdd", "douyin",
    "xiaohongshu", "kuaishou", "weixin", "meituan", "shopee", "general",
)

# ═══════════════════════════════════════════════════════════════════════════
# Intent 数据类
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class Intent:
    primary_role: str = "ops"
    support_roles: List[str] = field(default_factory=list)
    action: str = "query"
    platform: str = "general"
    tier: int = TIER_SINGLE
    confidence: float = 0.5
    keywords: List[str] = field(default_factory=list)  # matched keywords from message
    domain_id: str = "domain.general"
    domain_confidence: float = 0.3
    domain_candidates: List[Dict[str, Any]] = field(default_factory=list)

# ═══════════════════════════════════════════════════════════════════════════
# 关键词表
# ═══════════════════════════════════════════════════════════════════════════

ROLE_KEYWORDS: Dict[str, List[str]] = {
    "ops": [
        "直通车", "推广", "流量", "转化率", "ROI", "投放", "出价", "人群",
        "运营", "AARRR", "活动", "促销", "大促", "618", "双11", "定价",
        "渠道", "万相台", "广告", "竞价", "PPC", "ROAS", "GMV",
        "秒杀", "满减", "预售", "报名", "库存", "商品", "选品",
        "上架", "下架", "操盘", "规划", "策略", "复盘",
        # 营销语义（避免“营销问题被误派数据分析师”）
        "营销", "营销方式", "营销策略", "增长策略", "增长方案",
        "获客", "拉新", "促活", "留存", "复购", "品牌传播",
        "活动策划", "渠道策略", "投放策略", "公域", "私域",
        # 库存优化/智能定价关键词
        "EOQ", "安全库存", "补货", "库存积压", "周转", "缺货", "滞销",
        "价格弹性", "最优定价", "促销策略", "广告素材",
        # 广告疲劳/需求预测关键词
        "广告疲劳", "CTR下降", "投放疲劳", "频次", "人群包",
        "需求预测", "备货", "预测模型", "指数平滑", "MAPE", "预测精度",
        # ABC-XYZ库存分类
        "ABC分类", "XYZ分类", "SKU分类", "库存矩阵", "变异系数", "CV值", "帕累托库存",
    ],
    "data": [
        "数据", "分析", "SQL", "报表", "漏斗", "RFM", "异常", "趋势",
        "同比", "环比", "指标", "GMV", "UV", "PV", "统计", "预测",
        "模型", "维度", "占比", "增长率", "留存", "DAU", "MAU",
        "归因", "实验", "AB测试", "对照组", "显著性", "置信",
        "仪表板", "看板", "下钻", "口径",
    ],
    "service": [
        "客服", "售后", "退货", "退款", "投诉", "差评", "发货", "物流",
        "客诉", "DSR", "话术", "工单", "咨询", "签收", "赔偿", "客服文案",
        "质量问题", "损坏", "不满意", "售前", "满意度", "SLA",
        "升级", "纠纷", "仲裁", "好评", "评价", "FCR",
        # 新增：NPS/CSAT/客户体验关键词
        "NPS", "净推荐值", "CSAT", "客户满意度", "推荐者", "批评者", "被动者",
        "口碑", "客户体验", "CX", "复购", "流失", "忠诚度",
        # 新增：情感分析关键词
        "情感分析", "情绪", "愤怒", "骂人", "好评", "差评回复", "负面反馈",
        "情感评分", "紧急处理", "高危", "客诉分析",
        # 新增：NPS驱动因素
        "NPS驱动", "推荐者比例", "投诉分布", "满意度根因", "贬低者",
    ],
    "design": [
        "设计", "主图", "详情页", "视觉", "配色", "排版", "海报",
        "banner", "素材", "拍摄", "模特", "色值", "字体", "尺寸",
        "UI", "切图", "动效", "品牌", "logo", "VI", "包装",
        "首图", "白底图", "场景图", "视频封面",
    ],
    "accounting": [
        "财务", "成本", "利润", "毛利", "预算", "税", "发票",
        "账单", "盈亏", "现金流", "佣金", "账期", "结算",
        "费用", "营收", "净利", "固定成本", "可变成本", "盈亏平衡",
        "审计", "合同", "报销", "开票", "对账",
    ],
    "engineering": [
        "代码", "开发", "架构", "API", "数据库", "性能", "部署",
        "服务器", "bug", "系统", "接口", "SDK", "前端", "后端",
        "微服务", "缓存", "并发", "安全", "加密", "监控",
        "运维", "Docker", "CI", "CD", "回滚", "灰度",
        # 新增：SLA监控关键词
        "SLA", "延迟", "P99", "P95", "P90", "响应时间", "可用性", "错误预算",
        "超时", "宕机", "故障", "超时率", "成功率", "吞吐量",
    ],
    "web": [
        "SEO", "搜索排名", "关键词排名", "页面优化", "装修", "首页设计",
        "导航", "权重", "收录", "外链", "站内优化",
        "加载速度", "自然流量", "搜索优化",
        "meta", "H1", "结构化数据", "站点地图", "店铺装修",
        # 新增：标题SEO评分关键词
        "标题评分", "SEO评分", "标题质量", "关键词密度", "TF-IDF", "标题诊断",
    ],
    "creative": [
        "短视频", "种草", "脚本", "创意", "内容", "小红书", "文案",
        "IP", "直播", "爆款", "口播", "分镜", "剪辑", "BGM",
        "hook", "完播率", "互动率", "达人", "KOL", "KOC",
        "笔记", "故事", "人设", "slogan", "趣味",
        # CTR预测评分
        "CTR预测", "点击率", "标题吸引力", "AIDA", "情感触发词", "转化标题",
        # 内容创作 — 确保文案/营销内容请求路由到creative
        "写文案", "帮我写", "写一篇", "写一个", "营销文案", "推广文案", "种草文",
        "宣传语", "广告语", "标语", "品牌故事", "产品描述", "软文", "推文",
        "视频脚本", "直播脚本", "视频文案", "宣传片", "素材创意",
        "内容策划", "选题", "热点", "内容日历", "内容营销",
    ],
}


# 关键词加权：降低泛词“分析”对 data 的误吸附，保留强数据词主导权
ROLE_KEYWORD_WEIGHTS: Dict[str, Dict[str, float]] = {
    "data": {
        "分析": 0.35,
        "analysis": 0.35,
    }
}

_MARKETING_INTENT_KEYWORDS: List[str] = [
    "营销", "营销方式", "营销策略", "增长策略", "增长方案",
    "品牌传播", "品牌营销", "获客", "拉新", "促活", "留存", "复购",
    "渠道策略", "渠道投放", "活动策划", "推广节奏",
]

_DATA_EVIDENCE_KEYWORDS: List[str] = [
    "sql", "报表", "漏斗", "同比", "环比", "指标", "统计",
    "归因", "实验", "ab测试", "a/b", "显著性", "置信",
    "看板", "仪表板", "口径", "uv", "pv", "gmv", "留存率",
]

_DESIGN_CREATIVE_INTENT_KEYWORDS: List[str] = [
    "设计", "视觉", "文案", "素材", "主图", "详情页", "海报", "版式", "排版",
    "钩子", "脚本", "封面", "创意", "品牌风格", "色值", "字体",
]

_EXPERIMENT_INTENT_KEYWORDS: List[str] = [
    "a/b", "ab测试", "ab 实验", "实验", "测试", "对照组", "测试组",
]

_DATA_STRONG_CONTEXT_INTENT_KEYWORDS: List[str] = [
    "sql", "报表", "漏斗", "看板", "仪表板", "口径", "归因", "显著性", "置信", "dashboard",
]

QUICK_KEYWORDS = [
    "你好", "嗨", "hello", "hi", "在吗", "谢谢", "感谢", "帮助",
    "状态", "进度", "你是谁", "你能做什么", "再见", "拜拜",
]

QUICK_CONFIRM_KEYWORDS = [
    "确认", "确认下", "确认一下", "请确认", "简短确认", "短确认",
    "收到", "收到请回复", "明白", "知道了", "了解了",
    "ok", "okay", "got it", "roger", "noted", "ack", "acknowledge",
    "confirm", "short confirmation", "brief confirmation",
]

# 避免把业务问题误判为 quick（即便出现“确认/收到”这类词）
QUICK_COMPLEXITY_BLOCKERS = [
    "分析", "策略", "方案", "计划", "优化", "报表", "数据", "sql",
    "roi", "gmv", "转化", "投放", "预算", "活动", "库存", "商品", "订单",
    "设计", "代码", "接口", "部署", "财务", "成本", "利润", "退款", "投诉",
    "seo", "脚本", "文案", "流程", "执行", "排查", "故障", "bug",
]

SMALL_TALK_KEYWORDS = [
    "讲个笑话", "笑话", "闲聊", "聊天", "天气", "自我介绍",
    "你多大", "你叫什么", "娱乐", "段子",
]

ACTION_KEYWORDS: Dict[str, List[str]] = {
    "analyze": ["分析", "诊断", "排查", "原因", "为什么", "对比", "评估", "审计", "复盘"],
    "create":  ["制作", "创建", "写", "生成", "新建", "策划", "编写", "撰写", "设计"],
    "optimize": ["优化", "提升", "改善", "调整", "改进", "降低", "提高", "增长"],
    "query":   ["查询", "看看", "查看", "了解", "是什么", "怎么样", "多少", "哪些", "告诉我"],
    "plan":    ["规划", "计划", "方案", "预案", "排期", "安排", "目标", "预测"],
    "execute": ["执行", "操作", "上线", "发布", "投放", "启动", "开始", "处理", "解决"],
}

PLATFORM_KEYWORDS: Dict[str, List[str]] = {
    "taobao":       ["淘宝", "手淘", "淘系", "淘宝直通车"],
    "tmall":        ["天猫", "猫店", "天猫超市"],
    "jd":           ["京东", "JD", "京东快车", "京挑客"],
    "pdd":          ["拼多多", "PDD", "多多", "拼多多推广"],
    "douyin":       ["抖音", "千川", "巨量", "抖店", "抖音电商"],
    "xiaohongshu":  ["小红书", "薯", "红薯"],
    "kuaishou":     ["快手", "磁力金牛", "快手电商"],
    "weixin":       ["微信", "小程序", "公众号", "视频号", "微信小店"],
    "meituan":      ["美团", "大众点评", "美团闪购"],
    "shopee":       ["虾皮", "Shopee", "Lazada", "跨境"],
}

# 角色亲和度 — 某些关键词同时触发多个角色时，此表决定支持角色
ROLE_AFFINITY: Dict[str, List[str]] = {
    "ops":         ["data", "design"],
    "data":        ["ops", "accounting"],
    "service":     ["ops", "data"],
    "design":      ["creative", "ops"],
    "accounting":  ["data", "ops"],
    "engineering": ["data", "ops"],
    "web":         ["design", "creative"],
    "creative":    ["design", "ops"],
}


_ROLE_DOMAIN_BUCKETS: Dict[str, str] = {
    "creative": "content",
    "design": "content",
    "data": "data_analytics",
    "accounting": "finance",
    "ops": "operations",
    "service": "customer",
    "web": "digital",
    "engineering": "technical",
}


def _normalize_role_key(value: Any) -> str:
    return str(value or "").strip().lower()


def _merge_role_keywords(base: List[str], extra: List[str], limit: int = 120) -> List[str]:
    merged: List[str] = []
    seen: set[str] = set()

    for collection in (base, extra):
        for kw in collection:
            token = str(kw or "").strip()
            if not token:
                continue
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(token)
            if len(merged) >= limit:
                return merged
    return merged


def _resolve_runtime_role_context() -> Dict[str, Any]:
    runtime_roles: List[str] = list(ROLES)
    default_role = "ops"
    alias_map: Dict[str, str] = {r: r for r in ROLES}
    role_packages: Dict[str, List[Dict[str, Any]]] = {}
    role_keywords: Dict[str, List[str]] = {r: list(ROLE_KEYWORDS.get(r, [])) for r in ROLES}

    try:
        from src.core.role_router import build_runtime_role_context

        ctx = build_runtime_role_context()
        loaded_roles = [
            _normalize_role_key(x)
            for x in (ctx.get("runtime_roles") or [])
            if _normalize_role_key(x)
        ]
        for role in loaded_roles:
            if role not in runtime_roles:
                runtime_roles.append(role)
                role_keywords.setdefault(role, [])

        loaded_default = _normalize_role_key(ctx.get("default_role"))
        if loaded_default:
            default_role = loaded_default
            if default_role not in runtime_roles:
                runtime_roles.insert(0, default_role)
                role_keywords.setdefault(default_role, [])

        loaded_alias_map = ctx.get("alias_map") if isinstance(ctx.get("alias_map"), dict) else {}
        loaded_packages = ctx.get("role_packages") if isinstance(ctx.get("role_packages"), dict) else {}
        role_packages = loaded_packages if isinstance(loaded_packages, dict) else {}

        for key, value in loaded_alias_map.items():
            alias_key = _normalize_role_key(key)
            mapped = _normalize_role_key(value)
            if alias_key and mapped:
                alias_map[alias_key] = mapped

        for runtime_role, candidates in role_packages.items():
            role_key = _normalize_role_key(runtime_role)
            if not role_key:
                continue
            if role_key not in runtime_roles:
                runtime_roles.append(role_key)
                role_keywords.setdefault(role_key, [])

            package_keywords: List[str] = []
            if isinstance(candidates, list):
                for candidate in candidates:
                    if not isinstance(candidate, dict):
                        continue
                    kws = candidate.get("keywords")
                    if isinstance(kws, list):
                        package_keywords.extend([str(x).strip() for x in kws if str(x).strip()])

            role_keywords[role_key] = _merge_role_keywords(role_keywords.get(role_key, []), package_keywords)
    except Exception:
        pass

    if default_role not in runtime_roles:
        default_role = "ops" if "ops" in runtime_roles else runtime_roles[0]

    runtime_set = set(runtime_roles)
    normalized_alias_map: Dict[str, str] = {}
    for key, value in alias_map.items():
        alias_key = _normalize_role_key(key)
        mapped = _normalize_role_key(value)
        if not alias_key:
            continue
        if mapped not in runtime_set:
            mapped = default_role
        normalized_alias_map[alias_key] = mapped

    for role in runtime_roles:
        normalized_alias_map.setdefault(role, role)
        role_keywords.setdefault(role, [])

    return {
        "runtime_roles": runtime_roles,
        "default_role": default_role,
        "alias_map": normalized_alias_map,
        "role_keywords": role_keywords,
        "role_packages": role_packages,
    }


def _resolve_role_domain_bucket(role: str, runtime_ctx: Dict[str, Any]) -> str:
    role_key = _normalize_role_key(role)
    if role_key in _ROLE_DOMAIN_BUCKETS:
        return _ROLE_DOMAIN_BUCKETS[role_key]

    role_packages = runtime_ctx.get("role_packages") if isinstance(runtime_ctx.get("role_packages"), dict) else {}
    candidates = role_packages.get(role_key)
    if isinstance(candidates, list):
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            domains = candidate.get("domains")
            if not isinstance(domains, list):
                continue
            for domain in domains:
                domain_id = str(domain or "").strip()
                if not domain_id:
                    continue
                if domain_id.startswith("domain."):
                    return domain_id.split(".", 1)[1] or role_key or "general"
                return domain_id

    return role_key or "general"


# ═══════════════════════════════════════════════════════════════════════════
# 分析函数
# ═══════════════════════════════════════════════════════════════════════════

def _score_roles(
    text: str,
    roles: Optional[List[str]] = None,
    role_keywords: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, float]:
    """对每个角色计算关键词匹配得分。"""
    active_roles = [
        _normalize_role_key(r)
        for r in (roles or list(ROLES))
        if _normalize_role_key(r)
    ]
    if not active_roles:
        active_roles = ["ops"]

    keyword_map = role_keywords if isinstance(role_keywords, dict) else ROLE_KEYWORDS
    scores: Dict[str, float] = {r: 0.0 for r in active_roles}

    text_lower = text.lower()
    for role in active_roles:
        keywords = keyword_map.get(role) if isinstance(keyword_map.get(role), list) else []
        weight_map = ROLE_KEYWORD_WEIGHTS.get(role, {})
        for kw in keywords:
            if kw.lower() in text_lower:
                kw_key = str(kw or "").strip().lower()
                scores[role] += float(weight_map.get(kw_key, 1.0))
    return scores


def _extract_matched_keywords(
    text: str,
    role_keywords: Optional[Dict[str, List[str]]] = None,
) -> List[str]:
    """提取消息中匹配到的所有业务关键词（用于前端工作流可视化）。"""
    text_lower = text.lower()
    keyword_map = role_keywords if isinstance(role_keywords, dict) else ROLE_KEYWORDS
    matched = []
    for keywords in keyword_map.values():
        for kw in keywords:
            if kw.lower() in text_lower and kw not in matched:
                matched.append(kw)
    return matched[:8]  # 最多返回8个，够用即可


def _count_keyword_hits(text_lower: str, keywords: List[str]) -> int:
    if not text_lower:
        return 0
    return sum(1 for kw in keywords if kw and kw in text_lower)


def _apply_intent_role_biases(
    final_scores: Dict[str, float],
    *,
    message: str,
    history_text: str = "",
) -> None:
    """
    对角色分数做轻量语义偏置，修正“营销问法被误派 data”这类高频误判。

    规则：
    1) 营销信号命中时，对 ops 提升优先级；
    2) 当 data 仅由“分析”这类泛词触发且缺少数据证据时，限制其分值；
    3) 对 creative/web 仅做小幅补位偏置，避免主角色抖动。
    """
    message_lower = (message or "").lower()
    history_lower = (history_text or "").lower()

    marketing_hits = _count_keyword_hits(message_lower, _MARKETING_INTENT_KEYWORDS)
    data_evidence_hits = _count_keyword_hits(message_lower, _DATA_EVIDENCE_KEYWORDS)
    design_creative_hits = _count_keyword_hits(message_lower, _DESIGN_CREATIVE_INTENT_KEYWORDS)
    experiment_hits = _count_keyword_hits(message_lower, _EXPERIMENT_INTENT_KEYWORDS)
    data_strong_context_hits = _count_keyword_hits(message_lower, _DATA_STRONG_CONTEXT_INTENT_KEYWORDS)
    if history_lower:
        marketing_hits += int(round(_count_keyword_hits(history_lower, _MARKETING_INTENT_KEYWORDS) * 0.5))
        data_evidence_hits += int(round(_count_keyword_hits(history_lower, _DATA_EVIDENCE_KEYWORDS) * 0.5))
        design_creative_hits += int(round(_count_keyword_hits(history_lower, _DESIGN_CREATIVE_INTENT_KEYWORDS) * 0.4))
        experiment_hits += int(round(_count_keyword_hits(history_lower, _EXPERIMENT_INTENT_KEYWORDS) * 0.4))
        data_strong_context_hits += int(round(_count_keyword_hits(history_lower, _DATA_STRONG_CONTEXT_INTENT_KEYWORDS) * 0.4))

    if marketing_hits > 0 and "ops" in final_scores:
        final_scores["ops"] += 1.10 + min(marketing_hits, 3) * 0.25

    weak_data_only = (
        "data" in final_scores
        and data_evidence_hits == 0
        and (
            "分析" in message_lower
            or "analysis" in message_lower
            or "analyze" in message_lower
        )
    )
    if weak_data_only:
        final_scores["data"] = min(final_scores.get("data", 0.0), 0.80)
        if marketing_hits > 0:
            final_scores["data"] = max(0.0, final_scores["data"] - 0.45)

    design_creative_experiment_focus = bool(
        experiment_hits >= 1
        and design_creative_hits >= 2
        and data_strong_context_hits <= 1
    )
    if design_creative_experiment_focus:
        if "design" in final_scores:
            final_scores["design"] += 1.35 + min(design_creative_hits, 4) * 0.20
        if "creative" in final_scores:
            final_scores["creative"] += 1.05 + min(design_creative_hits, 4) * 0.16

        if "data" in final_scores:
            data_penalty = 1.05 + (0.35 if data_evidence_hits <= 2 else 0.0)
            final_scores["data"] = max(0.0, final_scores.get("data", 0.0) - data_penalty)

        if "design" in final_scores and "data" in final_scores:
            if final_scores["data"] >= final_scores["design"]:
                final_scores["design"] = final_scores["data"] + 0.08

    if marketing_hits > 0 and "creative" in final_scores:
        if any(token in message_lower for token in ["文案", "内容", "脚本", "短视频", "品牌", "种草"]):
            final_scores["creative"] += 0.45

    if marketing_hits > 0 and "web" in final_scores:
        if any(token in message_lower for token in ["seo", "搜索", "关键词", "站内", "流量入口"]):
            final_scores["web"] += 0.35


def _detect_action(text: str) -> str:
    """检测用户意图中的动作类型。"""
    text_lower = text.lower()
    best_action = "query"
    best_score = 0
    for action, keywords in ACTION_KEYWORDS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > best_score:
            best_score = score
            best_action = action
    return best_action


def _detect_platform(text: str) -> str:
    """检测提及的平台。"""
    text_lower = text.lower()
    for platform, keywords in PLATFORM_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                return platform
    return "general"


def _detect_complexity(text: str) -> int:
    """
    检测任务复杂度信号数量，用于决定是否触发多Agent协作。

    信号类型（每种+1分）：
    - 消息较长（>80字）→ 通常包含多个子任务
    - 规划/全局类关键词 → 需要多角色综合
    - 多目标连接词 → 明确多需求
    - 数据+执行组合 → 分析与行动都需要
    """
    signals = 0
    if len(text) > 80:
        signals += 1
    if any(kw in text for kw in ["方案", "规划", "计划", "策略", "系统", "整体", "全面", "综合"]):
        signals += 1
    if any(kw in text for kw in ["同时", "并且", "另外", "还需要", "以及", "还有", "加上", "另外还"]):
        signals += 1
    if any(kw in text for kw in ["数据", "分析"]) and any(
        kw in text for kw in ["执行", "方案", "策略", "操作", "推广", "优化"]
    ):
        signals += 1
    return signals


def _is_small_talk(text: str) -> bool:
    """判断是否明显非业务闲聊，避免误触发重调度。"""
    t = (text or "").strip().lower()
    if not t:
        return False
    return any(kw in t for kw in SMALL_TALK_KEYWORDS)


def _has_quick_complexity_signals(text_lower: str) -> bool:
    """quick 判定时的复杂业务信号门禁。"""
    if not text_lower:
        return False
    if any(kw in text_lower for kw in QUICK_COMPLEXITY_BLOCKERS):
        return True
    return bool(
        re.search(
            r"\b(analy[sz]e|analysis|strategy|plan|optimi[sz]e|report|data|sql|"
            r"roi|gmv|conversion|campaign|budget|api|deploy|finance|cost|profit|"
            r"error|bug|workflow|execution)\b",
            text_lower,
        )
    )


def _is_quick(text: str) -> bool:
    """判断是否为快速对话（打招呼、简单状态查询）。"""
    text_stripped = (text or "").strip()
    if not text_stripped:
        return False
    text_lower = text_stripped.lower()

    # 1) 保留原规则：短消息 + 快速关键词
    if len(text_stripped) <= 20 and any(kw in text_lower for kw in QUICK_KEYWORDS):
        return True

    # 2) 适度放宽：较短问候/状态确认，但必须没有复杂业务信号
    if len(text_stripped) <= 64 and any(kw in text_lower for kw in QUICK_KEYWORDS):
        if not _has_quick_complexity_signals(text_lower):
            return True

    # 3) 新增：确认/收悉类短句（例如 "please reply with a short confirmation"）
    if len(text_stripped) <= 96 and any(kw in text_lower for kw in QUICK_CONFIRM_KEYWORDS):
        if not _has_quick_complexity_signals(text_lower):
            return True

    if len(text_stripped) <= 96 and re.search(r"\b(short|brief)\s+confirmation\b", text_lower):
        return True

    return False


def analyze_intent(
    message: str,
    history: Optional[List[str]] = None,
    learned_keywords: Optional[Dict[str, List[str]]] = None,
) -> Intent:
    """
    分析用户消息的意图。

    Parameters
    ----------
    message : str
        用户当前消息。
    history : list[str] | None
        最近的对话历史文本（可选），用于上下文增强。
    learned_keywords : dict[str, list[str]] | None
        从 DB 预加载的已学习路由关键词，按角色分组。
        格式：{"ops": ["爆款选品", "流量诊断"], "data": [...]} 

    Returns
    -------
    Intent
        包含 primary_role, support_roles, action, platform, tier, confidence 的意图对象。
    """
    runtime_ctx = _resolve_runtime_role_context()
    runtime_roles = runtime_ctx["runtime_roles"]
    default_role = runtime_ctx["default_role"]
    alias_map = runtime_ctx["alias_map"]
    role_keywords = runtime_ctx["role_keywords"]

    default_or_ops = "ops" if "ops" in runtime_roles else default_role

    # 外层领域识别（domain）
    try:
        from src.core.domain_router import classify_domain

        domain_match = classify_domain(message)
    except Exception:
        domain_match = None

    # Quick tier 判断
    if _is_quick(message) or _is_small_talk(message):
        return Intent(
            primary_role=default_or_ops,
            support_roles=[],
            action="query",
            platform="general",
            tier=TIER_QUICK,
            confidence=0.9,
            domain_id=(domain_match.domain_id if domain_match else "domain.general"),
            domain_confidence=(domain_match.confidence if domain_match else 0.3),
            domain_candidates=(domain_match.candidates if domain_match else []),
        )

    # 合并历史上下文用于增强评分
    recent_history_text = ""
    if history:
        # 只取最近3条历史，权重较低
        recent_history_text = " ".join(history[-3:])

    # 角色评分（主消息权重2倍，历史权重1倍）
    msg_scores = _score_roles(message, roles=runtime_roles, role_keywords=role_keywords)
    hist_scores = _score_roles(
        recent_history_text,
        roles=runtime_roles,
        role_keywords=role_keywords,
    ) if recent_history_text else {r: 0.0 for r in runtime_roles}

    # ★ 注入学习到的关键词得分（权重1.5倍，介于主消息和历史之间）
    learned_scores: Dict[str, float] = {r: 0.0 for r in runtime_roles}
    if learned_keywords:
        text_lower = message.lower()
        for role, keywords in learned_keywords.items():
            role_key = _normalize_role_key(role)
            normalized_role = alias_map.get(role_key, role_key)
            if normalized_role in learned_scores:
                for kw in keywords:
                    if kw.lower() in text_lower:
                        learned_scores[normalized_role] += 1.0

    final_scores: Dict[str, float] = {}
    for r in runtime_roles:
        final_scores[r] = msg_scores[r] * 2.0 + hist_scores[r] * 0.5 + learned_scores[r] * 1.5

    _apply_intent_role_biases(
        final_scores,
        message=message,
        history_text=recent_history_text,
    )

    # 排序选出主角色
    sorted_roles = sorted(final_scores.items(), key=lambda x: x[1], reverse=True)
    top_score = sorted_roles[0][1] if sorted_roles else 0.0

    if top_score == 0.0:
        # 没有匹配任何关键词 → 默认 ops
        primary_role = default_or_ops
        confidence = 0.3
    else:
        primary_role = sorted_roles[0][0]
        # 置信度：主角色得分占总得分的比例
        total = sum(s for _, s in sorted_roles)
        confidence = round(min(top_score / max(total, 1.0), 1.0), 2)

    # 支持角色：得分 > 0 且不是主角色的前三个（最多3个，允许3-Agent协作）
    support_roles = [
        r for r, s in sorted_roles[1:]
        if s > 0
    ][:3]

    # 如果没有支持角色，从亲和度表补充
    if not support_roles:
        support_roles = [x for x in ROLE_AFFINITY.get(primary_role, []) if x in final_scores][:1]

    # 动作
    action = _detect_action(message)

    # 平台
    platform = _detect_platform(message)

    # Tier 判断：避免因共享关键词（如GMV在ops/data都有）误触发多Agent
    second_score = sorted_roles[1][1] if len(sorted_roles) > 1 else 0.0
    complexity = _detect_complexity(message)

    # 能力域划分：让数据和财务是不同域（两者协同是真正的多Agent场景）
    primary_domain = _resolve_role_domain_bucket(primary_role, runtime_ctx)
    second_role = sorted_roles[1][0] if len(sorted_roles) > 1 else ""
    second_domain = _resolve_role_domain_bucket(second_role, runtime_ctx)
    cross_domain = primary_domain != second_domain

    # TIER_MULTI 触发条件（任意一条满足）：
    # A. 第二名得分≥3.0 且跨域（排除噪音：GMV在ops/data只贡献2分，需要≥2个独立关键词命中）
    # B. 第二名得分≥2.0 且任务复杂度≥2（明确多需求信号）
    tier = TIER_SINGLE
    if second_score >= 3.0 and cross_domain:
        tier = TIER_MULTI
    elif second_score >= 2.0 and complexity >= 2:
        tier = TIER_MULTI

    # 领域感知的岗位建议（role packs）
    try:
        from src.core.role_router import suggest_roles

        suggested = suggest_roles(
            message,
            domain_match.domain_id if domain_match else "domain.general",
            max_roles=3,
            action=action,
        )
    except Exception:
        suggested = []

    suggested_roles: List[str] = []
    for role_name in suggested:
        role_key = _normalize_role_key(role_name)
        normalized = alias_map.get(role_key, role_key)
        if normalized in final_scores and normalized not in suggested_roles:
            suggested_roles.append(normalized)

    # 兼容保护：电商域保持历史路由行为，避免通用化改造影响既有能力输出稳定性。
    legacy_ecommerce_mode = bool(domain_match and domain_match.domain_id == "domain.ecommerce")

    if suggested_roles:
        suggested_primary = suggested_roles[0]
        if top_score == 0.0:
            if (not legacy_ecommerce_mode) and suggested_primary != primary_role:
                primary_role = suggested_primary
                confidence = max(confidence, 0.35)
        elif (not legacy_ecommerce_mode) and suggested_primary != primary_role and final_scores.get(primary_role, 0.0) < 2.0:
            if final_scores.get(suggested_primary, 0.0) >= final_scores.get(primary_role, 0.0):
                primary_role = suggested_primary
                confidence = max(confidence, 0.45)

        if not legacy_ecommerce_mode:
            for role_name in suggested_roles[1:]:
                if role_name != primary_role and role_name not in support_roles:
                    support_roles.append(role_name)

    normalized_support_roles: List[str] = []
    for role_name in support_roles:
        role_key = _normalize_role_key(role_name)
        normalized_role = alias_map.get(role_key, role_key)
        if normalized_role not in final_scores:
            continue
        if normalized_role == primary_role or normalized_role in normalized_support_roles:
            continue
        normalized_support_roles.append(normalized_role)

    support_roles = normalized_support_roles[:3]
    keywords = _extract_matched_keywords(message, role_keywords=role_keywords)

    return Intent(
        primary_role=primary_role,
        support_roles=support_roles,
        action=action,
        platform=platform,
        tier=tier,
        confidence=confidence,
        keywords=keywords,
        domain_id=(domain_match.domain_id if domain_match else "domain.general"),
        domain_confidence=(domain_match.confidence if domain_match else 0.3),
        domain_candidates=(domain_match.candidates if domain_match else []),
    )
