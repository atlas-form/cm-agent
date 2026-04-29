use std::io;

use crate::{ChatTurn, TerminalRenderer, UiEvent};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AppPhase {
    Running,
    Exit,
}

#[derive(Debug)]
pub struct AppState {
    pub phase: AppPhase,
}

impl Default for AppState {
    fn default() -> Self {
        Self {
            phase: AppPhase::Running,
        }
    }
}

pub struct TerminalUiApp {
    state: AppState,
    renderer: TerminalRenderer,
}

impl TerminalUiApp {
    pub fn new(renderer: TerminalRenderer) -> Self {
        Self {
            state: AppState::default(),
            renderer,
        }
    }

    pub fn render_banner(&self) {
        self.renderer.render_banner();
    }

    pub fn read_event(&self) -> io::Result<UiEvent> {
        self.renderer.render_prompt()?;
        let mut line = String::new();
        io::stdin().read_line(&mut line)?;
        Ok(UiEvent::parse(line))
    }

    pub fn state(&self) -> &AppState {
        &self.state
    }

    pub fn render_help(&self) {
        self.renderer.render_help();
    }

    pub fn render_turn(&self, turn: &ChatTurn) {
        self.renderer.render_turn(turn);
    }

    pub fn request_exit(&mut self) {
        self.state.phase = AppPhase::Exit;
    }
}
