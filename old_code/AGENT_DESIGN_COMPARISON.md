# Agent 设计对比：Python 旧项目 vs Rust 新项目

本文件只比较 agent 本体设计。

暂不讨论：

- skill 体系
- LLM 客户端
- API / CRUD
- 数据库表设计
- 前端 SSE 展示

目标是先判断：旧 Python 项目里的“Agent”到底是什么，和当前 Rust 项目已经设计好的多 Agent 架构有什么本质差异。

## 结论先行

Python 旧项目里的 Agent 更像“角色化调用管线”，不是严格意义上的长期运行 Agent。

Rust 当前设计里的 Agent 是“消息驱动的生命体”：

- 有自己的 `run()`
- 有内部状态
- 有消息收发边界
- 有 Cognition / Action 生命周期
- Worker 只能通过 Commander 接收任务和回报结果

所以第一阶段不应该照搬 Python 的 Agent 写法，而应该把 Python 中有价值的 agent 行为抽象出来，映射到 Rust 的：

- `CommanderAgent`
- `WorkerAgent`
- `World`
- `Message`
- `Cognition`
- `Action`

## Python 旧项目的 Agent 形态

旧项目中和 Agent 直接相关的核心代码主要在：

- `server/src/core/chat_pipeline.py`
- `server/src/core/multi_agent.py`
- `server/src/core/role_router.py`
- `server/src/core/intent.py`
- `server/src/services/cross_role_service.py`
- `server/src/services/workflow_orchestration.py`
- `server/src/models.py`
- `server/src/routes/agents.py`

### 1. Agent 主要是 role

Python 中的 agent 多数时候等价于一个岗位角色：

- `ops`
- `data`
- `service`
- `design`
- `accounting`
- `engineering`
- `web`
- `creative`

`models.py` 里的 `AgentInfo` 也只是数据描述：

```python
class AgentInfo(BaseModel):
    id: int
    name: str
    display_name: str
    role: str
    description: str = ""
    avatar: str = ""
    enabled: bool = True
```

它不是一个有生命周期的对象。

### 2. Python 没有真正的 Agent.run()

旧项目没有类似下面这样的 agent 生命体：

```rust
agent.run()
```

Python 的执行方式更像：

```text
用户请求
  -> chat_pipeline
  -> 判断 primary_role / support_roles
  -> 对每个 role 发起一次 LLM/工具调用
  -> 汇总结果
  -> 返回前端
```

这是一条请求管线，不是多个长期运行的 Agent。

### 3. 多 Agent 协作是一次性并发调用

`multi_agent.py` 的核心函数是：

```python
dispatch_support_agents(...)
```

它做的事情是：

1. 主 Agent 回复后，拿到 `support_roles`
2. 为每个 support role 创建一个 asyncio task
3. 每个 task 跑一次支持角色分析
4. 收集 token / reply / skills_used
5. 汇总成 `collab_done`

这说明旧项目里的“多 Agent”更接近：

```text
primary role 输出完成后
并发调用多个 support role
最后合并贡献
```

它不是：

```text
多个 Agent 长期存在
通过消息彼此协作
各自维护内部状态
```

### 4. Python 有 handoff，但不是强中心消息模型

`cross_role_service.py` 提供了：

- `create_handoff`
- `get_pending_handoffs`
- `complete_handoff`

它通过数据库表 `handoff_queue` 做角色间移交。

这是一种“业务记录式移交”，不是 Rust 当前设计里的内存消息通道：

```text
Commander -> Worker
Worker -> Commander
```

Python 的 handoff 可以作为行为参考，但不应该直接成为 Rust 的 agent 通信模型。

### 5. Python 的中心是 chat_pipeline，不是 CommanderAgent

旧项目真正的“大脑”是 `chat_pipeline.py`：

- 识别意图
- 选择角色
- 构造 prompt
- 控制 tool loop
- 控制质量重试
- 控制多 Agent 协作
- 控制事件输出

也就是说，Python 的中心不是一个 Agent 对象，而是一个巨大 pipeline 函数集合。

这就是旧项目难读的核心原因：决策、调度、执行、展示事件、质量控制混在同一条管线里。

## Rust 当前 Agent 设计

Rust 项目已经有更清晰的边界。

参考：

- `architectures/01_agent.md`
- `architectures/commander/01-introduction.md`
- `architectures/worker/01_structure.md`
- `crates/agent/src/commander/commander.rs`
- `crates/agent/src/worker/worker.rs`

### 1. World 是社会环境

Rust 中：

```text
World
 ├── CommanderAgent
 ├── WorkerAgent A
 ├── WorkerAgent B
 └── WorkerAgent C
```

World 不决策，不执行任务。

World 只负责：

- Agent 注册
- 生命周期管理
- 消息路由
- 启动 Agent

### 2. Commander 是唯一决策者

Commander 负责：

- 接收外部输入
- 理解任务
- 选择 Worker
- 派发任务
- 接收 Worker 结果
- 决定下一步

Commander 不负责：

- 具体执行
- 外部工具动作
- Worker 内部时间推进

### 3. Worker 是执行生命体

Worker 有明确内部状态：

```rust
state: WorkerState
phase: WorkerPhase
current_task: Option<Task>
current_action: Option<Box<dyn Action>>
memory: TaskMemory
```

Worker 的核心循环是：

```text
处理消息
如果 Running:
  Thinking -> Acting -> Thinking -> ...
```

