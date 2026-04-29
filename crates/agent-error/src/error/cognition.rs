use thiserror::Error;

#[derive(Error, Debug, Clone)]
pub enum CognitionError {
    #[error("Decode error: {0}")]
    Decode(String),

    #[error("Prompt error: {0}")]
    Prompt(String),

    #[error("Input error: {0}")]
    Input(String),

    #[error("Internal error: {0}")]
    Internal(String),
}

impl CognitionError {
    pub fn decode(message: impl Into<String>) -> Self {
        Self::Decode(message.into())
    }

    pub fn prompt(message: impl Into<String>) -> Self {
        Self::Prompt(message.into())
    }

    pub fn input(message: impl Into<String>) -> Self {
        Self::Input(message.into())
    }

    pub fn internal(message: impl Into<String>) -> Self {
        Self::Internal(message.into())
    }
}

#[derive(Debug, Clone)]
pub struct CognitionFailure {
    pub reason: FailureReason,
    pub description: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FailureReason {
    InsufficientInput,
    ConflictingInput,
    OutOfScope,
    InternalError,
}
