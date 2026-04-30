# Momory Implementation Status

## 2026-04-30

Phase 1 已完成：

- 扩展 `MemoryStore` contract：
  - `load_bundle`
  - `persist_session`
  - `upsert_record`
- 新增 typed memory model：
  - `MemoryKind`
  - `MemoryRecord`
  - `MemoryQuery`
  - `MemoryBudget`
  - `MemoryBundle`
  - `MemorySource`
- 新增 `InMemoryMemoryStore`。
- `AgentSession` 在启动 runtime 前按 scope 加载 `MemoryBundle`。
- `SessionRuntime` 将 `MemoryBundle` 注入 `SessionContext.extensions()`。
- Commander cognition 可以通过 `CommanderSessionContext::memory_records()` 读取 typed memory，并渲染为 cognition facts。
- session 成功结束后会持久化 `SessionSnapshot`，并由 store 返回实际写入数量。
- stream 增加 memory 可观测事件：
  - `MemoryLoaded`
  - `MemoryPersisted`
- 新增 memory 测试覆盖：
  - store scope/kind/limit 过滤；
  - manager 将 memory 注入 commander context；
  - stream 发送 memory load/persist 事件。

验证：

- `cargo fmt --all -- --check`
- `cargo test`

下一步建议进入 Phase 2：

1. 扩展 `SessionSnapshot`，纳入 task graph、role reports、evaluation 摘要。
2. 让 `persist_session` 升级为 `Result<usize>`，为数据库 backend 暴露失败原因。
3. 引入 `MemoryCommand`，让 `coordination_save_memory` 生成 command，而不是直接写 store。
4. 再开始迁移 role learning / routing hint extraction。
