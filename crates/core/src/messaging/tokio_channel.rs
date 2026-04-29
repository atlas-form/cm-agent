use std::{
    collections::VecDeque,
    sync::{Arc, Mutex},
};

use tokio::sync::mpsc;

use crate::{
    messaging::{MessageReceive, MessageSend},
    protocol::Message,
};

pub type MessageTx = mpsc::UnboundedSender<Message>;
pub type MessageRx = mpsc::UnboundedReceiver<Message>;

#[derive(Debug)]
pub struct TokioInbox {
    mailbox: Arc<Mutex<VecDeque<Message>>>,
    _receiver_task: tokio::task::JoinHandle<()>,
}

impl TokioInbox {
    pub fn new(mut rx: MessageRx) -> Self {
        let mailbox = Arc::new(Mutex::new(VecDeque::new()));
        let mailbox_for_task = Arc::clone(&mailbox);

        let receiver_task = tokio::spawn(async move {
            while let Some(message) = rx.recv().await {
                if let Ok(mut queue) = mailbox_for_task.lock() {
                    queue.push_back(message);
                }
            }
        });

        Self {
            mailbox,
            _receiver_task: receiver_task,
        }
    }
}

impl MessageReceive for TokioInbox {
    fn get(&mut self) -> Option<Message> {
        match self.mailbox.lock() {
            Ok(mut queue) => queue.pop_front(),
            Err(_) => None,
        }
    }
}

#[derive(Debug, Clone)]
pub struct TokioOutbox {
    tx: MessageTx,
}

impl TokioOutbox {
    pub fn new(tx: MessageTx) -> Self {
        Self { tx }
    }
}

impl MessageSend for TokioOutbox {
    fn send(&mut self, message: Message) {
        let _ = self.tx.send(message);
    }
}
