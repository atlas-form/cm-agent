use std::{collections::HashMap, sync::Arc};

use tracing::{info, warn};

use super::{
    CommanderPhase, CommanderState, CommanderTask, EvaluationOutcome, RoutedDecision, TaskGraphRuntime,
    TaskMemory, decision_intent_from_json, plan_task_graph,
};
use crate::{
    cognition::{Cognition, CognitionInput, CognitionResult, Context, Fact, Intent, IntentKind},
    core::{
        messaging::{MessageRx, MessageTx},
        protocol::{
            AgentId, DecisionIntent, Message, MessageContext, MessageId, Payload, SessionEvent,
            TaskId, TaskSpec, WorkerId, WorkerProfile,
        },
    },
    roles::{
        FinalSynthesisPromptInput, RoleAssignment, RoleCollaborationPlan, RoleContribution,
        RolePromptBuilder, RoleRoute, RoleRouteInput, RoleRouter,
    },
};

pub trait CommanderSessionContext: Send + Sync {
    fn get_worker_tx(&self, worker_id: &WorkerId) -> Option<MessageTx>;

    fn list_worker_profiles(&self) -> Vec<WorkerProfile>;
}

pub struct CommanderChannels {
    pub receiver: MessageRx,
    pub sender: MessageTx,
    pub event_tx: Option<tokio::sync::mpsc::Sender<SessionEvent>>,
}

#[derive(Debug, Clone, Copy)]
pub struct CommanderOptions {
    pub fast_route_enabled: bool,
    pub fast_route_min_score: f32,
}

pub struct Commander {
    id: AgentId,
    world_id: AgentId,
    state: CommanderState,
    phase: CommanderPhase,
    current_task: Option<CommanderTask>,
    current_context: MessageContext,
    cognition: Box<dyn Cognition + Send>,
    receiver: MessageRx,
    sender: MessageTx,
    event_tx: Option<tokio::sync::mpsc::Sender<SessionEvent>>,
    memory: TaskMemory,
    session_context: Arc<dyn CommanderSessionContext>,
    role_router: RoleRouter,
    fast_route_enabled: bool,
    fast_route_min_score: f32,
    active_collaboration: Option<RoleCollaborationPlan>,
    active_collaboration_task_id: Option<TaskId>,
    pending_support_workers: Vec<WorkerId>,
    completed_support_workers: Vec<WorkerId>,
    collaboration_outputs: HashMap<WorkerId, RoleContribution>,
    active_task_graph: Option<TaskGraphRuntime>,
}

impl Commander {
    pub fn new(
        id: AgentId,
        world_id: AgentId,
        cognition: Box<dyn Cognition + Send>,
        channels: CommanderChannels,
        session_context: Arc<dyn CommanderSessionContext>,
        role_router: RoleRouter,
        options: CommanderOptions,
    ) -> Self {
        Self {
            id,
            world_id,
            state: CommanderState::Idle,
            phase: CommanderPhase::Idle,
            current_task: None,
            current_context: MessageContext::default(),
            cognition,
            receiver: channels.receiver,
            sender: channels.sender,
            event_tx: channels.event_tx,
            memory: TaskMemory::default(),
            session_context,
            role_router,
            fast_route_enabled: options.fast_route_enabled,
            fast_route_min_score: options.fast_route_min_score,
            active_collaboration: None,
            active_collaboration_task_id: None,
            pending_support_workers: Vec::new(),
            completed_support_workers: Vec::new(),
            collaboration_outputs: HashMap::new(),
            active_task_graph: None,
        }
    }

    pub async fn run(&mut self) {
        while self.state != CommanderState::Shutdown {
            let Some(message) = self.receiver.recv().await else {
                break;
            };
            self.handle_message(message).await;

            while self.state == CommanderState::Running {
                self.runtime_step().await;
            }
        }
    }

    async fn runtime_step(&mut self) {
        match self.phase {
            CommanderPhase::Thinking => self.step_thinking().await,
            CommanderPhase::Idle => {
                self.state = CommanderState::Idle;
            }
        }
    }

