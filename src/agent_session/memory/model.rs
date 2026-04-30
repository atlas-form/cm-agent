use std::{collections::BTreeMap, time::SystemTime};

use serde::{Deserialize, Serialize};

use super::{MemoryCompactionPolicy, MemoryWriteOutcome};
use crate::core::protocol::{
    AgentId, Evaluation, RoleWorkOutput, SessionId, TaskGraphId, TaskId, TaskNodeId, UserId,
    WorkerId, WorkerReportStatus, WorkspaceId,
};

#[derive(Debug, Clone, Default, PartialEq, Eq, Hash, Serialize, Deserialize)]
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

#[derive(Debug, Clone, Default, PartialEq)]
pub struct SessionSnapshot {
    pub summary: String,
    pub final_output: String,
    pub blackboard: BTreeMap<String, String>,
    pub task_graphs: Vec<TaskGraphMemorySummary>,
    pub role_summaries: Vec<RoleMemorySummary>,
    pub worker_details: Vec<WorkerDetail>,
    pub risks: Vec<String>,
    pub open_questions: Vec<String>,
    pub evaluation_summary: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct TaskGraphMemorySummary {
    pub graph_id: TaskGraphId,
    pub root_task: String,
    pub roles: Vec<String>,
    pub role_summaries: Vec<RoleMemorySummary>,
    pub worker_details: Vec<WorkerDetail>,
    pub risks: Vec<String>,
    pub open_questions: Vec<String>,
    pub evaluation_summary: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RoleMemorySummary {
    pub graph_id: TaskGraphId,
    pub node_id: TaskNodeId,
    pub worker_id: WorkerId,
    pub role: String,
    pub summary: String,
    pub findings: Vec<String>,
    pub recommendations: Vec<String>,
    pub evidence: Vec<String>,
    pub risks: Vec<String>,
    pub open_questions: Vec<String>,
    pub status: WorkerReportStatus,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct WorkerDetail {
    pub graph_id: TaskGraphId,
    pub node_id: TaskNodeId,
    pub task_id: TaskId,
    pub worker_id: WorkerId,
    pub role: String,
    pub title: String,
    pub objective: String,
    pub attempt: u8,
    pub status: WorkerReportStatus,
    pub content: String,
    pub role_output: Option<RoleWorkOutput>,
    pub evidence: Vec<String>,
    pub risks: Vec<String>,
    pub open_questions: Vec<String>,
    pub evaluation: Option<Evaluation>,
}

#[derive(Debug, Default)]
pub struct NoopMemoryStore;

impl MemoryStore for NoopMemoryStore {}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct MemoryId(pub String);

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
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

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
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

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
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

pub(crate) fn scope_query_matches(query: &MemoryScope, record: &MemoryScope) -> bool {
    field_matches(&query.user_id, &record.user_id)
        && field_matches(&query.workspace_id, &record.workspace_id)
        && field_matches(&query.agent_id, &record.agent_id)
        && field_matches(&query.session_id, &record.session_id)
        && field_matches(&query.task_id, &record.task_id)
}

pub(crate) fn scope_identity_eq(left: &MemoryScope, right: &MemoryScope) -> bool {
    left == right
}

fn field_matches<T: PartialEq>(query: &Option<T>, record: &Option<T>) -> bool {
    match (query, record) {
        (None, _) => true,
        (Some(_), None) => true,
        (Some(query), Some(record)) => query == record,
    }
}
