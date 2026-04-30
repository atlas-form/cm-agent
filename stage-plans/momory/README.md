# Momory Stage Plans

> 目录名按当前阶段指令保留为 `momory`。文档内使用 `memory` 表示系统概念。

本目录用于规划 Rust 版 agent memory。

核心原则：

- 不照搬旧 Python `chat_pipeline.py` 的过程式后台任务。
- 以现有 Rust 架构为入口：`AgentManager -> AgentSession -> SessionRuntime -> SessionContext`。
- Memory 是 session runtime 的能力，不是散落在技能、prompt builder、数据库 helper 里的副作用。
- 先定义 contract 和内存实现，再接数据库；先保证可测试，再扩展召回和压缩。

文档：

- [`OLD_CODE_MEMORY_ANALYSIS.md`](OLD_CODE_MEMORY_ANALYSIS.md)：旧代码记忆机制拆解。
- [`RUST_MEMORY_IMPLEMENTATION_PLAN.md`](RUST_MEMORY_IMPLEMENTATION_PLAN.md)：Rust 方向实现计划。

