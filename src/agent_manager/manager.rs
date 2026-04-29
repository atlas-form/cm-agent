use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
};

use crate::{
    agent_error::Result,
    agent_session::{
        AgentSession, AgentSessionConfig, AgentSessionScope, CognitionFactory, MemoryStore,
        NoopMemoryStore, SessionResult, SessionRuntimeConfig,
    },
    core::protocol::{AgentId, SessionId, UserId, WorkspaceId},
};

#[derive(Clone)]
pub struct AgentManagerConfig {
    pub runtime: SessionRuntimeConfig,
    pub commander_cognition: CognitionFactory,
    pub worker_cognition: CognitionFactory,
    pub memory_store: Arc<dyn MemoryStore>,
}

impl AgentManagerConfig {
    pub fn new(commander_cognition: CognitionFactory, worker_cognition: CognitionFactory) -> Self {
        Self {
            runtime: SessionRuntimeConfig::default(),
            commander_cognition,
            worker_cognition,
            memory_store: Arc::new(NoopMemoryStore),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AgentRequest {
    pub user_id: Option<UserId>,
    pub workspace_id: Option<WorkspaceId>,
    pub agent_id: AgentId,
    pub session_id: SessionId,
    pub input: String,
}

pub struct AgentManager {
    config: AgentManagerConfig,
    active_sessions: Mutex<HashMap<SessionId, ()>>,
}

impl AgentManager {
    pub fn new(config: AgentManagerConfig) -> Self {
        Self {
            config,
            active_sessions: Mutex::new(HashMap::new()),
        }
    }

    pub async fn run_request(&self, request: AgentRequest) -> Result<SessionResult> {
        self.register_session(&request.session_id);
        let session = self.create_session(&request);
        let result = session.run_once(request.input).await;
        self.remove_session(&request.session_id);
        result
    }

    pub fn active_session_count(&self) -> usize {
        self.active_sessions
            .lock()
            .map(|sessions| sessions.len())
            .unwrap_or(0)
    }

    fn create_session(&self, request: &AgentRequest) -> AgentSession {
        AgentSession::new(
            AgentSessionScope {
                user_id: request.user_id.clone(),
                workspace_id: request.workspace_id.clone(),
                agent_id: request.agent_id.clone(),
                session_id: request.session_id.clone(),
            },
            AgentSessionConfig {
                runtime: self.config.runtime.clone(),
                commander_cognition: self.config.commander_cognition.clone(),
                worker_cognition: self.config.worker_cognition.clone(),
                memory_store: self.config.memory_store.clone(),
            },
        )
    }

    fn register_session(&self, session_id: &SessionId) {
        if let Ok(mut sessions) = self.active_sessions.lock() {
            sessions.insert(session_id.clone(), ());
        }
    }

    fn remove_session(&self, session_id: &SessionId) {
        if let Ok(mut sessions) = self.active_sessions.lock() {
            sessions.remove(session_id);
        }
    }
}
