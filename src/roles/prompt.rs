use std::path::PathBuf;

use crate::{
    agent_utils::prompt::Prompt,
    core::protocol::TaskId,
    roles::{RoleContribution, RoleProfile},
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RolePromptInput {
    pub role: RoleProfile,
    pub task: String,
    pub facts: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FinalSynthesisPromptInput {
    pub task_id: TaskId,
    pub task: String,
    pub support_workers: String,
    pub contributions: Vec<RoleContribution>,
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

    pub fn final_synthesis_prompt_path() -> PathBuf {
        PathBuf::from("prompts")
            .join("zh")
            .join("cognition")
            .join("final_synthesis.md")
    }

    pub fn build_final_synthesis_prompt(input: &FinalSynthesisPromptInput) -> String {
        let prompt_path = Self::final_synthesis_prompt_path();
        let prompt = match Prompt::load_from_repo(&prompt_path) {
            Ok(prompt) => prompt,
            Err(err) => {
                return format!(
                    "final_synthesis_prompt_unavailable\npath: {}\nerror: {}",
                    prompt_path.display(),
                    err
                );
            }
        };

        prompt.render(&[
            ("task_id", input.task_id.0.clone()),
            (
                "task",
                if input.task.trim().is_empty() {
                    "none".to_string()
                } else {
                    input.task.clone()
                },
            ),
            ("support_workers", input.support_workers.clone()),
            ("contributions", format_contributions(&input.contributions)),
        ])
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

fn format_contributions(contributions: &[RoleContribution]) -> String {
    if contributions.is_empty() {
        return "none".to_string();
    }

    contributions
        .iter()
        .enumerate()
        .map(|(index, contribution)| {
            let label = if index == 0 { "primary" } else { "support" };
            format!(
                "- {} {} ({}) [{}]: {}",
                label,
                contribution.runtime_role,
                contribution.worker_id.0,
                contribution.task_id.0,
                contribution.content
            )
        })
        .collect::<Vec<_>>()
        .join("\n")
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
    use super::{FinalSynthesisPromptInput, RolePromptBuilder, RolePromptInput};
    use crate::{
        core::protocol::{TaskId, WorkerId},
        roles::{RoleCatalog, RoleContribution, RoleId},
    };

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

    #[test]
    fn builds_final_synthesis_prompt_from_markdown() {
        let prompt = RolePromptBuilder::build_final_synthesis_prompt(&FinalSynthesisPromptInput {
            task_id: TaskId("task-1".to_string()),
            task: "分析数据并写文案".to_string(),
            support_workers: "worker.creative".to_string(),
            contributions: vec![RoleContribution {
                role_id: RoleId("role.data-analyst".to_string()),
                runtime_role: "data".to_string(),
                worker_id: WorkerId("worker.data".to_string()),
                task_id: TaskId("task-1".to_string()),
                content: "数据贡献".to_string(),
                confidence: None,
                needs_follow_up: false,
            }],
        });

        assert!(prompt.contains("prompt_source: prompts/zh/cognition/final_synthesis.md"));
        assert!(prompt.contains("分析数据并写文案"));
        assert!(prompt.contains("数据贡献"));
    }
}
