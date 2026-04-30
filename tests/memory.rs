use std::{
    sync::{Arc, Mutex},
    time::SystemTime,
};

use async_trait::async_trait;
use cm_agent::api::{
    AgentId, AgentManager, AgentManagerConfig, AgentRequest, Cognition, CognitionInput,
    CognitionResult, InMemoryMemoryStore, MemoryBudget, MemoryCompactionPolicy, MemoryId,
    MemoryKind, MemoryQuery, MemoryRecord, MemoryScope, MemorySource, MemoryStore, SessionEvent,
    SessionId, UserId,
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

fn test_memory_record(
    id: impl Into<String>,
    scope: MemoryScope,
    kind: MemoryKind,
    key: Option<&str>,
    content: impl Into<String>,
    confidence: f32,
) -> MemoryRecord {
    MemoryRecord {
        id: MemoryId(id.into()),
        scope,
        kind,
        key: key.map(str::to_string),
        content: content.into(),
        confidence,
        tags: vec!["test".to_string()],
        source: MemorySource {
            kind: "test".to_string(),
            description: "seed memory".to_string(),
        },
        created_at: SystemTime::now(),
        updated_at: SystemTime::now(),
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

#[test]
fn record_is_truncated_before_write() {
    let store = InMemoryMemoryStore::with_policy(MemoryCompactionPolicy {
        max_record_chars: 40,
        ..MemoryCompactionPolicy::default()
    });
    let scope = MemoryScope {
        user_id: Some(UserId("truncate-user".to_string())),
        workspace_id: None,
        agent_id: None,
        session_id: None,
        task_id: None,
    };
    let long_content = "alpha ".repeat(40);

    let outcome = MemoryStore::upsert_record(
        &store,
        test_memory_record(
            "truncate",
            scope,
            MemoryKind::UserPreference,
            Some("long"),
            long_content,
            1.2,
        ),
    );

    let records = store.records();
    assert_eq!(outcome.written, 1);
    assert_eq!(outcome.compaction.truncated_records, 1);
    assert_eq!(records.len(), 1);
    assert!(records[0].content.chars().count() <= 40);
    assert!(records[0].content.contains("memory compacted"));
    assert_eq!(records[0].confidence, 1.0);
}

#[test]
fn empty_record_is_not_written() {
    let store = InMemoryMemoryStore::new();
    let scope = MemoryScope {
        user_id: Some(UserId("empty-user".to_string())),
        workspace_id: None,
        agent_id: None,
        session_id: None,
        task_id: None,
    };

    let outcome = MemoryStore::upsert_record(
        &store,
        test_memory_record(
            "empty",
            scope,
            MemoryKind::ConversationFact,
            Some("blank"),
            "   \n\t   ",
            0.5,
        ),
    );

    assert_eq!(outcome.written, 0);
    assert_eq!(outcome.compaction.dropped_records, 1);
    assert!(store.records().is_empty());
}

#[test]
fn same_key_upsert_does_not_grow_store() {
    let store = InMemoryMemoryStore::new();
    let scope = MemoryScope {
        user_id: Some(UserId("upsert-user".to_string())),
        workspace_id: None,
        agent_id: None,
        session_id: None,
        task_id: None,
    };

    MemoryStore::upsert_record(
        &store,
        test_memory_record(
            "first",
            scope.clone(),
            MemoryKind::UserPreference,
            Some("platform"),
            "用户偏好抖音",
            0.5,
        ),
    );
    let outcome = MemoryStore::upsert_record(
        &store,
        test_memory_record(
            "second",
            scope,
            MemoryKind::UserPreference,
            Some("platform"),
            "用户偏好小红书",
            0.8,
        ),
    );

    let records = store.records();
    assert_eq!(records.len(), 1);
    assert_eq!(records[0].id.0, "first");
    assert_eq!(records[0].content, "用户偏好小红书");
    assert_eq!(outcome.compaction.merged_records, 1);
}

#[test]
fn scope_kind_compaction_prunes_unkeyed_records() {
    let store = InMemoryMemoryStore::with_policy(MemoryCompactionPolicy {
        max_records_per_scope_kind: 4,
        max_unkeyed_records_per_scope_kind: 2,
        ..MemoryCompactionPolicy::default()
    });
    let scope = MemoryScope {
        user_id: Some(UserId("prune-user".to_string())),
        workspace_id: None,
        agent_id: None,
        session_id: None,
        task_id: None,
    };

    MemoryStore::upsert_record(
        &store,
        test_memory_record(
            "keyed-a",
            scope.clone(),
            MemoryKind::ConversationFact,
            Some("a"),
            "关键事实 A",
            0.9,
        ),
    );
    MemoryStore::upsert_record(
        &store,
        test_memory_record(
            "keyed-b",
            scope.clone(),
            MemoryKind::ConversationFact,
            Some("b"),
            "关键事实 B",
            0.8,
        ),
    );
    for index in 0 .. 10 {
        MemoryStore::upsert_record(
            &store,
            test_memory_record(
                format!("unkeyed-{index}"),
                scope.clone(),
                MemoryKind::ConversationFact,
                None,
                format!("临时事实 {index}"),
                0.3 + (index as f32 * 0.01),
            ),
        );
    }

    let records = store.records();
    let scoped_facts = records
        .iter()
        .filter(|record| record.kind == MemoryKind::ConversationFact)
        .collect::<Vec<_>>();
    assert_eq!(scoped_facts.len(), 4);
    assert_eq!(
        scoped_facts
            .iter()
            .filter(|record| record.key.is_none())
            .count(),
        2
    );
    assert!(
        scoped_facts
            .iter()
            .any(|record| record.key.as_deref() == Some("a"))
    );
    assert!(
        scoped_facts
            .iter()
            .any(|record| record.key.as_deref() == Some("b"))
    );
}

#[test]
fn load_bundle_respects_char_budget_even_after_store_compaction() {
    let store = InMemoryMemoryStore::new();
    let scope = MemoryScope {
        user_id: Some(UserId("budget-user".to_string())),
        workspace_id: None,
        agent_id: None,
        session_id: None,
        task_id: None,
    };
    for index in 0 .. 4 {
        MemoryStore::upsert_record(
            &store,
            test_memory_record(
                format!("budget-{index}"),
                scope.clone(),
                MemoryKind::UserPreference,
                Some(&format!("pref-{index}")),
                format!("偏好 {index} {}", "内容".repeat(20)),
                0.7,
            ),
        );
    }

    let mut query = MemoryQuery::scoped(scope);
    query.budget = MemoryBudget {
        max_records: 4,
        max_chars: 30,
    };

    let bundle = MemoryStore::load_bundle(&store, query);
    let total_chars = bundle
        .records
        .iter()
        .map(|record| record.content.chars().count())
        .sum::<usize>();

    assert!(total_chars <= 30);
    assert!(!bundle.records.is_empty());
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
            .any(|event| matches!(event, SessionEvent::MemoryCompacted { .. }))
    );
    assert!(
        events
            .iter()
            .any(|event| matches!(event, SessionEvent::MemoryPersisted { .. }))
    );
}
