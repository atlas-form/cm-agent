use std::{env, sync::Arc, time::Instant};

use async_trait::async_trait;
use cm_agent::api::{
    AgentId, AgentManager, AgentManagerConfig, AgentRequest, Cognition, CognitionFailure,
    CognitionInput, CognitionResult, Context, Fact, FailureReason, RoleProfile, RolePromptBuilder,
    SessionEvent, SessionId, SessionRuntimeConfig, UserId,
};
use model_gateway_rs::{
    llm::{Llm, chat_completions::ChatCompletionsLlm},
    model::llm::{ChatMessage, LlmInput},
};
use serde_json::json;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let base_url = env_value("AGENT_LLM_BASE_URL", "http://127.0.0.1:11434");
    let model = env_value("AGENT_LLM_MODEL", "gemma4:26b");
    let max_tokens = env_u32("AGENT_LLM_MAX_TOKENS", 32_000);
    let timeout_secs = env_u64("AGENT_SESSION_TIMEOUT_SECS", 3_600);
    let commander_mode = env_value("AGENT_COMMANDER_MODE", "fast");
    let commander_fast_route = commander_mode != "llm";
    let worker_mode = env_value("AGENT_WORKER_MODE", "llm");
    let commander_fast_route_min_score = env_f32("AGENT_COMMANDER_FAST_MIN_SCORE", 2.0);
    let input = env_value(
        "AGENT_INPUT",
        "帮我分析数据指标归因漏斗，并写一版直播脚本文案",
    );

    println!("llm_commander_smoke");
    println!("base_url={base_url}");
    println!("model={model}");
    println!("max_tokens={max_tokens}");
    println!("timeout_secs={timeout_secs}");
    println!("commander_mode={commander_mode}");
    println!("commander_fast_route_min_score={commander_fast_route_min_score}");
    println!("worker_mode={worker_mode}");
    println!("input={input}");
    println!("# fast 模式下 Commander 对高置信输入不调用 LLM，低置信输入会 fallback 到 LLM。");
    println!("# 设置 AGENT_COMMANDER_MODE=llm 时才会真实调用 Commander LLM 并打印 raw JSON。");
    println!("# Worker 默认调用真实 LLM；设置 AGENT_WORKER_MODE=fake 可只观察路由耗时。");
    println!();

    let llm = Arc::new(
        ChatCompletionsLlm::new(
            &base_url,
            &model,
            env::var("AGENT_LLM_API_KEY").ok().as_deref(),
        )?
        .with_temperature(Some(0.1))
        .with_max_tokens(Some(max_tokens)),
    );
    let commander_llm = Arc::clone(&llm);
    let worker_llm = Arc::clone(&llm);
    let worker_mode_for_factory = worker_mode.clone();

    let mut config = AgentManagerConfig::new_role_aware(
        Arc::new(move || {
            Ok(Box::new(PrintingCommanderCognition {
                llm: Arc::clone(&commander_llm),
                system_prompt: load_prompt("prompts/zh/cognition/commander_routing.md")?,
            }))
        }),
        Arc::new(move |role: &RoleProfile| {
            if worker_mode_for_factory == "fake" {
                return Ok(Box::new(SmokeWorkerCognition {
                    runtime_role: role.runtime_role.clone(),
                }) as Box<dyn Cognition + Send>);
            }

            Ok(Box::new(PrintingWorkerCognition {
                llm: Arc::clone(&worker_llm),
                runtime_role: role.runtime_role.clone(),
                system_prompt: format!(
                    "{}\n\n{}",
                    load_prompt("prompts/zh/cognition/worker_execution.md")?,
                    RolePromptBuilder::build_worker_system_prompt(role)
                ),
            }) as Box<dyn Cognition + Send>)
        }),
    );
    config.runtime = SessionRuntimeConfig {
        response_timeout: std::time::Duration::from_secs(timeout_secs),
        commander_fast_route,
        commander_fast_route_min_score,
        ..SessionRuntimeConfig::default()
    };

    let manager = AgentManager::new(config);
    let started = Instant::now();
    let mut events = manager.run_stream(AgentRequest {
        user_id: Some(UserId("llm-smoke-user".to_string())),
        workspace_id: None,
        agent_id: AgentId("llm-smoke-agent".to_string()),
        session_id: SessionId("llm-commander-smoke-session".to_string()),
        input,
    })?;

    while let Some(event) = events.recv().await {
        if matches!(event, SessionEvent::CommanderThinking { .. }) {
            continue;
        }
        print_sse_event(started.elapsed().as_millis(), &event);
        if matches!(
            event,
            SessionEvent::Finished { .. } | SessionEvent::Failed { .. }
        ) {
            break;
        }
    }

    println!("# active_sessions={}", manager.active_session_count());
    Ok(())
}

struct PrintingCommanderCognition {
    llm: Arc<ChatCompletionsLlm>,
    system_prompt: String,
}

