from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List

from .base import SkillBase
from ._db_helpers import load_platform_connections, load_metrics_summary
from ._content_engine import (
    generate_architecture_review,
    generate_bug_diagnosis,
    generate_performance_plan,
    generate_tech_selection,
    generate_sla_interpretation,
)


class EngineeringArchReview(SkillBase):
    """架构评估技能 — LLM深度评审，含评分/风险/改造路径"""

    def __init__(self) -> None:
        super().__init__(
            name="engineering_arch_review",
            display_name="架构评估",
            description="AI深度评审系统架构，输出5维评分/核心风险/分级改造方案/大促备战清单",
            category="engineering",
            input_schema={
                "type": "object",
                "properties": {
                    "system_type": {
                        "type": "string",
                        "description": "系统类型：电商平台/ERP/CRM/数据平台/小程序",
                    },
                    "daily_orders": {"type": "integer", "description": "日均订单量"},
                    "tech_stack": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "技术栈列表（如 Python/MySQL/Redis/Nginx）",
                    },
                    "pain_points": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "当前主要痛点",
                    },
                    "current_metrics": {
                        "type": "object",
                        "description": "当前系统指标，如 {响应时间: '200ms', QPS: 500}",
                    },
                },
                "required": ["system_type"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        system_type: str = kwargs.get("system_type", "电商平台")
        daily_orders: int = kwargs.get("daily_orders", 1000)
        tech_stack: List[str] = kwargs.get("tech_stack", [])
        pain_points: List[str] = kwargs.get("pain_points", [])
        current_metrics: Dict[str, Any] = kwargs.get("current_metrics", {})

        # 补充：从真实数据加载 GMV/订单量，增强上下文
        if user_id and not daily_orders:
            summary = await load_metrics_summary(user_id, days=30)
            if summary.get("has_data"):
                orders = summary.get("totals", {}).get("orders", 0)
                if orders:
                    daily_orders = int(orders / 30)
                    current_metrics["月均GMV"] = f"¥{summary['totals'].get('gmv', 0):,.0f}"

        result = await generate_architecture_review(
            tech_stack=", ".join(tech_stack) if tech_stack else "",
            daily_orders=daily_orders,
            pain_points=pain_points,
            current_metrics=current_metrics,
        )
        result["系统类型"] = system_type
        return result


class EngineeringBugAnalysis(SkillBase):
    """Bug排查方案技能 — LLM生成深度诊断（根因/排查步骤/修复代码/预防）"""

    def __init__(self) -> None:
        super().__init__(
            name="engineering_bug_analysis",
            display_name="Bug排查方案",
            description="AI深度诊断Bug，输出根因分析/10步排查流程/修复代码示例/临时止血方案/预防措施",
            category="engineering",
            input_schema={
                "type": "object",
                "properties": {
                    "error_type": {
                        "type": "string",
                        "description": "错误类型：接口超时/数据异常/页面白屏/支付失败/库存不一致/OOM/死锁等",
                    },
                    "error_message": {"type": "string", "description": "完整错误信息或日志片段"},
                    "frequency": {"type": "string", "description": "频率：偶发/频繁/必现"},
                    "tech_stack": {"type": "string", "description": "技术栈，如 Python/FastAPI/MySQL"},
                    "context": {"type": "string", "description": "发生场景（如大促/特定操作/定时任务）"},
                },
                "required": ["error_type"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        error_type: str = kwargs.get("error_type", "接口超时")
        error_msg: str = kwargs.get("error_message", "")
        frequency: str = kwargs.get("frequency", "偶发")
        tech_stack: str = kwargs.get("tech_stack", "")
        context: str = kwargs.get("context", "")

        # 搜索已知解决方案
        bug_search_ctx = ""
        if kwargs.get("_needs_fresh_data", True):
            try:
                from .search import _web_search, _format_results_for_llm
                stack_hint = tech_stack or "Python"
                q = f"{error_type} {stack_hint} 解决方案 排查 最佳实践"
                results, _ = await _web_search(q, topic="general", max_results=4, days=90)
                if results:
                    bug_search_ctx = _format_results_for_llm(results, max_per_item=300)
            except Exception:
                pass

        result = await generate_bug_diagnosis(
            error_type=error_type,
            error_message=error_msg,
            tech_stack=tech_stack,
            frequency=frequency,
            context=context,
            search_context=bug_search_ctx,
        )
        result["紧急程度"] = "紧急" if frequency == "必现" else ("较急" if frequency == "频繁" else "一般")
        return result


class EngineeringPerfOptimize(SkillBase):
    """性能优化方案技能 — LLM生成含代码/配置的具体优化步骤"""

    def __init__(self) -> None:
        super().__init__(
            name="engineering_perf_optimize",
            display_name="性能优化方案",
            description="AI生成具体性能优化方案，含代码示例/配置参数/分阶段实施计划/压测脚本",
            category="engineering",
            input_schema={
                "type": "object",
                "properties": {
                    "bottleneck": {
                        "type": "string",
                        "description": "瓶颈类型：数据库/接口/前端/缓存/并发/网络",
                    },
                    "current_qps": {"type": "number", "description": "当前QPS/TPS"},
                    "target_qps": {"type": "number", "description": "目标QPS/TPS"},
                    "tech_stack": {"type": "string", "description": "技术栈"},
                    "metrics": {
                        "type": "object",
                        "description": "当前系统指标，如 {P99延迟: '1200ms', CPU使用率: '85%'}",
                    },
                },
                "required": ["bottleneck"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        bottleneck: str = kwargs.get("bottleneck", "数据库")
        current_qps: float = kwargs.get("current_qps", 500)
        target_qps: float = kwargs.get("target_qps", 2000)
        tech_stack: str = kwargs.get("tech_stack", "")
        metrics: Dict[str, Any] = kwargs.get("metrics", {})

        if kwargs.get("p99_latency_ms"):
            metrics["P99延迟"] = f"{kwargs['p99_latency_ms']}ms"

        return await generate_performance_plan(
            bottleneck=bottleneck,
            current_qps=current_qps,
            target_qps=target_qps,
            tech_stack=tech_stack,
            metrics=metrics,
        )


class EngineeringTechSelection(SkillBase):
    """技术选型技能 — LLM生成含真实基准数据的深度对比报告"""

    def __init__(self) -> None:
        super().__init__(
            name="engineering_tech_selection",
            display_name="技术选型",
            description="AI生成技术选型深度报告，含真实性能数据对比/迁移方案/POC验证清单",
            category="engineering",
            input_schema={
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": "选型领域：Web框架/数据库/消息队列/搜索引擎/缓存/容器编排/监控",
                    },
                    "requirements": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "核心需求列表",
                    },
                    "constraints": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "约束条件（预算/团队技能/合规/时间）",
                    },
                    "team_size": {"type": "integer", "description": "团队规模"},
                    "current_stack": {"type": "string", "description": "现有技术栈（迁移评估用）"},
                },
                "required": ["domain"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        domain: str = kwargs.get("domain", "数据库")
        requirements: List[str] = kwargs.get("requirements", [])
        constraints: List[str] = kwargs.get("constraints", [])
        team_size: int = kwargs.get("team_size", 5)
        current_stack: str = kwargs.get("current_stack", "")

        # 搜索实时技术对比数据
        tech_search_ctx = ""
        if kwargs.get("_needs_fresh_data", True):
            try:
                from .search import _web_search, _format_results_for_llm
                q = f"{domain} 技术选型 对比 性能 2026"
                results, _ = await _web_search(q, topic="general", max_results=5, days=90)
                if results:
                    tech_search_ctx = _format_results_for_llm(results, max_per_item=300)
            except Exception:
                pass

        return await generate_tech_selection(
            domain=domain,
            requirements=requirements,
            constraints=constraints,
            team_size=team_size,
            current_stack=current_stack,
            search_context=tech_search_ctx,
        )


class EngineeringSystemCheck(SkillBase):
    """平台接口健康检查技能 — 检查真实已配置平台的连接状态"""

    def __init__(self) -> None:
        super().__init__(
            name="engineering_system_check",
            display_name="平台接口健康检查",
            description="检查用户已配置的电商平台API连接状态，诊断同步和接口问题",
            category="engineering",
            input_schema={
                "type": "object",
                "properties": {
                    "test_live": {
                        "type": "boolean",
                        "description": "是否实际调用平台API测试连通性（默认仅检查配置）",
                    },
                    "platform": {
                        "type": "string",
                        "description": "指定平台（留空则检查全部已配置平台）",
                    },
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        test_live: bool = kwargs.get("test_live", False)
        target_platform: str = kwargs.get("platform", "")

        check_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # ── 从数据库加载真实连接配置 ──
        connections = await load_platform_connections(user_id)

        if target_platform:
            connections = [c for c in connections if c["platform"] == target_platform]

        platform_display = {
            "taobao": "淘宝/天猫",
            "jd": "京东",
            "pdd": "拼多多",
            "douyin": "抖音",
        }

        platform_status: Dict[str, Any] = {}

        for conn in connections:
            pname = conn["platform"]
            display = platform_display.get(pname, pname)
            last_sync = conn.get("last_sync_at") or "从未同步"

            status: Dict[str, Any] = {
                "平台": display,
                "配置状态": "已配置",
                "启用状态": "启用" if conn.get("enabled") else "已禁用",
                "最后同步": last_sync,
                "接口状态": "待检测",
            }

            if test_live and conn.get("enabled"):
                try:
                    from src.database import get_db
                    db = await get_db()
                    from src.services.platform_adapters import get_platform_registry
                    registry = await get_platform_registry(user_id, db)
                    adapter = registry.get(pname)
                    if adapter:
                        success, msg = await adapter.test_connection()
                        status["接口状态"] = "正常" if success else f"异常: {msg}"
                    else:
                        status["接口状态"] = "适配器未加载"
                except Exception as e:
                    status["接口状态"] = f"检测失败: {str(e)[:50]}"
            elif not conn.get("enabled"):
                status["接口状态"] = "已禁用（跳过）"

            platform_status[pname] = status

        if not platform_status:
            return {
                "检查时间": check_time,
                "已配置平台数": 0,
                "状态": "未配置任何平台",
                "建议": [
                    "前往「平台连接」页面配置淘宝/京东/拼多多/抖音的API密钥",
                    "配置完成后可调用本工具检测连通性",
                    "也可通过数据导入CSV来上传历史数据",
                ],
            }

        enabled_count = sum(1 for s in platform_status.values() if s["启用状态"] == "启用")
        api_ok = sum(1 for s in platform_status.values() if s["接口状态"] == "正常")

        result: Dict[str, Any] = {
            "检查时间": check_time,
            "已配置平台数": len(platform_status),
            "已启用": enabled_count,
            "平台详情": platform_status,
        }

        if test_live:
            result["接口正常数"] = api_ok
            result["接口异常数"] = enabled_count - api_ok

        suggestions = []
        for pname, s in platform_status.items():
            if s.get("最后同步") == "从未同步":
                suggestions.append(f"{s['平台']}从未同步，建议执行一次数据同步")
            if "异常" in str(s.get("接口状态", "")):
                suggestions.append(f"{s['平台']}接口异常，请检查API密钥是否有效")

        if suggestions:
            result["建议"] = suggestions

        return result


class EngineeringSLAMonitor(SkillBase):
    """SLA监控技能 — P50/P90/P99延迟 + 可用性 + 错误预算消耗率"""

    # SLO默认目标（电商系统典型值）
    _DEFAULT_SLO = {
        "availability": 99.9,       # 月可用性 99.9% = 43.8min/月宕机预算
        "p50_ms": 200,              # P50延迟目标
        "p90_ms": 500,              # P90延迟目标
        "p99_ms": 2000,             # P99延迟目标（长尾）
    }

    def __init__(self) -> None:
        super().__init__(
            name="engineering_sla_monitor",
            display_name="SLA监控",
            description="P50/P90/P99延迟分位数分析 + 可用性SLO监控 + 错误预算消耗率计算（按月/按季），识别SLO违规项，大促前风险预判",
            category="engineering",
            input_schema={
                "type": "object",
                "properties": {
                    "latency_samples": {
                        "type": "array",
                        "items": {"type": "number"},
                        "description": "延迟样本列表（毫秒），至少10个数据点",
                    },
                    "p50_ms": {"type": "number", "description": "P50延迟（毫秒），与latency_samples二选一"},
                    "p90_ms": {"type": "number", "description": "P90延迟（毫秒）"},
                    "p99_ms": {"type": "number", "description": "P99延迟（毫秒）"},
                    "total_requests": {"type": "integer", "description": "统计周期内总请求数"},
                    "error_count": {"type": "integer", "description": "错误请求数"},
                    "downtime_minutes": {"type": "number", "description": "本月宕机分钟数"},
                    "period_days": {"type": "integer", "description": "统计周期天数，默认30天"},
                    "slo_availability": {"type": "number", "description": "可用性SLO目标（%），默认99.9"},
                    "slo_p99_ms": {"type": "number", "description": "P99延迟SLO目标（毫秒），默认2000"},
                    "system_name": {"type": "string", "description": "系统名称"},
                },
                "required": [],
            },
        )

    def _percentile(self, data: List[float], pct: float) -> float:
        """计算百分位数（线性插值）。"""
        if not data:
            return 0.0
        sorted_data = sorted(data)
        n = len(sorted_data)
        idx = (pct / 100) * (n - 1)
        lo = int(idx)
        hi = min(lo + 1, n - 1)
        frac = idx - lo
        return sorted_data[lo] * (1 - frac) + sorted_data[hi] * frac

    async def execute(self, **kwargs) -> Dict[str, Any]:
        samples: List[float] = [float(x) for x in kwargs.get("latency_samples", [])]
        period_days: int = kwargs.get("period_days", 30)
        system_name: str = kwargs.get("system_name", "电商系统")
        total_requests: int = kwargs.get("total_requests", 0)
        error_count: int = kwargs.get("error_count", 0)
        downtime_min: float = kwargs.get("downtime_minutes", 0.0)
        slo_avail: float = kwargs.get("slo_availability", self._DEFAULT_SLO["availability"])
        slo_p99: float = kwargs.get("slo_p99_ms", self._DEFAULT_SLO["p99_ms"])

        # ── 计算延迟分位数 ──
        if samples:
            p50 = self._percentile(samples, 50)
            p90 = self._percentile(samples, 90)
            p99 = self._percentile(samples, 99)
        else:
            p50 = float(kwargs.get("p50_ms", 150))
            p90 = float(kwargs.get("p90_ms", 400))
            p99 = float(kwargs.get("p99_ms", 1800))

        # ── 可用性计算 ──
        period_minutes = period_days * 24 * 60
        if downtime_min > 0:
            availability = round((1 - downtime_min / period_minutes) * 100, 4)
        elif total_requests > 0 and error_count >= 0:
            # 用请求成功率近似可用性
            availability = round((1 - error_count / total_requests) * 100, 4) if total_requests else 99.9
        else:
            availability = 99.9  # 无数据时假设正常

        # ── 错误预算 ──
        # 错误预算 = 允许的不可用时间 = (1 - SLO) × 统计周期
        error_budget_total_min = (1 - slo_avail / 100) * period_minutes
        error_budget_consumed_min = downtime_min
        if error_budget_total_min > 0:
            error_budget_consumed_pct = round(error_budget_consumed_min / error_budget_total_min * 100, 1)
        else:
            error_budget_consumed_pct = 0.0
        error_budget_remaining_pct = round(100 - error_budget_consumed_pct, 1)

        # ── SLO违规检查 ──
        slo_checks = [
            {
                "name": "P99延迟",
                "target_ms": slo_p99,
                "actual_ms": p99,
                "passed": p99 <= slo_p99,
                "overshoot_pct": round((p99 - slo_p99) / slo_p99 * 100, 1) if p99 > slo_p99 else 0.0,
            },
            {
                "name": "P90延迟",
                "target_ms": self._DEFAULT_SLO["p90_ms"],
                "actual_ms": p90,
                "passed": p90 <= self._DEFAULT_SLO["p90_ms"],
                "overshoot_pct": round((p90 - self._DEFAULT_SLO["p90_ms"]) / self._DEFAULT_SLO["p90_ms"] * 100, 1) if p90 > self._DEFAULT_SLO["p90_ms"] else 0.0,
            },
            {
                "name": "可用性",
                "target_ms": slo_avail,
                "actual_ms": availability,
                "passed": availability >= slo_avail,
                "overshoot_pct": 0.0,
            },
        ]
        violations = [c for c in slo_checks if not c["passed"]]

        # ── 大促风险评估 ──
        # P99偏高表明长尾请求问题，大促流量 × 10 时 P99 可能更差
        peak_p99_est = p99 * 2.5  # 保守估计大促峰值
        promo_risk = (
            "🔴 高风险" if peak_p99_est > slo_p99 * 3
            else "🟠 中风险" if peak_p99_est > slo_p99 * 1.5
            else "🟢 低风险"
        )

        result: Dict[str, Any] = {
            "系统": system_name,
            "统计周期": f"{period_days}天",
            "延迟分位数": {
                "P50（中位数）": f"{p50:.0f}ms",
                "P90（90%用户体验）": f"{p90:.0f}ms",
                "P99（长尾体验）": f"{p99:.0f}ms",
                "目标P99": f"{slo_p99:.0f}ms",
                "P99状态": "✅ 达标" if p99 <= slo_p99 else f"❌ 超标{round((p99-slo_p99)/slo_p99*100,1)}%",
            },
            "可用性": {
                "实际可用性": f"{availability:.3f}%",
                "SLO目标": f"{slo_avail:.3f}%",
                "本期宕机": f"{downtime_min:.1f}分钟",
                "状态": "✅ 达标" if availability >= slo_avail else "❌ 未达标",
            },
            "错误预算": {
                "本期可用预算": f"{error_budget_total_min:.1f}分钟",
                "已消耗": f"{error_budget_consumed_min:.1f}分钟 ({error_budget_consumed_pct}%)",
                "剩余预算": f"{error_budget_remaining_pct}%",
                "预算状态": (
                    "🔴 预算耗尽" if error_budget_remaining_pct <= 0
                    else "🟠 预算紧张" if error_budget_remaining_pct < 20
                    else "🟡 预算警告" if error_budget_remaining_pct < 50
                    else "🟢 预算充足"
                ),
            },
            "SLO达标情况": [
                {
                    "指标": c["name"],
                    "目标": c["target_ms"],
                    "实际": c["actual_ms"],
                    "状态": "✅ 达标" if c["passed"] else f"❌ 超标{c['overshoot_pct']}%",
                }
                for c in slo_checks
            ],
            "SLO违规项数": len(violations),
            "大促峰值风险": {
                "预估大促P99": f"{peak_p99_est:.0f}ms（当前×2.5倍流量）",
                "风险等级": promo_risk,
            },
        }

        if total_requests:
            result["请求统计"] = {
                "总请求数": total_requests,
                "错误数": error_count,
                "错误率": f"{error_count/total_requests*100:.3f}%",
            }

        # LLM解读
        try:
            interpretation = await generate_sla_interpretation(
                p50=p50, p90=p90, p99=p99,
                availability=availability,
                error_budget_pct=error_budget_remaining_pct,
                slo_target=slo_avail,
                violations=violations,
                platform=system_name,
            )
            if interpretation:
                result["AI诊断建议"] = interpretation
        except Exception:
            pass

        return result


ALL_SKILLS: list[SkillBase] = [
    EngineeringArchReview(),
    EngineeringBugAnalysis(),
    EngineeringPerfOptimize(),
    EngineeringTechSelection(),
    EngineeringSystemCheck(),
    EngineeringSLAMonitor(),
]
