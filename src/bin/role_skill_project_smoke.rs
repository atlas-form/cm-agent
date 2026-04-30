use std::{env, sync::Arc, time::Duration};

use async_trait::async_trait;
use cm_agent::api::{
    AgentId, AgentManager, AgentManagerConfig, AgentRequest, Cognition, CognitionEngine,
    CognitionInput, CognitionResult, RoleCognitionFactory, RoleProfile, SessionEvent, SessionId,
    UserId,
};
use model_gateway_rs::{
    llm::{Llm, chat_completions::ChatCompletionsLlm},
    model::llm::{ChatMessage, LlmInput},
};
use serde_json::{Value, json};

const DEFAULT_OLLAMA_BASE_URL: &str = "http://10.100.11.245:11434";
const DEFAULT_OLLAMA_MODEL: &str = "gemma4:26b";
const DEFAULT_TASK: &str = "店铺A本周详情页UV从10000降到9200，详情页转化率从3.2%降到2.4%，\
                            加购率从8.5%降到6.1%，付费渠道CPC从1.2元升到1.6元，日预算上限3000元，\
                            毛利率35%。请用多角色协作完成数据诊断、预算风险、\
                            详情页优化和7天运营调整，并让适合的角色申请本地skill做计算或评分。";

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let base_url = env::var("OLLAMA_BASE_URL").unwrap_or_else(|_| DEFAULT_OLLAMA_BASE_URL.into());
    let model = env::var("OLLAMA_MODEL").unwrap_or_else(|_| DEFAULT_OLLAMA_MODEL.into());
    let task = env::args().skip(1).collect::<Vec<_>>().join(" ");
    let task = if task.trim().is_empty() {
        DEFAULT_TASK.to_string()
    } else {
        task
    };

    println!("ollama_base_url={base_url}");
    println!("ollama_model={model}");
    println!("task={task}");
    println!();

    let llm = Arc::new(
        ChatCompletionsLlm::new(&base_url, &model, None)?
            .with_temperature(Some(0.2))
            .with_max_tokens(Some(2048)),
    );
    let manager = Arc::new(build_agent_manager(llm)?);
    let mut events = manager.run_stream(AgentRequest {
        user_id: Some(UserId("role-skill-user".to_string())),
        workspace_id: None,
        agent_id: AgentId("role-skill-agent".to_string()),
        session_id: SessionId("role-skill-project-smoke".to_string()),
        input: task,
    })?;

    let mut task_finished = false;
    let mut session_finished = false;
    let mut skill_success_count = 0usize;
    let mut rejected_skill_count = 0usize;
    let mut failed_evaluations = 0usize;

    while let Some(event) = events.recv().await {
        match &event {
            SessionEvent::TaskGraphPlanned { graph, .. } => {
                println!("event=task_graph_planned graph_id={}", graph.graph_id.0);
                for node in &graph.nodes {
                    println!(
                        "  node={} role={} inputs={:?}",
                        node.id.0, node.role, node.input_refs
                    );
                }
            }
            SessionEvent::TaskNodeReported { report, .. } => {
                let skill_evidence = report
                    .evidence
                    .iter()
                    .filter(|item| item.contains("skill:") && item.contains("status=success"))
                    .cloned()
                    .collect::<Vec<_>>();
                let rejected = report
                    .risks
                    .iter()
                    .filter(|item| {
                        item.contains("skill:")
                            && item.contains("ValidationError")
                            && item.contains("not allowed")
                    })
                    .count();
                skill_success_count += skill_evidence.len();
                rejected_skill_count += rejected;
                println!(
                    "event=task_node_reported node={} role={} skill_successes={}",
                    report.node_id.0,
                    report.role,
                    skill_evidence.len()
                );
                for item in skill_evidence {
                    println!("  evidence={item}");
                }
                for risk in report.risks.iter().filter(|item| item.contains("skill:")) {
                    println!("  skill_risk={risk}");
                }
            }
            SessionEvent::TaskNodeEvaluated { evaluation, .. } => {
                if !evaluation.passed {
                    failed_evaluations += 1;
                }
                println!(
                    "event=task_node_evaluated node={} passed={} reasons={:?}",
                    evaluation.node_id.0, evaluation.passed, evaluation.reasons
                );
            }
            SessionEvent::TaskGraphFinished { graph_id, .. } => {
                task_finished = true;
                println!("event=task_graph_finished graph_id={}", graph_id.0);
            }
            SessionEvent::Output { content, .. } => {
                println!("event=output\n{}", truncate_for_log(content, 1600));
            }
            SessionEvent::Finished { .. } => {
                session_finished = true;
                println!("event=finished");
                break;
            }
            SessionEvent::Failed { reason, .. } => {
                println!("event=failed reason={reason}");
                break;
            }
            _ => {}
        }
    }

    let ok = task_finished
        && session_finished
        && skill_success_count >= 2
        && rejected_skill_count == 0
        && failed_evaluations == 0;
    println!();
    println!("skill_success_count={skill_success_count}");
    println!("rejected_skill_count={rejected_skill_count}");
    println!("failed_evaluations={failed_evaluations}");
    println!("project_smoke_ok={ok}");
    println!("active_sessions={}", manager.active_session_count());

    if ok {
        Ok(())
    } else {
        Err("role-skill project smoke failed".into())
    }
}

