# Codex Compaction Notes

参考源码：

- `/Users/ancient/src/github/codex/codex-rs/core/src/compact.rs`
- `/Users/ancient/src/github/codex/codex-rs/core/src/compact_remote.rs`
- `/Users/ancient/src/github/codex/codex-rs/core/src/session/turn.rs`
- `/Users/ancient/src/github/codex/codex-rs/core/src/session/turn_context.rs`
- `/Users/ancient/src/github/codex/codex-rs/core/templates/compact/prompt.md`
- `/Users/ancient/src/github/codex/codex-rs/core/templates/compact/summary_prefix.md`
- `/Users/ancient/src/github/codex/codex-rs/analytics/src/facts.rs`

## 核心思想

Codex 的压缩不是“删旧消息”这么简单，而是一次明确的 history replacement：

1. 根据 token 使用量或用户手动请求触发 compaction。
2. 用当前历史生成一份结构化摘要。
3. 用“保留的关键用户消息 + 摘要”替换原历史。
4. 把替换后的 history 作为 checkpoint 写回 session。
5. 重新计算 token usage。
6. 通过事件和 analytics 记录 compaction 的触发原因、阶段、实现方式、结果、压缩前后 token。

这套方案的本质是：**无限增长的交互日志必须被收敛成有限上下文状态，而不是无限追加。**

## 触发策略

Codex 支持两种 trigger：

- `Manual`：用户手动 `/compact`。
- `Auto`：系统发现上下文接近或超过限制。

Auto compaction 又区分原因：

- `ContextLimit`：上下文 token 达到 `auto_compact_token_limit`。
- `ModelDownshift`：切换到更小 context window 的模型前，需要先用旧模型压缩。

执行阶段分为：

- `StandaloneTurn`：手动压缩作为独立 turn。
- `PreTurn`：新一轮采样前先压缩。
- `MidTurn`：模型还需要继续工作，但 token 已到阈值，中途压缩后继续。

对 cm-agent 的启发：

- memory 不应等到“已经爆掉”才压缩。
- 每次写入前做单条压缩，每次写入后做 scope/kind 级预算收敛。
- 后续可以增加显式 `MemoryCompactionReason`：
  - `BeforePersist`
  - `ScopeLimit`
  - `KindLimit`
  - `BackendLimit`
  - `ModelDownshift`

## 压缩输入

Codex local compaction 会把 compaction prompt 作为 synthesized user input 加入临时 history，请模型根据完整上下文生成 handoff summary。

默认 prompt 要求摘要包含：

- 当前进展和关键决策；
- 重要上下文、约束、用户偏好；
- 剩余待办；
- 继续任务所需的关键数据、例子、引用。

这说明压缩不是普通摘要，而是面向“下一个 agent 能继续工作”的 state transfer。

对 cm-agent 的启发：

- memory 压缩不能只保留自然语言摘要，还要保留 typed fields。
- 推荐压缩目标：
  - `decisions`
  - `facts`
  - `preferences`
  - `constraints`
  - `open_questions`
  - `next_actions`
  - `source_refs`
  - `confidence`

## 压缩输出与替换

Codex local compaction 的 replacement history 大致是：

1. 可选 initial context；
2. 受 token budget 限制的最近真实用户消息；
3. 一条带固定 prefix 的 summary message。

它不会保留全部旧历史，而是用 compacted history 替换 live history。

Remote compaction 则调用 provider 的 compact endpoint，拿到模型返回的 compacted history 后再过滤：

- 丢弃 developer messages，避免旧指令污染；
- 丢弃非真实 user content；
- 保留真实 user message、hook prompt、assistant message、compaction item；
- 再按需要重新注入当前 canonical initial context。

对 cm-agent 的启发：

- memory store 不应该保留无限多 `SessionSummary`。
- 压缩结果应该替换或合并旧 record，而不是继续追加一条更长 record。
- 需要区分：
  - raw append log：可选、短期、审计用途；
  - live memory bundle：有限、压缩后、供 prompt 使用。

## Budget 策略

Codex 在构建 compacted history 时有硬预算：

- 最近用户消息最多约 `20_000` tokens；
- 超过则从最近消息倒序选取；
- 如果最后一条超预算，则按 token truncate；
- 压缩请求本身如果超过 context window，会移除较旧 history item；
- remote compaction 前还会删掉尾部 Codex-generated function-call history，直到 compact request 能放进 context window。

对 cm-agent 的启发：

