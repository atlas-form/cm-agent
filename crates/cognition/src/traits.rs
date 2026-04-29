use async_trait::async_trait;

use crate::{CognitionInput, CognitionResult};

/// Cognition 核心抽象
///
/// `Cognition : CognitionInput → CognitionResult`
///
/// - 纯函数语义
/// - 无副作用
/// - 无隐式依赖
#[async_trait]
pub trait Cognition {
    async fn evaluate(&self, input: CognitionInput) -> CognitionResult;
}
