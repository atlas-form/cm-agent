# Rust Memory Implementation Plan

## 目标

在现有 Rust 架构中补齐 agent memory，使它成为 `AgentSession` 和 `SessionRuntime` 的 typed capability。

当前 Rust 状态：

- `src/agent_session/memory.rs` 已有 `MemoryScope`、`MemoryStore`、`SessionSnapshot`、`NoopMemoryStore`。
- `AgentManagerConfig` 已持有 `Arc<dyn MemoryStore>`。
- `AgentSession::persist_if_ok` 会在 session 成功后调用 `persist_session`。
- `SessionContext` 已有 `SessionBlackboard` 和 `SessionExtensions`，适合承载运行期 memory bundle。
- `coordination_save_memory` 目前只是 pending skill spec。

这说明 Rust 方向已经不是“先写数据库 helper”，而是先稳定 memory contract。

## 设计原则

1. Memory contract 先于 storage backend。
2. Memory 读写必须带 `MemoryScope`，不要靠全局 user/conversation 变量。
3. Runtime 注入 typed `MemoryBundle`，prompt 拼接只在 cognition/prompt 边界发生。
4. 写入以 session result 和 task graph event 为来源，不从散落字符串里偷状态。
5. 存储层用 trait 隔离：`Noop -> InMemory -> Sqlite/Postgres`。
6. 技能层只提交 memory command，不直接写数据库。

## Core Model

建议把 `src/agent_session/memory.rs` 扩成以下语义模型：

```rust
pub struct MemoryScope {
    pub user_id: Option<UserId>,
    pub workspace_id: Option<WorkspaceId>,
    pub agent_id: Option<AgentId>,
    pub session_id: Option<SessionId>,
    pub task_id: Option<TaskId>,
}

pub enum MemoryKind {
    SessionSummary,
    ConversationFact,
    UserPreference,
    WorkspaceFact,
    RoleLearning,
    RoutingHint,
    Episode,
    CrossRoleContext,
}

pub struct MemoryRecord {
    pub id: MemoryId,
    pub scope: MemoryScope,
    pub kind: MemoryKind,
    pub key: Option<String>,
    pub content: String,
    pub confidence: f32,
    pub tags: Vec<String>,
    pub source: MemorySource,
    pub created_at: SystemTime,
    pub updated_at: SystemTime,
}

pub struct MemoryQuery {
    pub scope: MemoryScope,
    pub kinds: Vec<MemoryKind>,
    pub text: Option<String>,
    pub limit: usize,
    pub budget: MemoryBudget,
}

pub struct MemoryBundle {
    pub records: Vec<MemoryRecord>,
    pub summary: Option<String>,
}
```

`SessionSnapshot` 不应只保存 `summary: String`。下一步应包含：

- final output；
- role reports；
- task graph id；
- blackboard snapshot；
- selected facts；
- quality/evaluation summary；
- timestamps。

## Trait Contract

第一阶段建议保持同步或轻量异步二选一。因为当前 `persist_session` 是同步 trait 方法，短期可以先做同步 `InMemoryMemoryStore`；接数据库前再引入 async trait。

建议目标接口：

```rust
pub trait MemoryStore: Send + Sync {
    fn load_bundle(&self, query: MemoryQuery) -> MemoryBundle;
    fn persist_session(&self, scope: &MemoryScope, snapshot: SessionSnapshot);
    fn upsert_record(&self, record: MemoryRecord);
}
```

如果接 SQLite/Postgres，则改为：

```rust
#[async_trait]
pub trait MemoryStore: Send + Sync {
    async fn load_bundle(&self, query: MemoryQuery) -> Result<MemoryBundle>;
    async fn persist_session(&self, scope: &MemoryScope, snapshot: SessionSnapshot) -> Result<()>;
    async fn upsert_record(&self, record: MemoryRecord) -> Result<()>;
}
```

不要一开始就把 SQL schema 写死进 trait。

## Runtime Flow

### 1. Session Start

`AgentSession::run_once_with_events` 创建 runtime 前：

1. 根据 `AgentSessionScope` 构建 `MemoryQuery`。
2. 从 `MemoryStore` 读取 `MemoryBundle`。
3. 放入 `SessionContext.extensions()` 或新增 `SessionMemoryContext`。
4. 发出可观测事件，例如 `SessionEvent::MemoryLoaded { count, kinds }`。

### 2. Worker Execution

worker 不直接读数据库，只从 `SessionContext` 获取：

- 当前 session blackboard；
- memory bundle 的只读视图；
- role 相关 learning/routing hints；
- workspace facts。

role prompt 构造阶段再把 typed bundle 渲染成有限预算文本。

### 3. Session Finish

