use super::BoxedAction;
use crate::{
    action::{Action, ActionResult, ActionState},
    cognition::CognitionOutput,
};

pub(crate) fn decision_to_action(output: &CognitionOutput) -> Option<BoxedAction> {
    let kind = output
        .get("decision")
        .and_then(|decision| decision.get("kind"))
        .and_then(|kind| kind.as_str());

    match kind {
        Some("ActionIntent") => Some(Box::new(DecisionAction::new())),
        _ => None,
    }
}

/// 临时占位 Action：仅用于打通 Worker 框架阶段流转；
/// 具体 Action 语义与构造规则待后续架构文档冻结后再替换。
#[derive(Debug)]
struct DecisionAction {
    state: ActionState,
    result: Option<ActionResult<String, String, ()>>,
}

impl DecisionAction {
    fn new() -> Self {
        Self {
            state: ActionState::Eligible,
            result: None,
        }
    }
}

impl Action for DecisionAction {
    type Result = ActionResult<String, String, ()>;

    fn state(&self) -> ActionState {
        self.state
    }

    fn drive(&mut self) {
        if matches!(self.state, ActionState::Completed | ActionState::Aborted) {
            return;
        }

        self.state = ActionState::Completed;
        self.result = Some(ActionResult {
            action_id: "decision-action".into(),
            status: crate::action::ActionStatus::Success,
            output: Some("decision executed".to_string()),
            error: None,
            metadata: (),
        });
    }

    fn suspend(&mut self) {
        if self.state == ActionState::Active {
            self.state = ActionState::Suspended;
        }
    }

    fn resume(&mut self) {
        if self.state == ActionState::Suspended {
            self.state = ActionState::Active;
        }
    }

    fn take_result(&mut self) -> Option<Self::Result> {
        self.result.take()
    }
}
