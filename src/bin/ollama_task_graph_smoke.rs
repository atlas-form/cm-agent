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

const DEFAULT_OLLAMA_BASE_URL: &str = "http://127.0.0.1:11434";
const DEFAULT_OLLAMA_MODEL: &str = "gemma4:26b";
const DEFAULT_TASK: &str = "基于数据诊断做详情页设计和预算风险评估，并给运营调整方案";

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
    let manager = Arc::new(build_ollama_agent_manager(llm)?);
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

        CognitionResult::Success(parse_or_wrap_role_output(&raw))
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
    serde_json::from_str(raw)
        .ok()
        .or_else(|| {
            extract_json_object(raw).and_then(|json_text| serde_json::from_str(json_text).ok())
        })
        .unwrap_or_else(|| {
            json!({
                "decision": { "kind": "NoAction" },
                "role_output": {
                    "summary": raw,
                    "findings": [],
                    "recommendations": [],
                    "evidence": [],
                    "risks": [],
                    "open_questions": []
                }
            })
        })
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
