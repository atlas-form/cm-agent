use std::{
    sync::RwLock,
    time::{Duration, SystemTime},
};

use super::{
    DeterministicMemoryCompactor, MemoryBundle, MemoryCompactionPolicy, MemoryId, MemoryKind,
    MemoryQuery, MemoryRecord, MemoryScope, MemorySource, MemoryStore, MemoryWriteOutcome,
    SessionSnapshot, char_count, scope_identity_eq, scope_query_matches, truncate_chars,
};
use crate::agent_session::memory::MemoryCompactor;

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

    pub fn with_records(policy: MemoryCompactionPolicy, records: Vec<MemoryRecord>) -> Self {
        let store = Self::with_policy(policy);
        for record in records {
            MemoryStore::upsert_record(&store, record);
        }
        store
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
            .filter(|record| scope_query_matches(&query.scope, &record.scope))
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
