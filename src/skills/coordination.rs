use async_trait::async_trait;
use serde_json::{Value, json};

use crate::skills::{
    Skill, SkillCategory, SkillContext, SkillInputField, SkillOutcome, SkillPriority, SkillResult,
    SkillSpec, object_params, optional_f64_param, optional_i64_param, optional_string_param,
    optional_string_vec_param, string_param,
};

fn category() -> SkillCategory {
    SkillCategory::Custom("coordination".to_owned())
}

pub fn specs() -> Vec<SkillSpec> {
    vec![
        coordination_agent_handoff_spec(),
        coordination_task_orchestration_spec(),
        coordination_status_query_spec(),
        coordination_create_campaign_spec(),
        coordination_create_task_spec(),
        coordination_save_memory_spec(),
        platform_update_price_spec(),
        platform_update_inventory_spec(),
        platform_sync_products_spec(),
        coordination_skill_chain_planner_spec(),
        coordination_external_agent_call_spec(),
    ]
}

pub fn coordination_agent_handoff_spec() -> SkillSpec {
    SkillSpec::new(
        "coordination_agent_handoff",
        "Agent交接",
        "根据任务需求，确定应交接的目标Agent及交接信息",
    )
    .with_category(category())
    .with_priority(SkillPriority::Normal)
    .with_input(SkillInputField::optional(
        "current_agent",
        "当前Agent角色",
        json!({"type": "string"}),
    ))
    .with_input(SkillInputField::required(
        "task_description",
        "需交接的任务描述",
        json!({"type": "string"}),
    ))
    .with_input(SkillInputField::optional(
        "context",
        "上下文信息",
        json!({"type": "string"}),
    ))
    .with_tag("deterministic")
}

pub fn coordination_task_orchestration_spec() -> SkillSpec {
    SkillSpec::new(
        "coordination_task_orchestration",
        "任务编排",
        "将复杂需求拆分为多Agent协作的子任务，确定执行顺序",
    )
    .with_category(category())
    .with_priority(SkillPriority::High)
    .with_input(SkillInputField::required(
        "goal",
        "整体目标",
        json!({"type": "string"}),
    ))
    .with_input(SkillInputField::optional(
        "requirements",
        "具体需求列表",
        json!({"type": "array", "items": {"type": "string"}}),
    ))
    .with_tag("deterministic")
}

pub fn coordination_status_query_spec() -> SkillSpec {
    deferred_spec(
        "coordination_status_query",
        "状态查询",
        "查询当前系统各Agent和任务的运行状态",
        vec![SkillInputField::optional(
            "query_type",
            "查询类型：agent_status/task_status/system_health",
            json!({"type": "string"}),
        )],
    )
}

pub fn coordination_create_campaign_spec() -> SkillSpec {
    pending_spec(
        "coordination_create_campaign",
        "创建营销活动",
        "创建一个新的营销活动并保存到系统，包含活动名称、预算、关联产品等信息",
        vec![
            SkillInputField::required(
                "name",
                "活动名称，例如：618大促-主推款秒杀",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "budget",
                "活动预算（元），例如：5000",
                json!({"type": "number"}),
            ),
            SkillInputField::optional(
                "product_id",
                "关联产品ID（可选）",
                json!({"type": "integer"}),
            ),
            SkillInputField::optional(
                "status",
                "活动状态：draft/active/paused，默认draft",
                json!({"type": "string"}),
            ),
        ],
    )
}

pub fn coordination_create_task_spec() -> SkillSpec {
    pending_spec(
        "coordination_create_task",
        "创建工作任务",
        "在当前工作区创建一个新任务，指定负责角色、优先级和验收标准",
        vec![
            SkillInputField::required(
                "title",
                "任务标题，例如：优化主图CTR，目标提升20%",
                json!({"type": "string"}),
            ),
            SkillInputField::optional("description", "任务详细描述", json!({"type": "string"})),
            SkillInputField::optional(
                "owner_role",
                "负责角色：ops/data/service/design/accounting/engineering/web/creative",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "priority",
                "优先级：0=普通, 1=重要, 2=紧急",
                json!({"type": "integer"}),
            ),
            SkillInputField::optional(
                "acceptance_criteria",
                "验收标准，例如：CTR提升至3.5%以上",
                json!({"type": "string"}),
            ),
        ],
    )
}

