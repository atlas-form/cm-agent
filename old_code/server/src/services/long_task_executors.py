"""
长时任务执行器 — 月度审计、综合诊断、批量分析等。

每个执行器是一个 async generator，yield 进度/结果事件。
通过 @register_executor(task_type) 注册到 long_task_service。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncIterator, Callable, Dict, List

from src.services.long_task_service import register_executor

logger = logging.getLogger(__name__)


def _pev(pct: int, step: str) -> Dict[str, Any]:
    return {"type": "progress", "pct": pct, "step": step}


# ── 综合业务诊断 ─────────────────────────────────────────────────────────────

@register_executor("comprehensive_diagnosis")
async def exec_comprehensive_diagnosis(
    user_id: int,
    params: Dict[str, Any],
    progress_cb: Callable,
) -> AsyncIterator[Dict[str, Any]]:
    """
    综合业务诊断：
    1. 加载店铺指标
    2. 异常检测
    3. RFM客户分层
    4. 退款根因分析
    5. 需求预测
    6. GMV瀑布分析
    7. AI综合诊断报告
    """
    from src.core.metrics_store import get_summary, detect_anomalies, has_data, format_for_prompt
    from src.skills.registry import get_registry
    registry = get_registry()
    async def execute_skill(name, args, context=None):
        return await registry.execute(name, args, context)
    from src.llm_client import call_llm as llm_generate

    days = params.get("days", 30)
    platform = params.get("platform", "general")

    # Step 1: 加载指标
    yield _pev(5, "正在加载店铺指标数据...")
    await progress_cb(5, "加载指标")
    summary = await get_summary(user_id, days=days)
    has_real_data = await has_data(user_id)
    metrics_text = format_for_prompt(summary)

    # Step 2: 异常检测
    yield _pev(15, "正在进行异常指标检测...")
    await progress_cb(15, "异常检测")
    anomalies = await detect_anomalies(user_id, days=14)
    anomaly_text = ""
    if anomalies:
        lines = []
        for a in anomalies[:5]:
            lines.append(f"- {a.get('metric','')} 在 {a.get('date','')} 异常: {a.get('value','')}")
        anomaly_text = "检测到异常指标：\n" + "\n".join(lines)

    # Step 3: RFM 客户分层
    yield _pev(28, "正在进行RFM客户分层分析...")
    await progress_cb(28, "客户分层")
    rfm_result = {}
    try:
        skill_ctx = {"_user_id": user_id, "_needs_fresh_data": False}
        rfm_result = await execute_skill(
            "data_customer_segmentation",
            {"days": days},
            context=skill_ctx,
        )
    except Exception as e:
        logger.debug("RFM skip: %s", e)

    # Step 4: 退款根因
    yield _pev(42, "正在分析退款根因...")
    await progress_cb(42, "退款分析")
    refund_result = {}
    try:
        refund_result = await execute_skill(
            "data_refund_decomposition",
            {"days": days},
            context=skill_ctx,
        )
    except Exception as e:
        logger.debug("Refund skip: %s", e)

    # Step 5: 需求预测
    yield _pev(55, "正在生成销售需求预测...")
    await progress_cb(55, "需求预测")
    forecast_result = {}
    try:
        forecast_result = await execute_skill(
            "data_demand_forecast",
            {"horizon_days": 30},
            context=skill_ctx,
        )
    except Exception as e:
        logger.debug("Forecast skip: %s", e)

    # Step 6: GMV瀑布
    yield _pev(68, "正在构建GMV利润瀑布...")
    await progress_cb(68, "利润瀑布")
    waterfall_result = {}
    try:
        wfall_ctx = {"_user_id": user_id, "_needs_fresh_data": False}
        waterfall_result = await execute_skill(
            "accounting_gmv_waterfall",
            {"user_id": user_id},
            context=wfall_ctx,
        )
    except Exception as e:
        logger.debug("Waterfall skip: %s", e)

    # Step 7: 归因分析
    yield _pev(78, "正在进行渠道归因分析...")
    await progress_cb(78, "归因分析")
    attr_result = {}
    try:
        attr_result = await execute_skill(
            "data_attribution_analysis",
            {"days": days},
            context=skill_ctx,
        )
    except Exception as e:
        logger.debug("Attribution skip: %s", e)

    # Step 8: AI 综合报告
    yield _pev(88, "AI正在生成综合诊断报告...")
    await progress_cb(88, "生成报告")

    # 组装诊断上下文
    diag_ctx_parts = [metrics_text or "（无真实指标数据）"]
    if anomaly_text:
        diag_ctx_parts.append(anomaly_text)
    if rfm_result.get("分层结果"):
        diag_ctx_parts.append(f"客户分层：{rfm_result.get('分层结果','')}")
    if refund_result.get("帕累托分析"):
        diag_ctx_parts.append(f"退款根因：{refund_result.get('帕累托分析','')}")
    if forecast_result.get("预测结果"):
        diag_ctx_parts.append(f"需求预测：{forecast_result.get('预测结果','')}")
    if waterfall_result.get("瀑布分析"):
        diag_ctx_parts.append(f"利润瀑布：{str(waterfall_result.get('瀑布分析',''))[:500]}")
    if attr_result.get("归因结果"):
        diag_ctx_parts.append(f"渠道归因：{str(attr_result.get('归因结果',''))[:400]}")

    diag_context = "\n\n".join(diag_ctx_parts)

    diagnosis_report = ""
    try:
        messages = [
            {
                "role": "user",
                "content": (
                    f"请基于以下店铺数据，生成一份完整的综合业务诊断报告（{days}天周期）。\n\n"
                    f"{diag_context}\n\n"
                    "报告需包含：\n"
                    "1. 整体业务健康度评分（0-100分）及理由\n"
                    "2. 核心发现：3-5个最重要的业务洞察\n"
                    "3. 风险预警：需要立即关注的问题\n"
                    "4. 增长机会：可执行的具体增长建议（3条以上）\n"
                    "5. 优先行动清单：按优先级排序的7天内行动项\n"
                    "请用中文，结构清晰，数字具体，避免空话。"
                ),
            }
        ]
        system_prompt = (
            "你是资深电商综合分析师，擅长从多维度数据中找出根因和机会。"
            "分析要有深度，引用具体数字，给出可执行的具体建议。"
            "禁止编造数据，如果某项数据缺失请说明。"
        )
        diagnosis_report = await llm_generate(messages, system=system_prompt)
    except Exception as e:
        logger.warning("LLM diagnosis failed: %s", e)
        diagnosis_report = f"AI分析暂不可用。已完成数据收集：{metrics_text}"

    await progress_cb(100, "诊断完成")
    yield _pev(100, "综合诊断完成")

    result = {
        "report": diagnosis_report,
        "metrics_summary": summary,
        "anomalies": anomalies,
        "rfm": rfm_result,
        "refund": refund_result,
        "forecast": forecast_result,
        "waterfall": waterfall_result,
        "attribution": attr_result,
        "has_real_data": has_real_data,
        "days": days,
    }
    yield {"type": "result", "data": result}


# ── 月度经营审计 ──────────────────────────────────────────────────────────────

@register_executor("monthly_audit")
async def exec_monthly_audit(
    user_id: int,
    params: Dict[str, Any],
    progress_cb: Callable,
) -> AsyncIterator[Dict[str, Any]]:
    """
    月度经营审计：完整P&L + 预算差异 + 三场景预测 + 风险评估。
    """
    from src.core.metrics_store import get_summary, format_for_prompt
    from src.skills.registry import get_registry
    registry = get_registry()
    async def execute_skill(name, args, context=None):
        return await registry.execute(name, args, context)
    from src.llm_client import call_llm as llm_generate

    month = params.get("month", "")  # "2026-03" format, optional

    yield _pev(8, "正在加载月度指标数据...")
    await progress_cb(8, "加载指标")
    summary = await get_summary(user_id, days=30)
    metrics_text = format_for_prompt(summary)

    skill_ctx = {"_user_id": user_id, "_needs_fresh_data": False}

    # P&L 报表
    yield _pev(20, "正在生成P&L损益报表...")
    await progress_cb(20, "P&L报表")
    pl_result = {}
    try:
        pl_result = await execute_skill("accounting_pl_statement", {"period": month or "本月"}, context=skill_ctx)
    except Exception as e:
        logger.debug("PL skip: %s", e)

    # 预算差异
    yield _pev(35, "正在对比预算与实际...")
    await progress_cb(35, "预算差异")
    bva_result = {}
    try:
        bva_result = await execute_skill("accounting_budget_vs_actual", {"month": month}, context=skill_ctx)
    except Exception as e:
        logger.debug("BVA skip: %s", e)

    # GMV 瀑布
    yield _pev(50, "正在构建GMV利润瀑布...")
    await progress_cb(50, "利润瀑布")
    waterfall = {}
    try:
        waterfall = await execute_skill("accounting_gmv_waterfall", {"user_id": user_id}, context=skill_ctx)
    except Exception as e:
        logger.debug("Waterfall skip: %s", e)

    # 三场景分析
    yield _pev(65, "正在生成三场景预测...")
    await progress_cb(65, "场景预测")
    scenario = {}
    try:
        scenario = await execute_skill("accounting_scenario_analysis", {}, context=skill_ctx)
    except Exception as e:
        logger.debug("Scenario skip: %s", e)

    # 现金流预测
    yield _pev(78, "正在生成现金流预测...")
    await progress_cb(78, "现金流")
    cashflow = {}
    try:
        cashflow = await execute_skill("accounting_cash_flow_forecast", {}, context=skill_ctx)
    except Exception as e:
        logger.debug("Cashflow skip: %s", e)

    # AI 月报叙事
    yield _pev(88, "AI正在生成月度经营叙事报告...")
    await progress_cb(88, "生成月报")

    ctx_parts = [f"月度指标汇总：{metrics_text}"]
    if pl_result:
        ctx_parts.append(f"P&L摘要：{str(pl_result)[:600]}")
    if bva_result:
        ctx_parts.append(f"预算差异：{str(bva_result)[:500]}")
    if scenario:
        ctx_parts.append(f"场景分析：{str(scenario)[:500]}")

    audit_report = ""
    try:
        messages = [
            {
                "role": "user",
                "content": (
                    f"请基于以下月度财务数据，生成一份完整的月度经营审计报告。\n\n"
                    f"{chr(10).join(ctx_parts)}\n\n"
                    "报告需包含：\n"
                    "1. 月度经营摘要（关键指标达成情况）\n"
                    "2. 盈利分析（毛利/净利润结构，与上月对比）\n"
                    "3. 成本分析（各成本项趋势，超支项预警）\n"
                    "4. 收入驱动因素（流量/转化/客单价的贡献拆解）\n"
                    "5. 风险点（下月需关注的3个财务风险）\n"
                    "6. 下月经营建议（3条具体可执行措施）\n"
                    "请用中文，数字精确，逻辑严谨。"
                ),
            }
        ]
        system_prompt = (
            "你是资深电商CFO级财务分析师。擅长从财务数据中发现经营问题，"
            "给出具有实战价值的改善建议。数字引用准确，禁止编造。"
        )
        audit_report = await llm_generate(messages, system=system_prompt)
    except Exception as e:
        logger.warning("LLM audit failed: %s", e)
        audit_report = "AI叙事暂不可用。财务数据已汇总，请参阅各分项结果。"

    await progress_cb(100, "月度审计完成")
    yield _pev(100, "月度审计完成")

    yield {
        "type": "result",
        "data": {
            "report": audit_report,
            "pl": pl_result,
            "budget_vs_actual": bva_result,
            "waterfall": waterfall,
            "scenario": scenario,
            "cashflow": cashflow,
            "metrics": summary,
            "month": month,
        },
    }


# ── 周报生成 ──────────────────────────────────────────────────────────────────

@register_executor("weekly_report")
async def exec_weekly_report(
    user_id: int,
    params: dict,
    progress_cb: Callable,
) -> "AsyncIterator[dict]":
    """
    周报生成：本周经营数据 + 异常检测 + 下周趋势预测 + AI叙事报告。
    结果存入 store_daily_reports 表（每周一日期）。
    """
    from datetime import datetime, timedelta
    from src.core.metrics_store import get_summary, detect_anomalies, compare_periods, format_for_prompt
    from src.llm_client import call_llm

    today = datetime.utcnow().date()
    # 本周一日期
    monday = today - timedelta(days=today.weekday())
    week_label = monday.strftime("%Y-%m-%d")

    yield _pev(10, "加载本周指标数据...")
    await progress_cb(10, "加载指标")
    summary = await get_summary(user_id, days=7)
    metrics_text = format_for_prompt(summary)

    yield _pev(25, "检测本周异常...")
    await progress_cb(25, "异常检测")
    anomalies = await detect_anomalies(user_id, days=7)
    anomaly_text = ""
    if anomalies:
        lines = [f"- {a.get('metric','')} {a.get('direction','')}了 {abs(a.get('change_pct',0)):.1f}%" for a in anomalies[:3]]
        anomaly_text = "\n".join(lines)

    yield _pev(40, "计算环比变化...")
    await progress_cb(40, "环比计算")
    gmv_cmp = {}
    orders_cmp = {}
    try:
        gmv_cmp = await compare_periods(user_id, "gmv", current_days=7, compare_days=7)
        orders_cmp = await compare_periods(user_id, "orders", current_days=7, compare_days=7)
    except Exception:
        pass

    yield _pev(60, "预测下周趋势...")
    await progress_cb(60, "趋势预测")
    forecast_text = ""
    try:
        from src.skills.data_analysis import DataDemandForecast
        fc = DataDemandForecast()
        fc_r = await fc.execute(_user_id=user_id, metric="orders", days=30, forecast_days=[7])
        if fc_r.get("预测结果"):
            forecast_text = str(fc_r["预测结果"])[:300]
    except Exception:
        pass

    yield _pev(78, "AI生成周报叙事...")
    await progress_cb(78, "生成周报")

    # 构建周报上下文
    ctx_parts = [f"本周（{week_label} 起7天）指标：\n{metrics_text}"]
    if anomaly_text:
        ctx_parts.append(f"本周异常：\n{anomaly_text}")
    if gmv_cmp.get("change_pct") is not None:
        direction = gmv_cmp.get("direction", "")
        ctx_parts.append(f"GMV环比：{direction}{abs(gmv_cmp['change_pct']):.1f}%")
    if orders_cmp.get("change_pct") is not None:
        direction = orders_cmp.get("direction", "")
        ctx_parts.append(f"订单环比：{direction}{abs(orders_cmp['change_pct']):.1f}%")
    if forecast_text:
        ctx_parts.append(f"下周预测：{forecast_text}")

    report_text = ""
    try:
        messages = [{"role": "user", "content": (
            "请生成一份简洁的本周电商经营周报（不超过400字）。\n\n"
            + "\n\n".join(ctx_parts)
            + "\n\n周报格式：\n"
            "**本周一句话总结**：\n"
            "**亮点**（1-2条）：\n"
            "**隐患**（1-2条）：\n"
            "**下周建议**（2-3条可执行操作）：\n"
            "要求：数字具体，建议可执行，禁止废话。"
        )}]
        report_text = await call_llm(
            messages,
            system="你是资深电商运营顾问，擅长用简洁语言总结经营状况并给出务实建议。",
            temperature=0.5,
            max_tokens=600,
        ) or "本周数据已汇总，AI叙事暂不可用。"
    except Exception as e:
        report_text = f"本周指标摘要：{metrics_text}\n（AI叙事生成失败：{e}）"

    # 存入 store_daily_reports 表
    try:
        import json as _json
        from src.database import get_db
        db = await get_db()
        report_data = _json.dumps({
            "type": "weekly_report",
            "week": week_label,
            "report": report_text,
            "metrics": summary,
            "anomalies": anomalies,
            "gmv_compare": gmv_cmp,
        }, ensure_ascii=False)
        updated = await db.execute(
            """UPDATE store_daily_reports
               SET data = ?, archived = 0
               WHERE user_id = ? AND report_date = ?""",
            (report_data, user_id, week_label),
        )
        if int(getattr(updated, "rowcount", 0) or 0) <= 0:
            await db.execute(
                """INSERT INTO store_daily_reports (user_id, report_date, data)
                   VALUES (?, ?, ?)""",
                (user_id, week_label, report_data),
            )
        # 写入 background_events 通知
        await db.execute(
            """INSERT INTO background_events (user_id, event_type, data, consumed)
               VALUES (?, 'weekly_report_ready', ?, 0)""",
            (user_id, _json.dumps({"date": week_label, "summary": report_text[:120]}, ensure_ascii=False)),
        )
        await db.commit()
    except Exception as e:
        logger.warning("Failed to save weekly report: %s", e)

    await progress_cb(100, "周报完成")
    yield _pev(100, "周报已生成")
    yield {
        "type": "result",
        "data": {
            "report": report_text,
            "week": week_label,
            "metrics": summary,
            "anomalies": anomalies,
        },
    }


# ── 财务深度分析 ──────────────────────────────────────────────────────────────

@register_executor("financial_deep_analysis")
async def exec_financial_deep_analysis(
    user_id: int,
    params: Dict[str, Any],
    progress_cb: Callable,
) -> AsyncIterator[Dict[str, Any]]:
    """
    财务深度分析（长任务，耗时30-90s）：
    1. 完整P&L损益报表
    2. GMV→净利润瀑布分解（13步）
    3. 三情景+蒙特卡洛风险量化
    4. 现金流预测（12周）
    5. 预算差异分析
    6. AI综合财务诊断报告
    """
    from src.core.metrics_store import get_summary, format_for_prompt
    from src.skills.registry import get_registry
    registry = get_registry()

    async def exec_skill(name, args):
        return await registry.execute(name, args, {"_user_id": user_id, "_needs_fresh_data": False})

    from src.llm_client import call_llm as llm_gen

    days = params.get("days", 30)
    base_gmv = params.get("base_gmv")  # 用户明确提供的GMV

    yield _pev(5, "加载财务数据...")
    await progress_cb(5, "加载数据")
    summary = await get_summary(user_id, days=days)
    metrics_text = format_for_prompt(summary)

    # 自动从数据中获取 base_gmv
    if not base_gmv:
        totals = summary.get("totals", {})
        base_gmv = totals.get("gmv", 0)

    # Step 1: P&L 报表
    yield _pev(15, "生成P&L损益报表...")
    await progress_cb(15, "P&L报表")
    pl = {}
    try:
        pl = await exec_skill("accounting_pl_statement", {"period": "最近30天"})
    except Exception as e:
        logger.debug("PL skip: %s", e)

    # Step 2: GMV 瀑布
    yield _pev(28, "构建GMV→净利润瀑布...")
    await progress_cb(28, "利润瀑布")
    waterfall = {}
    try:
        waterfall = await exec_skill("accounting_gmv_waterfall", {"user_id": user_id})
    except Exception as e:
        logger.debug("Waterfall skip: %s", e)

    # Step 3: 情景分析 + 蒙特卡洛
    yield _pev(42, "运行三情景+蒙特卡洛模拟（1000次）...")
    await progress_cb(42, "蒙特卡洛模拟")
    scenario = {}
    if base_gmv > 0:
        try:
            scenario = await exec_skill("accounting_scenario_analysis", {"base_gmv": base_gmv})
        except Exception as e:
            logger.debug("Scenario skip: %s", e)

    # Step 4: 现金流预测
    yield _pev(58, "生成12周现金流预测...")
    await progress_cb(58, "现金流预测")
    cashflow = {}
    try:
        cashflow = await exec_skill("accounting_cash_flow_forecast", {})
    except Exception as e:
        logger.debug("Cashflow skip: %s", e)

    # Step 5: 预算差异
    yield _pev(70, "对比预算与实际...")
    await progress_cb(70, "预算差异")
    bva = {}
    try:
        bva = await exec_skill("accounting_budget_vs_actual", {})
    except Exception as e:
        logger.debug("BVA skip: %s", e)

    # Step 6: AI 深度财务诊断
    yield _pev(85, "AI生成综合财务诊断报告...")
    await progress_cb(85, "生成报告")

    ctx_parts = [f"店铺指标（{days}天）：{metrics_text}"]
    if pl.get("净利润") or pl.get("营业收入"):
        ctx_parts.append(f"P&L摘要：净利润={pl.get('净利润','N/A')}，毛利率={pl.get('毛利率','N/A')}")
    if waterfall.get("GMV利润瀑布"):
        wf_steps = waterfall["GMV利润瀑布"]
        ctx_parts.append(f"利润瀑布：共{len(wf_steps)}步，最终净利润={wf_steps[-1].get('金额','N/A') if wf_steps else 'N/A'}")
    if scenario.get("蒙特卡洛风险分析"):
        mc = scenario["蒙特卡洛风险分析"]
        ctx_parts.append(
            f"蒙特卡洛（1000次）：期望净利润={mc.get('期望净利润','N/A')}，"
            f"亏损概率={mc.get('亏损概率','N/A')}，P10={mc.get('P10（最差10%情景净利润）','N/A')}"
        )
    if scenario.get("三情景P&L对比"):
        for s in scenario["三情景P&L对比"]:
            ctx_parts.append(f"情景 {s.get('情景','')}：净利润={s.get('税后净利润','N/A')}")
    if bva.get("总差异"):
        ctx_parts.append(f"预算差异：{str(bva)[:400]}")

    report = ""
    try:
        report = await llm_gen(
            [{"role": "user", "content": (
                "请基于以下财务数据，生成一份深度财务诊断报告，要求：\n"
                "1. 盈利能力评估（毛利率/净利率健康度，与行业对比）\n"
                "2. 风险量化（亏损概率、尾部风险P10场景、最大潜在损失）\n"
                "3. 成本结构优化（找出最大漏损点，给出压缩方案）\n"
                "4. 情景策略（在乐观/悲观情景下应采取的不同行动）\n"
                "5. 现金流安全窗口（基于预测，资金安全期多久）\n"
                "6. 优先行动清单（3条最高ROI的财务优化行动，附量化预期改善）\n\n"
                f"数据：\n{chr(10).join(ctx_parts)}\n\n"
                "用中文，数字精确，避免空话。"
            )}],
            system="你是资深CFO级电商财务分析师，专注于从P&L和现金流数据中发现盈利改善机会，量化风险敞口。"
        )
    except Exception as e:
        logger.warning("LLM financial report failed: %s", e)
        report = "AI叙事暂不可用，财务数据已计算完成，请参阅各分项结果。"

    await progress_cb(100, "财务深度分析完成")
    yield _pev(100, "财务深度分析完成")
    yield {
        "type": "result",
        "data": {
            "report": report,
            "pl": pl,
            "waterfall": waterfall,
            "scenario": scenario,
            "cashflow": cashflow,
            "budget_vs_actual": bva,
            "metrics": summary,
            "base_gmv": base_gmv,
        },
    }


# ── 数据深度分析 ──────────────────────────────────────────────────────────────

@register_executor("data_deep_analysis")
async def exec_data_deep_analysis(
    user_id: int,
    params: Dict[str, Any],
    progress_cb: Callable,
) -> AsyncIterator[Dict[str, Any]]:
    """
    数据深度分析（长任务，耗时60-120s）：
    1. 指标看板快照
    2. 异常检测
    3. RFM客户分层（11类）
    4. 转化漏斗分析
    5. 渠道归因（4种模型）
    6. 需求预测（7/14/30天）
    7. LTV + 队列留存
    8. AI综合数据洞察报告
    """
    from src.core.metrics_store import get_summary, detect_anomalies, format_for_prompt
    from src.skills.registry import get_registry
    registry = get_registry()

    async def exec_skill(name, args):
        return await registry.execute(name, args, {"_user_id": user_id, "_needs_fresh_data": False})

    from src.llm_client import call_llm as llm_gen

    days = params.get("days", 30)

    # Step 1: 指标看板
    yield _pev(5, "加载数据看板指标...")
    await progress_cb(5, "加载指标")
    summary = await get_summary(user_id, days=days)
    metrics_text = format_for_prompt(summary)
    dashboard = {}
    try:
        dashboard = await exec_skill("data_dashboard", {"days": days})
    except Exception as e:
        logger.debug("Dashboard skip: %s", e)

    # Step 2: 异常检测
    yield _pev(14, "检测异常指标...")
    await progress_cb(14, "异常检测")
    anomalies = await detect_anomalies(user_id, days=days)
    anomaly_text = ""
    if anomalies:
        anomaly_text = "\n".join(
            f"- {a.get('metric','')} {a.get('direction','')} {abs(a.get('change_pct',0)):.1f}%"
            for a in anomalies[:5]
        )

    # Step 3: RFM 客户分层
    yield _pev(24, "RFM客户分层分析（11类）...")
    await progress_cb(24, "客户分层")
    rfm = {}
    try:
        rfm = await exec_skill("data_customer_segmentation", {"days": days})
    except Exception as e:
        logger.debug("RFM skip: %s", e)

    # Step 4: 转化漏斗
    yield _pev(36, "分析转化漏斗...")
    await progress_cb(36, "漏斗分析")
    funnel = {}
    try:
        funnel = await exec_skill("data_funnel_analysis", {"days": days})
    except Exception as e:
        logger.debug("Funnel skip: %s", e)

    # Step 5: 渠道归因
    yield _pev(48, "运行多模型渠道归因...")
    await progress_cb(48, "渠道归因")
    attribution = {}
    try:
        attribution = await exec_skill("data_attribution_analysis", {"days": days})
    except Exception as e:
        logger.debug("Attribution skip: %s", e)

    # Step 6: 需求预测
    yield _pev(60, "生成7/14/30天需求预测...")
    await progress_cb(60, "需求预测")
    forecast = {}
    try:
        forecast = await exec_skill("data_demand_forecast", {"metric": "orders", "days": days})
    except Exception as e:
        logger.debug("Forecast skip: %s", e)

    # Step 7: LTV + 队列
    yield _pev(73, "计算LTV和队列留存率...")
    await progress_cb(73, "LTV分析")
    ltv = {}
    cohort = {}
    try:
        ltv = await exec_skill("data_ltv_calculator", {"days": days})
        cohort = await exec_skill("data_cohort_analysis", {"days": days})
    except Exception as e:
        logger.debug("LTV/Cohort skip: %s", e)

    # Step 8: AI 综合洞察
    yield _pev(87, "AI生成综合数据洞察报告...")
    await progress_cb(87, "生成报告")

    ctx_parts = [f"店铺数据快照（{days}天）：{metrics_text}"]
    if anomaly_text:
        ctx_parts.append(f"异常指标：\n{anomaly_text}")
    if rfm.get("客群分层（11类）"):
        segs = rfm["客群分层（11类）"]
        top3 = sorted(segs, key=lambda x: x.get("客户数", 0), reverse=True)[:3]
        top3_str = ", ".join(f"{s['客群标签']}({s['客户数']}人)" for s in top3)
        ctx_parts.append(f"TOP3客群：{top3_str}")
    if funnel.get("整体转化率"):
        ctx_parts.append(f"整体转化率：{funnel['整体转化率']}")
    if attribution.get("各渠道归因明细"):
        ch_list = attribution["各渠道归因明细"][:3]
        ctx_parts.append(f"TOP渠道：{', '.join(str(c.get('渠道','')) + '=' + str(c.get('实际GMV占比','')) for c in ch_list)}")
    if forecast.get("预测结果"):
        ctx_parts.append(f"需求预测：{str(forecast['预测结果'])[:200]}")
    if ltv.get("平均LTV"):
        ctx_parts.append(f"LTV={ltv['平均LTV']}，LTV/CAC={ltv.get('LTV_CAC比率','N/A')}")
    if cohort.get("第1期留存率"):
        ctx_parts.append(f"30天留存率：{cohort['第1期留存率']}")

    report = ""
    try:
        report = await llm_gen(
            [{"role": "user", "content": (
                "请基于以下电商数据，生成一份深度数据洞察报告，要求：\n"
                "1. 业务健康度总评（0-100分，核心依据）\n"
                "2. 用户画像洞察（基于RFM分层，最值得关注的客群机会）\n"
                "3. 转化漏斗诊断（找出最大漏损节点，给出改善方向）\n"
                "4. 渠道效能矩阵（哪些渠道被低估？哪些在烧钱）\n"
                "5. 增长预测与警示（未来30天趋势，需要提前准备的备货/推广计划）\n"
                "6. LTV优化路径（如何将低价值用户升级为高价值用户）\n"
                "7. 数据驱动行动清单（5条最高优先级的数据改善行动）\n\n"
                f"数据：\n{chr(10).join(ctx_parts)}\n\n"
                "用中文，引用具体数字，避免空话。"
            )}],
            system="你是资深电商数据科学家，擅长从多维度数据中发现增长机会和风险点，给出可量化的优化建议。"
        )
    except Exception as e:
        logger.warning("LLM data report failed: %s", e)
        report = "AI洞察暂不可用，数据分析已完成，请参阅各分项结果。"

    await progress_cb(100, "数据深度分析完成")
    yield _pev(100, "数据深度分析完成")
    yield {
        "type": "result",
        "data": {
            "report": report,
            "dashboard": dashboard,
            "anomalies": anomalies,
            "rfm": rfm,
            "funnel": funnel,
            "attribution": attribution,
            "forecast": forecast,
            "ltv": ltv,
            "cohort": cohort,
            "metrics": summary,
            "days": days,
        },
    }
