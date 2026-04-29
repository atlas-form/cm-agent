#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WorkerState {
    Idle,
    Running,
    Paused,
    Shutdown,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WorkerPhase {
    Thinking,
    Acting,
    Finished,
    Failed,
}
