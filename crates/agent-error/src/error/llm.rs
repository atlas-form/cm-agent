use thiserror::Error;

#[derive(Error, Debug, Clone)]
pub enum LlmError {
    #[error("LLM error: {0}")]
    Message(String),
}

impl LlmError {
    pub fn message(message: impl Into<String>) -> Self {
        Self::Message(message.into())
    }
}
