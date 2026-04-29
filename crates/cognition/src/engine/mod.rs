mod decoder;

use std::{path::Path, sync::Arc};

pub use agent_llm::model::llm::ChatMessage as Message;
use agent_llm::{
    llm::Llm,
    model::llm::{ChatMessage, LlmInput},
};
use agent_utils::prompt::Prompt;
pub use decoder::*;

use crate::{Cognition, CognitionFailure, CognitionInput, CognitionResult, FailureReason};

/// Cognition 引擎的最小结构：输入快照 -> 决策输出
pub struct CognitionEngine<L: ?Sized, D: ?Sized> {
    llm: Arc<L>,
    system_messages: Vec<Message>,
    decoder: Arc<D>,
}

impl<L: ?Sized> CognitionEngine<L, JsonTemplateDecoder> {
    pub fn new(llm: Arc<L>, prompt_path: impl AsRef<Path>) -> agent_error::Result<Self> {
        let prompt = Prompt::load_from_repo(prompt_path)?;
        let system_messages = vec![prompt.to_message(agent_llm::model::role::Role::System)];
        let decoder = Arc::new(JsonTemplateDecoder::new(prompt.extract_json_schema()?));

        Ok(Self {
            llm,
            system_messages,
            decoder,
        })
    }
}

impl<L: ?Sized, D: ?Sized> CognitionEngine<L, D> {
    pub fn with_parts(llm: Arc<L>, system_messages: Vec<Message>, decoder: Arc<D>) -> Self {
        Self {
            llm,
            system_messages,
            decoder,
        }
    }
}

#[async_trait::async_trait]
impl<L, D> Cognition for CognitionEngine<L, D>
where
    L: Llm + Sync + Send + 'static + ?Sized,
    D: CognitionDecoder + Sync + Send + 'static + ?Sized,
{
    async fn evaluate(&self, input: CognitionInput) -> CognitionResult {
        let mut messages = self.system_messages.clone();
        messages.push(ChatMessage::user(&render_input(&input)));

        let raw = match self.llm.chat_once(LlmInput { messages }).await {
            Ok(output) => output.get_content().to_string(),
            Err(err) => {
                agent_utils::log_error!(err);
                return CognitionResult::Failure(CognitionFailure {
                    reason: FailureReason::InternalError,
                    description: err.to_string(),
                });
            }
        };
        if raw.trim().is_empty() {
            agent_utils::log_error_msg!("LLM returned empty response");
            return CognitionResult::Failure(CognitionFailure {
                reason: FailureReason::InternalError,
                description: "LLM returned empty response".to_string(),
            });
        }

        match self.decoder.decode(&raw) {
            Ok(output) => CognitionResult::Success(output),
            Err(failure) => {
                let snippet = raw.trim().chars().take(500).collect::<String>();
                let message = format!(
                    "cognition decode failure: reason={:?}, description={}, raw={}",
                    failure.reason, failure.description, snippet
                );
                agent_utils::log_error_msg!(&message);
                CognitionResult::Failure(failure)
            }
        }
    }
}

fn render_input(input: &CognitionInput) -> String {
    let facts = if input.context.facts.is_empty() {
        "- (none)".to_string()
    } else {
        input
            .context
            .facts
            .iter()
            .map(|fact| {
                format!(
                    "- source: {} | reliability: {} | content: {}",
                    fact.source, fact.reliability, fact.content
                )
            })
            .collect::<Vec<_>>()
            .join("\n")
    };

    let metadata = if input.context.metadata.is_empty() {
        "- (none)".to_string()
    } else {
        input
            .context
            .metadata
            .iter()
            .map(|(key, value)| format!("- {}: {}", key, value))
            .collect::<Vec<_>>()
            .join("\n")
    };

    format!(
        "Intent:\n- id: {}\n- kind: {:?}\n- description: {}\n\nContext facts:\n{}\n\nContext \
         metadata:\n{}",
        input.intent.id, input.intent.kind, input.intent.description, facts, metadata
    )
}
