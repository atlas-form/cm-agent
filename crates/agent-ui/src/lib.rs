//! # Agent UI
//!
//! 一个面向终端的人机交互层，采用类似 Codex TUI 的分层思想：
//! - `AppState`：状态容器
//! - `UiEvent`：交互事件
//! - `TerminalRenderer`：渲染输出
//! - `TerminalUiApp`：事件驱动循环

mod app;
mod event;
mod renderer;
mod session;

pub use app::*;
pub use event::*;
pub use renderer::*;
pub use session::*;
