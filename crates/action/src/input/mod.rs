/// Action 的最小执行单元输入。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ActionInput<P, M> {
    pub action_type: ActionType,
    pub target: ActionTarget,
    pub payload: P,
    pub metadata: M,
}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct ActionId(pub String);

impl From<String> for ActionId {
    fn from(value: String) -> Self {
        Self(value)
    }
}

impl From<&str> for ActionId {
    fn from(value: &str) -> Self {
        Self(value.to_owned())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct ActionType(pub String);

impl From<String> for ActionType {
    fn from(value: String) -> Self {
        Self(value)
    }
}

impl From<&str> for ActionType {
    fn from(value: &str) -> Self {
        Self(value.to_owned())
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct ActionTarget(pub String);

impl From<String> for ActionTarget {
    fn from(value: String) -> Self {
        Self(value)
    }
}

impl From<&str> for ActionTarget {
    fn from(value: &str) -> Self {
        Self(value.to_owned())
    }
}
