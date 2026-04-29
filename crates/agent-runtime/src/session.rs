use std::sync::Arc;

use agent_core::protocol::{AgentId, MessageContext, SessionId, UserId, WorkspaceId};
use agent_error::Result;
use cognition::Cognition;

use crate::{
    MemoryScope, MemoryStore, SessionResult, SessionRuntime, SessionRuntimeConfig,
    SessionRuntimeInput, SessionSnapshot,
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
        let commander_cognition = (self.config.commander_cognition)()?;
        let worker_cognition = (self.config.worker_cognition)()?;
        let context = MessageContext {
            user_id: self.scope.user_id.clone(),
            workspace_id: self.scope.workspace_id.clone(),
            agent_id: Some(self.scope.agent_id.clone()),
            session_id: Some(self.scope.session_id.clone()),
            task_id: None,
        };

        let mut runtime = SessionRuntime::start(SessionRuntimeInput {
            session_id: self.scope.session_id.clone(),
            context,
            task_description: input.into(),
            commander_cognition,
            worker_cognition,
            config: self.config.runtime.clone(),
        })?;

        let result = runtime.run_until_complete().await;
        runtime.shutdown().await;

        if let Ok(result) = &result {
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

        result
    }

    pub fn scope(&self) -> &AgentSessionScope {
        &self.scope
    }
}
