use agent_core::protocol::AgentId;

#[derive(Debug, Clone)]
pub struct Task {
    pub id: String,
    pub description: String,
    pub requester: AgentId,
}
