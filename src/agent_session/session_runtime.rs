use std::{sync::Arc, time::Duration};

use serde::{Deserialize, Serialize};
use tokio::sync::mpsc as tokio_mpsc;

use crate::{
    MemoryBundle, SessionContext,
    agent::{
        commander::{Commander, CommanderChannels, CommanderOptions},
        worker::Worker,
    },
    agent_error::{Result, SettingsError},
    cognition::Cognition,
    core::{
        messaging::MessageRx,
        protocol::{
            AgentId, Message, MessageContext, MessageId, Payload, SessionEvent, SessionId, TaskId,
            TaskSpec,
        },
    },
    roles::{RoleCatalog, RoleProfile, RoleRouter},
};

pub type SessionEventTx = tokio_mpsc::Sender<SessionEvent>;
pub type SessionEventRx = tokio_mpsc::Receiver<SessionEvent>;

#[derive(Debug, Clone)]
pub struct SessionRuntimeConfig {
    pub response_timeout: Duration,
    pub event_buffer: usize,
    pub commander_fast_route: bool,
    pub commander_fast_route_min_score: f32,
}

impl Default for SessionRuntimeConfig {
    fn default() -> Self {
        Self {
            response_timeout: Duration::from_secs(300),
            event_buffer: 1024,
            commander_fast_route: true,
            commander_fast_route_min_score: 2.0,
        }
    }
}

pub struct SessionRuntimeWorkerInput {
    pub role: RoleProfile,
    pub cognition: Box<dyn Cognition + Send>,
}

pub struct SessionRuntimeInput {
    pub session_id: SessionId,
    pub context: MessageContext,
    pub task_description: String,
    pub memory_bundle: MemoryBundle,
    pub commander_cognition: Box<dyn Cognition + Send>,
    pub workers: Vec<SessionRuntimeWorkerInput>,
    pub config: SessionRuntimeConfig,
    pub event_tx: Option<SessionEventTx>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
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
    worker_txs: Vec<crate::core::messaging::MessageTx>,
    response_rx: Option<MessageRx>,
    commander_loop: tokio::task::JoinHandle<()>,
    worker_loops: Vec<tokio::task::JoinHandle<()>>,
    response_timeout: Duration,
    event_tx: Option<SessionEventTx>,
}

impl SessionRuntime {
    pub fn start(input: SessionRuntimeInput) -> Result<Self> {
        let session_context = Arc::new(SessionContext::new());
        session_context.extensions().insert(input.memory_bundle);
        let (commander_tx, commander_rx) = tokio_mpsc::unbounded_channel::<Message>();
        let (response_tx, response_rx) = tokio_mpsc::unbounded_channel::<Message>();

        session_context
            .directory()
            .register_commander_tx(commander_tx.clone());

        if input.workers.is_empty() {
            return Err(
                SettingsError::invalid("session runtime requires at least one worker").into(),
            );
        }

        let mut worker_txs = Vec::new();
        let mut worker_loops = Vec::new();
        let role_catalog = RoleCatalog::new(
            input
                .workers
                .iter()
                .map(|worker| worker.role.clone())
                .collect(),
        );
        for worker_input in input.workers {
            let profile = worker_input.role.to_worker_profile();
            let (worker_tx, worker_rx) = tokio_mpsc::unbounded_channel::<Message>();
            session_context
                .directory()
                .register_worker_tx(profile.worker_id.clone(), worker_tx.clone());
            session_context.worker_catalog().register(profile.clone());

            let worker = Worker::new(
                worker_input.role,
                worker_input.cognition,
                worker_rx,
                commander_tx.clone(),
            );
            let worker_loop = tokio::spawn(async move {
                let mut worker = worker;
                worker.run().await;
            });

            worker_txs.push(worker_tx);
            worker_loops.push(worker_loop);
        }

        let commander = Commander::new(
            AgentId("commander".to_string()),
            AgentId("external-host".to_string()),
            input.commander_cognition,
            CommanderChannels {
                receiver: commander_rx,
                sender: response_tx,
                event_tx: input.event_tx.clone(),
            },
            session_context.clone(),
            RoleRouter::new(role_catalog),
            CommanderOptions {
                fast_route_enabled: input.config.commander_fast_route,
                fast_route_min_score: input.config.commander_fast_route_min_score,
            },
        );

        let commander_loop = tokio::spawn(async move {
            let mut commander = commander;
            commander.run().await;
        });

        Ok(Self {
            session_id: input.session_id,
            context: input.context,
            task_description: input.task_description,
            session_context,
            commander_tx,
            worker_txs,
            response_rx: Some(response_rx),
            commander_loop,
            worker_loops,
            response_timeout: input.config.response_timeout,
            event_tx: input.event_tx,
        })
    }

    pub async fn run_until_complete(&mut self) -> Result<SessionResult> {
        self.emit_event(SessionEvent::Started {
            session_id: self.session_id.clone(),
        })
        .await;

        let memory_kinds = self
            .session_context
            .extensions()
            .with::<MemoryBundle, _>(MemoryBundle::kind_names)
            .unwrap_or_default();
        let memory_count = self
            .session_context
            .extensions()
            .with::<MemoryBundle, _>(|bundle| bundle.records.len())
            .unwrap_or_default();
        self.emit_event(SessionEvent::MemoryLoaded {
            session_id: self.session_id.clone(),
            count: memory_count,
            kinds: memory_kinds,
        })
        .await;

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

        self.emit_event(SessionEvent::CommanderThinking {
            session_id: self.session_id.clone(),
        })
        .await;

        let Some(mut response_rx) = self.response_rx.take() else {
            return Err(SettingsError::invalid("session response receiver already used").into());
        };
        let timeout = self.response_timeout;
        let received = match tokio::time::timeout(timeout, response_rx.recv()).await {
            Ok(Some(message)) => message,
            Ok(None) => {
                let reason = "session response channel closed".to_string();
                return Err(SettingsError::invalid(reason).into());
            }
            Err(_) => {
                let reason = "session response timed out".to_string();
                return Err(SettingsError::invalid(reason).into());
            }
        };

        let output = match received.payload {
            Payload::Text { content } => content,
            _ => "session returned non-text response".to_string(),
        };

        self.emit_event(SessionEvent::Output {
            session_id: self.session_id.clone(),
            content: output.clone(),
        })
        .await;

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
        for worker_tx in &self.worker_txs {
            let _ = worker_tx.send(Message::new(
                MessageId(next_id("msg")),
                AgentId("session-runtime".to_string()),
                AgentId("worker".to_string()),
                Payload::Text {
                    content: "control:shutdown".to_string(),
                },
            ));
        }

        let _ = tokio::time::timeout(Duration::from_secs(2), async {
            let _ = self.commander_loop.await;
            for worker_loop in self.worker_loops {
                let _ = worker_loop.await;
            }
        })
        .await;
    }

    pub fn session_context(&self) -> Arc<SessionContext> {
        self.session_context.clone()
    }

    async fn emit_event(&self, event: SessionEvent) {
        if let Some(event_tx) = &self.event_tx {
            let _ = event_tx.send(event).await;
        }
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
