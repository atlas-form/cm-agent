use serde::{Deserialize, Serialize};

use crate::{
    core::protocol::WorkerId,
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
}

pub fn worker_id_for_runtime_role(runtime_role: &str) -> WorkerId {
    WorkerId(format!("worker.{runtime_role}"))
}
