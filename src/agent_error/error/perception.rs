use thiserror::Error;

#[derive(Error, Debug, Clone)]
pub enum PerceptionError {
    #[error("Input error: {0}")]
    Input(String),

    #[error("Source error: {0}")]
    Source(String),

    #[error("Timeout: {0}")]
    Timeout(String),

    #[error("Internal error: {0}")]
    Internal(String),
}

impl PerceptionError {
    pub fn input(message: impl Into<String>) -> Self {
        Self::Input(message.into())
    }

    pub fn source(message: impl Into<String>) -> Self {
        Self::Source(message.into())
    }

    pub fn timeout(message: impl Into<String>) -> Self {
        Self::Timeout(message.into())
    }

    pub fn internal(message: impl Into<String>) -> Self {
        Self::Internal(message.into())
    }
}

#[derive(Debug, Clone)]
pub struct PerceptionFailure {
    pub reason: FailureReason,
    pub description: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FailureReason {
    InvalidInput,
    SourceUnavailable,
    Timeout,
    InternalError,
}
