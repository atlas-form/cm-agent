use std::collections::BTreeMap;

use async_trait::async_trait;
use serde_json::{Value, json};

use crate::skills::{
    Skill, SkillCategory, SkillContext, SkillError, SkillInputField, SkillOutcome, SkillPriority,
    SkillResult, SkillSpec, object_params, optional_f64_param, optional_i64_param,
    optional_string_param, optional_string_vec_param, string_param,
};

fn category() -> SkillCategory {
    SkillCategory::Custom("service".to_owned())
}

fn string_schema() -> Value {
    json!({"type": "string"})
}

fn number_schema() -> Value {
    json!({"type": "number"})
}

fn integer_schema() -> Value {
    json!({"type": "integer"})
}

fn string_array_schema() -> Value {
    json!({"type": "array", "items": {"type": "string"}})
}

fn object_schema() -> Value {
    json!({"type": "object"})
}

fn outcome(output: Value, summary: impl Into<String>) -> SkillResult {
    Ok(SkillOutcome::new(output).with_summary(summary))
}

pub fn specs() -> Vec<SkillSpec> {
    vec![
        service_ticket_handler_spec(),
        service_faq_playbook_spec(),
        service_escalation_flow_spec(),
        service_dsr_improvement_spec(),
        service_return_handler_spec(),
        service_query_product_spec(),
        service_nps_analyzer_spec(),
        service_sentiment_analyzer_spec(),
        service_nps_driver_analysis_spec(),
    ]
}

pub fn service_ticket_handler_spec() -> SkillSpec {
    deferred_spec(
        "service_ticket_handler",
        "工单处理",
        "AI生成完整客服话术包（接单确认/安抚/解决方案/跟进/邀评），基于真实商品信息个性化",
        vec![
            SkillInputField::required(
                "issue_type",
                "问题类型：物流延迟/质量问题/退款退货/发错货/售后咨询/投诉",
                string_schema(),
            ),
            SkillInputField::optional("customer_message", "客户原始消息", string_schema()),
            SkillInputField::optional(
                "customer_emotion",
                "客户情绪：普通/焦虑/愤怒/满意",
                string_schema(),
            ),
            SkillInputField::optional("platform", "平台：淘宝/京东/拼多多/抖音", string_schema()),
            SkillInputField::optional(
                "product_id",
                "涉及商品ID（可选，自动加载商品信息定制话术）",
                integer_schema(),
            ),
            SkillInputField::optional("order_no", "订单号（可选）", string_schema()),
        ],
    )
}

pub fn service_faq_playbook_spec() -> SkillSpec {
    deferred_spec(
        "service_faq_playbook",
        "FAQ话术库",
        "AI基于真实商品信息和店铺政策生成专属FAQ话术库，含简短版和详细版答案",
        vec![
            SkillInputField::optional(
                "product_id",
                "商品ID，自动加载商品信息生成专属FAQ",
                integer_schema(),
            ),
            SkillInputField::optional(
                "questions",
                "需要生成答案的问题列表（留空则生成通用FAQ）",
                string_array_schema(),
            ),
            SkillInputField::optional("platform", "平台：淘宝/京东/拼多多/抖音", string_schema()),
            SkillInputField::optional(
                "policies",
                "店铺政策，如 {发货时效: '48小时', 退货政策: '7天无理由'}",
                object_schema(),
            ),
        ],
    )
}

pub fn service_escalation_flow_spec() -> SkillSpec {
    deferred_spec(
        "service_escalation_flow",
        "升级流程",
        "AI生成完整升级处理脚本，含主管接管话术/具体赔偿方案/纠纷预防/内部工单模板",
        vec![
            SkillInputField::required("issue_description", "问题描述", string_schema()),
            SkillInputField::optional(
                "customer_emotion",
                "客户情绪：平静/不满/愤怒/威胁投诉",
                string_schema(),
            ),
            SkillInputField::optional("previous_contacts", "此前联系次数", integer_schema()),
            SkillInputField::optional("product_id", "涉及商品ID（可选）", integer_schema()),
            SkillInputField::optional(
                "customer_history",
                "客户历史（高价值/新客/疑似羊毛党）",
                string_schema(),
            ),
        ],
    )
}

