use std::sync::Arc;

use serde_json::json;

mod example_support;

#[tokio::main]
async fn main() -> cm_agent::api::Result<()> {
    let manager = Arc::new(example_support::build_agent_manager());

    let result = manager
        .run_request(example_support::agent_request(
            "web-user-1",
            "http-session-1",
            "hello agent",
        ))
        .await?;

    let response = json!({
        "session_id": result.session_id,
        "output": result.output
    });

    println!(
        "{}",
        serde_json::to_string_pretty(&response).expect("serialize response")
    );
    println!("active_sessions={}", manager.active_session_count());
    Ok(())
}
