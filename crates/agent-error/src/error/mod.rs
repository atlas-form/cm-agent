pub mod cognition;
pub mod external;
pub mod llm;
pub mod perception;
pub mod prompt;
pub mod settings;

use thiserror::Error;

/// Core error enum that encompasses all error types.
#[derive(Error, Debug)]
pub enum Error {
    /// Cognition crate errors
    #[error(transparent)]
    Cognition(#[from] cognition::CognitionError),

    /// LLM tool errors
    #[error(transparent)]
    Llm(#[from] llm::LlmError),

    /// Perception errors
    #[error(transparent)]
    Perception(#[from] perception::PerceptionError),

    /// Prompt errors
    #[error(transparent)]
    Prompt(#[from] prompt::PromptError),

    /// Settings errors
    #[error(transparent)]
    Settings(#[from] settings::SettingsError),

    /// External dependency errors
    #[error(transparent)]
    External(#[from] external::ExternalError),
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ErrorKind {
    Cognition,
    Llm,
    Perception,
    Prompt,
    Settings,
    External,
    Internal,
}

impl Error {
    pub fn kind(&self) -> ErrorKind {
        match self {
            Error::Cognition(_) => ErrorKind::Cognition,
            Error::Llm(_) => ErrorKind::Llm,
            Error::Perception(_) => ErrorKind::Perception,
            Error::Prompt(_) => ErrorKind::Prompt,
            Error::Settings(_) => ErrorKind::Settings,
            Error::External(_) => ErrorKind::External,
        }
    }
}

impl From<toolcraft_config::error::Error> for Error {
    fn from(value: toolcraft_config::error::Error) -> Self {
        Self::External(external::ExternalError::from(value))
    }
}

impl From<std::io::Error> for Error {
    fn from(value: std::io::Error) -> Self {
        Self::External(external::ExternalError::from(value))
    }
}

/// Result type alias using Error.
pub type Result<T> = std::result::Result<T, Error>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_cognition_error() {
        let err = Error::from(cognition::CognitionError::internal("boom"));
        assert_eq!(err.to_string(), "Internal error: boom");
    }

    #[test]
    fn test_prompt_error() {
        let err = Error::from(prompt::PromptError::not_found("prompts/a.md"));
        assert_eq!(err.to_string(), "Prompt not found: prompts/a.md");
    }
}