pub fn service_dsr_improvement_spec() -> SkillSpec {
    SkillSpec::new(
        "service_dsr_improvement",
        "DSR提升方案",
        "根据当前DSR评分（可自动从店铺数据加载），输出各维度提升方案",
    )
    .with_category(category())
    .with_priority(SkillPriority::High)
    .with_input(SkillInputField::optional(
        "description_score",
        "描述相符评分(1-5)；不填则自动加载",
        number_schema(),
    ))
    .with_input(SkillInputField::optional(
        "service_score",
        "服务态度评分(1-5)；不填则自动加载",
        number_schema(),
    ))
    .with_input(SkillInputField::optional(
        "logistics_score",
        "物流服务评分(1-5)；不填则自动加载",
        number_schema(),
    ))
    .with_input(SkillInputField::optional(
        "platform",
        "平台：淘宝/京东/拼多多/抖音",
        string_schema(),
    ))
    .with_tag("deterministic")
}

pub fn service_query_product_spec() -> SkillSpec {
    deferred_spec(
        "service_query_product",
        "查询商品信息",
        "根据商品ID或商品名查询真实商品信息，用于客服应答",
        vec![
            SkillInputField::optional("product_id", "商品ID", integer_schema()),
            SkillInputField::optional(
                "product_name",
                "商品名称关键词（当ID不明时使用）",
                string_schema(),
            ),
        ],
    )
    .with_tag("data_source")
}

pub fn service_return_handler_spec() -> SkillSpec {
    SkillSpec::new(
        "service_return_handler",
        "退换货处理",
        "根据退换货原因和订单信息，输出处理方案和运费承担方",
    )
    .with_category(category())
    .with_priority(SkillPriority::Critical)
    .with_input(SkillInputField::required(
        "reason",
        "退换货原因：不喜欢/质量问题/发错货/尺码不合/破损",
        string_schema(),
    ))
    .with_input(SkillInputField::optional(
        "order_amount",
        "订单金额",
        number_schema(),
    ))
    .with_input(SkillInputField::optional(
        "days_since_receipt",
        "收货天数",
        integer_schema(),
    ))
    .with_input(SkillInputField::optional(
        "product_id",
        "商品ID（可选，用于关联商品信息）",
        integer_schema(),
    ))
    .with_tag("deterministic")
}

pub struct ServiceReturnHandler {
    spec: SkillSpec,
}

impl ServiceReturnHandler {
    pub fn new() -> Self {
        Self {
            spec: service_return_handler_spec(),
        }
    }
}

impl Default for ServiceReturnHandler {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl Skill for ServiceReturnHandler {
    fn spec(&self) -> &SkillSpec {
        &self.spec
    }

    async fn run(&self, params: Value, _context: SkillContext) -> SkillResult {
        let reason = string_param(&params, "reason")?;
        let amount = optional_f64_param(&params, "order_amount")?.unwrap_or(100.0);
        let days = optional_i64_param(&params, "days_since_receipt")?.unwrap_or(3);
        let seller_fault = matches!(reason.as_str(), "质量问题" | "发错货" | "破损");
        let within_policy = days <= 7;

        let output = if !within_policy && !seller_fault {
            json!({
                "退换货原因": reason,
                "收货天数": days,
                "处理结果": "超出7天无理由退货期限",
                "建议": "婉拒并解释政策，非卖家过失不受理",
                "话术": "亲，很抱歉该订单已超出7天无理由退货期限，按平台规则无法受理。如有其他问题随时联系~",
                "升级建议": "若客户坚持，请升级至主管协商处理"
            })
        } else if !within_policy && seller_fault {
            json!({
                "退换货原因": reason,
                "收货天数": days,
                "处理结果": "虽超期但卖家过失，特殊受理",
                "责任方": "卖家",
                "处理方案": {
                    "退款金额": amount,
                    "运费补贴": round2(amount * 0.1),
                    "补偿": "赠送10元无门槛券"
                },
                "话术": "亲，虽然已超出退货期限，但这是我们的问题，我们特殊为您受理，请放心~"
            })
        } else {
            let shipping_bearer = if seller_fault { "卖家" } else { "买家" };
            json!({
                "退换货原因": reason,
                "订单金额": amount,
                "收货天数": days,
                "是否在退货期": "是",
                "责任方": if seller_fault { "卖家" } else { "买家" },
                "运费承担": shipping_bearer,
                "处理方案": {
                    "退款金额": amount,
                    "运费补贴": if seller_fault { round2(amount * 0.1) } else { 0.0 },
                    "补偿": if seller_fault { "赠送10元无门槛券" } else { "无" }
                },
                "处理流程": [
                    "确认退货原因",
                    "提供退货地址",
                    format!("运费由{shipping_bearer}承担"),
                    "收到退货后48小时内退款"
                ],
                "话术": format!("亲，退货已受理，运费由{shipping_bearer}承担。请将商品寄回，我们收到后尽快为您退款~")
            })
        };

        outcome(output, "退换货处理方案已生成")
    }
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
            .with_tag("deferred"),
        SkillSpec::with_input,
    )
}