    async fn handle_message(&mut self, message: Message) {
        let context = message.context.clone();
        match message.payload {
            Payload::HumanCommand { task } => self.on_human_command(task, context),
            Payload::WorkerReportStarted { worker_id, task_id } => {
                self.memory
                    .push_progress(format!("worker {} started {}", worker_id.0, task_id.0));
                self.emit_event(SessionEvent::WorkerStarted {
                    session_id: self.session_id_from_context(&context),
                    worker_id: worker_id.clone(),
                    task_id: task_id.clone(),
                });
                if self.is_collaboration_worker(&worker_id) {
                    self.emit_event(SessionEvent::CollaborationWorkerStarted {
                        session_id: self.session_id_from_context(&context),
                        worker_id,
                        task_id,
                    });
                }
            }
            Payload::WorkerReportFinished {
                worker_id,
                task_id,
                output,
            } => {
                self.memory
                    .push_progress(format!("worker {} finished {}", worker_id.0, task_id.0));
                self.emit_event(SessionEvent::WorkerFinished {
                    session_id: self.session_id_from_context(&context),
                    worker_id: worker_id.clone(),
                    task_id: task_id.clone(),
                });
                if self
                    .handle_collaboration_worker_finished(
                        &worker_id,
                        &task_id,
                        output.as_deref(),
                        &context,
                    )
                    .await
                {
                    return;
                }
                let _ = self.sender.send(Message::new_with_context(
                    next_message_id(),
                    context,
                    self.id.clone(),
                    self.world_id.clone(),
                    Payload::Text {
                        content: final_worker_message(&task_id, output.as_deref()),
                    },
                ));
                self.phase = CommanderPhase::Idle;
                self.state = CommanderState::Idle;
            }
            Payload::WorkerReportFailed {
                worker_id,
                task_id,
                reason,
            } => {
                self.memory.push_progress(format!(
                    "worker {} failed {}: {}",
                    worker_id.0, task_id.0, reason
                ));
                self.emit_event(SessionEvent::WorkerFinished {
                    session_id: self.session_id_from_context(&context),
                    worker_id,
                    task_id: task_id.clone(),
                });
                let _ = self.sender.send(Message::new_with_context(
                    next_message_id(),
                    context,
                    self.id.clone(),
                    self.world_id.clone(),
                    Payload::Text {
                        content: format!("任务失败: {} ({reason})", task_id.0),
                    },
                ));
                self.phase = CommanderPhase::Idle;
                self.state = CommanderState::Idle;
            }
            Payload::WorkerReport { report } => {
                self.handle_task_graph_report(report, &context).await;
            }
            Payload::Control { signal, .. } => {
                if matches!(signal, crate::core::protocol::ControlSignal::Shutdown) {
                    self.state = CommanderState::Shutdown;
                }
            }
            _ => {}
        }
    }

    fn on_human_command(&mut self, task: TaskSpec, context: MessageContext) {
        let incoming_task = CommanderTask {
            id: task.id.0,
            description: task.description,
        };

        self.memory.clear();
        self.active_collaboration = None;
        self.active_collaboration_task_id = None;
        self.pending_support_workers.clear();
        self.completed_support_workers.clear();
        self.collaboration_outputs.clear();
        self.active_task_graph = None;
        self.memory.push_progress("human command received");
        self.memory
            .set_state("incoming_task_id", incoming_task.id.clone());
        self.current_task = Some(incoming_task);
        self.current_context = context;
        self.phase = CommanderPhase::Thinking;
        self.state = CommanderState::Running;
    }

