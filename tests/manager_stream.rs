use std::sync::Arc;

use async_trait::async_trait;
use cm_agent::api::{
    AgentId, AgentManager, AgentManagerConfig, AgentRequest, Cognition, CognitionInput,
    CognitionResult, SessionEvent, SessionId, UserId,
};
use serde_json::json;

struct TestCommanderCognition;

#[async_trait]
impl Cognition for TestCommanderCognition {
    async fn evaluate(&self, _input: CognitionInput) -> CognitionResult {
        CognitionResult::Success(json!({
            "decision": {
                "kind": "RouteTask",
                "route": {
                    "target_agent_id": "worker-1",
                    "task_summary": "stream integration",
                    "goal": "finish stream integration",
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
                "primary": "stream integration test",
                "evidence": [],
                "alternatives_considered": []
            }
        }))
    }
}

struct TestWorkerCognition;

#[async_trait]
impl Cognition for TestWorkerCognition {
    async fn evaluate(&self, _input: CognitionInput) -> CognitionResult {
        CognitionResult::Success(json!({
            "decision": {
                "kind": "NoAction"
            }
        }))
    }
}

#[tokio::test]
async fn manager_stream_finishes_and_cleans_active_session() {
    let manager = AgentManager::new(AgentManagerConfig::new(
        Arc::new(|| Ok(Box::new(TestCommanderCognition))),
        Arc::new(|| Ok(Box::new(TestWorkerCognition))),
    ));

    let mut rx = manager
        .run_stream(AgentRequest {
            user_id: Some(UserId("test-user".to_string())),
            workspace_id: None,
            agent_id: AgentId("test-agent".to_string()),
            session_id: SessionId("test-session".to_string()),
            input: "run stream integration".to_string(),
        })
        .expect("stream should start");

    let mut events = Vec::new();
    while let Some(event) = rx.recv().await {
        let terminal = matches!(
            event,
            SessionEvent::Finished { .. } | SessionEvent::Failed { .. }
        );
        events.push(event);
        if terminal {
            break;
        }
    }

    assert!(matches!(events.first(), Some(SessionEvent::Started { .. })));
    assert!(
        events
            .iter()
            .any(|event| matches!(event, SessionEvent::CommanderThinking { .. }))
    );
    assert!(
        events
            .iter()
            .any(|event| matches!(event, SessionEvent::Output { .. }))
    );
    assert!(matches!(events.last(), Some(SessionEvent::Finished { .. })));
    assert_eq!(manager.active_session_count(), 0);
}
