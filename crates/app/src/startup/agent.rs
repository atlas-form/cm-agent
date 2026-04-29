use std::sync::mpsc::Sender;

use agent::{commander::Commander, worker::Worker};
use agent_core::{
    messaging::{MessageSend, TokioInbox, TokioOutbox},
    protocol::Message,
};
use agent_error::Result;

use crate::startup::{
    cognition::{build_commander_cognition, build_worker_cognition},
    world::{WorldChannels, WorldReceivers},
};

pub fn spawn_agent_loops(
    channels: WorldChannels,
    receivers: WorldReceivers,
    commander_external_tx: Sender<Message>,
) -> Result<(tokio::task::JoinHandle<()>, tokio::task::JoinHandle<()>)> {
    let commander_cognition = build_commander_cognition()?;
    let worker_cognition = build_worker_cognition()?;

    let commander = Commander::new(
        agent_core::protocol::AgentId("commander".to_string()),
        agent_core::protocol::AgentId("human-ui".to_string()),
        commander_cognition,
        Box::new(TokioInbox::new(receivers.commander_in_rx)),
        Box::new(ExternalOutbox::new(commander_external_tx)),
    );

    let worker = Worker::new(
        agent_core::protocol::AgentId("worker-1".to_string()),
        worker_cognition,
        Box::new(TokioInbox::new(receivers.worker_in_rx)),
        Box::new(TokioOutbox::new(channels.commander_in_tx.clone())),
    );

    let commander_loop = tokio::task::spawn_blocking(move || {
        let mut commander = commander;
        commander.run();
    });

    let worker_loop = tokio::task::spawn_blocking(move || {
        let mut worker = worker;
        worker.run();
    });

    Ok((commander_loop, worker_loop))
}

struct ExternalOutbox {
    tx: Sender<Message>,
}

impl ExternalOutbox {
    fn new(tx: Sender<Message>) -> Self {
        Self { tx }
    }
}

impl MessageSend for ExternalOutbox {
    fn send(&mut self, message: Message) {
        let _ = self.tx.send(message);
    }
}
