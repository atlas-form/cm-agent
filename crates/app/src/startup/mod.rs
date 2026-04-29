mod agent;
mod cognition;
mod settings;
mod ui;
mod world;

use std::sync::mpsc;

use agent_core::protocol::{AgentId, ControlSignal, Message, MessageId, Payload};
use agent_error::Result;
use agent_ui::{AppPhase, ChatTurn, TerminalUiApp, UiEvent};

use self::{
    agent::spawn_agent_loops,
    settings::Settings,
    ui::{AgentBridgeSession, build_terminal_ui},
    world::{WorldChannels, init_world_channels},
};

pub struct AppStartup {
    app: TerminalUiApp,
    session: AgentBridgeSession,
    runtime: RuntimeHandles,
}

struct RuntimeHandles {
    channels: WorldChannels,
    commander_loop: tokio::task::JoinHandle<()>,
    worker_loop: tokio::task::JoinHandle<()>,
}

impl AppStartup {
    pub fn boot() -> Result<Self> {
        let settings = Settings::load_default()?;
        let (channels, agent_receivers) = init_world_channels(&settings)?;
        let (commander_external_tx, commander_external_rx) = mpsc::channel::<Message>();
        let (commander_loop, worker_loop) =
            spawn_agent_loops(channels.clone(), agent_receivers, commander_external_tx)?;
        let session =
            AgentBridgeSession::new(channels.commander_in_tx.clone(), commander_external_rx);
        let app = build_terminal_ui();

        Ok(Self {
            app,
            session,
            runtime: RuntimeHandles {
                channels,
                commander_loop,
                worker_loop,
            },
        })
    }

    pub fn run_ui(&mut self) -> Result<()> {
        self.app.render_banner();
        while self.app.state().phase == AppPhase::Running {
            let event = self.app.read_event()?;
            self.handle_ui_event(event);
        }
        Ok(())
    }

    pub async fn shutdown(&mut self) {
        use tokio::time::{Duration, timeout};

        self.runtime.send_shutdown();
        let _ = timeout(Duration::from_secs(2), async {
            let _ = (&mut self.runtime.commander_loop).await;
            let _ = (&mut self.runtime.worker_loop).await;
        })
        .await;
    }
}

impl AppStartup {
    fn handle_ui_event(&mut self, event: UiEvent) {
        match event {
            UiEvent::Help => self.app.render_help(),
            UiEvent::Quit => self.app.request_exit(),
            UiEvent::Empty => {}
            UiEvent::UserInput(input) => {
                let output = self.session.respond(&input);
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
    fn send_shutdown(&self) {
        let _ = self.channels.commander_in_tx.send(Message::new(
            next_message_id(),
            AgentId("app".to_string()),
            AgentId("commander".to_string()),
            Payload::Control {
                signal: ControlSignal::Shutdown,
                target: None,
            },
        ));
        let _ = self.channels.worker_in_tx.send(Message::new(
            next_message_id(),
            AgentId("app".to_string()),
            AgentId("worker-1".to_string()),
            Payload::Text {
                content: "control:shutdown".to_string(),
            },
        ));
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

fn next_message_id() -> MessageId {
    use std::time::{SystemTime, UNIX_EPOCH};

    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis())
        .unwrap_or(0);
    MessageId(format!("msg-{millis}"))
}
