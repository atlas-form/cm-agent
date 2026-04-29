use std::sync::Arc;

use async_trait::async_trait;
use cm_agent::api::{
    AgentId, AgentManager, AgentManagerConfig, AgentRequest, Cognition, CognitionInput,
    CognitionResult, SessionId, UserId,
};
use serde_json::json;

pub fn build_agent_manager() -> AgentManager {
    AgentManager::new(AgentManagerConfig::new(
        Arc::new(|| Ok(Box::new(ExampleCommanderCognition))),
        Arc::new(|| Ok(Box::new(ExampleWorkerCognition))),
    ))
}

pub fn agent_request(user_id: &str, session_id: &str, input: &str) -> AgentRequest {
    AgentRequest {
        user_id: Some(UserId(user_id.to_string())),
        workspace_id: None,
        agent_id: AgentId("web-agent".to_string()),
        session_id: SessionId(session_id.to_string()),
        input: input.to_string(),
    }
}

struct ExampleCommanderCognition;

#[async_trait]
impl Cognition for ExampleCommanderCognition {
    async fn evaluate(&self, input: CognitionInput) -> CognitionResult {
        CognitionResult::Success(json!({
            "decision": {
                "kind": "RouteTask",
                "route": {
                    "target_agent_id": "worker-1",
                    "task_summary": input.intent.description,
                    "goal": "finish the web API example request",
                    "constraints": []
                },
                "handoff": {
                    "why_this_agent": "worker-1 is the default general worker",
                    "expected_output": "done"
                },
                "clarification": {
                    "question": ""
                }
            },
            "confidence": 1.0,
            "rationale": {
                "primary": "web API example",
                "evidence": [],
                "alternatives_considered": []
            }
        }))
    }
}

struct ExampleWorkerCognition;

#[async_trait]
impl Cognition for ExampleWorkerCognition {
    async fn evaluate(&self, _input: CognitionInput) -> CognitionResult {
        CognitionResult::Success(json!({
            "decision": {
                "kind": "NoAction"
            }
        }))
    }
}
