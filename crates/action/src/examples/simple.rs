use crate::{
    Action, ActionId, ActionLifecycle, ActionResult, ActionState, ActionStatus, ActionTarget,
    ActionType,
};

/// 一个最小 Action 示例：在第一次 drive 时立即完成。
#[derive(Debug, Clone)]
pub struct SimpleAction {
    lifecycle: ActionLifecycle,
    result: Option<ActionResult<String, String, ()>>,
}

impl SimpleAction {
    pub fn new(action_id: impl Into<ActionId>) -> Self {
        let _ = action_id.into();
        Self {
            lifecycle: ActionLifecycle::new(),
            result: None,
        }
    }

    pub fn with_ready_result(action_id: impl Into<ActionId>, output: impl Into<String>) -> Self {
        let action_id = action_id.into();
        let result = ActionResult {
            action_id,
            status: ActionStatus::Success,
            output: Some(output.into()),
            error: None,
            metadata: (),
        };
        Self {
            lifecycle: ActionLifecycle::new(),
            result: Some(result),
        }
    }

    pub fn activate(&mut self) {
        let _ = self.lifecycle.transition(ActionState::Active);
    }
}

impl Action for SimpleAction {
    type Result = ActionResult<String, String, ()>;

    fn state(&self) -> ActionState {
        self.lifecycle.state()
    }

    fn drive(&mut self) {
        if self.state() != ActionState::Active {
            return;
        }

        if self.result.is_some() {
            let _ = self.lifecycle.transition(ActionState::Completed);
            return;
        }

        self.result = Some(ActionResult {
            action_id: ActionId("simple".to_string()),
            status: ActionStatus::Success,
            output: Some("done".to_string()),
            error: None,
            metadata: (),
        });
        let _ = self.lifecycle.transition(ActionState::Completed);
    }

    fn suspend(&mut self) {
        let _ = self.lifecycle.transition(ActionState::Suspended);
    }

    fn resume(&mut self) {
        let _ = self.lifecycle.transition(ActionState::Active);
    }

    fn take_result(&mut self) -> Option<Self::Result> {
        if matches!(self.state(), ActionState::Completed | ActionState::Aborted) {
            self.result.take()
        } else {
            None
        }
    }
}

#[allow(dead_code)]
fn _example_usage() {
    let mut action = SimpleAction::with_ready_result("simple", "ok");
    action.activate();
    action.drive();
    let _ = action.take_result();
    let _ = ActionType("example".to_string());
    let _ = ActionTarget("local".to_string());
}