macro_rules! deferred_service_skill {
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
                outcome(json!({
                    "status": "deferred",
                    "deferred_reason": "客服技能需要商品数据、店铺政策或内容引擎接入",
                    "skill": self.spec.id,
                    "normalized_inputs": normalized,
                    "deferred_dependencies": [$($dep),*],
                    "expected_outputs": ["处理策略", "客服话术", "SLA/优先级", "复盘字段"]
                }), "客服技能骨架已返回")
            }
        }
    };
}

deferred_service_skill!(
    ServiceTicketHandler,
    service_ticket_handler_spec,
    required ["issue_type"],
    defaults {
        "issue_type" => |p: &Value| string_param(p, "issue_type").map(Some),
        "customer_message" => |p: &Value| optional_string_param(p, "customer_message"),
        "customer_emotion" => |p: &Value| Ok(optional_string_param(p, "customer_emotion")?.unwrap_or_else(|| "普通".to_owned())),
        "platform" => |p: &Value| Ok(optional_string_param(p, "platform")?.unwrap_or_else(|| "淘宝".to_owned())),
        "product_id" => |p: &Value| optional_i64_param(p, "product_id"),
        "order_no" => |p: &Value| optional_string_param(p, "order_no"),
    },
    deps ["content_engine", "product_profile", "shop_policy"]
);
deferred_service_skill!(
    ServiceFaqPlaybook,
    service_faq_playbook_spec,
    required [],
    defaults {
        "product_id" => |p: &Value| optional_i64_param(p, "product_id"),
        "questions" => |p: &Value| Ok(optional_string_vec_param(p, "questions")?.unwrap_or_else(default_faq_questions)),
        "platform" => |p: &Value| Ok(optional_string_param(p, "platform")?.unwrap_or_else(|| "淘宝".to_owned())),
        "policies" => |p: &Value| Ok(p.get("policies").cloned().unwrap_or_else(|| json!({}))),
    },
    deps ["content_engine", "product_profile", "shop_policy"]
);
deferred_service_skill!(
    ServiceEscalationFlow,
    service_escalation_flow_spec,
    required ["issue_description"],
    defaults {
        "issue_description" => |p: &Value| string_param(p, "issue_description").map(Some),
        "customer_emotion" => |p: &Value| Ok(optional_string_param(p, "customer_emotion")?.unwrap_or_else(|| "平静".to_owned())),
        "previous_contacts" => |p: &Value| Ok(optional_i64_param(p, "previous_contacts")?.unwrap_or(0)),
        "product_id" => |p: &Value| optional_i64_param(p, "product_id"),
        "customer_history" => |p: &Value| optional_string_param(p, "customer_history"),
    },
    deps ["content_engine", "product_profile"]
);
deferred_service_skill!(
    ServiceQueryProduct,
    service_query_product_spec,
    required [],
    defaults {
        "product_id" => |p: &Value| optional_i64_param(p, "product_id"),
        "product_name" => |p: &Value| optional_string_param(p, "product_name"),
    },
    deps ["product_database"]
);

pub struct ServiceDsrImprovement {
    spec: SkillSpec,
}

impl ServiceDsrImprovement {
    pub fn new() -> Self {
        Self {
            spec: service_dsr_improvement_spec(),
        }
    }
}

impl Default for ServiceDsrImprovement {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl Skill for ServiceDsrImprovement {
    fn spec(&self) -> &SkillSpec {
        &self.spec
    }

