use std::collections::{HashMap, HashSet};

use crate::{
    core::protocol::{
        Evaluation, RoleWorkOutput, TaskGraph, TaskGraphId, TaskId, TaskNode, TaskNodeId,
        WorkerAssignment, WorkerId, WorkerReport, WorkerReportStatus,
    },
    roles::RoleRoute,
};

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
        let evaluation = evaluate_report(&report);
        self.reports.insert(report.node_id.clone(), report.clone());

        if evaluation.passed {
            self.rework_instructions.remove(&report.node_id);
            self.states
                .insert(report.node_id.clone(), TaskNodeRuntimeState::Passed);
            return EvaluationOutcome::Passed(evaluation);
        }

        let Some(node) = self.graph.nodes.iter().find(|node| node.id == report.node_id) else {
            self.states.insert(
                report.node_id.clone(),
                TaskNodeRuntimeState::Failed("node not found".to_string()),
            );
            return EvaluationOutcome::Failed(evaluation);
        };
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
                    if content.is_empty() { "无内容" } else { content }
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
                Some(TaskNodeRuntimeState::Failed(reason)) => {
                    Some(format!("- {}({}) failed: {}", node.title, node.role, reason))
                }
                Some(TaskNodeRuntimeState::Skipped(reason)) => {
                    Some(format!("- {}({}) skipped: {}", node.title, node.role, reason))
                }
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
    let lower = task.to_lowercase();
    let graph_id = TaskGraphId(format!("graph-{}", task_id.0));
    let max_attempts = 2;
    let mut nodes = Vec::new();

    if contains_any(&lower, &[
        "详情页", "设计", "预算", "风险", "归因", "漏斗", "数据", "转化率", "指标",
    ]) {
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
        if contains_any(&lower, &["运营", "调整", "方案", "策略"]) {
            let mut ops_inputs = Vec::new();
            if nodes.iter().any(|node| node.role == "design") {
                ops_inputs.push("design");
            }
            if nodes.iter().any(|node| node.role == "accounting") {
                ops_inputs.push("accounting");
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
        if contains_any(&lower, &["文案", "脚本", "标题", "内容"]) {
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
    } else if contains_any(&lower, &["代码", "rust", "python", "架构", "实现", "bug"]) {
        nodes.push(node(
            "engineering",
            "engineering",
            "工程判断",
            format!("给出工程分析和实现建议：{task}"),
            Vec::new(),
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

fn evaluate_report(report: &WorkerReport) -> Evaluation {
    let content = report.content.trim();
    let mut reasons = Vec::new();
    if report.status == WorkerReportStatus::Failed {
        reasons.push("worker reported failed status".to_string());
    }
    if content.is_empty() {
        reasons.push("content is empty".to_string());
    }
    if !report.open_questions.is_empty() {
        reasons.push("open questions remain".to_string());
    }
    if let Some(role_output) = &report.role_output {
        reasons.extend(evaluate_role_output(&report.role, role_output));
    }
    let lower = content.to_lowercase();
    if contains_any(&lower, &["todo", "tbd", "placeholder", "无法处理", "不知道", "无数据"]) {
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

fn evaluate_role_output(role: &str, output: &RoleWorkOutput) -> Vec<String> {
    let mut reasons = Vec::new();
    let role = role.trim();

    if output.summary.trim().is_empty() {
        reasons.push("role output summary is empty".to_string());
    }
    if !output.open_questions.is_empty() {
        reasons.push("role output has open questions".to_string());
    }
    if output
        .summary
        .contains("已调用")
        || output.findings.iter().any(|item| item.contains("已调用"))
        || output.recommendations.iter().any(|item| item.contains("已调用"))
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
        "ops" => {
            if output.recommendations.len() < 3 {
                reasons.push("ops role should provide at least three recommendations".to_string());
            }
        }
        "design" => {
            if output.recommendations.is_empty() {
                reasons.push("design role should provide visual or page recommendations".to_string());
            }
            if !contains_any(
                &format!(
                    "{} {}",
                    output.summary,
                    output.recommendations.join(" ")
                ),
                &["视觉", "页面", "素材", "版式", "主图", "详情页", "设计"],
            ) {
                reasons.push("design role output lacks design-specific content".to_string());
            }
        }
        "accounting" => {
            if output.risks.is_empty() {
                reasons.push("accounting role should provide budget or financial risks".to_string());
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
        }
        "service" => {
            if output.recommendations.is_empty() {
                reasons.push("service role should provide scripts, SOP, or escalation steps".to_string());
            }
        }
        "engineering" => {
            if output.risks.is_empty() {
                reasons.push("engineering role should provide technical risks or rollback boundaries".to_string());
            }
        }
        "web" => {
            if output.recommendations.is_empty() {
                reasons.push("web role should provide SEO, keyword, or conversion recommendations".to_string());
            }
        }
        _ => {}
    }

    reasons
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
        let design = graph.nodes.iter().find(|node| node.role == "design").unwrap();
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
        let design = graph.nodes.iter().find(|node| node.role == "design").unwrap();
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
    fn scheduler_returns_only_one_ready_node_per_worker() {
        let graph = TaskGraph {
            graph_id: TaskGraphId("graph-same-worker".to_string()),
            root_task: "same worker".to_string(),
            nodes: vec![
                node("chat-a", "chat", "直接回答 A", "A".to_string(), Vec::new(), 2),
                node("chat-b", "chat", "直接回答 B", "B".to_string(), Vec::new(), 2),
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
        report.open_questions = vec!["缺少业务指标口径".to_string()];

        let outcome = runtime.apply_report(report);

        assert!(matches!(outcome, EvaluationOutcome::Rework { .. }));
        assert!(matches!(
            runtime.states.get(&node.id),
            Some(TaskNodeRuntimeState::Pending)
        ));
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
    fn planner_can_make_ops_depend_on_design_and_accounting() {
        let graph = plan_task_graph(
            &TaskId("task-9".to_string()),
            "基于数据诊断做详情页设计和预算风险评估，并给运营方案",
            None,
        );

        let design = graph.nodes.iter().find(|node| node.role == "design").unwrap();
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
            role_output: None,
            evidence: Vec::new(),
            risks: Vec::new(),
            open_questions: Vec::new(),
            status: WorkerReportStatus::Completed,
        }
    }
}
