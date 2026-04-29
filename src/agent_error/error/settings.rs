use thiserror::Error;

#[derive(Error, Debug, Clone)]
pub enum SettingsError {
    #[error("Invalid settings: {0}")]
    Invalid(String),

    #[error("Missing llm usage: {0}")]
    MissingLlmUsage(String),

    #[error("Unsupported llm provider: {0}")]
    UnsupportedProvider(String),
}

impl SettingsError {
    pub fn invalid(message: impl Into<String>) -> Self {
        Self::Invalid(message.into())
    }

    pub fn missing_llm_usage(usage: impl Into<String>) -> Self {
        Self::MissingLlmUsage(usage.into())
    }

    pub fn unsupported_provider(provider: impl Into<String>) -> Self {
        Self::UnsupportedProvider(provider.into())
    }
}
