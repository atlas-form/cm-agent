use std::{
    collections::HashSet,
    sync::{Arc, Mutex},
};

use async_trait::async_trait;
use cm_agent::api::{
    AgentId, AgentManager, AgentManagerConfig, AgentRequest, Cognition, CognitionInput,
    CognitionResult, RoleProfile, SessionId, UserId,
};
use serde_json::json;

struct CapturingCommanderCognition {
    available_workers: Arc<Mutex<Option<String>>>,
}

#[async_trait]
impl Cognition for CapturingCommanderCognition {
    async fn evaluate(&self, input: CognitionInput) -> CognitionResult {
        let count = input
            .context
            .metadata
            .get("available_workers.count")
            .cloned();
        *self.available_workers.lock().expect("lock capture") = count;

        CognitionResult::Success(json!({
            "decision": {
                "kind": "RouteTask",
                "route": {
                    "target_agent_id": "worker.ops",
                    "task_summary": "default roles",
                    "goal": "verify default roles",
                    "constraints": []
                },
                "handoff": {
                    "why_this_agent": "ops is a default role worker",
                    "expected_output": "done"
                },
                "clarification": {
                    "question": ""
                }
            },
            "confidence": 1.0,
            "rationale": {
                "primary": "default role catalog test",
                "evidence": [],
                "alternatives_considered": []
            }
        }))
    }
}

struct NoopWorkerCognition;

#[async_trait]
impl Cognition for NoopWorkerCognition {
    async fn evaluate(&self, _input: CognitionInput) -> CognitionResult {
        CognitionResult::Success(json!({
            "decision": {
                "kind": "NoAction"
            }
        }))
    }
}

struct CapturingWorkerCognition {
    runtime_role: Arc<Mutex<Option<String>>>,
}

#[async_trait]
impl Cognition for CapturingWorkerCognition {
    async fn evaluate(&self, input: CognitionInput) -> CognitionResult {
        *self.runtime_role.lock().expect("lock role capture") =
            input.context.metadata.get("role.runtime_role").cloned();

        CognitionResult::Success(json!({
            "decision": {
                "kind": "NoAction"
            }
        }))
    }
}

#[tokio::test]
async fn manager_initializes_builtin_roles_for_each_session() {
    let available_workers = Arc::new(Mutex::new(None));
    let commander_capture = Arc::clone(&available_workers);
    let manager = AgentManager::new(AgentManagerConfig::new(
        Arc::new(move || {
            Ok(Box::new(CapturingCommanderCognition {
                available_workers: Arc::clone(&commander_capture),
            }))
        }),
        Arc::new(|| Ok(Box::new(NoopWorkerCognition))),
    ));

    manager
        .run_request(AgentRequest {
            user_id: Some(UserId("role-user".to_string())),
            workspace_id: None,
            agent_id: AgentId("role-agent".to_string()),
            session_id: SessionId("role-session".to_string()),
            input: "verify default roles".to_string(),
        })
        .await
        .expect("request should complete");

    assert_eq!(
        available_workers.lock().expect("lock capture").as_deref(),
        Some("8")
    );
    assert_eq!(manager.active_session_count(), 0);
}

#[tokio::test]
async fn role_worker_receives_role_context() {
    let available_workers = Arc::new(Mutex::new(None));
    let runtime_role = Arc::new(Mutex::new(None));
    let commander_capture = Arc::clone(&available_workers);
    let worker_capture = Arc::clone(&runtime_role);
    let manager = AgentManager::new(AgentManagerConfig::new(
        Arc::new(move || {
            Ok(Box::new(CapturingCommanderCognition {
                available_workers: Arc::clone(&commander_capture),
            }))
        }),
        Arc::new(move || {
            Ok(Box::new(CapturingWorkerCognition {
                runtime_role: Arc::clone(&worker_capture),
            }))
        }),
    ));

    manager
        .run_request(AgentRequest {
            user_id: Some(UserId("role-user".to_string())),
            workspace_id: None,
            agent_id: AgentId("role-agent".to_string()),
            session_id: SessionId("role-session-context".to_string()),
            input: "verify role context".to_string(),
        })
        .await
        .expect("request should complete");

    assert_eq!(
        runtime_role.lock().expect("lock role capture").as_deref(),
        Some("ops")
    );
    assert_eq!(manager.active_session_count(), 0);
}

#[tokio::test]
async fn role_aware_worker_factory_receives_each_builtin_role() {
    let available_workers = Arc::new(Mutex::new(None));
    let constructed_roles = Arc::new(Mutex::new(HashSet::new()));
    let commander_capture = Arc::clone(&available_workers);
    let role_capture = Arc::clone(&constructed_roles);
    let manager = AgentManager::new(AgentManagerConfig::new_role_aware(
        Arc::new(move || {
            Ok(Box::new(CapturingCommanderCognition {
                available_workers: Arc::clone(&commander_capture),
            }))
        }),
        Arc::new(move |role: &RoleProfile| {
            role_capture
                .lock()
                .expect("lock constructed roles")
                .insert(role.runtime_role.clone());
            Ok(Box::new(NoopWorkerCognition))
        }),
    ));

    manager
        .run_request(AgentRequest {
            user_id: Some(UserId("role-user".to_string())),
            workspace_id: None,
            agent_id: AgentId("role-agent".to_string()),
            session_id: SessionId("role-session-factory".to_string()),
            input: "verify role factory".to_string(),
        })
        .await
        .expect("request should complete");

    let constructed_roles = constructed_roles.lock().expect("lock constructed roles");
    assert_eq!(constructed_roles.len(), 8);
    assert!(constructed_roles.contains("ops"));
    assert!(constructed_roles.contains("data"));
    assert!(constructed_roles.contains("web"));
}