pub fn coordination_save_memory_spec() -> SkillSpec {
    pending_spec(
        "coordination_save_memory",
        "保存工作记忆",
        "将对话中发现的重要事实、决策或结论保存到工作区记忆，供后续对话使用",
        vec![
            SkillInputField::required(
                "key",
                "记忆键名，例如：pricing_strategy_2024",
                json!({"type": "string"}),
            ),
            SkillInputField::required(
                "content",
                "记忆内容，例如：本品定价区间150-180元，低于竞品均价200元",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "memory_type",
                "记忆类型：fact=事实, decision=决策, insight=洞察, warning=风险",
                json!({"type": "string"}),
            ),
        ],
    )
}

pub fn platform_update_price_spec() -> SkillSpec {
    pending_spec(
        "platform_update_price",
        "修改商品价格",
        "将商品价格修改同步到淘宝/京东/拼多多/抖音等真实平台。需要平台API凭证，修改前必须确认。",
        vec![
            SkillInputField::required(
                "platform",
                "平台标识: taobao/jd/pdd/douyin",
                json!({"type": "string"}),
            ),
            SkillInputField::required(
                "product_id",
                "平台商品ID（从平台后台或同步接口获取）",
                json!({"type": "string"}),
            ),
            SkillInputField::required(
                "new_price",
                "新价格（元），例如 99.9",
                json!({"type": "number"}),
            ),
            SkillInputField::optional(
                "sku_id",
                "SKU ID（可选，多规格商品时使用）",
                json!({"type": "string"}),
            ),
        ],
    )
}

pub fn platform_update_inventory_spec() -> SkillSpec {
    pending_spec(
        "platform_update_inventory",
        "修改商品库存",
        "将商品库存数量修改同步到淘宝/京东/拼多多/抖音等真实平台。需要用户已配置平台API凭证。",
        vec![
            SkillInputField::required(
                "platform",
                "平台标识: taobao/jd/pdd/douyin",
                json!({"type": "string"}),
            ),
            SkillInputField::required("product_id", "平台商品ID", json!({"type": "string"})),
            SkillInputField::required("quantity", "新库存数量", json!({"type": "integer"})),
            SkillInputField::optional("sku_id", "SKU ID（可选）", json!({"type": "string"})),
        ],
    )
}

pub fn platform_sync_products_spec() -> SkillSpec {
    pending_spec(
        "platform_sync_products",
        "同步平台商品",
        "从已连接的平台实时拉取商品列表，获取真实商品ID、当前价格和库存。",
        vec![
            SkillInputField::required(
                "platform",
                "平台标识: taobao/jd/pdd/douyin",
                json!({"type": "string"}),
            ),
            SkillInputField::optional("page", "页码，默认1", json!({"type": "integer"})),
            SkillInputField::optional("page_size", "每页数量，默认20", json!({"type": "integer"})),
        ],
    )
}

pub fn coordination_skill_chain_planner_spec() -> SkillSpec {
    SkillSpec::new(
        "coordination_skill_chain_planner",
        "技能链规划",
        "分析任务目标，返回推荐的技能调用顺序和依赖关系。",
    )
    .with_category(category())
    .with_priority(SkillPriority::High)
    .with_input(SkillInputField::required(
        "goal",
        "整体任务目标，例如：分析本月亏损原因",
        json!({"type": "string"}),
    ))
    .with_input(SkillInputField::optional(
        "role",
        "当前Agent角色: ops/data/accounting/web/creative等",
        json!({"type": "string"}),
    ))
    .with_input(SkillInputField::optional(
        "action",
        "动作类型: analyze/create/optimize/plan/execute/query",
        json!({"type": "string"}),
    ))
    .with_input(SkillInputField::optional(
        "skills_available",
        "可用技能名列表（可选，用于精确匹配）",
        json!({"type": "array", "items": {"type": "string"}}),
    ))
    .with_tag("deterministic")
}

