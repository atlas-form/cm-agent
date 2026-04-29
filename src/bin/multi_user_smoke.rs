use std::{sync::Arc, time::Duration};

use async_trait::async_trait;
use cm_agent::api::{
    AgentId, AgentManager, AgentManagerConfig, AgentRequest, Cognition, CognitionInput,
    CognitionResult, Error, Result, SessionId, UserId,
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
                    "task_summary": "multi-user smoke",
                    "goal": "finish one isolated request",
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
                "primary": "multi-user smoke test",
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
        tokio::time::sleep(Duration::from_millis(50)).await;
        CognitionResult::Success(json!({
            "decision": {
                "kind": "NoAction"
            }
        }))
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    let manager = Arc::new(AgentManager::new(AgentManagerConfig::new(
        Arc::new(|| Ok(Box::new(CommanderSmokeCognition))),
        Arc::new(|| Ok(Box::new(WorkerSmokeCognition))),
    )));

    let mut handles = Vec::new();
    for index in 0 .. 10 {
        let manager = Arc::clone(&manager);
        handles.push(tokio::spawn(async move {
            let user_id = format!("user-{index}");
            let session_id = format!("session-{index}");

            let result = manager
                .run_request(AgentRequest {
                    user_id: Some(UserId(user_id.clone())),
                    workspace_id: None,
                    agent_id: AgentId("multi-user-agent".to_string()),
                    session_id: SessionId(session_id.clone()),
                    input: format!("run request for {user_id}"),
                })
                .await?;

            Ok::<_, Error>((user_id, session_id, result.output))
        }));
    }

    for handle in handles {
        let (user_id, session_id, output) = handle.await.expect("multi-user task panicked")?;
        println!("{user_id} / {session_id} -> {output}");
    }

    println!("active_sessions={}", manager.active_session_count());
    assert_eq!(manager.active_session_count(), 0);
    Ok(())
}
