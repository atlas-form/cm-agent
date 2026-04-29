use std::sync::Arc;

use crate::{
    MemoryScope, MemoryStore, SessionEventRx, SessionResult, SessionRuntime, SessionRuntimeConfig,
    SessionRuntimeInput, SessionSnapshot,
    agent_error::Result,
    cognition::Cognition,
    core::protocol::{AgentId, MessageContext, SessionEvent, SessionId, UserId, WorkspaceId},
};

pub type CognitionFactory =
    Arc<dyn Fn() -> Result<Box<dyn Cognition + Send>> + Send + Sync + 'static>;

#[derive(Clone)]
pub struct AgentSessionConfig {
    pub runtime: SessionRuntimeConfig,
    pub commander_cognition: CognitionFactory,
    pub worker_cognition: CognitionFactory,
    pub memory_store: Arc<dyn MemoryStore>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentSessionScope {
    pub user_id: Option<UserId>,
    pub workspace_id: Option<WorkspaceId>,
    pub agent_id: AgentId,
    pub session_id: SessionId,
}

pub struct AgentSession {
    scope: AgentSessionScope,
    config: AgentSessionConfig,
}

impl AgentSession {
    pub fn new(scope: AgentSessionScope, config: AgentSessionConfig) -> Self {
        Self { scope, config }
    }

    pub async fn run_once(&self, input: impl Into<String>) -> Result<SessionResult> {
        self.run_once_with_events(input, None).await
    }

    pub fn run_stream(self, input: impl Into<String>) -> Result<SessionEventRx> {
        let (event_tx, event_rx) = tokio::sync::mpsc::channel(self.config.runtime.event_buffer);
        let input = input.into();
        let session_id = self.scope.session_id.clone();

        tokio::spawn(async move {
            let result = self
                .run_once_with_events(input, Some(event_tx.clone()))
                .await;
            match result {
                Ok(_) => {
                    let _ = event_tx.send(SessionEvent::Finished { session_id }).await;
                }
                Err(err) => {
                    let _ = event_tx
                        .send(SessionEvent::Failed {
                            session_id,
                            reason: err.to_string(),
                        })
                        .await;
                    tracing::warn!(error = %err, "streaming agent session failed");
                }
            }
        });

        Ok(event_rx)
    }

    pub(crate) async fn run_once_with_events(
        &self,
        input: impl Into<String>,
        event_tx: Option<tokio::sync::mpsc::Sender<crate::core::protocol::SessionEvent>>,
    ) -> Result<SessionResult> {
        let commander_cognition = (self.config.commander_cognition)()?;
        let worker_cognition = (self.config.worker_cognition)()?;
        let context = self.message_context();

        let mut runtime = SessionRuntime::start(SessionRuntimeInput {
            session_id: self.scope.session_id.clone(),
            context,
            task_description: input.into(),
            commander_cognition,
            worker_cognition,
            config: self.config.runtime.clone(),
            event_tx,
        })?;

        let result = runtime.run_until_complete().await;
        runtime.shutdown().await;
        self.persist_if_ok(&result);
        result
    }

    pub fn scope(&self) -> &AgentSessionScope {
        &self.scope
    }

    fn message_context(&self) -> MessageContext {
        MessageContext {
            user_id: self.scope.user_id.clone(),
            workspace_id: self.scope.workspace_id.clone(),
            agent_id: Some(self.scope.agent_id.clone()),
            session_id: Some(self.scope.session_id.clone()),
            task_id: None,
        }
    }

    fn persist_if_ok(&self, result: &Result<SessionResult>) {
        if let Ok(result) = result {
            self.config.memory_store.persist_session(
                &MemoryScope {
                    user_id: self.scope.user_id.clone(),
                    workspace_id: self.scope.workspace_id.clone(),
                    agent_id: Some(self.scope.agent_id.clone()),
                    session_id: Some(result.session_id.clone()),
                    task_id: None,
                },
                SessionSnapshot {
                    summary: result.output.clone(),
                },
            );
        }
    }
}
