use std::{collections::HashMap, thread, time::Duration};

use agent_core::{
    messaging::{MessageReceive, MessageSend},
    protocol::{AgentId, DecisionIntent, Message, MessageId, Payload, TaskSpec, WorkerId},
};
use cognition::{
    Cognition, CognitionInput, CognitionResult, Context, Fact, Intent, IntentKind,
};
use futures::executor::block_on;
use tracing::{info, warn};
use world::{get_worker_tx, list_worker_profiles};

use super::{CommanderPhase, CommanderState, CommanderTask, RoutedDecision, TaskMemory, decision_intent_from_json};

pub struct Commander {
    id: AgentId,
    world_id: AgentId,
    state: CommanderState,
    phase: CommanderPhase,
    current_task: Option<CommanderTask>,
    cognition: Box<dyn Cognition + Send>,
    receiver: Box<dyn MessageReceive + Send>,
    sender: Box<dyn MessageSend + Send>,
    memory: TaskMemory,
}

impl Commander {
    pub fn new(
        id: AgentId,
        world_id: AgentId,
        cognition: Box<dyn Cognition + Send>,
        receiver: Box<dyn MessageReceive + Send>,
        sender: Box<dyn MessageSend + Send>,
    ) -> Self {
        Self {
            id,
            world_id,
            state: CommanderState::Idle,
            phase: CommanderPhase::Idle,
            current_task: None,
            cognition,
            receiver,
            sender,
            memory: TaskMemory::default(),
        }
    }

    pub fn run(&mut self) {
        while self.state != CommanderState::Shutdown {
            let had_messages = self.process_messages();

            if self.state == CommanderState::Running {
                self.runtime_step();
            } else if !had_messages {
                thread::sleep(Duration::from_millis(10));
            }
        }
    }

    fn runtime_step(&mut self) {
        match self.phase {
            CommanderPhase::Thinking => self.step_thinking(),
            CommanderPhase::Idle => {
                self.state = CommanderState::Idle;
            }
        }
    }

    fn process_messages(&mut self) -> bool {
        let mut handled = 0usize;

        while let Some(message) = self.receiver.get() {
            handled += 1;
            self.handle_message(message);
        }

        handled > 0
    }

    fn handle_message(&mut self, message: Message) {
        match message.payload {
            Payload::HumanCommand { task } => self.on_human_command(task),
            Payload::WorkerReportStarted { worker_id, task_id } => {
                self.memory
                    .push_progress(format!("worker {} started {}", worker_id.0, task_id.0));
                self.sender.send(Message::new(
                    next_message_id(),
                    self.id.clone(),
                    self.world_id.clone(),
                    Payload::Text {
                        content: format!("任务开始执行: {}", task_id.0),
                    },
                ));
            }
            Payload::WorkerReportFinished { worker_id, task_id } => {
                self.memory
                    .push_progress(format!("worker {} finished {}", worker_id.0, task_id.0));
                self.sender.send(Message::new(
                    next_message_id(),
                    self.id.clone(),
                    self.world_id.clone(),
                    Payload::Text {
                        content: format!("任务已完成: {}", task_id.0),
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
                self.sender.send(Message::new(
                    next_message_id(),
                    self.id.clone(),
                    self.world_id.clone(),
                    Payload::Text {
                        content: format!("任务失败: {} ({reason})", task_id.0),
                    },
                ));
                self.phase = CommanderPhase::Idle;
                self.state = CommanderState::Idle;
            }
            Payload::Control { signal, .. } => {
                if matches!(signal, agent_core::protocol::ControlSignal::Shutdown) {
                    self.state = CommanderState::Shutdown;
                }
            }
            _ => {}
        }
    }

    fn on_human_command(&mut self, task: TaskSpec) {
        let incoming_task = CommanderTask {
            id: task.id.0,
            description: task.description,
        };

        self.memory.clear();
        self.memory.push_progress("human command received");
        self.memory
            .set_state("incoming_task_id", incoming_task.id.clone());
        self.current_task = Some(incoming_task);
        self.phase = CommanderPhase::Thinking;
        self.state = CommanderState::Running;
    }

    fn step_thinking(&mut self) {
        let input = self.build_cognition_input();

        let routed = match block_on(self.cognition.evaluate(input)) {
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
                if !self.dispatch_task_to_worker(task, routed.target_worker_id) {
                    self.sender.send(Message::new(
                        next_message_id(),
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
                self.sender.send(Message::new(
                    next_message_id(),
                    self.id.clone(),
                    self.world_id.clone(),
                    Payload::Text { content },
                ));
            }
            DecisionIntent::IgnoreNewTask => {
                self.sender.send(Message::new(
                    next_message_id(),
                    self.id.clone(),
                    self.world_id.clone(),
                    Payload::Text {
                        content: "已评估该请求：当前策略是暂不接收新任务。".to_string(),
                    },
                ));
            }
            DecisionIntent::KeepCurrentTask => {
                self.sender.send(Message::new(
                    next_message_id(),
                    self.id.clone(),
                    self.world_id.clone(),
                    Payload::Text {
                        content: "已评估当前输入：继续保持当前任务。".to_string(),
                    },
                ));
            }
            DecisionIntent::ReplaceCurrentTask { task } => {
                self.sender.send(Message::new(
                    next_message_id(),
                    self.id.clone(),
                    self.world_id.clone(),
                    Payload::Text {
                        content: format!("建议替换为新任务：{}", task.description),
                    },
                ));
            }
            DecisionIntent::StrategyHint { hint } => {
                self.sender.send(Message::new(
                    next_message_id(),
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
        let target_worker = target_worker.unwrap_or_else(|| WorkerId("worker-1".to_string()));
        let Some(worker_tx) = get_worker_tx(&target_worker) else {
            warn!(worker_id = %target_worker.0, "no worker tx available for dispatch");
            return false;
        };

        let task_message = Message::new(
            next_message_id(),
            self.id.clone(),
            AgentId(target_worker.0.clone()),
            Payload::Text {
                content: format!("task:start:{}", task.description),
            },
        );
        worker_tx.send(task_message).is_ok()
    }

    fn build_cognition_input(&self) -> CognitionInput {
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
        let worker_profiles = list_worker_profiles();
        metadata.insert("available_workers.count".to_string(), worker_profiles.len().to_string());
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
            source: "world.worker_catalog".to_string(),
            content: profile.summary_line(),
            reliability: 1.0,
        }));

        CognitionInput {
            intent,
            context: Context { facts, metadata },
        }
    }
}

fn next_message_id() -> MessageId {
    use std::time::{SystemTime, UNIX_EPOCH};

    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);

    MessageId(format!("msg-{millis}"))
}
