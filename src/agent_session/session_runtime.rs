use std::{sync::Arc, time::Duration};

use tokio::sync::mpsc as tokio_mpsc;

use crate::{
    SessionContext,
    agent::{commander::Commander, worker::Worker},
    agent_error::{Result, SettingsError},
    cognition::Cognition,
    core::{
        messaging::MessageRx,
        protocol::{
            AgentId, Message, MessageContext, MessageId, Payload, SessionId, TaskId, TaskSpec,
            WorkerId, WorkerProfile,
        },
    },
};

#[derive(Debug, Clone)]
pub struct SessionRuntimeConfig {
    pub response_timeout: Duration,
    pub worker_profiles: Vec<WorkerProfile>,
}

impl Default for SessionRuntimeConfig {
    fn default() -> Self {
        Self {
            response_timeout: Duration::from_secs(300),
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
    session_context: Arc<SessionContext>,
    commander_tx: crate::core::messaging::MessageTx,
    worker_tx: crate::core::messaging::MessageTx,
    response_rx: Option<MessageRx>,
    commander_loop: tokio::task::JoinHandle<()>,
    worker_loop: tokio::task::JoinHandle<()>,
    response_timeout: Duration,
}

impl SessionRuntime {
    pub fn start(input: SessionRuntimeInput) -> Result<Self> {
        let session_context = Arc::new(SessionContext::new());
        let (commander_tx, commander_rx) = tokio_mpsc::unbounded_channel::<Message>();
        let (worker_tx, worker_rx) = tokio_mpsc::unbounded_channel::<Message>();
        let (response_tx, response_rx) = tokio_mpsc::unbounded_channel::<Message>();

        session_context
            .directory()
            .register_commander_tx(commander_tx.clone());
        for profile in &input.config.worker_profiles {
            session_context
                .directory()
                .register_worker_tx(profile.worker_id.clone(), worker_tx.clone());
            session_context.worker_catalog().register(profile.clone());
        }

        let commander = Commander::new(
            AgentId("commander".to_string()),
            AgentId("external-host".to_string()),
            input.commander_cognition,
            commander_rx,
            response_tx,
            session_context.clone(),
        );

        let worker = Worker::new(
            AgentId("worker-1".to_string()),
            input.worker_cognition,
            worker_rx,
            commander_tx.clone(),
        );

        let commander_loop = tokio::spawn(async move {
            let mut commander = commander;
            commander.run().await;
        });
        let worker_loop = tokio::spawn(async move {
            let mut worker = worker;
            worker.run().await;
        });

        Ok(Self {
            session_id: input.session_id,
            context: input.context,
            task_description: input.task_description,
            session_context,
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

        let Some(mut response_rx) = self.response_rx.take() else {
            return Err(SettingsError::invalid("session response receiver already used").into());
        };
        let timeout = self.response_timeout;
        let received = tokio::time::timeout(timeout, response_rx.recv())
            .await
            .map_err(|_| SettingsError::invalid("session response timed out"))?
            .ok_or_else(|| SettingsError::invalid("session response channel closed"))?;

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
                signal: crate::core::protocol::ControlSignal::Shutdown,
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

    pub fn session_context(&self) -> Arc<SessionContext> {
        self.session_context.clone()
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
    use std::{
        sync::atomic::{AtomicU64, Ordering},
        time::{SystemTime, UNIX_EPOCH},
    };

    static NEXT_ID_COUNTER: AtomicU64 = AtomicU64::new(1);

    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    let sequence = NEXT_ID_COUNTER.fetch_add(1, Ordering::Relaxed);
    format!("{prefix}-{millis}-{sequence}")
}