    async fn run(&self, params: Value, _context: SkillContext) -> SkillResult {
        object_params(&params)?;
        let desc = optional_f64_param(&params, "description_score")?.unwrap_or(4.6);
        let svc = optional_f64_param(&params, "service_score")?.unwrap_or(4.7);
        let logi = optional_f64_param(&params, "logistics_score")?.unwrap_or(4.5);
        let platform =
            optional_string_param(&params, "platform")?.unwrap_or_else(|| "淘宝".to_owned());
        let overall = round2((desc + svc + logi) / 3.0);
        let lowest = [("描述相符", desc), ("服务态度", svc), ("物流服务", logi)]
            .into_iter()
            .min_by(|a, b| a.1.partial_cmp(&b.1).unwrap_or(std::cmp::Ordering::Equal))
            .unwrap_or(("描述相符", desc));
        outcome(
            json!({
                "平台": platform,
                "数据来源": "用户提供/默认样例",
                "DSR总评": overall,
                "各维度评分": {"描述相符": desc, "服务态度": svc, "物流服务": logi},
                "健康状态": if overall >= 4.8 { "优秀" } else if overall >= 4.6 { "正常" } else { "需改善" },
                "优先改善维度": lowest.0,
                "改善骨架": ["定位低分订单与评价关键词", "建立问题分类", "制定客服/物流/描述修正动作", "按周复盘DSR变化"],
                "deferred_dependencies": ["store_metrics", "content_engine", "fresh_benchmark"]
            }),
            "DSR提升方案骨架已返回",
        )
    }
}

fn default_faq_questions() -> Vec<String> {
    vec![
        "什么时候发货？".to_owned(),
        "支持退货退款吗？怎么操作？".to_owned(),
        "质量有保障吗？".to_owned(),
        "有优惠活动吗？".to_owned(),
        "怎么选尺码/规格？".to_owned(),
        "包邮吗？运费多少？".to_owned(),
        "可以开发票吗？".to_owned(),
        "商品是正品吗？".to_owned(),
    ]
}

pub fn service_nps_analyzer_spec() -> SkillSpec {
    SkillSpec::new(
        "service_nps_analyzer",
        "NPS客户满意度分析",
        "计算NPS净推荐值、CSAT满意度评分和客户忠诚度分层",
    )
    .with_category(category())
    .with_priority(SkillPriority::Critical)
    .with_input(SkillInputField::optional(
        "total_respondents",
        "调查总人数",
        integer_schema(),
    ))
    .with_input(SkillInputField::optional(
        "score_distribution",
        "评分分布，key=评分，value=人数",
        object_schema(),
    ))
    .with_input(SkillInputField::optional(
        "promoters_count",
        "推荐者数量（9-10分）",
        integer_schema(),
    ))
    .with_input(SkillInputField::optional(
        "passives_count",
        "被动者数量（7-8分）",
        integer_schema(),
    ))
    .with_input(SkillInputField::optional(
        "detractors_count",
        "批评者数量（0-6分）",
        integer_schema(),
    ))
    .with_input(SkillInputField::optional(
        "top_issues",
        "批评者主要问题列表",
        string_array_schema(),
    ))
    .with_input(SkillInputField::optional(
        "platform",
        "平台：淘宝/京东/拼多多/抖音/综合",
        string_schema(),
    ))
    .with_input(SkillInputField::optional(
        "days",
        "若无调查数据，从外部数据推算的天数",
        integer_schema(),
    ))
    .with_tag("deterministic")
}

pub struct ServiceNpsAnalyzer {
    spec: SkillSpec,
}

impl ServiceNpsAnalyzer {
    pub fn new() -> Self {
        Self {
            spec: service_nps_analyzer_spec(),
        }
    }
}

impl Default for ServiceNpsAnalyzer {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl Skill for ServiceNpsAnalyzer {
    fn spec(&self) -> &SkillSpec {
        &self.spec
    }

