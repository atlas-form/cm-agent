use std::cmp::Ordering;

use super::model::MemoryRecord;

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

pub(crate) fn char_count(value: &str) -> usize {
    value.chars().count()
}

pub(crate) fn truncate_chars(value: &str, max_chars: usize) -> String {
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
