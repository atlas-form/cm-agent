use crate::{
    roles::RoleProfile,
    skills::{SkillCategory, SkillPriority, SkillSpec, staged_specs},
};

#[derive(Debug, Clone, PartialEq)]
pub struct RoleSkillCatalog {
    specs: Vec<SkillSpec>,
}

impl RoleSkillCatalog {
    pub fn staged() -> Self {
        Self {
            specs: staged_specs(),
        }
    }

    pub fn recommended_for_role(&self, role: &RoleProfile) -> Vec<&SkillSpec> {
        let runtime_role = role.runtime_role.as_str();
        let mut specs = self
            .specs
            .iter()
            .filter(|spec| role_can_use_skill(runtime_role, spec))
            .collect::<Vec<_>>();
        specs.sort_by(|left, right| {
            skill_priority_rank(right.priority)
                .cmp(&skill_priority_rank(left.priority))
                .then_with(|| left.id.cmp(&right.id))
        });
        specs
    }

    pub fn can_role_use_skill(&self, role: &RoleProfile, skill_id: &str) -> bool {
        self.specs
            .iter()
            .find(|spec| spec.id == skill_id)
            .is_some_and(|spec| role_can_use_skill(&role.runtime_role, spec))
    }

    pub fn format_for_prompt(&self, role: &RoleProfile) -> String {
        let specs = self.recommended_for_role(role);
        if specs.is_empty() {
            return "none".to_string();
        }

        specs
            .into_iter()
            .take(8)
            .map(format_skill_line)
            .collect::<Vec<_>>()
            .join("\n")
    }
}

impl Default for RoleSkillCatalog {
    fn default() -> Self {
        Self::staged()
    }
}

pub fn role_can_use_skill(runtime_role: &str, spec: &SkillSpec) -> bool {
    if matches_role_category(runtime_role, &spec.category) {
        return true;
    }

    match runtime_role {
        "chat" => spec.id == "coordination_agent_handoff",
        "ops" => {
            spec.id == "coordination_task_orchestration"
                || spec.id == "coordination_skill_chain_planner"
                || spec.id == "coordination_create_task"
                || spec.id == "query_store_metrics"
        }
        "data" => spec.id.starts_with("search_industry_"),
        "service" => spec.id == "search_platform_policy",
        "creative" => spec.id == "web_title_seo_scorer",
        "web" => {
            spec.id == "creative_title_ctr_scorer"
                || spec.id == "search_trends"
                || spec.id == "search_competitor"
        }
        "design" => spec.id == "creative_title_ctr_scorer",
        "accounting" => spec.id == "data_channel_roi" || spec.id == "query_store_metrics",
        "engineering" => spec.id == "coordination_status_query",
        _ => false,
    }
}

fn matches_role_category(runtime_role: &str, category: &SkillCategory) -> bool {
    match (runtime_role, category) {
        ("data", SkillCategory::Data) => true,
        (_, SkillCategory::Custom(value)) => value == runtime_role,
        _ => false,
    }
}

fn format_skill_line(spec: &SkillSpec) -> String {
    let required = spec
        .inputs
        .iter()
        .filter(|input| input.required)
        .map(|input| input.name.as_str())
        .collect::<Vec<_>>();
    let optional = spec
        .inputs
        .iter()
        .filter(|input| !input.required)
        .map(|input| input.name.as_str())
        .take(4)
        .collect::<Vec<_>>();
    let mode = if spec.tags.iter().any(|tag| tag == "deferred") {
        "deferred"
    } else if spec.tags.iter().any(|tag| tag == "pending_approval")
        || spec.id.starts_with("platform_")
    {
        "pending_approval"
    } else {
        "local"
    };

    format!(
        "- `{}` mode={} required=[{}] optional=[{}]",
        spec.id,
        mode,
        if required.is_empty() {
            "none".to_string()
        } else {
            required.join(", ")
        },
        if optional.is_empty() {
            "none".to_string()
        } else {
            optional.join(", ")
        }
    )
}

fn skill_priority_rank(priority: SkillPriority) -> u8 {
    match priority {
        SkillPriority::Low => 0,
        SkillPriority::Normal => 1,
        SkillPriority::High => 2,
        SkillPriority::Critical => 3,
    }
}

#[cfg(test)]
mod tests {
    use super::RoleSkillCatalog;
    use crate::roles::RoleCatalog;

    fn role(runtime_role: &str) -> crate::roles::RoleProfile {
        RoleCatalog::builtin()
            .roles()
            .iter()
            .find(|role| role.runtime_role == runtime_role)
            .expect("role should exist")
            .clone()
    }

    #[test]
    fn exposes_role_specific_skill_lines() {
        let catalog = RoleSkillCatalog::staged();
        let prompt = catalog.format_for_prompt(&role("web"));

        assert!(prompt.contains("web_title_seo_scorer"));
        assert!(prompt.contains("web_keyword_research"));
        assert!(prompt.contains("mode="));
    }

    #[test]
    fn keeps_roles_from_requesting_unowned_skills() {
        let catalog = RoleSkillCatalog::staged();

        assert!(catalog.can_role_use_skill(&role("accounting"), "accounting_roi_calc"));
        assert!(!catalog.can_role_use_skill(&role("accounting"), "creative_video_script"));
        assert!(catalog.can_role_use_skill(&role("creative"), "web_title_seo_scorer"));
    }
}
