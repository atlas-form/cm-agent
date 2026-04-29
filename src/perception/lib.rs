//! # Perception
//!
//! Perception 负责将低语义刺激（Stimulus）聚合成可认知处理的知觉对象（Percept）。

pub mod input;
pub mod output;
pub mod perceptions;
mod traits;

pub use input::*;
pub use output::*;
pub use perceptions::*;
pub use traits::*;
