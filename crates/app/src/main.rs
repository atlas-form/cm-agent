mod startup;

use agent_error::Result;
use agent_utils::logging::{LogLevel, init_tracing_with_level};
use startup::AppStartup;

#[tokio::main(flavor = "multi_thread", worker_threads = 4)]
async fn main() -> Result<()> {
    init_tracing_with_level(LogLevel::Info);
    let mut startup = AppStartup::boot()?;
    startup.run_ui().await?;
    startup.shutdown().await;
    Ok(())
}
