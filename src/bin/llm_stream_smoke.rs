use std::{sync::Arc, time::Duration};

use async_trait::async_trait;
use cm_agent::{
    AgentId, AgentManager, AgentManagerConfig, AgentRequest, Cognition, CognitionEngine,
    CognitionFailure, CognitionInput, CognitionResult, FailureReason, JsonTemplateDecoder,
    SessionEvent, SessionId, SessionRuntimeConfig, UserId,
    llm::{Llm, chat_completions::ChatCompletionsLlm},
    model::llm::{ChatMessage, LlmInput},
};
use serde_json::json;

const COMMANDER_PROMPT: &str = "prompts/zh/cognition/commander_routing.md";
const WORKER_PROMPT: &str = "prompts/zh/cognition/worker_execution.md";

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let base_url =
        std::env::var("OLLAMA_BASE_URL").unwrap_or_else(|_| "http://127.0.0.1:11434".to_string());
    let model = std::env::var("OLLAMA_MODEL").unwrap_or_else(|_| "gemma4:26b".to_string());
    let max_tokens = std::env::var("OLLAMA_MAX_TOKENS")
        .ok()
        .and_then(|value| value.parse::<u32>().ok())
        .unwrap_or(1_024);
    let timeout_secs = std::env::var("AGENT_TIMEOUT_SECS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(120);
    let heartbeat_secs = std::env::var("AGENT_HEARTBEAT_SECS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(5);
    let input = std::env::var("AGENT_INPUT").unwrap_or_else(|_| {
        "请把这个请求路由给合适的 worker，让 worker 完成一次最小任务并返回完成状态。".to_string()
    });
    let full_prompts = std::env::var("AGENT_FULL_PROMPTS")
        .map(|value| value == "1" || value.eq_ignore_ascii_case("true"))
        .unwrap_or(false);

    println!("llm_stream_smoke");
    println!("base_url={base_url}");
    println!("model={model}");
    println!("max_tokens={max_tokens}");
    println!("timeout_secs={timeout_secs}");
    println!("heartbeat_secs={heartbeat_secs}");
    println!("full_prompts={full_prompts}");
    println!("input={input}");
    println!("# 当前测试还没有 token 级 LlmChunk；LLM 调用期间会先输出 heartbeat。");
    println!("# 默认模式只用真实 LLM 做一次轻量 Commander 判断，Worker 使用快速假执行。");
    println!();

    let llm = Arc::new(
        ChatCompletionsLlm::new(&base_url, &model, None)?
            .with_temperature(Some(0.0))
            .with_max_tokens(Some(max_tokens)),
    );

    let commander_llm = llm.clone();
    let worker_llm = llm.clone();
    let stream_start = tokio::time::Instant::now();
    let mut config = if full_prompts {
        AgentManagerConfig::new(
            Arc::new(move || {
                Ok(Box::new(
                    CognitionEngine::<ChatCompletionsLlm, JsonTemplateDecoder>::new(
                        commander_llm.clone(),
                        COMMANDER_PROMPT,
                    )?,
                ) as Box<dyn Cognition + Send>)
            }),
            Arc::new(move || {
                Ok(Box::new(
                    CognitionEngine::<ChatCompletionsLlm, JsonTemplateDecoder>::new(
                        worker_llm.clone(),
                        WORKER_PROMPT,
                    )?,
                ) as Box<dyn Cognition + Send>)
            }),
        )
    } else {
        AgentManagerConfig::new(
            Arc::new(move || {
                Ok(Box::new(FastCommanderLlmCognition {
                    llm: commander_llm.clone(),
                    stream_start,
                }) as Box<dyn Cognition + Send>)
            }),
            Arc::new(|| Ok(Box::new(FastWorkerCognition) as Box<dyn Cognition + Send>)),
        )
    };
    config.runtime = SessionRuntimeConfig {
        event_buffer: 256,
        response_timeout: Duration::from_secs(timeout_secs),
        ..SessionRuntimeConfig::default()
    };

    let manager = AgentManager::new(config);
    let start = stream_start;
    let mut events = manager.run_stream(AgentRequest {
        user_id: Some(UserId("llm-stream-user".to_string())),
        workspace_id: None,
        agent_id: AgentId("llm-stream-agent".to_string()),
        session_id: SessionId("llm-stream-session".to_string()),
        input,
    })?;

    let mut heartbeat = tokio::time::interval(Duration::from_secs(heartbeat_secs));
    loop {
        tokio::select! {
            event = events.recv() => {
                let Some(event) = event else {
                    println!("# event channel closed");
                    break;
                };
                print_sse_event(start.elapsed().as_millis(), &event);
                if matches!(
                    event,
                    SessionEvent::Finished { .. } | SessionEvent::Failed { .. }
                ) {
                    break;
                }
            }
            _ = heartbeat.tick() => {
                println!(
                    ": elapsed_ms={} waiting_for=llm_or_agent active_sessions={}",
                    start.elapsed().as_millis(),
                    manager.active_session_count()
                );
                println!();
            }
        }
    }

    println!("# active_sessions={}", manager.active_session_count());
    Ok(())
}

fn print_sse_event(elapsed_ms: u128, event: &SessionEvent) {
    let (name, data) = match event {
        SessionEvent::Started { session_id } => {
            ("started", format!(r#"{{"session_id":"{}"}}"#, session_id.0))
        }
        SessionEvent::CommanderThinking { session_id } => (
            "commander_thinking",
            format!(r#"{{"session_id":"{}"}}"#, session_id.0),
        ),
        SessionEvent::WorkerStarted {
            session_id,
            worker_id,
            task_id,
        } => (
            "worker_started",
            format!(
                r#"{{"session_id":"{}","worker_id":"{}","task_id":"{}"}}"#,
                session_id.0, worker_id.0, task_id.0
            ),
        ),
        SessionEvent::LlmChunk {
            session_id,
            content,
        } => (
            "llm_chunk",
            format!(
                r#"{{"session_id":"{}","content":"{}"}}"#,
                session_id.0,
                escape_json(content)
            ),
        ),
        SessionEvent::WorkerFinished {
            session_id,
            worker_id,
            task_id,
        } => (
            "worker_finished",
            format!(
                r#"{{"session_id":"{}","worker_id":"{}","task_id":"{}"}}"#,
                session_id.0, worker_id.0, task_id.0
            ),
        ),
        SessionEvent::Output {
            session_id,
            content,
        } => (
            "output",
            format!(
                r#"{{"session_id":"{}","content":"{}"}}"#,
                session_id.0,
                escape_json(content)
            ),
        ),
        SessionEvent::Failed { session_id, reason } => (
            "failed",
            format!(
                r#"{{"session_id":"{}","reason":"{}"}}"#,
                session_id.0,
                escape_json(reason)
            ),
        ),
        SessionEvent::Finished { session_id } => (
            "finished",
            format!(r#"{{"session_id":"{}"}}"#, session_id.0),
        ),
    };

    println!(": elapsed_ms={elapsed_ms}");
    println!("event: {name}");
    println!("data: {data}");
    println!();
}

fn escape_json(value: &str) -> String {
    value
        .replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('\n', "\\n")
        .replace('\r', "\\r")
}

struct FastCommanderLlmCognition {
    llm: Arc<ChatCompletionsLlm>,
    stream_start: tokio::time::Instant,
}

#[async_trait]
impl Cognition for FastCommanderLlmCognition {
    async fn evaluate(&self, input: CognitionInput) -> CognitionResult {
        let prompt = format!(
            "你正在做 agent smoke test。请阅读任务，然后用一句中文说明你已经完成路由判断，最多 30 \
             个字，不要输出 JSON。\n任务：{}",
            input.intent.description
        );
        let output = match self
            .llm
            .chat_once(LlmInput {
                messages: vec![ChatMessage::user(prompt)],
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

        let raw = output.get_content().trim();
        println!("# commander_llm_raw={}", escape_json(raw));
        print_sse_event(
            self.stream_start.elapsed().as_millis(),
            &SessionEvent::LlmChunk {
                session_id: SessionId("llm-stream-session".to_string()),
                content: raw.to_string(),
            },
        );

        CognitionResult::Success(json!({
            "decision": {
                "kind": "RouteTask",
                "route": {
                    "target_agent_id": "worker-1",
                    "task_summary": input.intent.description,
                    "goal": "完成一次最小 agent 调度并返回完成状态",
                    "constraints": []
                },
                "handoff": {
                    "why_this_agent": "worker-1 是默认通用 worker",
                    "expected_output": "完成状态"
                },
                "clarification": {
                    "question": ""
                }
            },
            "confidence": 1.0,
            "rationale": {
                "primary": "LLM smoke check returned a response, route to default worker",
                "evidence": ["commander LLM responded during smoke test"],
                "alternatives_considered": []
            }
        }))
    }
}

struct FastWorkerCognition;

#[async_trait]
impl Cognition for FastWorkerCognition {
    async fn evaluate(&self, _input: CognitionInput) -> CognitionResult {
        CognitionResult::Success(json!({
            "decision": {
                "kind": "NoAction",
                "action": {
                    "action_type": "",
                    "parameters": {}
                },
                "state_change": {
                    "key": "",
                    "value": ""
                },
                "strategy": {
                    "priority": 0,
                    "hint": ""
                }
            },
            "confidence": 1.0,
            "rationale": {
                "primary": "fast worker finishes the smoke task without extra actions",
                "evidence": ["smoke test mode"],
                "alternatives_considered": []
            }
        }))
    }
}
