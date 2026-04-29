# Role Worker 实现规划

本文回答一个核心问题：

> 真正能干活的 roles 应该放在哪里？

结论：

```text
src/roles  = 角色定义、角色目录、角色路由、角色 prompt 规则
src/agent  = 真正运行的 agent 生命体：Commander / Worker
```

所以 `roles` 不应该变成执行器。

真正能干活的是 `agent::worker::Worker`。

Role 是 Worker 的身份、能力、prompt 和约束。

## 当前状态

现在已经有：

```text
src/roles/
  RoleProfile
  RoleCatalog
  8 个内置 role profile
```

每个 session 启动时，`AgentManagerConfig.roles` 会转成 `WorkerProfile`，注入 `SessionContext.worker_catalog`。

但是现在还只是配置：

```text
worker.ops
worker.data
worker.service
...
```

这些已经不是单纯 worker profile。

当前实现是：

- `SessionRuntime` 会为每个 role 启动一个短生命 Worker loop。
- `Worker` 持有 `RoleProfile`。
- `Worker` cognition context 会带上 `role.id` / `role.name` / `role.runtime_role`。
- `Worker` cognition context 会带上 `RolePromptBuilder` 生成的 role prompt。
- `AgentManagerConfig::new_role_aware(...)` 可以按 role 创建不同 worker cognition。
- Worker 完成后会把 cognition output 作为角色贡献回传给 Commander。
- Commander 可以先派 primary role，再派 support roles，并汇总贡献。

## 目标结构

最终结构应该是：

```text
AgentManager
  -> AgentSession
      -> SessionRuntime
          -> SessionContext
          -> Commander
          -> RoleWorker(ops)
          -> RoleWorker(data)
          -> RoleWorker(service)
          -> RoleWorker(creative)
          -> RoleWorker(engineering)
          -> RoleWorker(accounting)
          -> RoleWorker(design)
          -> RoleWorker(web)
```

注意：

`RoleWorker` 不一定是一个新 trait。

第一阶段可以仍然使用 `Worker` struct，只是 Worker 持有 `RoleProfile`。

## 目录边界

### src/roles

放业务语义：

```text
src/roles/
  profile.rs       RoleProfile / RoleId / RoleAction
  catalog.rs       内置角色目录
  router.rs        RoleRouter，选 primary/support roles
  prompt.rs        RolePrompt，生成 role-specific prompt
  collaboration.rs support roles 协作计划
```

它不做：

- 不启动 async task
- 不收发 Message
- 不等待 LLM
- 不执行 action
- 不保存 session 状态

### src/agent/worker

放真正执行生命体：

```text
src/agent/worker/
  runtime.rs       Worker 状态机
  task.rs          Worker task
  state.rs         Worker state / phase
  memory.rs        Worker 临时记忆
  action_bridge.rs action 映射
```

当前 Worker 已经具备 role-aware 能力：

```text
Worker {
  id: AgentId,
  role: RoleProfile,
  cognition: Box<dyn Cognition + Send>,
  ...
}
```

Worker 是执行器。

RoleProfile 是 Worker 的身份配置。

## Prompt 放在哪里

第一阶段建议放在：

```text
src/roles/prompt.rs
```

原因：

- 角色还在快速变化
- Rust 单元测试更容易
- 不需要处理文件路径、发布 include、热加载

后续稳定后可以迁移到：

```text
prompts/zh/roles/
  ops.md
  data.md
  service.md
  creative.md
  engineering.md
  accounting.md
  design.md
  web.md
```

第一版 `RolePrompt` 应该短，不要照搬 Python `prompt_builder.py`。

建议结构：

```text
你是 {role.name}
角色：{role.runtime_role}
能力：{required_capabilities}
偏好动作：{preferred_actions}
任务：{task}
上下文：{facts}

要求：
- 只在本角色能力范围内回答
- 不编造数据
- 给出可执行结果
- 返回 Worker JSON schema
```

## 实现阶段

### Phase 1：RolePrompt

新增：

```text
src/roles/prompt.rs
```

定义：

```rust
pub struct RolePromptInput {
    pub role: RoleProfile,
    pub task: String,
    pub facts: Vec<String>,
}

pub struct RolePromptBuilder;
```