- memory 需要至少三层预算：
  1. 单条 record 最大长度；
  2. 同一 `(scope, kind)` 最大 record 数；
  3. `MemoryBundle` 召回总字符/token 预算。
- 每次保存前先压缩单条内容，保存后再按 scope/kind 裁剪。
- 裁剪优先级建议：
  - 保留高 confidence；
  - 保留更新的 record；
  - 保留有 key 的 consolidated record；
  - 丢弃低 confidence、重复、过期、无 key 的临时 summary。

## Context 注入

Codex 有一个很重要的边界：压缩后会重新注入 canonical initial context，而不是信任压缩摘要里的旧 developer/system context。

不同阶段注入位置不同：

- pre-turn/manual：不在 replacement history 里注入，下一轮正常重建；
- mid-turn：把 initial context 插到最后一个真实 user message 或 summary 前，保证模型继续时仍有当前 canonical context。

对 cm-agent 的启发：

- memory 压缩结果不能携带系统指令、角色 prompt、权限策略这类可重新生成的上下文。
- runtime 每次都应该从当前配置重建 role/system context。
- memory 只保存事实和状态，不保存可派生指令。

## 可观测性

Codex 把 compaction 作为一等事件记录：

- trigger；
- reason；
- implementation；
- phase；
- strategy；
- status；
- error；
- active context tokens before/after；
- duration。

对 cm-agent 的启发：

- memory 压缩也应有事件：
  - `MemoryCompactionStarted`
  - `MemoryCompactionFinished`
  - 或在 `MemoryPersisted` 中带 `compressed_count`、`dropped_count`、`before_chars`、`after_chars`。
- store 层压缩不可静默，否则线上很难判断 memory 是否丢失重要信息。

## Failure 策略

Codex local compaction：

- 上游 stream 失败时按 provider retry 策略重试；
- context window exceeded 时会移除最旧 history item 再试；
- 如果只有一条仍超限，则报错并停止。

Remote compaction：

- 失败时记录 token/byte breakdown；
- 发 error event；
- 不安装失败的 replacement history。

对 cm-agent 的启发：

- deterministic 压缩必须永不失败，至少能 truncate。
- LLM 压缩如果失败，不应破坏已有 memory。
- durable backend 写入前可以先生成 candidate compacted records，成功后原子替换。

## 推荐给 cm-agent 的压缩模型

### 1. 每次保存前压缩单条 record

新增 `MemoryCompactor`：

```rust
pub trait MemoryCompactor: Send + Sync {
    fn compact_record(&self, record: MemoryRecord, policy: &MemoryCompactionPolicy) -> MemoryRecord;
    fn compact_scope(&self, records: Vec<MemoryRecord>, policy: &MemoryCompactionPolicy) -> MemoryCompactionResult;
}
```

第一版 deterministic：

- normalize whitespace；
- 去掉空内容；
- content 超过 `max_record_chars` 时保留 head/tail 或结构化摘要；
- tags 去重；
- confidence clamp；
- source metadata 保留。

### 2. 每次保存后做 scope/kind 收敛

对同一 `(user_id, workspace_id, agent_id, kind)`：

- 有相同 key：upsert 合并；
- 无 key：按 confidence + updated_at 排序；
- 超过 `max_records_per_kind` 的低优先级记录合并成一条 `SessionSummary` 或直接丢弃；
- 写入 `MemoryCompactionEvent` 记录 dropped/merged/truncated。

### 3. MemoryBundle 召回仍然有预算

即使 store 已压缩，召回时也不能无限加载：

- `MemoryQuery.limit`
- `MemoryBudget.max_records`
- `MemoryBudget.max_chars`
- 后续可改 token 预算。

### 4. 区分 live memory 和 audit log

Codex 的 replacement history 思路适合 cm-agent：

- live memory：有限、压缩、用于 prompt；
- memory events/audit：可选、用于调试和恢复，不直接进入 prompt。

## 对当前阶段计划的修正

之前 Phase 1 少了一个硬约束：**任何 memory 保存都必须经过压缩策略。**

因此下一步不应直接进入 DB backend，而应先补：

1. `MemoryCompactionPolicy`
2. `MemoryCompactor`
3. deterministic `DefaultMemoryCompactor`
4. `InMemoryMemoryStore` 写入前/写入后压缩
5. 压缩测试：
   - 单条超长内容会被压缩；
   - 同 scope/kind 超数量会裁剪；
   - 同 key 记录 upsert 不增加数量；
   - `load_bundle` 永远受预算限制；
   - 压缩不会删除高 confidence/keyed record。

