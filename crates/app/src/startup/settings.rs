use agent_error::Result;
use serde::Deserialize;
use toolcraft_config::load_settings;

use crate::startup::shared_services::LlmKey;

pub const DEFAULT_SERVICES_CONFIG_PATH: &str = "config/services.toml";

#[derive(Debug, Clone, Deserialize)]
pub struct LlmConfig {
    #[serde(default = "default_provider")]
    pub provider: String,
    #[serde(default = "default_profile")]
    pub profile: String,
    pub base_url: String,
    pub model: String,
    #[serde(default)]
    pub api_key: String,
    #[serde(default)]
    pub max_tokens: Option<u32>,
    #[serde(default)]
    pub temperature: Option<f32>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Settings {
    pub llms: Vec<LlmConfig>,
}

impl Settings {
    pub fn load_default() -> Result<Self> {
        Self::load(DEFAULT_SERVICES_CONFIG_PATH)
    }

    pub fn load(config_path: &str) -> Result<Self> {
        let r = load_settings(config_path)?;
        Ok(r)
    }
}

impl LlmConfig {
    pub fn key(&self) -> LlmKey {
        LlmKey::new(
            self.provider.clone(),
            self.model.clone(),
            self.base_url.clone(),
            self.profile.clone(),
        )
    }
}

fn default_provider() -> String {
    "chat_completions".to_string()
}

fn default_profile() -> String {
    "default".to_string()
}