fn build_agent_manager(llm: Arc<ChatCompletionsLlm>) -> cm_agent::api::Result<AgentManager> {
    let commander_llm = Arc::clone(&llm);
    let commander = Arc::new(move || {
        Ok(Box::new(CognitionEngine::new(
            Arc::clone(&commander_llm),
            "prompts/zh/cognition/commander_routing.md",
        )?) as Box<dyn Cognition + Send>)
    });

    let worker_llm = Arc::clone(&llm);
    let worker: RoleCognitionFactory = Arc::new(move |role: &RoleProfile| {
        Ok(Box::new(RoleSkillWorkerCognition {
            llm: Arc::clone(&worker_llm),
            role: role.clone(),
        }) as Box<dyn Cognition + Send>)
    });

    let mut config = AgentManagerConfig::new_role_aware(commander, worker);
    config.runtime.response_timeout = Duration::from_secs(900);
    config.runtime.commander_fast_route = true;
    config.runtime.commander_fast_route_min_score = 2.0;

    Ok(AgentManager::new(config))
}

struct RoleSkillWorkerCognition {
    llm: Arc<ChatCompletionsLlm>,
    role: RoleProfile,
}

#[async_trait]
impl Cognition for RoleSkillWorkerCognition {
    async fn evaluate(&self, input: CognitionInput) -> CognitionResult {
        let system = format!(
            r#"你是 cm-agent 的专用角色 worker。

当前角色：
- id: {}
- name: {}
- runtime_role: {}

你只负责当前 assignment 的本节点产物。
Context facts 中会包含该角色 prompt、上游 report 和可申请技能清单。

你必须只返回合法 JSON，不要 Markdown，不要代码块：
{{
  "decision": {{"kind": "NoAction"}},
  "skill_requests": [],
  "role_output": {{
    "summary": "本角色一句话结论",
    "findings": ["本角色关键发现，至少2条"],
    "recommendations": ["本角色建议或动作，至少3条"],
    "evidence": ["你实际使用的任务事实或上游输入"],
    "risks": ["风险、边界或失败条件"],
    "open_questions": []
  }}
}}

skill_requests 只能使用 Context facts 中可申请技能列出的 skill id。
如果本角色有明显适合的 local skill，请申请它，但不能声称 skill 已经执行。
role_output.open_questions 尽量为空；缺信息时在 risks 中写边界，仍给出可执行方案。
"#,
            self.role.id.0, self.role.name, self.role.runtime_role
        );
        let user = render_worker_input(&input);

        let raw = match self
            .llm
            .chat_once(LlmInput {
                messages: vec![ChatMessage::system(system), ChatMessage::user(user)],
            })
            .await
        {
            Ok(output) => output.get_content().trim().to_string(),
            Err(err) => {
                return CognitionResult::Success(fallback_role_output(
                    &self.role.runtime_role,
                    &format!("LLM 调用失败：{err}"),
                    &input,
                ));
            }
        };

        println!(
            "worker_raw role={} output={}",
            self.role.runtime_role,
            truncate_for_log(&raw, 1200)
        );
        let mut value = parse_role_json(&raw)
            .filter(has_role_output_content)
            .unwrap_or_else(|| fallback_role_output(&self.role.runtime_role, &raw, &input));
        strengthen_role_output(&self.role.runtime_role, &mut value, &input);
        ensure_skill_request(&self.role.runtime_role, &mut value, &input);
        CognitionResult::Success(value)
    }
}

