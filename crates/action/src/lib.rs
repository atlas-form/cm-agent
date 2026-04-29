//! # Action
//!
//! Action 负责在受控的生命周期中执行副作用，
//! 其行为必须遵循架构文档中定义的执行语义。

pub mod examples;
pub mod input;
pub mod lifecycle;
pub mod output;
pub mod traits;

pub use examples::*;
pub use input::*;
pub use lifecycle::*;
pub use output::*;
pub use traits::*;
