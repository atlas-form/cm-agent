pub use crate::agent_error::{CognitionFailure, FailureReason};
pub type CognitionOutput = serde_json::Value;

/// 认知结果
///
/// # 不变量
/// - 至多一个输出
/// - 输出一旦产生即不可变
/// - 不允许中间结果外泄
/// - 不允许部分输出
#[derive(Debug, Clone)]
pub enum CognitionResult {
    Success(CognitionOutput),
    Failure(CognitionFailure),
}
