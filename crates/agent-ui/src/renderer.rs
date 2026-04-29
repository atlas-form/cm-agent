use std::io::{self, Write};

use crate::ChatTurn;

#[derive(Debug, Clone)]
pub struct RenderConfig {
    pub prompt: String,
    pub title: String,
}

impl Default for RenderConfig {
    fn default() -> Self {
        Self {
            prompt: "you> ".to_string(),
            title: "Agent UI".to_string(),
        }
    }
}

#[derive(Debug, Default)]
pub struct TerminalRenderer {
    config: RenderConfig,
}

impl TerminalRenderer {
    pub fn new(config: RenderConfig) -> Self {
        Self { config }
    }

    pub fn render_banner(&self) {
        println!("=== {} ===", self.config.title);
        println!("输入文本与 agent 交互；输入 /help 查看命令。");
    }

    pub fn render_help(&self) {
        println!("可用命令:");
        println!("  /help  查看帮助");
        println!("  /quit  退出");
    }

    pub fn render_turn(&self, turn: &ChatTurn) {
        println!("you> {}", turn.user);
        println!("agent> {}", turn.assistant);
    }

    pub fn render_prompt(&self) -> io::Result<()> {
        print!("{}", self.config.prompt);
        io::stdout().flush()
    }
}
