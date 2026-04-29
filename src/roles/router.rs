use serde::{Deserialize, Serialize};

use crate::roles::{RoleAction, RoleCatalog, RoleId, RoleProfile};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RoleRouteInput {
    pub message: String,
    pub domain_id: Option<String>,
    pub action: Option<RoleAction>,
    pub max_roles: usize,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RoleScore {
    pub role_id: RoleId,
    pub runtime_role: String,
    pub score: f32,
    pub keyword_hits: usize,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct RoleRoute {
    pub primary_role: RoleId,
    pub primary_runtime_role: String,
    pub support_roles: Vec<RoleId>,
    pub support_runtime_roles: Vec<String>,
    pub scores: Vec<RoleScore>,
}

#[derive(Debug, Clone)]
pub struct RoleRouter {
    catalog: RoleCatalog,
}

impl RoleRouter {
    pub fn new(catalog: RoleCatalog) -> Self {
        Self { catalog }
    }

    pub fn route(&self, input: RoleRouteInput) -> RoleRoute {
        let max_roles = input.max_roles.max(1);
        let message = input.message.to_lowercase();
        let domain_id = input
            .domain_id
            .as_deref()
            .unwrap_or("domain.general")
            .to_string();

        let mut scored = self
            .catalog
            .roles()
            .iter()
            .map(|role| score_role(role, &message, &domain_id, input.action.as_ref()))
            .collect::<Vec<_>>();

        scored.sort_by(|a, b| {
            b.score
                .total_cmp(&a.score)
                .then_with(|| b.keyword_hits.cmp(&a.keyword_hits))
                .then_with(|| {
                    role_priority(&self.catalog, &a.role_id)
                        .cmp(&role_priority(&self.catalog, &b.role_id))
                })
        });

        let fallback = self
            .catalog
            .roles()
            .iter()
            .find(|role| role.runtime_role == "chat")
            .or_else(|| {
                self.catalog
                    .roles()
                    .iter()
                    .find(|role| role.runtime_role == "ops")
            })
            .or_else(|| self.catalog.roles().first())
            .expect("role catalog should not be empty");

        let primary = scored
            .first()
            .filter(|score| score.score > 0.0)
            .cloned()
            .unwrap_or_else(|| RoleScore {
                role_id: fallback.id.clone(),
                runtime_role: fallback.runtime_role.clone(),
                score: 0.0,
                keyword_hits: 0,
            });

        let support_roles = scored
            .iter()
            .filter(|score| score.role_id != primary.role_id && score.score > 0.0)
            .take(max_roles.saturating_sub(1))
            .map(|score| score.role_id.clone())
            .collect::<Vec<_>>();
        let support_runtime_roles = scored
            .iter()
            .filter(|score| score.role_id != primary.role_id && score.score > 0.0)
            .take(max_roles.saturating_sub(1))
            .map(|score| score.runtime_role.clone())
            .collect::<Vec<_>>();

        RoleRoute {
            primary_role: primary.role_id.clone(),
            primary_runtime_role: primary.runtime_role.clone(),
            support_roles,
            support_runtime_roles,
            scores: scored,
        }
    }
}

impl Default for RoleRouter {
    fn default() -> Self {
        Self::new(RoleCatalog::builtin())
    }
}

fn score_role(
    role: &RoleProfile,
    message: &str,
    domain_id: &str,
    action: Option<&RoleAction>,
) -> RoleScore {
    let keyword_hits = role
        .keywords
        .iter()
        .filter(|keyword| message.contains(&keyword.to_lowercase()))
        .count();
    let keyword_score = keyword_hits as f32 * 2.0;
    let action_score = action
        .filter(|action| role.preferred_actions.iter().any(|item| item == *action))
        .map(|_| 1.0)
        .unwrap_or(0.0);
    let signal_score = keyword_score + action_score;
    let domain_score =
        if signal_score > 0.0 && role.domains.iter().any(|domain| domain == domain_id) {
            0.5
        } else {
            0.0
        };

    RoleScore {
        role_id: role.id.clone(),
        runtime_role: role.runtime_role.clone(),
        score: signal_score + domain_score,
        keyword_hits,
    }
}

fn role_priority(catalog: &RoleCatalog, role_id: &RoleId) -> u32 {
    catalog
        .roles()
        .iter()
        .find(|role| &role.id == role_id)
        .map(|role| role.priority)
        .unwrap_or(u32::MAX)
}

#[cfg(test)]
mod tests {
    use super::{RoleRouteInput, RoleRouter};
    use crate::roles::RoleAction;

    #[test]
    fn routes_data_request_to_data_role() {
        let route = RoleRouter::default().route(RoleRouteInput {
            message: "帮我分析漏斗指标和转化率下降原因".to_string(),
            domain_id: Some("domain.ecommerce".to_string()),
            action: Some(RoleAction::Analyze),
            max_roles: 3,
        });

        assert_eq!(route.primary_runtime_role, "data");
        assert!(route.support_roles.len() <= 2);
    }

    #[test]
    fn routes_copy_request_to_creative_role() {
        let route = RoleRouter::default().route(RoleRouteInput {
            message: "帮我写一版直播脚本文案和标题".to_string(),
            domain_id: None,
            action: Some(RoleAction::Create),
            max_roles: 2,
        });

        assert_eq!(route.primary_runtime_role, "creative");
    }

    #[test]
    fn falls_back_to_chat_for_empty_message() {
        let route = RoleRouter::default().route(RoleRouteInput {
            message: "".to_string(),
            domain_id: None,
            action: None,
            max_roles: 3,
        });

        assert_eq!(route.primary_runtime_role, "chat");
    }

    #[test]
    fn routes_question_to_chat_role() {
        let route = RoleRouter::default().route(RoleRouteInput {
            message: "你能解释一下这个 agent 是什么吗".to_string(),
            domain_id: None,
            action: None,
            max_roles: 3,
        });

        assert_eq!(route.primary_runtime_role, "chat");
    }
}