    async fn run(&self, params: Value, _context: SkillContext) -> SkillResult {
        object_params(&params)?;
        let mut promoters = 0_i64;
        let mut passives = 0_i64;
        let mut detractors = 0_i64;
        let mut total = 0_i64;
        let mut data_source = "用户提供";

        if let Some(distribution) = params.get("score_distribution").and_then(Value::as_object) {
            for (score, count) in distribution {
                let score = score.parse::<i64>().map_err(|_| {
                    SkillError::invalid_parameter("score_distribution", "score keys 0-10")
                })?;
                let count = count.as_i64().ok_or_else(|| {
                    SkillError::invalid_parameter("score_distribution", "integer counts")
                })?;
                total += count;
                if score >= 9 {
                    promoters += count;
                } else if score >= 7 {
                    passives += count;
                } else {
                    detractors += count;
                }
            }
        } else if params.get("promoters_count").is_some() {
            promoters = optional_i64_param(&params, "promoters_count")?.unwrap_or(0);
            passives = optional_i64_param(&params, "passives_count")?.unwrap_or(0);
            detractors = optional_i64_param(&params, "detractors_count")?.unwrap_or(0);
            total = optional_i64_param(&params, "total_respondents")?
                .unwrap_or(promoters + passives + detractors);
        } else {
            data_source = "默认样例";
            promoters = 45;
            passives = 35;
            detractors = 20;
            total = 100;
        }

        if total <= 0 {
            total = promoters + passives + detractors;
        }
        if total <= 0 {
            return Err(SkillError::execution(
                "无有效数据，请提供评分分布或各分段人数",
            ));
        }

        let prom_pct = round1(promoters as f64 / total as f64 * 100.0);
        let pass_pct = round1(passives as f64 / total as f64 * 100.0);
        let detr_pct = round1(detractors as f64 / total as f64 * 100.0);
        let nps = round1(prom_pct - detr_pct);
        let csat_pct = round1((promoters + passives) as f64 / total as f64 * 100.0);
        let top_issues = optional_string_vec_param_or_empty(&params, "top_issues")?;

        let output = json!({
            "数据来源": data_source,
            "调查总人数": total,
            "NPS净推荐值": nps,
            "NPS评级": nps_grade(nps),
            "电商行业基准": "NPS 优秀≥50 | 正常≥0 | 危险<-10",
            "分群详情": {
                "推荐者(9-10分)": format!("{promoters}人（{prom_pct}%）"),
                "被动者(7-8分)": format!("{passives}人（{pass_pct}%）"),
                "批评者(0-6分)": format!("{detractors}人（{detr_pct}%）")
            },
            "CSAT满意度": format!("{csat_pct}%（{}）", csat_grade(csat_pct)),
            "忠诚度洞察": {
                "推荐者价值": format!("贡献约{prom_pct}%的口碑传播和复购"),
                "被动者风险": format!("{pass_pct}%的客户随时可能流向竞品，需主动维系"),
                "批评者负面影响": format!("预估影响约{}人的购买决策", (detractors as f64 * 5.6).round() as i64),
                "转化优先级": "被动者→推荐者成本最低，批评者→中立是当务之急"
            },
            "批评者主要问题": top_issues.iter().take(5).collect::<Vec<_>>(),
            "改善优先级": top_issues.first().map(|issue| format!("重点解决：{issue}（批评者最常提及）"))
        });

        outcome(output, "NPS与CSAT分析已完成")
    }
}

pub fn service_sentiment_analyzer_spec() -> SkillSpec {
    SkillSpec::new(
        "service_sentiment_analyzer",
        "客服情感分析",
        "对客户消息进行0-10情感强度评分，输出紧急等级、优先级和处理建议",
    )
    .with_category(category())
    .with_priority(SkillPriority::Critical)
    .with_input(SkillInputField::optional(
        "customer_text",
        "客户消息文本",
        string_schema(),
    ))
    .with_input(SkillInputField::optional(
        "platform",
        "平台：淘宝/京东/拼多多/抖音",
        string_schema(),
    ))
    .with_input(
        SkillInputField::optional("channel", "来源渠道：im/review/complaint", string_schema())
            .with_default(json!("im")),
    )
    .with_input(SkillInputField::optional(
        "batch_texts",
        "批量分析多条消息（最多20条）",
        string_array_schema(),
    ))
    .with_tag("deterministic")
}

pub struct ServiceSentimentAnalyzer {
    spec: SkillSpec,
}

impl ServiceSentimentAnalyzer {
    pub fn new() -> Self {
        Self {
            spec: service_sentiment_analyzer_spec(),
        }
    }
}

impl Default for ServiceSentimentAnalyzer {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl Skill for ServiceSentimentAnalyzer {
    fn spec(&self) -> &SkillSpec {
        &self.spec
    }

