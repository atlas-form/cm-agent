//! # World
//!
//! World 是系统级运行时上下文：
//! - Endpoint Directory（通信端点目录）
//! - Blackboard（世界共享状态）
//! - Extension Registry（可扩展能力挂载）
//!
//! 说明：
//! - 推荐直接使用 `WorldRuntime` 及其子系统；
//! - 当前保留了 `init_world/register_*` 等兼容 API，便于旧代码平滑迁移。

mod blackboard;
mod bootstrap;
mod directory;
mod extensions;
mod llm_manager;
mod runtime;
mod worker_catalog;

pub use agent_core::protocol::{Message, WorkerId};
pub use blackboard::*;
pub use bootstrap::*;
pub use directory::*;
pub use extensions::*;
pub use llm_manager::*;
pub use runtime::*;
pub use worker_catalog::*;
