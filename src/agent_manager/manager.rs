use std::{
    collections::HashMap,
    sync::{Arc, Mutex},
};

use serde::{Deserialize, Serialize};
use tokio::sync::mpsc;

use crate::{
    agent_error::Result,
    agent_session::{
        AgentSession, AgentSessionConfig, AgentSessionScope, CognitionFactory, MemoryStore,
        NoopMemoryStore, RoleCognitionFactory, SessionEventRx, SessionResult, SessionRuntimeConfig,
    },
    core::protocol::{AgentId, SessionEvent, SessionId, UserId, WorkspaceId},
    roles::{RoleCatalog, RoleProfile},
};

#[derive(Clone)]
pub struct AgentManagerConfig {
    pub runtime: SessionRuntimeConfig,
    pub roles: RoleCatalog,
    pub commander_cognition: CognitionFactory,
    pub worker_cognition: RoleCognitionFactory,
    pub memory_store: Arc<dyn MemoryStore>,
}

impl AgentManagerConfig {
    pub fn new(commander_cognition: CognitionFactory, worker_cognition: CognitionFactory) -> Self {
        let worker_cognition =
            Arc::new(move |_: &RoleProfile| worker_cognition()) as RoleCognitionFactory;
        Self::new_role_aware(commander_cognition, worker_cognition)
    }

    pub fn new_role_aware(
        commander_cognition: CognitionFactory,
        worker_cognition: RoleCognitionFactory,
    ) -> Self {
        Self {
            runtime: SessionRuntimeConfig::default(),
            roles: RoleCatalog::configured(),
            commander_cognition,
            worker_cognition,
            memory_store: Arc::new(NoopMemoryStore),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AgentRequest {
    pub user_id: Option<UserId>,
    pub workspace_id: Option<WorkspaceId>,
    pub agent_id: AgentId,
    pub session_id: SessionId,
    pub input: String,
}

pub struct AgentManager {
    config: AgentManagerConfig,
    active_sessions: Arc<Mutex<HashMap<SessionId, ()>>>,
}

impl AgentManager {
    pub fn new(config: AgentManagerConfig) -> Self {
        Self {
            config,
            active_sessions: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    pub async fn run_request(&self, request: AgentRequest) -> Result<SessionResult> {
        self.register_session(&request.session_id);
        let session = self.create_session(&request);
        let result = session.run_once(request.input).await;
        self.remove_session(&request.session_id);
        result
    }

    pub fn run_stream(&self, request: AgentRequest) -> Result<SessionEventRx> {
        self.register_session(&request.session_id);
        let session_id = request.session_id.clone();
        let input = request.input.clone();
        let session = self.create_session(&request);
        let (event_tx, event_rx) = mpsc::channel(self.config.runtime.event_buffer);
        let active_sessions = self.active_sessions.clone();

        tokio::spawn(async move {
            let result = session
                .run_once_with_events(input, Some(event_tx.clone()))
                .await;
            remove_session_from(&active_sessions, &session_id);
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
                }
            }
        });

        Ok(event_rx)
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
                roles: self.config.roles.clone(),
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
        remove_session_from(&self.active_sessions, session_id);
    }
}

fn remove_session_from(
    active_sessions: &Arc<Mutex<HashMap<SessionId, ()>>>,
    session_id: &SessionId,
) {
    if let Ok(mut sessions) = active_sessions.lock() {
        sessions.remove(session_id);
    }
}
