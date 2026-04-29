//! # Cognition
//!
//! Cognition 是 Agent 中唯一进行语义判断的能力，
//! 它基于一次性输入快照产生原子化决策结果，
//! 不依赖、也不控制任何其他功能模块。

pub mod engine;
pub mod input;
pub mod output;
mod traits;

pub use engine::*;
pub use input::*;
pub use output::*;
pub use traits::*;
