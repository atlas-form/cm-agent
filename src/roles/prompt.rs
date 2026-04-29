use crate::roles::RoleProfile;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RolePromptInput {
    pub role: RoleProfile,
    pub task: String,
    pub facts: Vec<String>,
}

pub struct RolePromptBuilder;

impl RolePromptBuilder {
    pub fn build_worker_prompt(input: &RolePromptInput) -> String {
        let capabilities = join_or_none(&input.role.required_capabilities);
        let optional_capabilities = join_or_none(&input.role.optional_capabilities);
        let actions = input
            .role
            .preferred_actions
            .iter()
            .map(|action| action.as_str())
            .collect::<Vec<_>>()
            .join(", ");
        let facts = join_or_none(&input.facts);

        format!(
            "你是{role_name}。\n角色ID：{runtime_role}\n核心能力：{capabilities}\n可辅助能力：\
             {optional_capabilities}\n偏好动作：{actions}\n当前任务：{task}\n已知事实：{facts}\n\\
             n要求：\n- 只在本角色能力范围内判断下一步。\n- 不编造用户没有提供的数据。\n- \
             优先给出可执行、可验证的下一步。\n- 如果没有必要动作，返回 NoAction。",
            role_name = input.role.name,
            runtime_role = input.role.runtime_role,
            capabilities = capabilities,
            optional_capabilities = optional_capabilities,
            actions = if actions.is_empty() { "none" } else { &actions },
            task = input.task,
            facts = facts,
        )
    }
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
        assert!(prompt.contains("data"));
        assert!(prompt.contains("分析转化率下降"));
    }
}
