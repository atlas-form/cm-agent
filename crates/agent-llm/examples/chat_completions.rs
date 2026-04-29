use agent_llm::{
    llm::{Llm, chat_completions::ChatCompletionsLlm},
    model::llm::{ChatMessage, LlmInput},
};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let base_url = "http://127.0.0.1:11434";
    let model = "gpt-oss";

    let llm = ChatCompletionsLlm::new(base_url, model, None)?
        .with_temperature(Some(0.7))
        .with_max_tokens(Some(20_000));

    let input = LlmInput {
        messages: vec![
            ChatMessage::system("You are a helpful assistant."),
            ChatMessage::user("hi"),
        ],
    };

    let output = llm.chat_once(input).await?;
    println!("{}", output.get_content());

    Ok(())
}
