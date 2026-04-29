use crate::ActionId;

/// Action 执行后的客观事实记录。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ActionResult<O, E, M> {
    pub action_id: ActionId,
    pub status: ActionStatus,
    pub output: Option<O>,
    pub error: Option<E>,
    pub metadata: M,
}

/// ActionResult 的客观状态描述。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ActionStatus {
    Success,
    Failure,
    Timeout,
    Cancelled,
    Unreachable,
    Other(String),
}
