use std::{
    collections::BTreeMap,
    sync::RwLock,
    time::{Duration, SystemTime},
};

use crate::core::protocol::{AgentId, SessionId, TaskId, UserId, WorkspaceId};

#[derive(Debug, Clone, Default, PartialEq, Eq, Hash)]
pub struct MemoryScope {
    pub user_id: Option<UserId>,
    pub workspace_id: Option<WorkspaceId>,
    pub agent_id: Option<AgentId>,
    pub session_id: Option<SessionId>,
    pub task_id: Option<TaskId>,
}

pub trait MemoryStore: Send + Sync {
    fn load_bundle(&self, _query: MemoryQuery) -> MemoryBundle {
        MemoryBundle::default()
    }

    fn persist_session(&self, _scope: &MemoryScope, _snapshot: SessionSnapshot) -> usize {
        0
    }

    fn upsert_record(&self, _record: MemoryRecord) {}
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SessionSnapshot {
    pub summary: String,
    pub final_output: String,
    pub blackboard: BTreeMap<String, String>,
}

#[derive(Debug, Default)]
pub struct NoopMemoryStore;

impl MemoryStore for NoopMemoryStore {}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct MemoryId(pub String);

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum MemoryKind {
    SessionSummary,
    ConversationFact,
    UserPreference,
    WorkspaceFact,
    RoleLearning,
    RoutingHint,
    Episode,
    CrossRoleContext,
}

impl MemoryKind {
    pub const fn as_str(&self) -> &'static str {
        match self {
            Self::SessionSummary => "session_summary",
            Self::ConversationFact => "conversation_fact",
            Self::UserPreference => "user_preference",
            Self::WorkspaceFact => "workspace_fact",
            Self::RoleLearning => "role_learning",
            Self::RoutingHint => "routing_hint",
            Self::Episode => "episode",
            Self::CrossRoleContext => "cross_role_context",
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct MemorySource {
    pub kind: String,
    pub description: String,
}

impl MemorySource {
    pub fn session_result() -> Self {
        Self {
            kind: "session_result".to_string(),
            description: "Persisted from a completed agent session".to_string(),
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct MemoryRecord {
    pub id: MemoryId,
    pub scope: MemoryScope,
    pub kind: MemoryKind,
    pub key: Option<String>,
    pub content: String,
    pub confidence: f32,
    pub tags: Vec<String>,
    pub source: MemorySource,
    pub created_at: SystemTime,
    pub updated_at: SystemTime,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MemoryBudget {
    pub max_records: usize,
    pub max_chars: usize,
}

impl Default for MemoryBudget {
    fn default() -> Self {
        Self {
            max_records: 12,
            max_chars: 2_400,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MemoryQuery {
    pub scope: MemoryScope,
    pub kinds: Vec<MemoryKind>,
    pub text: Option<String>,
    pub limit: usize,
    pub budget: MemoryBudget,
}

impl MemoryQuery {
    pub fn scoped(scope: MemoryScope) -> Self {
        Self {
            scope,
            kinds: Vec::new(),
            text: None,
            limit: 12,
            budget: MemoryBudget::default(),
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct MemoryBundle {
    pub records: Vec<MemoryRecord>,
    pub summary: Option<String>,
}

impl MemoryBundle {
    pub fn kind_names(&self) -> Vec<String> {
        let mut names = self
            .records
            .iter()
            .map(|record| record.kind.as_str().to_string())
            .collect::<Vec<_>>();
        names.sort();
        names.dedup();
        names
    }
}

#[derive(Debug, Default)]
pub struct InMemoryMemoryStore {
    records: RwLock<Vec<MemoryRecord>>,
}

impl InMemoryMemoryStore {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn records(&self) -> Vec<MemoryRecord> {
        self.records
            .read()
            .expect("memory store lock poisoned when reading records")
            .clone()
    }
}

impl MemoryStore for InMemoryMemoryStore {
    fn load_bundle(&self, query: MemoryQuery) -> MemoryBundle {
        let text = query.text.as_ref().map(|value| value.to_lowercase());
        let mut char_budget = query.budget.max_chars;
        let limit = query.limit.min(query.budget.max_records);
        let records = self
            .records
            .read()
            .expect("memory store lock poisoned when loading bundle")
            .iter()
            .filter(|record| scope_matches(&query.scope, &record.scope))
            .filter(|record| query.kinds.is_empty() || query.kinds.contains(&record.kind))
            .filter(|record| {
                text.as_ref()
                    .is_none_or(|needle| record.content.to_lowercase().contains(needle))
            })
            .rev()
            .filter_map(|record| {
                if char_budget == 0 {
                    return None;
                }
                let mut record = record.clone();
                if record.content.len() > char_budget {
                    record.content.truncate(char_budget);
                }
                char_budget = char_budget.saturating_sub(record.content.len());
                Some(record)
            })
            .take(limit)
            .collect::<Vec<_>>();
        let summary = records
            .iter()
            .find(|record| record.kind == MemoryKind::SessionSummary)
            .map(|record| record.content.clone());

        MemoryBundle { records, summary }
    }

    fn persist_session(&self, scope: &MemoryScope, snapshot: SessionSnapshot) -> usize {
        if snapshot.summary.trim().is_empty() && snapshot.final_output.trim().is_empty() {
            return 0;
        }

        let mut persisted = 0;
        let content = if snapshot.summary.trim().is_empty() {
            snapshot.final_output
        } else {
            snapshot.summary
        };

        self.upsert_record(MemoryRecord {
            id: MemoryId(next_memory_id()),
            scope: scope.clone(),
            kind: MemoryKind::SessionSummary,
            key: scope.session_id.as_ref().map(|id| id.0.clone()),
            content,
            confidence: 1.0,
            tags: vec!["session".to_string()],
            source: MemorySource::session_result(),
            created_at: SystemTime::now(),
            updated_at: SystemTime::now(),
        });
        persisted += 1;

        for (key, value) in snapshot.blackboard {
            if value.trim().is_empty() {
                continue;
            }
            self.upsert_record(MemoryRecord {
                id: MemoryId(next_memory_id()),
                scope: scope.clone(),
                kind: MemoryKind::ConversationFact,
                key: Some(key),
                content: value,
                confidence: 0.8,
                tags: vec!["blackboard".to_string()],
                source: MemorySource::session_result(),
                created_at: SystemTime::now(),
                updated_at: SystemTime::now(),
            });
            persisted += 1;
        }
        persisted
    }

    fn upsert_record(&self, record: MemoryRecord) {
        let mut records = self
            .records
            .write()
            .expect("memory store lock poisoned when upserting record");

        if let Some(key) = &record.key
            && let Some(existing) = records.iter_mut().find(|existing| {
                existing.scope == record.scope
                    && existing.kind == record.kind
                    && existing.key.as_ref() == Some(key)
            })
        {
            existing.content = record.content;
            existing.confidence = record.confidence;
            existing.tags = record.tags;
            existing.source = record.source;
            existing.updated_at = SystemTime::now();
            return;
        }

        records.push(record);
    }
}

fn scope_matches(query: &MemoryScope, record: &MemoryScope) -> bool {
    field_matches(&query.user_id, &record.user_id)
        && field_matches(&query.workspace_id, &record.workspace_id)
        && field_matches(&query.agent_id, &record.agent_id)
        && field_matches(&query.session_id, &record.session_id)
        && field_matches(&query.task_id, &record.task_id)
}

fn field_matches<T: PartialEq>(query: &Option<T>, record: &Option<T>) -> bool {
    match (query, record) {
        (None, _) => true,
        (Some(_), None) => true,
        (Some(query), Some(record)) => query == record,
    }
}

fn next_memory_id() -> String {
    use std::sync::atomic::{AtomicU64, Ordering};

    static NEXT_MEMORY_ID: AtomicU64 = AtomicU64::new(1);

    let millis = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap_or(Duration::ZERO)
        .as_millis();
    let sequence = NEXT_MEMORY_ID.fetch_add(1, Ordering::Relaxed);
    format!("mem-{millis}-{sequence}")
}
