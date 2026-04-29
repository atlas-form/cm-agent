use serde::{Deserialize, Serialize};

use crate::protocol::{SessionId, TaskId, WorkerId};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
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
    CollaborationStarted {
        session_id: SessionId,
        primary_worker_id: WorkerId,
        support_worker_ids: Vec<WorkerId>,
    },
    CollaborationWorkerStarted {
        session_id: SessionId,
        worker_id: WorkerId,
        task_id: TaskId,
    },
    CollaborationWorkerFinished {
        session_id: SessionId,
        worker_id: WorkerId,
        task_id: TaskId,
        content: Option<String>,
    },
    CollaborationFinished {
        session_id: SessionId,
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
            Self::CollaborationStarted { .. } => "collaboration_started",
            Self::CollaborationWorkerStarted { .. } => "collaboration_worker_started",
            Self::CollaborationWorkerFinished { .. } => "collaboration_worker_finished",
            Self::CollaborationFinished { .. } => "collaboration_finished",
            Self::Output { .. } => "output",
            Self::Failed { .. } => "failed",
            Self::Finished { .. } => "finished",
        }
    }
}
