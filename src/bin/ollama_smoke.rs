use cm_agent::{
    llm::{Llm, chat_completions::ChatCompletionsLlm},
    model::llm::{ChatMessage, LlmInput},
};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let base_url =
        std::env::var("OLLAMA_BASE_URL").unwrap_or_else(|_| "http://127.0.0.1:11434".to_string());
    let model = std::env::var("OLLAMA_MODEL").unwrap_or_else(|_| "gemma4:26b".to_string());
    let max_tokens = std::env::var("OLLAMA_MAX_TOKENS")
        .ok()
        .and_then(|value| value.parse::<u32>().ok())
        .unwrap_or(20_000);

    let llm = ChatCompletionsLlm::new(&base_url, &model, None)?
        .with_temperature(Some(0.0))
        .with_max_tokens(Some(max_tokens));

    let output = llm
        .chat_once(LlmInput {
            messages: vec![ChatMessage::user("请只回答 pong")],
        })
        .await?;

    println!("{}", output.get_content());
    Ok(())
}
