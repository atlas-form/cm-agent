mod tokio_channel;

use crate::protocol::Message;

pub trait MessageReceive {
    fn get(&mut self) -> Option<Message>;
}

pub trait MessageSend {
    fn send(&mut self, message: Message);
}

pub use tokio_channel::*;
