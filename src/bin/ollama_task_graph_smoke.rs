use std::{
    collections::{HashMap, HashSet},
    env,
    sync::Arc,
    time::Duration,
};

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

const DEFAULT_OLLAMA_BASE_URL: &str = "http://127.0.0.1:11434";
const DEFAULT_OLLAMA_MODEL: &str = "gemma4:26b";
const DEFAULT_TASK: &str = "基于数据诊断做详情页设计和预算风险评估，并给运营调整方案";

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let base_url = env::var("OLLAMA_BASE_URL").unwrap_or_else(|_| DEFAULT_OLLAMA_BASE_URL.into());
    let model = env::var("OLLAMA_MODEL").unwrap_or_else(|_| DEFAULT_OLLAMA_MODEL.into());
    let args = env::args().skip(1).collect::<Vec<_>>();
    let matrix_mode = args.iter().any(|arg| arg == "--matrix");
    let task = args
        .iter()
        .filter(|arg| arg.as_str() != "--matrix")
        .cloned()
        .collect::<Vec<_>>()
        .join(" ");
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
    let manager = Arc::new(build_ollama_agent_manager(llm)?);
    if matrix_mode {
        run_matrix(manager).await?;
        return Ok(());
    }

    let mut events = manager.run_stream(AgentRequest {
        user_id: Some(UserId("ollama-user".to_string())),
        workspace_id: None,
        agent_id: AgentId("ollama-agent".to_string()),
        session_id: SessionId("ollama-task-graph-smoke".to_string()),
        input: task,
    })?;

    while let Some(event) = events.recv().await {
        print_event(&event);
        if matches!(
            event,
            SessionEvent::Finished { .. } | SessionEvent::Failed { .. }
        ) {
            break;
        }
    }

    println!("active_sessions={}", manager.active_session_count());
    Ok(())
}

#[derive(Debug, Clone)]
struct MatrixCase {
    name: &'static str,
    task: &'static str,
    expected_roles: &'static [&'static str],
}

#[derive(Debug, Default)]
struct MatrixResult {
    planned_roles: HashSet<String>,
    started_roles: HashSet<String>,
    passed_roles: HashSet<String>,
    failed_roles: HashMap<String, Vec<String>>,
}

async fn run_matrix(manager: Arc<AgentManager>) -> Result<(), Box<dyn std::error::Error>> {
    let cases = role_matrix_cases();
    let mut covered_roles = HashSet::new();
    let mut all_ok = true;

    println!("matrix_cases={}", cases.len());
    println!();

    for (index, case) in cases.iter().enumerate() {
        println!("=== matrix_case={} name={} ===", index + 1, case.name);
        println!("task={}", case.task);
        let result = run_matrix_case(Arc::clone(&manager), case, index).await?;
        covered_roles.extend(result.started_roles.iter().cloned());

        let missing = case
            .expected_roles
            .iter()
            .filter(|role| !result.started_roles.contains(**role))
            .copied()
            .collect::<Vec<_>>();
        let failed_expected = case
            .expected_roles
            .iter()
            .filter(|role| !result.passed_roles.contains(**role))
            .copied()
            .collect::<Vec<_>>();
        let ok = missing.is_empty() && failed_expected.is_empty();
        all_ok &= ok;

        println!(
            "planned={:?} started={:?} passed={:?}",
            sorted_set(&result.planned_roles),
            sorted_set(&result.started_roles),
            sorted_set(&result.passed_roles)
        );
        if !result.failed_roles.is_empty() {
            println!("failed={:?}", result.failed_roles);
        }
        if !missing.is_empty() {
            println!("missing_expected_started={missing:?}");
        }
        if !failed_expected.is_empty() {
            println!("missing_expected_passed={failed_expected:?}");
        }
        println!("case_ok={ok}");
        println!();
    }

    let expected_all = [
        "chat",
        "ops",
        "data",
        "service",
        "creative",
        "engineering",
        "accounting",
        "design",
        "web",
    ];
    let missing_all = expected_all
        .iter()
        .filter(|role| !covered_roles.contains(**role))
        .copied()
        .collect::<Vec<_>>();
    println!("covered_roles={:?}", sorted_set(&covered_roles));
    println!("missing_roles={missing_all:?}");
    println!("matrix_ok={}", all_ok && missing_all.is_empty());
    println!("active_sessions={}", manager.active_session_count());

    Ok(())
}