    async fn step_thinking(&mut self) {
        let role_route = self.current_role_route();
        if self.fast_route_enabled && self.try_task_graph_route(role_route.as_ref()).await {
            self.phase = CommanderPhase::Idle;
            self.state = CommanderState::Idle;
            return;
        }
        if self.fast_route_enabled && self.try_fast_route(role_route.as_ref()) {
            self.phase = CommanderPhase::Idle;
            self.state = CommanderState::Idle;
            return;
        }

        let input = self.build_cognition_input(role_route.as_ref());

        let routed = match self.cognition.evaluate(input).await {
            CognitionResult::Success(output) => {
                info!(output = ?output, "commander cognition output");
                decision_intent_from_json(&output, self.current_task.clone())
            }
            CognitionResult::Failure(failure) => {
                warn!(error = %failure.description, "commander cognition failure");
                self.memory
                    .set_error(format!("cognition failure: {}", failure.description));
                RoutedDecision {
                    intent: DecisionIntent::Ignore,
                    target_worker_id: None,
                    clarification: None,
                }
            }
        };

        match routed.intent {
            DecisionIntent::ExecuteTask { task } => {
                let target_worker = routed
                    .target_worker_id
                    .or_else(|| role_route.as_ref().map(primary_worker_id));
                self.prepare_collaboration(role_route.as_ref(), target_worker.as_ref());
                if !self.dispatch_task_to_worker(task, target_worker) {
                    let _ = self.sender.send(Message::new_with_context(
                        next_message_id(),
                        self.current_context.clone(),
                        self.id.clone(),
                        self.world_id.clone(),
                        Payload::Text {
                            content: "任务分发失败：未找到可用 worker".to_string(),
                        },
                    ));
                }
            }
            DecisionIntent::Ignore => {
                let content = routed
                    .clarification
                    .map(|question| format!("需要补充信息后再路由：{question}"))
                    .unwrap_or_else(|| "已评估当前输入：本轮无需执行新任务。".to_string());
                let _ = self.sender.send(Message::new_with_context(
                    next_message_id(),
                    self.current_context.clone(),
                    self.id.clone(),
                    self.world_id.clone(),
                    Payload::Text { content },
                ));
            }
            DecisionIntent::IgnoreNewTask => {
                let _ = self.sender.send(Message::new_with_context(
                    next_message_id(),
                    self.current_context.clone(),
                    self.id.clone(),
                    self.world_id.clone(),
                    Payload::Text {
                        content: "已评估该请求：当前策略是暂不接收新任务。".to_string(),
                    },
                ));
            }
            DecisionIntent::KeepCurrentTask => {
                let _ = self.sender.send(Message::new_with_context(
                    next_message_id(),
                    self.current_context.clone(),
                    self.id.clone(),
                    self.world_id.clone(),
                    Payload::Text {
                        content: "已评估当前输入：继续保持当前任务。".to_string(),
                    },
                ));
            }
            DecisionIntent::ReplaceCurrentTask { task } => {
                let _ = self.sender.send(Message::new_with_context(
                    next_message_id(),
                    self.current_context.clone(),
                    self.id.clone(),
                    self.world_id.clone(),
                    Payload::Text {
                        content: format!("建议替换为新任务：{}", task.description),
                    },
                ));
            }
            DecisionIntent::StrategyHint { hint } => {
                let _ = self.sender.send(Message::new_with_context(
                    next_message_id(),
                    self.current_context.clone(),
                    self.id.clone(),
                    self.world_id.clone(),
                    Payload::Text {
                        content: format!("策略建议：{hint}"),
                    },
                ));
            }
        }

        self.phase = CommanderPhase::Idle;
        self.state = CommanderState::Idle;
    }

    fn dispatch_task_to_worker(&self, task: TaskSpec, target_worker: Option<WorkerId>) -> bool {
        let target_worker = target_worker.unwrap_or_else(|| WorkerId("worker.chat".to_string()));
        let Some(worker_tx) = self.session_context.get_worker_tx(&target_worker) else {
            warn!(worker_id = %target_worker.0, "no worker tx available for dispatch");
            return false;
        };

        let task_message = Message::new_with_context(
            next_message_id(),
            self.current_context.clone(),
            self.id.clone(),
            AgentId(target_worker.0.clone()),
            Payload::Text {
                content: format!("task:start:{}", task.description),
            },
        );
        worker_tx.send(task_message).is_ok()
    }

