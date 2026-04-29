use thiserror::Error;

#[derive(Error, Debug, Clone)]
pub enum PromptError {
    #[error("Prompt IO error: {0}")]
    Io(String),

    #[error("Prompt not found: {0}")]
    NotFound(String),

    #[error("Prompt decode error: {0}")]
    Decode(String),

    #[error("Prompt internal error: {0}")]
    Internal(String),
}

impl PromptError {
    pub fn io(message: impl Into<String>) -> Self {
        Self::Io(message.into())
    }

    pub fn not_found(path: impl Into<String>) -> Self {
        Self::NotFound(path.into())
    }

    pub fn decode(message: impl Into<String>) -> Self {
        Self::Decode(message.into())
    }

    pub fn internal(message: impl Into<String>) -> Self {
        Self::Internal(message.into())
    }
}
