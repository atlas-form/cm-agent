use std::{
    collections::{HashMap, HashSet},
    env, fs,
    path::Path,
};

use crate::{
    agent_session::{RoleMemorySummary, TaskGraphMemorySummary, WorkerDetail},
    core::protocol::{
        Evaluation, RoleWorkOutput, TaskGraph, TaskGraphId, TaskId, TaskNode, TaskNodeId,
        WorkerAssignment, WorkerId, WorkerReport, WorkerReportStatus,
    },
    roles::{RoleCatalog, RoleRoute},
};

const DEFAULT_TASK_GRAPH_RULES_PATH: &str = "config/task-graph-rules.json";
const TASK_GRAPH_RULES_ENV: &str = "CM_AGENT_TASK_GRAPH_RULES";

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TaskNodeRuntimeState {
    Pending,
    Running,
    Passed,
    Failed(String),
    Skipped(String),
}

#[derive(Debug, Clone)]
pub struct TaskGraphRuntime {
    pub graph: TaskGraph,
    pub attempts: HashMap<TaskNodeId, u8>,
    pub states: HashMap<TaskNodeId, TaskNodeRuntimeState>,
    pub reports: HashMap<TaskNodeId, WorkerReport>,
    pub rework_instructions: HashMap<TaskNodeId, String>,
    pub evaluations: Vec<Evaluation>,
}

impl TaskGraphRuntime {
    pub fn new(graph: TaskGraph) -> Self {
        let states = graph
            .nodes
            .iter()
            .map(|node| (node.id.clone(), TaskNodeRuntimeState::Pending))
            .collect();

        Self {
            graph,
            attempts: HashMap::new(),
            states,
            reports: HashMap::new(),
            rework_instructions: HashMap::new(),
            evaluations: Vec::new(),
        }
    }

    pub fn ready_nodes(&self) -> Vec<TaskNode> {
        let busy_workers = self
            .graph
            .nodes
            .iter()
            .filter(|node| {
                matches!(
                    self.states.get(&node.id),
                    Some(TaskNodeRuntimeState::Running)
                )
            })
            .map(|node| node.worker_id.clone())
            .collect::<HashSet<_>>();
        let mut scheduled_workers = HashSet::new();

        self.graph
            .nodes
            .iter()
            .filter(|node| {
                let ready = matches!(
                    self.states.get(&node.id),
                    Some(TaskNodeRuntimeState::Pending)
                ) && node.input_refs.iter().all(|input_ref| {
                    matches!(
                        self.states.get(input_ref),
                        Some(TaskNodeRuntimeState::Passed)
                    )
                });
                if !ready || busy_workers.contains(&node.worker_id) {
                    return false;
                }
                scheduled_workers.insert(node.worker_id.clone())
            })
            .cloned()
            .collect()
    }

    pub fn build_assignment(&self, node: &TaskNode) -> WorkerAssignment {
        let attempt = self.attempts.get(&node.id).copied().unwrap_or(0) + 1;
        let inputs = node
            .input_refs
            .iter()
            .filter_map(|input_ref| self.reports.get(input_ref).cloned())
            .collect();

        WorkerAssignment {
            graph_id: self.graph.graph_id.clone(),
            node_id: node.id.clone(),
            task_id: TaskId(format!("{}:{}", self.graph.graph_id.0, node.id.0)),
            worker_id: node.worker_id.clone(),
            role: node.role.clone(),
            attempt,
            objective: node.objective.clone(),
            inputs,
            rework_instruction: self.rework_instructions.get(&node.id).cloned(),
        }
    }

    pub fn mark_running(&mut self, assignment: &WorkerAssignment) {
        self.attempts
            .insert(assignment.node_id.clone(), assignment.attempt);
        self.states
            .insert(assignment.node_id.clone(), TaskNodeRuntimeState::Running);
    }

    pub fn apply_report(&mut self, report: WorkerReport) -> EvaluationOutcome {
        let node = self
            .graph
            .nodes
            .iter()
            .find(|node| node.id == report.node_id)
            .cloned();
        let evaluation = evaluate_report(&report, node.as_ref(), &self.reports);
        self.evaluations.push(evaluation.clone());
        self.reports.insert(report.node_id.clone(), report.clone());

        let Some(node) = node else {
            self.states.insert(
                report.node_id.clone(),
                TaskNodeRuntimeState::Failed("node not found".to_string()),
            );
            return EvaluationOutcome::Failed(evaluation);
        };

        if evaluation.passed {
            self.rework_instructions.remove(&report.node_id);
            self.states
                .insert(report.node_id.clone(), TaskNodeRuntimeState::Passed);
            return EvaluationOutcome::Passed(evaluation);
        }

        let attempt = self.attempts.get(&node.id).copied().unwrap_or(1);
        if attempt < node.max_attempts {
            let instruction = evaluation
                .rework_instruction
                .clone()
                .unwrap_or_else(|| "请补齐节点产物，避免空内容和占位内容。".to_string());
            self.rework_instructions
                .insert(node.id.clone(), instruction.clone());
            self.states
                .insert(node.id.clone(), TaskNodeRuntimeState::Pending);
            return EvaluationOutcome::Rework {
                evaluation,
                instruction,
            };
        }

        let reason = evaluation.reasons.join("; ");
        self.states
            .insert(node.id.clone(), TaskNodeRuntimeState::Failed(reason));
        self.skip_blocked_descendants();
        EvaluationOutcome::Failed(evaluation)
    }

    pub fn is_finished(&self) -> bool {
        self.states.values().all(|state| {
            matches!(
                state,
                TaskNodeRuntimeState::Passed
                    | TaskNodeRuntimeState::Failed(_)
                    | TaskNodeRuntimeState::Skipped(_)
            )
        })
    }

    pub fn synthesize(&self) -> String {
        let mut lines = vec![
            "多智能体任务图已完成。".to_string(),
            format!("原始任务：{}", self.graph.root_task),
            String::new(),
            "通过的角色产物：".to_string(),
        ];

        let mut passed_count = 0;
        for node in &self.graph.nodes {
            if !matches!(
                self.states.get(&node.id),
                Some(TaskNodeRuntimeState::Passed)
            ) {
                continue;
            }
            passed_count += 1;
            let Some(report) = self.reports.get(&node.id) else {
                lines.push(format!("- {}({}): 无内容", node.title, node.role));
                continue;
            };
            if let Some(role_output) = &report.role_output {
                lines.push(format!(
                    "- {}({}): {}",
                    node.title,
                    node.role,
                    role_output.summary.trim()
                ));
                extend_synthesis_items(&mut lines, "  发现", &role_output.findings);
                extend_synthesis_items(&mut lines, "  建议", &role_output.recommendations);
                extend_synthesis_items(&mut lines, "  风险", &role_output.risks);
                extend_synthesis_items(&mut lines, "  缺口", &role_output.open_questions);
            } else {
                let content = report.content.trim();
                lines.push(format!(
                    "- {}({}): {}",
                    node.title,
                    node.role,
                    if content.is_empty() {
                        "无内容"
                    } else {
                        content
                    }
                ));
            }
        }
        if passed_count == 0 {
            lines.push("- 无。".to_string());
        }

        let gaps = self
            .graph
            .nodes
            .iter()
            .filter_map(|node| match self.states.get(&node.id) {
                Some(TaskNodeRuntimeState::Failed(reason)) => Some(format!(
                    "- {}({}) failed: {}",
                    node.title, node.role, reason
                )),
                Some(TaskNodeRuntimeState::Skipped(reason)) => Some(format!(
                    "- {}({}) skipped: {}",
                    node.title, node.role, reason
                )),
                _ => None,
            })
            .collect::<Vec<_>>();
        if !gaps.is_empty() {
            lines.push(String::new());
            lines.push("未完成节点：".to_string());
            lines.extend(gaps);
        }

        lines.join("\n")
    }