输出：

```rust
String
```

第一阶段先返回简短系统 prompt。

### Phase 2：Role-aware Worker

修改 `agent::worker::Worker`：

```rust
pub struct Worker {
    id: AgentId,
    role: Option<RoleProfile>,
    ...
}
```

或者更直接：

```rust
pub struct Worker {
    id: AgentId,
    role: RoleProfile,
    ...
}
```

推荐第二种。

因为未来 Worker 必须有角色，不应该存在无角色 Worker。

Worker 构造函数改成：

```rust
Worker::new(
    role: RoleProfile,
    cognition: Box<dyn Cognition + Send>,
    receiver: MessageRx,
    sender: MessageTx,
)
```

`id` 从 role 推导：

```rust
AgentId(format!("worker.{}", role.runtime_role))
```

### Phase 3：SessionRuntime 多 Worker

当前已经是：

```text
worker_channels: HashMap<WorkerId, MessageTx>
worker_loops: Vec<JoinHandle<()>>
```

SessionRuntime 启动时：

```text
for role in RoleCatalog:
  create channel
  create Worker(role)
  register worker tx
  spawn worker loop
```

这样 Commander 派发给：

```text
worker.ops
worker.data
worker.creative
```

就是真正不同的 Worker task。

默认兼容旧的 `worker_cognition` factory。

需要按角色区分时，使用 `RoleCognitionFactory`。

### Phase 4：Role-specific Cognition

现在 `AgentManagerConfig` 只有：

```rust
pub worker_cognition: CognitionFactory
```

已经支持：

```rust
pub worker_cognition: RoleCognitionFactory
```

类似：

```rust
pub type RoleCognitionFactory =
    Arc<dyn Fn(&RoleProfile) -> Result<Box<dyn Cognition + Send>> + Send + Sync>;
```

这样每个 role 可以拿到自己的 prompt：

```text
worker.ops -> ops prompt
worker.data -> data prompt
```

为了兼容简单使用方式，旧 factory 仍然保留，并自动适配到 role-aware factory：

```text
旧：Fn() -> Cognition
新：Fn(&RoleProfile) -> Cognition
```

### Phase 5：Commander RoleRouter

新增：

```text
src/roles/router.rs
```

Commander 收到用户任务后：

```text
RoleRouter::route(message)
  -> primary_role
  -> support_roles
```

当前已支持：

```text
dispatch task to worker.{primary_role}
primary done -> dispatch support roles
support done -> Commander 汇总角色贡献
```

### Phase 6：Support Roles

主 role 完成后：

```text
Commander 收到 primary WorkerReportFinished
  -> dispatch support roles
  -> collect support contributions
  -> synthesize final output
```

这个阶段已经落地第一版。

还没有做 token 级 streaming contribution，也没有做复杂最终 synthesis。

## 不要做的事

不要把每个 role 写成：

```text
src/agent/roles/ops.rs
src/agent/roles/data.rs
...
```

原因：

- 角色会越来越多
- 大量代码重复
- 角色差异主要是 prompt / capabilities / tools，不是不同 Rust 类型

正确做法：

```text
一个 Worker runtime
多个 RoleProfile
多个 RolePrompt
多个 role-specific cognition
```

## 最小落地顺序

已完成：

1. `src/roles/prompt.rs`
2. `RolePromptBuilder` 单元测试
3. `RoleCognitionFactory` 类型
4. `Worker` 持有 `RoleProfile`
5. `SessionRuntime` 启动多个 Worker loop
6. 更新 web examples / tests
7. `RoleRouter`
8. Commander 使用 RoleRouter 派发 primary role
9. Commander 派发 support roles
10. Worker 输出作为 role contribution 回流

下一步如果继续 role 方向，应该做：

```text
role contribution schema
collaboration SSE events
final synthesis prompt
role-specific LLM smoke example
```

## 核心判断

真正能干活的不是 `RoleProfile`。

真正能干活的是：

```text
Worker + RoleProfile + RolePrompt + Cognition
```

所以：

```text
roles 目录管“这个岗位是什么”
agent/worker 管“这个岗位如何在 session 里活起来并执行”
Commander 管“该让哪个岗位干活”
```
