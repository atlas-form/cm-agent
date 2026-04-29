use crate::protocol::{AgentId, MessageId, Payload};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Message {
    pub id: MessageId,
    pub from: AgentId,
    pub to: AgentId,
    pub payload: Payload,
}

impl Message {
    pub fn new(id: MessageId, from: AgentId, to: AgentId, payload: Payload) -> Self {
        Self {
            id,
            from,
            to,
            payload,
        }
    }
}
