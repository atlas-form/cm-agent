use async_trait::async_trait;
use serde_json::{Value, json};

use crate::skills::{
    Skill, SkillCategory, SkillContext, SkillInputField, SkillOutcome, SkillPriority, SkillResult,
    SkillSpec, optional_string_param, string_param,
};

fn category() -> SkillCategory {
    SkillCategory::Custom("coordination".to_owned())
}

pub fn specs() -> Vec<SkillSpec> {
    vec![coordination_agent_handoff_spec()]
}

pub fn coordination_agent_handoff_spec() -> SkillSpec {
    SkillSpec::new(
        "coordination_agent_handoff",
        "Agent交接",
        "根据任务需求，确定应交接的目标Agent及交接信息",
    )
    .with_category(category())
    .with_priority(SkillPriority::Normal)
    .with_input(SkillInputField::optional(
        "current_agent",
        "当前Agent角色",
        json!({"type": "string"}),
    ))
    .with_input(SkillInputField::required(
        "task_description",
        "需交接的任务描述",
        json!({"type": "string"}),
    ))
    .with_input(SkillInputField::optional(
        "context",
        "上下文信息",
        json!({"type": "string"}),
    ))
    .with_tag("deterministic")
}

pub struct CoordinationAgentHandoff {
    spec: SkillSpec,
}

impl CoordinationAgentHandoff {
    pub fn new() -> Self {
        Self {
            spec: coordination_agent_handoff_spec(),
        }
    }
}

impl Default for CoordinationAgentHandoff {
    fn default() -> Self {
        Self::new()
    }
}

#[async_trait]
impl Skill for CoordinationAgentHandoff {
    fn spec(&self) -> &SkillSpec {
        &self.spec
    }

    async fn run(&self, params: Value, _context: SkillContext) -> SkillResult {
        let current = optional_string_param(&params, "current_agent")?
            .unwrap_or_else(|| "unknown".to_owned());
        let task = string_param(&params, "task_description")?;
        let context = optional_string_param(&params, "context")?.unwrap_or_default();
        let mut matches = agent_matches(&task)
            .into_iter()
            .filter(|(agent, _, _)| *agent != current)
            .collect::<Vec<_>>();
        matches.sort_by(|a, b| b.2.cmp(&a.2));
        if matches.is_empty() {
            matches.push(("ops".to_owned(), "运营专家".to_owned(), 0));
        }
        let target = matches[0].clone();
        let alternatives = matches
            .iter()
            .skip(1)
            .take(2)
            .map(|(agent, name, score)| json!({"agent": agent, "name": name, "匹配度": score}))
            .collect::<Vec<_>>();

        Ok(SkillOutcome::new(json!({
            "当前Agent": current,
            "任务描述": task,
            "推荐交接": {
                "目标Agent": target.0,
                "Agent名称": target.1,
                "匹配度": target.2
            },
            "备选Agent": alternatives,
            "交接信息": {
                "任务摘要": task,
                "上下文": if context.is_empty() { "无额外上下文".to_owned() } else { context },
                "优先级": "中"
            }
        }))
        .with_summary("Agent交接建议已生成"))
    }
}

fn agent_matches(task: &str) -> Vec<(String, String, i64)> {
    const AGENTS: &[(&str, &str, &[&str])] = &[
        (
            "ops",
            "运营专家",
            &[
                "运营", "促销", "活动", "推广", "投放", "选品", "定价", "库存",
            ],
        ),
        (
            "data",
            "数据分析师",
            &[
                "数据",
                "分析",
                "报表",
                "漏斗",
                "转化率",
                "趋势",
                "异常",
                "指标",
            ],
        ),
        (
            "service",
            "客服专家",
            &[
                "客服", "工单", "投诉", "退款", "退货", "售后", "DSR", "评价",
            ],
        ),
        (
            "design",
            "设计师",
            &["设计", "主图", "详情页", "配色", "海报", "视觉", "素材"],
        ),
        (
            "accounting",
            "财务分析师",
            &["财务", "成本", "利润", "预算", "ROI", "税务", "合规"],
        ),
        (
            "engineering",
            "技术工程师",
            &["技术", "架构", "bug", "性能", "部署", "开发", "接口"],
        ),
        (
            "web",
            "SEO专家",
            &["SEO", "网站", "店铺装修", "关键词", "标题", "转化", "建站"],
        ),
        (
            "creative",
            "内容创作者",
            &["视频", "文案", "种草", "直播", "IP", "内容", "创意"],
        ),
    ];

    AGENTS
        .iter()
        .copied()
    .map(|(agent, name, keywords)| {
        let score = keywords
            .iter()
            .filter(|keyword| task.contains(**keyword))
            .count() as i64;
        (agent.to_owned(), name.to_owned(), score)
    })
    .filter(|(_, _, score)| *score > 0)
    .collect()
}
