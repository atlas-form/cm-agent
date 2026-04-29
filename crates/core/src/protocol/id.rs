#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct AgentId(pub String);

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct UserId(pub String);

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct WorkspaceId(pub String);

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct SessionId(pub String);

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct WorkerId(pub String);

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct TaskId(pub String);

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct MessageId(pub String);
