"""
因果推理框架 — 结构化假设验证，取代LLM猜测式诊断。

当用户问"为什么销售下降了"，系统不是让LLM瞎猜，
而是按优先级逐一验证预设假设: 流量→客单价→退货→缺货→渠道结构。
每个假设附带SQL hint帮助数据Agent快速验证。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# ═══════════════════════════════════════════════════════════════
# 异常类型检测关键词
# ═══════════════════════════════════════════════════════════════

ANOMALY_KEYWORDS: Dict[str, List[str]] = {
    "sales_drop": [
        "销售下降", "收入减少", "销量下降", "GMV下跌",
        "订单减少", "营收下滑", "卖不动",
    ],
    "return_spike": [
        "退货增加", "退货率升高", "退款增多",
        "退货暴增", "退款率",
    ],
    "conversion_drop": [
        "转化下降", "转化率降低", "转化变差",
        "不转化", "转化低",
    ],
    "cost_surge": [
        "成本上升", "费用增加", "ROI下降",
        "亏损", "超预算", "花费太多",
    ],
    "traffic_drop": [
        "流量下降", "访客减少", "UV下降",
        "没有流量", "曝光下降",
    ],
    "complaint_surge": [
        "投诉增多", "差评增加", "客诉",
        "大量投诉", "评分下降",
    ],
}

# ═══════════════════════════════════════════════════════════════
# 因果假设库 — 每个异常类型的验证链
# ═══════════════════════════════════════════════════════════════

CAUSAL_HYPOTHESES: Dict[str, List[Dict[str, Any]]] = {
    "sales_drop": [
        {
            "hypothesis": "流量下降导致订单减少",
            "check": "对比近7天与前7天的UV/PV",
            "sql_hint": (
                "SELECT date, SUM(uv) FROM traffic "
                "GROUP BY date ORDER BY date DESC LIMIT 14"
            ),
            "threshold": 0.15,
            "next_if_confirmed": (
                "检查推广投放是否暂停、关键词质量分是否下降、是否有新竞品抢量"
            ),
        },
        {
            "hypothesis": "客单价下降拉低GMV",
            "check": "对比近期平均客单价",
            "sql_hint": (
                "SELECT AVG(order_amount) FROM orders "
                "WHERE date >= ? GROUP BY week"
            ),
            "threshold": 0.10,
            "next_if_confirmed": (
                "检查是否主推品从高价切到低价、促销折扣力度是否过大"
            ),
        },
        {
            "hypothesis": "退货增多导致有效销售减少",
            "check": "对比退货率变化",
            "sql_hint": (
                "SELECT COUNT(CASE WHEN status='returned' THEN 1 END)"
                "*100.0/COUNT(*) FROM orders WHERE date >= ?"
            ),
            "threshold": 0.05,
            "next_if_confirmed": (
                "检查是否有批次质量问题、物流损坏、描述不符投诉"
            ),
        },
        {
            "hypothesis": "热销品缺货导致整体销售下滑",
            "check": "检查TOP5商品库存和销售变化",
            "sql_hint": (
                "SELECT product_name, current_stock, sales_7d, sales_7d_prev "
                "FROM product_performance ORDER BY sales_7d_prev DESC LIMIT 5"
            ),
            "threshold": 0.0,
            "next_if_confirmed": "紧急补货，同时推荐替代品填补销售缺口",
        },
        {
            "hypothesis": "渠道/地区结构变化",
            "check": "按渠道/地区拆分销售占比变化",
            "sql_hint": (
                "SELECT channel, SUM(amount) FROM orders "
                "WHERE date >= ? GROUP BY channel"
            ),
            "threshold": 0.10,
            "next_if_confirmed": (
                "识别下滑渠道，分析该渠道是否有政策变化或竞品活动"
            ),
        },
    ],
    "return_spike": [
        {
            "hypothesis": "产品质量批次问题",
            "check": "检查退货原因分布，是否集中在'质量问题'",
            "sql_hint": (
                "SELECT return_reason, COUNT(*) FROM returns "
                "WHERE date >= ? GROUP BY return_reason"
            ),
            "threshold": 0.30,
            "next_if_confirmed": (
                "联系供应商排查批次，暂停发货该批次，准备客户补偿方案"
            ),
        },
        {
            "hypothesis": "物流运输损坏",
            "check": "检查退货原因中'损坏/破损'的比例",
            "sql_hint": (
                "SELECT COUNT(CASE WHEN reason LIKE '%损坏%' THEN 1 END)"
                "*100.0/COUNT(*) FROM returns"
            ),
            "threshold": 0.20,
            "next_if_confirmed": "更换包装方案或物流服务商，申请物流赔付",
        },
        {
            "hypothesis": "描述与实物不符",
            "check": "检查'不符/不一致/色差'相关退货",
            "sql_hint": (
                "SELECT COUNT(CASE WHEN reason LIKE '%不符%' "
                "OR reason LIKE '%色差%' THEN 1 END) FROM returns"
            ),
            "threshold": 0.15,
            "next_if_confirmed": "更新详情页描述和图片，确保真实反映产品",
        },
    ],
    "conversion_drop": [
        {
            "hypothesis": "竞品降价抢夺流量",
            "check": "监控竞品价格变化",
            "sql_hint": "比较自身与竞品TOP3的售价变化趋势",
            "threshold": 0.10,
            "next_if_confirmed": (
                "评估跟进降价的利润影响，或差异化定位避开价格战"
            ),
        },
        {
            "hypothesis": "页面体验劣化(加载慢/图片失效)",
            "check": "检查页面加载时间和跳出率",
            "sql_hint": (
                "SELECT avg_load_time, bounce_rate FROM page_performance "
                "WHERE date >= ?"
            ),
            "threshold": 0.0,
            "next_if_confirmed": "优化图片大小，检查CDN，修复失效链接",
        },
        {
            "hypothesis": "评价变差影响转化信心",
            "check": "检查近期差评数量和DSR评分变化",
            "sql_hint": (
                "SELECT AVG(rating) FROM reviews "
                "WHERE date >= ? GROUP BY week"
            ),
            "threshold": -0.3,
            "next_if_confirmed": "启动差评回访和改善计划，提升好评引导",
        },
    ],
    "cost_surge": [
        {
            "hypothesis": "推广成本飙升(CPC/CPM上涨)",
            "check": "对比推广花费和单次点击成本",
            "sql_hint": (
                "SELECT campaign, SUM(cost), AVG(cpc) FROM ad_data "
                "WHERE date >= ? GROUP BY campaign"
            ),
            "threshold": 0.20,
            "next_if_confirmed": "优化关键词，暂停低ROI计划，调整出价策略",
        },
        {
            "hypothesis": "退货退款侵蚀利润",
            "check": "计算退货造成的净损失",
            "sql_hint": (
                "SELECT SUM(refund_amount) FROM refunds WHERE date >= ?"
            ),
            "threshold": 0.0,
            "next_if_confirmed": (
                "控制退货源头（质量/描述/物流），调整定价覆盖退货成本"
            ),
        },
    ],
    "traffic_drop": [
        {
            "hypothesis": "搜索排名下降",
            "check": "检查核心关键词搜索排名变化",
            "sql_hint": (
                "SELECT keyword, current_rank, prev_rank "
                "FROM keyword_rankings"
            ),
            "threshold": 5,
            "next_if_confirmed": (
                "优化标题关键词，提高点击率和转化率以恢复权重"
            ),
        },
        {
            "hypothesis": "推广计划暂停或预算耗尽",
            "check": "检查推广计划状态和预算消耗",
            "sql_hint": (
                "SELECT campaign, status, budget, spent "
                "FROM campaigns WHERE status != 'active'"
            ),
            "threshold": 0.0,
            "next_if_confirmed": "恢复推广计划或追加预算",
        },
        {
            "hypothesis": "平台算法/规则变更",
            "check": "是否有近期平台公告或规则调整",
            "sql_hint": "无SQL，需查看平台公告",
            "threshold": 0.0,
            "next_if_confirmed": "根据新规则调整运营策略",
        },
    ],
    "complaint_surge": [
        {
            "hypothesis": "系统性质量问题",
            "check": "投诉是否集中在同一产品/批次",
            "sql_hint": (
                "SELECT product_name, COUNT(*) FROM complaints "
                "WHERE date >= ? GROUP BY product_name ORDER BY 2 DESC"
            ),
            "threshold": 0.0,
            "next_if_confirmed": "暂停问题产品销售，启动质量调查，准备批量补偿",
        },
        {
            "hypothesis": "物流时效延迟",
            "check": "检查发货和签收时效",
            "sql_hint": (
                "SELECT AVG(delivery_days), "
                "COUNT(CASE WHEN delivery_days > 5 THEN 1 END) "
                "FROM deliveries"
            ),
            "threshold": 0.0,
            "next_if_confirmed": "催促物流/更换服务商，主动联系延迟订单客户",
        },
    ],
}

# 异常类型的中文标签（用于 prompt 展示）
_ANOMALY_LABELS: Dict[str, str] = {
    "sales_drop": "销售下降",
    "return_spike": "退货激增",
    "conversion_drop": "转化率下降",
    "cost_surge": "成本飙升",
    "traffic_drop": "流量下降",
    "complaint_surge": "投诉激增",
}


# ═══════════════════════════════════════════════════════════════
# 公开 API
# ═══════════════════════════════════════════════════════════════


def detect_anomaly_type(message: str) -> Optional[str]:
    """
    检测用户消息中的异常类型。

    遍历所有异常关键词，返回首个命中的类型。
    如果消息命中多个类型，按匹配关键词数量取最多的。
    返回 None 表示未检测到已知异常。
    """
    if not message:
        return None

    scores: Dict[str, int] = {}
    for anomaly_type, keywords in ANOMALY_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in message)
        if count > 0:
            scores[anomaly_type] = count

    if not scores:
        return None

    # 取匹配关键词最多的类型；相同时按字典中的定义顺序（稳定排序）
    return max(scores, key=lambda k: scores[k])


def detect_all_anomaly_types(message: str) -> List[str]:
    """
    检测消息中包含的所有异常类型，按匹配度降序返回。

    适用于用户一句话提到多个问题的场景，如
    "销售下降了，退货也变多了"。
    """
    if not message:
        return []

    scored: List[tuple[str, int]] = []
    for anomaly_type, keywords in ANOMALY_KEYWORDS.items():
        count = sum(1 for kw in keywords if kw in message)
        if count > 0:
            scored.append((anomaly_type, count))

    scored.sort(key=lambda x: x[1], reverse=True)
    return [t for t, _ in scored]


def get_hypotheses(anomaly_type: str) -> List[Dict[str, Any]]:
    """
    获取该异常类型的假设验证链。

    返回按优先级排列的假设列表（列表顺序即优先级）。
    如果类型不存在则返回空列表。
    """
    return CAUSAL_HYPOTHESES.get(anomaly_type, [])


def build_causal_prompt(anomaly_type: str) -> str:
    """
    构建因果推理提示词段落，注入到 data agent 的 system prompt 中。

    返回 Markdown 格式的结构化分析框架，引导 LLM 按优先级
    逐一验证假设而非自由猜测。

    如果异常类型不存在，返回空字符串（不注入无关内容）。
    """
    hypotheses = get_hypotheses(anomaly_type)
    if not hypotheses:
        return ""

    label = _ANOMALY_LABELS.get(anomaly_type, anomaly_type)

    lines: List[str] = [
        "## 因果分析框架",
        f"检测到用户可能在讨论「{label}」，请按以下优先级逐一验证：",
        "",
    ]

    for idx, h in enumerate(hypotheses, 1):
        lines.append(f"{idx}. **假设: {h['hypothesis']}**")
        lines.append(f"   - 验证方法: {h['check']}")
        lines.append(f"   - SQL参考: `{h['sql_hint']}`")

        # 格式化阈值说明
        threshold = h["threshold"]
        if isinstance(threshold, float) and threshold > 0:
            lines.append(
                f"   - 判定阈值: 变化超过{threshold:.0%}视为显著"
            )
        elif isinstance(threshold, (int, float)) and threshold > 0:
            lines.append(
                f"   - 判定阈值: 变化超过{threshold}视为显著"
            )

        lines.append(f"   - 若确认: {h['next_if_confirmed']}")
        lines.append("")

    lines.append(
        "请从假设1开始逐一检查，找到根因后给出具体行动建议。"
    )
    lines.append(
        "如果某个假设被排除（数据无显著变化），明确说明后继续下一个。"
    )

    return "\n".join(lines)


def build_multi_causal_prompt(message: str) -> str:
    """
    对用户消息做完整的因果分析 prompt 构建。

    自动检测所有异常类型，为每个类型生成假设框架，
    合并为一份完整的分析指引。
    """
    anomaly_types = detect_all_anomaly_types(message)
    if not anomaly_types:
        return ""

    sections: List[str] = []
    for atype in anomaly_types:
        prompt = build_causal_prompt(atype)
        if prompt:
            sections.append(prompt)

    if not sections:
        return ""

    if len(sections) == 1:
        return sections[0]

    # 多个异常类型时加一个总览提示
    header = (
        "## 多维度异常分析\n"
        "检测到用户消息涉及多个异常维度，请依次分析：\n"
    )
    return header + "\n---\n\n".join(sections)


def get_anomaly_summary(anomaly_type: str) -> Dict[str, Any]:
    """
    获取异常类型的摘要信息，适用于 SSE 事件推送或前端展示。

    返回结构:
    {
        "type": "sales_drop",
        "label": "销售下降",
        "hypothesis_count": 5,
        "hypotheses": ["流量下降导致订单减少", ...]
    }
    """
    hypotheses = get_hypotheses(anomaly_type)
    return {
        "type": anomaly_type,
        "label": _ANOMALY_LABELS.get(anomaly_type, anomaly_type),
        "hypothesis_count": len(hypotheses),
        "hypotheses": [h["hypothesis"] for h in hypotheses],
    }
