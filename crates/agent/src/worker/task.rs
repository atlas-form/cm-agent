use agent_core::protocol::{AgentId, MessageContext};

#[derive(Debug, Clone)]
pub struct Task {
    pub id: String,
    pub context: MessageContext,
    pub description: String,
    pub requester: AgentId,
}
