use crate::protocol::{SessionId, TaskId, WorkerId};

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SessionEvent {
    Started {
        session_id: SessionId,
    },
    CommanderThinking {
        session_id: SessionId,
    },
    WorkerStarted {
        session_id: SessionId,
        worker_id: WorkerId,
        task_id: TaskId,
    },
    LlmChunk {
        session_id: SessionId,
        content: String,
    },
    WorkerFinished {
        session_id: SessionId,
        worker_id: WorkerId,
        task_id: TaskId,
    },
    Output {
        session_id: SessionId,
        content: String,
    },
    Failed {
        session_id: SessionId,
        reason: String,
    },
    Finished {
        session_id: SessionId,
    },
}