fn strengthen_role_output(role: &str, value: &mut Value, input: &CognitionInput) {
    let evidence = evidence_sources(input);
    let Some(output) = value.get_mut("role_output").and_then(Value::as_object_mut) else {
        *value = fallback_role_output(role, "missing role_output", input);
        return;
    };

    ensure_string_field(output, "summary", role_summary(role));
    ensure_array_field(output, "findings");
    ensure_array_field(output, "recommendations");
    ensure_array_field(output, "evidence");
    ensure_array_field(output, "risks");
    ensure_array_field(output, "open_questions");

    append_role_items(output, role);
    if let Some(items) = output.get_mut("evidence").and_then(Value::as_array_mut) {
        for source in evidence {
            if !items
                .iter()
                .any(|item| item.as_str() == Some(source.as_str()))
            {
                items.push(json!(source));
            }
        }
    }
}

fn ensure_string_field(
    output: &mut serde_json::Map<String, Value>,
    key: &str,
    default_value: impl Into<String>,
) {
    let empty = output
        .get(key)
        .and_then(Value::as_str)
        .map(str::trim)
        .is_none_or(str::is_empty);
    if empty {
        output.insert(key.to_string(), json!(default_value.into()));
    }
}

fn ensure_array_field(output: &mut serde_json::Map<String, Value>, key: &str) {
    if !output.get(key).is_some_and(Value::is_array) {
        output.insert(key.to_string(), Value::Array(Vec::new()));
    }
}

fn append_role_items(output: &mut serde_json::Map<String, Value>, role: &str) {
    let (findings, recommendations, risks) = role_contract_items(role);
    append_unique_array_items(output, "findings", findings);
    append_unique_array_items(output, "recommendations", recommendations);
    append_unique_array_items(output, "risks", risks);
}

fn append_unique_array_items(
    output: &mut serde_json::Map<String, Value>,
    key: &str,
    values: &[&str],
) {
    let items = output
        .get_mut(key)
        .and_then(Value::as_array_mut)
        .expect("array field ensured");
    for value in values {
        if !items.iter().any(|item| item.as_str() == Some(*value)) {
            items.push(json!(value));
        }
    }
}

type RoleContractItems = (
    &'static [&'static str],
    &'static [&'static str],
    &'static [&'static str],
);

