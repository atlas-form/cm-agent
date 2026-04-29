# Worker 结构设计冻结文档（阶段 1）

## 1. 设计前提

本结构设计基于已冻结的 Runtime 语义：

- 单任务模型
- Worker 自主掌控时间
- 消息驱动
- 认知（Cognition）与执行（Action）交替推进
- 协作式暂停
- 无 async runtime
- 无多任务调度

本阶段目标：

> 定义 Worker 的“最小必要结构”，不讨论实现细节。

---

## 2. Worker 的责任边界

Worker 必须承担以下职责：

1. 生命周期管理（Idle / Running / Paused / Shutdown）
2. 当前任务管理（单任务）
3. 认知-执行交替控制
4. 当前 Action 持有与推进
5. 消息驱动通信
6. Memory 存储与任务状态保持

Worker 不是：

- 多任务调度器
- 操作系统
- async executor
- 进程管理器

---

## 3. 必须存在的内部状态

### 3.1 运行状态

```rust
state: WorkerState
```

用于控制生命周期：

```rust
enum WorkerState {
    Idle,
    Running,
    Paused,
    Shutdown,
}
```

---

### 3.2 当前执行阶段

```rust
phase: WorkerPhase
```

用于认知-执行交替：

```rust
enum WorkerPhase {
    Thinking,
    Acting,
    Finished,
    Failed,
}
```

---

### 3.3 当前任务

```rust
current_task: Option<Task>
```

单任务模型下，同一时间最多一个任务。

---

### 3.4 当前 Action

```rust
current_action: Option<Box<dyn Action>>
```

仅在 Acting 阶段存在。

---

### 3.5 Cognition 引擎

```rust
cognition: Box<dyn Cognition>
```

用于 Thinking 阶段生成下一步 Action。

---

### 3.6 通信接口

```rust
receiver: Box<dyn MessageReceive>,
sender: Box<dyn MessageSend>,
```

用于消息驱动模型。

---

### 3.7 Memory（任务上下文）

```rust
memory: TaskMemory
```

用于：

- 记录任务进度
- 存储中间状态
- 支持阶段间数据传递

---

## 4. Worker 的最小字段集合（阶段 1）

```rust
pub struct Worker {
    id: AgentId,

    state: WorkerState,
    phase: WorkerPhase,

    current_task: Option<Task>,
    current_action: Option<Box<dyn Action>>,

    cognition: Box<dyn Cognition>,

    receiver: Box<dyn MessageReceive>,
    sender: Box<dyn MessageSend>,

    memory: TaskMemory,
}
```

---

## 5. 设计原则

- 字段来源于语义推导，而非功能堆叠
- 每个字段必须支撑已冻结的 Runtime 语义
- 不为未来复杂度提前买单
- 当前仅支持单任务模型

---

## 6. 一句话定义 Worker（阶段 1）

> Worker 是一个承载认知-执行交替状态机的单任务消息驱动生命体。
