# Memory Phase 2: File Store + Snapshot Enrichment

第二阶段继续围绕 Rust 项目的 memory runtime 演进：先让 memory 从进程内存落到文件，再继续补充 session snapshot 的结构。当前存储先用本地文件，未来切换到 S3 / object storage 时，应尽量复用同一份序列化 envelope 和 store 边界。

## Baseline

Phase 1 / Phase 1.5 已完成：

- typed memory model: `MemoryScope` / `MemoryKind` / `MemoryRecord` / `MemoryQuery` / `MemoryBundle`
- memory write boundary: `MemoryStore`
- in-process implementation: `InMemoryMemoryStore`
- deterministic compaction: `MemoryCompactionPolicy` / `MemoryWriteOutcome` / `DeterministicMemoryCompactor`
- session 启动时加载 memory bundle
- commander 可读取 memory facts
- session 成功后保存 snapshot
- 每次保存前 record compaction
- 保存后 exact `scope + kind` compaction
- `MemoryLoaded` / `MemoryCompacted` / `MemoryPersisted` events

当前缺口：

- memory 只能保存在进程内，session/runtime 结束后不可恢复。
- 未来 S3/object storage 需要一个稳定的序列化格式和 durable store 边界。
- `SessionSnapshot` 仍然偏薄，只保存 final output 和 blackboard。
- task graph / worker reports / evaluation 还没有进入 memory snapshot。

## Goal

把 memory 写入路径升级为：

```text
SessionRuntime
  -> SessionResult
  -> SessionSnapshot
  -> MemoryStore::persist_session
  -> compacted MemoryRecords
  -> durable JSON envelope
      -> file now
      -> S3 object later
```

## Scope

本阶段负责：

- 新增文件型 memory store，当前以 JSON 文件保存 records。
- 文件 store 必须继续复用现有 compaction contract，不能绕过 `upsert_record`。
- 文件格式包含 version 和 records，便于后续迁移。
- 文件写入使用临时文件 + rename，避免半写入破坏 memory。
- 文件不存在时返回空 memory；文件损坏时在 open 阶段显式返回错误。
- 更新公开 API export，让 embedding application 可以直接使用文件 store。
- 增加文件 store 回归测试。
- 继续规划 snapshot enrichment：
  - 扩展 `SessionResult`
  - 扩展 `SessionSnapshot`
  - 持久化 role contribution / risk / open question / evaluation summary

本阶段不负责：

- 真正接入 S3 SDK。
- DB / vector search。
- LLM summarization。
- role learning extraction。
- routing hint extraction。
- 旧 Python pipeline 复刻。

## Design Rules

- `MemoryStore` 仍然是唯一写入边界。
- durable store 不自己发明第二套 memory 语义，先复用 `InMemoryMemoryStore` 的查询、upsert、compaction。
- 文件内容保存 compacted records，而不是原始 session transcript。
- 每次写入前都必须走 compaction，每次成功写入后再 flush durable backend。
- JSON envelope 面向 object storage 设计：一个对象就是一份 memory snapshot。
- `AgentSession` 只负责组装 snapshot，不直接处理文件、S3 或压缩。
- snapshot 不保存 system prompt、developer instructions、role prompt、权限策略、runtime config。

## Implementation Outline

### 1. File Store

新增 `FileMemoryStore`：

```rust
FileMemoryStore
  -> path: PathBuf
  -> inner: InMemoryMemoryStore
```

行为：

- `open(path)`：读取 JSON envelope；不存在则创建空 store。
- `open_with_policy(path, policy)`：支持测试和部署调整压缩策略。
- `load_bundle(query)`：委托给 inner。
- `upsert_record(record)`：委托给 inner，成功后 flush compacted records。
- `persist_session(scope, snapshot)`：委托给 inner，成功后 flush compacted records。

### 2. Durable Envelope

文件结构：

```json
{
  "version": 1,
  "records": []
}
```

未来 S3 可以把同一个 envelope 放到 object key 中，例如：

```text
memory/{workspace_id}/{user_id}/records.json
```

### 3. Snapshot Enrichment

在 file store 稳定后继续：

- 扩展 `SessionResult` 结构化字段。
- 扩展 `SessionSnapshot`。
- 将 role summary / risks / open questions / evaluation summary 映射成合适 `MemoryKind`。
- 保持所有 records 继续走 store 内 compaction。

## Verification

必须通过：

```bash
rtk cargo test --test memory
rtk cargo test
rtk cargo check --bins
```

## Completion Definition

- `MEMORY_PHASE_2_SNAPSHOT_CHECKLIST.md` checklist 全部完成。
- `FileMemoryStore` 可以保存、重载、查询 compacted records。
- 文件写入不会绕过 memory compaction。
- durable JSON envelope 为未来 S3/object storage 留出稳定边界。
- `SessionSnapshot` 能表达结构化 session memory。
- 普通 session 仍正常保存 summary。
- 现有全量测试通过。
