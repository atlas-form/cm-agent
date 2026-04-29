use std::{
    collections::HashSet,
    sync::{Arc, Mutex},
};

use async_trait::async_trait;
use cm_agent::api::{
    AgentId, AgentManager, AgentManagerConfig, AgentRequest, Cognition, CognitionInput,
    CognitionResult, RoleProfile, SessionEvent, SessionId, UserId,
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

struct RouteWithoutTargetCommanderCognition;

#[async_trait]
impl Cognition for RouteWithoutTargetCommanderCognition {
    async fn evaluate(&self, _input: CognitionInput) -> CognitionResult {
        CognitionResult::Success(json!({
            "decision": {
                "kind": "RouteTask",
                "route": {
                    "target_agent_id": "",
                    "task_summary": "route without target",
                    "goal": "let role router choose the worker",
                    "constraints": []
                },
                "handoff": {
                    "why_this_agent": "router should choose",
                    "expected_output": "done"
                },
                "clarification": {
                    "question": ""
                }
            },
            "confidence": 1.0,
            "rationale": {
                "primary": "role router fallback test",
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

struct CapturingWorkerSetCognition {
    runtime_roles: Arc<Mutex<HashSet<String>>>,
}

#[async_trait]
impl Cognition for CapturingWorkerSetCognition {
    async fn evaluate(&self, input: CognitionInput) -> CognitionResult {
        if let Some(runtime_role) = input.context.metadata.get("role.runtime_role") {
            self.runtime_roles
                .lock()
                .expect("lock role set capture")
                .insert(runtime_role.clone());
        }

        CognitionResult::Success(json!({
            "decision": {
                "kind": "NoAction"
            }
        }))
    }
}

#[tokio::test]
async fn manager_initializes_builtin_roles_for_each_session() {
    let manager = AgentManager::new(AgentManagerConfig::new(
        Arc::new(|| Ok(Box::new(RouteWithoutTargetCommanderCognition))),
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
    assert_eq!(constructed_roles.len(), 9);
    assert!(constructed_roles.contains("chat"));
    assert!(constructed_roles.contains("ops"));
    assert!(constructed_roles.contains("data"));
    assert!(constructed_roles.contains("web"));
}

#[tokio::test]
async fn commander_routes_plain_question_to_chat_role() {
    let runtime_role = Arc::new(Mutex::new(None));
    let worker_capture = Arc::clone(&runtime_role);
    let manager = AgentManager::new(AgentManagerConfig::new(
        Arc::new(|| Ok(Box::new(RouteWithoutTargetCommanderCognition))),
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
            session_id: SessionId("role-session-chat".to_string()),
            input: "你能解释一下这个 agent 是什么吗".to_string(),
        })
        .await
        .expect("request should complete");

    assert_eq!(
        runtime_role.lock().expect("lock role capture").as_deref(),
        Some("chat")
    );
}

#[tokio::test]
async fn commander_uses_role_router_when_target_worker_is_missing() {
    let runtime_roles = Arc::new(Mutex::new(HashSet::new()));
    let worker_capture = Arc::clone(&runtime_roles);
    let manager = AgentManager::new(AgentManagerConfig::new(
        Arc::new(|| Ok(Box::new(RouteWithoutTargetCommanderCognition))),
        Arc::new(move || {
            Ok(Box::new(CapturingWorkerSetCognition {
                runtime_roles: Arc::clone(&worker_capture),
            }))
        }),
    ));

    manager
        .run_request(AgentRequest {
            user_id: Some(UserId("role-user".to_string())),
            workspace_id: None,
            agent_id: AgentId("role-agent".to_string()),
            session_id: SessionId("role-session-router".to_string()),
            input: "帮我分析漏斗指标和归因口径".to_string(),
        })
        .await
        .expect("request should complete");

    let runtime_roles = runtime_roles.lock().expect("lock role set capture");
    assert!(runtime_roles.contains("data"));
}

#[tokio::test]
async fn commander_dispatches_task_graph_roles_after_upstream_role() {
    let runtime_roles = Arc::new(Mutex::new(HashSet::new()));
    let worker_capture = Arc::clone(&runtime_roles);
    let manager = AgentManager::new(AgentManagerConfig::new(
        Arc::new(|| Ok(Box::new(RouteWithoutTargetCommanderCognition))),
        Arc::new(move || {
            Ok(Box::new(CapturingWorkerSetCognition {
                runtime_roles: Arc::clone(&worker_capture),
            }))
        }),
    ));

    let result = manager
        .run_request(AgentRequest {
            user_id: Some(UserId("role-user".to_string())),
            workspace_id: None,
            agent_id: AgentId("role-agent".to_string()),
            session_id: SessionId("role-session-support".to_string()),
            input: "帮我分析数据指标归因漏斗，并写一版直播脚本文案".to_string(),
        })
        .await
        .expect("request should complete");

    let runtime_roles = runtime_roles.lock().expect("lock role set capture");
    assert!(runtime_roles.contains("data"));
    assert!(runtime_roles.contains("creative"));
    assert!(result.output.contains("多智能体任务图已完成"));
    assert!(result.output.contains("数据诊断(data)"));
    assert!(result.output.contains("创意产出(creative)"));
}

#[tokio::test]
async fn stream_emits_task_graph_events() {
    let manager = AgentManager::new(AgentManagerConfig::new(
        Arc::new(|| Ok(Box::new(RouteWithoutTargetCommanderCognition))),
        Arc::new(|| Ok(Box::new(NoopWorkerCognition))),
    ));

    let mut rx = manager
        .run_stream(AgentRequest {
            user_id: Some(UserId("role-user".to_string())),
            workspace_id: None,
            agent_id: AgentId("role-agent".to_string()),
            session_id: SessionId("role-session-collab-stream".to_string()),
            input: "帮我分析数据指标归因漏斗，并写一版直播脚本文案".to_string(),
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

    assert!(
        events
            .iter()
            .any(|event| matches!(event, SessionEvent::WorkerStarted { .. }))
    );
    assert!(
        events
            .iter()
            .any(|event| matches!(event, SessionEvent::WorkerFinished { .. }))
    );
    assert!(
        events
            .iter()
            .any(|event| matches!(event, SessionEvent::TaskGraphPlanned { .. }))
    );
    assert!(
        events
            .iter()
            .any(|event| matches!(event, SessionEvent::TaskNodeStarted { .. }))
    );
    assert!(
        events
            .iter()
            .any(|event| matches!(event, SessionEvent::TaskGraphFinished { .. }))
    );
}