async fn run_matrix_case(
    manager: Arc<AgentManager>,
    case: &MatrixCase,
    index: usize,
) -> Result<MatrixResult, Box<dyn std::error::Error>> {
    let mut events = manager.run_stream(AgentRequest {
        user_id: Some(UserId("ollama-matrix-user".to_string())),
        workspace_id: None,
        agent_id: AgentId("ollama-matrix-agent".to_string()),
        session_id: SessionId(format!("ollama-task-graph-matrix-{index}")),
        input: case.task.to_string(),
    })?;

    let mut result = MatrixResult::default();
    let mut node_roles = HashMap::<String, String>::new();

    while let Some(event) = events.recv().await {
        match &event {
            SessionEvent::TaskGraphPlanned { graph, .. } => {
                for node in &graph.nodes {
                    node_roles.insert(node.id.0.clone(), node.role.clone());
                    result.planned_roles.insert(node.role.clone());
                }
            }
            SessionEvent::TaskNodeStarted { node_id, .. } => {
                if let Some(role) = node_roles.get(&node_id.0) {
                    result.started_roles.insert(role.clone());
                }
            }
            SessionEvent::TaskNodeEvaluated { evaluation, .. } => {
                if let Some(role) = node_roles.get(&evaluation.node_id.0) {
                    if evaluation.passed {
                        result.passed_roles.insert(role.clone());
                    } else {
                        result
                            .failed_roles
                            .entry(role.clone())
                            .or_default()
                            .extend(evaluation.reasons.clone());
                    }
                }
            }
            SessionEvent::Finished { .. } | SessionEvent::Failed { .. } => break,
            _ => {}
        }
    }

    Ok(result)
}

fn role_matrix_cases() -> Vec<MatrixCase> {
    vec![
        MatrixCase {
            name: "chat",
            task: "请用三句话解释 agent session 是什么，以及它和普通聊天上下文有什么区别。",
            expected_roles: &["chat"],
        },
        MatrixCase {
            name: "ops",
            task: "为一个新品冷启动设计一套7天运营增长策略，包含投放、转化、复盘和风险控制。",
            expected_roles: &["ops"],
        },
        MatrixCase {
            name: "data_design_accounting_ops",
            task: "店铺A本周详情页UV从10000降到9200，详情页转化率从3.2%降到2.4%，加购率从8.5%\
                   降到6.1%，付费渠道CPC从1.2元升到1.6元，日预算上限3000元，毛利率35%。\
                   请基于这些数据诊断详情页问题，做详情页设计建议和预算风险评估，\
                   并给一套运营调整方案。",
            expected_roles: &["data", "design", "accounting", "ops"],
        },
        MatrixCase {
            name: "service",
            task: "客服团队近期投诉和退款增加，请设计一套售后SOP，包含首响话术、升级规则、\
                   退款边界和满意度复盘。",
            expected_roles: &["service"],
        },
        MatrixCase {
            name: "creative",
            task: "帮一个保温杯新品写3条短视频脚本钩子、3个标题和一版直播开场文案，不能编造功效。",
            expected_roles: &["ops", "creative"],
        },
        MatrixCase {
            name: "engineering",
            task: "系统接口延迟升高并影响SLA，请给出技术架构排查、性能优化、\
                   发布回滚和稳定性风险方案。",
            expected_roles: &["engineering"],
        },
        MatrixCase {
            name: "web",
            task: "为一个跨境电商独立站制定SEO关键词集群、标题优化、收录策略和自然流量增长方案。",
            expected_roles: &["web"],
        },
    ]
}

fn sorted_set(values: &HashSet<String>) -> Vec<String> {
    let mut values = values.iter().cloned().collect::<Vec<_>>();
    values.sort();
    values
}

