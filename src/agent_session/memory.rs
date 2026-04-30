use std::{
    cmp::Ordering,
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

    fn persist_session(
        &self,
        _scope: &MemoryScope,
        _snapshot: SessionSnapshot,
    ) -> MemoryWriteOutcome {
        MemoryWriteOutcome::default()
    }

    fn upsert_record(&self, _record: MemoryRecord) -> MemoryWriteOutcome {
        MemoryWriteOutcome::default()
    }
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

#[derive(Debug, Clone, PartialEq)]
pub struct MemoryCompactionPolicy {
    pub max_record_chars: usize,
    pub max_records_per_scope_kind: usize,
    pub max_unkeyed_records_per_scope_kind: usize,
    pub max_bundle_records: usize,
    pub max_bundle_chars: usize,
    pub min_confidence_to_keep_when_pruning: f32,
}

impl Default for MemoryCompactionPolicy {
    fn default() -> Self {
        Self {
            max_record_chars: 1_200,
            max_records_per_scope_kind: 32,
            max_unkeyed_records_per_scope_kind: 8,
            max_bundle_records: 12,
            max_bundle_chars: 2_400,
            min_confidence_to_keep_when_pruning: 0.4,
        }
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct MemoryCompactionStats {
    pub input_records: usize,
    pub output_records: usize,
    pub truncated_records: usize,
    pub merged_records: usize,
    pub dropped_records: usize,
    pub before_chars: usize,
    pub after_chars: usize,
}

impl MemoryCompactionStats {
    pub fn add(&mut self, other: MemoryCompactionStats) {
        self.input_records += other.input_records;
        self.output_records += other.output_records;
        self.truncated_records += other.truncated_records;
        self.merged_records += other.merged_records;
        self.dropped_records += other.dropped_records;
        self.before_chars += other.before_chars;
        self.after_chars += other.after_chars;
    }

    pub fn has_activity(&self) -> bool {
        self.input_records > 0
            || self.output_records > 0
            || self.truncated_records > 0
            || self.merged_records > 0
            || self.dropped_records > 0
            || self.before_chars > 0
            || self.after_chars > 0
    }
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct MemoryWriteOutcome {
    pub written: usize,
    pub compaction: MemoryCompactionStats,
}

impl MemoryWriteOutcome {
    pub fn add(&mut self, other: MemoryWriteOutcome) {
        self.written += other.written;
        self.compaction.add(other.compaction);
    }
}

pub trait MemoryCompactor: Send + Sync {
    fn compact_record(
        &self,
        record: MemoryRecord,
        policy: &MemoryCompactionPolicy,
    ) -> (Option<MemoryRecord>, MemoryCompactionStats);

    fn compact_scope_kind(
        &self,
        records: Vec<MemoryRecord>,
        policy: &MemoryCompactionPolicy,
    ) -> (Vec<MemoryRecord>, MemoryCompactionStats);
}

#[derive(Debug, Clone, Default)]
pub struct DeterministicMemoryCompactor;

impl MemoryCompactor for DeterministicMemoryCompactor {
    fn compact_record(
        &self,
        mut record: MemoryRecord,
        policy: &MemoryCompactionPolicy,
    ) -> (Option<MemoryRecord>, MemoryCompactionStats) {
        let before_chars = char_count(&record.content);
        let mut stats = MemoryCompactionStats {
            input_records: 1,
            before_chars,
            ..MemoryCompactionStats::default()
        };

        record.content = normalize_whitespace(&record.content);
        record.confidence = record.confidence.clamp(0.0, 1.0);
        record.tags = dedup_sorted_strings(record.tags);

        if record.content.is_empty() {
            stats.dropped_records = 1;
            return (None, stats);
        }

        if char_count(&record.content) > policy.max_record_chars {
            record.content = truncate_head_tail(&record.content, policy.max_record_chars);
            stats.truncated_records = 1;
        }

        stats.output_records = 1;
        stats.after_chars = char_count(&record.content);
        (Some(record), stats)
    }

    fn compact_scope_kind(
        &self,
        mut records: Vec<MemoryRecord>,
        policy: &MemoryCompactionPolicy,
    ) -> (Vec<MemoryRecord>, MemoryCompactionStats) {
        let before_chars = records
            .iter()
            .map(|record| char_count(&record.content))
            .sum();
        let input_records = records.len();

        records.sort_by(compare_memory_retention_priority);

        let mut kept = Vec::new();
        let mut unkeyed_count = 0usize;
        for record in records {
            if kept.len() >= policy.max_records_per_scope_kind {
                continue;
            }

            let is_keyed = record.key.is_some();
            if !is_keyed {
                if unkeyed_count >= policy.max_unkeyed_records_per_scope_kind {
                    continue;
                }
                unkeyed_count += 1;
            }
            kept.push(record);
        }

        let after_chars = kept.iter().map(|record| char_count(&record.content)).sum();
        let output_records = kept.len();
        let dropped_records = input_records.saturating_sub(output_records);
        (
            kept,
            MemoryCompactionStats {
                input_records,
                output_records,
                dropped_records,
                before_chars,
                after_chars,
                ..MemoryCompactionStats::default()
            },
        )
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MemoryBudget {
    pub max_records: usize,
    pub max_chars: usize,
}

impl Default for MemoryBudget {
    fn default() -> Self {
        let policy = MemoryCompactionPolicy::default();
        Self {
            max_records: policy.max_bundle_records,
            max_chars: policy.max_bundle_chars,
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
    policy: MemoryCompactionPolicy,
    compactor: DeterministicMemoryCompactor,
}

impl InMemoryMemoryStore {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn with_policy(policy: MemoryCompactionPolicy) -> Self {
        Self {
            records: RwLock::new(Vec::new()),
            policy,
            compactor: DeterministicMemoryCompactor,
        }
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
                let chars = char_count(&record.content);
                if chars > char_budget {
                    record.content = truncate_chars(&record.content, char_budget);
                }
                char_budget = char_budget.saturating_sub(char_count(&record.content));
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

    fn persist_session(
        &self,
        scope: &MemoryScope,
        snapshot: SessionSnapshot,
    ) -> MemoryWriteOutcome {
        if snapshot.summary.trim().is_empty() && snapshot.final_output.trim().is_empty() {
            return MemoryWriteOutcome::default();
        }

        let mut outcome = MemoryWriteOutcome::default();
        let content = if snapshot.summary.trim().is_empty() {
            snapshot.final_output
        } else {
            snapshot.summary
        };

        outcome.add(self.upsert_record(MemoryRecord {
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
        }));

        for (key, value) in snapshot.blackboard {
            if value.trim().is_empty() {
                continue;
            }
            outcome.add(self.upsert_record(MemoryRecord {
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
            }));
        }
        outcome
    }

    fn upsert_record(&self, record: MemoryRecord) -> MemoryWriteOutcome {
        let (record, record_stats) = match self.compactor.compact_record(record, &self.policy) {
            (Some(record), stats) => (record, stats),
            (None, stats) => {
                return MemoryWriteOutcome {
                    written: 0,
                    compaction: stats,
                };
            }
        };

        let scope = record.scope.clone();
        let kind = record.kind.clone();
        let mut outcome = MemoryWriteOutcome {
            written: 1,
            compaction: record_stats,
        };

        let mut records = self
            .records
            .write()
            .expect("memory store lock poisoned when upserting record");

        if let Some(key) = &record.key
            && let Some(existing) = records.iter_mut().find(|existing| {
                scope_identity_eq(&existing.scope, &record.scope)
                    && existing.kind == record.kind
                    && existing.key.as_ref() == Some(key)
            })
        {
            let original_id = existing.id.clone();
            let created_at = existing.created_at;
            existing.content = record.content;
            existing.confidence = record.confidence;
            existing.tags = record.tags;
            existing.source = record.source;
            existing.id = original_id;
            existing.created_at = created_at;
            existing.updated_at = SystemTime::now();
            outcome.compaction.merged_records += 1;
        } else {
            records.push(record);
        }

        let group = records
            .iter()
            .filter(|record| scope_identity_eq(&record.scope, &scope) && record.kind == kind)
            .cloned()
            .collect::<Vec<_>>();
        let (compacted_group, group_stats) = self.compactor.compact_scope_kind(group, &self.policy);
        outcome.compaction.add(group_stats);
        records.retain(|record| !(scope_identity_eq(&record.scope, &scope) && record.kind == kind));
        records.extend(compacted_group);
        outcome
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

fn scope_identity_eq(left: &MemoryScope, right: &MemoryScope) -> bool {
    left == right
}

fn compare_memory_retention_priority(left: &MemoryRecord, right: &MemoryRecord) -> Ordering {
    let left_keyed = left.key.is_some();
    let right_keyed = right.key.is_some();
    right_keyed
        .cmp(&left_keyed)
        .then_with(|| {
            right
                .confidence
                .partial_cmp(&left.confidence)
                .unwrap_or(Ordering::Equal)
        })
        .then_with(|| {
            right
                .updated_at
                .duration_since(left.updated_at)
                .map(|_| Ordering::Greater)
                .unwrap_or(Ordering::Less)
        })
        .then_with(|| char_count(&left.content).cmp(&char_count(&right.content)))
}

fn normalize_whitespace(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn dedup_sorted_strings(mut values: Vec<String>) -> Vec<String> {
    values.retain(|value| !value.trim().is_empty());
    for value in &mut values {
        *value = value.trim().to_string();
    }
    values.sort();
    values.dedup();
    values
}

fn char_count(value: &str) -> usize {
    value.chars().count()
}

fn truncate_chars(value: &str, max_chars: usize) -> String {
    value.chars().take(max_chars).collect()
}

fn truncate_head_tail(value: &str, max_chars: usize) -> String {
    const MARKER: &str = "\n\n[...memory compacted...]\n\n";
    if max_chars == 0 {
        return String::new();
    }
    if max_chars <= char_count(MARKER) + 2 {
        return truncate_chars(value, max_chars);
    }

    let available = max_chars - char_count(MARKER);
    let head_chars = available / 2;
    let tail_chars = available - head_chars;
    let head = value.chars().take(head_chars).collect::<String>();
    let tail = value
        .chars()
        .rev()
        .take(tail_chars)
        .collect::<Vec<_>>()
        .into_iter()
        .rev()
        .collect::<String>();
    format!("{head}{MARKER}{tail}")
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
