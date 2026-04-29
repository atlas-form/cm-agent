use agent_error::{Error, LlmError, Result};
use async_trait::async_trait;
use serde::{Deserialize, Serialize};
use toolcraft_request::{ByteStream, HeaderMap, Request};

use crate::{
    llm::Llm,
    model::{
        llm::{ChatMessage, LlmInput, LlmOutput},
        role::Role,
    },
};

const CHAT_COMPLETIONS_ENDPOINT: &str = "v1/chat/completions";

/// Standard chat completions client.
///
/// Callers only need to pass the service root URL, for example:
/// `http://127.0.0.1:11434`
///
/// The client will send requests to `/v1/chat/completions`.
pub struct ChatCompletionsLlm {
    request: Request,
    model: String,
    default_max_tokens: Option<u32>,
    default_temperature: Option<f32>,
}

impl ChatCompletionsLlm {
    pub fn new(base_url: &str, model: &str, api_key: Option<&str>) -> Result<Self> {
        let mut request =
            Request::new().map_err(|err| Error::from(LlmError::message(format!("{err:?}"))))?;
        request
            .set_base_url(base_url)
            .map_err(|err| Error::from(LlmError::message(format!("{err:?}"))))?;

        let mut headers = HeaderMap::new();
        headers
            .insert("Content-Type", "application/json".to_string())
            .map_err(|err| Error::from(LlmError::message(err.to_string())))?;

        if let Some(api_key) = api_key {
            headers
                .insert("Authorization", format!("Bearer {api_key}"))
                .map_err(|err| Error::from(LlmError::message(err.to_string())))?;
        }

        request.set_default_headers(headers);

        Ok(Self {
            request,
            model: model.to_string(),
            default_max_tokens: None,
            default_temperature: None,
        })
    }

    pub fn with_api_key(mut self, api_key: &str) -> Result<Self> {
        let mut headers = HeaderMap::new();
        headers
            .insert("Content-Type", "application/json".to_string())
            .map_err(|err| Error::from(LlmError::message(err.to_string())))?;
        headers
            .insert("Authorization", format!("Bearer {api_key}"))
            .map_err(|err| Error::from(LlmError::message(err.to_string())))?;
        self.request.set_default_headers(headers);
        Ok(self)
    }

    pub fn with_max_tokens(mut self, max_tokens: Option<u32>) -> Self {
        self.default_max_tokens = max_tokens;
        self
    }

    pub fn with_temperature(mut self, temperature: Option<f32>) -> Self {
        self.default_temperature = temperature;
        self
    }
}

#[async_trait]
impl Llm for ChatCompletionsLlm {
    async fn chat_once(&self, input: LlmInput) -> Result<LlmOutput> {
        let body = OpenAiChatRequest {
            model: self.model.clone(),
            messages: input.messages,
            temperature: self.default_temperature,
            max_tokens: self.default_max_tokens,
            stream: Some(false),
        };
        let payload = serde_json::to_value(body)
            .map_err(|err| Error::from(LlmError::message(format!("{err:?}"))))?;
        let response = self
            .request
            .post(CHAT_COMPLETIONS_ENDPOINT, &payload, None)
            .await
            .map_err(|err| Error::from(LlmError::message(format!("{err:?}"))))?;
        let json: OpenAiChatResponse = response
            .json()
            .await
            .map_err(|err| Error::from(LlmError::message(format!("{err:?}"))))?;
        Ok(json.into())
    }

    async fn chat_stream(&self, input: LlmInput) -> Result<ByteStream> {
        let body = OpenAiChatRequest {
            model: self.model.clone(),
            messages: input.messages,
            temperature: self.default_temperature,
            max_tokens: self.default_max_tokens,
            stream: Some(true),
        };
        let payload = serde_json::to_value(body)
            .map_err(|err| Error::from(LlmError::message(format!("{err:?}"))))?;
        let response = self
            .request
            .post_stream(CHAT_COMPLETIONS_ENDPOINT, &payload, None)
            .await
            .map_err(|err| Error::from(LlmError::message(format!("{err:?}"))))?;
        Ok(response)
    }
}

#[derive(Debug, Clone, Serialize)]
struct OpenAiChatRequest {
    model: String,
    messages: Vec<ChatMessage>,
    #[serde(skip_serializing_if = "Option::is_none")]
    temperature: Option<f32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    max_tokens: Option<u32>,
    #[serde(skip_serializing_if = "Option::is_none")]
    stream: Option<bool>,
}

#[derive(Debug, Deserialize)]
struct OpenAiChatResponse {
    choices: Vec<OpenAiChoice>,
    usage: Option<OpenAiUsage>,
}

#[derive(Debug, Deserialize)]
struct OpenAiChoice {
    message: OpenAiMessage,
}

#[derive(Debug, Deserialize)]
struct OpenAiMessage {
    role: Role,
    content: String,
}

#[derive(Debug, Deserialize)]
struct OpenAiUsage {
    total_tokens: Option<u32>,
}

impl From<OpenAiChatResponse> for LlmOutput {
    fn from(response: OpenAiChatResponse) -> Self {
        let message = response
            .choices
            .into_iter()
            .next()
            .map(|choice| ChatMessage {
                role: choice.message.role,
                content: choice.message.content,
            });

        LlmOutput {
            message,
            usage: response.usage.and_then(|usage| usage.total_tokens),
        }
    }
}