pub fn coordination_external_agent_call_spec() -> SkillSpec {
    pending_spec(
        "coordination_external_agent_call",
        "外部Agent调用",
        "调用外部系统或Agent API（A2A集成）。endpoint必须在系统配置的白名单中，不能调用任意URL。",
        vec![
            SkillInputField::required(
                "endpoint",
                "外部API/Agent端点URL（必须在白名单内）",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "method",
                "HTTP方法",
                json!({"type": "string", "enum": ["GET", "POST"]}),
            ),
            SkillInputField::optional("payload", "POST请求体（JSON）", json!({"type": "object"})),
            SkillInputField::required(
                "description",
                "本次调用目的（用于日志和用户展示）",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "extract_key",
                "从响应JSON中提取的字段路径，支持点号分隔（如 data.price）",
                json!({"type": "string"}),
            ),
        ],
    )
}

fn deferred_spec(
    id: &str,
    name: &str,
    description: &str,
    inputs: Vec<SkillInputField>,
) -> SkillSpec {
    inputs.into_iter().fold(
        SkillSpec::new(id, name, description)
            .with_category(category())
            .with_priority(SkillPriority::Normal)
            .with_tag("deferred"),
        SkillSpec::with_input,
    )
}

fn pending_spec(
    id: &str,
    name: &str,
    description: &str,
    inputs: Vec<SkillInputField>,
) -> SkillSpec {
    inputs.into_iter().fold(
        SkillSpec::new(id, name, description)
            .with_category(category())
            .with_priority(SkillPriority::High)
            .with_tag("pending_approval")
            .with_tag("side_effect"),
        SkillSpec::with_input,
    )
}

pub struct CoordinationAgentHandoff {
    spec: SkillSpec,
}

impl CoordinationAgentHandoff {
    pub fn new() -> Self {
        Self {
            spec: coordination_agent_handoff_spec(),
        }
    }
}

impl Default for CoordinationAgentHandoff {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl Skill for CoordinationAgentHandoff {
    fn spec(&self) -> &SkillSpec {
        &self.spec
    }

    async fn run(&self, params: Value, _context: SkillContext) -> SkillResult {
        let current = optional_string_param(&params, "current_agent")?
            .unwrap_or_else(|| "unknown".to_owned());
        let task = string_param(&params, "task_description")?;
        let context = optional_string_param(&params, "context")?.unwrap_or_default();
        let mut matches = agent_matches(&task)
            .into_iter()
            .filter(|(agent, _, _)| *agent != current)
            .collect::<Vec<_>>();
        matches.sort_by(|a, b| b.2.cmp(&a.2));
        if matches.is_empty() {
            matches.push(("ops".to_owned(), "运营专家".to_owned(), 0));
        }
        let target = matches[0].clone();
        let alternatives = matches
            .iter()
            .skip(1)
            .take(2)
            .map(|(agent, name, score)| json!({"agent": agent, "name": name, "匹配度": score}))
            .collect::<Vec<_>>();

        Ok(SkillOutcome::new(json!({
            "当前Agent": current,
            "任务描述": task,
            "推荐交接": {
                "目标Agent": target.0,
                "Agent名称": target.1,
                "匹配度": target.2
            },
            "备选Agent": alternatives,
            "交接信息": {
                "任务摘要": task,
                "上下文": if context.is_empty() { "无额外上下文".to_owned() } else { context },
                "优先级": "中"
            }
        }))
        .with_summary("Agent交接建议已生成"))
    }
}

pub struct CoordinationTaskOrchestration {
    spec: SkillSpec,
}

impl CoordinationTaskOrchestration {
    pub fn new() -> Self {
        Self {
            spec: coordination_task_orchestration_spec(),
        }
    }
}

impl Default for CoordinationTaskOrchestration {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl Skill for CoordinationTaskOrchestration {
    fn spec(&self) -> &SkillSpec {
        &self.spec
    }