#[async_trait]
impl Cognition for PrintingCommanderCognition {
    async fn evaluate(&self, input: CognitionInput) -> CognitionResult {
        let output = match self
            .llm
            .chat_once(LlmInput {
                messages: vec![
                    ChatMessage::system(self.system_prompt.clone()),
                    ChatMessage::user(render_input(&input)),
                ],
            })
            .await
        {
            Ok(output) => output,
            Err(err) => {
                return CognitionResult::Failure(CognitionFailure {
                    reason: FailureReason::InternalError,
                    description: err.to_string(),
                });
            }
        };

        let raw = output.get_content().trim().to_string();
        println!("# commander_llm_raw_begin");
        println!("{raw}");
        println!("# commander_llm_raw_end");
        println!();

        match serde_json::from_str(&strip_json_fence(&raw)) {
            Ok(value) => CognitionResult::Success(value),
            Err(err) => CognitionResult::Failure(CognitionFailure {
                reason: FailureReason::InternalError,
                description: format!("failed to parse commander JSON: {err}; raw={raw}"),
            }),
        }
    }
}

struct PrintingWorkerCognition {
    llm: Arc<ChatCompletionsLlm>,
    runtime_role: String,
    system_prompt: String,
}

#[async_trait]
impl Cognition for PrintingWorkerCognition {
    async fn evaluate(&self, input: CognitionInput) -> CognitionResult {
        let output = match self
            .llm
            .chat_once(LlmInput {
                messages: vec![
                    ChatMessage::system(self.system_prompt.clone()),
                    ChatMessage::user(render_input(&input)),
                ],
            })
            .await
        {
            Ok(output) => output,
            Err(err) => {
                return CognitionResult::Failure(CognitionFailure {
                    reason: FailureReason::InternalError,
                    description: err.to_string(),
                });
            }
        };

        let raw = output.get_content().trim().to_string();
        println!("# worker_llm_raw_begin role={}", self.runtime_role);
        println!("{raw}");
        println!("# worker_llm_raw_end role={}", self.runtime_role);
        println!();

        match serde_json::from_str(&strip_json_fence(&raw)) {
            Ok(value) => CognitionResult::Success(value),
            Err(err) => CognitionResult::Failure(CognitionFailure {
                reason: FailureReason::InternalError,
                description: format!(
                    "failed to parse worker JSON for role={}: {err}; raw={raw}",
                    self.runtime_role
                ),
            }),
        }
    }
}

struct SmokeWorkerCognition {
    runtime_role: String,
}

#[async_trait]
impl Cognition for SmokeWorkerCognition {
    async fn evaluate(&self, input: CognitionInput) -> CognitionResult {
        CognitionResult::Success(json!({
            "decision": {
                "kind": "NoAction"
            },
            "confidence": 1.0,
            "rationale": {
                "primary": format!("{} role smoke worker completed without real LLM", self.runtime_role),
                "evidence": [
                    format!("intent={}", input.intent.description),
                    format!("role={}", self.runtime_role)
                ],
                "alternatives_considered": []
            }
        }))
    }
}

fn load_prompt(relative_path: &str) -> cm_agent::api::Result<String> {
    let path = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join(relative_path);
    std::fs::read_to_string(path).map_err(Into::into)
}

fn render_input(input: &CognitionInput) -> String {
    format!(
        "Intent:\n- id: {}\n- kind: {:?}\n- description: {}\n\nContext facts:\n{}\n\nContext \
         metadata:\n{}",
        input.intent.id,
        input.intent.kind,
        input.intent.description,
        render_facts(&input.context),
        render_metadata(&input.context)
    )
}

fn render_facts(context: &Context) -> String {
    if context.facts.is_empty() {
        return "- (none)".to_string();
    }

    context
        .facts
        .iter()
        .map(
            |Fact {
                 source,
                 reliability,
                 content,
             }| {
                format!("- source: {source} | reliability: {reliability} | content: {content}")
            },
        )
        .collect::<Vec<_>>()
        .join("\n")
}

fn render_metadata(context: &Context) -> String {
    if context.metadata.is_empty() {
        return "- (none)".to_string();
    }

    context
        .metadata
        .iter()
        .map(|(key, value)| format!("- {key}: {value}"))
        .collect::<Vec<_>>()
        .join("\n")
}

fn strip_json_fence(raw: &str) -> String {
    let trimmed = raw.trim();
    if !trimmed.starts_with("```") {
        return trimmed.to_string();
    }

    trimmed
        .trim_start_matches("```json")
        .trim_start_matches("```")
        .trim_end_matches("```")
        .trim()
        .to_string()
}

fn print_sse_event(elapsed_ms: u128, event: &SessionEvent) {
    println!(": elapsed_ms={elapsed_ms}");
    println!("event: {}", event.event_name());
    println!(
        "data: {}",
        serde_json::to_string(event).expect("serialize session event")
    );
    println!();
}

fn env_value(key: &str, default: &str) -> String {
    env::var(key).unwrap_or_else(|_| default.to_string())
}

fn env_u32(key: &str, default: u32) -> u32 {
    env::var(key)
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(default)
}

fn env_u64(key: &str, default: u64) -> u64 {
    env::var(key)
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(default)
}

fn env_f32(key: &str, default: f32) -> f32 {
    env::var(key)
        .ok()
        .and_then(|value| value.parse().ok())
        .unwrap_or(default)
}
