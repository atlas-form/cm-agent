use std::{
    collections::HashSet,
    sync::{Arc, Mutex},
};

use async_trait::async_trait;
use cm_agent::api::{
    AgentId, AgentManager, AgentManagerConfig, AgentRequest, Cognition, CognitionInput,
    CognitionResult, InMemoryMemoryStore, MemoryKind, RoleProfile, SessionEvent, SessionId, UserId,
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
    async fn evaluate(&self, input: CognitionInput) -> CognitionResult {
        CognitionResult::Success(json!({
            "decision": {
                "kind": "NoAction"
            },
            "role_output": test_role_output(&input)
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
            },
            "role_output": test_role_output(&input)
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
            },
            "role_output": test_role_output(&input)
        }))
    }
}

fn test_role_output(input: &CognitionInput) -> serde_json::Value {
    let role = input
        .context
        .metadata
        .get("role.runtime_role")
        .map(String::as_str)
        .unwrap_or("chat");
    let mut evidence = vec!["原始任务".to_string()];
    if input
        .context
        .metadata
        .get("assignment.input_count")
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(0)
        > 0
    {
        evidence.push("worker.assignment.input.data".to_string());
    }

    match role {
        "data" => json!({
            "summary": "数据角色已完成漏斗指标和归因口径诊断。",
            "findings": ["漏斗指标需要拆解", "归因口径需要统一"],
            "recommendations": ["按渠道和页面阶段复核转化断点"],
            "evidence": evidence,
            "risks": ["口径不一致会导致误判"],
            "open_questions": []
        }),
        "creative" => json!({
            "summary": "创意角色已形成直播脚本文案方向。",
            "findings": ["内容需要突出首段钩子"],
            "recommendations": ["输出三版直播脚本开头和标题文案"],
            "evidence": evidence,
            "risks": ["不能编造产品功效或用户反馈"],
            "open_questions": []
        }),
        "ops" => json!({
            "summary": "运营角色已形成可执行运营调整方案。",
            "findings": ["运营目标需要转化链路承接"],
            "recommendations": ["P1 优化首屏卖点", "P2 调整投放人群", "P3 每日复盘转化"],
            "evidence": evidence,
            "risks": ["预算消耗需要止损线"],
            "open_questions": []
        }),
        _ => json!({
            "summary": "测试 worker 已完成当前角色节点。",
            "findings": ["已基于当前任务形成判断"],
            "recommendations": ["按角色边界推进下一步"],
            "evidence": evidence,
            "risks": ["需要确认上下文边界"],
            "open_questions": []
        }),
    }
}

#[tokio::test]
async fn manager_initializes_selected_roles_for_each_session() {
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
            input: "你能解释一下这个 agent 是什么吗".to_string(),
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
            input: "帮我做一个运营增长方案".to_string(),
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
async fn role_aware_worker_factory_receives_selected_roles() {
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
            input: "我是做咖啡运营的，主要营销平台是抖音，现在马上就五一了，请给提升转化率的方案"
                .to_string(),
        })
        .await
        .expect("request should complete");

    let constructed_roles = constructed_roles.lock().expect("lock constructed roles");
    assert!(constructed_roles.contains("chat"));
    assert!(constructed_roles.contains("ops"));
    assert!(constructed_roles.contains("data"));
    assert!(constructed_roles.contains("accounting"));
    assert!(constructed_roles.contains("creative"));
    assert!(!constructed_roles.contains("engineering"));
    assert!(!constructed_roles.contains("web"));
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
async fn task_graph_result_carries_memory_snapshot_fields() {
    let manager = AgentManager::new(AgentManagerConfig::new(
        Arc::new(|| Ok(Box::new(RouteWithoutTargetCommanderCognition))),
        Arc::new(|| Ok(Box::new(NoopWorkerCognition))),
    ));

    let result = manager
        .run_request(AgentRequest {
            user_id: Some(UserId("role-user".to_string())),
            workspace_id: None,
            agent_id: AgentId("role-agent".to_string()),
            session_id: SessionId("role-session-memory-snapshot".to_string()),
            input: "帮我分析数据指标归因漏斗，并写一版直播脚本文案".to_string(),
        })
        .await
        .expect("request should complete");

    assert_eq!(result.task_graphs.len(), 1);
    assert!(
        result
            .role_summaries
            .iter()
            .any(|summary| summary.role == "data" && summary.summary.contains("数据角色已完成"))
    );
    assert!(
        result
            .role_summaries
            .iter()
            .any(|summary| summary.role == "creative"
                && summary.summary.contains("创意角色已形成"))
    );
    assert!(result.risks.iter().any(|risk| risk.contains("口径不一致")));
    assert!(
        result
            .evaluation_summary
            .as_deref()
            .is_some_and(|summary| summary.contains("passed=true"))
    );
}

#[tokio::test]
async fn task_graph_memory_persists_structured_output_without_prompt_leaks() {
    let memory_store = Arc::new(InMemoryMemoryStore::new());
    let mut config = AgentManagerConfig::new(
        Arc::new(|| Ok(Box::new(RouteWithoutTargetCommanderCognition))),
        Arc::new(|| Ok(Box::new(NoopWorkerCognition))),
    );
    config.memory_store = memory_store.clone();
    let manager = AgentManager::new(config);

    manager
        .run_request(AgentRequest {
            user_id: Some(UserId("role-user".to_string())),
            workspace_id: None,
            agent_id: AgentId("role-agent".to_string()),
            session_id: SessionId("role-session-memory-persist".to_string()),
            input: "帮我分析数据指标归因漏斗，并写一版直播脚本文案".to_string(),
        })
        .await
        .expect("request should complete");

    let records = memory_store.records();
    assert!(
        records
            .iter()
            .any(|record| record.kind == MemoryKind::CrossRoleContext
                && record.content.contains("数据角色已完成"))
    );
    assert!(
        records
            .iter()
            .any(|record| record.tags.contains(&"risk".to_string())
                && record.content.contains("口径不一致"))
    );

    let joined_memory = records
        .iter()
        .map(|record| record.content.as_str())
        .collect::<Vec<_>>()
        .join("\n");
    for forbidden in [
        "system prompt",
        "developer instructions",
        "role prompt",
        "sandbox policy",
        "runtime config",
    ] {
        assert!(
            !joined_memory.to_lowercase().contains(forbidden),
            "memory should not contain {forbidden}"
        );
    }
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
