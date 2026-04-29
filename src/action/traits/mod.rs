use crate::ActionState;

/// Action 核心抽象。
///
/// Runtime 负责驱动 Action 的生命周期与执行授权。
pub trait Action {
    type Result;

    /// 当前生命周期状态。
    fn state(&self) -> ActionState;

    /// 单次执行机会，由 Runtime 授权。
    fn drive(&mut self);

    /// 由 Runtime 发起的暂停信号。
    fn suspend(&mut self);

    /// 由 Runtime 发起的恢复信号。
    fn resume(&mut self);

    /// 提取最终执行结果。只能在 Completed / Aborted 状态下成功返回。
    fn take_result(&mut self) -> Option<Self::Result>;
}
