use std::fmt;

/// 生命周期状态
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ActionState {
    Eligible,
    Active,
    Suspended,
    Completed,
    Aborted,
}

impl ActionState {
    pub const fn is_terminal(self) -> bool {
        matches!(self, Self::Completed | Self::Aborted)
    }

    pub fn can_transition(self, next: ActionState) -> bool {
        matches!(
            (self, next),
            (Self::Eligible, Self::Eligible)
                | (Self::Active, Self::Active)
                | (Self::Suspended, Self::Suspended)
                | (Self::Completed, Self::Completed)
                | (Self::Aborted, Self::Aborted)
                | (Self::Eligible, Self::Active)
                | (
                    Self::Active,
                    Self::Suspended | Self::Completed | Self::Aborted
                )
                | (Self::Suspended, Self::Active)
        )
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ActionTransitionError {
    pub from: ActionState,
    pub to: ActionState,
}

impl fmt::Display for ActionTransitionError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "invalid action state transition: {:?} -> {:?}",
            self.from, self.to
        )
    }
}

impl std::error::Error for ActionTransitionError {}

/// 用于实现 Action 生命周期管理的轻量状态机。
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ActionLifecycle {
    state: ActionState,
}

impl ActionLifecycle {
    pub const fn new() -> Self {
        Self {
            state: ActionState::Eligible,
        }
    }

    pub const fn state(&self) -> ActionState {
        self.state
    }

    pub fn transition(&mut self, next: ActionState) -> Result<(), ActionTransitionError> {
        if self.state.can_transition(next) {
            self.state = next;
            Ok(())
        } else {
            Err(ActionTransitionError {
                from: self.state,
                to: next,
            })
        }
    }
}

impl Default for ActionLifecycle {
    fn default() -> Self {
        Self::new()
    }
}
