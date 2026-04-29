use crate::protocol::{AgentId, MessageId, Payload, SessionId, TaskId, UserId, WorkspaceId};

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct MessageContext {
    pub user_id: Option<UserId>,
    pub workspace_id: Option<WorkspaceId>,
    pub agent_id: Option<AgentId>,
    pub session_id: Option<SessionId>,
    pub task_id: Option<TaskId>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Message {
    pub id: MessageId,
    pub context: MessageContext,
    pub from: AgentId,
    pub to: AgentId,
    pub payload: Payload,
}

impl Message {
    pub fn new(id: MessageId, from: AgentId, to: AgentId, payload: Payload) -> Self {
        Self {
            id,
            context: MessageContext::default(),
            from,
            to,
            payload,
        }
    }

    pub fn new_with_context(
        id: MessageId,
        context: MessageContext,
        from: AgentId,
        to: AgentId,
        payload: Payload,
    ) -> Self {
        Self {
            id,
            context,
            from,
            to,
            payload,
        }
    }
}