    fn dispatch_assignment_to_worker(
        &self,
        assignment: crate::core::protocol::WorkerAssignment,
    ) -> bool {
        let Some(worker_tx) = self.session_context.get_worker_tx(&assignment.worker_id) else {
            warn!(
                worker_id = %assignment.worker_id.0,
                "no worker tx available for task graph assignment"
            );
            return false;
        };

        let task_message = Message::new_with_context(
            next_message_id(),
            self.current_context.clone(),
            self.id.clone(),
            AgentId(assignment.worker_id.0.clone()),
            Payload::WorkerAssignment { assignment },
        );
        worker_tx.send(task_message).is_ok()
    }

    async fn try_task_graph_route(&mut self, role_route: Option<&RoleRoute>) -> bool {
        let Some(current_task) = self.current_task.clone() else {
            return false;
        };
        if role_route
            .map(primary_route_score)
            .is_some_and(|score| score < self.fast_route_min_score)
        {
            return false;
        }

        let task_id = TaskId(current_task.id);
        let graph = plan_task_graph(&task_id, &current_task.description, role_route);
        if graph
            .nodes
            .iter()
            .any(|node| self.session_context.get_worker_tx(&node.worker_id).is_none())
        {
            return false;
        }

        self.memory.push_progress(format!(
            "task graph planned: {} nodes",
            graph.nodes.len()
        ));
        self.emit_event(SessionEvent::TaskGraphPlanned {
            session_id: self.session_id_from_context(&self.current_context),
            graph: graph.clone(),
        });
        self.active_collaboration = None;
        self.active_collaboration_task_id = None;
        self.pending_support_workers.clear();
        self.completed_support_workers.clear();
        self.collaboration_outputs.clear();
        let runtime = TaskGraphRuntime::new(graph);
        self.schedule_task_graph_runtime(runtime).await;
        true
    }

    async fn handle_task_graph_report(
        &mut self,
        report: crate::core::protocol::WorkerReport,
        context: &MessageContext,
    ) {
        self.memory.push_progress(format!(
            "task graph node {} reported by {}",
            report.node_id.0, report.worker_id.0
        ));
        self.emit_event(SessionEvent::WorkerFinished {
            session_id: self.session_id_from_context(context),
            worker_id: report.worker_id.clone(),
            task_id: report.task_id.clone(),
        });
        self.emit_event(SessionEvent::TaskNodeReported {
            session_id: self.session_id_from_context(context),
            graph_id: report.graph_id.clone(),
            node_id: report.node_id.clone(),
            worker_id: report.worker_id.clone(),
        });

        let Some(mut runtime) = self.active_task_graph.take() else {
            return;
        };
        let graph_id = runtime.graph.graph_id.clone();
        let worker_id = report.worker_id.clone();
        let node_id = report.node_id.clone();
        let outcome = runtime.apply_report(report);

        match outcome {
            EvaluationOutcome::Passed(evaluation) => {
                self.emit_event(SessionEvent::TaskNodeEvaluated {
                    session_id: self.session_id_from_context(context),
                    graph_id: graph_id.clone(),
                    evaluation,
                });
                self.emit_event(SessionEvent::TaskNodePassed {
                    session_id: self.session_id_from_context(context),
                    graph_id,
                    node_id,
                });
            }
            EvaluationOutcome::Rework {
                evaluation,
                instruction,
            } => {
                let attempt = runtime.attempts.get(&node_id).copied().unwrap_or(1) + 1;
                self.emit_event(SessionEvent::TaskNodeEvaluated {
                    session_id: self.session_id_from_context(context),
                    graph_id: graph_id.clone(),
                    evaluation,
                });
                self.emit_event(SessionEvent::TaskNodeReworkRequested {
                    session_id: self.session_id_from_context(context),
                    graph_id,
                    node_id,
                    worker_id,
                    attempt,
                    instruction,
                });
            }
            EvaluationOutcome::Failed(evaluation) => {
                let reason = evaluation.reasons.join("; ");
                self.emit_event(SessionEvent::TaskNodeEvaluated {
                    session_id: self.session_id_from_context(context),
                    graph_id: graph_id.clone(),
                    evaluation,
                });
                self.emit_event(SessionEvent::TaskNodeFailed {
                    session_id: self.session_id_from_context(context),
                    graph_id,
                    node_id,
                    reason,
                });
            }
        }

        self.schedule_task_graph_runtime(runtime).await;
    }

