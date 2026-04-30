# Momory Next Implementation Plan

本文是 momory 当前唯一的下一步执行入口。

已完成的 Phase 1 runtime 计划和状态文件已删除，不再保留重复入口。当前重点只剩一件事：

```text
Memory Compaction
```

## 背景

当前代码已经有基础 memory runtime：

- `MemoryScope`
- `MemoryKind`
- `MemoryRecord`
- `MemoryQuery`
- `MemoryBudget`
- `MemoryBundle`
- `MemoryStore`
- `InMemoryMemoryStore`
- session 启动加载 memory
- session 成功后持久化 snapshot
- commander 可读取 memory facts

但现在还不能继续做数据库，也不能继续扩展长期记忆类型。

原因很简单：

```text
没有压缩约束的 memory store = 无限增长的日志
```

所以接下来必须先完成压缩层。

参考资料：

- [`CODEX_COMPACTION_NOTES.md`](CODEX_COMPACTION_NOTES.md)
- [`OLD_CODE_MEMORY_ANALYSIS.md`](OLD_CODE_MEMORY_ANALYSIS.md)
- `/Users/ancient/src/github/codex/codex-rs/core/src/compact.rs`
- `/Users/ancient/src/github/codex/codex-rs/core/src/compact_remote.rs`
- `/Users/ancient/src/github/codex/codex-rs/core/src/session/turn.rs`

## 设计结论

Codex 的压缩对象是 conversation history，cm-agent 的压缩对象是 typed memory records。

映射关系：

```text
Codex:
old live history
  -> compact request
  -> compacted replacement history
  -> replace live history
  -> recompute token usage

cm-agent:
incoming memory records
  -> compact each record
  -> merge into live memory set
  -> compact scope/kind slice
  -> replace live memory slice
  -> emit compaction stats
```

不能照搬 Codex：

- 不照搬完整 conversation history replacement。
- 不把 compact prompt 当作 user message。
- 不复用 `ResponseItem` 过滤规则。

必须继承 Codex 的思想：

- 压缩是 checkpoint，不是随手 truncate。
- live context 必须有预算。
- 压缩失败不能污染原状态。
- canonical context 每次由 runtime 重建，不能被压进 memory。
- 压缩必须可观测。

## 核心原则

1. 每次保存前必须压缩单条 record。
2. 每次保存后必须按 exact `scope + kind` 收敛。
3. 召回 `MemoryBundle` 时仍然必须受预算限制。
4. 压缩由 `MemoryStore` 内部保证，调用方不能绕过。
5. durable backend 必须复用同一压缩 contract。
6. 第一版只做 deterministic compaction，不调用 LLM。
7. LLM 压缩以后只能作为后台增强，不能阻塞写入。

## 不保存的内容

Memory 压缩结果不能保存这些可派生上下文：

- system prompt
- developer instructions
- role prompt
- permission / sandbox policy
- runtime config
- worker catalog
- 当前日期和环境描述

这些内容必须由 runtime 每次重新构建。

Memory 只保存：

- 用户偏好
- 已确认事实
- 决策
- 约束
- 风险
- role learning
- routing hint
- session / episode 摘要
- workspace fact

## 目标 API

### MemoryCompactionPolicy

新增：

```rust
pub struct MemoryCompactionPolicy {
    pub max_record_chars: usize,
    pub max_records_per_scope_kind: usize,
    pub max_unkeyed_records_per_scope_kind: usize,
    pub max_bundle_records: usize,
    pub max_bundle_chars: usize,
    pub min_confidence_to_keep_when_pruning: f32,
}
```

默认值建议：

```text
max_record_chars = 1200
max_records_per_scope_kind = 32
max_unkeyed_records_per_scope_kind = 8
max_bundle_records = 12
max_bundle_chars = 2400
min_confidence_to_keep_when_pruning = 0.4
```

### MemoryCompactionStats

新增：

```rust
pub struct MemoryCompactionStats {
    pub input_records: usize,
    pub output_records: usize,
    pub truncated_records: usize,
    pub merged_records: usize,
    pub dropped_records: usize,
    pub before_chars: usize,
    pub after_chars: usize,
}
```

Stats 必须可累加，用于 `persist_session` 聚合多条写入结果。

### MemoryWriteOutcome

新增：

```rust
pub struct MemoryWriteOutcome {
    pub written: usize,
    pub compaction: MemoryCompactionStats,
}
```

Phase 1.5 暂时保持同步接口，后续 durable backend 再改成 `Result<MemoryWriteOutcome>`。

### MemoryCompactor

新增：

```rust
pub trait MemoryCompactor: Send + Sync {
    fn compact_record(
        &self,
        record: MemoryRecord,
        policy: &MemoryCompactionPolicy,
    ) -> (Option<MemoryRecord>, MemoryCompactionStats);

    fn compact_scope_kind(
        &self,
        records: Vec<MemoryRecord>,
        policy: &MemoryCompactionPolicy,
    ) -> (Vec<MemoryRecord>, MemoryCompactionStats);
}
```

`compact_record` 返回 `Option<MemoryRecord>`，因为空内容应该直接拒写。

第一版实现：

```rust
DeterministicMemoryCompactor
```

## 写入流程

### 1. 写入前压缩单条

所有写入入口统一经过：

