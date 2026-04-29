use crate::core::protocol::{AgentId, MessageContext, WorkerAssignment};

#[derive(Debug, Clone)]
pub struct Task {
    pub id: String,
    pub context: MessageContext,
    pub description: String,
    pub requester: AgentId,
    pub assignment: Option<WorkerAssignment>,
}