    async fn schedule_task_graph_runtime(&mut self, mut runtime: TaskGraphRuntime) {
        if runtime.is_finished() {
            self.finish_task_graph(runtime).await;
            return;
        }

        let ready_nodes = runtime.ready_nodes();
        for node in ready_nodes {
            self.emit_event(SessionEvent::TaskNodeReady {
                session_id: self.session_id_from_context(&self.current_context),
                graph_id: runtime.graph.graph_id.clone(),
                node_id: node.id.clone(),
                worker_id: node.worker_id.clone(),
            });
            let assignment = runtime.build_assignment(&node);
            if self.dispatch_assignment_to_worker(assignment.clone()) {
                runtime.mark_running(&assignment);
                self.emit_event(SessionEvent::WorkerStarted {
                    session_id: self.session_id_from_context(&self.current_context),
                    worker_id: assignment.worker_id.clone(),
                    task_id: assignment.task_id.clone(),
                });
                self.emit_event(SessionEvent::TaskNodeStarted {
                    session_id: self.session_id_from_context(&self.current_context),
                    graph_id: assignment.graph_id,
                    node_id: assignment.node_id,
                    worker_id: assignment.worker_id,
                    attempt: assignment.attempt,
                });
            }
        }

        self.active_task_graph = Some(runtime);
    }

    async fn finish_task_graph(&mut self, runtime: TaskGraphRuntime) {
        let content = runtime.synthesize();
        self.emit_event(SessionEvent::TaskGraphFinished {
            session_id: self.session_id_from_context(&self.current_context),
            graph_id: runtime.graph.graph_id.clone(),
        });
        let _ = self.sender.send(Message::new_with_context(
            next_message_id(),
            self.current_context.clone(),
            self.id.clone(),
            self.world_id.clone(),
            Payload::Text { content },
        ));
        self.active_task_graph = None;
        self.phase = CommanderPhase::Idle;
        self.state = CommanderState::Idle;
    }

    fn try_fast_route(&mut self, role_route: Option<&RoleRoute>) -> bool {
        let Some(role_route) = role_route else {
            return false;
        };
        if primary_route_score(role_route) < self.fast_route_min_score {
            return false;
        }
        let Some(current_task) = self.current_task.clone() else {
            return false;
        };

        let target_worker = primary_worker_id(role_route);
        if self.session_context.get_worker_tx(&target_worker).is_none() {
            return false;
        }

        let task = TaskSpec {
            id: TaskId(current_task.id),
            description: current_task.description,
        };
        self.prepare_collaboration(Some(role_route), Some(&target_worker));
        if !self.dispatch_task_to_worker(task, Some(target_worker)) {
            let _ = self.sender.send(Message::new_with_context(
                next_message_id(),
                self.current_context.clone(),
                self.id.clone(),
                self.world_id.clone(),
                Payload::Text {
                    content: "任务分发失败：未找到可用 worker".to_string(),
                },
            ));
        }

        true
    }

    fn prepare_collaboration(
        &mut self,
        role_route: Option<&RoleRoute>,
        target_worker: Option<&WorkerId>,
    ) {
        let Some(role_route) = role_route else {
            return;
        };
        let plan = RoleCollaborationPlan::from_route(role_route);
        if target_worker != Some(&plan.primary.worker_id) {
            return;
        }

        self.memory.set_state(
            "role_route.primary_runtime_role",
            plan.primary.runtime_role.clone(),
        );
        self.memory.set_state(
            "role_route.support_runtime_roles",
            plan.support
                .iter()
                .map(|role| role.runtime_role.clone())
                .collect::<Vec<_>>()
                .join(", "),
        );
        if plan.support.is_empty() {
            return;
        }
        self.emit_event(SessionEvent::CollaborationStarted {
            session_id: self.session_id_from_context(&self.current_context),
            primary_worker_id: plan.primary.worker_id.clone(),
            support_worker_ids: plan
                .support
                .iter()
                .map(|assignment| assignment.worker_id.clone())
                .collect(),
        });
        self.active_collaboration = Some(plan);
        self.active_collaboration_task_id = None;
        self.pending_support_workers.clear();
        self.completed_support_workers.clear();
        self.collaboration_outputs.clear();
    }

