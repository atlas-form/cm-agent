mod decoder;

use std::{path::Path, sync::Arc};

pub use decoder::*;
pub use model_gateway_rs::model::llm::ChatMessage as Message;
use model_gateway_rs::{
    llm::Llm,
    model::llm::{ChatMessage, LlmInput},
};

use crate::{
    Cognition, CognitionFailure, CognitionInput, CognitionResult, FailureReason,
    agent_utils::prompt::Prompt,
};

/// Cognition 引擎的最小结构：输入快照 -> 决策输出
pub struct CognitionEngine<L: ?Sized, D: ?Sized> {
    llm: Arc<L>,
    system_messages: Vec<Message>,
    decoder: Arc<D>,
    retry_policy: CognitionRetryPolicy,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct CognitionRetryPolicy {
    pub max_attempts: usize,
    pub retry_empty: bool,
    pub retry_llm_error: bool,
    pub repair_decode_error: bool,
}

impl Default for CognitionRetryPolicy {
    fn default() -> Self {
        Self {
            max_attempts: 3,
            retry_empty: true,
            retry_llm_error: true,
            repair_decode_error: true,
        }
    }
}

impl CognitionRetryPolicy {
    pub fn disabled() -> Self {
        Self {
            max_attempts: 1,
            retry_empty: false,
            retry_llm_error: false,
            repair_decode_error: false,
        }
    }

    fn max_attempts(self) -> usize {
        self.max_attempts.max(1)
    }
}

impl<L: ?Sized> CognitionEngine<L, JsonTemplateDecoder> {
    pub fn new(llm: Arc<L>, prompt_path: impl AsRef<Path>) -> crate::agent_error::Result<Self> {
        let prompt = Prompt::load_from_repo(prompt_path)?;
        let system_messages = vec![prompt.to_message(model_gateway_rs::model::role::Role::System)];
        let decoder = Arc::new(JsonTemplateDecoder::new(prompt.extract_json_schema()?));

        Ok(Self {
            llm,
            system_messages,
            decoder,
            retry_policy: CognitionRetryPolicy::default(),
        })
    }

    pub fn new_with_system_append(
        llm: Arc<L>,
        prompt_path: impl AsRef<Path>,
        system_append: impl Into<String>,
    ) -> crate::agent_error::Result<Self> {
        let prompt = Prompt::load_from_repo(prompt_path)?;
        let decoder = Arc::new(JsonTemplateDecoder::new(prompt.extract_json_schema()?));
        let content = format!("{}\n\n{}", prompt.content, system_append.into());
        let system_messages = vec![Message {
            role: model_gateway_rs::model::role::Role::System,
            content: content.into(),
        }];

        Ok(Self {
            llm,
            system_messages,
            decoder,
            retry_policy: CognitionRetryPolicy::default(),
        })
    }
}

impl<L: ?Sized, D: ?Sized> CognitionEngine<L, D> {
    pub fn with_parts(llm: Arc<L>, system_messages: Vec<Message>, decoder: Arc<D>) -> Self {
        Self {
            llm,
            system_messages,
            decoder,
            retry_policy: CognitionRetryPolicy::default(),
        }
    }

