use crate::core::protocol::{AgentId, SessionId, TaskId, UserId, WorkspaceId};

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct MemoryScope {
    pub user_id: Option<UserId>,
    pub workspace_id: Option<WorkspaceId>,
    pub agent_id: Option<AgentId>,
    pub session_id: Option<SessionId>,
    pub task_id: Option<TaskId>,
}

pub trait MemoryStore: Send + Sync {
    fn persist_session(&self, _scope: &MemoryScope, _snapshot: SessionSnapshot) {}
}

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SessionSnapshot {
    pub summary: String,
}

#[derive(Debug, Default)]
pub struct NoopMemoryStore;

impl MemoryStore for NoopMemoryStore {}
