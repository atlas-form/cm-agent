#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChatTurn {
    pub user: String,
    pub assistant: String,
}
