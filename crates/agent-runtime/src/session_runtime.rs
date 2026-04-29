use std::{
    sync::{Arc, mpsc},
    time::Duration,
};

use agent::{commander::Commander, worker::Worker};
use agent_core::{
    messaging::{MessageSend, TokioInbox, TokioOutbox},
    protocol::{
        AgentId, Message, MessageContext, MessageId, Payload, SessionId, TaskId, TaskSpec, WorkerId,
    },
};
use agent_error::{Result, SettingsError};
use cognition::Cognition;
use tokio::sync::mpsc as tokio_mpsc;
use world::{WorkerProfile, WorldRuntime};

#[derive(Debug, Clone)]
pub struct SessionRuntimeConfig {
    pub response_timeout: Duration,
    pub worker_profiles: Vec<WorkerProfile>,
}

impl Default for SessionRuntimeConfig {
    fn default() -> Self {
        Self {
            response_timeout: Duration::from_secs(100),
            worker_profiles: vec![default_worker_profile()],
        }
    }
}

pub struct SessionRuntimeInput {
    pub session_id: SessionId,
    pub context: MessageContext,
    pub task_description: String,
    pub commander_cognition: Box<dyn Cognition + Send>,
    pub worker_cognition: Box<dyn Cognition + Send>,
    pub config: SessionRuntimeConfig,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionResult {
    pub session_id: SessionId,
    pub output: String,
}

pub struct SessionRuntime {
    session_id: SessionId,
    context: MessageContext,
    task_description: String,
    world: Arc<WorldRuntime>,
    commander_tx: agent_core::messaging::MessageTx,
    worker_tx: agent_core::messaging::MessageTx,
    response_rx: Option<mpsc::Receiver<Message>>,
    commander_loop: tokio::task::JoinHandle<()>,
    worker_loop: tokio::task::JoinHandle<()>,
    response_timeout: Duration,
}

impl SessionRuntime {
    pub fn start(input: SessionRuntimeInput) -> Result<Self> {
        let world = Arc::new(WorldRuntime::new());
        let (commander_tx, commander_rx) = tokio_mpsc::unbounded_channel::<Message>();
        let (worker_tx, worker_rx) = tokio_mpsc::unbounded_channel::<Message>();
        let (response_tx, response_rx) = mpsc::channel::<Message>();

        world
            .directory()
            .register_commander_tx(commander_tx.clone());
        for profile in &input.config.worker_profiles {
            world
                .directory()
                .register_worker_tx(profile.worker_id.clone(), worker_tx.clone());
            world.worker_catalog().register(profile.clone());
        }

        let commander = Commander::new_with_world(
            AgentId("commander".to_string()),
            AgentId("external-host".to_string()),
            input.commander_cognition,
            Box::new(TokioInbox::new(commander_rx)),
            Box::new(ExternalOutbox::new(response_tx)),
            world.clone(),
        );

        let worker = Worker::new(
            AgentId("worker-1".to_string()),
            input.worker_cognition,
            Box::new(TokioInbox::new(worker_rx)),
            Box::new(TokioOutbox::new(commander_tx.clone())),
        );

        let commander_loop = tokio::task::spawn_blocking(move || {
            let mut commander = commander;
            commander.run();
        });
        let worker_loop = tokio::task::spawn_blocking(move || {
            let mut worker = worker;
            worker.run();
        });

        Ok(Self {
            session_id: input.session_id,
            context: input.context,
            task_description: input.task_description,
            world,
            commander_tx,
            worker_tx,
            response_rx: Some(response_rx),
            commander_loop,
            worker_loop,
            response_timeout: input.config.response_timeout,
        })
    }

    pub async fn run_until_complete(&mut self) -> Result<SessionResult> {
        let task_id = TaskId(next_id("task"));
        let mut context = self.context.clone();
        context.session_id = Some(self.session_id.clone());
        context.task_id = Some(task_id.clone());

        let message = Message::new_with_context(
            MessageId(next_id("msg")),
            context,
            AgentId("external-host".to_string()),
            AgentId("commander".to_string()),
            Payload::HumanCommand {
                task: TaskSpec {
                    id: task_id,
                    description: self.task_description.clone(),
                },
            },
        );

        self.commander_tx
            .send(message)
            .map_err(|_| SettingsError::invalid("failed to send task to session commander"))?;

        let Some(response_rx) = self.response_rx.take() else {
            return Err(SettingsError::invalid("session response receiver already used").into());
        };
        let timeout = self.response_timeout;
        let received = tokio::task::spawn_blocking(move || response_rx.recv_timeout(timeout))
            .await
            .map_err(|err| SettingsError::invalid(format!("session response join failed: {err}")))?
            .map_err(|err| SettingsError::invalid(format!("session response failed: {err}")))?;

        let output = match received.payload {
            Payload::Text { content } => content,
            _ => "session returned non-text response".to_string(),
        };

        Ok(SessionResult {
            session_id: self.session_id.clone(),
            output,
        })
    }

    pub async fn shutdown(self) {
        let _ = self.commander_tx.send(Message::new(
            MessageId(next_id("msg")),
            AgentId("session-runtime".to_string()),
            AgentId("commander".to_string()),
            Payload::Control {
                signal: agent_core::protocol::ControlSignal::Shutdown,
                target: None,
            },
        ));
        let _ = self.worker_tx.send(Message::new(
            MessageId(next_id("msg")),
            AgentId("session-runtime".to_string()),
            AgentId("worker-1".to_string()),
            Payload::Text {
                content: "control:shutdown".to_string(),
            },
        ));

        let _ = tokio::time::timeout(Duration::from_secs(2), async {
            let _ = self.commander_loop.await;
            let _ = self.worker_loop.await;
        })
        .await;
    }

    pub fn world(&self) -> Arc<WorldRuntime> {
        self.world.clone()
    }
}

struct ExternalOutbox {
    tx: mpsc::Sender<Message>,
}

impl ExternalOutbox {
    fn new(tx: mpsc::Sender<Message>) -> Self {
        Self { tx }
    }
}

impl MessageSend for ExternalOutbox {
    fn send(&mut self, message: Message) {
        let _ = self.tx.send(message);
    }
}

fn default_worker_profile() -> WorkerProfile {
    WorkerProfile {
        worker_id: WorkerId("worker-1".to_string()),
        agent_id: "worker-1".to_string(),
        name: "General Worker".to_string(),
        description: "通用执行 worker，适合处理常规单步任务和基础动作执行。".to_string(),
        capabilities: vec![
            "general_execution".to_string(),
            "single_step_actions".to_string(),
            "basic_task_handling".to_string(),
        ],
        constraints: vec![
            "one_task_at_a_time".to_string(),
            "limited_to_registered_actions".to_string(),
        ],
        status: "ready".to_string(),
    }
}

fn next_id(prefix: &str) -> String {
    use std::time::{SystemTime, UNIX_EPOCH};

    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    format!("{prefix}-{millis}")
}