```text
incoming MemoryRecord
  -> normalize whitespace
  -> trim content
  -> reject empty content
  -> clamp confidence
  -> dedup/sort tags
  -> truncate content if needed
  -> upsert/insert
```

单条超长内容第一版用 head/tail 保留：

```text
{head}

[...memory compacted...]

{tail}
```

约束：

- `scope` 不能丢
- `kind` 不能丢
- `key` 不能丢
- `source` 不能丢
- `id` 尽量保留，更新已有 record 时保留原 id

### 2. 写入时 keyed upsert

有 `key`：

```text
same exact scope + kind + key => update existing
```

无 `key`：

```text
append candidate => scope/kind compaction 决定保留或丢弃
```

注意：

- query scope matching 可以把 `None` 当宽作用域。
- upsert / compaction identity 必须 exact match。

因此需要区分：

```rust
scope_query_matches(...)
scope_identity_eq(...)
```

### 3. 写入后 scope/kind 收敛

对同一 exact `scope + kind` 的 live records 做收敛。

排序优先级：

1. keyed record 高于 unkeyed record
2. confidence 高的优先
3. updated_at 新的优先
4. content 较短但信息完整的优先
5. source 更可信的优先

超过预算时：

- 先丢低 confidence unkeyed record
- 再丢旧 unkeyed summary
- keyed record 尽量保留
- keyed record 也超限时，保留 confidence + recency 前 N 条
- dropped 数量写入 stats

## 召回流程

`load_bundle` 仍必须应用预算：

- `MemoryQuery.limit`
- `MemoryBudget.max_records`
- `MemoryBudget.max_chars`
- kind filter
- text filter

不能因为 store 已压缩，就把所有 memory 都塞进 prompt。

## 文件落点

短期仍放在：

```text
src/agent_session/memory.rs
```

内容变多后再拆：

```text
src/agent_session/memory/
  mod.rs
  model.rs
  compaction.rs
  store.rs
  in_memory.rs
```

本阶段不做拆文件，避免无意义 churn。

## Session Event

当前已有：

- `MemoryLoaded`
- `MemoryPersisted`

本阶段建议新增：

```rust
MemoryCompacted {
    session_id: SessionId,
    stats: MemoryCompactionStats,
}
```

如果不想扩大 event surface，至少让 `MemoryPersisted` 能携带 compaction stats。

推荐独立 event，因为以后 `coordination_save_memory`、后台 consolidation、DB migration 都可能触发 compaction。

## 实施步骤

### Step 1: Model

修改 `src/agent_session/memory.rs`：

- 添加 `MemoryCompactionPolicy`
- 添加 `MemoryCompactionStats`
- 添加 `MemoryWriteOutcome`
- 添加 `MemoryCompactor`
- 添加 `DeterministicMemoryCompactor`

验收：

- policy 有默认值
- stats 可累加
- outcome 能表达 written + compaction

### Step 2: Record Compaction

实现：

- whitespace normalize
- empty reject
- confidence clamp
- tags dedup/sort
- content head/tail truncate

验收：

- 超长 record 保存后不超过 `max_record_chars`
- 空内容不写
- tags 去重
- confidence 不越界

### Step 3: Scope/Kind Compaction

实现：

- exact scope identity
- `(scope_identity, kind)` 分组
- keyed/unkeyed 排序
- 超限 prune
- stats dropped/truncated

验收：

- 连续写入 100 条同 kind，store 不超过 policy 限制
- keyed records 优先保留
- high confidence records 优先保留
- old low confidence unkeyed records 先被丢

### Step 4: Store Integration

`InMemoryMemoryStore`：

- 新增 policy
- 新增 compactor
- `new()` 使用默认 policy
- 新增 `with_policy(policy)`
- `upsert_record` 返回 `MemoryWriteOutcome`
- `persist_session` 聚合 outcome

验收：

- 所有写入路径都经过 compactor
- 无法绕过压缩直接 push record

### Step 5: Events

接入 stream 事件：

- `MemoryPersisted` 使用真实 outcome.written
- 发送 `MemoryCompacted` 或扩展 persisted payload

验收：

- stream 能看到压缩发生
- stats 中 before/after/dropped/truncated 有意义

### Step 6: Tests

新增或扩展 `tests/memory.rs`：

- `record_is_truncated_before_write`
- `empty_record_is_not_written`
- `same_key_upsert_does_not_grow_store`
- `scope_kind_compaction_prunes_unkeyed_records`
- `keyed_high_confidence_records_survive_pruning`
- `load_bundle_respects_char_budget_even_after_store_compaction`
- `persist_session_reports_compaction_stats`

## 不做

本阶段不做：

- DB backend
- LLM summarization
- role learning extraction
- routing hint extraction
- `coordination_save_memory`
- vector search
- audit table
- 文件拆分重构

## 最终验收标准

本阶段完成后，momory 才能满足“不允许记忆无限扩大”的基本要求：

- 任意单条 memory 有硬长度上限
- 任意 exact scope/kind 有硬数量上限
- 任意召回 bundle 有硬预算上限
- 每次保存都经过压缩
- 压缩结果替换 live memory slice，而不是无限追加
- 压缩行为可测试、可观测
- 后续 durable backend 不能绕过同一压缩 contract

