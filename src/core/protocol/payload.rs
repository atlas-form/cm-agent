use crate::protocol::{AgentId, TaskId, WorkerAssignment, WorkerId, WorkerReport};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TaskSpec {
    pub id: TaskId,
    pub description: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DecisionIntent {
    KeepCurrentTask,
    ReplaceCurrentTask { task: TaskSpec },
    IgnoreNewTask,
    ExecuteTask { task: TaskSpec },
    StrategyHint { hint: String },
    Ignore,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ControlSignal {
    Start,
    Pause,
    Resume,
    Shutdown,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Payload {
    HumanCommand {
        task: TaskSpec,
    },
    WorkerReportStarted {
        worker_id: WorkerId,
        task_id: TaskId,
    },
    WorkerReportFinished {
        worker_id: WorkerId,
        task_id: TaskId,
        output: Option<String>,
    },
    WorkerReportFailed {
        worker_id: WorkerId,
        task_id: TaskId,
        reason: String,
    },
    WorkerAssignment {
        assignment: WorkerAssignment,
    },
    WorkerReport {
        report: WorkerReport,
    },
    DecisionIntent {
        intent: DecisionIntent,
    },
    Control {
        signal: ControlSignal,
        target: Option<AgentId>,
    },
    Text {
        content: String,
    },
}
