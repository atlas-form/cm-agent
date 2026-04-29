use serde::{Deserialize, Serialize};

use crate::{
    core::protocol::{TaskId, WorkerId},
    roles::{RoleId, RoleRoute},
};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RoleAssignment {
    pub role_id: RoleId,
    pub runtime_role: String,
    pub worker_id: WorkerId,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RoleCollaborationPlan {
    pub primary: RoleAssignment,
    pub support: Vec<RoleAssignment>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RoleContribution {
    pub role_id: RoleId,
    pub runtime_role: String,
    pub worker_id: WorkerId,
    pub task_id: TaskId,
    pub content: String,
    pub confidence: Option<u8>,
    pub needs_follow_up: bool,
}

impl RoleContribution {
    pub fn new(assignment: &RoleAssignment, task_id: TaskId, content: impl Into<String>) -> Self {
        Self {
            role_id: assignment.role_id.clone(),
            runtime_role: assignment.runtime_role.clone(),
            worker_id: assignment.worker_id.clone(),
            task_id,
            content: content.into(),
            confidence: None,
            needs_follow_up: false,
        }
    }
}

impl RoleCollaborationPlan {
    pub fn from_route(route: &RoleRoute) -> Self {
        Self {
            primary: RoleAssignment {
                role_id: route.primary_role.clone(),
                runtime_role: route.primary_runtime_role.clone(),
                worker_id: worker_id_for_runtime_role(&route.primary_runtime_role),
            },
            support: route
                .support_roles
                .iter()
                .zip(route.support_runtime_roles.iter())
                .map(|(role_id, runtime_role)| RoleAssignment {
                    role_id: role_id.clone(),
                    runtime_role: runtime_role.clone(),
                    worker_id: worker_id_for_runtime_role(runtime_role),
                })
                .collect(),
        }
    }

    pub fn assignment_for(&self, worker_id: &WorkerId) -> Option<&RoleAssignment> {
        if self.primary.worker_id == *worker_id {
            return Some(&self.primary);
        }
        self.support
            .iter()
            .find(|assignment| assignment.worker_id == *worker_id)
    }
}

pub fn worker_id_for_runtime_role(runtime_role: &str) -> WorkerId {
    WorkerId(format!("worker.{runtime_role}"))
}
