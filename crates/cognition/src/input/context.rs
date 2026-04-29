use std::collections::HashMap;

/// 认知上下文
///
/// - 只读
/// - 不保证完整
/// - 不保证真实
/// - 不保证一致
/// - 不承诺覆盖世界
#[derive(Debug, Clone, Default)]
pub struct Context {
    pub facts: Vec<Fact>,
    pub metadata: HashMap<String, String>,
}

#[derive(Debug, Clone)]
pub struct Fact {
    pub source: String,
    pub content: String,
    pub reliability: f64,
}