    async fn handle_collaboration_worker_finished(
        &mut self,
        worker_id: &WorkerId,
        task_id: &TaskId,
        output: Option<&str>,
        context: &MessageContext,
    ) -> bool {
        let Some(plan) = self.active_collaboration.clone() else {
            return false;
        };

        if worker_id == &plan.primary.worker_id && !plan.support.is_empty() {
            self.active_collaboration_task_id = Some(task_id.clone());
            if let Some(output) = output {
                self.collaboration_outputs.insert(
                    worker_id.clone(),
                    build_contribution(&plan.primary, task_id, output),
                );
            }
            self.emit_event(SessionEvent::CollaborationWorkerFinished {
                session_id: self.session_id_from_context(context),
                worker_id: worker_id.clone(),
                task_id: task_id.clone(),
                content: output.map(ToString::to_string),
            });
            self.dispatch_support_workers(&plan, task_id).await;
            return true;
        }

        if self
            .pending_support_workers
            .iter()
            .any(|item| item == worker_id)
        {
            self.pending_support_workers
                .retain(|item| item != worker_id);
            self.completed_support_workers.push(worker_id.clone());
            if let Some(output) = output
                && let Some(assignment) = plan.assignment_for(worker_id)
            {
                self.collaboration_outputs.insert(
                    worker_id.clone(),
                    build_contribution(assignment, task_id, output),
                );
            }
            self.emit_event(SessionEvent::CollaborationWorkerFinished {
                session_id: self.session_id_from_context(context),
                worker_id: worker_id.clone(),
                task_id: task_id.clone(),
                content: output.map(ToString::to_string),
            });
            if self.pending_support_workers.is_empty() {
                let primary_task_id = self
                    .active_collaboration_task_id
                    .clone()
                    .unwrap_or_else(|| task_id.clone());
                self.send_collaboration_done(context, &primary_task_id)
                    .await;
            }
            return true;
        }

        false
    }

    async fn dispatch_support_workers(
        &mut self,
        plan: &RoleCollaborationPlan,
        primary_task_id: &TaskId,
    ) {
        self.pending_support_workers = plan
            .support
            .iter()
            .map(|assignment| assignment.worker_id.clone())
            .collect();

        let mut dispatched = Vec::new();
        for assignment in &plan.support {
            let task = TaskSpec {
                id: TaskId(format!(
                    "{}:support:{}",
                    primary_task_id.0, assignment.runtime_role
                )),
                description: format!(
                    "作为{}支持角色，基于主角色结果补充本岗位判断。",
                    assignment.runtime_role
                ),
            };
            if self.dispatch_task_to_worker(task, Some(assignment.worker_id.clone())) {
                dispatched.push(assignment.worker_id.clone());
            }
        }

        self.pending_support_workers
            .retain(|worker_id| dispatched.iter().any(|item| item == worker_id));
        if self.pending_support_workers.is_empty() {
            let context = self.current_context.clone();
            self.send_collaboration_done(&context, primary_task_id)
                .await;
        }
    }

    async fn send_collaboration_done(&mut self, context: &MessageContext, task_id: &TaskId) {
        let support_workers = self
            .completed_support_workers
            .iter()
            .map(|worker| worker.0.clone())
            .collect::<Vec<_>>()
            .join(", ");
        let content = if support_workers.is_empty() {
            self.active_collaboration
                .as_ref()
                .and_then(|plan| self.collaboration_outputs.get(&plan.primary.worker_id))
                .map(|contribution| contribution.content.clone())
                .unwrap_or_else(|| format!("任务已完成: {}", task_id.0))
        } else {
            self.synthesize_collaboration_output(task_id, &support_workers)
        };
        let _ = self.sender.send(Message::new_with_context(
            next_message_id(),
            context.clone(),
            self.id.clone(),
            self.world_id.clone(),
            Payload::Text { content },
        ));
        self.emit_event(SessionEvent::CollaborationFinished {
            session_id: self.session_id_from_context(context),
        });
        self.active_collaboration = None;
        self.active_collaboration_task_id = None;
        self.pending_support_workers.clear();
        self.completed_support_workers.clear();
        self.collaboration_outputs.clear();
        self.phase = CommanderPhase::Idle;
        self.state = CommanderState::Idle;
    }

