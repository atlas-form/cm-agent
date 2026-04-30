use std::{
    fs,
    path::PathBuf,
    sync::{Arc, Mutex},
    time::{Duration, SystemTime},
};

use async_trait::async_trait;
use cm_agent::api::{
    AgentId, AgentManager, AgentManagerConfig, AgentRequest, Cognition, CognitionInput,
    CognitionResult, FileMemoryStore, FileMemoryStoreError, InMemoryMemoryStore, MemoryBudget,
    MemoryCompactionPolicy, MemoryId, MemoryKind, MemoryQuery, MemoryRecord, MemoryScope,
    MemorySource, MemoryStore, RoleMemorySummary, SessionEvent, SessionId, SessionSnapshot,
    TaskGraphId, TaskGraphMemorySummary, TaskNodeId, UserId, WorkerDetail, WorkerId,
    WorkerReportStatus,
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

fn temp_memory_path(name: &str) -> PathBuf {
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT_TEMP_PATH: AtomicU64 = AtomicU64::new(1);
    let millis = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap_or(Duration::ZERO)
        .as_millis();
    let sequence = NEXT_TEMP_PATH.fetch_add(1, Ordering::Relaxed);
    std::env::temp_dir()
        .join("cm-agent-memory-tests")
        .join(format!("{name}-{millis}-{sequence}.json"))
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

#[test]
fn file_memory_store_writes_and_reloads_records() {
    let path = temp_memory_path("reload");
    let scope = MemoryScope {
        user_id: Some(UserId("file-user".to_string())),
        workspace_id: None,
        agent_id: None,
        session_id: None,
        task_id: None,
    };
    {
        let store = FileMemoryStore::open(&path).expect("open file memory store");
        MemoryStore::upsert_record(
            &store,
            test_memory_record(
                "file-record",
                scope.clone(),
                MemoryKind::UserPreference,
                Some("platform"),
                "用户偏好文件持久化 memory",
                0.9,
            ),
        );
    }

    let store = FileMemoryStore::open(&path).expect("reload file memory store");
    let mut query = MemoryQuery::scoped(scope);
    query.kinds = vec![MemoryKind::UserPreference];
    let bundle = MemoryStore::load_bundle(&store, query);

    assert_eq!(bundle.records.len(), 1);
    assert_eq!(bundle.records[0].content, "用户偏好文件持久化 memory");
    assert!(
        fs::read_to_string(&path)
            .expect("read memory file")
            .contains("\"version\"")
    );
}

#[test]
fn file_memory_store_compacts_before_flushing() {
    let path = temp_memory_path("compact");
    let store = FileMemoryStore::open_with_policy(
        &path,
        MemoryCompactionPolicy {
            max_record_chars: 48,
            ..MemoryCompactionPolicy::default()
        },
    )
    .expect("open file memory store");
    let scope = MemoryScope {
        user_id: Some(UserId("file-compact-user".to_string())),
        workspace_id: None,
        agent_id: None,
        session_id: None,
        task_id: None,
    };

    let outcome = MemoryStore::upsert_record(
        &store,
        test_memory_record(
            "file-long-record",
            scope,
            MemoryKind::ConversationFact,
            Some("long"),
            "durable memory ".repeat(20),
            0.9,
        ),
    );

    let stored = store.records();
    let persisted = fs::read_to_string(&path).expect("read memory file");
    assert_eq!(outcome.written, 1);
    assert_eq!(outcome.compaction.truncated_records, 1);
    assert_eq!(stored.len(), 1);
    assert!(stored[0].content.chars().count() <= 48);
    assert!(persisted.contains("memory compacted"));
}

#[test]
fn file_memory_store_persists_session_snapshot() {
    let path = temp_memory_path("session");
    let scope = MemoryScope {
        user_id: Some(UserId("file-session-user".to_string())),
        workspace_id: None,
        agent_id: None,
        session_id: Some(SessionId("file-session".to_string())),
        task_id: None,
    };
    {
        let store = FileMemoryStore::open(&path).expect("open file memory store");
        let outcome = MemoryStore::persist_session(
            &store,
            &scope,
            SessionSnapshot {
                summary: "本次 session 形成了文件持久化 memory".to_string(),
                final_output: "final output".to_string(),
                blackboard: [("decision".to_string(), "使用文件 store".to_string())].into(),
                ..SessionSnapshot::default()
            },
        );
        assert_eq!(outcome.written, 2);
    }

    let store = FileMemoryStore::open(&path).expect("reload file memory store");
    let bundle = MemoryStore::load_bundle(&store, MemoryQuery::scoped(scope));

    assert!(
        bundle
            .records
            .iter()
            .any(|record| record.kind == MemoryKind::SessionSummary)
    );
    assert!(
        bundle
            .records
            .iter()
            .any(|record| record.kind == MemoryKind::ConversationFact
                && record.key.as_deref() == Some("decision"))
    );
}

#[test]
fn file_memory_store_reports_corrupt_file_on_open() {
    let path = temp_memory_path("corrupt");
    fs::create_dir_all(path.parent().expect("temp path has parent")).expect("create temp dir");
    fs::write(&path, "{not valid json").expect("write corrupt memory file");

    let error = FileMemoryStore::open(&path).expect_err("corrupt file should fail");

    assert!(matches!(error, FileMemoryStoreError::Json { .. }));
}

#[test]
fn session_snapshot_persists_role_risks_questions_and_evaluation() {
    let store = InMemoryMemoryStore::new();
    let scope = MemoryScope {
        user_id: Some(UserId("snapshot-user".to_string())),
        workspace_id: None,
        agent_id: Some(AgentId("snapshot-agent".to_string())),
        session_id: Some(SessionId("snapshot-session".to_string())),
        task_id: None,
    };
    let role_summary = RoleMemorySummary {
        graph_id: TaskGraphId("graph-snapshot".to_string()),
        node_id: TaskNodeId("node-data".to_string()),
        worker_id: WorkerId("worker.data".to_string()),
        role: "data".to_string(),
        summary: "数据角色完成归因口径诊断".to_string(),
        findings: vec!["漏斗指标需要拆解".to_string()],
        recommendations: vec!["统一渠道口径".to_string()],
        evidence: vec!["原始任务".to_string()],
        risks: vec!["口径不一致会导致误判".to_string()],
        open_questions: vec!["需要确认 GMV 统计口径".to_string()],
        status: WorkerReportStatus::Completed,
    };
    let worker_detail = WorkerDetail {
        graph_id: TaskGraphId("graph-snapshot".to_string()),
        node_id: TaskNodeId("node-data".to_string()),
        task_id: cm_agent::api::TaskId("graph-snapshot:node-data".to_string()),
        worker_id: WorkerId("worker.data".to_string()),
        role: "data".to_string(),
        title: "数据诊断".to_string(),
        objective: "分析数据指标归因".to_string(),
        attempt: 1,
        status: WorkerReportStatus::Completed,
        content: "数据角色完成归因口径诊断".to_string(),
        role_output: None,
        evidence: vec!["原始任务".to_string()],
        risks: vec!["口径不一致会导致误判".to_string()],
        open_questions: vec!["需要确认 GMV 统计口径".to_string()],
        evaluation: None,
    };
    let outcome = MemoryStore::persist_session(
        &store,
        &scope,
        SessionSnapshot {
            summary: "结构化 snapshot 已完成".to_string(),
            final_output: "final".to_string(),
            task_graphs: vec![TaskGraphMemorySummary {
                graph_id: TaskGraphId("graph-snapshot".to_string()),
                root_task: "分析数据指标归因".to_string(),
                roles: vec!["data".to_string()],
                role_summaries: vec![role_summary.clone()],
                worker_details: vec![worker_detail.clone()],
                risks: role_summary.risks.clone(),
                open_questions: role_summary.open_questions.clone(),
                evaluation_summary: Some("node-data passed=true score=1.00".to_string()),
            }],
            role_summaries: vec![role_summary],
            worker_details: vec![worker_detail],
            risks: vec!["口径不一致会导致误判".to_string()],
            open_questions: vec!["需要确认 GMV 统计口径".to_string()],
            evaluation_summary: Some("node-data passed=true score=1.00".to_string()),
            ..SessionSnapshot::default()
        },
    );

    let records = store.records();
    assert!(outcome.written >= 5);
    assert!(
        records
            .iter()
            .any(|record| record.kind == MemoryKind::CrossRoleContext
                && record.content.contains("数据角色完成归因口径诊断"))
    );
    assert!(
        records
            .iter()
            .any(|record| record.tags.contains(&"risk".to_string())
                && record.content.contains("口径不一致"))
    );
    assert!(
        records
            .iter()
            .any(|record| record.tags.contains(&"open_question".to_string())
                && record.content.contains("GMV"))
    );
    assert!(
        records
            .iter()
            .any(|record| record.tags.contains(&"evaluation".to_string())
                && record.content.contains("passed=true"))
    );
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
