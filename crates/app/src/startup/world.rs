use std::sync::Arc;

use agent_error::{LlmError, Result, SettingsError};
use agent_llm::llm::chat_completions::ChatCompletionsLlm;
use world::{LlmKey, init_world, register_llm};

use crate::startup::settings::{LlmConfig, Settings};

pub fn init_shared_services(settings: &Settings) -> Result<()> {
    init_world();
    register_configured_llms(settings)
}

fn register_configured_llms(settings: &Settings) -> Result<()> {
    if settings.llms.is_empty() {
        return Err(SettingsError::invalid("no llms configured in config/services.toml").into());
    }

    for config in &settings.llms {
        register_llm_from_config(config)?;
    }

    Ok(())
}

fn register_llm_from_config(config: &LlmConfig) -> Result<()> {
    match config.provider.as_str() {
        "chat_completions" => register_chat_completions_llm(config),
        provider => Err(SettingsError::unsupported_provider(provider).into()),
    }
}

fn register_chat_completions_llm(config: &LlmConfig) -> Result<()> {
    let api_key = (!config.api_key.is_empty()).then_some(config.api_key.as_str());
    let llm = ChatCompletionsLlm::new(&config.base_url, &config.model, api_key)
        .map(|client| {
            client
                .with_max_tokens(config.max_tokens)
                .with_temperature(config.temperature)
        })
        .map_err(|err| {
            LlmError::message(format!(
                "init {} llm failed for model {}: {err}",
                config.provider, config.model
            ))
        })?;

    let key = llm_key_from_config(config);
    register_llm(key, Arc::new(llm));
    Ok(())
}

fn llm_key_from_config(config: &LlmConfig) -> LlmKey {
    config.key()
}
