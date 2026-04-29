use std::sync::Arc;

use async_trait::async_trait;
use cm_agent::api::{
    AgentId, AgentManager, AgentManagerConfig, AgentRequest, Cognition, CognitionInput,
    CognitionResult, Result, SessionEvent, SessionId, UserId,
};
use serde_json::json;

struct CommanderStreamCognition;

#[async_trait]
impl Cognition for CommanderStreamCognition {
    async fn evaluate(&self, _input: CognitionInput) -> CognitionResult {
        CognitionResult::Success(json!({
            "decision": {
                "kind": "RouteTask",
                "route": {
                    "target_agent_id": "worker-1",
                    "task_summary": "stream smoke",
                    "goal": "finish stream smoke",
                    "constraints": []
                },
                "handoff": {
                    "why_this_agent": "default worker",
                    "expected_output": "done"
                },
                "clarification": {
                    "question": ""
                }
            },
            "confidence": 1.0,
            "rationale": {
                "primary": "stream smoke test",
                "evidence": [],
                "alternatives_considered": []
            }
        }))
    }
}

struct WorkerStreamCognition;

#[async_trait]
impl Cognition for WorkerStreamCognition {
    async fn evaluate(&self, _input: CognitionInput) -> CognitionResult {
        CognitionResult::Success(json!({
            "decision": {
                "kind": "NoAction"
            }
        }))
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    let manager = AgentManager::new(AgentManagerConfig::new(
        Arc::new(|| Ok(Box::new(CommanderStreamCognition))),
        Arc::new(|| Ok(Box::new(WorkerStreamCognition))),
    ));

    let mut events = manager.run_stream(AgentRequest {
        user_id: Some(UserId("stream-user".to_string())),
        workspace_id: None,
        agent_id: AgentId("stream-agent".to_string()),
        session_id: SessionId("stream-session".to_string()),
        input: "run stream smoke".to_string(),
    })?;

    while let Some(event) = events.recv().await {
        println!("{event:?}");
        if matches!(
            event,
            SessionEvent::Finished { .. } | SessionEvent::Failed { .. }
        ) {
            break;
        }
    }

    println!("active_sessions={}", manager.active_session_count());
    assert_eq!(manager.active_session_count(), 0);
    Ok(())
}
