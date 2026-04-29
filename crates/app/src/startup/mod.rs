mod cognition;
mod settings;
mod shared_services;
mod ui;

use std::sync::Arc;

use agent_error::Result;
use agent_manager::{AgentManager, AgentManagerConfig};
use agent_ui::{AppPhase, ChatTurn, TerminalUiApp, UiEvent};

use self::{
    cognition::{build_commander_cognition, build_worker_cognition},
    settings::Settings,
    shared_services::init_shared_services,
    ui::{AgentBridgeSession, build_terminal_ui},
};

pub struct AppStartup {
    app: TerminalUiApp,
    session: AgentBridgeSession,
    runtime: RuntimeHandles,
}

struct RuntimeHandles {
    manager: Arc<AgentManager>,
}

impl AppStartup {
    pub fn boot() -> Result<Self> {
        let settings = Settings::load_default()?;
        init_shared_services(&settings)?;
        let manager = Arc::new(AgentManager::new(AgentManagerConfig::new(
            Arc::new(build_commander_cognition),
            Arc::new(build_worker_cognition),
        )));
        let session = AgentBridgeSession::new(manager.clone());
        let app = build_terminal_ui();

        Ok(Self {
            app,
            session,
            runtime: RuntimeHandles { manager },
        })
    }

    pub async fn run_ui(&mut self) -> Result<()> {
        self.app.render_banner();
        while self.app.state().phase == AppPhase::Running {
            let event = self.app.read_event()?;
            self.handle_ui_event(event).await;
        }
        Ok(())
    }

    pub async fn shutdown(&mut self) {
        self.runtime.shutdown().await;
    }
}

impl AppStartup {
    async fn handle_ui_event(&mut self, event: UiEvent) {
        match event {
            UiEvent::Help => self.app.render_help(),
            UiEvent::Quit => self.app.request_exit(),
            UiEvent::Empty => {}
            UiEvent::UserInput(input) => {
                let output = self
                    .session
                    .respond(&input)
                    .await
                    .unwrap_or_else(|err| format!("系统错误：{err}"));
                let turn = ChatTurn {
                    user: input,
                    assistant: output,
                };
                self.app.render_turn(&turn);
            }
        }
    }
}

impl RuntimeHandles {
    async fn shutdown(&self) {
        let _ = self.manager.active_session_count();
    }
}

pub(crate) fn task_id(prefix: &str) -> String {
    use std::time::{SystemTime, UNIX_EPOCH};

    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    format!("{prefix}-{millis}")
}
