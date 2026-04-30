use async_trait::async_trait;
use serde_json::{Value, json};

use crate::skills::{
    Skill, SkillCategory, SkillContext, SkillInputField, SkillOutcome, SkillPriority, SkillResult,
    SkillSpec, object_params, optional_bool_param, optional_f64_param, optional_i64_param,
    optional_string_param, optional_string_vec_param, string_param,
};

fn category() -> SkillCategory {
    SkillCategory::Custom("engineering".to_owned())
}

pub fn specs() -> Vec<SkillSpec> {
    vec![
        engineering_arch_review_spec(),
        engineering_bug_analysis_spec(),
        engineering_perf_optimize_spec(),
        engineering_tech_selection_spec(),
        engineering_system_check_spec(),
        engineering_sla_monitor_spec(),
    ]
}

pub fn engineering_arch_review_spec() -> SkillSpec {
    deferred_spec(
        "engineering_arch_review",
        "架构评估",
        "AI深度评审系统架构，输出5维评分/核心风险/分级改造方案/大促备战清单",
        vec![
            SkillInputField::required(
                "system_type",
                "系统类型：电商平台/ERP/CRM/数据平台/小程序",
                json!({"type": "string"}),
            ),
            SkillInputField::optional("daily_orders", "日均订单量", json!({"type": "integer"})),
            SkillInputField::optional(
                "tech_stack",
                "技术栈列表（如 Python/MySQL/Redis/Nginx）",
                json!({"type": "array", "items": {"type": "string"}}),
            ),
            SkillInputField::optional(
                "pain_points",
                "当前主要痛点",
                json!({"type": "array", "items": {"type": "string"}}),
            ),
            SkillInputField::optional(
                "current_metrics",
                "当前系统指标，如 {响应时间: '200ms', QPS: 500}",
                json!({"type": "object"}),
            ),
        ],
    )
}

pub fn engineering_bug_analysis_spec() -> SkillSpec {
    deferred_spec(
        "engineering_bug_analysis",
        "Bug排查方案",
        "AI深度诊断Bug，输出根因分析/10步排查流程/修复代码示例/临时止血方案/预防措施",
        vec![
            SkillInputField::required(
                "error_type",
                "错误类型：接口超时/数据异常/页面白屏/支付失败/库存不一致/OOM/死锁等",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "error_message",
                "完整错误信息或日志片段",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "frequency",
                "频率：偶发/频繁/必现",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "tech_stack",
                "技术栈，如 Python/FastAPI/MySQL",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "context",
                "发生场景（如大促/特定操作/定时任务）",
                json!({"type": "string"}),
            ),
        ],
    )
}

pub fn engineering_perf_optimize_spec() -> SkillSpec {
    deferred_spec(
        "engineering_perf_optimize",
        "性能优化方案",
        "AI生成具体性能优化方案，含代码示例/配置参数/分阶段实施计划/压测脚本",
        vec![
            SkillInputField::required(
                "bottleneck",
                "瓶颈类型：数据库/接口/前端/缓存/并发/网络",
                json!({"type": "string"}),
            ),
            SkillInputField::optional("current_qps", "当前QPS/TPS", json!({"type": "number"})),
            SkillInputField::optional("target_qps", "目标QPS/TPS", json!({"type": "number"})),
            SkillInputField::optional("tech_stack", "技术栈", json!({"type": "string"})),
            SkillInputField::optional(
                "metrics",
                "当前系统指标，如 {P99延迟: '1200ms', CPU使用率: '85%'}",
                json!({"type": "object"}),
            ),
        ],
    )
}

pub fn engineering_tech_selection_spec() -> SkillSpec {
    deferred_spec(
        "engineering_tech_selection",
        "技术选型",
        "AI生成技术选型深度报告，含真实性能数据对比/迁移方案/POC验证清单",
        vec![
            SkillInputField::required(
                "domain",
                "选型领域：Web框架/数据库/消息队列/搜索引擎/缓存/容器编排/监控",
                json!({"type": "string"}),
            ),
            SkillInputField::optional(
                "requirements",
                "核心需求列表",
                json!({"type": "array", "items": {"type": "string"}}),
            ),
            SkillInputField::optional(
                "constraints",
                "约束条件（预算/团队技能/合规/时间）",
                json!({"type": "array", "items": {"type": "string"}}),
            ),
            SkillInputField::optional("team_size", "团队规模", json!({"type": "integer"})),
            SkillInputField::optional(
                "current_stack",
                "现有技术栈（迁移评估用）",
                json!({"type": "string"}),
            ),
        ],
    )
}

