#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CommanderState {
    Idle,
    Running,
    Shutdown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CommanderPhase {
    Thinking,
    Idle,
}
