//! # Core
//!
//! 系统级标准类型定义：
//! - 标准 ID
//! - 统一消息封装
//! - 协议 Payload

pub mod messaging;
pub mod protocol;

pub use messaging::*;
pub use protocol::*;
