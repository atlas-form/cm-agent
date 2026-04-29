use std::sync::Arc;

use async_trait::async_trait;
use cm_agent::{
    AgentId, AgentManager, AgentManagerConfig, AgentRequest, Cognition, CognitionInput,
    CognitionResult, SessionId, UserId,
};
use serde_json::json;

struct CommanderSmokeCognition;

#[async_trait]
impl Cognition for CommanderSmokeCognition {
    async fn evaluate(&self, _input: CognitionInput) -> CognitionResult {
        CognitionResult::Success(json!({
            "decision": {
                "kind": "RouteTask",
                "route": {
                    "target_agent_id": "worker-1",
                    "task_summary": "smoke",
                    "goal": "finish smoke",
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
                "primary": "smoke test",
                "evidence": [],
                "alternatives_considered": []
            }
        }))
    }
}

struct WorkerSmokeCognition;

#[async_trait]
impl Cognition for WorkerSmokeCognition {
    async fn evaluate(&self, _input: CognitionInput) -> CognitionResult {
        CognitionResult::Success(json!({
            "decision": {
                "kind": "NoAction"
            }
        }))
    }
}

#[tokio::main]
async fn main() -> cm_agent::agent_error::Result<()> {
    let manager = AgentManager::new(AgentManagerConfig::new(
        Arc::new(|| Ok(Box::new(CommanderSmokeCognition))),
        Arc::new(|| Ok(Box::new(WorkerSmokeCognition))),
    ));

    let result = manager
        .run_request(AgentRequest {
            user_id: Some(UserId("smoke-user".to_string())),
            workspace_id: None,
            agent_id: AgentId("smoke-agent".to_string()),
            session_id: SessionId("smoke-session".to_string()),
            input: "run smoke".to_string(),
        })
        .await?;

    println!("{}", result.output);
    Ok(())
}