    fn format_collaboration_outputs(&self) -> String {
        let Some(plan) = &self.active_collaboration else {
            return "角色贡献：none".to_string();
        };

        let mut lines = Vec::new();
        if let Some(contribution) = self.collaboration_outputs.get(&plan.primary.worker_id) {
            lines.push(format!(
                "primary {}: {}",
                plan.primary.runtime_role, contribution.content
            ));
        }
        for assignment in &plan.support {
            if let Some(contribution) = self.collaboration_outputs.get(&assignment.worker_id) {
                lines.push(format!(
                    "support {}: {}",
                    assignment.runtime_role, contribution.content
                ));
            }
        }

        if lines.is_empty() {
            "角色贡献：none".to_string()
        } else {
            format!("角色贡献：\n{}", lines.join("\n"))
        }
    }

    fn synthesize_collaboration_output(&self, task_id: &TaskId, support_workers: &str) -> String {
        let task = self
            .current_task
            .as_ref()
            .map(|task| task.description.clone())
            .unwrap_or_default();
        let Some(plan) = &self.active_collaboration else {
            return RolePromptBuilder::build_final_synthesis_prompt(&FinalSynthesisPromptInput {
                task_id: task_id.clone(),
                task,
                support_workers: support_workers.to_string(),
                contributions: Vec::new(),
            });
        };

        let mut contributions = Vec::new();
        if let Some(contribution) = self.collaboration_outputs.get(&plan.primary.worker_id) {
            contributions.push(contribution.clone());
        }
        for assignment in &plan.support {
            if let Some(contribution) = self.collaboration_outputs.get(&assignment.worker_id) {
                contributions.push(contribution.clone());
            }
        }

        RolePromptBuilder::build_final_synthesis_prompt(&FinalSynthesisPromptInput {
            task_id: task_id.clone(),
            task,
            support_workers: support_workers.to_string(),
            contributions,
        })
    }

    fn is_collaboration_worker(&self, worker_id: &WorkerId) -> bool {
        self.active_collaboration
            .as_ref()
            .map(|plan| plan.assignment_for(worker_id).is_some())
            .unwrap_or(false)
    }

    fn session_id_from_context(
        &self,
        context: &MessageContext,
    ) -> crate::core::protocol::SessionId {
        context
            .session_id
            .clone()
            .unwrap_or_else(|| crate::core::protocol::SessionId("unknown-session".to_string()))
    }

    fn emit_event(&self, event: SessionEvent) {
        if let Some(event_tx) = &self.event_tx {
            let _ = event_tx.try_send(event);
        }
    }

