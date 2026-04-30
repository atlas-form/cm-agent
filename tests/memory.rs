use std::{
    sync::{Arc, Mutex},
    time::SystemTime,
};

use async_trait::async_trait;
use cm_agent::api::{
    AgentId, AgentManager, AgentManagerConfig, AgentRequest, Cognition, CognitionInput,
    CognitionResult, InMemoryMemoryStore, MemoryId, MemoryKind, MemoryQuery, MemoryRecord,
    MemoryScope, MemorySource, MemoryStore, SessionEvent, SessionId, UserId,
};
use serde_json::json;

struct MemoryCapturingCommander {
    captured_memory_fact: Arc<Mutex<Option<String>>>,
}

#[async_trait]
impl Cognition for MemoryCapturingCommander {
    async fn evaluate(&self, input: CognitionInput) -> CognitionResult {
        let memory_fact = input
            .context
            .facts
            .iter()
            .find(|fact| fact.source == "memory.user_preference")
            .map(|fact| fact.content.clone());
        *self
            .captured_memory_fact
            .lock()
            .expect("lock memory fact capture") = memory_fact;

        CognitionResult::Success(json!({
            "decision": {
                "kind": "RouteTask",
                "route": {
                    "target_agent_id": "worker.ops",
                    "task_summary": "verify memory",
                    "goal": "verify memory injection",
                    "constraints": []
                },
                "handoff": {
                    "why_this_agent": "ops can verify memory",
                    "expected_output": "done"
                },
                "clarification": {
                    "question": ""
                }
            },
            "confidence": 1.0,
            "rationale": {
                "primary": "memory injection test",
                "evidence": [],
                "alternatives_considered": []
            }
        }))
    }
}

struct NoopWorker;

#[async_trait]
impl Cognition for NoopWorker {
    async fn evaluate(&self, _input: CognitionInput) -> CognitionResult {
        CognitionResult::Success(json!({
            "decision": {
                "kind": "NoAction"
            },
            "role_output": {
                "summary": "memory worker done",
                "findings": ["memory path completed"],
                "recommendations": ["keep typed memory contract"],
                "evidence": ["memory.user_preference"],
                "risks": [],
                "open_questions": []
            }
        }))
    }
}

#[test]
fn in_memory_store_filters_by_scope_kind_and_limit() {
    let store = InMemoryMemoryStore::new();
    let scope = MemoryScope {
        user_id: Some(UserId("user-1".to_string())),
        workspace_id: None,
        agent_id: Some(AgentId("agent-1".to_string())),
        session_id: None,
        task_id: None,
    };

    MemoryStore::upsert_record(
        &store,
        MemoryRecord {
            id: MemoryId("mem-1".to_string()),
            scope: MemoryScope {
                agent_id: None,
                ..scope.clone()
            },
            kind: MemoryKind::UserPreference,
            key: Some("platform".to_string()),
            content: "用户常看抖音和小红书".to_string(),
            confidence: 0.9,
            tags: vec!["profile".to_string()],
            source: MemorySource {
                kind: "test".to_string(),
                description: "seed memory".to_string(),
            },
            created_at: SystemTime::now(),
            updated_at: SystemTime::now(),
        },
    );
    MemoryStore::upsert_record(
        &store,
        MemoryRecord {
            id: MemoryId("mem-2".to_string()),
            scope,
            kind: MemoryKind::RoutingHint,
            key: Some("route".to_string()),
            content: "直播脚本应该路由到 creative".to_string(),
            confidence: 0.8,
            tags: vec!["routing".to_string()],
            source: MemorySource {
                kind: "test".to_string(),
                description: "seed memory".to_string(),
            },
            created_at: SystemTime::now(),
            updated_at: SystemTime::now(),
        },
    );

    let mut query = MemoryQuery::scoped(MemoryScope {
        user_id: Some(UserId("user-1".to_string())),
        workspace_id: None,
        agent_id: Some(AgentId("agent-1".to_string())),
        session_id: None,
        task_id: None,
    });
    query.kinds = vec![MemoryKind::UserPreference];
    query.limit = 1;

    let bundle = MemoryStore::load_bundle(&store, query);

    assert_eq!(bundle.records.len(), 1);
    assert_eq!(bundle.records[0].kind, MemoryKind::UserPreference);
    assert!(bundle.records[0].content.contains("抖音"));
}

#[tokio::test]
async fn manager_loads_memory_bundle_into_commander_context() {
    let store = Arc::new(InMemoryMemoryStore::new());
    MemoryStore::upsert_record(
        store.as_ref(),
        MemoryRecord {
            id: MemoryId("mem-preference".to_string()),
            scope: MemoryScope {
                user_id: Some(UserId("memory-user".to_string())),
                workspace_id: None,
                agent_id: Some(AgentId("memory-agent".to_string())),
                session_id: None,
                task_id: None,
            },
            kind: MemoryKind::UserPreference,
            key: Some("platforms".to_string()),
            content: "用户偏好先看抖音 GMV，再看小红书内容效率".to_string(),
            confidence: 0.92,
            tags: vec!["profile".to_string()],
            source: MemorySource {
                kind: "test".to_string(),
                description: "seed memory".to_string(),
            },
            created_at: SystemTime::now(),
            updated_at: SystemTime::now(),
        },
    );

    let captured_memory_fact = Arc::new(Mutex::new(None));
    let commander_capture = Arc::clone(&captured_memory_fact);
    let mut config = AgentManagerConfig::new(
        Arc::new(move || {
            Ok(Box::new(MemoryCapturingCommander {
                captured_memory_fact: Arc::clone(&commander_capture),
            }))
        }),
        Arc::new(|| Ok(Box::new(NoopWorker))),
    );
    config.memory_store = store.clone();
    let manager = AgentManager::new(config);

    manager
        .run_request(AgentRequest {
            user_id: Some(UserId("memory-user".to_string())),
            workspace_id: None,
            agent_id: AgentId("memory-agent".to_string()),
            session_id: SessionId("memory-session".to_string()),
            input: "验证记忆注入".to_string(),
        })
        .await
        .expect("request should complete");

    assert_eq!(
        captured_memory_fact
            .lock()
            .expect("lock memory fact capture")
            .as_deref(),
        Some("用户偏好先看抖音 GMV，再看小红书内容效率")
    );
    assert!(
        store
            .records()
            .iter()
            .any(|record| record.kind == MemoryKind::SessionSummary)
    );
}

#[tokio::test]
async fn stream_reports_memory_load_and_persist_events() {
    let mut config = AgentManagerConfig::new(
        Arc::new(|| {
            Ok(Box::new(MemoryCapturingCommander {
                captured_memory_fact: Arc::new(Mutex::new(None)),
            }))
        }),
        Arc::new(|| Ok(Box::new(NoopWorker))),
    );
    config.memory_store = Arc::new(InMemoryMemoryStore::new());
    let manager = AgentManager::new(config);

    let mut rx = manager
        .run_stream(AgentRequest {
            user_id: Some(UserId("memory-user".to_string())),
            workspace_id: None,
            agent_id: AgentId("memory-agent".to_string()),
            session_id: SessionId("memory-stream-session".to_string()),
            input: "验证记忆事件".to_string(),
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
            .any(|event| matches!(event, SessionEvent::MemoryLoaded { .. }))
    );
    assert!(
        events
            .iter()
            .any(|event| matches!(event, SessionEvent::MemoryPersisted { .. }))
    );
}
