use agent_ui::{AppPhase, ChatTurn, RenderConfig, TerminalRenderer, TerminalUiApp, UiEvent};

fn main() -> std::io::Result<()> {
    let renderer = TerminalRenderer::new(RenderConfig {
        title: "Agent Terminal UI".to_string(),
        prompt: "you> ".to_string(),
    });
    let mut app = TerminalUiApp::new(renderer);
    app.render_banner();

    while app.state().phase == AppPhase::Running {
        match app.read_event()? {
            UiEvent::Help => app.render_help(),
            UiEvent::Quit => app.request_exit(),
            UiEvent::Empty => {}
            UiEvent::UserInput(input) => {
                let turn = ChatTurn {
                    user: input.clone(),
                    assistant: format!("echo: {input}"),
                };
                app.render_turn(&turn);
            }
        }
    }

    Ok(())
}
