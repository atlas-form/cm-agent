use std::{
    sync::mpsc::{Receiver, RecvTimeoutError},
    time::Duration,
};

use agent_core::{
    messaging::MessageTx,
    protocol::{AgentId, Message, MessageId, Payload, TaskId, TaskSpec},
};
use agent_ui::{RenderConfig, TerminalRenderer, TerminalUiApp};

use crate::startup::task_id;

pub struct AgentBridgeSession {
    commander_tx: MessageTx,
    response_rx: Receiver<Message>,
}

impl AgentBridgeSession {
    pub fn new(commander_tx: MessageTx, response_rx: Receiver<Message>) -> Self {
        Self {
            commander_tx,
            response_rx,
        }
    }

    pub fn respond(&mut self, input: &str) -> String {
        let message = Message::new(
            MessageId(task_id("msg")),
            AgentId("human-ui".to_string()),
            AgentId("commander".to_string()),
            Payload::HumanCommand {
                task: TaskSpec {
                    id: TaskId(task_id("task")),
                    description: input.to_string(),
                },
            },
        );

        if self.commander_tx.send(message).is_err() {
            return "系统错误：无法提交任务到 commander".to_string();
        }

        let timeout = Duration::from_secs(100);
        match self.response_rx.recv_timeout(timeout) {
            Ok(message) => match message.payload {
                Payload::Text { content } => content,
                _ => "系统提示：收到非文本响应".to_string(),
            },
            Err(RecvTimeoutError::Timeout) => "任务处理中（超时未收到完成回报）".to_string(),
            Err(RecvTimeoutError::Disconnected) => "系统错误：commander 外部通道已断开".to_string(),
        }
    }
}

pub fn build_terminal_ui() -> TerminalUiApp {
    let renderer = TerminalRenderer::new(RenderConfig {
        title: "Agent App".to_string(),
        prompt: "you> ".to_string(),
    });
    TerminalUiApp::new(renderer)
}
