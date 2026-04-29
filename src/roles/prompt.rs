use std::path::PathBuf;

use crate::{agent_utils::prompt::Prompt, roles::RoleProfile};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RolePromptInput {
    pub role: RoleProfile,
    pub task: String,
    pub facts: Vec<String>,
}

pub struct RolePromptBuilder;

impl RolePromptBuilder {
    pub fn build_worker_system_prompt(role: &RoleProfile) -> String {
        Self::render_role_prompt(role, "", &[])
    }

    pub fn build_worker_prompt(input: &RolePromptInput) -> String {
        Self::render_role_prompt(&input.role, &input.task, &input.facts)
    }

    pub fn prompt_path_for(role: &RoleProfile) -> PathBuf {
        PathBuf::from("prompts")
            .join("zh")
            .join("roles")
            .join(format!("{}.md", safe_runtime_role(&role.runtime_role)))
    }

    fn render_role_prompt(role: &RoleProfile, task: &str, facts: &[String]) -> String {
        let prompt_path = Self::prompt_path_for(role);
        let prompt = match Prompt::load_from_repo(&prompt_path) {
            Ok(prompt) => prompt,
            Err(err) => {
                return format!(
                    "role_prompt_unavailable\npath: {}\nerror: {}",
                    prompt_path.display(),
                    err
                );
            }
        };

        prompt.render(&[
            ("role_name", role.name.clone()),
            ("runtime_role", role.runtime_role.clone()),
            (
                "required_capabilities",
                join_or_none(&role.required_capabilities),
            ),
            (
                "optional_capabilities",
                join_or_none(&role.optional_capabilities),
            ),
            ("preferred_actions", role_actions(role)),
            (
                "task",
                if task.trim().is_empty() {
                    "none".to_string()
                } else {
                    task.to_string()
                },
            ),
            ("facts", join_or_none(facts)),
        ])
    }
}

fn safe_runtime_role(runtime_role: &str) -> String {
    runtime_role
        .chars()
        .filter(|ch| ch.is_ascii_alphanumeric() || *ch == '_' || *ch == '-')
        .collect()
}

fn role_actions(role: &RoleProfile) -> String {
    let actions = role
        .preferred_actions
        .iter()
        .map(|action| action.as_str().to_string())
        .collect::<Vec<_>>();
    join_or_none(&actions)
}

fn join_or_none(values: &[String]) -> String {
    if values.is_empty() {
        "none".to_string()
    } else {
        values.join(", ")
    }
}

#[cfg(test)]
mod tests {
    use super::{RolePromptBuilder, RolePromptInput};
    use crate::roles::RoleCatalog;

    #[test]
    fn builds_prompt_with_role_identity() {
        let role = RoleCatalog::builtin()
            .roles()
            .iter()
            .find(|role| role.runtime_role == "data")
            .expect("data role exists")
            .clone();

        let prompt = RolePromptBuilder::build_worker_prompt(&RolePromptInput {
            role,
            task: "分析转化率下降".to_string(),
            facts: vec!["用户提供了转化率指标".to_string()],
        });

        assert!(prompt.contains("数据分析师"));
        assert!(prompt.contains("runtime_role: data"));
        assert!(prompt.contains("prompt_source: prompts/zh/roles/data.md"));
        assert!(prompt.contains("分析转化率下降"));
    }

    #[test]
    fn builds_system_prompt_without_task_specific_text() {
        let role = RoleCatalog::builtin()
            .roles()
            .iter()
            .find(|role| role.runtime_role == "creative")
            .expect("creative role exists")
            .clone();

        let prompt = RolePromptBuilder::build_worker_system_prompt(&role);

        assert!(prompt.contains("内容创意师"));
        assert!(prompt.contains("runtime_role: creative"));
        assert!(prompt.contains("prompt_source: prompts/zh/roles/creative.md"));
    }

    #[test]
    fn resolves_prompt_path_from_runtime_role() {
        let role = RoleCatalog::builtin()
            .roles()
            .iter()
            .find(|role| role.runtime_role == "chat")
            .expect("chat role exists")
            .clone();

        let path = RolePromptBuilder::prompt_path_for(&role);

        assert_eq!(path.to_string_lossy(), "prompts/zh/roles/chat.md");
    }
}
