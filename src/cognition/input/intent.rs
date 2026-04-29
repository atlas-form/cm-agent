/// 认知意图
///
/// - 必须是单一的
/// - 表示判断目标，而不是执行目标
/// - 不等同于用户原始指令
#[derive(Debug, Clone)]
pub struct Intent {
    pub id: String,
    pub kind: IntentKind,
    pub description: String,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum IntentKind {
    Decision,
    Analysis,
    Planning,
    Evaluation,
    Custom(String),
}
