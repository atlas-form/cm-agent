use std::sync::Arc;

use agent_core::{
    messaging::{MessageRx, MessageTx},
    protocol::{Message, WorkerId},
};
use agent_error::{LlmError, Result, SettingsError};
use agent_llm::llm::chat_completions::ChatCompletionsLlm;
use tokio::sync::mpsc;
use world::{
    LlmKey, WorkerProfile, init_world, register_commander_tx, register_llm,
    register_worker_profile, register_worker_tx,
};

use crate::startup::settings::{LlmConfig, Settings};

#[derive(Clone)]
pub struct WorldChannels {
    pub commander_in_tx: MessageTx,
    pub worker_in_tx: MessageTx,
}

pub struct WorldReceivers {
    pub commander_in_rx: MessageRx,
    pub worker_in_rx: MessageRx,
}

pub fn init_world_channels(settings: &Settings) -> Result<(WorldChannels, WorldReceivers)> {
    let (commander_in_tx, commander_in_rx) = mpsc::unbounded_channel::<Message>();
    let (worker_in_tx, worker_in_rx) = mpsc::unbounded_channel::<Message>();

    init_world();
    register_configured_llms(settings)?;
    register_commander_tx(commander_in_tx.clone());
    register_worker_tx(WorkerId("worker-1".to_string()), worker_in_tx.clone());
    register_worker_profile(WorkerProfile {
        worker_id: WorkerId("worker-1".to_string()),
        agent_id: "worker-1".to_string(),
        name: "General Worker".to_string(),
        description: "通用执行 worker，适合处理常规单步任务和基础动作执行。".to_string(),
        capabilities: vec![
            "general_execution".to_string(),
            "single_step_actions".to_string(),
            "basic_task_handling".to_string(),
        ],
        constraints: vec![
            "one_task_at_a_time".to_string(),
            "limited_to_registered_actions".to_string(),
        ],
        status: "ready".to_string(),
    });

    Ok((
        WorldChannels {
            commander_in_tx,
            worker_in_tx,
        },
        WorldReceivers {
            commander_in_rx,
            worker_in_rx,
        },
    ))
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
