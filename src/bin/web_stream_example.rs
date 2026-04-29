use std::sync::Arc;

use cm_agent::api::SessionEvent;

mod example_support;

#[tokio::main]
async fn main() -> cm_agent::api::Result<()> {
    let manager = Arc::new(example_support::build_agent_manager());
    let mut events = manager.run_stream(example_support::agent_request(
        "web-user-2",
        "sse-session-1",
        "stream hello",
    ))?;

    while let Some(event) = events.recv().await {
        print_sse_event(&event);
        if matches!(
            event,
            SessionEvent::Finished { .. } | SessionEvent::Failed { .. }
        ) {
            break;
        }
    }

    println!("active_sessions={}", manager.active_session_count());
    Ok(())
}

fn print_sse_event(event: &SessionEvent) {
    println!("event: {}", event.event_name());
    println!(
        "data: {}",
        serde_json::to_string(event).expect("serialize session event")
    );
    println!();
}