    pub fn memory_summary(&self) -> TaskGraphMemorySummary {
        let worker_details = self
            .graph
            .nodes
            .iter()
            .filter_map(|node| {
                let report = self.reports.get(&node.id)?;
                let evaluation = self
                    .evaluations
                    .iter()
                    .rev()
                    .find(|evaluation| evaluation.node_id == node.id)
                    .cloned();
                Some(WorkerDetail {
                    graph_id: report.graph_id.clone(),
                    node_id: report.node_id.clone(),
                    task_id: report.task_id.clone(),
                    worker_id: report.worker_id.clone(),
                    role: report.role.clone(),
                    title: node.title.clone(),
                    objective: node.objective.clone(),
                    attempt: self.attempts.get(&node.id).copied().unwrap_or(1),
                    status: report.status.clone(),
                    content: report.content.clone(),
                    role_output: report.role_output.clone(),
                    evidence: report.evidence.clone(),
                    risks: report.risks.clone(),
                    open_questions: report.open_questions.clone(),
                    evaluation,
                })
            })
            .collect::<Vec<_>>();
        let role_summaries = self
            .graph
            .nodes
            .iter()
            .filter_map(|node| self.reports.get(&node.id))
            .map(|report| {
                let role_output = report.role_output.clone().unwrap_or_default();
                RoleMemorySummary {
                    graph_id: report.graph_id.clone(),
                    node_id: report.node_id.clone(),
                    worker_id: report.worker_id.clone(),
                    role: report.role.clone(),
                    summary: if role_output.summary.trim().is_empty() {
                        report.content.clone()
                    } else {
                        role_output.summary
                    },
                    findings: role_output.findings,
                    recommendations: role_output.recommendations,
                    evidence: if role_output.evidence.is_empty() {
                        report.evidence.clone()
                    } else {
                        role_output.evidence
                    },
                    risks: merge_unique(role_output.risks, report.risks.clone()),
                    open_questions: merge_unique(
                        role_output.open_questions,
                        report.open_questions.clone(),
                    ),
                    status: report.status.clone(),
                }
            })
            .collect::<Vec<_>>();

        let roles = self
            .graph
            .nodes
            .iter()
            .map(|node| node.role.clone())
            .collect::<Vec<_>>();
        let evaluation_summary = (!self.evaluations.is_empty()).then(|| {
            self.evaluations
                .iter()
                .map(|evaluation| {
                    format!(
                        "{} passed={} score={:.2}: {}",
                        evaluation.node_id.0,
                        evaluation.passed,
                        evaluation.score,
                        evaluation.reasons.join("; ")
                    )
                })
                .collect::<Vec<_>>()
                .join("\n")
        });

        TaskGraphMemorySummary {
            graph_id: self.graph.graph_id.clone(),
            root_task: self.graph.root_task.clone(),
            roles,
            worker_details,
            risks: unique_strings(
                role_summaries
                    .iter()
                    .flat_map(|summary| summary.risks.clone())
                    .collect(),
            ),
            open_questions: unique_strings(
                role_summaries
                    .iter()
                    .flat_map(|summary| summary.open_questions.clone())
                    .collect(),
            ),
            role_summaries,
            evaluation_summary,
        }
    }

    fn skip_blocked_descendants(&mut self) {
        let failed = self
            .states
            .iter()
            .filter_map(|(node_id, state)| {
                matches!(state, TaskNodeRuntimeState::Failed(_)).then_some(node_id.clone())
            })
            .collect::<HashSet<_>>();

        let mut changed = true;
        while changed {
            changed = false;
            for node in &self.graph.nodes {
                if !matches!(
                    self.states.get(&node.id),
                    Some(TaskNodeRuntimeState::Pending)
                ) {
                    continue;
                }
                if node.input_refs.iter().any(|input_ref| {
                    failed.contains(input_ref)
                        || matches!(
                            self.states.get(input_ref),
                            Some(TaskNodeRuntimeState::Skipped(_))
                        )
                }) {
                    self.states.insert(
                        node.id.clone(),
                        TaskNodeRuntimeState::Skipped("dependency did not pass".to_string()),
                    );
                    changed = true;
                }
            }
        }
    }
}

#[derive(Debug, Clone, PartialEq)]
pub enum EvaluationOutcome {
    Passed(Evaluation),
    Rework {
        evaluation: Evaluation,
        instruction: String,
    },
    Failed(Evaluation),
}

pub fn plan_task_graph(task_id: &TaskId, task: &str, route: Option<&RoleRoute>) -> TaskGraph {
    if let Some(graph) = plan_configured_task_graph(task_id, task) {
        return graph;
    }
    plan_builtin_task_graph(task_id, task, route)
}

fn plan_builtin_task_graph(task_id: &TaskId, task: &str, route: Option<&RoleRoute>) -> TaskGraph {
    let lower = task.to_lowercase();
    let graph_id = TaskGraphId(format!("graph-{}", task_id.0));
    let max_attempts = 2;
    let mut nodes = Vec::new();
    let marketing_campaign = contains_any(
        &lower,
        &[
            "抖音",
            "直播",
            "短视频",
            "五一",
            "节日",
            "营销",
            "活动",
            "促销",
            "投流",
            "千川",
            "引流",
        ],
    );

    if contains_any(
        &lower,
        &["客服", "售后", "投诉", "退款", "满意度", "nps", "服务"],
    ) {
        nodes.push(node(
            "service",
            "service",
            "服务方案",
            format!("给出客服、售后、投诉、退款或满意度处理方案：{task}"),
            Vec::new(),
            max_attempts,
        ));
    } else if contains_any(
        &lower,
        &[
            "seo",
            "关键词",
            "搜索",
            "自然流量",
            "收录",
            "排名",
            "标题优化",
            "独立站",
        ],
    ) {
        nodes.push(node(
            "web",
            "web",
            "Web/SEO方案",
            format!("给出 SEO、关键词、收录、标题或自然流量增长方案：{task}"),
            Vec::new(),
            max_attempts,
        ));
    } else if contains_any(
        &lower,
        &[
            "代码",
            "rust",
            "python",
            "架构",
            "接口",
            "性能",
            "sla",
            "故障",
            "系统",
            "发布",
            "回滚",
            "稳定性",
            "bug",
        ],
    ) {
        nodes.push(node(
            "engineering",
            "engineering",
            "工程判断",
            format!("给出工程分析和实现建议：{task}"),
            Vec::new(),
            max_attempts,
        ));
    } else if contains_any(
        &lower,
        &[
            "详情页",
            "设计",
            "预算",
            "风险",
            "归因",
            "漏斗",
            "数据",
            "转化率",
            "指标",
        ],
    ) {
        nodes.push(node(
            "data",
            "data",
            "数据诊断",
            format!("分析任务中的数据、指标、归因和约束：{task}"),
            Vec::new(),
            max_attempts,
        ));
        if contains_any(&lower, &["详情页", "设计", "页面"]) {
            nodes.push(node(
                "design",
                "design",
                "体验设计",
                "基于上游数据诊断，提出详情页或体验优化方案。".to_string(),
                vec!["data"],
                max_attempts,
            ));
        }
        if contains_any(&lower, &["预算", "成本", "财务", "风险"]) {
            nodes.push(node(
                "accounting",
                "accounting",
                "预算评估",
                "基于上游数据诊断，评估预算、成本和经营风险。".to_string(),
                vec!["data"],
                max_attempts,
            ));
        }
        if marketing_campaign && contains_any(&lower, &["转化率", "方案", "运营", "营销", "活动"])
        {
            if !nodes.iter().any(|node| node.role == "accounting") {
                nodes.push(node(
                    "accounting",
                    "accounting",
                    "预算评估",
                    "基于上游数据诊断，评估活动成本、引流SKU、投流预算和经营风险。".to_string(),
                    vec!["data"],
                    max_attempts,
                ));
            }
            nodes.push(node(
                "creative",
                "creative",
                "创意产出",
                "基于上游数据诊断，产出抖音短视频、直播或活动内容创意。".to_string(),
                vec!["data"],
                max_attempts,
            ));
        }
        if contains_any(&lower, &["运营", "调整", "方案", "策略"]) {
            let mut ops_inputs = Vec::new();
            if nodes.iter().any(|node| node.role == "design") {
                ops_inputs.push("design");
            }
            if nodes.iter().any(|node| node.role == "accounting") {
                ops_inputs.push("accounting");
            }
            if nodes.iter().any(|node| node.role == "creative") {
                ops_inputs.push("creative");
            }
            if ops_inputs.is_empty() {
                ops_inputs.push("data");
            }
            nodes.push(node(
                "ops",
                "ops",
                "运营方案",
                "基于上游结果，形成可执行运营调整方案。".to_string(),
                ops_inputs,
                max_attempts,
            ));
        }
        if contains_any(&lower, &["文案", "脚本", "标题", "内容"])
            && !nodes.iter().any(|node| node.role == "creative")
        {
            nodes.push(node(
                "creative",
                "creative",
                "创意产出",
                "基于上游数据诊断，产出内容创意或文案。".to_string(),
                vec!["data"],
                max_attempts,
            ));
        }
    } else if contains_any(&lower, &["文案", "脚本", "标题", "内容"]) {
        nodes.push(node(
            "ops",
            "ops",
            "运营定位",
            format!("梳理内容任务目标、受众和运营约束：{task}"),
            Vec::new(),
            max_attempts,
        ));
        nodes.push(node(
            "creative",
            "creative",
            "创意产出",
            "基于运营定位，产出内容创意或文案。".to_string(),
            vec!["ops"],
            max_attempts,
        ));
    } else {
        let role = route
            .map(|route| route.primary_runtime_role.as_str())
            .unwrap_or("chat");
        nodes.push(node(
            role,
            role,
            "直接回答",
            task.to_string(),
            Vec::new(),
            max_attempts,
        ));
    }

    TaskGraph {
        graph_id,
        root_task: task.to_string(),
        nodes,
    }
}

