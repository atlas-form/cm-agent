mod context;
mod intent;

pub use context::*;
pub use intent::*;

/// 认知输入快照
///
/// # 不变量
/// - 输入在 Cognition 开始前一次性确定
/// - 执行期间不可变
/// - Cognition 不得请求补充输入
/// - Cognition 不得访问输入之外的任何信息
#[derive(Debug, Clone)]
pub struct CognitionInput {
    /// 本次判断"要解决什么"
    pub intent: Intent,
    /// 本次判断"允许看到的事实集合"
    pub context: Context,
}
