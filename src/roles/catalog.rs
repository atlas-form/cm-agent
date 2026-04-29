use serde::{Deserialize, Serialize};

use crate::{
    core::protocol::WorkerProfile,
    roles::{RoleAction, RoleId, RoleProfile},
};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct RoleCatalog {
    roles: Vec<RoleProfile>,
}

impl RoleCatalog {
    pub fn builtin() -> Self {
        Self {
            roles: builtin_roles(),
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

fn builtin_roles() -> Vec<RoleProfile> {
    vec![
        role(
            "role.chat-assistant",
            "对话助手",
            "chat",
            5,
            &[
                "你好",
                "hello",
                "hi",
                "问一下",
                "请问",
                "什么",
                "为什么",
                "怎么",
                "如何",
                "是否",
                "是不是",
                "能否",
                "可以吗",
                "解释",
                "说明",
                "区别",
                "概念",
                "什么意思",
                "聊聊",
            ],
            &[RoleAction::Answer, RoleAction::Query],
            &["chat.answer", "chat.clarification"],
            &["knowledge.explanation", "intent.classification"],
        ),
        role(
            "role.ops-strategist",
            "运营策略师",
            "ops",
            10,
            &[
                "运营", "增长", "策略", "活动", "投放", "推广", "转化", "复盘", "营销", "获客",
                "拉新", "促活", "留存", "复购",
            ],
            &[RoleAction::Plan, RoleAction::Optimize, RoleAction::Execute],
            &["ops.strategy", "campaign.orchestration"],
            &["data.analysis", "creative.content", "service.workflow"],
        ),
        role(
            "role.data-analyst",
            "数据分析师",
            "data",
            20,
            &[
                "数据", "分析", "报表", "指标", "预测", "漏斗", "归因", "A/B",
            ],
            &[RoleAction::Analyze, RoleAction::Query, RoleAction::Plan],
            &["data.analysis", "metrics.diagnosis"],
            &["forecast.demand", "attribution.modeling"],
        ),
        role(
            "role.customer-success",
            "客户成功专员",
            "service",
            30,
            &["客服", "售后", "投诉", "退款", "满意度", "NPS", "服务"],
            &[RoleAction::Execute, RoleAction::Optimize, RoleAction::Query],
            &["service.workflow", "service.sentiment"],
            &["nps.analysis", "refund.process"],
        ),
        role(
            "role.creative-studio",
            "内容创意师",
            "creative",
            40,
            &["文案", "创意", "视频", "脚本", "种草", "直播", "标题"],
            &[RoleAction::Create, RoleAction::Optimize],
            &["creative.content"],
            &["creative.video", "creative.copywriting", "seo.title"],
        ),
        role(
            "role.engineering-architect",
            "技术架构师",
            "engineering",
            50,
            &["技术", "架构", "接口", "性能", "SLA", "故障", "系统"],
            &[
                RoleAction::Analyze,
                RoleAction::Execute,
                RoleAction::Optimize,
            ],
            &["engineering.architecture", "engineering.reliability"],
            &["engineering.sla", "data.pipeline"],
        ),
        role(
            "role.finance-analyst",
            "财务分析师",
            "accounting",
            60,
            &["财务", "成本", "利润", "ROI", "预算", "现金流", "毛利"],
            &[RoleAction::Analyze, RoleAction::Plan, RoleAction::Query],
            &["finance.profitability", "finance.cashflow"],
            &["budget.planning", "roi.modeling"],
        ),
        role(
            "role.design-core",
            "设计师",
            "design",
            70,
            &[
                "设计",
                "视觉",
                "主图",
                "详情页",
                "海报",
                "排版",
                "版式",
                "UI",
                "UX",
            ],
            &[RoleAction::Create, RoleAction::Optimize],
            &["design.visual", "design.layout"],
            &["creative.content", "conversion.optimization"],
        ),
        role(
            "role.web-seo",
            "SEO专家",
            "web",
            80,
            &[
                "SEO",
                "关键词",
                "搜索",
                "自然流量",
                "收录",
                "排名",
                "标题优化",
            ],
            &[
                RoleAction::Analyze,
                RoleAction::Optimize,
                RoleAction::Create,
            ],
            &["seo.optimization", "web.content"],
            &["creative.copywriting", "data.analysis"],
        ),
    ]
}

#[allow(clippy::too_many_arguments)]
fn role(
    id: &str,
    name: &str,
    runtime_role: &str,
    priority: u32,
    keywords: &[&str],
    preferred_actions: &[RoleAction],
    required_capabilities: &[&str],
    optional_capabilities: &[&str],
) -> RoleProfile {
    RoleProfile {
        id: RoleId(id.to_string()),
        name: name.to_string(),
        runtime_role: runtime_role.to_string(),
        priority,
        domains: vec![
            "domain.general".to_string(),
            "domain.ecommerce".to_string(),
            "domain.education".to_string(),
        ],
        keywords: keywords.iter().map(|value| value.to_string()).collect(),
        preferred_actions: preferred_actions.to_vec(),
        required_capabilities: required_capabilities
            .iter()
            .map(|value| value.to_string())
            .collect(),
        optional_capabilities: optional_capabilities
            .iter()
            .map(|value| value.to_string())
            .collect(),
    }
}

#[cfg(test)]
mod tests {
    use super::RoleCatalog;

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
}