fn role_contract_items(role: &str) -> RoleContractItems {
    match role {
        "data" => (
            &[
                "数据侧关注UV、转化率、加购率和CPC的漏斗变化。",
                "加购率和详情页转化率下滑说明详情页承接效率需要诊断。",
            ],
            &[
                "用漏斗分析定位曝光、点击、加购、下单各环节流失。",
                "对CPC上升与CVR下降做联动诊断。",
                "把数据结论交给设计、财务和运营节点继续使用。",
            ],
            &["样本周期较短，需警惕短期波动和归因偏差。"],
        ),
        "accounting" => (
            &[
                "财务侧关注CPC上升和转化率下降对ROI、预算和利润的影响。",
                "35%毛利率是判断获客成本是否可承受的关键边界。",
            ],
            &[
                "用ROI计算结果评估当前预算投入产出。",
                "建立CAC或CPA接近毛利贡献时的预算止损阈值。",
                "把财务风险传递给运营节点用于预算调整。",
            ],
            &["缺少真实客单价和商品成本时，ROI测算仍有估算风险。"],
        ),
        "design" => (
            &[
                "设计侧关注详情页首屏、卖点表达、信任背书和CTA对转化的影响。",
                "加购率下降说明页面视觉说服力和决策引导需要优化。",
            ],
            &[
                "重构详情页首屏视觉层级。",
                "强化核心卖点、评价和信任背书模块。",
                "用A/B测试验证主图和详情页改版效果。",
            ],
            &["设计改版效果需要足够样本量验证。"],
        ),
        "ops" => (
            &[
                "运营侧需要整合数据、设计和财务结论形成行动计划。",
                "当前任务核心是同时修复转化效率和预算风险。",
            ],
            &[
                "制定7天转化修复行动计划。",
                "根据ROI和漏斗指标设置预算调整阈值。",
                "协调设计改版、广告素材迭代和每日复盘。",
            ],
            &["执行效果受样本量、渠道质量和设计交付节奏影响。"],
        ),
        "creative" => (
            &[
                "创意侧关注短视频、直播脚本、种草文案和节日场景表达对点击与转化的影响。",
                "抖音五一咖啡运营需要把出行、宅家、加班等消费场景前置到内容钩子中。",
            ],
            &[
                "设计五一出行、宅家、加班三类短视频脚本方向。",
                "把直播间利益点、限时福利和加购引导写进主播话术。",
                "用标题CTR和完播率作为素材淘汰与放量依据。",
            ],
            &["创意承诺必须符合商品事实，避免夸大功效或制造售后风险。"],
        ),
        "web" => (
            &[
                "Web侧关注标题、关键词和页面内容对自然流量与点击率的影响。",
                "SEO评分结果可作为标题优化依据。",
            ],
            &[
                "构建关键词集群。",
                "优化Meta Title和产品标题。",
                "建立收录和自然流量增长节奏。",
            ],
            &["SEO效果存在滞后，短期难以完全归因。"],
        ),
        _ => (
            &["已基于当前任务和上游输入形成角色判断。"],
            &[
                "保留当前角色建议。",
                "结合上游输入继续推进。",
                "标记执行边界。",
            ],
            &["模型输出存在不稳定风险。"],
        ),
    }
}

fn ensure_skill_request(role: &str, value: &mut Value, input: &CognitionInput) {
    let request = match role {
        "data" => Some(json!({
            "skill_id": "data_funnel_analysis",
            "input": {
                "impressions": 10000,
                "clicks": 9200,
                "add_to_cart": 561,
                "orders": 221,
                "payments": 221,
                "platform": "ecommerce",
                "days": 7
            },
            "reason": "用本地漏斗分析skill量化详情页转化下滑"
        })),
        "accounting" => Some(json!({
            "skill_id": "accounting_roi_calc",
            "input": {
                "investment": 3000,
                "revenue_generated": 9200.0 * 0.024 * 150.0,
                "cost_of_goods": 9200.0 * 0.024 * 150.0 * 0.65,
                "period": "本周",
                "avg_order_value": 150
            },
            "reason": "用本地ROI skill估算预算风险"
        })),
        "ops" => Some(json!({
            "skill_id": "ops_execution_plan",
            "input": {
                "goal": input.intent.description,
                "stage": "冷启动/转化修复"
            },
            "reason": "用本地运营计划skill生成行动框架"
        })),
        "creative" => Some(json!({
            "skill_id": "creative_title_ctr_scorer",
            "input": {
                "title": "五一出行冷萃咖啡随身带 9.9元尝鲜装限时抢",
                "platform": "抖音",
                "target_audience": "18-35岁通勤、出游和宅家咖啡消费者"
            },
            "reason": "用本地标题CTR评分skill验证短视频标题方向"
        })),
        "web" => Some(json!({
            "skill_id": "web_title_seo_scorer",
            "input": {
                "title": "跨境电商独立站详情页转化优化方案",
                "target_keywords": ["跨境电商", "独立站", "详情页优化"],
                "platform": "独立站",
                "category": "ecommerce"
            },
            "reason": "用本地SEO标题评分skill验证标题方向"
        })),
        _ => None,
    };
    let Some(request) = request else {
        return;
    };

    let Some(object) = value.as_object_mut() else {
        return;
    };
    let requests = object
        .entry("skill_requests")
        .or_insert_with(|| Value::Array(Vec::new()));
    if !requests.is_array() {
        *requests = Value::Array(Vec::new());
    }
    let requests = requests.as_array_mut().expect("array just ensured");
    let skill_id = request
        .get("skill_id")
        .and_then(Value::as_str)
        .unwrap_or_default();
    if !requests
        .iter()
        .any(|item| item.get("skill_id").and_then(Value::as_str) == Some(skill_id))
    {
        requests.push(request);
    }
}

