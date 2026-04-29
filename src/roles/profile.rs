use serde::{Deserialize, Serialize};

use crate::core::protocol::{WorkerId, WorkerProfile};

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct RoleId(pub String);

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RoleAction {
    Analyze,
    Create,
    Optimize,
    Plan,
    Execute,
    Query,
}

impl RoleAction {
    pub const fn as_str(&self) -> &'static str {
        match self {
            Self::Analyze => "analyze",
            Self::Create => "create",
            Self::Optimize => "optimize",
            Self::Plan => "plan",
            Self::Execute => "execute",
            Self::Query => "query",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RoleProfile {
    pub id: RoleId,
    pub name: String,
    pub runtime_role: String,
    pub priority: u32,
    pub domains: Vec<String>,
    pub keywords: Vec<String>,
    pub preferred_actions: Vec<RoleAction>,
    pub required_capabilities: Vec<String>,
    pub optional_capabilities: Vec<String>,
}

impl RoleProfile {
    pub fn worker_id(&self) -> WorkerId {
        WorkerId(format!("worker.{}", self.runtime_role))
    }

    pub fn to_worker_profile(&self) -> WorkerProfile {
        WorkerProfile {
            worker_id: self.worker_id(),
            agent_id: format!("worker.{}", self.runtime_role),
            name: self.name.clone(),
            description: format!("{} role worker", self.name),
            capabilities: self.capabilities(),
            constraints: vec![
                "session_scoped".to_string(),
                "one_task_at_a_time".to_string(),
                "limited_to_registered_actions".to_string(),
            ],
            status: "ready".to_string(),
        }
    }

    fn capabilities(&self) -> Vec<String> {
        let mut capabilities = Vec::new();
        capabilities.push(format!("role.{}", self.runtime_role));
        capabilities.extend(self.required_capabilities.iter().cloned());
        capabilities.extend(self.optional_capabilities.iter().cloned());
        capabilities.extend(
            self.preferred_actions
                .iter()
                .map(|action| format!("action.{}", action.as_str())),
        );
        capabilities
    }
}
