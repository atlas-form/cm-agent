use serde::{Deserialize, Serialize};

use crate::protocol::{SessionId, TaskId, WorkerId};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
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

impl SessionEvent {
    pub const fn event_name(&self) -> &'static str {
        match self {
            Self::Started { .. } => "started",
            Self::CommanderThinking { .. } => "commander_thinking",
            Self::WorkerStarted { .. } => "worker_started",
            Self::LlmChunk { .. } => "llm_chunk",
            Self::WorkerFinished { .. } => "worker_finished",
            Self::Output { .. } => "output",
            Self::Failed { .. } => "failed",
            Self::Finished { .. } => "finished",
        }
    }
}
