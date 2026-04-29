use std::{
    cmp::Ordering,
    collections::HashMap,
    sync::{Arc, OnceLock, RwLock},
};

use agent_error::{LlmError, Result, SettingsError};
use agent_llm::llm::{Llm, chat_completions::ChatCompletionsLlm};

use crate::startup::settings::{LlmConfig, Settings};

pub type SharedLlm = Arc<dyn Llm + Send + Sync>;

#[derive(Debug, Clone, Hash, PartialEq, Eq)]
pub struct LlmKey {
    pub provider: String,
    pub model: String,
    pub endpoint: String,
    pub profile: String,
}

impl LlmKey {
    pub fn new(
        provider: impl Into<String>,
        model: impl Into<String>,
        endpoint: impl Into<String>,
        profile: impl Into<String>,
    ) -> Self {
        Self {
            provider: provider.into(),
            model: model.into(),
            endpoint: endpoint.into(),
            profile: profile.into(),
        }
    }
}

#[derive(Default)]
struct SharedLlmRegistry {
    inner: RwLock<HashMap<LlmKey, SharedLlm>>,
}

impl SharedLlmRegistry {
    fn register(&self, key: LlmKey, llm: SharedLlm) {
        let mut inner = self
            .inner
            .write()
            .expect("shared llm registry lock poisoned when register");
        inner.insert(key, llm);
    }

    fn first(&self) -> Option<SharedLlm> {
        let inner = self
            .inner
            .read()
            .expect("shared llm registry lock poisoned when first");

        inner
            .iter()
            .filter(|(key, _)| key.profile == "default")
            .min_by(|(left, _), (right, _)| compare_llm_key(left, right))
            .or_else(|| {
                inner
                    .iter()
                    .min_by(|(left, _), (right, _)| compare_llm_key(left, right))
            })
            .map(|(_, llm)| llm.clone())
    }
}

static SHARED_LLMS: OnceLock<SharedLlmRegistry> = OnceLock::new();

pub fn init_shared_services(settings: &Settings) -> Result<()> {
    register_configured_llms(settings)
}

pub fn get_default_llm() -> Option<SharedLlm> {
    shared_llms().first()
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
    shared_llms().register(key, Arc::new(llm));
    Ok(())
}

fn llm_key_from_config(config: &LlmConfig) -> LlmKey {
    config.key()
}

fn shared_llms() -> &'static SharedLlmRegistry {
    SHARED_LLMS.get_or_init(SharedLlmRegistry::default)
}

fn compare_llm_key(left: &LlmKey, right: &LlmKey) -> Ordering {
    (&left.profile, &left.provider, &left.model, &left.endpoint).cmp(&(
        &right.profile,
        &right.provider,
        &right.model,
        &right.endpoint,
    ))
}