`SessionRuntime::run_until_complete` 产出更完整的 `SessionResult`。

`AgentSession::persist_if_ok` 将 result 转成 `SessionSnapshot`：

- session summary；
- output excerpt；
- worker reports；
- task graph/evaluation；
- blackboard snapshot。

然后调用 `MemoryStore::persist_session`。

### 4. Explicit Memory Skill

`coordination_save_memory` 后续不要直接碰数据库。它应该生成 typed command：

```rust
MemoryCommand::Upsert {
    scope,
    kind,
    key,
    content,
    confidence,
    tags,
}
```

command 由 runtime 或 skill executor 统一提交到 `MemoryStore`。

## Storage Plan

### Phase 1: Contract + Tests

- 扩展 `MemoryStore` contract。
- 新增 `InMemoryMemoryStore`，内部用 `RwLock<Vec<MemoryRecord>>`。
- `NoopMemoryStore` 保持兼容。
- 增加 `MemoryBundle` 注入测试：
  - manager 创建 session 时可以加载 memory；
  - session 成功后会 persist snapshot；
  - scope 包含 user/workspace/agent/session。

### Phase 2: Session Summary

- 扩展 `SessionSnapshot`。
- 在 `persist_session` 中写入 `SessionSummary` record。
- 从 `SessionBlackboard::snapshot()` 捕获运行期事实。
- 给 `SessionEvent` 增加 `MemoryLoaded`、`MemoryPersisted` 或统一 metadata event。

### Phase 3: Role Learning

- 从旧 `agent_memory.py` 迁移 deterministic extraction，但改成 Rust service：
  - `LearningExtractor`；
  - `LearningCategory`；
  - `RoleLearningRecord`；
  - `RoutingHintExtractor`。
- extraction 输入来自 typed evaluation/quality result，而不是解析最终回复字符串。
- routing hint 存为 `MemoryKind::RoutingHint`，不要和 role learning 混表。

### Phase 4: Long-Term Facts

- 实现 `MemoryConsolidator`：
  - 从 session snapshot 和 blackboard 提取候选事实；
  - 合并同 scope + kind + key 的记录；
  - 做长度、数量、置信度、过期控制。
- 用户偏好、workspace fact、conversation fact 分开建模。
- prompt 渲染只读取 `MemoryBundle`，不在 store 层拼 prompt。

### Phase 5: Durable Backend

- 增加 `SqliteMemoryStore` 或项目最终数据库后端。
- 推荐 schema 方向：
  - `memory_records(id, kind, scope_user_id, scope_workspace_id, scope_agent_id, scope_session_id, scope_task_id, key, content, confidence, tags_json, source_json, created_at, updated_at)`
  - `memory_events(id, memory_id, event_type, payload_json, created_at)`
- 先用 JSON tags/source，后续有检索需求再加索引表或向量表。

## 和旧代码的映射

| 旧代码概念 | Rust 概念 |
| --- | --- |
| `conversation_contexts.summary` | `MemoryKind::SessionSummary` 或 `ConversationFact` |
| `conversation_contexts.facts.goals` | `MemoryKind::UserPreference` / `WorkspaceFact`，按 scope 决定 |
| `profile.platforms/interests` | `MemoryKind::UserPreference`，带 counter metadata |
| `learnings.success/blindspot/improvement` | `MemoryKind::RoleLearning` |
| `learnings.routing_keyword` | `MemoryKind::RoutingHint` |
| `agent_episodes` | `MemoryKind::Episode` |
| `role_shared_context` | `MemoryKind::CrossRoleContext` 或 workspace blackboard persistence |
| `_build_longterm_memory_prompt` | `MemoryRenderer`，只在 prompt 边界运行 |

## 不做的事

- 不把旧 Python 的 `chat_pipeline.py` 流程搬到 Rust。
- 不在 skill 里直接 SQL 写 memory。
- 不把 memory store 设计成 prompt 字符串仓库。
- 不把所有记忆塞进一个 `facts: serde_json::Value`。
- 不在第一阶段引入向量数据库。

## 下一步执行清单

1. 修改 `src/agent_session/memory.rs`，加入 typed records/query/bundle。
2. 增加 `InMemoryMemoryStore` 和针对 scope/filter/limit 的单元测试。
3. 在 `AgentSession` 启动 runtime 前加载 bundle，并放入 `SessionContext`。
4. 扩展 `SessionSnapshot`，持久化 final output + blackboard snapshot。
5. 增加 memory 相关 `SessionEvent`，让 stream 能观测加载和保存。
6. 将 `coordination_save_memory` 从 pending spec 推进成 command 产出，而不是直接实现存储。
7. 再考虑 SQLite backend 和旧数据迁移。