fn node(
    id: &str,
    role: &str,
    title: &str,
    objective: String,
    input_refs: Vec<&str>,
    max_attempts: u8,
) -> TaskNode {
    TaskNode {
        id: TaskNodeId(id.to_string()),
        role: role.to_string(),
        worker_id: WorkerId(format!("worker.{role}")),
        title: title.to_string(),
        objective,
        input_refs: input_refs
            .into_iter()
            .map(|input_ref| TaskNodeId(input_ref.to_string()))
            .collect(),
        acceptance: vec!["content must be specific and non-empty".to_string()],
        max_attempts,
    }
}

#[derive(Debug, Clone, serde::Deserialize)]
struct TaskGraphRuleSet {
    #[serde(default)]
    rules: Vec<TaskGraphRule>,
}

#[derive(Debug, Clone, serde::Deserialize)]
struct TaskGraphRule {
    id: String,
    #[serde(default)]
    description: String,
    #[serde(default)]
    match_any: Vec<String>,
    #[serde(default)]
    match_all: Vec<String>,
    #[serde(default)]
    match_all_any: Vec<Vec<String>>,
    #[serde(default)]
    nodes: Vec<TaskGraphRuleNode>,
}

#[derive(Debug, Clone, serde::Deserialize)]
struct TaskGraphRuleNode {
    id: String,
    role: String,
    title: String,
    objective: String,
    #[serde(default)]
    depends_on: Vec<String>,
    #[serde(default = "default_rule_node_max_attempts")]
    max_attempts: u8,
    #[serde(default)]
    acceptance: Vec<String>,
}

fn default_rule_node_max_attempts() -> u8 {
    2
}

fn plan_configured_task_graph(task_id: &TaskId, task: &str) -> Option<TaskGraph> {
    let path = env::var(TASK_GRAPH_RULES_ENV)
        .ok()
        .filter(|path| !path.trim().is_empty())
        .unwrap_or_else(|| DEFAULT_TASK_GRAPH_RULES_PATH.to_string());
    if !Path::new(&path).exists() {
        return None;
    }

    let rules = match load_task_graph_rules(&path) {
        Ok(rules) => rules,
        Err(error) => {
            crate::log_error_msg!(&format!(
                "task graph rules ignored: path={path}, error={error}"
            ));
            return None;
        }
    };
    plan_task_graph_from_rules(task_id, task, &rules)
}

fn load_task_graph_rules(path: impl AsRef<Path>) -> Result<TaskGraphRuleSet, String> {
    let content = fs::read_to_string(path.as_ref()).map_err(|error| error.to_string())?;
    parse_task_graph_rules(&content)
}

fn parse_task_graph_rules(content: &str) -> Result<TaskGraphRuleSet, String> {
    let rules = serde_json::from_str::<TaskGraphRuleSet>(content)
        .map_err(|error| format!("invalid task graph rules json: {error}"))?;
    validate_task_graph_rules(&rules)?;
    Ok(rules)
}

fn plan_task_graph_from_rules(
    task_id: &TaskId,
    task: &str,
    rules: &TaskGraphRuleSet,
) -> Option<TaskGraph> {
    let rule = rules.rules.iter().find(|rule| rule.matches(task))?;
    Some(build_configured_task_graph(task_id, task, rule))
}

impl TaskGraphRule {
    fn matches(&self, task: &str) -> bool {
        let lower = task.to_lowercase();
        let contains = |token: &str| lower.contains(&token.to_lowercase());
        if !self.match_all.iter().all(|token| contains(token)) {
            return false;
        }
        if !self.match_any.is_empty() && !self.match_any.iter().any(|token| contains(token)) {
            return false;
        }
        self.match_all_any
            .iter()
            .all(|group| group.iter().any(|token| contains(token)))
    }
}

fn build_configured_task_graph(task_id: &TaskId, task: &str, rule: &TaskGraphRule) -> TaskGraph {
    let graph_id = TaskGraphId(format!("graph-{}", task_id.0));
    let nodes = rule
        .nodes
        .iter()
        .map(|node| TaskNode {
            id: TaskNodeId(node.id.clone()),
            role: node.role.clone(),
            worker_id: WorkerId(format!("worker.{}", node.role)),
            title: node.title.clone(),
            objective: render_rule_template(&node.objective, task, rule),
            input_refs: node
                .depends_on
                .iter()
                .map(|input_ref| TaskNodeId(input_ref.clone()))
                .collect(),
            acceptance: if node.acceptance.is_empty() {
                vec!["content must be specific and non-empty".to_string()]
            } else {
                node.acceptance.clone()
            },
            max_attempts: node.max_attempts.max(1),
        })
        .collect();

    TaskGraph {
        graph_id,
        root_task: task.to_string(),
        nodes,
    }
}

fn render_rule_template(template: &str, task: &str, rule: &TaskGraphRule) -> String {
    template
        .replace("{{task}}", task)
        .replace("{{rule_id}}", &rule.id)
        .replace("{{rule_description}}", &rule.description)
}

fn validate_task_graph_rules(rules: &TaskGraphRuleSet) -> Result<(), String> {
    validate_task_graph_rules_with_catalog(rules, &RoleCatalog::configured())
}

fn validate_task_graph_rules_with_catalog(
    rules: &TaskGraphRuleSet,
    catalog: &RoleCatalog,
) -> Result<(), String> {
    let valid_roles = catalog
        .roles()
        .iter()
        .map(|role| role.runtime_role.clone())
        .collect::<HashSet<_>>();
    let mut rule_ids = HashSet::new();

    for rule in &rules.rules {
        validate_rule(rule, &valid_roles)?;
        if !rule_ids.insert(rule.id.clone()) {
            return Err(format!("duplicate rule id '{}'", rule.id));
        }
    }

    Ok(())
}

fn validate_rule(rule: &TaskGraphRule, valid_roles: &HashSet<String>) -> Result<(), String> {
    if rule.id.trim().is_empty() {
        return Err("rule id is required".to_string());
    }
    if rule.nodes.is_empty() {
        return Err(format!("rule '{}' must declare at least one node", rule.id));
    }

    let mut node_ids = HashSet::new();
    for node in &rule.nodes {
        if node.id.trim().is_empty() {
            return Err(format!("rule '{}' has node without id", rule.id));
        }
        if !node_ids.insert(node.id.clone()) {
            return Err(format!(
                "rule '{}' has duplicate node '{}'",
                rule.id, node.id
            ));
        }
        if !valid_roles.contains(&node.role) {
            return Err(format!(
                "rule '{}' node '{}' uses unknown role '{}'",
                rule.id, node.id, node.role
            ));
        }
        if node.title.trim().is_empty() {
            return Err(format!(
                "rule '{}' node '{}' must declare title",
                rule.id, node.id
            ));
        }
        if node.objective.trim().is_empty() {
            return Err(format!(
                "rule '{}' node '{}' must declare objective",
                rule.id, node.id
            ));
        }
    }

    for node in &rule.nodes {
        for dep in &node.depends_on {
            if !node_ids.contains(dep) {
                return Err(format!(
                    "rule '{}' node '{}' depends on unknown node '{}'",
                    rule.id, node.id, dep
                ));
            }
            if dep == &node.id {
                return Err(format!(
                    "rule '{}' node '{}' cannot depend on itself",
                    rule.id, node.id
                ));
            }
        }
    }

    if !rule.nodes.iter().any(|node| node.depends_on.is_empty()) {
        return Err(format!(
            "rule '{}' must have at least one root node",
            rule.id
        ));
    }
    validate_rule_has_no_cycles(rule)
}

fn validate_rule_has_no_cycles(rule: &TaskGraphRule) -> Result<(), String> {
    #[derive(Clone, Copy, PartialEq, Eq)]
    enum VisitState {
        Visiting,
        Done,
    }

    fn visit(
        node_id: &str,
        deps: &HashMap<&str, Vec<&str>>,
        states: &mut HashMap<String, VisitState>,
        rule_id: &str,
    ) -> Result<(), String> {
        match states.get(node_id).copied() {
            Some(VisitState::Visiting) => {
                return Err(format!(
                    "rule '{rule_id}' contains dependency cycle at node '{node_id}'"
                ));
            }
            Some(VisitState::Done) => return Ok(()),
            None => {}
        }

        states.insert(node_id.to_string(), VisitState::Visiting);
        for dep in deps.get(node_id).into_iter().flatten() {
            visit(dep, deps, states, rule_id)?;
        }
        states.insert(node_id.to_string(), VisitState::Done);
        Ok(())
    }

    let deps = rule
        .nodes
        .iter()
        .map(|node| {
            (
                node.id.as_str(),
                node.depends_on
                    .iter()
                    .map(String::as_str)
                    .collect::<Vec<_>>(),
            )
        })
        .collect::<HashMap<_, _>>();
    let mut states = HashMap::new();
    for node in &rule.nodes {
        visit(&node.id, &deps, &mut states, &rule.id)?;
    }
    Ok(())
}

