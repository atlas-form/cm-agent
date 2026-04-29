use crate::protocol::WorkerId;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WorkerProfile {
    pub worker_id: WorkerId,
    pub agent_id: String,
    pub name: String,
    pub description: String,
    pub capabilities: Vec<String>,
    pub constraints: Vec<String>,
    pub status: String,
}

impl WorkerProfile {
    pub fn summary_line(&self) -> String {
        let capabilities = if self.capabilities.is_empty() {
            "none".to_string()
        } else {
            self.capabilities.join(", ")
        };
        let constraints = if self.constraints.is_empty() {
            "none".to_string()
        } else {
            self.constraints.join(", ")
        };

        format!(
            "worker_id={} | agent_id={} | name={} | status={} | capabilities={} | constraints={} \
             | description={}",
            self.worker_id.0,
            self.agent_id,
            self.name,
            self.status,
            capabilities,
            constraints,
            self.description
        )
    }
}