pub fn engineering_system_check_spec() -> SkillSpec {
    SkillSpec::new(
        "engineering_system_check",
        "平台接口健康检查",
        "检查用户已配置的电商平台API连接状态，诊断同步和接口问题",
    )
    .with_category(category())
    .with_priority(SkillPriority::High)
    .with_input(SkillInputField::optional(
        "test_live",
        "是否实际调用平台API测试连通性（默认仅检查配置）",
        json!({"type": "boolean"}),
    ))
    .with_input(SkillInputField::optional(
        "platform",
        "指定平台（留空则检查全部已配置平台）",
        json!({"type": "string"}),
    ))
    .with_tag("deferred")
    .with_tag("pending_approval")
}

pub fn engineering_sla_monitor_spec() -> SkillSpec {
    SkillSpec::new(
        "engineering_sla_monitor",
        "SLA监控",
        "P50/P90/P99延迟分位数分析 + 可用性SLO监控 + 错误预算消耗率计算",
    )
    .with_category(category())
    .with_priority(SkillPriority::Critical)
    .with_input(SkillInputField::optional(
        "latency_samples",
        "延迟样本列表（毫秒），至少10个数据点",
        json!({"type": "array", "items": {"type": "number"}}),
    ))
    .with_input(SkillInputField::optional(
        "p50_ms",
        "P50延迟（毫秒）",
        json!({"type": "number"}),
    ))
    .with_input(SkillInputField::optional(
        "p90_ms",
        "P90延迟（毫秒）",
        json!({"type": "number"}),
    ))
    .with_input(SkillInputField::optional(
        "p99_ms",
        "P99延迟（毫秒）",
        json!({"type": "number"}),
    ))
    .with_input(SkillInputField::optional(
        "total_requests",
        "统计周期内总请求数",
        json!({"type": "integer"}),
    ))
    .with_input(SkillInputField::optional(
        "error_count",
        "错误请求数",
        json!({"type": "integer"}),
    ))
    .with_input(SkillInputField::optional(
        "downtime_minutes",
        "本月宕机分钟数",
        json!({"type": "number"}),
    ))
    .with_input(SkillInputField::optional(
        "period_days",
        "统计周期天数，默认30天",
        json!({"type": "integer"}),
    ))
    .with_input(SkillInputField::optional(
        "slo_availability",
        "可用性SLO目标（%），默认99.9",
        json!({"type": "number"}),
    ))
    .with_input(SkillInputField::optional(
        "slo_p99_ms",
        "P99延迟SLO目标（毫秒），默认2000",
        json!({"type": "number"}),
    ))
    .with_input(SkillInputField::optional(
        "system_name",
        "系统名称",
        json!({"type": "string"}),
    ))
    .with_tag("deterministic")
}

pub struct EngineeringSlaMonitor {
    spec: SkillSpec,
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
            .with_priority(SkillPriority::High)
            .with_tag("deferred")
            .with_tag("analysis_engine"),
        SkillSpec::with_input,
    )
}

macro_rules! deferred_engineering_skill {
    ($type_name:ident, $spec_fn:ident, required [$($required:literal),*], defaults {$($key:literal => $value:expr),* $(,)?}, deps [$($dep:literal),* $(,)?]) => {
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
                    "status": "deferred",
                    "deferred_reason": "工程分析技能需要技术上下文、搜索资料或分析引擎接入",
                    "skill": self.spec.id,
                    "normalized_inputs": normalized,
                    "deferred_dependencies": [$($dep),*],
                    "expected_outputs": ["诊断摘要", "风险分级", "实施步骤", "验证清单"]
                })).with_summary("工程技能骨架已返回"))
            }
        }
    };
}

