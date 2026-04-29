use serde::{Deserialize, Serialize};

use crate::protocol::{TaskId, WorkerId};

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct TaskGraphId(pub String);

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct TaskNodeId(pub String);

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TaskGraph {
    pub graph_id: TaskGraphId,
    pub root_task: String,
    pub nodes: Vec<TaskNode>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct TaskNode {
    pub id: TaskNodeId,
    pub role: String,
    pub worker_id: WorkerId,
    pub title: String,
    pub objective: String,
    pub input_refs: Vec<TaskNodeId>,
    pub acceptance: Vec<String>,
    pub max_attempts: u8,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorkerAssignment {
    pub graph_id: TaskGraphId,
    pub node_id: TaskNodeId,
    pub task_id: TaskId,
    pub worker_id: WorkerId,
    pub role: String,
    pub attempt: u8,
    pub objective: String,
    pub inputs: Vec<WorkerReport>,
    pub rework_instruction: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WorkerReport {
    pub graph_id: TaskGraphId,
    pub node_id: TaskNodeId,
    pub task_id: TaskId,
    pub worker_id: WorkerId,
    pub role: String,
    pub content: String,
    pub evidence: Vec<String>,
    pub open_questions: Vec<String>,
    pub status: WorkerReportStatus,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum WorkerReportStatus {
    Completed,
    Failed,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Evaluation {
    pub node_id: TaskNodeId,
    pub passed: bool,
    pub score: f32,
    pub reasons: Vec<String>,
    pub rework_instruction: Option<String>,
}