    async fn run(&self, params: Value, _context: SkillContext) -> SkillResult {
        object_params(&params)?;
        let goal = string_param(&params, "goal")?;
        let requirements = optional_string_vec_param(&params, "requirements")?.unwrap_or_default();
        let tasks = orchestration_template(&goal);
        Ok(SkillOutcome::new(json!({
            "目标": goal,
            "需求": if requirements.is_empty() { vec!["参考标准流程".to_owned()] } else { requirements },
            "任务编排": tasks,
            "总工期估算": "约7天（含并行）",
            "关键路径": "任务1 -> 任务2 -> 并行执行 -> 收尾",
            "风险提示": ["多Agent协作需同步边界和验收标准", "设计/内容/技术任务建议设置缓冲"],
            "编排方式": "Rust deterministic template"
        }))
        .with_summary("任务编排骨架已生成"))
    }
}

macro_rules! deferred_coordination_skill {
    ($type_name:ident, $spec_fn:ident, $status:literal, required [$($required:literal),*], defaults {$($key:literal => $value:expr),* $(,)?}) => {
        pub struct $type_name {
            spec: SkillSpec,
        }

        impl $type_name {
            pub fn new() -> Self {
                Self { spec: $spec_fn() }
            }
        }

        impl Default for $type_name {
            fn default() -> Self {
                Self::new()
            }
        }

        #[async_trait]
        impl Skill for $type_name {
            fn spec(&self) -> &SkillSpec {
                &self.spec
            }

            async fn run(&self, params: Value, _context: SkillContext) -> SkillResult {
                object_params(&params)?;
                $(let _ = string_param(&params, $required)?;)*
                let mut normalized = serde_json::Map::new();
                $(normalized.insert($key.to_owned(), json!($value(&params)?));)*
                Ok(SkillOutcome::new(json!({
                    "status": $status,
                    "approval_required": $status == "pending_approval",
                    "deferred_reason": "协调/平台动作尚未接入Rust runtime；本实现不执行数据库、网络或平台写动作",
                    "skill": self.spec.id,
                    "normalized_inputs": normalized,
                    "no_side_effects": true
                })).with_summary("协调技能骨架已返回"))
            }
        }
    };
}

deferred_coordination_skill!(
    CoordinationStatusQuery,
    coordination_status_query_spec,
    "deferred",
    required [],
    defaults {
        "query_type" => |p: &Value| Ok(optional_string_param(p, "query_type")?.unwrap_or_else(|| "system_health".to_owned())),
    }
);
deferred_coordination_skill!(
    CoordinationCreateCampaign,
    coordination_create_campaign_spec,
    "pending_approval",
    required ["name"],
    defaults {
        "name" => |p: &Value| string_param(p, "name").map(Some),
        "budget" => |p: &Value| optional_f64_param(p, "budget"),
        "product_id" => |p: &Value| optional_i64_param(p, "product_id"),
        "status" => |p: &Value| Ok(optional_string_param(p, "status")?.unwrap_or_else(|| "draft".to_owned())),
    }
);
deferred_coordination_skill!(
    CoordinationCreateTask,
    coordination_create_task_spec,
    "pending_approval",
    required ["title"],
    defaults {
        "title" => |p: &Value| string_param(p, "title").map(Some),
        "description" => |p: &Value| optional_string_param(p, "description"),
        "owner_role" => |p: &Value| Ok(optional_string_param(p, "owner_role")?.unwrap_or_else(|| "ops".to_owned())),
        "priority" => |p: &Value| Ok(optional_i64_param(p, "priority")?.unwrap_or(0)),
        "acceptance_criteria" => |p: &Value| optional_string_param(p, "acceptance_criteria"),
    }
);
deferred_coordination_skill!(
    CoordinationSaveMemory,
    coordination_save_memory_spec,
    "pending_approval",
    required ["key", "content"],
    defaults {
        "key" => |p: &Value| string_param(p, "key").map(Some),
        "content" => |p: &Value| string_param(p, "content").map(Some),
        "memory_type" => |p: &Value| Ok(optional_string_param(p, "memory_type")?.unwrap_or_else(|| "fact".to_owned())),
    }
);
deferred_coordination_skill!(
    PlatformUpdatePrice,
    platform_update_price_spec,
    "pending_approval",
    required ["platform", "product_id"],
    defaults {
        "platform" => |p: &Value| string_param(p, "platform").map(Some),
        "product_id" => |p: &Value| string_param(p, "product_id").map(Some),
        "new_price" => |p: &Value| Ok(optional_f64_param(p, "new_price")?),
        "sku_id" => |p: &Value| optional_string_param(p, "sku_id"),
    }
);
deferred_coordination_skill!(
    PlatformUpdateInventory,
    platform_update_inventory_spec,
    "pending_approval",
    required ["platform", "product_id"],
    defaults {
        "platform" => |p: &Value| string_param(p, "platform").map(Some),
        "product_id" => |p: &Value| string_param(p, "product_id").map(Some),
        "quantity" => |p: &Value| Ok(optional_i64_param(p, "quantity")?),
        "sku_id" => |p: &Value| optional_string_param(p, "sku_id"),
    }
);
deferred_coordination_skill!(
    PlatformSyncProducts,
    platform_sync_products_spec,
    "pending_approval",
    required ["platform"],
    defaults {
        "platform" => |p: &Value| string_param(p, "platform").map(Some),
        "page" => |p: &Value| Ok(optional_i64_param(p, "page")?.unwrap_or(1)),
        "page_size" => |p: &Value| Ok(optional_i64_param(p, "page_size")?.unwrap_or(20)),
    }
);
deferred_coordination_skill!(
    CoordinationExternalAgentCall,
    coordination_external_agent_call_spec,
    "pending_approval",
    required ["endpoint", "description"],
    defaults {
        "endpoint" => |p: &Value| string_param(p, "endpoint").map(Some),
        "method" => |p: &Value| Ok(optional_string_param(p, "method")?.unwrap_or_else(|| "GET".to_owned())),
        "payload" => |p: &Value| Ok(p.get("payload").cloned().unwrap_or_else(|| json!({}))),
        "description" => |p: &Value| string_param(p, "description").map(Some),
        "extract_key" => |p: &Value| optional_string_param(p, "extract_key"),
    }
);

pub struct CoordinationSkillChainPlanner {
    spec: SkillSpec,
}

impl CoordinationSkillChainPlanner {
    pub fn new() -> Self {
        Self {
            spec: coordination_skill_chain_planner_spec(),
        }
    }
}

impl Default for CoordinationSkillChainPlanner {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl Skill for CoordinationSkillChainPlanner {
    fn spec(&self) -> &SkillSpec {
        &self.spec
    }