    pub fn with_retry_policy(mut self, retry_policy: CognitionRetryPolicy) -> Self {
        self.retry_policy = retry_policy;
        self
    }
}

#[async_trait::async_trait]
impl<L, D> Cognition for CognitionEngine<L, D>
where
    L: Llm + Sync + Send + 'static + ?Sized,
    D: CognitionDecoder + Sync + Send + 'static + ?Sized,
{
    async fn evaluate(&self, input: CognitionInput) -> CognitionResult {
        let mut base_messages = self.system_messages.clone();
        base_messages.push(ChatMessage::user(render_input(&input)));

        let max_attempts = self.retry_policy.max_attempts();
        let mut messages = base_messages.clone();
        let mut last_failure = CognitionFailure {
            reason: FailureReason::InternalError,
            description: "LLM cognition did not run".to_string(),
        };

        for attempt in 1 ..= max_attempts {
            let raw = match self.llm.chat_once(LlmInput { messages }).await {
                Ok(output) => output.get_content().to_string(),
                Err(err) => {
                    crate::log_error!(err);
                    last_failure = CognitionFailure {
                        reason: FailureReason::InternalError,
                        description: format!("LLM request failed on attempt {attempt}: {err}"),
                    };
                    if attempt < max_attempts && self.retry_policy.retry_llm_error {
                        messages = retry_messages(
                            &base_messages,
                            "上一轮 LLM 请求失败。请重新完成任务，只返回合法 JSON。",
                            attempt + 1,
                        );
                        continue;
                    }
                    break;
                }
            };

            if raw.trim().is_empty() {
                crate::log_error_msg!("LLM returned empty response");
                last_failure = CognitionFailure {
                    reason: FailureReason::InternalError,
                    description: format!("LLM returned empty response on attempt {attempt}"),
                };
                if attempt < max_attempts && self.retry_policy.retry_empty {
                    messages = retry_messages(
                        &base_messages,
                        "上一轮输出为空。请重新完成任务，只返回合法 JSON，不要 \
                         Markdown，不要解释。",
                        attempt + 1,
                    );
                    continue;
                }
                break;
            }

            match self.decoder.decode(&raw) {
                Ok(output) => return CognitionResult::Success(output),
                Err(failure) => {
                    let snippet = raw_snippet(&raw);
                    let message = format!(
                        "cognition decode failure on attempt {attempt}/{max_attempts}: \
                         reason={:?}, description={}, raw={}",
                        failure.reason, failure.description, snippet
                    );
                    crate::log_error_msg!(&message);
                    last_failure = CognitionFailure {
                        reason: failure.reason.clone(),
                        description: format!(
                            "decode failed on attempt {attempt}/{max_attempts}: {}; raw={snippet}",
                            failure.description
                        ),
                    };
                    if attempt < max_attempts && self.retry_policy.repair_decode_error {
                        messages = repair_messages(&base_messages, &raw, &failure, attempt + 1);
                        continue;
                    }
                    break;
                }
            }
        }

        CognitionResult::Failure(CognitionFailure {
            reason: last_failure.reason,
            description: format!(
                "cognition failed after {max_attempts} attempt(s): {}",
                last_failure.description
            ),
        })
    }
}

fn retry_messages(
    base_messages: &[Message],
    instruction: &str,
    next_attempt: usize,
) -> Vec<Message> {
    let mut messages = base_messages.to_vec();
    messages.push(ChatMessage::user(format!(
        "Retry attempt {next_attempt}: {instruction}"
    )));
    messages
}

fn repair_messages(
    base_messages: &[Message],
    raw: &str,
    failure: &CognitionFailure,
    next_attempt: usize,
) -> Vec<Message> {
    let mut messages = base_messages.to_vec();
    messages.push(ChatMessage::assistant(raw.to_string()));
    messages.push(ChatMessage::user(format!(
        "Repair attempt {next_attempt}: 上一条输出无法被系统解析为目标 JSON。\n请只返回合法 \
         JSON，不要 Markdown，不要解释。\n必须满足系统提示词中的 JSON schema \
         和字段结构。\n解析错误: {}\n原始输出片段: {}",
        failure.description,
        raw_snippet(raw)
    )));
    messages
}

fn raw_snippet(raw: &str) -> String {
    raw.trim().chars().take(500).collect()
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

#[cfg(test)]
mod tests {
    use std::{
        collections::VecDeque,
        sync::{Arc, Mutex},
    };

    use async_trait::async_trait;
    use model_gateway_rs::{
        error::Result as LlmResult,
        llm::Llm,
        model::llm::{ChatMessage, LlmInput, LlmOutput},
    };
    use serde_json::json;
    use toolcraft_request::ByteStream;

    use super::{CognitionEngine, CognitionRetryPolicy, JsonTemplateDecoder, Message};
    use crate::{Cognition, CognitionInput, Context, Intent, IntentKind};

    #[derive(Default)]
    struct SequenceLlm {
        outputs: Mutex<VecDeque<String>>,
        calls: Mutex<usize>,
    }

    impl SequenceLlm {
        fn new(outputs: impl IntoIterator<Item = impl Into<String>>) -> Self {
            Self {
                outputs: Mutex::new(outputs.into_iter().map(Into::into).collect()),
                calls: Mutex::new(0),
            }
        }

        fn calls(&self) -> usize {
            *self
                .calls
                .lock()
                .expect("calls mutex should not be poisoned")
        }
    }

    #[async_trait]
    impl Llm for SequenceLlm {
        async fn chat_once(&self, _input: LlmInput) -> LlmResult<LlmOutput> {
            *self
                .calls
                .lock()
                .expect("calls mutex should not be poisoned") += 1;
            let content = self
                .outputs
                .lock()
                .expect("outputs mutex should not be poisoned")
                .pop_front()
                .unwrap_or_default();
            Ok(LlmOutput {
                message: Some(ChatMessage::assistant(content)),
                usage: None,
            })
        }

        async fn chat_stream(&self, _input: LlmInput) -> LlmResult<ByteStream> {
            unimplemented!("streaming is not used by cognition engine tests")
        }
    }

    fn engine(llm: Arc<SequenceLlm>) -> CognitionEngine<SequenceLlm, JsonTemplateDecoder> {
        CognitionEngine::with_parts(
            llm,
            vec![Message::system("Return JSON with field answer.")],
            Arc::new(JsonTemplateDecoder::new(json!({"answer": ""}))),
        )
    }

    fn input() -> CognitionInput {
        CognitionInput {
            intent: Intent {
                id: "intent-1".to_string(),
                kind: IntentKind::Decision,
                description: "answer".to_string(),
            },
            context: Context::default(),
        }
    }

    #[tokio::test]
    async fn retries_empty_output_before_failing_worker() {
        let llm = Arc::new(SequenceLlm::new(["", r#"{"answer":"ok"}"#]));

        let result = engine(llm.clone()).evaluate(input()).await;

        assert!(matches!(result, crate::CognitionResult::Success(_)));
        assert_eq!(llm.calls(), 2);
    }

    #[tokio::test]
    async fn repairs_decode_failure_with_next_llm_attempt() {
        let llm = Arc::new(SequenceLlm::new(["not json", r#"{"answer":"fixed"}"#]));

        let result = engine(llm.clone()).evaluate(input()).await;

        assert!(matches!(result, crate::CognitionResult::Success(_)));
        assert_eq!(llm.calls(), 2);
    }

    #[tokio::test]
    async fn fails_after_configured_attempts() {
        let llm = Arc::new(SequenceLlm::new(["bad", "still bad", "again bad"]));

        let result = engine(llm.clone())
            .with_retry_policy(CognitionRetryPolicy {
                max_attempts: 3,
                ..CognitionRetryPolicy::default()
            })
            .evaluate(input())
            .await;

        let crate::CognitionResult::Failure(failure) = result else {
            panic!("result should fail");
        };
        assert!(failure.description.contains("after 3 attempt"));
        assert_eq!(llm.calls(), 3);
    }
}
