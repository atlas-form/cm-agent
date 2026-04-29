pub mod error;

pub use error::{
    Error, ErrorKind, Result,
    cognition::{CognitionError, CognitionFailure, FailureReason},
    external::ExternalError,
    llm::LlmError,
    perception::{FailureReason as PerceptionFailureReason, PerceptionError, PerceptionFailure},
    prompt::PromptError,
    settings::SettingsError,
};