    fn build_cognition_input(&self, role_route: Option<&RoleRoute>) -> CognitionInput {
        let intent = Intent {
            id: self
                .current_task
                .as_ref()
                .map(|task| task.id.clone())
                .unwrap_or_else(|| "commander-idle".to_string()),
            kind: IntentKind::Decision,
            description: self
                .current_task
                .as_ref()
                .map(|task| task.description.clone())
                .unwrap_or_else(|| "evaluate current situation".to_string()),
        };

        let mut metadata = HashMap::new();
        metadata.insert("state".to_string(), format!("{:?}", self.state));
        metadata.insert("phase".to_string(), format!("{:?}", self.phase));
        if let Some(role_route) = role_route {
            metadata.insert(
                "role_route.primary_role".to_string(),
                role_route.primary_role.0.clone(),
            );
            metadata.insert(
                "role_route.primary_runtime_role".to_string(),
                role_route.primary_runtime_role.clone(),
            );
            metadata.insert(
                "role_route.support_roles".to_string(),
                role_route
                    .support_roles
                    .iter()
                    .map(|role| role.0.clone())
                    .collect::<Vec<_>>()
                    .join(", "),
            );
        }
        let worker_profiles = self.session_context.list_worker_profiles();
        metadata.insert(
            "available_workers.count".to_string(),
            worker_profiles.len().to_string(),
        );
        for (index, profile) in worker_profiles.iter().enumerate() {
            metadata.insert(
                format!("available_workers.{index}.worker_id"),
                profile.worker_id.0.clone(),
            );
            metadata.insert(
                format!("available_workers.{index}.agent_id"),
                profile.agent_id.clone(),
            );
            metadata.insert(
                format!("available_workers.{index}.capabilities"),
                profile.capabilities.join(", "),
            );
            metadata.insert(
                format!("available_workers.{index}.constraints"),
                profile.constraints.join(", "),
            );
            metadata.insert(
                format!("available_workers.{index}.status"),
                profile.status.clone(),
            );
        }
        for (key, value) in &self.memory.state {
            metadata.insert(format!("memory.{key}"), value.clone());
        }

        let mut facts = self
            .memory
            .progress
            .iter()
            .map(|entry| Fact {
                source: "commander.memory".to_string(),
                content: entry.clone(),
                reliability: 1.0,
            })
            .collect::<Vec<_>>();
        facts.extend(worker_profiles.iter().map(|profile| Fact {
            source: "session_context.worker_catalog".to_string(),
            content: profile.summary_line(),
            reliability: 1.0,
        }));

        CognitionInput {
            intent,
            context: Context { facts, metadata },
        }
    }

    fn current_role_route(&self) -> Option<RoleRoute> {
        let task = self.current_task.as_ref()?;
        Some(self.role_router.route(RoleRouteInput {
            message: task.description.clone(),
            domain_id: Some("domain.general".to_string()),
            action: None,
            max_roles: 3,
        }))
    }
}

fn primary_worker_id(role_route: &RoleRoute) -> WorkerId {
    WorkerId(format!("worker.{}", role_route.primary_runtime_role))
}

fn primary_route_score(role_route: &RoleRoute) -> f32 {
    role_route
        .scores
        .iter()
        .find(|score| score.role_id == role_route.primary_role)
        .map(|score| score.score)
        .unwrap_or(0.0)
}

fn final_worker_message(task_id: &TaskId, output: Option<&str>) -> String {
    output
        .filter(|value| !value.trim().is_empty())
        .map(extract_worker_content)
        .unwrap_or_else(|| format!("任务已完成: {}", task_id.0))
}

fn build_contribution(
    assignment: &RoleAssignment,
    task_id: &TaskId,
    output: &str,
) -> RoleContribution {
    RoleContribution::new(assignment, task_id.clone(), extract_worker_content(output))
}

fn extract_worker_content(output: &str) -> String {
    let trimmed = output.trim();
    let Ok(value) = serde_json::from_str::<serde_json::Value>(trimmed) else {
        return trimmed.to_string();
    };

    value
        .pointer("/decision/action/parameters/answer")
        .and_then(|value| value.as_str())
        .or_else(|| {
            value
                .pointer("/role_output")
                .and_then(|value| value.as_str())
        })
        .or_else(|| {
            value
                .pointer("/rationale/primary")
                .and_then(|value| value.as_str())
        })
        .map(ToString::to_string)
        .unwrap_or_else(|| trimmed.to_string())
}

fn next_message_id() -> MessageId {
    use std::{
        sync::atomic::{AtomicU64, Ordering},
        time::{SystemTime, UNIX_EPOCH},
    };

    static NEXT_MESSAGE_COUNTER: AtomicU64 = AtomicU64::new(1);

    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    let sequence = NEXT_MESSAGE_COUNTER.fetch_add(1, Ordering::Relaxed);

    MessageId(format!("msg-{millis}-{sequence}"))
}
