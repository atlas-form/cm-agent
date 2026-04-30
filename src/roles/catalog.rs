use std::{collections::HashSet, env, fs, path::Path};

use serde::{Deserialize, Serialize};

use crate::{core::protocol::WorkerProfile, roles::RoleProfile};

const DEFAULT_ROLES_PATH: &str = "config/roles.json";
const ROLES_CONFIG_ENV: &str = "CM_AGENT_ROLES";

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RoleCatalog {
    roles: Vec<RoleProfile>,
}

impl RoleCatalog {
    pub fn builtin() -> Self {
        Self {
            roles: parse_configured_roles(include_str!("../../config/roles-default.json"))
                .expect("embedded config/roles-default.json must be valid"),
        }
    }

    pub fn configured() -> Self {
        let path = env::var(ROLES_CONFIG_ENV)
            .ok()
            .filter(|path| !path.trim().is_empty())
            .unwrap_or_else(|| DEFAULT_ROLES_PATH.to_string());
        if !Path::new(&path).exists() {
            return Self::builtin();
        }

        match load_configured_roles(&path) {
            Ok(roles) => Self { roles },
            Err(error) => {
                crate::log_error_msg!(&format!(
                    "configured roles ignored: path={path}, error={error}"
                ));
                Self::builtin()
            }
        }
    }

    pub fn new(roles: Vec<RoleProfile>) -> Self {
        Self { roles }
    }

    pub fn roles(&self) -> &[RoleProfile] {
        &self.roles
    }

    pub fn to_worker_profiles(&self) -> Vec<WorkerProfile> {
        self.roles
            .iter()
            .map(RoleProfile::to_worker_profile)
            .collect()
    }
}

impl Default for RoleCatalog {
    fn default() -> Self {
        Self::builtin()
    }
}

#[derive(Debug, Clone, Deserialize)]
struct ConfiguredRoleSet {
    #[serde(default)]
    roles: Vec<RoleProfile>,
}

fn load_configured_roles(path: impl AsRef<Path>) -> Result<Vec<RoleProfile>, String> {
    let content = fs::read_to_string(path.as_ref()).map_err(|error| error.to_string())?;
    parse_configured_roles(&content)
}

fn parse_configured_roles(content: &str) -> Result<Vec<RoleProfile>, String> {
    let configured = serde_json::from_str::<ConfiguredRoleSet>(content)
        .map_err(|error| format!("invalid roles json: {error}"))?;
    validate_configured_roles(&configured.roles)?;
    Ok(configured.roles)
}

fn validate_configured_roles(configured: &[RoleProfile]) -> Result<(), String> {
    if configured.is_empty() {
        return Err("roles config must declare at least one role".to_string());
    }
    let mut role_ids = HashSet::new();
    let mut runtime_roles = HashSet::new();

    for role in configured {
        if role.id.0.trim().is_empty() {
            return Err("role id is required".to_string());
        }
        if !role_ids.insert(role.id.0.clone()) {
            return Err(format!("duplicate role id '{}'", role.id.0));
        }
        if role.name.trim().is_empty() {
            return Err(format!("role '{}' must declare name", role.id.0));
        }
        if role.runtime_role.trim().is_empty() {
            return Err(format!("role '{}' must declare runtime_role", role.id.0));
        }
        if !role
            .runtime_role
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || ch == '_' || ch == '-')
        {
            return Err(format!(
                "role '{}' runtime_role '{}' contains unsupported characters",
                role.id.0, role.runtime_role
            ));
        }
        if !runtime_roles.insert(role.runtime_role.clone()) {
            return Err(format!("duplicate runtime_role '{}'", role.runtime_role));
        }
        if role.keywords.is_empty() {
            return Err(format!(
                "role '{}' should declare at least one keyword",
                role.id.0
            ));
        }
        if role.preferred_actions.is_empty() {
            return Err(format!(
                "role '{}' should declare at least one preferred action",
                role.id.0
            ));
        }
    }

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{RoleCatalog, parse_configured_roles};

    #[test]
    fn builtin_catalog_contains_expected_roles() {
        let catalog = RoleCatalog::builtin();
        let roles = catalog.roles();

        assert_eq!(roles.len(), 9);
        assert!(roles.iter().any(|role| role.runtime_role == "chat"));
        assert!(roles.iter().any(|role| role.runtime_role == "ops"));
        assert!(roles.iter().any(|role| role.runtime_role == "data"));
        assert!(roles.iter().any(|role| role.runtime_role == "web"));
    }

    #[test]
    fn builtin_catalog_exports_worker_profiles() {
        let profiles = RoleCatalog::builtin().to_worker_profiles();

        assert_eq!(profiles.len(), 9);
        assert!(
            profiles
                .iter()
                .any(|profile| profile.worker_id.0 == "worker.chat")
        );
        assert!(
            profiles
                .iter()
                .any(|profile| profile.worker_id.0 == "worker.ops")
        );
    }

    #[test]
    fn configured_roles_can_define_full_catalog() {
        let configured = parse_configured_roles(
            r#"{
              "roles": [
                {
                  "id": "role.douyin-ads",
                  "name": "千川投放专员",
                  "runtime_role": "douyin_ads",
                  "priority": 25,
                  "domains": ["domain.ecommerce"],
                  "keywords": ["千川", "投流", "roi", "cpc", "人群包"],
                  "preferred_actions": ["analyze", "optimize", "execute"],
                  "required_capabilities": ["ads.optimization", "roi.control"],
                  "optional_capabilities": ["data.analysis", "creative.content"]
                }
              ]
            }"#,
        )
        .expect("configured role should parse");

        let catalog = RoleCatalog::new(configured);

        let role = catalog
            .roles()
            .iter()
            .find(|role| role.runtime_role == "douyin_ads")
            .expect("configured role should be present");
        assert_eq!(role.name, "千川投放专员");
        assert!(
            role.required_capabilities
                .contains(&"ads.optimization".to_string())
        );
    }

    #[test]
    fn configured_roles_reject_duplicate_runtime_role() {
        let error = parse_configured_roles(
            r#"{
              "roles": [
                {
                  "id": "role.a",
                  "name": "角色A",
                  "runtime_role": "same_role",
                  "priority": 25,
                  "domains": ["domain.ecommerce"],
                  "keywords": ["A"],
                  "preferred_actions": ["plan"],
                  "required_capabilities": [],
                  "optional_capabilities": []
                },
                {
                  "id": "role.b",
                  "name": "角色B",
                  "runtime_role": "same_role",
                  "priority": 26,
                  "domains": ["domain.ecommerce"],
                  "keywords": ["B"],
                  "preferred_actions": ["plan"],
                  "required_capabilities": [],
                  "optional_capabilities": []
                }
              ]
            }"#,
        )
        .expect_err("duplicate runtime role should fail");

        assert!(error.contains("duplicate runtime_role"));
    }
}
