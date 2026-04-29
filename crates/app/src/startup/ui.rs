use std::sync::Arc;

use agent_core::protocol::{AgentId, SessionId, UserId};
use agent_error::Result;
use agent_manager::{AgentManager, AgentRequest};
use agent_ui::{RenderConfig, TerminalRenderer, TerminalUiApp};

use crate::startup::task_id;

pub struct AgentBridgeSession {
    manager: Arc<AgentManager>,
    user_id: UserId,
    agent_id: AgentId,
}

impl AgentBridgeSession {
    pub fn new(manager: Arc<AgentManager>) -> Self {
        Self {
            manager,
            user_id: UserId("terminal-user".to_string()),
            agent_id: AgentId("terminal-agent".to_string()),
        }
    }

    pub async fn respond(&mut self, input: &str) -> Result<String> {
        let result = self
            .manager
            .run_request(AgentRequest {
                user_id: Some(self.user_id.clone()),
                workspace_id: None,
                agent_id: self.agent_id.clone(),
                session_id: SessionId(task_id("session")),
                input: input.to_string(),
            })
            .await?;
        Ok(result.output)
    }
}

pub fn build_terminal_ui() -> TerminalUiApp {
    let renderer = TerminalRenderer::new(RenderConfig {
        title: "Agent App".to_string(),
        prompt: "you> ".to_string(),
    });
    TerminalUiApp::new(renderer)
}