fn evaluate_report(
    report: &WorkerReport,
    node: Option<&TaskNode>,
    upstream_reports: &HashMap<TaskNodeId, WorkerReport>,
) -> Evaluation {
    let content = report.content.trim();
    let mut reasons = Vec::new();
    if report.status == WorkerReportStatus::Failed {
        reasons.push("worker reported failed status".to_string());
    }
    if content.is_empty() {
        reasons.push("content is empty".to_string());
    }
    if open_questions_are_blocking(report.role_output.as_ref(), &report.open_questions) {
        reasons.push("open questions remain".to_string());
    }
    match &report.role_output {
        Some(role_output) => {
            reasons.extend(evaluate_role_output(&report.role, role_output));
            reasons.extend(evaluate_quality_contract(report, role_output));
            if let Some(node) = node {
                reasons.extend(evaluate_upstream_evidence(
                    node,
                    role_output,
                    upstream_reports,
                ));
            }
        }
        None => reasons.push("structured role output is missing".to_string()),
    }
    let lower = content.to_lowercase();
    if contains_any(
        &lower,
        &[
            "todo",
            "tbd",
            "placeholder",
            "无法处理",
            "不知道",
            "无数据",
            "作为ai",
            "as an ai",
        ],
    ) {
        reasons.push("content looks like placeholder or failure text".to_string());
    }

    let passed = reasons.is_empty();
    Evaluation {
        node_id: report.node_id.clone(),
        passed,
        score: if passed { 1.0 } else { 0.0 },
        reasons,
        rework_instruction: (!passed).then_some(
            "请重新提交本节点结果：内容必须具体、非空，不能是占位或失败表达。".to_string(),
        ),
    }
}

fn evaluate_quality_contract(report: &WorkerReport, output: &RoleWorkOutput) -> Vec<String> {
    let mut reasons = Vec::new();
    let content = format!(
        "{} {} {} {} {}",
        output.summary,
        output.findings.join(" "),
        output.recommendations.join(" "),
        output.risks.join(" "),
        report.content
    );
    let lower = content.to_lowercase();

    if output.summary.chars().count() < 8 {
        reasons.push("quality completeness: summary is too thin".to_string());
    }
    if output.findings.is_empty() && output.recommendations.is_empty() {
        reasons.push("quality completeness: no findings or recommendations".to_string());
    }
    if contains_any(
        &lower,
        &[
            "已查询",
            "已检索",
            "查询到",
            "搜索结果显示",
            "数据库显示",
            "实时数据",
            "调用工具",
            "tool result",
        ],
    ) {
        reasons.push("quality fabrication: claims unsupported external evidence".to_string());
    }
    if is_action_oriented_role(&report.role) && output.recommendations.is_empty() {
        reasons.push("quality actionability: missing executable recommendations".to_string());
    }
    if is_risk_sensitive_role(&report.role) && output.risks.is_empty() {
        reasons.push("quality risk awareness: missing risk boundary".to_string());
    }
    if contains_any(
        &lower,
        &[
            "不能回答",
            "无法回答",
            "无法完成任务",
            "无法继续",
            "无法推进",
            "我不能",
            "i cannot",
            "can't help",
        ],
    ) && output.open_questions.is_empty()
    {
        reasons
            .push("quality refusal detection: refusal without explicit open question".to_string());
    }

    reasons
}

fn open_questions_are_blocking(output: Option<&RoleWorkOutput>, open_questions: &[String]) -> bool {
    if open_questions.is_empty() {
        return false;
    }
    if open_questions.iter().any(|item| {
        contains_any(
            item,
            &[
                "无法继续",
                "无法判断",
                "无法完成任务",
                "阻止",
                "必须提供",
                "必须补充",
            ],
        )
    }) {
        return true;
    }

    output
        .map(|output| {
            output.summary.trim().is_empty()
                || (output.findings.is_empty() && output.recommendations.is_empty())
        })
        .unwrap_or(true)
}

fn evaluate_upstream_evidence(
    node: &TaskNode,
    output: &RoleWorkOutput,
    upstream_reports: &HashMap<TaskNodeId, WorkerReport>,
) -> Vec<String> {
    if node.input_refs.is_empty() {
        return Vec::new();
    }

    let mut reasons = Vec::new();
    if output.evidence.is_empty() {
        reasons.push("upstream evidence: dependent role must cite upstream inputs".to_string());
        return reasons;
    }

    for input_ref in &node.input_refs {
        let source_marker = format!("worker.assignment.input.{}", input_ref.0);
        let upstream_role = upstream_reports
            .get(input_ref)
            .map(|report| report.role.as_str())
            .unwrap_or_default();
        let cited = output.evidence.iter().any(|item| {
            let item = item.trim();
            item.contains(&input_ref.0)
                || item.contains(&source_marker)
                || (!upstream_role.is_empty() && item.contains(upstream_role))
        });
        if !cited {
            reasons.push(format!(
                "upstream evidence: missing citation for input {}",
                input_ref.0
            ));
        }
    }

    reasons
}

fn evaluate_role_output(role: &str, output: &RoleWorkOutput) -> Vec<String> {
    let mut reasons = Vec::new();
    let role = role.trim();

    if output.summary.trim().is_empty() {
        reasons.push("role output summary is empty".to_string());
    }
    if open_questions_are_blocking(Some(output), &output.open_questions) {
        reasons.push("role output has open questions".to_string());
    }
    if output.summary.contains("已调用")
        || output.findings.iter().any(|item| item.contains("已调用"))
        || output
            .recommendations
            .iter()
            .any(|item| item.contains("已调用"))
    {
        reasons.push("role output claims external tool execution".to_string());
    }

    match role {
        "data" => {
            if output.findings.len() < 2 {
                reasons.push("data role should provide at least two findings".to_string());
            }
            if output.risks.is_empty() {
                reasons.push("data role should mark data or interpretation risks".to_string());
            }
        }
        "ops" if output.recommendations.len() < 3 => {
            reasons.push("ops role should provide at least three recommendations".to_string());
        }
        "design" => {
            if output.recommendations.is_empty() {
                reasons
                    .push("design role should provide visual or page recommendations".to_string());
            }
            if !contains_any(
                &format!("{} {}", output.summary, output.recommendations.join(" ")),
                &["视觉", "页面", "素材", "版式", "主图", "详情页", "设计"],
            ) {
                reasons.push("design role output lacks design-specific content".to_string());
            }
        }
        "accounting" => {
            if output.risks.is_empty() {
                reasons
                    .push("accounting role should provide budget or financial risks".to_string());
            }
            if !contains_any(
                &format!("{} {}", output.summary, output.findings.join(" ")),
                &["预算", "成本", "利润", "roi", "ROI", "现金流", "财务"],
            ) {
                reasons.push("accounting role output lacks financial content".to_string());
            }
        }
        "creative" => {
            if output.recommendations.is_empty() {
                reasons.push("creative role should provide usable creative directions".to_string());
            }
            if !contains_any(
                &format!("{} {}", output.summary, output.recommendations.join(" ")),
                &["文案", "标题", "脚本", "创意", "内容", "短视频"],
            ) {
                reasons.push("creative role output lacks creative-specific content".to_string());
            }
        }
        "service" => {
            if output.recommendations.is_empty() {
                reasons.push(
                    "service role should provide scripts, SOP, or escalation steps".to_string(),
                );
            }
            if !contains_any(
                &format!("{} {}", output.summary, output.recommendations.join(" ")),
                &[
                    "客服",
                    "售后",
                    "投诉",
                    "退款",
                    "sop",
                    "SOP",
                    "升级",
                    "满意度",
                ],
            ) {
                reasons.push("service role output lacks service-specific content".to_string());
            }
        }
        "engineering" => {
            if output.risks.is_empty() {
                reasons.push(
                    "engineering role should provide technical risks or rollback boundaries"
                        .to_string(),
                );
            }
            if !contains_any(
                &format!("{} {}", output.summary, output.findings.join(" ")),
                &[
                    "技术",
                    "架构",
                    "接口",
                    "性能",
                    "发布",
                    "回滚",
                    "稳定性",
                    "系统",
                ],
            ) {
                reasons.push("engineering role output lacks technical content".to_string());
            }
        }
        "web" => {
            if output.recommendations.is_empty() {
                reasons.push(
                    "web role should provide SEO, keyword, or conversion recommendations"
                        .to_string(),
                );
            }
            if !contains_any(
                &format!("{} {}", output.summary, output.recommendations.join(" ")),
                &["seo", "SEO", "关键词", "收录", "标题", "自然流量", "页面"],
            ) {
                reasons.push("web role output lacks web or SEO-specific content".to_string());
            }
        }
        _ => {}
    }

    reasons
}

fn is_action_oriented_role(role: &str) -> bool {
    matches!(
        role,
        "ops" | "design" | "creative" | "service" | "engineering" | "web"
    )
}

fn is_risk_sensitive_role(role: &str) -> bool {
    matches!(
        role,
        "ops" | "data" | "accounting" | "engineering" | "service" | "web"
    )
}

fn extend_synthesis_items(lines: &mut Vec<String>, label: &str, items: &[String]) {
    if items.is_empty() {
        return;
    }
    lines.push(format!(
        "{label}: {}",
        items
            .iter()
            .filter(|item| !item.trim().is_empty())
            .map(|item| item.trim().to_string())
            .collect::<Vec<_>>()
            .join("；")
    ));
}

