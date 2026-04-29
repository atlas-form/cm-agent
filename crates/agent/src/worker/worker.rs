use std::{collections::HashMap, thread, time::Duration};

use action::{Action, ActionResult, ActionState};
use agent_core::{
    messaging::{MessageReceive, MessageSend},
    protocol::{AgentId, Message, MessageId, Payload, TaskId, WorkerId},
};
use cognition::{Cognition, CognitionInput, CognitionResult, Context, Fact, Intent, IntentKind};
use futures::executor::block_on;
use tracing::warn;

use super::{Task, TaskMemory, WorkerPhase, WorkerState, action_bridge::decision_to_action};

pub type BoxedAction = Box<dyn Action<Result = ActionResult<String, String, ()>> + Send>;

pub struct Worker {
    id: AgentId,
    state: WorkerState,
    phase: WorkerPhase,
    current_task: Option<Task>,
    current_action: Option<BoxedAction>,
    cognition: Box<dyn Cognition + Send>,
    receiver: Box<dyn MessageReceive + Send>,
    sender: Box<dyn MessageSend + Send>,
    memory: TaskMemory,
}

impl Worker {
    pub fn new(
        id: AgentId,
        cognition: Box<dyn Cognition + Send>,
        receiver: Box<dyn MessageReceive + Send>,
        sender: Box<dyn MessageSend + Send>,
    ) -> Self {
        Self {
            id,
            state: WorkerState::Idle,
            phase: WorkerPhase::Thinking,
            current_task: None,
            current_action: None,
            cognition,
            receiver,
            sender,
            memory: TaskMemory::default(),
        }
    }

    pub fn run(&mut self) {
        while self.state != WorkerState::Shutdown {
            let had_messages = self.process_messages();

            if self.state == WorkerState::Running {
                self.runtime_step();
            } else if !had_messages {
                thread::sleep(Duration::from_millis(10));
            }
        }
    }
}

impl Worker {
    fn runtime_step(&mut self) {
        match self.phase {
            WorkerPhase::Thinking => self.step_thinking(),
            WorkerPhase::Acting => self.step_acting(),
            WorkerPhase::Finished => self.handle_task_finished(),
            WorkerPhase::Failed => self.handle_task_failed(),
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
        let Payload::Text { content } = message.payload else {
            return;
        };
        let payload = content.trim();

        match payload {
            "control:start" => {
                if self.current_task.is_some() {
                    self.state = WorkerState::Running;
                }
            }
            "control:pause" => {
                self.state = WorkerState::Paused;
                if let Some(action) = self.current_action.as_mut() {
                    action.suspend();
                }
            }
            "control:resume" => {
                self.state = WorkerState::Running;
                if let Some(action) = self.current_action.as_mut() {
                    action.resume();
                }
            }
            "control:shutdown" => {
                self.state = WorkerState::Shutdown;
            }
            _ => {
                if let Some(description) = payload.strip_prefix("task:start:") {
                    self.start_task(Task {
                        id: new_task_id(),
                        description: description.trim().to_string(),
                        requester: message.from,
                    });
                }
            }
        }
    }

    fn start_task(&mut self, task: Task) {
        self.current_task = Some(task);
        self.current_action = None;
        self.phase = WorkerPhase::Thinking;
        self.state = WorkerState::Running;
        self.memory.clear();
        self.memory.push_progress("task accepted");
    }

    fn step_thinking(&mut self) {
        let Some(task) = self.current_task.as_ref() else {
            self.state = WorkerState::Idle;
            return;
        };

        self.memory.push_progress("thinking");

        let input = CognitionInput {
            intent: Intent {
                id: task.id.clone(),
                kind: IntentKind::Planning,
                description: task.description.clone(),
            },
            context: build_context(self.id.clone(), self.phase, &self.memory),
        };

        match block_on(self.cognition.evaluate(input)) {
            CognitionResult::Success(output) => {
                if let Some(action) = decision_to_action(&output) {
                    self.current_action = Some(action);
                    self.phase = WorkerPhase::Acting;
                    self.memory.push_progress("action selected");
                } else {
                    self.phase = WorkerPhase::Finished;
                    self.memory.push_progress("no further action");
                }
            }
            CognitionResult::Failure(failure) => {
                warn!(error = %failure.description, "worker cognition failure");
                self.phase = WorkerPhase::Failed;
                self.memory
                    .set_error(format!("cognition failure: {}", failure.description));
            }
        }
    }

    fn step_acting(&mut self) {
        let Some(action) = self.current_action.as_mut() else {
            self.phase = WorkerPhase::Failed;
            self.memory
                .set_error("acting phase entered without current_action");
            warn!("worker acting phase without action");
            return;
        };

        self.memory.push_progress("acting");
        action.drive();

        match action.state() {
            ActionState::Completed => {
                if let Some(result) = action.take_result() {
                    self.memory
                        .set_state("last_action_status", format!("{:?}", result.status));
                }
                self.current_action = None;
                self.phase = WorkerPhase::Thinking;
                self.memory.push_progress("action completed");
            }
            ActionState::Aborted => {
                self.phase = WorkerPhase::Failed;
                self.memory.set_error("action aborted");
                warn!("worker action aborted");
            }
            ActionState::Eligible | ActionState::Active | ActionState::Suspended => {}
        }
    }

    fn handle_task_finished(&mut self) {
        let Some(task) = self.current_task.take() else {
            self.state = WorkerState::Idle;
            return;
        };

        self.sender.send(Message {
            id: next_message_id(),
            from: self.id.clone(),
            to: task.requester,
            payload: Payload::WorkerReportFinished {
                worker_id: WorkerId(self.id.0.clone()),
                task_id: TaskId(task.id),
            },
        });
        self.phase = WorkerPhase::Thinking;
        self.state = WorkerState::Idle;
    }

    fn handle_task_failed(&mut self) {
        let Some(task) = self.current_task.take() else {
            self.state = WorkerState::Idle;
            return;
        };

        let reason = self
            .memory
            .last_error
            .clone()
            .unwrap_or_else(|| "unknown error".to_string());
        self.sender.send(Message {
            id: next_message_id(),
            from: self.id.clone(),
            to: task.requester,
            payload: Payload::WorkerReportFailed {
                worker_id: WorkerId(self.id.0.clone()),
                task_id: TaskId(task.id.clone()),
                reason: reason.clone(),
            },
        });
        self.current_action = None;
        self.phase = WorkerPhase::Thinking;
        self.state = WorkerState::Idle;
        warn!(task_id = %task.id, reason = %reason, "worker reporting task failed");
    }
}

fn build_context(worker_id: AgentId, phase: WorkerPhase, memory: &TaskMemory) -> Context {
    let mut metadata = HashMap::new();
    metadata.insert("worker_id".to_string(), worker_id.0);
    metadata.insert("phase".to_string(), format!("{phase:?}"));

    for (key, value) in &memory.state {
        metadata.insert(format!("memory.{key}"), value.clone());
    }

    let facts = memory
        .progress
        .iter()
        .map(|entry| Fact {
            source: "worker.runtime".to_string(),
            content: entry.clone(),
            reliability: 1.0,
        })
        .collect::<Vec<_>>();

    Context { facts, metadata }
}

fn new_task_id() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};

    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);

    format!("task-{millis}")
}

fn next_message_id() -> MessageId {
    use std::time::{SystemTime, UNIX_EPOCH};

    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);

    MessageId(format!("msg-{millis}"))
}