deferred_engineering_skill!(
    EngineeringArchReview,
    engineering_arch_review_spec,
    required ["system_type"],
    defaults {
        "system_type" => |p: &Value| string_param(p, "system_type").map(Some),
        "daily_orders" => |p: &Value| Ok(optional_i64_param(p, "daily_orders")?.unwrap_or(1000)),
        "tech_stack" => |p: &Value| Ok(optional_string_vec_param(p, "tech_stack")?.unwrap_or_default()),
        "pain_points" => |p: &Value| Ok(optional_string_vec_param(p, "pain_points")?.unwrap_or_default()),
        "current_metrics" => |p: &Value| Ok(p.get("current_metrics").cloned().unwrap_or_else(|| json!({}))),
    },
    deps ["architecture_review_engine", "business_metrics"]
);
deferred_engineering_skill!(
    EngineeringBugAnalysis,
    engineering_bug_analysis_spec,
    required ["error_type"],
    defaults {
        "error_type" => |p: &Value| string_param(p, "error_type").map(Some),
        "error_message" => |p: &Value| optional_string_param(p, "error_message"),
        "frequency" => |p: &Value| Ok(optional_string_param(p, "frequency")?.unwrap_or_else(|| "偶发".to_owned())),
        "tech_stack" => |p: &Value| optional_string_param(p, "tech_stack"),
        "context" => |p: &Value| optional_string_param(p, "context"),
    },
    deps ["debug_knowledge", "fresh_best_practices"]
);
deferred_engineering_skill!(
    EngineeringPerfOptimize,
    engineering_perf_optimize_spec,
    required ["bottleneck"],
    defaults {
        "bottleneck" => |p: &Value| string_param(p, "bottleneck").map(Some),
        "current_qps" => |p: &Value| Ok(optional_f64_param(p, "current_qps")?.unwrap_or(500.0)),
        "target_qps" => |p: &Value| Ok(optional_f64_param(p, "target_qps")?.unwrap_or(2000.0)),
        "tech_stack" => |p: &Value| optional_string_param(p, "tech_stack"),
        "metrics" => |p: &Value| Ok(p.get("metrics").cloned().unwrap_or_else(|| json!({}))),
    },
    deps ["performance_plan_engine"]
);
deferred_engineering_skill!(
    EngineeringTechSelection,
    engineering_tech_selection_spec,
    required ["domain"],
    defaults {
        "domain" => |p: &Value| string_param(p, "domain").map(Some),
        "requirements" => |p: &Value| Ok(optional_string_vec_param(p, "requirements")?.unwrap_or_default()),
        "constraints" => |p: &Value| Ok(optional_string_vec_param(p, "constraints")?.unwrap_or_default()),
        "team_size" => |p: &Value| Ok(optional_i64_param(p, "team_size")?.unwrap_or(5)),
        "current_stack" => |p: &Value| optional_string_param(p, "current_stack"),
    },
    deps ["fresh_benchmarks", "tech_selection_engine"]
);

pub struct EngineeringSystemCheck {
    spec: SkillSpec,
}

impl EngineeringSystemCheck {
    pub fn new() -> Self {
        Self {
            spec: engineering_system_check_spec(),
        }
    }
}

impl Default for EngineeringSystemCheck {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl Skill for EngineeringSystemCheck {
    fn spec(&self) -> &SkillSpec {
        &self.spec
    }

    async fn run(&self, params: Value, _context: SkillContext) -> SkillResult {
        object_params(&params)?;
        let test_live = optional_bool_param(&params, "test_live")?.unwrap_or(false);
        let platform = optional_string_param(&params, "platform")?;
        let status = if test_live {
            "pending_approval"
        } else {
            "deferred"
        };
        Ok(SkillOutcome::new(json!({
            "status": status,
            "approval_required": test_live,
            "deferred_reason": if test_live { "实际调用平台API健康检查属于外部平台动作，需要显式审批和适配器接入" } else { "平台连接配置读取尚未接入Rust skill runtime" },
            "skill": self.spec.id,
            "platform": platform,
            "planned_checks": ["凭证完整性", "连接配置", "最近同步状态", "错误码分布"],
            "no_side_effects": true
        })).with_summary("平台接口健康检查骨架已返回"))
    }
}

impl EngineeringSlaMonitor {
    pub fn new() -> Self {
        Self {
            spec: engineering_sla_monitor_spec(),
        }
    }
}

impl Default for EngineeringSlaMonitor {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl Skill for EngineeringSlaMonitor {
    fn spec(&self) -> &SkillSpec {
        &self.spec
    }