fn build_ollama_agent_manager(llm: Arc<ChatCompletionsLlm>) -> cm_agent::api::Result<AgentManager> {
    let commander_llm = Arc::clone(&llm);
    let commander = Arc::new(move || {
        Ok(Box::new(CognitionEngine::new(
            Arc::clone(&commander_llm),
            "prompts/zh/cognition/commander_routing.md",
        )?) as Box<dyn Cognition + Send>)
    });

    let worker_llm = Arc::clone(&llm);
    let worker: RoleCognitionFactory = Arc::new(move |role: &RoleProfile| {
        Ok(Box::new(OllamaRoleWorkerCognition {
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

struct OllamaRoleWorkerCognition {
    llm: Arc<ChatCompletionsLlm>,
    role: RoleProfile,
}

#[async_trait]
impl Cognition for OllamaRoleWorkerCognition {
    async fn evaluate(&self, input: CognitionInput) -> CognitionResult {
        let system = format!(
            r#"你是 cm-agent 里的专用角色 worker。

当前角色：
- id: {}
- name: {}
- runtime_role: {}

你只负责完成当前 assignment 的本节点产物，不调度其他 worker。
如果 Context facts 中包含 upstream report，请把它们作为输入。

你必须只返回合法 JSON，不要 Markdown，不要代码块：
{{
  "decision": {{"kind": "NoAction"}},
  "role_output": {{
    "summary": "本角色一句话结论",
    "findings": ["本角色关键发现"],
    "recommendations": ["本角色建议或动作"],
    "evidence": ["你实际使用了哪些上游事实"],
    "risks": ["风险、边界或失败条件"],
    "open_questions": []
  }}
}}

除非确实无法继续，否则 role_output.open_questions 必须为空数组。
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
                return CognitionResult::Success(json!({
                    "decision": { "kind": "NoAction" },
                    "role_output": {
                        "summary": format!("LLM 调用失败：{err}"),
                        "findings": [],
                        "recommendations": [],
                        "evidence": [],
                        "risks": [format!("LLM 调用失败：{err}")],
                        "open_questions": [format!("LLM 调用失败：{err}")]
                    }
                }));
            }
        };

        println!(
            "worker_raw role={} output={}",
            self.role.runtime_role,
            truncate_for_log(&raw, 1200)
        );
        if let Some(parsed) = parse_role_json(&raw)
            && has_role_output_content(&parsed)
        {
            return CognitionResult::Success(parsed);
        }

        let repair_system = r#"你必须把上一条 worker 输出修正为合法 JSON。
只能返回 JSON，不能返回 Markdown。
必须包含非空 role_output.summary。
role_output 必须包含 findings、recommendations、evidence、risks、open_questions 数组。
如果上一条输出无法完成任务，也要在 role_output.summary 说明失败原因，并在 risks 或 open_questions 写明边界。"#;
        let repair_user = format!(
            "当前角色：{}\n原始任务输入：\n{}\n\n上一条输出：\n{}",
            self.role.runtime_role,
            render_worker_input(&input),
            raw
        );
        let repaired = match self
            .llm
            .chat_once(LlmInput {
                messages: vec![
                    ChatMessage::system(repair_system.to_string()),
                    ChatMessage::user(repair_user),
                ],
            })
            .await
        {
            Ok(output) => {
                let repaired_raw = output.get_content().trim().to_string();
                println!(
                    "worker_repair_raw role={} output={}",
                    self.role.runtime_role,
                    truncate_for_log(&repaired_raw, 1200)
                );
                parse_or_wrap_role_output(&repaired_raw)
            }
            Err(_) => parse_or_wrap_role_output(&raw),
        };

        CognitionResult::Success(repaired)
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

fn parse_or_wrap_role_output(raw: &str) -> Value {
    let parsed = parse_role_json(raw).or_else(|| salvage_role_output_from_text(raw));
    match parsed {
        Some(value) if has_role_output_content(&value) => value,
        Some(value) => json!({
            "decision": value.get("decision").cloned().unwrap_or_else(|| json!({ "kind": "NoAction" })),
            "role_output": {
                "summary": format!("模型未按契约返回有效 role_output，原始输出：{}", raw),
                "findings": [],
                "recommendations": [],
                "evidence": [],
                "risks": ["模型输出缺少结构化 role_output"],
                "open_questions": ["需要 worker 重新提交结构化 role_output"]
            }
        }),
        None => json!({
            "decision": { "kind": "NoAction" },
            "role_output": {
                "summary": raw,
                "findings": [],
                "recommendations": [],
                "evidence": [],
                "risks": [],
                "open_questions": []
            }
        }),
    }
}

fn parse_role_json(raw: &str) -> Option<Value> {
    parse_json_value_lenient(raw)
        .or_else(|| extract_json_object(raw).and_then(parse_json_value_lenient))
}

fn parse_json_value_lenient(raw: &str) -> Option<Value> {
    serde_json::from_str(raw)
        .ok()
        .or_else(|| serde_json::from_str(&escape_newlines_inside_json_strings(raw)).ok())
}

fn salvage_role_output_from_text(raw: &str) -> Option<Value> {
    if !raw.contains("role_output") {
        return None;
    }

    let summary = extract_json_string_field(raw, "summary").unwrap_or_default();
    let findings = extract_json_string_array_field(raw, "findings");
    let recommendations = extract_json_string_array_field(raw, "recommendations");
    let evidence = extract_json_string_array_field(raw, "evidence");
    let risks = extract_json_string_array_field(raw, "risks");
    let open_questions = extract_json_string_array_field(raw, "open_questions");
    if summary.trim().is_empty()
        && findings.is_empty()
        && recommendations.is_empty()
        && evidence.is_empty()
        && risks.is_empty()
        && open_questions.is_empty()
    {
        return None;
    }

    Some(json!({
        "decision": { "kind": "NoAction" },
        "role_output": {
            "summary": summary,
            "findings": findings,
            "recommendations": recommendations,
            "evidence": evidence,
            "risks": risks,
            "open_questions": open_questions
        }
    }))
}

fn extract_json_string_field(raw: &str, key: &str) -> Option<String> {
    let key_pos = raw.find(&format!("\"{key}\""))?;
    let after_key = &raw[key_pos ..];
    let colon_pos = after_key.find(':')?;
    let after_colon = after_key[colon_pos + 1 ..].trim_start();
    let mut chars = after_colon.chars();
    if chars.next()? != '"' {
        return None;
    }

    let mut value = String::new();
    let mut escape = false;
    for ch in chars {
        if escape {
            value.push(ch);
            escape = false;
            continue;
        }
        match ch {
            '\\' => escape = true,
            '"' => return Some(value),
            _ => value.push(ch),
        }
    }

    None
}

fn extract_json_string_array_field(raw: &str, key: &str) -> Vec<String> {
    let Some(key_pos) = raw.find(&format!("\"{key}\"")) else {
        return Vec::new();
    };
    let after_key = &raw[key_pos ..];
    let Some(colon_pos) = after_key.find(':') else {
        return Vec::new();
    };
    let after_colon = after_key[colon_pos + 1 ..].trim_start();
    let Some(array_start) = after_colon.find('[') else {
        return Vec::new();
    };
    let mut items = Vec::new();
    let mut in_string = false;
    let mut escape = false;
    let mut current = String::new();

    for ch in after_colon[array_start + 1 ..].chars() {
        if in_string {
            if escape {
                current.push(ch);
                escape = false;
                continue;
            }
            match ch {
                '\\' => escape = true,
                '"' => {
                    let item = current.trim();
                    if !item.is_empty() {
                        items.push(item.to_string());
                    }
                    current.clear();
                    in_string = false;
                }
                _ => current.push(ch),
            }
            continue;
        }

        match ch {
            '"' => in_string = true,
            ']' => break,
            _ => {}
        }
    }

    items
}

fn escape_newlines_inside_json_strings(raw: &str) -> String {
    let mut output = String::with_capacity(raw.len());
    let mut in_string = false;
    let mut escape = false;

    for ch in raw.chars() {
        if in_string {
            if escape {
                output.push(ch);
                escape = false;
                continue;
            }
            match ch {
                '\\' => {
                    output.push(ch);
                    escape = true;
                }
                '"' => {
                    output.push(ch);
                    in_string = false;
                }
                '\n' => output.push_str("\\n"),
                '\r' => {}
                _ => output.push(ch),
            }
            continue;
        }

        output.push(ch);
        if ch == '"' {
            in_string = true;
        }
    }

    output
}

fn has_role_output_content(value: &Value) -> bool {
    let Some(role_output) = value.get("role_output") else {
        return false;
    };
    if let Some(text) = role_output.as_str() {
        return !text.trim().is_empty();
    }
    let Some(role_output) = role_output.as_object() else {
        return false;
    };
    role_output
        .get("summary")
        .and_then(|summary| summary.as_str())
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

fn print_event(event: &SessionEvent) {
    match event {
        SessionEvent::TaskGraphPlanned { graph, .. } => {
            println!("event={} graph_id={}", event.event_name(), graph.graph_id.0);
            for node in &graph.nodes {
                let inputs = node
                    .input_refs
                    .iter()
                    .map(|input| input.0.as_str())
                    .collect::<Vec<_>>()
                    .join(",");
                println!(
                    "  node={} role={} worker={} inputs=[{}]",
                    node.id.0, node.role, node.worker_id.0, inputs
                );
            }
        }
        SessionEvent::TaskNodeStarted {
            node_id,
            worker_id,
            attempt,
            ..
        } => {
            println!(
                "event={} node={} worker={} attempt={}",
                event.event_name(),
                node_id.0,
                worker_id.0,
                attempt
            );
        }
        SessionEvent::TaskNodeEvaluated { evaluation, .. } => {
            println!(
                "event={} node={} passed={} score={} reasons={:?}",
                event.event_name(),
                evaluation.node_id.0,
                evaluation.passed,
                evaluation.score,
                evaluation.reasons
            );
        }
        SessionEvent::Output { content, .. } => {
            println!("event={}", event.event_name());
            println!("{content}");
        }
        SessionEvent::Failed { reason, .. } => {
            println!("event={} reason={reason}", event.event_name());
        }
        _ => println!("event={}", event.event_name()),
    }
}
