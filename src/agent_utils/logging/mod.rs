use tracing_subscriber::{EnvFilter, fmt as ts_fmt};

#[derive(Debug, Clone, Copy)]
pub enum LogLevel {
    Trace,
    Debug,
    Info,
    Warn,
    Error,
}

impl LogLevel {
    fn as_str(self) -> &'static str {
        match self {
            LogLevel::Trace => "trace",
            LogLevel::Debug => "debug",
            LogLevel::Info => "info",
            LogLevel::Warn => "warn",
            LogLevel::Error => "error",
        }
    }
}

/// Initialize global tracing subscriber once.
///
/// Honors RUST_LOG if set, otherwise defaults to "info".
pub fn init_tracing() {
    init_tracing_with_level(LogLevel::Info);
}

/// Initialize tracing with a default level when RUST_LOG is not set.
pub fn init_tracing_with_level(level: LogLevel) {
    let filter =
        EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new(level.as_str()));
    let _ = ts_fmt().pretty().with_env_filter(filter).try_init();
}

#[macro_export]
macro_rules! log_error {
    ($err:expr) => {
        $crate::tracing::error!(error = %$err);
    };
}

#[macro_export]
macro_rules! log_error_msg {
    ($msg:expr) => {
        $crate::tracing::error!(message = $msg);
    };
}

#[macro_export]
macro_rules! log_trace {
    ($msg:expr) => {
        $crate::tracing::trace!(message = $msg);
    };
}

#[macro_export]
macro_rules! log_info {
    ($msg:expr) => {
        $crate::tracing::info!(message = $msg);
    };
}

#[macro_export]
macro_rules! log_warn {
    ($msg:expr) => {
        $crate::tracing::warn!(message = $msg);
    };
}