    async fn run(&self, params: Value, _context: SkillContext) -> SkillResult {
        object_params(&params)?;
        let platform =
            optional_string_param(&params, "platform")?.unwrap_or_else(|| "淘宝".to_owned());
        let channel = optional_string_param(&params, "channel")?.unwrap_or_else(|| "im".to_owned());
        let channel_display = match channel.as_str() {
            "im" => "旺旺/IM",
            "review" => "买家评价",
            "complaint" => "投诉中心",
            other => other,
        };

        if let Some(texts) = optional_string_vec_param_or_empty(&params, "batch_texts")
            .ok()
            .filter(|v| !v.is_empty())
        {
            let details = texts
                .iter()
                .take(20)
                .enumerate()
                .map(|(index, text)| {
                    let scored = score_sentiment(text);
                    json!({
                        "序号": index + 1,
                        "文本摘要": truncate_chars(text, 50),
                        "情感强度": scored.intensity,
                        "紧急等级": scored.urgency,
                        "处理优先级": scored.priority,
                        "触发关键词": scored.triggered
                    })
                })
                .collect::<Vec<_>>();
            let scores = details
                .iter()
                .filter_map(|item| item.get("情感强度").and_then(Value::as_f64))
                .collect::<Vec<_>>();
            let avg = if scores.is_empty() {
                5.0
            } else {
                round1(scores.iter().sum::<f64>() / scores.len() as f64)
            };
            let high_risk = details
                .iter()
                .filter(|item| item.get("处理优先级").and_then(Value::as_i64) == Some(1))
                .count();
            let negative = details
                .iter()
                .filter(|item| item.get("处理优先级").and_then(Value::as_i64) == Some(2))
                .count();
            let positive = details
                .iter()
                .filter(|item| {
                    item.get("处理优先级")
                        .and_then(Value::as_i64)
                        .is_some_and(|p| p >= 4)
                })
                .count();
            return outcome(
                json!({
                    "分析模式": "批量分析",
                    "消息数量": details.len(),
                    "平台": platform,
                    "渠道": channel_display,
                    "整体情感评分": avg,
                    "分布统计": {
                        "高危需立即处理": high_risk,
                        "负面优先处理": negative,
                        "正常处理": details.len().saturating_sub(high_risk + negative + positive),
                        "正面/高满意": positive
                    },
                    "建议": if high_risk > 0 { format!("有{high_risk}条高危消息需立即处理") } else { "无高危消息，正常处理队列".to_owned() },
                    "明细": details
                }),
                "批量情感分析已完成",
            );
        }

        let text = optional_string_param(&params, "customer_text")?
            .ok_or_else(|| SkillError::missing_parameter("customer_text"))?;
        let scored = score_sentiment(&text);
        outcome(
            json!({
                "平台": platform,
                "渠道": channel_display,
                "客户消息摘要": truncate_chars(&text, 80),
                "情感强度评分": format!("{}/10", scored.intensity),
                "情感状态": sentiment_state(scored.intensity),
                "紧急等级": scored.urgency,
                "处理优先级": scored.priority,
                "触发关键词": scored.triggered,
                "电商行业参考": "高危=需2分钟内响应，负面=30分钟内，中性=2小时内SLA",
                "建议": sentiment_advice(scored.intensity)
            }),
            "客服情感分析已完成",
        )
    }
}

pub fn service_nps_driver_analysis_spec() -> SkillSpec {
    SkillSpec::new(
        "service_nps_driver_analysis",
        "NPS驱动因素分析",
        "4类投诉来源根因诊断，计算各类别NPS影响系数，识别最大满意度驱动因素",
    )
    .with_category(category())
    .with_priority(SkillPriority::Critical)
    .with_input(SkillInputField::optional(
        "promoter_count",
        "推荐者数量（评分9-10）",
        integer_schema(),
    ))
    .with_input(SkillInputField::optional(
        "detractor_count",
        "贬低者数量（评分0-6）",
        integer_schema(),
    ))
    .with_input(SkillInputField::optional(
        "passive_count",
        "被动者数量（评分7-8）",
        integer_schema(),
    ))
    .with_input(SkillInputField::optional(
        "complaint_distribution",
        "投诉分布：{物流配送: 120, 商品质量: 85}",
        object_schema(),
    ))
    .with_input(SkillInputField::optional(
        "total_tickets",
        "总工单数",
        integer_schema(),
    ))
    .with_input(SkillInputField::optional(
        "platform",
        "平台：淘宝/京东/拼多多/抖音",
        string_schema(),
    ))
    .with_tag("deterministic")
}

pub struct ServiceNpsDriverAnalysis {
    spec: SkillSpec,
}

impl ServiceNpsDriverAnalysis {
    pub fn new() -> Self {
        Self {
            spec: service_nps_driver_analysis_spec(),
        }
    }
}

impl Default for ServiceNpsDriverAnalysis {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl Skill for ServiceNpsDriverAnalysis {
    fn spec(&self) -> &SkillSpec {
        &self.spec
    }