fn merge_unique(mut left: Vec<String>, right: Vec<String>) -> Vec<String> {
    for item in right {
        if !item.trim().is_empty() && !left.contains(&item) {
            left.push(item);
        }
    }
    left
}

fn unique_strings(items: Vec<String>) -> Vec<String> {
    items.into_iter().fold(Vec::new(), |mut unique, item| {
        if !item.trim().is_empty() && !unique.contains(&item) {
            unique.push(item);
        }
        unique
    })
}

fn contains_any(value: &str, needles: &[&str]) -> bool {
    needles.iter().any(|needle| value.contains(needle))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn planner_creates_single_chat_node_for_plain_question() {
        let graph = plan_task_graph(
            &TaskId("task-1".to_string()),
            "解释一下 agent session 是什么",
            None,
        );

        assert_eq!(graph.nodes.len(), 1);
        assert_eq!(graph.nodes[0].role, "chat");
        assert!(graph.nodes[0].input_refs.is_empty());
    }

    #[test]
    fn planner_creates_serial_data_to_ops_graph() {
        let graph = plan_task_graph(
            &TaskId("task-2".to_string()),
            "分析转化率下降并给运营调整方案",
            None,
        );

        let data = graph.nodes.iter().find(|node| node.role == "data").unwrap();
        let ops = graph.nodes.iter().find(|node| node.role == "ops").unwrap();
        assert!(data.input_refs.is_empty());
        assert_eq!(ops.input_refs, vec![data.id.clone()]);
    }

    #[test]
    fn planner_creates_parallel_design_and_accounting_after_data() {
        let graph = plan_task_graph(
            &TaskId("task-3".to_string()),
            "基于数据诊断做详情页优化和预算风险评估",
            None,
        );

        let data = graph.nodes.iter().find(|node| node.role == "data").unwrap();
        let design = graph
            .nodes
            .iter()
            .find(|node| node.role == "design")
            .unwrap();
        let accounting = graph
            .nodes
            .iter()
            .find(|node| node.role == "accounting")
            .unwrap();
        assert_eq!(design.input_refs, vec![data.id.clone()]);
        assert_eq!(accounting.input_refs, vec![data.id.clone()]);

        let data_node = data.clone();
        let mut runtime = TaskGraphRuntime::new(graph);
        assert_eq!(runtime.ready_nodes().len(), 1);
        let data_assignment = runtime.build_assignment(&data_node);
        runtime.mark_running(&data_assignment);
        let outcome = runtime.apply_report(report_from_assignment(&data_assignment, "data ok"));
        assert!(matches!(outcome, EvaluationOutcome::Passed(_)));
        let ready_roles = runtime
            .ready_nodes()
            .into_iter()
            .map(|node| node.role)
            .collect::<Vec<_>>();
        assert!(ready_roles.contains(&"design".to_string()));
        assert!(ready_roles.contains(&"accounting".to_string()));
    }

    #[test]
    fn planner_creates_serial_data_design_ops_graph() {
        let graph = plan_task_graph(
            &TaskId("task-6".to_string()),
            "基于数据诊断做详情页设计并给运营方案",
            None,
        );

        let data = graph.nodes.iter().find(|node| node.role == "data").unwrap();
        let design = graph
            .nodes
            .iter()
            .find(|node| node.role == "design")
            .unwrap();
        let ops = graph.nodes.iter().find(|node| node.role == "ops").unwrap();
        assert_eq!(design.input_refs, vec![data.id.clone()]);
        assert_eq!(ops.input_refs, vec![design.id.clone()]);
    }

    #[test]
    fn planner_creates_serial_data_accounting_ops_graph() {
        let graph = plan_task_graph(
            &TaskId("task-7".to_string()),
            "分析预算风险并给运营调整方案",
            None,
        );

        let data = graph.nodes.iter().find(|node| node.role == "data").unwrap();
        let accounting = graph
            .nodes
            .iter()
            .find(|node| node.role == "accounting")
            .unwrap();
        let ops = graph.nodes.iter().find(|node| node.role == "ops").unwrap();
        assert_eq!(accounting.input_refs, vec![data.id.clone()]);
        assert_eq!(ops.input_refs, vec![accounting.id.clone()]);
    }

    #[test]
    fn planner_adds_finance_and_creative_for_douyin_holiday_conversion_plan() {
        let graph = plan_task_graph(
            &TaskId("task-coffee".to_string()),
            "我是做咖啡运营的，主要营销平台是抖音，现在马上就五一了，\
             可以帮我想一个运营方案吗提升我的转化率",
            None,
        );

        let data = graph.nodes.iter().find(|node| node.role == "data").unwrap();
        let accounting = graph
            .nodes
            .iter()
            .find(|node| node.role == "accounting")
            .unwrap();
        let creative = graph
            .nodes
            .iter()
            .find(|node| node.role == "creative")
            .unwrap();
        let ops = graph.nodes.iter().find(|node| node.role == "ops").unwrap();

        assert_eq!(accounting.input_refs, vec![data.id.clone()]);
        assert_eq!(creative.input_refs, vec![data.id.clone()]);
        assert!(ops.input_refs.contains(&accounting.id));
        assert!(ops.input_refs.contains(&creative.id));
    }

    #[test]
    fn configurable_planner_builds_task_graph_from_matching_rule() {
        let rules = parse_task_graph_rules(
            r#"{
              "rules": [
                {
                  "id": "douyin_holiday_conversion",
                  "description": "抖音节日转化率",
                  "match_any": ["抖音", "五一"],
                  "match_all_any": [["转化率", "转化"], ["方案", "运营"]],
                  "nodes": [
                    {
                      "id": "data",
                      "role": "data",
                      "title": "数据诊断",
                      "objective": "分析：{{task}}"
                    },
                    {
                      "id": "creative",
                      "role": "creative",
                      "title": "创意产出",
                      "depends_on": ["data"],
                      "objective": "规则={{rule_id}}；{{rule_description}}"
                    },
                    {
                      "id": "ops",
                      "role": "ops",
                      "title": "运营方案",
                      "depends_on": ["creative"],
                      "objective": "整合输出"
                    }
                  ]
                }
              ]
            }"#,
        )
        .expect("valid configured rules");

        let graph = plan_task_graph_from_rules(
            &TaskId("task-config".to_string()),
            "五一抖音咖啡转化率运营方案",
            &rules,
        )
        .expect("rule should match");

        assert_eq!(graph.nodes.len(), 3);
        assert_eq!(graph.nodes[0].role, "data");
        assert!(graph.nodes[0].objective.contains("五一抖音咖啡"));
        assert_eq!(graph.nodes[1].role, "creative");
        assert_eq!(graph.nodes[1].input_refs, vec![graph.nodes[0].id.clone()]);
        assert!(
            graph.nodes[1]
                .objective
                .contains("douyin_holiday_conversion")
        );
        assert_eq!(graph.nodes[2].input_refs, vec![graph.nodes[1].id.clone()]);
    }

    #[test]
    fn configurable_planner_returns_none_when_no_rule_matches() {
        let rules = parse_task_graph_rules(
            r#"{
              "rules": [
                {
                  "id": "seo_only",
                  "match_all": ["SEO"],
                  "nodes": [
                    {
                      "id": "web",
                      "role": "web",
                      "title": "SEO方案",
                      "objective": "优化搜索"
                    }
                  ]
                }
              ]
            }"#,
        )
        .expect("valid configured rules");

        let graph = plan_task_graph_from_rules(
            &TaskId("task-no-match".to_string()),
            "五一抖音咖啡转化率运营方案",
            &rules,
        );

        assert!(graph.is_none());
    }

    #[test]
    fn configurable_planner_rejects_unknown_role_and_cycles() {
        let unknown_role = parse_task_graph_rules(
            r#"{
              "rules": [
                {
                  "id": "bad_role",
                  "nodes": [
                    {
                      "id": "x",
                      "role": "not_a_role",
                      "title": "坏节点",
                      "objective": "test"
                    }
                  ]
                }
              ]
            }"#,
        )
        .expect_err("unknown role should fail");
        assert!(unknown_role.contains("unknown role"));

        let cycle = parse_task_graph_rules(
            r#"{
              "rules": [
                {
                  "id": "cycle",
                  "nodes": [
                    {
                      "id": "a",
                      "role": "data",
                      "title": "A",
                      "objective": "a",
                      "depends_on": ["b"]
                    },
                    {
                      "id": "b",
                      "role": "ops",
                      "title": "B",
                      "objective": "b",
                      "depends_on": ["a"]
                    }
                  ]
                }
              ]
            }"#,
        )
        .expect_err("cycle should fail");
        assert!(cycle.contains("root node") || cycle.contains("cycle"));
    }

    #[test]
    fn configurable_planner_accepts_roles_from_configured_catalog() {
        let rules = serde_json::from_str::<TaskGraphRuleSet>(
            r#"{
              "rules": [
                {
                  "id": "douyin_ads_plan",
                  "match_all": ["千川"],
                  "nodes": [
                    {
                      "id": "ads",
                      "role": "douyin_ads",
                      "title": "千川投放",
                      "objective": "优化千川计划：{{task}}"
                    }
                  ]
                }
              ]
            }"#,
        )
        .expect("rules json should parse");
        let mut roles = RoleCatalog::builtin().roles().to_vec();
        roles.push(crate::roles::RoleProfile {
            id: crate::roles::RoleId("role.douyin-ads".to_string()),
            name: "千川投放专员".to_string(),
            runtime_role: "douyin_ads".to_string(),
            priority: 25,
            domains: vec!["domain.ecommerce".to_string()],
            keywords: vec!["千川".to_string(), "投流".to_string()],
            preferred_actions: vec![crate::roles::RoleAction::Optimize],
            required_capabilities: vec!["ads.optimization".to_string()],
            optional_capabilities: vec!["data.analysis".to_string()],
        });
        let catalog = RoleCatalog::new(roles);

        validate_task_graph_rules_with_catalog(&rules, &catalog)
            .expect("configured role should be accepted");

        let graph =
            plan_task_graph_from_rules(&TaskId("task-ads".to_string()), "千川投流优化", &rules)
                .expect("rule should match");
        assert_eq!(graph.nodes[0].role, "douyin_ads");
    }

    #[test]
    fn planner_prioritizes_service_role_for_support_workflows() {
        let graph = plan_task_graph(
            &TaskId("task-service".to_string()),
            "客服团队近期投诉和退款增加，请设计一套售后SOP",
            None,
        );

        assert_eq!(graph.nodes.len(), 1);
        assert_eq!(graph.nodes[0].role, "service");
    }

    #[test]
    fn planner_prioritizes_engineering_role_for_sla_incidents() {
        let graph = plan_task_graph(
            &TaskId("task-engineering".to_string()),
            "系统接口延迟升高并影响SLA，请给出架构排查和回滚方案",
            None,
        );

        assert_eq!(graph.nodes.len(), 1);
        assert_eq!(graph.nodes[0].role, "engineering");
    }

    #[test]
    fn planner_prioritizes_web_role_for_seo_workflows() {
        let graph = plan_task_graph(
            &TaskId("task-web".to_string()),
            "为跨境电商独立站制定SEO关键词集群、标题优化和收录策略",
            None,
        );

        assert_eq!(graph.nodes.len(), 1);
        assert_eq!(graph.nodes[0].role, "web");
    }

    #[test]
    fn scheduler_returns_only_one_ready_node_per_worker() {
        let graph = TaskGraph {
            graph_id: TaskGraphId("graph-same-worker".to_string()),
            root_task: "same worker".to_string(),
            nodes: vec![
                node(
                    "chat-a",
                    "chat",
                    "直接回答 A",
                    "A".to_string(),
                    Vec::new(),
                    2,
                ),
                node(
                    "chat-b",
                    "chat",
                    "直接回答 B",
                    "B".to_string(),
                    Vec::new(),
                    2,
                ),
            ],
        };
        let mut runtime = TaskGraphRuntime::new(graph);

        let ready = runtime.ready_nodes();
        assert_eq!(ready.len(), 1);
        assert_eq!(ready[0].worker_id, WorkerId("worker.chat".to_string()));

        let assignment = runtime.build_assignment(&ready[0]);
        runtime.mark_running(&assignment);
        assert!(runtime.ready_nodes().is_empty());

        let outcome = runtime.apply_report(report_from_assignment(&assignment, "first done"));
        assert!(matches!(outcome, EvaluationOutcome::Passed(_)));
        let next_ready = runtime.ready_nodes();
        assert_eq!(next_ready.len(), 1);
        assert_ne!(next_ready[0].id, ready[0].id);
    }

    #[test]
    fn runtime_requests_rework_for_empty_report() {
        let graph = plan_task_graph(&TaskId("task-4".to_string()), "解释一下 agent", None);
        let mut runtime = TaskGraphRuntime::new(graph);
        let node = runtime.ready_nodes().remove(0);
        let assignment = runtime.build_assignment(&node);
        runtime.mark_running(&assignment);

        let outcome = runtime.apply_report(report_from_assignment(&assignment, ""));

        assert!(matches!(outcome, EvaluationOutcome::Rework { .. }));
        assert!(matches!(
            runtime.states.get(&node.id),
            Some(TaskNodeRuntimeState::Pending)
        ));
        let next_assignment = runtime.build_assignment(&node);
        assert_eq!(next_assignment.attempt, 2);
        assert!(next_assignment.rework_instruction.is_some());
    }

    #[test]
    fn runtime_fails_after_attempts_are_exhausted() {
        let mut graph = plan_task_graph(&TaskId("task-5".to_string()), "解释一下 agent", None);
        graph.nodes[0].max_attempts = 1;
        let mut runtime = TaskGraphRuntime::new(graph);
        let node = runtime.ready_nodes().remove(0);
        let assignment = runtime.build_assignment(&node);
        runtime.mark_running(&assignment);

        let outcome = runtime.apply_report(report_from_assignment(&assignment, ""));

        assert!(matches!(outcome, EvaluationOutcome::Failed(_)));
        assert!(runtime.is_finished());
        assert!(runtime.synthesize().contains("未完成节点"));
    }

    #[test]
    fn runtime_requests_rework_when_open_questions_remain() {
        let graph = plan_task_graph(&TaskId("task-8".to_string()), "解释一下 agent", None);
        let mut runtime = TaskGraphRuntime::new(graph);
        let node = runtime.ready_nodes().remove(0);
        let assignment = runtime.build_assignment(&node);
        runtime.mark_running(&assignment);
        let mut report = report_from_assignment(&assignment, "需要更多上下文");
        report.open_questions = vec!["必须补充业务指标口径后才能继续".to_string()];

        let outcome = runtime.apply_report(report);

        assert!(matches!(outcome, EvaluationOutcome::Rework { .. }));
        assert!(matches!(
            runtime.states.get(&node.id),
            Some(TaskNodeRuntimeState::Pending)
        ));
    }

    #[test]
    fn evaluator_allows_non_blocking_open_questions_with_substantive_output() {
        let graph = TaskGraph {
            graph_id: TaskGraphId("graph-design-gaps".to_string()),
            root_task: "做详情页设计建议".to_string(),
            nodes: vec![node(
                "design",
                "design",
                "体验设计",
                "做详情页设计建议".to_string(),
                Vec::new(),
                2,
            )],
        };
        let mut runtime = TaskGraphRuntime::new(graph);
        let node = runtime.ready_nodes().remove(0);
        let assignment = runtime.build_assignment(&node);
        runtime.mark_running(&assignment);
        let mut report = report_from_assignment(&assignment, "设计建议完整");
        let role_output = report.role_output.as_mut().expect("role output");
        role_output.open_questions = vec!["需要当前详情页截图以便进一步细化设计。".to_string()];
        report.open_questions = role_output.open_questions.clone();

        let outcome = runtime.apply_report(report);

        assert!(matches!(outcome, EvaluationOutcome::Passed(_)));
    }

    #[test]
    fn evaluator_rejects_incomplete_role_output_for_data_role() {
        let graph = plan_task_graph(&TaskId("task-10".to_string()), "分析转化率下降", None);
        let mut runtime = TaskGraphRuntime::new(graph);
        let node = runtime.ready_nodes().remove(0);
        let assignment = runtime.build_assignment(&node);
        runtime.mark_running(&assignment);
        let mut report = report_from_assignment(&assignment, "数据结论不足");
        report.role_output = Some(RoleWorkOutput {
            summary: "数据结论不足".to_string(),
            findings: vec!["转化下降".to_string()],
            recommendations: Vec::new(),
            evidence: Vec::new(),
            risks: Vec::new(),
            open_questions: Vec::new(),
        });

        let outcome = runtime.apply_report(report);

        assert!(matches!(outcome, EvaluationOutcome::Rework { .. }));
        assert!(matches!(
            runtime.states.get(&node.id),
            Some(TaskNodeRuntimeState::Pending)
        ));
    }

    #[test]
    fn evaluator_rejects_missing_structured_role_output() {
        let graph = plan_task_graph(&TaskId("task-15".to_string()), "分析转化率下降", None);
        let mut runtime = TaskGraphRuntime::new(graph);
        let node = runtime.ready_nodes().remove(0);
        let assignment = runtime.build_assignment(&node);
        runtime.mark_running(&assignment);
        let mut report = report_from_assignment(&assignment, "只有纯文本，不含结构化角色产物");
        report.role_output = None;

        let outcome = runtime.apply_report(report);

        assert!(matches!(outcome, EvaluationOutcome::Rework { .. }));
    }

    #[test]
    fn evaluator_passes_complete_ops_role_output() {
        let graph = TaskGraph {
            graph_id: TaskGraphId("graph-task-11".to_string()),
            root_task: "给运营调整方案".to_string(),
            nodes: vec![node(
                "ops",
                "ops",
                "运营方案",
                "给运营调整方案".to_string(),
                Vec::new(),
                2,
            )],
        };
        let mut runtime = TaskGraphRuntime::new(graph);
        let node = runtime.ready_nodes().remove(0);
        let assignment = runtime.build_assignment(&node);
        runtime.mark_running(&assignment);
        let mut report = report_from_assignment(&assignment, "运营方案完整");
        report.role_output = Some(RoleWorkOutput {
            summary: "先用三档优先级推进运营调整。".to_string(),
            findings: vec!["当前目标是提升转化".to_string()],
            recommendations: vec![
                "P1 修正首屏卖点".to_string(),
                "P2 调整投放人群".to_string(),
                "P3 每日复盘转化".to_string(),
            ],
            evidence: vec!["原始任务".to_string()],
            risks: vec!["预算消耗需设置止损线".to_string()],
            open_questions: Vec::new(),
        });

        let outcome = runtime.apply_report(report);

        assert!(matches!(outcome, EvaluationOutcome::Passed(_)));
    }

    #[test]
    fn synthesis_uses_structured_role_output_sections() {
        let graph = TaskGraph {
            graph_id: TaskGraphId("graph-task-12".to_string()),
            root_task: "给运营调整方案".to_string(),
            nodes: vec![node(
                "ops",
                "ops",
                "运营方案",
                "给运营调整方案".to_string(),
                Vec::new(),
                2,
            )],
        };
        let mut runtime = TaskGraphRuntime::new(graph);
        let node = runtime.ready_nodes().remove(0);
        let assignment = runtime.build_assignment(&node);
        runtime.mark_running(&assignment);
        let mut report = report_from_assignment(&assignment, "运营方案完整");
        report.role_output = Some(RoleWorkOutput {
            summary: "运营先聚焦转化链路。".to_string(),
            findings: vec!["首屏承接弱".to_string()],
            recommendations: vec![
                "P1 优化首屏".to_string(),
                "P2 调整人群".to_string(),
                "P3 复盘素材".to_string(),
            ],
            evidence: vec!["原始任务".to_string()],
            risks: vec!["不要同时改太多变量".to_string()],
            open_questions: Vec::new(),
        });
        assert!(matches!(
            runtime.apply_report(report),
            EvaluationOutcome::Passed(_)
        ));

        let output = runtime.synthesize();

        assert!(output.contains("运营先聚焦转化链路"));
        assert!(output.contains("发现: 首屏承接弱"));
        assert!(output.contains("建议: P1 优化首屏"));
        assert!(output.contains("风险: 不要同时改太多变量"));
    }

    #[test]
    fn evaluator_requires_dependent_role_to_cite_upstream_report() {
        let graph = TaskGraph {
            graph_id: TaskGraphId("graph-task-13".to_string()),
            root_task: "基于数据诊断做详情页设计".to_string(),
            nodes: vec![
                node(
                    "data",
                    "data",
                    "数据诊断",
                    "分析转化率下降".to_string(),
                    Vec::new(),
                    2,
                ),
                node(
                    "design",
                    "design",
                    "体验设计",
                    "基于数据诊断优化详情页".to_string(),
                    vec!["data"],
                    2,
                ),
            ],
        };
        let mut runtime = TaskGraphRuntime::new(graph);
        let data = runtime.ready_nodes().remove(0);
        let data_assignment = runtime.build_assignment(&data);
        runtime.mark_running(&data_assignment);
        assert!(matches!(
            runtime.apply_report(report_from_assignment(&data_assignment, "数据诊断完成")),
            EvaluationOutcome::Passed(_)
        ));

        let design = runtime.ready_nodes().remove(0);
        let design_assignment = runtime.build_assignment(&design);
        runtime.mark_running(&design_assignment);
        let mut report = report_from_assignment(&design_assignment, "设计方案完整");
        report.role_output = Some(RoleWorkOutput {
            summary: "详情页设计应围绕首屏信任和转化路径收敛。".to_string(),
            findings: vec!["页面首屏承接弱".to_string()],
            recommendations: vec!["重排主图、卖点和行动按钮的页面层级。".to_string()],
            evidence: vec!["原始任务".to_string()],
            risks: vec!["缺少真实热区和点击数据，方案需小流量验证。".to_string()],
            open_questions: Vec::new(),
        });

        let outcome = runtime.apply_report(report);

        assert!(matches!(outcome, EvaluationOutcome::Rework { .. }));
    }

    #[test]
    fn evaluator_accepts_dependent_role_with_upstream_citation() {
        let graph = TaskGraph {
            graph_id: TaskGraphId("graph-task-14".to_string()),
            root_task: "基于数据诊断做详情页设计".to_string(),
            nodes: vec![
                node(
                    "data",
                    "data",
                    "数据诊断",
                    "分析转化率下降".to_string(),
                    Vec::new(),
                    2,
                ),
                node(
                    "design",
                    "design",
                    "体验设计",
                    "基于数据诊断优化详情页".to_string(),
                    vec!["data"],
                    2,
                ),
            ],
        };
        let mut runtime = TaskGraphRuntime::new(graph);
        let data = runtime.ready_nodes().remove(0);
        let data_assignment = runtime.build_assignment(&data);
        runtime.mark_running(&data_assignment);
        assert!(matches!(
            runtime.apply_report(report_from_assignment(&data_assignment, "数据诊断完成")),
            EvaluationOutcome::Passed(_)
        ));

        let design = runtime.ready_nodes().remove(0);
        let design_assignment = runtime.build_assignment(&design);
        runtime.mark_running(&design_assignment);
        let mut report = report_from_assignment(&design_assignment, "设计方案完整");
        report.role_output = Some(RoleWorkOutput {
            summary: "详情页设计应围绕首屏信任和转化路径收敛。".to_string(),
            findings: vec!["页面首屏承接弱".to_string()],
            recommendations: vec!["重排主图、卖点和行动按钮的页面层级。".to_string()],
            evidence: vec!["worker.assignment.input.data".to_string()],
            risks: vec!["缺少真实热区和点击数据，方案需小流量验证。".to_string()],
            open_questions: Vec::new(),
        });

        let outcome = runtime.apply_report(report);

        assert!(matches!(outcome, EvaluationOutcome::Passed(_)));
    }

    #[test]
    fn evaluator_covers_role_contract_scenarios_from_old_python_effects() {
        let cases = vec![
            (
                "data",
                RoleWorkOutput {
                    summary: "数据诊断先锁定漏斗、口径和归因边界。".to_string(),
                    findings: vec![
                        "点击到成交漏斗存在断点".to_string(),
                        "活动口径需要统一".to_string(),
                    ],
                    recommendations: vec!["按渠道拆分转化漏斗并标记异常段。".to_string()],
                    evidence: vec!["原始任务".to_string()],
                    risks: vec!["样本量不足会导致归因误判。".to_string()],
                    open_questions: Vec::new(),
                },
            ),
            (
                "service",
                RoleWorkOutput {
                    summary: "客服 SOP 应先稳定投诉响应和升级边界。".to_string(),
                    findings: vec!["退款争议需要统一口径".to_string()],
                    recommendations: vec!["建立售后首响、补偿、升级三段 SOP。".to_string()],
                    evidence: vec!["原始任务".to_string()],
                    risks: vec!["超政策承诺会扩大财务和履约风险。".to_string()],
                    open_questions: Vec::new(),
                },
            ),
            (
                "accounting",
                RoleWorkOutput {
                    summary: "预算风险控制要先看成本、利润和 ROI 止损线。".to_string(),
                    findings: vec!["预算消耗需要绑定毛利空间".to_string()],
                    recommendations: vec!["按 ROI 阈值设置日预算上限。".to_string()],
                    evidence: vec!["原始任务".to_string()],
                    risks: vec!["现金流不足时不能扩大投放。".to_string()],
                    open_questions: Vec::new(),
                },
            ),
            (
                "engineering",
                RoleWorkOutput {
                    summary: "工程发布计划应围绕稳定性、回滚和接口兼容。".to_string(),
                    findings: vec!["系统发布需要保护核心接口".to_string()],
                    recommendations: vec!["先灰度发布并准备回滚检查单。".to_string()],
                    evidence: vec!["原始任务".to_string()],
                    risks: vec!["缺少监控会放大发布故障。".to_string()],
                    open_questions: Vec::new(),
                },
            ),
            (
                "web",
                RoleWorkOutput {
                    summary: "SEO 页面应围绕关键词集群和收录路径优化。".to_string(),
                    findings: vec!["页面标题和关键词意图需要对齐".to_string()],
                    recommendations: vec!["建立核心词、长尾词和内容页面的 SEO 集群。".to_string()],
                    evidence: vec!["原始任务".to_string()],
                    risks: vec!["未验证搜索量时不能承诺自然流量结果。".to_string()],
                    open_questions: Vec::new(),
                },
            ),
            (
                "creative",
                RoleWorkOutput {
                    summary: "短视频创意应先产出可测试的标题和脚本钩子。".to_string(),
                    findings: vec!["内容需要突出首三秒冲突".to_string()],
                    recommendations: vec!["准备三组标题和短视频脚本开头做 A/B 测试。".to_string()],
                    evidence: vec!["原始任务".to_string()],
                    risks: vec!["不能编造产品功效或用户反馈。".to_string()],
                    open_questions: Vec::new(),
                },
            ),
        ];

        for (role, role_output) in cases {
            let report = WorkerReport {
                graph_id: TaskGraphId(format!("graph-{role}")),
                node_id: TaskNodeId(role.to_string()),
                task_id: TaskId(format!("task-{role}")),
                worker_id: WorkerId(format!("worker.{role}")),
                role: role.to_string(),
                content: role_output.summary.clone(),
                role_output: Some(role_output),
                evidence: Vec::new(),
                risks: Vec::new(),
                open_questions: Vec::new(),
                status: WorkerReportStatus::Completed,
            };

            let evaluation = evaluate_report(&report, None, &HashMap::new());

            assert!(
                evaluation.passed,
                "{role} should pass, got {:?}",
                evaluation.reasons
            );
        }
    }

    #[test]
    fn evaluator_allows_risk_marked_missing_precision_without_refusal() {
        let role_output = RoleWorkOutput {
            summary: "预算风险需要先用 CPA、ROI 和毛利边界判断。".to_string(),
            findings: vec!["CPC 上升叠加 CVR 下降会推高 CPA。".to_string()],
            recommendations: vec!["先按客单价补齐盈亏平衡测算。".to_string()],
            evidence: vec!["原始任务".to_string()],
            risks: vec!["缺乏客单价数据，无法完成精确盈亏平衡点量化计算。".to_string()],
            open_questions: Vec::new(),
        };
        let report = WorkerReport {
            graph_id: TaskGraphId("graph-accounting-precision".to_string()),
            node_id: TaskNodeId("accounting".to_string()),
            task_id: TaskId("task-accounting-precision".to_string()),
            worker_id: WorkerId("worker.accounting".to_string()),
            role: "accounting".to_string(),
            content: role_output.summary.clone(),
            role_output: Some(role_output),
            evidence: Vec::new(),
            risks: Vec::new(),
            open_questions: Vec::new(),
            status: WorkerReportStatus::Completed,
        };

        let evaluation = evaluate_report(&report, None, &HashMap::new());

        assert!(evaluation.passed, "{:?}", evaluation.reasons);
    }

    #[test]
    fn evaluator_allows_web_seo_terms_without_claiming_external_search() {
        let role_output = RoleWorkOutput {
            summary: "SEO 页面应围绕关键词集群和搜索结果展示结构优化。".to_string(),
            findings: vec!["自然流量增长依赖页面主题集群和标题匹配。".to_string()],
            recommendations: vec![
                "建立核心词、长尾词和 FAQ 页面的关键词集群。".to_string(),
                "优化标题、描述和结构化数据以改善搜索结果展示。".to_string(),
            ],
            evidence: vec!["原始任务".to_string()],
            risks: vec!["未验证搜索量和收录状态时不能承诺排名结果。".to_string()],
            open_questions: Vec::new(),
        };
        let report = WorkerReport {
            graph_id: TaskGraphId("graph-web-seo".to_string()),
            node_id: TaskNodeId("web".to_string()),
            task_id: TaskId("task-web-seo".to_string()),
            worker_id: WorkerId("worker.web".to_string()),
            role: "web".to_string(),
            content: role_output.summary.clone(),
            role_output: Some(role_output),
            evidence: Vec::new(),
            risks: Vec::new(),
            open_questions: Vec::new(),
            status: WorkerReportStatus::Completed,
        };

        let evaluation = evaluate_report(&report, None, &HashMap::new());

        assert!(evaluation.passed, "{:?}", evaluation.reasons);
    }

    #[test]
    fn evaluator_rejects_claimed_external_search_results() {
        let role_output = RoleWorkOutput {
            summary: "已检索到实时搜索结果显示该关键词排名第一。".to_string(),
            findings: vec!["搜索结果显示当前页面排名第一。".to_string()],
            recommendations: vec!["继续扩大该关键词页面。".to_string()],
            evidence: vec!["原始任务".to_string()],
            risks: vec!["需要验证数据来源。".to_string()],
            open_questions: Vec::new(),
        };
        let report = WorkerReport {
            graph_id: TaskGraphId("graph-web-fabrication".to_string()),
            node_id: TaskNodeId("web".to_string()),
            task_id: TaskId("task-web-fabrication".to_string()),
            worker_id: WorkerId("worker.web".to_string()),
            role: "web".to_string(),
            content: role_output.summary.clone(),
            role_output: Some(role_output),
            evidence: Vec::new(),
            risks: Vec::new(),
            open_questions: Vec::new(),
            status: WorkerReportStatus::Completed,
        };

        let evaluation = evaluate_report(&report, None, &HashMap::new());

        assert!(!evaluation.passed);
        assert!(
            evaluation
                .reasons
                .contains(&"quality fabrication: claims unsupported external evidence".to_string())
        );
    }

    #[test]
    fn planner_can_make_ops_depend_on_design_and_accounting() {
        let graph = plan_task_graph(
            &TaskId("task-9".to_string()),
            "基于数据诊断做详情页设计和预算风险评估，并给运营方案",
            None,
        );

        let design = graph
            .nodes
            .iter()
            .find(|node| node.role == "design")
            .unwrap();
        let accounting = graph
            .nodes
            .iter()
            .find(|node| node.role == "accounting")
            .unwrap();
        let ops = graph.nodes.iter().find(|node| node.role == "ops").unwrap();

        assert!(ops.input_refs.contains(&design.id));
        assert!(ops.input_refs.contains(&accounting.id));
    }

    fn report_from_assignment(assignment: &WorkerAssignment, content: &str) -> WorkerReport {
        WorkerReport {
            graph_id: assignment.graph_id.clone(),
            node_id: assignment.node_id.clone(),
            task_id: assignment.task_id.clone(),
            worker_id: assignment.worker_id.clone(),
            role: assignment.role.clone(),
            content: content.to_string(),
            role_output: valid_role_output_for_role(&assignment.role, content),
            evidence: Vec::new(),
            risks: Vec::new(),
            open_questions: Vec::new(),
            status: WorkerReportStatus::Completed,
        }
    }

    fn valid_role_output_for_role(role: &str, content: &str) -> Option<RoleWorkOutput> {
        if content.trim().is_empty() {
            return None;
        }

        let summary = format!("测试角色产物：{}，已满足当前节点目标。", content.trim());
        let output = match role {
            "data" => RoleWorkOutput {
                summary: summary.clone(),
                findings: vec![
                    "漏斗指标需要拆解".to_string(),
                    "归因口径需要统一".to_string(),
                ],
                recommendations: vec!["按渠道和页面段落复核转化断点。".to_string()],
                evidence: vec!["原始任务".to_string()],
                risks: vec!["数据口径不一致会导致误判。".to_string()],
                open_questions: Vec::new(),
            },
            "design" => RoleWorkOutput {
                summary: summary.clone(),
                findings: vec!["页面首屏承接需要优化".to_string()],
                recommendations: vec!["调整详情页视觉层级和主图卖点。".to_string()],
                evidence: vec!["worker.assignment.input.data".to_string()],
                risks: vec!["缺少真实点击数据时需要小流量验证。".to_string()],
                open_questions: Vec::new(),
            },
            "accounting" => RoleWorkOutput {
                summary: summary.clone(),
                findings: vec!["预算和成本需要绑定 ROI 判断。".to_string()],
                recommendations: vec!["设置预算止损线。".to_string()],
                evidence: vec!["worker.assignment.input.data".to_string()],
                risks: vec!["现金流不足会放大投放风险。".to_string()],
                open_questions: Vec::new(),
            },
            "ops" => RoleWorkOutput {
                summary: summary.clone(),
                findings: vec!["运营目标需要转化链路承接。".to_string()],
                recommendations: vec![
                    "P1 优化首屏卖点。".to_string(),
                    "P2 调整投放人群。".to_string(),
                    "P3 每日复盘转化。".to_string(),
                ],
                evidence: vec![
                    "worker.assignment.input.design".to_string(),
                    "worker.assignment.input.accounting".to_string(),
                    "worker.assignment.input.data".to_string(),
                ],
                risks: vec!["预算消耗需设置止损线。".to_string()],
                open_questions: Vec::new(),
            },
            "chat" => RoleWorkOutput {
                summary: summary.clone(),
                findings: vec!["已基于当前任务直接回答。".to_string()],
                recommendations: Vec::new(),
                evidence: vec!["原始任务".to_string()],
                risks: Vec::new(),
                open_questions: Vec::new(),
            },
            _ => RoleWorkOutput {
                summary,
                findings: vec!["已形成角色判断。".to_string()],
                recommendations: vec!["按角色边界推进下一步。".to_string()],
                evidence: vec!["原始任务".to_string()],
                risks: vec!["需要持续确认边界。".to_string()],
                open_questions: Vec::new(),
            },
        };

        Some(output)
    }
}
