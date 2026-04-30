use serde::{Deserialize, Serialize};

use crate::protocol::{
    Evaluation, SessionId, TaskGraph, TaskGraphId, TaskId, TaskNodeId, WorkerId, WorkerReport,
};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum SessionEvent {
    Started {
        session_id: SessionId,
    },
    MemoryLoaded {
        session_id: SessionId,
        count: usize,
        kinds: Vec<String>,
    },
    MemoryPersisted {
        session_id: SessionId,
        count: usize,
    },
    MemoryCompacted {
        session_id: SessionId,
        input_records: usize,
        output_records: usize,
        truncated_records: usize,
        merged_records: usize,
        dropped_records: usize,
        before_chars: usize,
        after_chars: usize,
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
    TaskGraphPlanned {
        session_id: SessionId,
        graph: TaskGraph,
    },
    TaskNodeReady {
        session_id: SessionId,
        graph_id: TaskGraphId,
        node_id: TaskNodeId,
        worker_id: WorkerId,
    },
    TaskNodeStarted {
        session_id: SessionId,
        graph_id: TaskGraphId,
        node_id: TaskNodeId,
        worker_id: WorkerId,
        attempt: u8,
    },
    TaskNodeReported {
        session_id: SessionId,
        graph_id: TaskGraphId,
        node_id: TaskNodeId,
        worker_id: WorkerId,
        report: Box<WorkerReport>,
    },
    TaskNodeEvaluated {
        session_id: SessionId,
        graph_id: TaskGraphId,
        evaluation: Evaluation,
    },
    TaskNodeReworkRequested {
        session_id: SessionId,
        graph_id: TaskGraphId,
        node_id: TaskNodeId,
        worker_id: WorkerId,
        attempt: u8,
        instruction: String,
    },
    TaskNodePassed {
        session_id: SessionId,
        graph_id: TaskGraphId,
        node_id: TaskNodeId,
    },
    TaskNodeFailed {
        session_id: SessionId,
        graph_id: TaskGraphId,
        node_id: TaskNodeId,
        reason: String,
    },
    TaskGraphFinished {
        session_id: SessionId,
        graph_id: TaskGraphId,
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
            Self::MemoryLoaded { .. } => "memory_loaded",
            Self::MemoryPersisted { .. } => "memory_persisted",
            Self::MemoryCompacted { .. } => "memory_compacted",
            Self::CommanderThinking { .. } => "commander_thinking",
            Self::WorkerStarted { .. } => "worker_started",
            Self::LlmChunk { .. } => "llm_chunk",
            Self::WorkerFinished { .. } => "worker_finished",
            Self::CollaborationStarted { .. } => "collaboration_started",
            Self::CollaborationWorkerStarted { .. } => "collaboration_worker_started",
            Self::CollaborationWorkerFinished { .. } => "collaboration_worker_finished",
            Self::CollaborationFinished { .. } => "collaboration_finished",
            Self::TaskGraphPlanned { .. } => "task_graph_planned",
            Self::TaskNodeReady { .. } => "task_node_ready",
            Self::TaskNodeStarted { .. } => "task_node_started",
            Self::TaskNodeReported { .. } => "task_node_reported",
            Self::TaskNodeEvaluated { .. } => "task_node_evaluated",
            Self::TaskNodeReworkRequested { .. } => "task_node_rework_requested",
            Self::TaskNodePassed { .. } => "task_node_passed",
            Self::TaskNodeFailed { .. } => "task_node_failed",
            Self::TaskGraphFinished { .. } => "task_graph_finished",
            Self::Output { .. } => "output",
            Self::Failed { .. } => "failed",
            Self::Finished { .. } => "finished",
        }
    }
}