这和 Python 的一次性 support role task 完全不同。

### 4. Rust 的通信是强中心化

Rust 冻结设计要求：

```text
Human -> Commander
Commander -> Worker
Worker -> Commander
Commander -> Human
```

Worker 之间不互相通信。

这点和 Python 的 cross-role handoff 不同。Python 里角色可以通过数据库表形成横向移交；Rust 中即使需要“移交”，也应该由 Commander 决策和转发。

## 核心差异表

| 维度 | Python 旧项目 | Rust 新项目 |
|---|---|---|
| Agent 本质 | role + pipeline 调用 | 长期运行的 struct 生命体 |
| 生命周期 | 请求内临时存在 | `new()` 后 `run()` 长期存在 |
| 中心节点 | `chat_pipeline.py` | `CommanderAgent` |
| 执行者 | role-specific LLM 调用 | `WorkerAgent` |
| 多 Agent | support roles 并发补充 | Commander 派发多个 Worker |
| 通信 | 函数调用、SSE、DB handoff | Message channel |
| 状态 | 多在数据库/局部变量中 | Agent 内部 state/phase/memory |
| 决策 | pipeline 内混合判断 | Commander Cognition |
| 执行 | 工具调用循环混在 pipeline | Worker Cognition + Action |
| 角色 | 业务岗位 | Worker profile/capability |

## Python 中值得保留的 Agent 行为

虽然 Python 的结构不适合照搬，但有些行为值得迁移：

### 1. 主角色 + 支持角色

Python 有：

```text
primary_role
support_roles
```

Rust 可以映射为：

```text
Commander 选择一个主 Worker
必要时再派发若干支持 Worker
Commander 汇总结果
```

注意：支持 Worker 之间不直接通信。

### 2. 角色能力匹配

Python 的 `role_router.py` 做了大量岗位匹配规则。

Rust 中不应该叫 role router，而应该变成：

```text
Worker capability matching
```

也就是 Commander 认知输入中的 WorkerProfile。

### 3. 协作结果汇总

Python 的 `AgentContribution` 很有参考价值：

```python
role
display_name
reply
skills_used
elapsed_ms
```

Rust 可以设计成 WorkerReport 的一部分，但不要保留 `role` 作为核心身份，应该用：

```text
worker_id
capability
task_id
result
elapsed
```

### 4. 超时和降级

Python 多 Agent 协作里有：

- 单角色 timeout
- 总 timeout
- degraded reply
- fallback contribution

Rust 后续也需要，但这应该属于 Commander 的任务控制策略，或者 Worker Action 的失败/超时报告，不应该混进 Agent 本体第一阶段。

## Python 中不应该照搬的部分

### 1. 不照搬 chat_pipeline

`chat_pipeline.py` 是旧项目复杂度最高的地方。

它不是 Rust 架构中的一个模块，而应该被拆散：

- 意图识别 -> Commander Cognition
- 路由 -> Commander 决策
- 工具调用 -> Worker Action
- 协作汇总 -> Commander 处理 WorkerReport
- 事件输出 -> UI/外部 adapter

### 2. 不照搬 role = agent

Rust 里 Worker 可以有角色能力，但 Worker 不应该只是 role 字符串。

Rust 中更合适的是：

```rust
WorkerProfile {
    worker_id,
    name,
    description,
    capabilities,
    constraints,
    status,
}
```

### 3. 不照搬 DB handoff

Python 的 `handoff_queue` 是业务系统里的持久化移交。

Rust 第一阶段应该使用内存消息模型：

```text
Commander send Message to Worker
Worker send Report to Commander
```

如果以后需要持久化，再由外层 storage/event log 承担。

## 第一阶段建议目标

第一阶段只做 Agent 核心，不碰 skill/LLM 细节。

目标应该是：

```text
HumanCommand
  -> Commander
  -> Commander 判断任务需要哪些 Worker
  -> Worker 执行模拟 Action
  -> WorkerReport
  -> Commander 汇总
  -> 对外输出最终结果
```

最小闭环：

1. 支持多个 Worker 注册
2. Commander 能看到 WorkerProfile
3. Commander 能把任务派给指定 Worker
4. Worker 能维护单任务状态
5. Worker 完成后回报 Commander
6. Commander 能等待/汇总一个或多个 Worker 结果

## 建议映射关系

| Python 概念 | Rust 映射 |
|---|---|
| `primary_role` | Commander 选择的主 Worker |
| `support_roles` | Commander 追加派发的支持 Worker |
| `AgentContribution` | `WorkerReportFinished` / `WorkerContribution` |
| `dispatch_support_agents` | Commander 多 Worker 派发与汇总流程 |
| `handoff_queue` | Commander-mediated message dispatch |
| `role_router` | WorkerProfile capability matching |
| `chat_pipeline` | 拆为 Commander Cognition + Worker Action + UI adapter |
| `collab_done` | Commander final summary event |

## 最重要的设计判断

Python 旧项目没有一个值得直接迁移的 Agent 类。

真正值得迁移的是行为：

- 主次角色协作
- 角色能力匹配
- 多贡献汇总
- 超时降级
- handoff 语义

但这些行为必须重新放进 Rust 已有的强中心模型里：

```text
Commander 决策
Worker 执行
Message 通信
Action 推进
World 管生命周期
```

这应该是后续重构的第一原则。
