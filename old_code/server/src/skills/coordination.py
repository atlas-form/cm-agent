from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, List

from .base import SkillBase


class CoordinationAgentHandoff(SkillBase):
    """Agent交接技能"""

    def __init__(self) -> None:
        super().__init__(
            name="coordination_agent_handoff",
            display_name="Agent交接",
            description="根据任务需求，确定应交接的目标Agent及交接信息",
            category="coordination",
            input_schema={
                "type": "object",
                "properties": {
                    "current_agent": {"type": "string", "description": "当前Agent角色"},
                    "task_description": {"type": "string", "description": "需交接的任务描述"},
                    "context": {"type": "string", "description": "上下文信息"},
                },
                "required": ["task_description"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        current = kwargs.get("current_agent", "unknown")
        task = kwargs.get("task_description", "")
        context = kwargs.get("context", "")

        # Rule-based agent matching
        keyword_map = {
            "ops": ["运营", "促销", "活动", "推广", "投放", "选品", "定价", "库存"],
            "data": ["数据", "分析", "报表", "漏斗", "转化率", "趋势", "异常", "指标"],
            "service": ["客服", "工单", "投诉", "退款", "退货", "售后", "DSR", "评价"],
            "design": ["设计", "主图", "详情页", "配色", "海报", "视觉", "素材"],
            "accounting": ["财务", "成本", "利润", "预算", "ROI", "税务", "合规"],
            "engineering": ["技术", "架构", "bug", "性能", "部署", "开发", "接口"],
            "web": ["SEO", "网站", "店铺装修", "关键词", "标题", "转化", "建站"],
            "creative": ["视频", "文案", "种草", "直播", "IP", "内容", "创意"],
        }

        agent_names = {
            "ops": "运营专家",
            "data": "数据分析师",
            "service": "客服专家",
            "design": "设计师",
            "accounting": "财务分析师",
            "engineering": "技术工程师",
            "web": "SEO专家",
            "creative": "内容创作者",
        }

        matched_agents = []
        for agent, keywords in keyword_map.items():
            if agent == current:
                continue
            score = sum(1 for kw in keywords if kw in task)
            if score > 0:
                matched_agents.append({"agent": agent, "name": agent_names[agent], "匹配度": score})

        matched_agents.sort(key=lambda x: x["匹配度"], reverse=True)

        if not matched_agents:
            matched_agents = [{"agent": "ops", "name": "运营专家", "匹配度": 0}]

        target = matched_agents[0]

        return {
            "当前Agent": current,
            "任务描述": task,
            "推荐交接": {
                "目标Agent": target["agent"],
                "Agent名称": target["name"],
                "匹配度": target["匹配度"],
            },
            "备选Agent": matched_agents[1:3] if len(matched_agents) > 1 else [],
            "交接信息": {
                "任务摘要": task,
                "上下文": context or "无额外上下文",
                "优先级": "中",
                "创建时间": time.strftime("%Y-%m-%d %H:%M:%S"),
            },
        }


class CoordinationTaskOrchestration(SkillBase):
    """任务编排技能"""

    def __init__(self) -> None:
        super().__init__(
            name="coordination_task_orchestration",
            display_name="任务编排",
            description="将复杂需求拆分为多Agent协作的子任务，确定执行顺序",
            category="coordination",
            input_schema={
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "整体目标"},
                    "requirements": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "具体需求列表",
                    },
                },
                "required": ["goal"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        import json as _json
        goal = kwargs.get("goal", "")
        requirements: List[str] = kwargs.get("requirements", [])
        role = kwargs.get("_role", "ops")

        req_text = "\n".join(f"- {r}" for r in requirements) if requirements else "（无额外要求）"

        # ── LLM动态任务分解 ──────────────────────────────────────────────────
        tasks = []
        critical_path = ""
        risk_tips: List[str] = []
        llm_raw = ""

        try:
            from ._content_engine import _call
            system = (
                "你是电商运营总监，擅长将复杂目标拆解为多职能团队的协作任务。\n"
                "可用的Agent角色：ops(运营) | data(数据分析) | service(客服) | design(设计) | "
                "accounting(财务) | engineering(技术) | web(SEO) | creative(内容创作)\n"
                "职责边界：\n"
                "- creative: 短视频脚本/种草文案/直播话术/品牌内容\n"
                "- design: 主图/详情页/海报/视觉规范\n"
                "- ops: 运营策略/定价/促销活动/渠道投放\n"
                "- data: 数据分析/RFM/漏斗/预测/诊断\n"
                "- accounting: 预算/成本/P&L/现金流\n"
                "- web: SEO/关键词/店铺装修\n"
                "- service: 客服话术/售后/NPS\n"
                "- engineering: 技术开发/系统/性能\n"
            )
            prompt = (
                f"目标：{goal}\n额外要求：\n{req_text}\n\n"
                "请将此目标拆分为5-8个子任务，返回JSON格式（直接输出JSON，不要前言）：\n"
                "{\n"
                '  "任务编排": [\n'
                '    {"序号": 1, "任务": "...", "Agent": "data", "耗时": "1天", "依赖": "无", "目标产出": "..."},\n'
                "    ...\n"
                "  ],\n"
                '  "关键路径": "任务1 → 任务2 → ...",\n'
                '  "风险提示": ["风险1", "风险2"]\n'
                "}"
            )
            llm_raw = await asyncio.wait_for(_call(system, prompt, max_tokens=800, temperature=0.2), timeout=8.0)

            # 解析 JSON
            start = llm_raw.find("{")
            end = llm_raw.rfind("}") + 1
            if start >= 0 and end > start:
                parsed = _json.loads(llm_raw[start:end])
                tasks = parsed.get("任务编排", [])
                critical_path = parsed.get("关键路径", "")
                risk_tips = parsed.get("风险提示", [])
        except Exception:
            pass

        # 如果 LLM 失败，使用场景模板兜底
        if not tasks:
            _TEMPLATES = {
                "新品上市": [
                    {"序号": 1, "任务": "竞品分析与市场调研", "Agent": "data", "耗时": "2天", "依赖": "无", "目标产出": "竞品报告"},
                    {"序号": 2, "任务": "定价策略与成本核算", "Agent": "accounting", "耗时": "1天", "依赖": "任务1", "目标产出": "定价方案"},
                    {"序号": 3, "任务": "主图与详情页设计", "Agent": "design", "耗时": "3天", "依赖": "任务1", "目标产出": "设计稿"},
                    {"序号": 4, "任务": "商品文案与SEO标题", "Agent": "web", "耗时": "1天", "依赖": "任务3", "目标产出": "优化文案"},
                    {"序号": 5, "任务": "种草内容与脚本", "Agent": "creative", "耗时": "2天", "依赖": "任务3", "目标产出": "内容素材"},
                    {"序号": 6, "任务": "客服话术准备", "Agent": "service", "耗时": "1天", "依赖": "任务4", "目标产出": "话术库"},
                    {"序号": 7, "任务": "上线推广与监控", "Agent": "ops", "耗时": "持续", "依赖": "任务4,5,6", "目标产出": "上线运营"},
                ],
                "大促活动": [
                    {"序号": 1, "任务": "历史数据分析与目标设定", "Agent": "data", "耗时": "1天", "依赖": "无", "目标产出": "数据报告"},
                    {"序号": 2, "任务": "活动策划与预算分配", "Agent": "ops", "耗时": "2天", "依赖": "任务1", "目标产出": "活动方案"},
                    {"序号": 3, "任务": "财务预算与盈亏测算", "Agent": "accounting", "耗时": "1天", "依赖": "任务2", "目标产出": "预算报告"},
                    {"序号": 4, "任务": "活动海报与素材设计", "Agent": "design", "耗时": "2天", "依赖": "任务2", "目标产出": "视觉素材"},
                    {"序号": 5, "任务": "直播脚本与种草内容", "Agent": "creative", "耗时": "1天", "依赖": "任务2", "目标产出": "内容脚本"},
                    {"序号": 6, "任务": "客服应急话术与预案", "Agent": "service", "耗时": "1天", "依赖": "任务2", "目标产出": "应急手册"},
                    {"序号": 7, "任务": "技术压测与系统保障", "Agent": "engineering", "耗时": "1天", "依赖": "任务2", "目标产出": "压测报告"},
                ],
            }
            for key, tmpl in _TEMPLATES.items():
                if key in goal:
                    tasks = tmpl
                    break
            if not tasks:
                tasks = [
                    {"序号": 1, "任务": "现状分析与数据调研", "Agent": "data", "耗时": "1天", "依赖": "无", "目标产出": "分析报告"},
                    {"序号": 2, "任务": "策略与方案制定", "Agent": "ops", "耗时": "1天", "依赖": "任务1", "目标产出": "执行方案"},
                    {"序号": 3, "任务": "财务可行性评估", "Agent": "accounting", "耗时": "1天", "依赖": "任务2", "目标产出": "财务测算"},
                    {"序号": 4, "任务": "视觉设计", "Agent": "design", "耗时": "2天", "依赖": "任务2", "目标产出": "设计稿"},
                    {"序号": 5, "任务": "内容与文案制作", "Agent": "creative", "耗时": "2天", "依赖": "任务2", "目标产出": "内容素材"},
                    {"序号": 6, "任务": "执行落地与效果监控", "Agent": "ops", "耗时": "持续", "依赖": "任务3,4,5", "目标产出": "执行报告"},
                ]
            critical_path = "任务1 → 任务2 → 任务3/4/5（并行）→ 任务6"
            risk_tips = ["设计与内容环节容易延期，建议预留1天buffer", "多Agent协作时注意阶段信息同步"]

        return {
            "目标": goal,
            "需求": requirements if requirements else ["参考标准流程"],
            "任务编排": tasks,
            "总工期估算": f"约{len(tasks) + 1}天（含并行）",
            "关键路径": critical_path or "任务1 → 任务2 → 并行执行 → 收尾",
            "风险提示": risk_tips if risk_tips else ["执行过程中注意各环节信息同步"],
            "编排方式": "AI动态规划" if llm_raw else "场景模板",
        }


class CoordinationStatusQuery(SkillBase):
    """状态查询技能"""

    def __init__(self) -> None:
        super().__init__(
            name="coordination_status_query",
            display_name="状态查询",
            description="查询当前系统各Agent和任务的运行状态",
            category="coordination",
            input_schema={
                "type": "object",
                "properties": {
                    "query_type": {
                        "type": "string",
                        "description": "查询类型：agent_status/task_status/system_health",
                    },
                },
                "required": [],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        query_type = kwargs.get("query_type", "system_health")
        now_str = time.strftime("%Y-%m-%d %H:%M:%S")

        # ── 从真实数据库查询系统指标 ──
        today_messages = 0
        today_llm_calls = 0
        error_count = 0
        active_campaigns = 0
        pending_tasks = 0
        memory_count = 0

        try:
            from src.database import get_db
            db = await get_db()
            today_start = time.strftime("%Y-%m-%d 00:00:00")

            # 今日消息数
            row = await db.fetchone(
                "SELECT COUNT(*) as cnt FROM messages WHERE created_at >= ?", (today_start,)
            )
            if row:
                today_messages = row["cnt"]

            # 活跃营销活动数
            row = await db.fetchone(
                "SELECT COUNT(*) as cnt FROM campaigns WHERE user_id = ? AND status = 'active'",
                (user_id,)
            )
            if row:
                active_campaigns = row["cnt"]

            # 待处理任务数
            row = await db.fetchone(
                "SELECT COUNT(*) as cnt FROM workspace_tasks WHERE user_id = ? AND status IN ('pending','in_progress')",
                (user_id,)
            )
            if row:
                pending_tasks = row["cnt"]

            # 记忆条目数
            row = await db.fetchone(
                "SELECT COUNT(*) as cnt FROM agent_memories WHERE user_id = ?", (user_id,)
            )
            if row:
                memory_count = row["cnt"]

        except Exception:
            pass  # 降级为0值，不影响功能

        agents = [
            {"角色": "运营专家", "代号": "ops", "状态": "在线"},
            {"角色": "数据分析师", "代号": "data", "状态": "在线"},
            {"角色": "客服专家", "代号": "service", "状态": "在线"},
            {"角色": "设计师", "代号": "design", "状态": "在线"},
            {"角色": "财务分析师", "代号": "accounting", "状态": "在线"},
            {"角色": "技术工程师", "代号": "engineering", "状态": "在线"},
            {"角色": "SEO专家", "代号": "web", "状态": "在线"},
            {"角色": "内容创作者", "代号": "creative", "状态": "在线"},
        ]

        return {
            "查询类型": query_type,
            "查询时间": now_str,
            "系统状态": {
                "运行状态": "正常",
                "在线Agent数": len(agents),
            },
            "Agent列表": agents,
            "业务指标（真实）": {
                "今日消息总量": today_messages,
                "活跃营销活动": active_campaigns,
                "待处理任务": pending_tasks,
                "Agent记忆条目": memory_count,
            },
            "系统说明": "消息量/任务/活动数据来自真实DB；LLM调用统计需集成链路追踪后获取",
        }


class CoordinationCreateCampaign(SkillBase):
    """创建营销活动 — 直接写入数据库，真实执行操作"""

    def __init__(self) -> None:
        super().__init__(
            name="coordination_create_campaign",
            display_name="创建营销活动",
            description="创建一个新的营销活动并保存到系统，包含活动名称、预算、关联产品等信息",
            category="coordination",
            input_schema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "活动名称，例如：618大促-主推款秒杀"},
                    "budget": {"type": "number", "description": "活动预算（元），例如：5000"},
                    "product_id": {"type": "integer", "description": "关联产品ID（可选）"},
                    "status": {"type": "string", "description": "活动状态：draft/active/paused，默认draft"},
                },
                "required": ["name"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from src.services.action_adapters import execute_adapter_action
        # 从运行上下文获取 user_id（由 registry 注入）
        user_id = kwargs.pop("_user_id", 1)
        workspace_id = kwargs.pop("_workspace_id", None)
        success, result = await execute_adapter_action(
            action_type="create_campaign",
            payload=kwargs,
            context={"user_id": user_id, "workspace_id": workspace_id},
        )
        if success:
            return {
                "状态": "✅ 活动创建成功",
                "活动ID": result.get("campaign_id"),
                "活动名称": result.get("name"),
                "提示": "活动已保存到系统，可在营销活动页面查看和管理",
            }
        return {"状态": "❌ 创建失败", "原因": result.get("error", "未知错误")}


class CoordinationCreateTask(SkillBase):
    """创建工作区任务 — 直接写入工作区任务列表，真实执行操作"""

    def __init__(self) -> None:
        super().__init__(
            name="coordination_create_task",
            display_name="创建工作任务",
            description="在当前工作区创建一个新任务，指定负责角色、优先级和验收标准",
            category="coordination",
            input_schema={
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "任务标题，例如：优化主图CTR，目标提升20%"},
                    "description": {"type": "string", "description": "任务详细描述"},
                    "owner_role": {
                        "type": "string",
                        "description": "负责角色：ops/data/service/design/accounting/engineering/web/creative",
                    },
                    "priority": {"type": "integer", "description": "优先级：0=普通, 1=重要, 2=紧急"},
                    "acceptance_criteria": {"type": "string", "description": "验收标准，例如：CTR提升至3.5%以上"},
                },
                "required": ["title"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from src.services.action_adapters import execute_adapter_action
        user_id = kwargs.pop("_user_id", 1)
        workspace_id = kwargs.pop("_workspace_id", None)
        if not workspace_id:
            return {"状态": "⚠️ 未关联工作区", "提示": "请先打开或创建一个工作区，再创建任务"}
        success, result = await execute_adapter_action(
            action_type="create_workspace_task",
            payload=kwargs,
            context={"user_id": user_id, "workspace_id": workspace_id},
        )
        if success:
            priority_labels = {0: "普通", 1: "重要", 2: "紧急"}
            return {
                "状态": "✅ 任务创建成功",
                "任务ID": result.get("task_id"),
                "任务标题": result.get("title"),
                "优先级": priority_labels.get(kwargs.get("priority", 0), "普通"),
                "负责角色": kwargs.get("owner_role", "ops"),
                "提示": "任务已保存到工作区，可在工作区页面查看进度",
            }
        return {"状态": "❌ 创建失败", "原因": result.get("error", "未知错误")}


class CoordinationSaveMemory(SkillBase):
    """保存工作区记忆 — 将重要信息持久化到工作区知识库"""

    def __init__(self) -> None:
        super().__init__(
            name="coordination_save_memory",
            display_name="保存工作记忆",
            description="将对话中发现的重要事实、决策或结论保存到工作区记忆，供后续对话使用",
            category="coordination",
            input_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "记忆键名，例如：pricing_strategy_2024"},
                    "content": {"type": "string", "description": "记忆内容，例如：本品定价区间150-180元，低于竞品均价200元"},
                    "memory_type": {
                        "type": "string",
                        "description": "记忆类型：fact=事实, decision=决策, insight=洞察, warning=风险",
                    },
                },
                "required": ["key", "content"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from src.services.action_adapters import execute_adapter_action
        user_id = kwargs.pop("_user_id", 1)
        workspace_id = kwargs.pop("_workspace_id", None)
        if not workspace_id:
            return {"状态": "⚠️ 未关联工作区", "提示": "请先打开或创建一个工作区，再保存记忆"}
        role = kwargs.pop("_role", "ops")
        payload = dict(kwargs)
        payload["role"] = role
        success, result = await execute_adapter_action(
            action_type="save_workspace_memory",
            payload=payload,
            context={"user_id": user_id, "workspace_id": workspace_id},
        )
        if success:
            mode = "更新" if result.get("mode") == "updated" else "新建"
            return {
                "状态": f"✅ 记忆已{mode}",
                "记忆键": kwargs.get("key"),
                "内容摘要": kwargs.get("content", "")[:80],
                "提示": "此信息已持久化，下次对话中可直接引用",
            }
        return {"状态": "❌ 保存失败", "原因": result.get("error", "未知错误")}


class PlatformUpdatePrice(SkillBase):
    """修改商品价格 — 直接同步到真实平台（需配置平台API凭证）"""

    def __init__(self) -> None:
        super().__init__(
            name="platform_update_price",
            display_name="修改商品价格",
            description=(
                "将商品价格修改同步到淘宝/京东/拼多多/抖音等真实平台。"
                "需要用户已在「平台连接」页面配置对应平台的API凭证。"
                "修改前请确认商品ID和新价格，此操作会立即生效。"
            ),
            category="coordination",
            input_schema={
                "type": "object",
                "properties": {
                    "platform": {
                        "type": "string",
                        "description": "平台标识: taobao/jd/pdd/douyin",
                    },
                    "product_id": {
                        "type": "string",
                        "description": "平台商品ID（从平台后台或同步接口获取）",
                    },
                    "new_price": {
                        "type": "number",
                        "description": "新价格（元），例如 99.9",
                    },
                    "sku_id": {
                        "type": "string",
                        "description": "SKU ID（可选，多规格商品时使用）",
                    },
                },
                "required": ["platform", "product_id", "new_price"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from src.services.action_adapters import execute_adapter_action
        user_id = kwargs.pop("_user_id", 1)
        kwargs.pop("_workspace_id", None)
        kwargs.pop("_role", None)
        success, result = await execute_adapter_action(
            action_type="update_product_price",
            payload=kwargs,
            context={"user_id": user_id},
        )
        if success:
            return {
                "状态": "✅ 价格修改成功",
                "商品ID": result.get("product_id"),
                "平台": result.get("platform"),
                "新价格": f"¥{result.get('new_price', 0):.2f}",
                "提示": result.get("message", ""),
            }
        return {"状态": "❌ 修改失败", "原因": result.get("error", "未知错误")}


class PlatformUpdateInventory(SkillBase):
    """修改商品库存 — 直接同步到真实平台"""

    def __init__(self) -> None:
        super().__init__(
            name="platform_update_inventory",
            display_name="修改商品库存",
            description=(
                "将商品库存数量修改同步到淘宝/京东/拼多多/抖音等真实平台。"
                "需要用户已配置平台API凭证。操作后库存立即更新。"
            ),
            category="coordination",
            input_schema={
                "type": "object",
                "properties": {
                    "platform": {
                        "type": "string",
                        "description": "平台标识: taobao/jd/pdd/douyin",
                    },
                    "product_id": {
                        "type": "string",
                        "description": "平台商品ID",
                    },
                    "quantity": {
                        "type": "integer",
                        "description": "新库存数量",
                    },
                    "sku_id": {
                        "type": "string",
                        "description": "SKU ID（可选）",
                    },
                },
                "required": ["platform", "product_id", "quantity"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        from src.services.action_adapters import execute_adapter_action
        user_id = kwargs.pop("_user_id", 1)
        kwargs.pop("_workspace_id", None)
        kwargs.pop("_role", None)
        success, result = await execute_adapter_action(
            action_type="update_inventory",
            payload=kwargs,
            context={"user_id": user_id},
        )
        if success:
            return {
                "状态": "✅ 库存修改成功",
                "商品ID": result.get("product_id"),
                "平台": result.get("platform"),
                "新库存": result.get("quantity"),
                "提示": result.get("message", ""),
            }
        return {"状态": "❌ 修改失败", "原因": result.get("error", "未知错误")}


class PlatformSyncProducts(SkillBase):
    """从平台同步商品列表（获取真实商品ID和价格）"""

    def __init__(self) -> None:
        super().__init__(
            name="platform_sync_products",
            display_name="同步平台商品",
            description=(
                "从已连接的平台实时拉取商品列表，获取真实商品ID、当前价格和库存。"
                "用于了解店铺现有商品状态，是修改价格/库存前的必要前置操作。"
            ),
            category="coordination",
            input_schema={
                "type": "object",
                "properties": {
                    "platform": {
                        "type": "string",
                        "description": "平台标识: taobao/jd/pdd/douyin",
                    },
                    "page": {"type": "integer", "description": "页码，默认1"},
                    "page_size": {"type": "integer", "description": "每页数量，默认20"},
                },
                "required": ["platform"],
            },
        )

    async def execute(self, **kwargs) -> Dict[str, Any]:
        user_id: int = kwargs.get("_user_id", 0)
        platform: str = kwargs.get("platform", "")
        page: int = int(kwargs.get("page", 1))
        page_size: int = int(kwargs.get("page_size", 20))

        if not user_id or not platform:
            return {"error": "缺少必要参数（user_id 或 platform）"}

        try:
            import src.database as db_module
            from src.services.platform_adapters import get_platform_registry
            registry = await get_platform_registry(user_id, db_module)
            adapter = registry.get(platform)
            if adapter is None:
                return {
                    "状态": "未配置",
                    "提示": f"平台 {platform} 未配置，请先在「平台连接」页面填写API凭证",
                }
            if not adapter.is_configured():
                return {
                    "状态": "凭证不完整",
                    "提示": f"{platform} 凭证不完整，请检查配置",
                }
            products = await adapter.get_products(page=page, page_size=page_size)
            if not products:
                return {"状态": "无商品数据", "平台": platform, "商品列表": []}
            return {
                "状态": "✅ 同步成功",
                "平台": platform,
                "商品数量": len(products),
                "商品列表": [
                    {
                        "商品ID": p.product_id,
                        "标题": p.title[:40],
                        "价格": f"¥{p.price:.2f}",
                        "库存": p.inventory,
                        "状态": p.status,
                        "SKU": p.sku,
                    }
                    for p in products
                ],
            }
        except Exception as e:
            return {"error": f"同步失败: {e}"}


class CoordinationSkillChainPlanner(SkillBase):
    """
    技能链规划器 — 分析目标，返回最优技能调用顺序。

    帮助LLM在tool_use循环中做出更智能的决策：
    先调用哪个技能获取数据，再调用哪个技能做分析，最后调用哪个技能执行。
    """

    # 按角色+动作预置的技能链模板（20+组合，覆盖8个角色×常见动作）
    _CHAIN_TEMPLATES: Dict[str, List[Dict[str, str]]] = {
        # ── 运营专家 ──
        "ops_analyze": [
            {"skill": "data_query_store_metrics", "reason": "加载真实GMV/ROI/转化率数据"},
            {"skill": "data_dashboard", "reason": "看板全貌：异常/健康度/各平台对比"},
            {"skill": "ops_execution_plan", "reason": "基于真实数据制定执行方案"},
        ],
        "ops_plan": [
            {"skill": "data_query_store_metrics", "reason": "了解当前业绩基准"},
            {"skill": "ops_assortment_planning", "reason": "制定选品/品类规划"},
            {"skill": "ops_execution_plan", "reason": "生成30天可落地执行计划"},
            {"skill": "coordination_create_task", "reason": "将关键任务保存到工作区"},
        ],
        "ops_create": [
            {"skill": "data_query_store_metrics", "reason": "加载商品和竞品数据"},
            {"skill": "ops_listing_copy", "reason": "生成商品标题和详情文案"},
            {"skill": "web_keyword_research", "reason": "补充核心关键词"},
        ],
        "ops_pricing": [
            {"skill": "data_query_store_metrics", "reason": "加载历史销量和客单价"},
            {"skill": "ops_smart_pricing", "reason": "价格弹性模型+竞品定位+最优利润定价"},
            {"skill": "accounting_cost_calc", "reason": "验证利润率是否达标"},
        ],
        "ops_promote": [
            {"skill": "data_query_store_metrics", "reason": "加载当前GMV基线和广告ROI"},
            {"skill": "ops_promo_planning", "reason": "制定促销活动方案"},
            {"skill": "creative_seeding_copy", "reason": "生成配套种草文案"},
            {"skill": "coordination_create_campaign", "reason": "将活动保存到系统"},
        ],
        "ops_inventory": [
            {"skill": "data_query_store_metrics", "reason": "加载销售速度和库存数据"},
            {"skill": "ops_inventory_optimizer", "reason": "EOQ+安全库存+ABC分类+周转率计算"},
            {"skill": "data_demand_forecast", "reason": "SES需求预测，优化备货量"},
            {"skill": "ops_smart_pricing", "reason": "如库存积压，制定清仓定价方案"},
        ],
        "ops_ad_optimize": [
            {"skill": "data_query_store_metrics", "reason": "加载广告花费/ROI/UV数据"},
            {"skill": "ops_ad_fatigue_detector", "reason": "检测CTR衰减、疲劳渠道和预算重分配"},
            {"skill": "data_channel_roi", "reason": "各渠道ROI对比，确认重点加码方向"},
            {"skill": "coordination_create_task", "reason": "创建素材更新任务"},
        ],

        # ── 数据分析师 ──
        "data_analyze": [
            {"skill": "data_query_store_metrics", "reason": "加载多维度指标数据"},
            {"skill": "data_anomaly_diagnosis", "reason": "检测指标异常并定位根因"},
            {"skill": "data_multi_period_trend", "reason": "多周期趋势+拐点检测"},
        ],
        "data_plan": [
            {"skill": "data_query_store_metrics", "reason": "加载历史数据"},
            {"skill": "data_customer_segmentation", "reason": "RFM分层客群"},
            {"skill": "data_funnel_analysis", "reason": "漏斗转化分析"},
            {"skill": "data_ltv_calculator", "reason": "计算客户LTV/CAC健康度"},
        ],
        "data_report": [
            {"skill": "data_query_store_metrics", "reason": "加载原始数据"},
            {"skill": "data_dashboard", "reason": "生成综合看板"},
            {"skill": "data_channel_roi", "reason": "渠道ROI归因"},
            {"skill": "data_attribution_analysis", "reason": "多触点渠道贡献归因"},
        ],
        "data_forecast": [
            {"skill": "data_query_store_metrics", "reason": "加载历史时序数据"},
            {"skill": "data_demand_forecast", "reason": "SES指数平滑需求预测（订单/GMV/UV）"},
            {"skill": "data_seasonal_decompose", "reason": "分离季节性噪音，识别真实增长趋势"},
            {"skill": "data_cohort_analysis", "reason": "队列留存率趋势"},
        ],
        "data_rfm": [
            {"skill": "data_query_store_metrics", "reason": "加载客户交易数据"},
            {"skill": "data_customer_segmentation", "reason": "RFM四分位+11类标签分群"},
            {"skill": "data_ltv_calculator", "reason": "计算各层LTV，量化价值差异"},
            {"skill": "coordination_save_memory", "reason": "保存高价值客群特征供后续对话使用"},
        ],
        "data_ab": [
            {"skill": "data_ab_test_analyzer", "reason": "卡方检验A/B测试统计显著性"},
            {"skill": "data_funnel_analysis", "reason": "对比两组漏斗转化"},
        ],

        # ── 财务分析师 ──
        "accounting_analyze": [
            {"skill": "data_query_store_metrics", "reason": "加载GMV/费用等财务原始数据"},
            {"skill": "accounting_pl_statement", "reason": "计算完整P&L利润表"},
            {"skill": "accounting_financial_narrative", "reason": "生成财务健康度诊断"},
        ],
        "accounting_plan": [
            {"skill": "data_query_store_metrics", "reason": "加载历史GMV作为预算基准"},
            {"skill": "accounting_budget_plan", "reason": "编制月度预算计划"},
            {"skill": "accounting_budget_vs_actual", "reason": "对比上期预算执行情况"},
        ],
        "accounting_cost": [
            {"skill": "accounting_cost_calc", "reason": "核算单品成本结构"},
            {"skill": "accounting_break_even_calc", "reason": "盈亏平衡点分析"},
            {"skill": "accounting_roi_calc", "reason": "广告投放ROI计算"},
        ],
        "accounting_cashflow": [
            {"skill": "data_query_store_metrics", "reason": "加载收入和支出数据"},
            {"skill": "accounting_cash_flow_forecast", "reason": "预测未来6个月现金流"},
            {"skill": "accounting_profit_analysis", "reason": "利润结构健康度分析"},
        ],

        # ── 客服专家 ──
        "service_analyze": [
            {"skill": "data_query_store_metrics", "reason": "加载退款率/DSR等服务数据"},
            {"skill": "service_sentiment_analyzer", "reason": "批量情感分析，识别高危负面消息"},
            {"skill": "service_nps_analyzer", "reason": "NPS净推荐值+CSAT满意度量化评估"},
            {"skill": "service_dsr_improvement", "reason": "DSR综合诊断+AI改善方案"},
        ],
        "service_improve": [
            {"skill": "service_sentiment_analyzer", "reason": "分析客诉情感强度，识别紧急等级"},
            {"skill": "service_dsr_improvement", "reason": "诊断DSR各维度问题"},
            {"skill": "service_response_templates", "reason": "生成标准化应答话术"},
            {"skill": "coordination_create_task", "reason": "创建客服优化任务"},
        ],
        "service_nps": [
            {"skill": "service_nps_analyzer", "reason": "计算NPS/CSAT，识别推荐者/批评者分布"},
            {"skill": "service_nps_driver_analysis", "reason": "4类投诉根因分析，计算各类别NPS拖累系数"},
            {"skill": "data_customer_segmentation", "reason": "RFM分层，识别哪类客户NPS最低"},
            {"skill": "service_dsr_improvement", "reason": "将低NPS问题映射到DSR改善行动"},
        ],
        "service_nps_deep": [
            {"skill": "data_query_store_metrics", "reason": "加载真实退款/评价数据"},
            {"skill": "service_nps_driver_analysis", "reason": "量化4类投诉对NPS的影响系数"},
            {"skill": "service_sentiment_analyzer", "reason": "批量分析评价情感，定位高危客诉"},
            {"skill": "service_dsr_improvement", "reason": "基于驱动因素制定DSR改善方案"},
        ],
        "engineering_sla": [
            {"skill": "engineering_sla_monitor", "reason": "P50/P90/P99延迟 + 错误预算监控"},
            {"skill": "engineering_perf_optimize", "reason": "基于P99超标制定性能优化方案"},
            {"skill": "engineering_system_check", "reason": "检查平台连接和系统整体健康度"},
        ],
        "web_title_optimize": [
            {"skill": "web_title_seo_scorer", "reason": "诊断标题SEO质量评分"},
            {"skill": "web_seo_optimize", "reason": "关键词策略和标题重写"},
            {"skill": "web_keyword_research", "reason": "补充长尾关键词矩阵"},
        ],
        "creative_ctr_boost": [
            {"skill": "creative_title_ctr_scorer", "reason": "AIDA+情感触发词密度评分"},
            {"skill": "creative_seeding_copy", "reason": "基于评分结果优化文案"},
            {"skill": "web_title_seo_scorer", "reason": "同步检查SEO质量"},
        ],
        "ops_sku_classify": [
            {"skill": "data_query_store_metrics", "reason": "加载SKU销售历史数据"},
            {"skill": "ops_abc_xyz_classifier", "reason": "ABC-XYZ双维分类9格矩阵"},
            {"skill": "ops_inventory_optimizer", "reason": "基于分类结果设定差异化安全库存"},
        ],

        # ── 设计师 ──
        "design_create": [
            {"skill": "design_color_scheme", "reason": "先确定品牌配色体系"},
            {"skill": "design_main_image", "reason": "主图设计执行方案"},
            {"skill": "design_detail_page", "reason": "详情页模块设计"},
        ],
        "design_campaign": [
            {"skill": "design_campaign_poster", "reason": "活动海报设计方案"},
            {"skill": "design_material_spec", "reason": "各平台素材规格清单"},
        ],

        # ── 内容创作者 ──
        "creative_create": [
            {"skill": "data_query_store_metrics", "reason": "了解平台数据，个性化内容"},
            {"skill": "creative_video_script", "reason": "生成完整短视频脚本"},
            {"skill": "creative_seeding_copy", "reason": "配套种草文案"},
        ],
        "creative_live": [
            {"skill": "data_query_store_metrics", "reason": "加载商品销售数据"},
            {"skill": "creative_live_script", "reason": "生成完整直播话术脚本"},
            {"skill": "creative_content_calendar", "reason": "配套内容发布排期"},
        ],
        "creative_plan": [
            {"skill": "creative_ip_branding", "reason": "IP人设定位"},
            {"skill": "creative_content_calendar", "reason": "内容排期规划"},
            {"skill": "creative_trend_catch", "reason": "趋势捕捉和创意方向"},
        ],

        # ── SEO专家 ──
        "web_optimize": [
            {"skill": "data_query_store_metrics", "reason": "了解当前搜索流量和转化"},
            {"skill": "web_seo_optimize", "reason": "SEO关键词和内容优化方案"},
            {"skill": "web_page_conversion", "reason": "页面转化率提升计划"},
        ],
        "web_build": [
            {"skill": "web_store_design", "reason": "店铺视觉改造方案"},
            {"skill": "web_page_conversion", "reason": "转化率优化方案"},
            {"skill": "web_keyword_research", "reason": "关键词矩阵研究"},
        ],

        # ── 技术工程师 ──
        "engineering_diagnose": [
            {"skill": "engineering_bug_analysis", "reason": "深度Bug诊断和修复方案"},
            {"skill": "engineering_system_check", "reason": "检查平台接口连接状态"},
        ],
        "engineering_optimize": [
            {"skill": "engineering_system_check", "reason": "检查当前系统状态"},
            {"skill": "engineering_perf_optimize", "reason": "性能优化方案"},
            {"skill": "engineering_arch_review", "reason": "架构评审和改造路径"},
        ],

        # ── 跨角色协同 ──
        "multi_gmv_boost": [
            {"skill": "data_query_store_metrics", "reason": "数据专家：加载基线指标"},
            {"skill": "data_anomaly_diagnosis", "reason": "数据专家：识别核心问题"},
            {"skill": "ops_execution_plan", "reason": "运营专家：制定GMV提升执行方案"},
            {"skill": "accounting_roi_calc", "reason": "财务专家：验证投入产出可行性"},
        ],
        "multi_product_launch": [
            {"skill": "ops_listing_copy", "reason": "运营专家：商品标题和文案"},
            {"skill": "design_main_image", "reason": "设计师：主图设计方案"},
            {"skill": "creative_video_script", "reason": "内容创作：种草视频脚本"},
            {"skill": "web_seo_optimize", "reason": "SEO专家：搜索关键词优化"},
        ],
    }

    # 技能前置依赖（调用某技能前建议先调哪个）
    _PREREQUISITES: Dict[str, List[str]] = {
        "ops_execution_plan": ["data_query_store_metrics"],
        "ops_assortment_planning": ["data_query_store_metrics"],
        "ops_smart_pricing": ["data_query_store_metrics"],
        "accounting_pl_statement": ["data_query_store_metrics"],
        "accounting_financial_narrative": ["data_query_store_metrics"],
        "accounting_budget_vs_actual": ["data_query_store_metrics", "accounting_budget_plan"],
        "accounting_cash_flow_forecast": ["data_query_store_metrics"],
        "data_anomaly_diagnosis": ["data_query_store_metrics"],
        "data_multi_period_trend": ["data_query_store_metrics"],
        "data_dashboard": ["data_query_store_metrics"],
        "data_ltv_calculator": ["data_query_store_metrics"],
        "data_cohort_analysis": ["data_query_store_metrics"],
        "data_channel_roi": ["data_query_store_metrics"],
        "data_attribution_analysis": ["data_query_store_metrics"],
        "web_seo_optimize": ["data_query_store_metrics"],
        "web_page_conversion": ["data_query_store_metrics"],
        "service_dsr_improvement": ["data_query_store_metrics"],
        "creative_content_calendar": ["data_query_store_metrics"],
        "data_demand_forecast": ["data_query_store_metrics"],
        "ops_ad_fatigue_detector": ["data_query_store_metrics"],
        "ops_inventory_optimizer": ["data_query_store_metrics"],
        "service_nps_analyzer": ["data_query_store_metrics"],
        "data_customer_segmentation": ["data_query_store_metrics"],
    }

    def __init__(self) -> None:
        super().__init__(
            name="coordination_skill_chain_planner",
            display_name="技能链规划",
            description=(
                "分析任务目标，返回推荐的技能调用顺序和依赖关系。"
                "在处理复杂多步骤任务时，先调用此技能获得执行路径，"
                "再按序调用各技能以避免遗漏数据加载步骤。"
            ),
            category="coordination",
            input_schema={
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "整体任务目标，例如：分析本月亏损原因"},
                    "role": {
                        "type": "string",
                        "description": "当前Agent角色: ops/data/accounting/web/creative等",
                    },
                    "action": {
                        "type": "string",
                        "description": "动作类型: analyze/create/optimize/plan/execute/query",
                    },
                    "skills_available": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "可用技能名列表（可选，用于精确匹配）",
                    },
                },
                "required": ["goal"],
            },
        )

    # 全量技能目录摘要（供LLM规划时参考，每条技能一行）
    _SKILL_CATALOG_SUMMARY = """
data: query_store_metrics(查询真实指标), funnel_analysis(漏斗转化), anomaly_diagnosis(异常诊断),
  trend_forecast(趋势预测), customer_segmentation(RFM11分层), competitor_analysis(竞品分析),
  dashboard(综合看板), multi_period_trend(多周期环比), channel_roi(渠道ROI), ltv_calculator(LTV计算),
  cohort_analysis(留存队列), ab_test_analyzer(A/B卡方检验), attribution_analysis(多触点归因),
  refund_decomposition(退款帕累托), seasonal_decompose(季节性分解), price_elasticity(弧弹性),
  demand_forecast(SES需求预测)
ops: promo_planning(促销策划), pricing_strategy(定价策略), channel_strategy(渠道策略),
  inventory_planning(库存规划), listing_copy(商品文案), assortment_planning(选品规划),
  execution_plan(执行计划), smart_pricing(弹性+竞品最优价), inventory_optimizer(EOQ+安全库存+ABC),
  ad_fatigue_detector(CTR衰减+疲劳+预算重分配)
accounting: cost_calc(成本核算), break_even_calc(盈亏平衡), roi_calc(ROI计算),
  pl_statement(P&L利润表), profit_analysis(利润结构), cash_flow_forecast(现金流预测),
  budget_plan(月度预算), financial_narrative(财务诊断), budget_vs_actual(预实差异),
  gmv_waterfall(GMV→净利瀑布分解)
service: ticket_handler(工单话术), faq_playbook(FAQ), escalation_flow(升级流程),
  dsr_improvement(DSR改善), return_handler(退货处理), query_product(商品查询),
  nps_analyzer(NPS+CSAT), sentiment_analyzer(0-10情感评分+紧急分类)
creative: video_script(短视频脚本), seeding_copy(种草文案), live_script(直播话术),
  ip_branding(IP定位), content_calendar(内容排期), trend_catch(趋势捕捉)
design: color_scheme(配色), main_image(主图), detail_page(详情页),
  campaign_poster(海报), material_spec(素材规格)
web: seo_optimize(SEO优化), page_conversion(转化率), store_design(店铺装修),
  keyword_research(关键词研究)
engineering: bug_analysis(Bug诊断), perf_optimize(性能优化), arch_review(架构评审),
  system_check(系统检查)
coordination: agent_handoff(交接), create_campaign(创建活动), create_task(创建任务),
  save_memory(保存记忆), platform_update_price(修改价格), platform_update_inventory(修改库存),
  platform_sync_products(同步商品)
"""

    async def _llm_plan_chain(
        self, goal: str, role: str, action: str, available: List[str]
    ) -> List[Dict[str, str]]:
        """当无预设模板匹配时，调用LLM根据目标动态生成最优技能链。"""
        try:
            from src.skills._content_engine import _call, _SYSTEM_ANALYSIS_EXPERT
            import json as _json

            # 构建可用技能上下文
            catalog = self._SKILL_CATALOG_SUMMARY
            if available:
                catalog += f"\n当前可用技能：{', '.join(available[:20])}"

            prompt = f"""你是一个电商AI平台的技能链规划专家。根据任务目标，从技能目录中选择最优的技能调用序列。

当前Agent角色：{role}
动作类型：{action}
用户任务目标：{goal}

技能目录（格式：分类: 技能名(描述)）：
{catalog}

请输出JSON格式的技能调用序列（3-5个技能为佳，必须真实存在于目录中）：
{{
  "chain": [
    {{"skill": "data_query_store_metrics", "reason": "加载真实数据作为分析基础"}},
    {{"skill": "技能名2", "reason": "理由2"}},
    {{"skill": "技能名3", "reason": "理由3"}}
  ]
}}

规则：
1. 先数据加载（query_store_metrics），再计算分析，最后生成内容/执行操作
2. 技能名格式为 分类_具体名，如 data_ltv_calculator
3. 根据任务需要灵活选择，不受角色限制（运营任务可调数据技能）
4. 只输出JSON，不要其他内容"""

            response = await asyncio.wait_for(_call(_SYSTEM_ANALYSIS_EXPERT, prompt, max_tokens=400, temperature=0.3), timeout=8.0)
            if response:
                # 提取JSON
                start = response.find("{")
                end = response.rfind("}") + 1
                if start >= 0 and end > start:
                    data = _json.loads(response[start:end])
                    chain = data.get("chain", [])
                    if chain and isinstance(chain, list):
                        return chain[:5]  # 最多5步
        except Exception:
            pass

        # 最终兜底（数据加载+角色动作）
        return [
            {"skill": "data_query_store_metrics", "reason": "加载真实业务数据作为分析基础"},
            {"skill": f"{role}_execution_plan", "reason": "生成可落地的执行方案"},
        ]

    async def execute(self, **kwargs) -> Dict[str, Any]:
        goal: str = kwargs.get("goal", "")
        role: str = kwargs.get("role", "ops")
        action: str = kwargs.get("action", "analyze")

        # 优先通过关键词精确匹配专用链（比通用 role_action 模板更具体）
        chain = None
        goal_lower = goal.lower()
        if any(kw in goal_lower for kw in ["nps驱动", "投诉根因", "满意度根因", "推荐者比例", "贬低者"]):
            chain = self._CHAIN_TEMPLATES["service_nps_deep"]
        elif any(kw in goal_lower for kw in ["sla", "p99", "p90", "延迟监控", "错误预算", "可用性slo"]):
            chain = self._CHAIN_TEMPLATES["engineering_sla"]
        elif any(kw in goal_lower for kw in ["标题seo", "标题评分", "关键词密度", "seo评分"]):
            chain = self._CHAIN_TEMPLATES["web_title_optimize"]
        elif any(kw in goal_lower for kw in ["点击率提升", "ctr预测", "标题吸引力", "aida", "情感触发词"]):
            chain = self._CHAIN_TEMPLATES["creative_ctr_boost"]
        elif any(kw in goal_lower for kw in ["abc xyz", "abc-xyz", "sku分类", "库存矩阵", "变异系数"]):
            chain = self._CHAIN_TEMPLATES["ops_sku_classify"]
        elif any(kw in goal_lower for kw in ["广告疲劳", "ctr下降", "投放疲劳", "素材更新", "创意刷新", "广告优化"]):
            chain = self._CHAIN_TEMPLATES["ops_ad_optimize"]
        elif any(kw in goal_lower for kw in ["rfm", "客群分层", "客户分层", "客户分群", "champions"]):
            chain = self._CHAIN_TEMPLATES["data_rfm"]

        # 未命中专用关键词 → 查找精确 role_action 模板
        if not chain:
            template_key = f"{role}_{action}"
            chain = self._CHAIN_TEMPLATES.get(template_key)

        # 仍未找到 → 通用关键词兜底
        if not chain:
            if any(kw in goal_lower for kw in ["现金流", "预算", "差异", "方差"]):
                chain = self._CHAIN_TEMPLATES["accounting_cashflow"]
            elif any(kw in goal_lower for kw in ["财务", "利润", "成本", "毛利", "P&L", "亏损"]):
                chain = self._CHAIN_TEMPLATES["accounting_analyze"]
            elif any(kw in goal_lower for kw in ["A/B", "ab测试", "实验组", "对照组"]):
                chain = self._CHAIN_TEMPLATES["data_ab"]
            elif any(kw in goal_lower for kw in ["归因", "归因分析", "渠道贡献", "多触点"]):
                chain = self._CHAIN_TEMPLATES["data_report"]
            elif any(kw in goal_lower for kw in ["预测", "预报", "未来", "下月", "下季"]):
                chain = self._CHAIN_TEMPLATES["data_forecast"]
            elif any(kw in goal_lower for kw in ["数据", "指标", "看板", "异常", "诊断"]):
                chain = self._CHAIN_TEMPLATES["data_analyze"]
            elif any(kw in goal_lower for kw in ["ltv", "留存", "流失", "队列", "复购"]):
                chain = self._CHAIN_TEMPLATES["data_plan"]
            elif any(kw in goal_lower for kw in ["直播", "直播脚本", "带货话术"]):
                chain = self._CHAIN_TEMPLATES["creative_live"]
            elif any(kw in goal_lower for kw in ["视频", "文案", "脚本", "种草", "软文"]):
                chain = self._CHAIN_TEMPLATES["creative_create"]
            elif any(kw in goal_lower for kw in ["ip", "内容规划", "内容策略", "内容日历"]):
                chain = self._CHAIN_TEMPLATES["creative_plan"]
            elif any(kw in goal_lower for kw in ["seo", "排名", "关键词", "搜索"]):
                chain = self._CHAIN_TEMPLATES["web_optimize"]
            elif any(kw in goal_lower for kw in ["建站", "店铺装修", "页面设计"]):
                chain = self._CHAIN_TEMPLATES["web_build"]
            elif any(kw in goal_lower for kw in ["定价", "价格弹性", "最优价", "清仓"]):
                chain = self._CHAIN_TEMPLATES["ops_pricing"]
            elif any(kw in goal_lower for kw in ["促销", "活动", "大促", "618", "双11"]):
                chain = self._CHAIN_TEMPLATES["ops_promote"]
            elif any(kw in goal_lower for kw in ["库存", "补货", "滞销", "积压", "eoq", "安全库存"]):
                chain = self._CHAIN_TEMPLATES["ops_inventory"]
            elif any(kw in goal_lower for kw in ["nps", "满意度", "推荐者", "批评者", "净推荐值"]):
                chain = self._CHAIN_TEMPLATES["service_nps"]
            elif any(kw in goal_lower for kw in ["情感分析", "情绪分析", "客诉分析", "高危消息"]):
                chain = self._CHAIN_TEMPLATES["service_analyze"]
            elif any(kw in goal_lower for kw in ["主图", "详情页", "配色", "海报", "设计"]):
                chain = self._CHAIN_TEMPLATES["design_create"]
            elif any(kw in goal_lower for kw in ["dsr", "客服", "评价", "投诉", "售后"]):
                chain = self._CHAIN_TEMPLATES["service_analyze"]
            elif any(kw in goal_lower for kw in ["bug", "报错", "异常", "技术问题", "接口"]):
                chain = self._CHAIN_TEMPLATES["engineering_diagnose"]
            elif any(kw in goal_lower for kw in ["性能", "架构", "优化", "扩容", "稳定性"]):
                chain = self._CHAIN_TEMPLATES["engineering_optimize"]
            elif any(kw in goal_lower for kw in ["gmv", "增长", "提升", "提高", "冲量"]):
                chain = self._CHAIN_TEMPLATES["multi_gmv_boost"]
            elif any(kw in goal_lower for kw in ["新品", "上新", "选品", "商品"]):
                chain = self._CHAIN_TEMPLATES["multi_product_launch"]
            elif any(kw in goal_lower for kw in ["方案", "规划", "计划", "策略"]):
                chain = self._CHAIN_TEMPLATES["ops_plan"]
            else:
                # 通用兜底：先查数据，再执行
                chain = [
                    {"skill": "data_query_store_metrics", "reason": "加载真实业务数据作为分析基础"},
                    {"skill": f"{role}_{action}", "reason": f"执行{action}任务"},
                ]

        # 若仍未找到合适链 → 调用 LLM 动态规划（AI驱动兜底）
        if not chain or chain == [
            {"skill": "data_query_store_metrics", "reason": "加载真实业务数据作为分析基础"},
            {"skill": f"{role}_{action}", "reason": f"执行{action}任务"},
        ]:
            chain = await self._llm_plan_chain(goal, role, action, kwargs.get("skills_available", []))

        # 附上前置依赖提示
        prerequisites_note = {}
        for step in chain:
            skill_name = step.get("skill", "")
            prereqs = self._PREREQUISITES.get(skill_name, [])
            if prereqs:
                prerequisites_note[skill_name] = prereqs

        return {
            "任务目标": goal,
            "推荐执行链": [
                {"步骤": i + 1, "技能": s["skill"], "原因": s["reason"]}
                for i, s in enumerate(chain)
            ],
            "前置依赖说明": prerequisites_note or "无特殊依赖",
            "执行建议": (
                "以上是推荐路径，但你可以根据实际情况灵活调整顺序和工具选择。"
                "核心原则：先加载真实数据，再计算分析，最后生成内容或执行操作。"
            ),
            "总步骤数": len(chain),
            "规划来源": "LLM动态规划" if not self._CHAIN_TEMPLATES.get(f"{role}_{action}") else "预设模板",
        }


class CoordinationExternalAgentCall(SkillBase):
    """调用外部Agent/API（A2A集成）。endpoint必须在EXTERNAL_AGENT_WHITELIST中，防止SSRF攻击。"""

    def __init__(self) -> None:
        super().__init__(
            name="coordination_external_agent_call",
            display_name="外部Agent调用",
            description=(
                "调用外部系统或Agent API（A2A集成）。"
                "适用场景：需要调用外部数据源、第三方AI服务或合作方API时使用。"
                "endpoint必须在系统配置的白名单中，不能调用任意URL。"
            ),
            category="coordination",
            input_schema={
                "type": "object",
                "properties": {
                    "endpoint": {
                        "type": "string",
                        "description": "外部API/Agent端点URL（必须在白名单内）",
                    },
                    "method": {
                        "type": "string",
                        "enum": ["GET", "POST"],
                        "description": "HTTP方法",
                        "default": "GET",
                    },
                    "payload": {
                        "type": "object",
                        "description": "POST请求体（JSON）",
                        "default": {},
                    },
                    "description": {
                        "type": "string",
                        "description": "本次调用目的（用于日志和用户展示）",
                    },
                    "extract_key": {
                        "type": "string",
                        "description": "从响应JSON中提取的字段路径，支持点号分隔（如 data.price）",
                        "default": "",
                    },
                },
                "required": ["endpoint", "description"],
            },
        )

    async def execute(self, **kwargs) -> dict:
        from src.config import EXTERNAL_AGENT_WHITELIST
        import httpx

        endpoint = kwargs.get("endpoint", "").strip()
        method = kwargs.get("method", "GET").upper()
        payload = kwargs.get("payload", {})
        description = kwargs.get("description", "外部Agent调用")
        extract_key = kwargs.get("extract_key", "")

        if not endpoint:
            return {"success": False, "error": "endpoint不能为空"}

        # 白名单检查（SSRF防护）
        if not EXTERNAL_AGENT_WHITELIST:
            return {
                "success": False,
                "error": "未配置外部Agent白名单（EXTERNAL_AGENT_WHITELIST），请联系管理员",
            }

        allowed = any(endpoint.startswith(prefix) for prefix in EXTERNAL_AGENT_WHITELIST)
        if not allowed:
            return {
                "success": False,
                "error": f"endpoint不在白名单内。允许前缀: {', '.join(EXTERNAL_AGENT_WHITELIST[:3])}",
            }

        # 发起HTTP请求
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                if method == "POST":
                    response = await client.post(
                        endpoint,
                        json=payload,
                        headers={"Content-Type": "application/json"},
                    )
                else:
                    response = await client.get(endpoint)

            status_code = response.status_code
            try:
                data = response.json()
            except Exception:
                data = response.text

            # 按extract_key提取字段
            extracted = None
            if extract_key and isinstance(data, dict):
                keys = extract_key.split(".")
                extracted = data
                for key in keys:
                    if isinstance(extracted, dict):
                        extracted = extracted.get(key)
                    else:
                        extracted = None
                        break

            return {
                "success": status_code < 400,
                "status_code": status_code,
                "调用目的": description,
                "endpoint": endpoint,
                "data": data,
                "extracted": extracted,
            }

        except httpx.TimeoutException:
            return {"success": False, "error": "请求超时（10秒）", "endpoint": endpoint}
        except Exception as e:
            return {"success": False, "error": str(e), "endpoint": endpoint}


ALL_SKILLS: list[SkillBase] = [
    CoordinationAgentHandoff(),
    CoordinationTaskOrchestration(),
    CoordinationStatusQuery(),
    CoordinationCreateCampaign(),
    CoordinationCreateTask(),
    CoordinationSaveMemory(),
    CoordinationSkillChainPlanner(),
    PlatformUpdatePrice(),
    PlatformUpdateInventory(),
    PlatformSyncProducts(),
    CoordinationExternalAgentCall(),
]


