# Momory Stage Plans

> 目录名按当前阶段指令保留为 `momory`。文档内使用 `memory` 表示系统概念。

本目录用于规划 Rust 版 agent memory。

当前唯一执行入口：

- [`NEXT_IMPLEMENTATION_PLAN.md`](NEXT_IMPLEMENTATION_PLAN.md)：下一阶段只做 memory compaction。

核心原则：

- 不照搬旧 Python `chat_pipeline.py` 的过程式后台任务。
- 以现有 Rust 架构为入口：`AgentManager -> AgentSession -> SessionRuntime -> SessionContext`。
- Memory 是 session runtime 的能力，不是散落在技能、prompt builder、数据库 helper 里的副作用。
- 当前阶段先做压缩，再接数据库；memory 不能无限扩大。

参考文档：

- [`OLD_CODE_MEMORY_ANALYSIS.md`](OLD_CODE_MEMORY_ANALYSIS.md)：旧代码记忆机制拆解。
- [`CODEX_COMPACTION_NOTES.md`](CODEX_COMPACTION_NOTES.md)：参考 Codex 源码后的压缩思想总结。