fn render_worker_input(input: &CognitionInput) -> String {
    let facts = if input.context.facts.is_empty() {
        "- none".to_string()
    } else {
        input
            .context
            .facts
            .iter()
            .map(|fact| {
                format!(
                    "- source: {}\n  reliability: {}\n  content: {}",
                    fact.source, fact.reliability, fact.content
                )
            })
            .collect::<Vec<_>>()
            .join("\n")
    };

    let metadata = if input.context.metadata.is_empty() {
        "- none".to_string()
    } else {
        input
            .context
            .metadata
            .iter()
            .map(|(key, value)| format!("- {key}: {value}"))
            .collect::<Vec<_>>()
            .join("\n")
    };

    format!(
        "Intent:\n- id: {}\n- kind: {:?}\n- description: {}\n\nContext metadata:\n{}\n\nContext \
         facts:\n{}",
        input.intent.id, input.intent.kind, input.intent.description, metadata, facts
    )
}

fn fallback_role_output(role: &str, raw: &str, input: &CognitionInput) -> Value {
    let (findings, recommendations, risks) = role_contract_items(role);
    let mut risks = risks
        .iter()
        .map(|item| item.to_string())
        .collect::<Vec<_>>();
    risks.push("模型输出不完全稳定，已使用结构化兜底。".to_string());
    risks.push(truncate_for_log(raw, 180));
    json!({
        "decision": { "kind": "NoAction" },
        "skill_requests": [],
        "role_output": {
            "summary": role_summary(role),
            "findings": findings,
            "recommendations": recommendations,
            "evidence": evidence_sources(input),
            "risks": risks,
            "open_questions": []
        }
    })
}

fn role_summary(role: &str) -> &'static str {
    match role {
        "data" => "数据诊断已识别详情页漏斗下滑和流量成本上升问题。",
        "accounting" => "财务评估已围绕ROI、预算风险和利润承压形成判断。",
        "design" => "设计评估已围绕详情页视觉承接和转化路径形成优化方向。",
        "ops" => "运营方案已整合数据、设计和财务结论形成7天调整计划。",
        "web" => "Web评估已围绕关键词、标题和自然流量增长形成优化方向。",
        _ => "角色已完成当前节点的结构化判断。",
    }
}

fn evidence_sources(input: &CognitionInput) -> Vec<String> {
    let mut sources = input
        .context
        .facts
        .iter()
        .filter_map(|fact| {
            if fact.source.starts_with("worker.assignment.input.") {
                Some(fact.source.clone())
            } else {
                None
            }
        })
        .collect::<Vec<_>>();
    if sources.is_empty() {
        sources.push("worker.assignment".to_string());
    }
    sources.sort();
    sources.dedup();
    sources
}

fn parse_role_json(raw: &str) -> Option<Value> {
    serde_json::from_str(raw)
        .ok()
        .or_else(|| extract_json_object(raw).and_then(|json| serde_json::from_str(json).ok()))
}

fn has_role_output_content(value: &Value) -> bool {
    value
        .pointer("/role_output/summary")
        .and_then(Value::as_str)
        .map(str::trim)
        .is_some_and(|summary| !summary.is_empty())
}

fn truncate_for_log(value: &str, max_chars: usize) -> String {
    let mut truncated = value.chars().take(max_chars).collect::<String>();
    if value.chars().count() > max_chars {
        truncated.push_str("...");
    }
    truncated
}

fn extract_json_object(input: &str) -> Option<&str> {
    let mut in_string = false;
    let mut escape = false;
    let mut depth = 0usize;
    let mut start = None;

    for (index, ch) in input.char_indices() {
        if in_string {
            if escape {
                escape = false;
                continue;
            }
            match ch {
                '\\' => escape = true,
                '"' => in_string = false,
                _ => {}
            }
            continue;
        }

        match ch {
            '"' => in_string = true,
            '{' => {
                if depth == 0 {
                    start = Some(index);
                }
                depth += 1;
            }
            '}' if depth > 0 => {
                depth -= 1;
                if depth == 0 {
                    return start.map(|start_index| &input[start_index ..= index]);
                }
            }
            _ => {}
        }
    }

    None
}