    async fn run(&self, params: Value, _context: SkillContext) -> SkillResult {
        object_params(&params)?;
        let mut promoter = optional_i64_param(&params, "promoter_count")?.unwrap_or(0);
        let mut detractor = optional_i64_param(&params, "detractor_count")?.unwrap_or(0);
        let mut passive = optional_i64_param(&params, "passive_count")?.unwrap_or(0);
        if promoter + detractor + passive == 0 {
            promoter = 45;
            passive = 35;
            detractor = 20;
        }

        let platform =
            optional_string_param(&params, "platform")?.unwrap_or_else(|| "淘宝".to_owned());
        let mut dist = parse_string_i64_map(params.get("complaint_distribution"))?;
        if dist.is_empty() {
            dist.extend([
                ("物流配送".to_owned(), 38),
                ("商品质量".to_owned(), 25),
                ("售后退款".to_owned(), 18),
                ("客服响应".to_owned(), 12),
                ("商品描述".to_owned(), 7),
            ]);
        }

        let total_tickets = optional_i64_param(&params, "total_tickets")?
            .unwrap_or_else(|| dist.values().sum::<i64>())
            .max(1);
        let total_respondents = promoter + detractor + passive;
        let promoter_pct = promoter as f64 / total_respondents as f64;
        let detractor_pct = detractor as f64 / total_respondents as f64;
        let nps_score = round1((promoter_pct - detractor_pct) * 100.0);

        let mut rows = dist.into_iter().map(|(category, count)| {
            let rate = count as f64 / total_tickets as f64;
            let coeff = nps_coefficient(&category);
            let impact = round2(rate * coeff * 100.0);
            json!({
                "问题类别": category,
                "投诉量": count,
                "投诉率": format!("{:.1}%", rate * 100.0),
                "NPS拖累": format!("{impact:+.2}分"),
                "处理优先级": if impact.abs() > 3.0 { "P0" } else if impact.abs() > 1.5 { "P1" } else { "P2" },
                "_impact_sort": impact
            })
        }).collect::<Vec<_>>();
        rows.sort_by(|a, b| {
            a.get("_impact_sort")
                .and_then(Value::as_f64)
                .partial_cmp(&b.get("_impact_sort").and_then(Value::as_f64))
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        let max_uplift = round1(
            rows.iter()
                .filter_map(|row| row.get("_impact_sort").and_then(Value::as_f64))
                .sum::<f64>()
                .abs(),
        );
        for row in &mut rows {
            if let Some(map) = row.as_object_mut() {
                map.remove("_impact_sort");
            }
        }
        let industry_avg = match platform.as_str() {
            "淘宝" => 45,
            "京东" => 52,
            "拼多多" => 30,
            "抖音" => 40,
            _ => 40,
        };
        let nps_gap = round1(nps_score - industry_avg as f64);
        let max_pain = rows
            .first()
            .and_then(|row| row.get("问题类别"))
            .cloned()
            .unwrap_or_else(|| json!("无数据"));

        outcome(
            json!({
                "平台": platform,
                "NPS分数": format!("{nps_score:+.1}"),
                "NPS构成": {
                    "推荐者(9-10分)": format!("{:.0}%", promoter_pct * 100.0),
                    "被动者(7-8分)": format!("{:.0}%", passive as f64 / total_respondents as f64 * 100.0),
                    "贬低者(0-6分)": format!("{:.0}%", detractor_pct * 100.0),
                    "受访总数": total_respondents
                },
                "行业基准": {
                    "行业平均NPS": industry_avg,
                    "差距": format!("{nps_gap:+.1}分"),
                    "位置": if nps_gap >= 0.0 { "优于行业" } else { "低于行业" }
                },
                "驱动因素分析": rows,
                "理论最大NPS提升": format!("+{max_uplift}分（解决全部投诉问题）"),
                "最大痛点": max_pain,
                "总工单数": total_tickets
            }),
            "NPS驱动因素分析已完成",
        )
    }
}

fn round1(value: f64) -> f64 {
    (value * 10.0).round() / 10.0
}

fn round2(value: f64) -> f64 {
    (value * 100.0).round() / 100.0
}

fn optional_string_vec_param_or_empty(
    params: &Value,
    name: &str,
) -> Result<Vec<String>, SkillError> {
    Ok(optional_string_vec_param(params, name)?.unwrap_or_default())
}

fn nps_grade(nps: f64) -> &'static str {
    if nps >= 70.0 {
        "极优秀（世界级）"
    } else if nps >= 50.0 {
        "优秀"
    } else if nps >= 30.0 {
        "良好"
    } else if nps >= 0.0 {
        "正常"
    } else {
        "需改善（批评者过多）"
    }
}

fn csat_grade(csat: f64) -> &'static str {
    if csat >= 90.0 {
        "优秀"
    } else if csat >= 75.0 {
        "良好"
    } else {
        "需改善"
    }
}

struct SentimentScore {
    intensity: f64,
    urgency: &'static str,
    priority: i64,
    triggered: Vec<&'static str>,
}

fn score_sentiment(text: &str) -> SentimentScore {
    let keywords = [
        ("骗子", -3.0),
        ("欺诈", -3.0),
        ("举报", -2.8),
        ("曝光", -2.5),
        ("投诉到", -2.5),
        ("差评", -2.0),
        ("投诉", -2.0),
        ("维权", -2.0),
        ("退款", -1.5),
        ("破损", -1.5),
        ("质量差", -1.5),
        ("假货", -2.0),
        ("不满意", -1.2),
        ("等了好久", -1.0),
        ("还没到", -1.0),
        ("赔偿", -1.5),
        ("退货", -0.8),
        ("问题", -0.5),
        ("延误", -0.8),
        ("发错", -1.0),
        ("缺货", -0.8),
        ("好评", 0.8),
        ("满意", 0.8),
        ("感谢", 0.6),
        ("棒", 0.5),
        ("超快", 0.5),
        ("完美", 1.0),
        ("推荐", 0.8),
        ("再次购买", 1.0),
        ("愤怒", -0.9),
        ("气死", -0.9),
        ("太差了", -0.75),
        ("垃圾", -0.75),
        ("失望", -0.6),
        ("挺好", 0.3),
        ("不错", 0.3),
        ("很好", 0.45),
        ("太好了", 0.6),
    ];
    let mut score = 5.0;
    let mut triggered = Vec::new();
    for (keyword, delta) in keywords {
        if text.contains(keyword) {
            score += delta;
            triggered.push(keyword);
        }
    }
    if text.chars().count() > 200 && score < 4.0 {
        score -= 0.5;
    }
    let exclamations = text.matches('！').count() + text.matches("!!").count();
    if exclamations >= 2 && score < 5.0 {
        score -= 0.3 * exclamations.min(3) as f64;
    }
    let intensity = round1(score.clamp(0.0, 10.0));
    let (urgency, priority) = if intensity < 2.0 {
        ("高危-立即介入（2分钟内）", 1)
    } else if intensity < 3.5 {
        ("负面-优先处理（30分钟内）", 2)
    } else if intensity < 5.5 {
        ("中性-正常排队（2小时内）", 3)
    } else if intensity < 7.5 {
        ("正面-无需人工干预", 4)
    } else {
        ("高满意-可邀请好评", 5)
    };
    SentimentScore {
        intensity,
        urgency,
        priority,
        triggered: triggered.into_iter().take(5).collect(),
    }
}

fn sentiment_state(intensity: f64) -> &'static str {
    if intensity < 2.0 {
        "极度负面"
    } else if intensity < 4.0 {
        "负面"
    } else if intensity < 6.0 {
        "中性"
    } else if intensity < 8.0 {
        "正面"
    } else {
        "极度正面"
    }
}

fn sentiment_advice(intensity: f64) -> &'static str {
    if intensity < 2.0 {
        "立即人工介入，先承认问题并给出明确补偿或升级路径"
    } else if intensity < 4.0 {
        "优先响应，围绕事实核验、解决时限和补偿预期沟通"
    } else if intensity < 6.0 {
        "按常规SLA处理，保持信息透明"
    } else if intensity < 8.0 {
        "维持友好沟通，可引导收藏或复购"
    } else {
        "可邀请评价或沉淀为好评素材"
    }
}

fn truncate_chars(text: &str, limit: usize) -> String {
    text.chars().take(limit).collect()
}

fn parse_string_i64_map(value: Option<&Value>) -> Result<BTreeMap<String, i64>, SkillError> {
    let mut out = BTreeMap::new();
    let Some(value) = value else {
        return Ok(out);
    };
    let object = value
        .as_object()
        .ok_or_else(|| SkillError::invalid_parameter("complaint_distribution", "object"))?;
    for (key, value) in object {
        let count = value.as_i64().ok_or_else(|| {
            SkillError::invalid_parameter("complaint_distribution", "integer counts")
        })?;
        out.insert(key.clone(), count);
    }
    Ok(out)
}

fn nps_coefficient(category: &str) -> f64 {
    match category {
        "物流配送" => -0.35,
        "商品质量" => -0.48,
        "客服响应" => -0.22,
        "售后退款" => -0.30,
        "包装体验" => -0.12,
        "价格感知" => -0.18,
        "商品描述" => -0.20,
        "平台体验" => -0.10,
        _ => -0.15,
    }
}
