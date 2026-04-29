from __future__ import annotations

from typing import Any, Dict, List

from .base import SkillBase
from ._db_helpers import load_product_info, load_metrics_summary, load_user_products
from ._content_engine import (
    generate_service_response,
    generate_faq_answers,
    generate_escalation_script,
    generate_dsr_improvement_plan,
    generate_return_resolution,
    generate_nps_interpretation,
    generate_nps_driver_analysis,
)


class ServiceTicketHandler(SkillBase):
    """工单处理技能 — LLM生成完整5步话术包（接单/安抚/方案/跟进/邀评）"""

    def __init__(self) -> None:
        super().__init__(
            name="service_ticket_handler",
            display_name="工单处理",
            description="AI生成完整客服话术包（接单确认/安抚/解决方案/跟进/邀评），基于真实商品信息个性化",
            category="service",
            input_schema={
                "type": "object",
                "properties": {
                    "issue_type": {
                        "type": "string",
                        "description": "问题类型：物流延迟/质量问题/退款退货/发错货/售后咨询/投诉",
                    },
                    "customer_message": {"type": "string", "description": "客户原始消息"},
                    "customer_emotion": {
                        "type": "string",
                        "description": "客户情绪：普通/焦虑/愤怒/满意",
                    },
                    "platform": {"type": "string", "description": "平台：淘宝/京东/拼多多/抖音"},
                    "product_id": {"type": "integer", "description": "涉及商品ID（可选，自动加载商品信息定制话术）"},
                    "order_no": {"type": "string", "description": "订单号（可选）"},
                },
                "required": ["issue_type"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        issue_type: str = kwargs.get("issue_type", "售后咨询")
        message: str = kwargs.get("customer_message", "")
        emotion: str = kwargs.get("customer_emotion", "普通")
        platform: str = kwargs.get("platform", "淘宝")
        product_id: int = kwargs.get("product_id", 0)
        order_no: str = kwargs.get("order_no", "")

        # ── 加载真实产品信息 ──
        pinfo: Dict[str, Any] = {}
        if user_id and product_id:
            loaded = await load_product_info(user_id, product_id)
            if loaded:
                pinfo = loaded

        issue_details = message or issue_type
        if order_no:
            issue_details = f"订单号：{order_no}，{issue_details}"

        result = await generate_service_response(
            ticket_type=issue_type,
            issue_details=issue_details,
            product_info=pinfo,
            platform=platform,
            customer_emotion=emotion,
        )

        # 添加工单元数据
        sla_map = {"愤怒": "2小时", "焦虑": "4小时", "普通": "12小时", "满意": "24小时"}
        result["工单优先级"] = "P1紧急" if emotion == "愤怒" else ("P2较急" if emotion == "焦虑" else "P3普通")
        result["SLA目标"] = sla_map.get(emotion, "12小时")
        if order_no:
            result["订单号"] = order_no
        return result


class ServiceFAQPlaybook(SkillBase):
    """FAQ话术库技能 — LLM基于真实商品信息生成专属FAQ问答库"""

    def __init__(self) -> None:
        super().__init__(
            name="service_faq_playbook",
            display_name="FAQ话术库",
            description="AI基于真实商品信息和店铺政策生成专属FAQ话术库，含简短版和详细版答案",
            category="service",
            input_schema={
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer", "description": "商品ID，自动加载商品信息生成专属FAQ"},
                    "questions": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "需要生成答案的问题列表（留空则生成通用FAQ）",
                    },
                    "platform": {"type": "string", "description": "平台：淘宝/京东/拼多多/抖音"},
                    "policies": {
                        "type": "object",
                        "description": "店铺政策，如 {发货时效: '48小时', 退货政策: '7天无理由'}",
                    },
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        product_id: int = kwargs.get("product_id", 0)
        questions: List[str] = kwargs.get("questions", [])
        platform: str = kwargs.get("platform", "淘宝")
        policies: Dict[str, Any] = kwargs.get("policies", {})

        pinfo: Dict[str, Any] = {}
        if user_id and product_id:
            loaded = await load_product_info(user_id, product_id)
            if loaded:
                pinfo = loaded

        # 默认通用问题
        if not questions:
            questions = [
                "什么时候发货？",
                "支持退货退款吗？怎么操作？",
                "质量有保障吗？",
                "有优惠活动吗？",
                "怎么选尺码/规格？",
                "包邮吗？运费多少？",
                "可以开发票吗？",
                "商品是正品吗？",
            ]

        return await generate_faq_answers(
            product_info=pinfo,
            shop_policies=policies,
            questions=questions,
            platform=platform,
        )


class ServiceEscalationFlow(SkillBase):
    """升级流程技能 — LLM生成完整升级处理脚本（主管话术/赔偿方案/纠纷预防）"""

    def __init__(self) -> None:
        super().__init__(
            name="service_escalation_flow",
            display_name="升级流程",
            description="AI生成完整升级处理脚本，含主管接管话术/具体赔偿方案/纠纷预防/内部工单模板",
            category="service",
            input_schema={
                "type": "object",
                "properties": {
                    "issue_description": {"type": "string", "description": "问题描述"},
                    "customer_emotion": {
                        "type": "string",
                        "description": "客户情绪：平静/不满/愤怒/威胁投诉",
                    },
                    "previous_contacts": {"type": "integer", "description": "此前联系次数"},
                    "product_id": {"type": "integer", "description": "涉及商品ID（可选）"},
                    "customer_history": {"type": "string", "description": "客户历史（高价值/新客/疑似羊毛党）"},
                },
                "required": ["issue_description"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        description: str = kwargs.get("issue_description", "")
        emotion: str = kwargs.get("customer_emotion", "平静")
        contacts: int = kwargs.get("previous_contacts", 0)
        product_id: int = kwargs.get("product_id", 0)
        customer_history: str = kwargs.get("customer_history", "")

        # 自动确定升级级别
        level = 1
        if emotion in ("愤怒", "威胁投诉") or contacts >= 3:
            level = 3
        elif emotion == "不满" or contacts >= 2:
            level = 2

        pinfo: Dict[str, Any] = {}
        if user_id and product_id:
            loaded = await load_product_info(user_id, product_id)
            if loaded:
                pinfo = loaded

        result = await generate_escalation_script(
            issue_type=description,
            escalation_level=level,
            product_info=pinfo,
            customer_history=customer_history,
        )

        result["升级原因"] = [
            r for r in [
                "客户情绪激动" if emotion in ("愤怒", "威胁投诉") else None,
                f"多次联系（{contacts}次）" if contacts >= 2 else None,
                "需要更高权限处理" if level >= 2 else None,
            ] if r
        ] or ["常规升级"]
        result["历史联系次数"] = contacts
        return result


class ServiceDSRImprovement(SkillBase):
    """DSR提升方案技能 — 自动加载真实DSR数据"""

    def __init__(self) -> None:
        super().__init__(
            name="service_dsr_improvement",
            display_name="DSR提升方案",
            description="根据当前DSR评分（可自动从店铺数据加载），输出各维度提升方案",
            category="service",
            input_schema={
                "type": "object",
                "properties": {
                    "description_score": {"type": "number", "description": "描述相符评分(1-5)；不填则自动加载"},
                    "service_score": {"type": "number", "description": "服务态度评分(1-5)；不填则自动加载"},
                    "logistics_score": {"type": "number", "description": "物流服务评分(1-5)；不填则自动加载"},
                    "platform": {"type": "string", "description": "平台：淘宝/京东/拼多多/抖音"},
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)

        # ── 自动加载DSR（从store_metrics）──
        data_source = "用户提供"
        auto_desc: float = 0.0
        auto_svc: float = 0.0
        auto_logi: float = 0.0

        if user_id:
            summary = await load_metrics_summary(user_id, days=30)
            if summary.get("has_data"):
                platforms = summary.get("platforms", {})
                desc_vals = [p.get("dsr_description", 0) for p in platforms.values() if p.get("dsr_description")]
                svc_vals = [p.get("dsr_service", 0) for p in platforms.values() if p.get("dsr_service")]
                logi_vals = [p.get("dsr_logistics", 0) for p in platforms.values() if p.get("dsr_logistics")]
                if desc_vals:
                    auto_desc = sum(desc_vals) / len(desc_vals)
                    auto_svc = sum(svc_vals) / len(svc_vals) if svc_vals else 0
                    auto_logi = sum(logi_vals) / len(logi_vals) if logi_vals else 0
                    data_source = "真实数据（近30天）"

        desc = kwargs.get("description_score") if kwargs.get("description_score") is not None else auto_desc or 4.6
        svc = kwargs.get("service_score") if kwargs.get("service_score") is not None else auto_svc or 4.7
        logi = kwargs.get("logistics_score") if kwargs.get("logistics_score") is not None else auto_logi or 4.5

        overall = round((desc + svc + logi) / 3, 2)
        platform: str = kwargs.get("platform", "淘宝")

        result = {
            "数据来源": data_source,
            "DSR总评": overall,
            "各维度评分": {
                "描述相符": desc,
                "服务态度": svc,
                "物流服务": logi,
            },
            "健康状态": "优秀" if overall >= 4.8 else ("正常" if overall >= 4.6 else "需改善"),
        }

        # 搜索平台DSR行业基准
        dsr_bench_ctx = ""
        if kwargs.get("_needs_fresh_data", True):
            try:
                from .search import _web_search, _format_results_for_llm
                q = f"{platform} DSR评分 行业基准 店铺评分提升 2026"
                results, _ = await _web_search(q, topic="general", max_results=4, days=60)
                if results:
                    dsr_bench_ctx = _format_results_for_llm(results, max_per_item=250)
            except Exception:
                pass

        # LLM深度改善方案（替代固定措施列表）
        try:
            narrative = await generate_dsr_improvement_plan(
                dsr_scores={"描述相符": desc, "服务态度": svc, "物流服务": logi},
                data_source=data_source,
                platform=platform,
                search_context=dsr_bench_ctx,
            )
            if narrative:
                result["AI改善方案"] = narrative
            else:
                result["提升方案"] = [{"提示": "各维度均达标，继续保持"}] if overall >= 4.8 else [
                    {"提示": "请参考AI改善方案（生成失败，请重试）"}
                ]
        except Exception:
            result["提升方案"] = [{"提示": "AI方案生成失败，请重试"}]

        return result


class ServiceReturnHandler(SkillBase):
    """退换货处理技能"""

    def __init__(self) -> None:
        super().__init__(
            name="service_return_handler",
            display_name="退换货处理",
            description="根据退换货原因和订单信息，输出处理方案和运费承担方",
            category="service",
            input_schema={
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "退换货原因：不喜欢/质量问题/发错货/尺码不合/破损",
                    },
                    "order_amount": {"type": "number", "description": "订单金额"},
                    "days_since_receipt": {"type": "integer", "description": "收货天数"},
                    "product_id": {"type": "integer", "description": "商品ID（可选，用于关联商品信息）"},
                },
                "required": ["reason"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        reason: str = kwargs.get("reason", "不喜欢")
        amount: float = kwargs.get("order_amount", 100)
        days: int = kwargs.get("days_since_receipt", 3)
        product_id: int = kwargs.get("product_id", 0)

        if user_id and product_id:
            pinfo = await load_product_info(user_id, product_id)
            if pinfo and pinfo.get("selling_price") and not kwargs.get("order_amount"):
                amount = pinfo["selling_price"]

        seller_fault = reason in ("质量问题", "发错货", "破损")
        within_policy = days <= 7
        is_complex = (amount >= 200) or (not within_policy and seller_fault) or (days > 30)

        # 标准处理结构
        if not within_policy and not seller_fault:
            standard_result = {
                "退换货原因": reason,
                "收货天数": days,
                "处理结果": "超出7天无理由退货期限",
                "建议": "婉拒并解释政策，非卖家过失不受理",
                "话术": "亲，很抱歉该订单已超出7天无理由退货期限，按平台规则无法受理。如有其他问题随时联系~",
                "升级建议": "若客户坚持，请升级至主管协商处理",
            }
        elif not within_policy and seller_fault:
            standard_result = {
                "退换货原因": reason,
                "收货天数": days,
                "处理结果": "虽超期但卖家过失，特殊受理",
                "责任方": "卖家",
                "处理方案": {"退款金额": amount, "运费补贴": round(amount * 0.1, 2), "补偿": "赠送10元无门槛券"},
                "话术": "亲，虽然已超出退货期限，但这是我们的问题，我们特殊为您受理，请放心~",
            }
        else:
            shipping_bearer = "卖家" if seller_fault else "买家"
            standard_result = {
                "退换货原因": reason,
                "订单金额": amount,
                "收货天数": days,
                "是否在退货期": "是",
                "责任方": "卖家" if seller_fault else "买家",
                "运费承担": shipping_bearer,
                "处理方案": {
                    "退款金额": amount,
                    "运费补贴": round(amount * 0.1, 2) if seller_fault else 0,
                    "补偿": "赠送10元无门槛券" if seller_fault else "无",
                },
                "处理流程": [
                    "确认退货原因",
                    "提供退货地址",
                    f"运费由{shipping_bearer}承担",
                    "收到退货后48小时内退款",
                ],
                "话术": f"亲，退货已受理，运费由{shipping_bearer}承担。请将商品寄回，我们收到后尽快为您退款~",
            }

        # 复杂场景（高额订单/超期卖家过失）追加LLM专业话术
        if is_complex:
            try:
                pinfo_for_llm = pinfo if (user_id and product_id) else {}
                llm_resolution = await generate_return_resolution(
                    reason=reason,
                    amount=amount,
                    days_since_receipt=days,
                    seller_fault=seller_fault,
                    product_info=pinfo_for_llm or None,
                )
                if llm_resolution:
                    standard_result["AI专业处理方案"] = llm_resolution
            except Exception:
                pass

        return standard_result


class ServiceQueryProduct(SkillBase):
    """查询用户商品信息技能 — 客服场景快速调出商品详情"""

    def __init__(self) -> None:
        super().__init__(
            name="service_query_product",
            display_name="查询商品信息",
            description="根据商品ID或商品名查询真实商品信息，用于客服应答",
            category="service",
            input_schema={
                "type": "object",
                "properties": {
                    "product_id": {"type": "integer", "description": "商品ID"},
                    "product_name": {"type": "string", "description": "商品名称关键词（当ID不明时使用）"},
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        product_id: int = kwargs.get("product_id", 0)
        product_name: str = kwargs.get("product_name", "")

        if not user_id:
            return {"error": "未获取到用户信息"}

        if product_id:
            info = await load_product_info(user_id, product_id)
            if info:
                return {
                    "商品ID": info["id"],
                    "商品名": info["name"],
                    "类目": info.get("category", ""),
                    "售价": info.get("selling_price", 0),
                    "成本价": info.get("cost_price", 0),
                    "SKU": info.get("sku", ""),
                    "状态": info.get("lifecycle_status", ""),
                    "描述": info.get("description", "")[:200],
                    "卖点": info.get("selling_points", [])[:3],
                }
            return {"error": f"未找到商品ID={product_id}"}

        products = await load_user_products(user_id, limit=20)
        if product_name:
            matched = [p for p in products if product_name.lower() in p["name"].lower()]
        else:
            matched = products[:5]

        if not matched:
            return {"message": "未找到匹配商品", "建议": "请提供商品ID或更精确的商品名称"}

        return {
            "匹配商品列表": [
                {
                    "ID": p["id"],
                    "名称": p["name"],
                    "类目": p.get("category", ""),
                    "售价": p.get("selling_price", 0),
                    "状态": p.get("lifecycle_status", ""),
                }
                for p in matched
            ],
            "数量": len(matched),
        }


class ServiceNPSAnalyzer(SkillBase):
    """NPS净推荐值计算与CSAT满意度分析 — 客户忠诚度量化评估"""

    def __init__(self) -> None:
        super().__init__(
            name="service_nps_analyzer",
            display_name="NPS客户满意度分析",
            description=(
                "计算NPS净推荐值(%推荐者 - %批评者) + CSAT满意度评分 + 客户忠诚度分层，"
                "识别批评者主要问题并输出提升路径。可直接提供评分数据，或从真实DSR推算。"
            ),
            category="service",
            input_schema={
                "type": "object",
                "properties": {
                    "total_respondents": {"type": "integer", "description": "调查总人数"},
                    "score_distribution": {
                        "type": "object",
                        "description": "评分分布，格式：{0:5, 1:3, ..., 10:42}（key=评分, value=人数）",
                    },
                    "promoters_count": {"type": "integer", "description": "推荐者数量（9-10分），与score_distribution二选一"},
                    "passives_count": {"type": "integer", "description": "被动者数量（7-8分）"},
                    "detractors_count": {"type": "integer", "description": "批评者数量（0-6分）"},
                    "top_issues": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "批评者主要问题列表（如：['物流太慢','品质不如描述']）",
                    },
                    "platform": {"type": "string", "description": "平台：淘宝/京东/拼多多/抖音/综合"},
                    "days": {"type": "integer", "description": "若无调查数据，从真实DSR推算的天数，默认30"},
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        platform: str = kwargs.get("platform", "综合")
        top_issues: List[str] = kwargs.get("top_issues", [])

        # ── 获取评分数据 ──
        total: int = 0
        promoters: int = 0
        passives: int = 0
        detractors: int = 0
        data_source = "用户提供"

        score_dist: dict = kwargs.get("score_distribution", {})
        if score_dist:
            # 从分布计算
            for score_str, count in score_dist.items():
                s = int(score_str)
                n = int(count)
                total += n
                if s >= 9:
                    promoters += n
                elif s >= 7:
                    passives += n
                else:
                    detractors += n
        elif kwargs.get("promoters_count") is not None:
            promoters = int(kwargs.get("promoters_count", 0))
            passives = int(kwargs.get("passives_count", 0))
            detractors = int(kwargs.get("detractors_count", 0))
            total = int(kwargs.get("total_respondents", promoters + passives + detractors))
        else:
            # 从真实DSR推算（DSR 4.9+ → 高NPS）
            data_source = "DSR推算"
            days = int(kwargs.get("days", 30))
            summary = await load_metrics_summary(user_id, days=days)
            if summary.get("has_data"):
                platforms_data = summary.get("platforms", {})
                dsr_vals = []
                for pdata in platforms_data.values():
                    d = pdata.get("dsr_description", 0) or 0
                    s = pdata.get("dsr_service", 0) or 0
                    l = pdata.get("dsr_logistics", 0) or 0
                    if d > 0:
                        dsr_vals.append((d + s + l) / 3)

                avg_dsr = sum(dsr_vals) / len(dsr_vals) if dsr_vals else 4.6
                # DSR→NPS映射（经验公式）
                # DSR 5.0 ≈ NPS 80, DSR 4.8 ≈ NPS 50, DSR 4.6 ≈ NPS 20, DSR 4.4 ≈ NPS 0
                nps_est = round((avg_dsr - 4.0) / 1.0 * 80 - 40, 1)
                # 构造分布（估算）
                total = 100  # 以100人为基准
                prom_pct = max(0, min(95, (nps_est + 100) / 200 * 95))
                detr_pct = max(0, min(90, (100 - nps_est) / 200 * 90))
                promoters = round(total * prom_pct / 100)
                detractors = round(total * detr_pct / 100)
                passives = total - promoters - detractors
            else:
                return {"has_data": False, "提示": "请提供score_distribution或promoters/passives/detractors数量，或先导入店铺数据。"}

        if total <= 0:
            total = promoters + passives + detractors
        if total == 0:
            return {"error": "无有效数据，请提供评分分布或各分段人数"}

        # ── NPS计算 ──
        # NPS = (推荐者% - 批评者%)，范围 -100 到 +100
        prom_pct = round(promoters / total * 100, 1)
        pass_pct = round(passives / total * 100, 1)
        detr_pct = round(detractors / total * 100, 1)
        nps = round(prom_pct - detr_pct, 1)

        nps_grade = (
            "极优秀（世界级）" if nps >= 70
            else "优秀" if nps >= 50
            else "良好" if nps >= 30
            else "正常" if nps >= 0
            else "需改善（批评者过多）"
        )

        # ── CSAT满意度评分（满意=8-10分，通过score_dist计算或估算）──
        # 如果没有原始分布，用推荐者+被动者估算满意度
        csat_pct = round((promoters + passives) / total * 100, 1)
        csat_grade = "优秀" if csat_pct >= 90 else ("良好" if csat_pct >= 75 else "需改善")

        # ── 客户忠诚度价值分析 ──
        # 推荐者：高复购、口碑传播价值
        # 被动者：复购低、易被竞品拉走
        # 批评者：负口碑扩散风险（1个批评者平均影响5-6人决策）
        detractor_impact = round(detractors * 5.6, 0)  # 批评者负面影响人数估算

        result = {
            "数据来源": data_source,
            "调查总人数": total,
            "NPS净推荐值": nps,
            "NPS评级": nps_grade,
            "电商行业基准": "NPS 优秀≥50 | 正常≥0 | 危险<-10",
            "分群详情": {
                "推荐者(9-10分)": f"{promoters}人（{prom_pct}%）",
                "被动者(7-8分)": f"{passives}人（{pass_pct}%）",
                "批评者(0-6分)": f"{detractors}人（{detr_pct}%）",
            },
            "CSAT满意度": f"{csat_pct}%（{csat_grade}）",
            "忠诚度洞察": {
                "推荐者价值": f"贡献约{prom_pct}%的口碑传播和复购",
                "被动者风险": f"{pass_pct}%的客户随时可能流向竞品，需主动维系",
                "批评者负面影响": f"预估影响约{int(detractor_impact)}人的购买决策",
                "转化优先级": "被动者→推荐者成本最低，批评者→中立是当务之急",
            },
        }

        if top_issues:
            # 帕累托分析问题
            result["批评者主要问题"] = top_issues[:5]
            result["改善优先级"] = f"重点解决：{top_issues[0]}（批评者最常提及）"

        # LLM深度分析
        try:
            interpretation = await generate_nps_interpretation(
                nps_score=nps,
                promoters_pct=prom_pct,
                detractors_pct=detr_pct,
                passives_pct=pass_pct,
                platform=platform,
                top_issues=top_issues or None,
            )
            if interpretation:
                result["AI满意度诊断"] = interpretation
        except Exception:
            pass

        return result


class ServiceSentimentAnalyzer(SkillBase):
    """客服情感分析 — 0-10强度评分+电商危机词增强+紧急等级分类"""

    # 电商危机关键词及情感强度调整值（负=降低评分，正=提升评分）
    _CRISIS_KEYWORDS: Dict[str, float] = {
        # 极度负面（-3.0 ~ -2.0）
        "骗子": -3.0, "欺诈": -3.0, "举报": -2.8, "曝光": -2.5, "投诉到": -2.5,
        "差评": -2.0, "投诉": -2.0, "维权": -2.0,
        # 负面（-1.5 ~ -1.0）
        "退款": -1.5, "破损": -1.5, "质量差": -1.5, "假货": -2.0,
        "不满意": -1.2, "等了好久": -1.0, "还没到": -1.0, "赔偿": -1.5,
        "纠纷": -1.5, "仲裁": -1.5, "拉黑": -1.2,
        # 轻微负面（-0.5）
        "退货": -0.8, "问题": -0.5, "延误": -0.8, "发错": -1.0, "缺货": -0.8,
        # 正面词（+0.5 ~ +1.0）
        "好评": +0.8, "满意": +0.8, "感谢": +0.6, "棒": +0.5, "超快": +0.5,
        "完美": +1.0, "推荐": +0.8, "再次购买": +1.0,
    }

    # 情绪分类词（中文语义打底，在没有危机词时使用）
    _SENTIMENT_BASE: Dict[str, float] = {
        "愤怒": -3.0, "气死": -3.0, "太差了": -2.5, "垃圾": -2.5,
        "失望": -2.0, "难受": -1.5, "不开心": -1.0,
        "普通": 0.0, "一般": 0.0, "还行": 0.5,
        "挺好": 1.0, "不错": 1.0, "很好": 1.5, "太好了": 2.0, "完美": 2.5,
    }

    def __init__(self) -> None:
        super().__init__(
            name="service_sentiment_analyzer",
            display_name="客服情感分析",
            description=(
                "对客户消息进行0-10情感强度评分（0=极度负面，5=中性，10=极度正面），"
                "叠加电商危机关键词增强层，输出紧急等级、应对优先级和处理建议。"
            ),
            category="service",
            input_schema={
                "type": "object",
                "properties": {
                    "customer_text": {"type": "string", "description": "客户消息文本"},
                    "platform": {"type": "string", "description": "平台：淘宝/京东/拼多多/抖音"},
                    "channel": {
                        "type": "string",
                        "description": "来源渠道：im（旺旺）/review（评价）/complaint（投诉中心）",
                        "default": "im",
                    },
                    "batch_texts": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "批量分析多条消息（最多20条），与customer_text二选一",
                    },
                },
                "required": [],
            },
        )

    def _score_text(self, text: str) -> Dict[str, Any]:
        """对单条文本计算情感强度评分（0-10）。"""
        text_lower = text.lower()
        base_score = 5.0  # 中性起点

        triggered = []

        # Step 1: 情绪基础词
        for kw, delta in self._SENTIMENT_BASE.items():
            if kw in text_lower:
                base_score += delta * 0.3  # 低权重，防止过激
                triggered.append(kw)

        # Step 2: 电商危机词覆盖层（高权重）
        for kw, delta in self._CRISIS_KEYWORDS.items():
            if kw in text_lower:
                base_score += delta
                if kw not in triggered:
                    triggered.append(kw)

        # 文本长度信号：超长负面消息情绪更强烈
        if len(text) > 200 and base_score < 4:
            base_score -= 0.5
        elif len(text) > 100 and base_score < 3:
            base_score -= 0.3

        # 感叹号/全大写增强信号
        exclamation_count = text.count("！") + text.count("!!")
        if exclamation_count >= 2 and base_score < 5:
            base_score -= 0.3 * min(exclamation_count, 3)

        # 限制到 0-10
        intensity = round(max(0.0, min(10.0, base_score)), 1)

        # 紧急等级分类
        if intensity < 2.0:
            urgency = "🔴 高危-立即介入（2分钟内）"
            priority = 1
        elif intensity < 3.5:
            urgency = "🟠 负面-优先处理（30分钟内）"
            priority = 2
        elif intensity < 5.5:
            urgency = "🟡 中性-正常排队（2小时内）"
            priority = 3
        elif intensity < 7.5:
            urgency = "🟢 正面-无需人工干预"
            priority = 4
        else:
            urgency = "⭐ 高满意-可邀请好评"
            priority = 5

        return {
            "情感强度": intensity,
            "紧急等级": urgency,
            "处理优先级": priority,
            "触发关键词": triggered[:5],
        }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        platform: str = kwargs.get("platform", "淘宝")
        channel: str = kwargs.get("channel", "im")
        customer_text: str = kwargs.get("customer_text", "")
        batch_texts: List[str] = kwargs.get("batch_texts", [])

        channel_display = {"im": "旺旺/IM", "review": "买家评价", "complaint": "投诉中心"}.get(channel, channel)

        # 批量分析模式
        if batch_texts:
            texts = batch_texts[:20]
            scored = [self._score_text(t) for t in texts]

            # 聚合统计
            scores = [s["情感强度"] for s in scored]
            avg_score = round(sum(scores) / len(scores), 1)
            high_risk = sum(1 for s in scored if s["处理优先级"] == 1)
            negative = sum(1 for s in scored if s["处理优先级"] == 2)
            positive = sum(1 for s in scored if s["处理优先级"] >= 4)

            # 高频危机词统计
            all_keywords: Dict[str, int] = {}
            for s in scored:
                for kw in s["触发关键词"]:
                    all_keywords[kw] = all_keywords.get(kw, 0) + 1
            top_keywords = sorted(all_keywords.items(), key=lambda x: -x[1])[:5]

            return {
                "分析模式": "批量分析",
                "消息数量": len(texts),
                "平台": platform,
                "渠道": channel_display,
                "整体情感评分": avg_score,
                "分布统计": {
                    "高危需立即处理": high_risk,
                    "负面优先处理": negative,
                    "正常处理": len(texts) - high_risk - negative - positive,
                    "正面/高满意": positive,
                },
                "高频危机词": [{"词": kw, "出现次数": cnt} for kw, cnt in top_keywords],
                "建议": (
                    f"⚠️ 有{high_risk}条高危消息需立即处理"
                    if high_risk > 0
                    else "✅ 无高危消息，正常处理队列"
                ),
                "明细": [
                    {"序号": i + 1, "文本摘要": t[:50], **scored[i]}
                    for i, t in enumerate(texts)
                ],
            }

        # 单条分析
        if not customer_text:
            return {"error": "请提供 customer_text 或 batch_texts"}

        scored = self._score_text(customer_text)
        intensity = scored["情感强度"]
        urgency = scored["紧急等级"]
        triggered = scored["触发关键词"]

        result: Dict[str, Any] = {
            "平台": platform,
            "渠道": channel_display,
            "客户消息摘要": customer_text[:80],
            "情感强度评分": f"{intensity}/10",
            "情感状态": (
                "极度负面" if intensity < 2
                else "负面" if intensity < 4
                else "中性" if intensity < 6
                else "正面" if intensity < 8
                else "极度正面"
            ),
            "紧急等级": urgency,
            "触发关键词": triggered,
            "电商行业参考": "高危=需2分钟内响应，负面=30分钟内，中性=2小时内SLA",
        }

        # 高危/负面：调用LLM生成处理建议
        if intensity < 4.5:
            try:
                from ._content_engine import generate_sentiment_interpretation
                advice = await generate_sentiment_interpretation(
                    intensity_score=intensity,
                    urgency_level=urgency,
                    triggered_keywords=triggered,
                    raw_text=customer_text,
                    platform=platform,
                )
                if advice:
                    result["AI处理建议"] = advice
            except Exception:
                pass

        return result


class ServiceNPSDriverAnalysis(SkillBase):
    """NPS驱动因素分析 — 4类投诉来源根因诊断 + NPS影响系数矩阵"""

    # NPS系数：各问题类别对NPS的拖累幅度（负值=拖累）
    _NPS_COEFFICIENTS = {
        "物流配送": -0.35,   # 慢/丢件/破损
        "商品质量": -0.48,   # 货不对版/瑕疵/假货
        "客服响应": -0.22,   # 慢/冷漠/解决率低
        "售后退款": -0.30,   # 退款慢/拒退/流程复杂
        "包装体验": -0.12,   # 破损/过度/不环保
        "价格感知": -0.18,   # 比价亏/隐藏费用
        "商品描述": -0.20,   # 图文不符/信息缺失
        "平台体验": -0.10,   # 搜索/界面/结算
    }

    def __init__(self) -> None:
        super().__init__(
            name="service_nps_driver_analysis",
            display_name="NPS驱动因素分析",
            description="4类投诉来源根因诊断，计算各类别NPS影响系数，识别最大满意度驱动因素；输入投诉分布或自动从历史工单推导",
            category="service",
            input_schema={
                "type": "object",
                "properties": {
                    "promoter_count": {"type": "integer", "description": "推荐者数量（评分9-10）"},
                    "detractor_count": {"type": "integer", "description": "贬低者数量（评分0-6）"},
                    "passive_count": {"type": "integer", "description": "被动者数量（评分7-8）"},
                    "complaint_distribution": {
                        "type": "object",
                        "description": "投诉分布：{物流配送: 120, 商品质量: 85, ...}",
                    },
                    "total_tickets": {"type": "integer", "description": "总工单数（用于计算比率）"},
                    "platform": {"type": "string", "description": "平台：淘宝/京东/拼多多/抖音"},
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        promoter = kwargs.get("promoter_count", 0)
        detractor = kwargs.get("detractor_count", 0)
        passive = kwargs.get("passive_count", 0)
        complaint_dist: Dict[str, int] = kwargs.get("complaint_distribution", {})
        total_tickets: int = kwargs.get("total_tickets", 0)
        platform: str = kwargs.get("platform", "淘宝")

        # ── 计算NPS ──
        total_respondents = promoter + detractor + passive
        if total_respondents == 0:
            # 无真实数据时使用行业默认分布
            promoter, passive, detractor = 45, 35, 20
            total_respondents = 100

        promoter_pct = promoter / total_respondents
        detractor_pct = detractor / total_respondents
        nps_score = round((promoter_pct - detractor_pct) * 100, 1)

        # ── 计算各类别NPS影响 ──
        # 如未提供投诉分布，使用电商行业默认分布
        if not complaint_dist:
            complaint_dist = {
                "物流配送": 38,
                "商品质量": 25,
                "售后退款": 18,
                "客服响应": 12,
                "商品描述": 7,
            }
            total_tickets = sum(complaint_dist.values())

        if not total_tickets:
            total_tickets = sum(complaint_dist.values()) or 100

        # 对每个类别计算：投诉率 × NPS系数 = NPS影响
        category_data: List[Dict[str, Any]] = []
        total_impact = 0.0

        for cat, cnt in sorted(complaint_dist.items(), key=lambda x: -x[1]):
            complaint_rate = cnt / total_tickets
            coeff = self._NPS_COEFFICIENTS.get(cat, -0.15)
            # NPS影响 = 投诉率 × |系数| × 100（转化为NPS点数）
            nps_impact = complaint_rate * coeff * 100
            total_impact += nps_impact
            category_data.append({
                "category": cat,
                "complaint_count": cnt,
                "complaint_rate": complaint_rate,
                "nps_coefficient": coeff,
                "nps_impact": round(nps_impact, 2),
                "priority": "P0" if abs(nps_impact) > 3 else "P1" if abs(nps_impact) > 1.5 else "P2",
            })

        # 按NPS影响排序（影响最大的排前面）
        category_data.sort(key=lambda x: x["nps_impact"])

        # ── 理论最大NPS提升（解决所有投诉）──
        max_uplift = round(abs(total_impact), 1)

        # ── 行业基准 ──
        benchmarks = {
            "淘宝": 45, "京东": 52, "拼多多": 30,
            "抖音": 40, "general": 40,
        }
        industry_avg = benchmarks.get(platform, 40)
        nps_gap = round(nps_score - industry_avg, 1)

        result: Dict[str, Any] = {
            "平台": platform,
            "NPS分数": f"{nps_score:+.1f}",
            "NPS构成": {
                "推荐者(9-10分)": f"{promoter_pct:.0%}",
                "被动者(7-8分)": f"{passive / total_respondents:.0%}",
                "贬低者(0-6分)": f"{detractor_pct:.0%}",
                "受访总数": total_respondents,
            },
            "行业基准": {
                "行业平均NPS": industry_avg,
                "差距": f"{nps_gap:+.1f}分",
                "位置": "优于行业" if nps_gap >= 0 else "低于行业",
            },
            "驱动因素分析": [
                {
                    "问题类别": d["category"],
                    "投诉量": d["complaint_count"],
                    "投诉率": f"{d['complaint_rate']:.1%}",
                    "NPS拖累": f"{d['nps_impact']:+.2f}分",
                    "处理优先级": d["priority"],
                }
                for d in category_data
            ],
            "理论最大NPS提升": f"+{max_uplift}分（解决全部投诉问题）",
            "最大痛点": category_data[0]["category"] if category_data else "无数据",
            "总工单数": total_tickets,
        }

        # 实时搜索行业NPS基准（仅当需要实时数据时）
        search_ctx = ""
        if kwargs.get("_needs_fresh_data", True):
            try:
                from .search import _web_search, _format_results_for_llm
                nps_query = f"{platform} 电商 NPS 行业基准 客户满意度 2026"
                nps_results, _eng = await _web_search(nps_query, topic="general", max_results=4, days=60)
                if nps_results:
                    search_ctx = _format_results_for_llm(nps_results, max_per_item=250)
                    result["行业基准参考（实时）"] = search_ctx
            except Exception:
                pass

        # LLM深度分析
        try:
            advice = await generate_nps_driver_analysis(
                promoter_pct=promoter_pct,
                detractor_pct=detractor_pct,
                category_data=category_data,
                platform=platform,
                search_context=search_ctx,
            )
            if advice:
                result["AI改善建议"] = advice
        except Exception:
            pass

        return result


ALL_SKILLS: list[SkillBase] = [
    ServiceTicketHandler(),
    ServiceFAQPlaybook(),
    ServiceEscalationFlow(),
    ServiceDSRImprovement(),
    ServiceReturnHandler(),
    ServiceQueryProduct(),
    ServiceNPSAnalyzer(),
    ServiceSentimentAnalyzer(),
    ServiceNPSDriverAnalysis(),
]