    async fn run(&self, params: Value, _context: SkillContext) -> SkillResult {
        object_params(&params)?;
        let samples = params
            .get("latency_samples")
            .and_then(Value::as_array)
            .map(|items| items.iter().filter_map(Value::as_f64).collect::<Vec<_>>())
            .unwrap_or_default();

        let (p50, p90, p99) = if samples.is_empty() {
            (
                optional_f64_param(&params, "p50_ms")?.unwrap_or(150.0),
                optional_f64_param(&params, "p90_ms")?.unwrap_or(400.0),
                optional_f64_param(&params, "p99_ms")?.unwrap_or(1800.0),
            )
        } else {
            (
                percentile(samples.clone(), 50.0),
                percentile(samples.clone(), 90.0),
                percentile(samples, 99.0),
            )
        };

        let period_days = optional_i64_param(&params, "period_days")?
            .unwrap_or(30)
            .max(1);
        let system_name =
            optional_string_param(&params, "system_name")?.unwrap_or_else(|| "电商系统".to_owned());
        let total_requests = optional_i64_param(&params, "total_requests")?
            .unwrap_or(0)
            .max(0);
        let error_count = optional_i64_param(&params, "error_count")?
            .unwrap_or(0)
            .max(0);
        let downtime_min = optional_f64_param(&params, "downtime_minutes")?
            .unwrap_or(0.0)
            .max(0.0);
        let slo_avail = optional_f64_param(&params, "slo_availability")?.unwrap_or(99.9);
        let slo_p99 = optional_f64_param(&params, "slo_p99_ms")?.unwrap_or(2000.0);
        let period_minutes = period_days as f64 * 24.0 * 60.0;

        let availability = if downtime_min > 0.0 {
            round4((1.0 - downtime_min / period_minutes) * 100.0)
        } else if total_requests > 0 {
            round4((1.0 - error_count as f64 / total_requests as f64) * 100.0)
        } else {
            99.9
        };

        let budget_total = (1.0 - slo_avail / 100.0) * period_minutes;
        let budget_used_pct = if budget_total > 0.0 {
            round1(downtime_min / budget_total * 100.0)
        } else {
            0.0
        };
        let budget_left_pct = round1(100.0 - budget_used_pct);
        let p99_pass = p99 <= slo_p99;
        let p90_pass = p90 <= 500.0;
        let availability_pass = availability >= slo_avail;
        let violations = [p99_pass, p90_pass, availability_pass]
            .iter()
            .filter(|passed| !**passed)
            .count();
        let peak_p99 = p99 * 2.5;

        let mut output = json!({
            "系统": system_name,
            "统计周期": format!("{period_days}天"),
            "延迟分位数": {
                "P50（中位数）": format!("{p50:.0}ms"),
                "P90（90%用户体验）": format!("{p90:.0}ms"),
                "P99（长尾体验）": format!("{p99:.0}ms"),
                "目标P99": format!("{slo_p99:.0}ms"),
                "P99状态": if p99_pass { "达标".to_owned() } else { format!("超标{}%", round1((p99 - slo_p99) / slo_p99 * 100.0)) }
            },
            "可用性": {
                "实际可用性": format!("{availability:.3}%"),
                "SLO目标": format!("{slo_avail:.3}%"),
                "本期宕机": format!("{downtime_min:.1}分钟"),
                "状态": if availability_pass { "达标" } else { "未达标" }
            },
            "错误预算": {
                "本期可用预算": format!("{budget_total:.1}分钟"),
                "已消耗": format!("{downtime_min:.1}分钟 ({budget_used_pct}%)"),
                "剩余预算": format!("{budget_left_pct}%"),
                "预算状态": budget_state(budget_left_pct)
            },
            "SLO达标情况": [
                {"指标": "P99延迟", "目标": slo_p99, "实际": round1(p99), "状态": if p99_pass { "达标" } else { "未达标" }},
                {"指标": "P90延迟", "目标": 500, "实际": round1(p90), "状态": if p90_pass { "达标" } else { "未达标" }},
                {"指标": "可用性", "目标": slo_avail, "实际": availability, "状态": if availability_pass { "达标" } else { "未达标" }}
            ],
            "SLO违规项数": violations,
            "大促峰值风险": {
                "预估大促P99": format!("{peak_p99:.0}ms（当前×2.5倍流量）"),
                "风险等级": if peak_p99 > slo_p99 * 3.0 { "高风险" } else if peak_p99 > slo_p99 * 1.5 { "中风险" } else { "低风险" }
            }
        });

        if total_requests > 0 {
            output["请求统计"] = json!({
                "总请求数": total_requests,
                "错误数": error_count,
                "错误率": format!("{:.3}%", error_count as f64 / total_requests as f64 * 100.0)
            });
        }

        Ok(SkillOutcome::new(output).with_summary("SLA监控分析已完成"))
    }
}

fn percentile(mut data: Vec<f64>, pct: f64) -> f64 {
    if data.is_empty() {
        return 0.0;
    }
    data.sort_by(|a, b| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal));
    let idx = pct / 100.0 * (data.len() - 1) as f64;
    let lo = idx.floor() as usize;
    let hi = (lo + 1).min(data.len() - 1);
    let frac = idx - lo as f64;
    data[lo] * (1.0 - frac) + data[hi] * frac
}

fn round1(value: f64) -> f64 {
    (value * 10.0).round() / 10.0
}

fn round4(value: f64) -> f64 {
    (value * 10_000.0).round() / 10_000.0
}

fn budget_state(left: f64) -> &'static str {
    if left <= 0.0 {
        "预算耗尽"
    } else if left < 20.0 {
        "预算紧张"
    } else if left < 50.0 {
        "预算警告"
    } else {
        "预算充足"
    }
}
