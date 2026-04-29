use thiserror::Error;

#[derive(Error, Debug)]
pub enum ExternalError {
    #[error(transparent)]
    Config(#[from] toolcraft_config::error::Error),

    #[error(transparent)]
    Io(#[from] std::io::Error),
}