    async fn run(&self, params: Value, _context: SkillContext) -> SkillResult {
        object_params(&params)?;
        let goal = string_param(&params, "goal")?;
        let role = optional_string_param(&params, "role")?.unwrap_or_else(|| "ops".to_owned());
        let action =
            optional_string_param(&params, "action")?.unwrap_or_else(|| "analyze".to_owned());
        let chain = skill_chain_template(&goal, &role, &action);
        Ok(SkillOutcome::new(json!({
            "任务目标": goal,
            "推荐执行链": chain.iter().enumerate().map(|(index, (skill, reason))| {
                json!({"步骤": index + 1, "技能": skill, "原因": reason})
            }).collect::<Vec<_>>(),
            "前置依赖说明": "未接registry，仅返回静态建议",
            "执行建议": "先加载数据，再分析，最后生成内容或提交需审批的写动作。",
            "总步骤数": chain.len(),
            "规划来源": "Rust deterministic template"
        }))
        .with_summary("技能链规划已生成"))
    }
}

fn agent_matches(task: &str) -> Vec<(String, String, i64)> {
    const AGENTS: &[(&str, &str, &[&str])] = &[
        (
            "ops",
            "运营专家",
            &[
                "运营", "促销", "活动", "推广", "投放", "选品", "定价", "库存",
            ],
        ),
        (
            "data",
            "数据分析师",
            &[
                "数据",
                "分析",
                "报表",
                "漏斗",
                "转化率",
                "趋势",
                "异常",
                "指标",
            ],
        ),
        (
            "service",
            "客服专家",
            &[
                "客服", "工单", "投诉", "退款", "退货", "售后", "DSR", "评价",
            ],
        ),
        (
            "design",
            "设计师",
            &["设计", "主图", "详情页", "配色", "海报", "视觉", "素材"],
        ),
        (
            "accounting",
            "财务分析师",
            &["财务", "成本", "利润", "预算", "ROI", "税务", "合规"],
        ),
        (
            "engineering",
            "技术工程师",
            &["技术", "架构", "bug", "性能", "部署", "开发", "接口"],
        ),
        (
            "web",
            "SEO专家",
            &["SEO", "网站", "店铺装修", "关键词", "标题", "转化", "建站"],
        ),
        (
            "creative",
            "内容创作者",
            &["视频", "文案", "种草", "直播", "IP", "内容", "创意"],
        ),
    ];

    AGENTS
        .iter()
        .copied()
        .map(|(agent, name, keywords)| {
            let score = keywords
                .iter()
                .filter(|keyword| task.contains(**keyword))
                .count() as i64;
            (agent.to_owned(), name.to_owned(), score)
        })
        .filter(|(_, _, score)| *score > 0)
        .collect()
}

fn orchestration_template(goal: &str) -> Vec<Value> {
    let launch = goal.contains("新品") || goal.contains("上新");
    let promo = goal.contains("大促") || goal.contains("活动") || goal.contains("618");
    let rows = if launch {
        vec![
            ("竞品分析与市场调研", "data", "2天", "无", "竞品报告"),
            (
                "定价策略与成本核算",
                "accounting",
                "1天",
                "任务1",
                "定价方案",
            ),
            ("主图与详情页设计", "design", "3天", "任务1", "设计稿"),
            ("商品文案与SEO标题", "web", "1天", "任务3", "优化文案"),
            ("种草内容与脚本", "creative", "2天", "任务3", "内容素材"),
            ("客服话术准备", "service", "1天", "任务4", "话术库"),
            ("上线推广与监控", "ops", "持续", "任务4,5,6", "上线运营"),
        ]
    } else if promo {
        vec![
            ("历史数据分析与目标设定", "data", "1天", "无", "数据报告"),
            ("活动策划与预算分配", "ops", "2天", "任务1", "活动方案"),
            (
                "财务预算与盈亏测算",
                "accounting",
                "1天",
                "任务2",
                "预算报告",
            ),
            ("活动海报与素材设计", "design", "2天", "任务2", "视觉素材"),
            ("直播脚本与种草内容", "creative", "1天", "任务2", "内容脚本"),
            ("客服应急话术与预案", "service", "1天", "任务2", "应急手册"),
            (
                "技术压测与系统保障",
                "engineering",
                "1天",
                "任务2",
                "压测报告",
            ),
        ]
    } else {
        vec![
            ("现状分析与数据调研", "data", "1天", "无", "分析报告"),
            ("策略与方案制定", "ops", "1天", "任务1", "执行方案"),
            ("财务可行性评估", "accounting", "1天", "任务2", "财务测算"),
            ("视觉设计", "design", "2天", "任务2", "设计稿"),
            ("内容与文案制作", "creative", "2天", "任务2", "内容素材"),
            ("执行落地与效果监控", "ops", "持续", "任务3,4,5", "执行报告"),
        ]
    };
    rows.into_iter()
        .enumerate()
        .map(|(idx, (task, agent, duration, dep, output))| {
            json!({
                "序号": idx + 1,
                "任务": task,
                "Agent": agent,
                "耗时": duration,
                "依赖": dep,
                "目标产出": output
            })
        })
        .collect()
}

fn skill_chain_template(goal: &str, role: &str, action: &str) -> Vec<(&'static str, &'static str)> {
    let goal_lower = goal.to_lowercase();
    if goal_lower.contains("nps") || goal.contains("满意度") {
        return vec![
            ("service_nps_analyzer", "计算NPS/CSAT基线"),
            ("service_nps_driver_analysis", "识别满意度驱动因素"),
            ("service_dsr_improvement", "生成服务改善动作"),
        ];
    }
    if goal_lower.contains("seo") || goal.contains("关键词") || role == "web" {
        return vec![
            ("web_keyword_research", "建立关键词矩阵"),
            ("web_title_seo_scorer", "评分当前标题"),
            ("web_seo_optimize", "输出优化方案"),
        ];
    }
    if goal.contains("设计") || goal.contains("主图") || role == "design" {
        return vec![
            ("design_material_spec", "确定素材规格"),
            ("design_main_image", "生成主图方案"),
            ("design_detail_page", "规划详情页"),
        ];
    }
    if goal.contains("技术") || goal_lower.contains("bug") || role == "engineering" {
        return vec![
            ("engineering_system_check", "检查平台/接口状态"),
            ("engineering_bug_analysis", "定位技术问题"),
            ("engineering_perf_optimize", "给出优化方案"),
        ];
    }
    if action == "create" {
        return vec![
            ("data_query_store_metrics", "加载真实业务数据作为输入"),
            ("creative_video_script", "生成内容资产"),
            ("coordination_create_task", "提交需审批的任务创建动作"),
        ];
    }
    vec![
        ("data_query_store_metrics", "加载真实业务数据作为分析基础"),
        ("ops_execution_plan", "生成可落地执行方案"),
        ("coordination_create_task", "提交需审批的工作任务"),
    ]
}
