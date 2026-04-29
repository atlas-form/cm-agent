# Commander 结构设计文档（阶段 1 冻结版）

---

## 1. 角色定位

Commander 是：

> 强中心化模型中的唯一宏观决策者
> 一个完整的 Agent 生命体
> 纯认知节点

Commander：

- 不执行具体任务
- 不维护 Worker 列表
- 不维护 Worker 状态
- 不进行结构调度
- 不持有系统结构信息

它只负责：

> 接收输入 → 进行认知 → 生成决策意图

---

## 2. 与 World 的职责划分

### Commander 负责：

- 解析人类任务
- 进行宏观决策
- 判断当前任务与新任务的关系
- 生成 DecisionIntent

### World 负责：

- 持有 Worker 注册信息
- 持有 Worker 状态视图
- 根据 DecisionIntent 执行分发
- 向 Worker 发送控制消息
- 向 Commander 反馈 Worker 报告

Commander 不知道：

- 有多少 Worker
- Worker 当前是否忙碌
- Worker 能做什么

这些属于 World 的职责。

---

## 3. Commander 的结构（最终形态）

Commander 是一个完整的 Agent struct，
不拆分 runtime。

```rust
pub struct Commander {
    // 运行状态
    state: CommanderState,
    phase: CommanderPhase,

    // 当前正在处理的人类任务（单占位模型）
    current_task: Option<CommanderTask>,

    // 宏观认知核心
    cognition: Box<dyn Cognition>,

    // 消息通道
    receiver: Box<dyn MessageReceive>,
    sender: Box<dyn MessageSend>,

    // 内部记忆（用于构造 CognitionInput）
    memory: TaskMemory,
}
```

---

## 4. 运行模型

Commander 的 run() 模型与 Worker 对称：

```text
while state != Shutdown:
    process_messages()
    if Running:
        runtime_step()
    else:
        sleep
```

区别在于：

- Worker 有 Thinking / Acting
- Commander 只有 Thinking

Commander 不存在 Acting 阶段。

---

## 5. 状态模型

### CommanderState

- Idle
- Running
- Shutdown

### CommanderPhase

- Thinking
- Idle

无 Finished / Failed。
无 Acting。

---

## 6. 单任务占位模型

阶段 1 不引入队列。

Commander 同时只处理：

> 一个当前人类任务

当新任务到达时：

- 由 Cognition 决定：
  - 是否替换当前任务
  - 是否忽略
  - 是否产生新的决策意图

任务积压与调度由 World 处理。

Commander 不维护全局任务队列。

---

## 7. 决策输出模型

Commander 不直接分发任务。

它生成：

```text
DecisionIntent
```

DecisionIntent 只表达：

> 应该发生什么

不表达：

> 由谁执行
> 是否可以执行
> 当前系统状态如何

World 根据 DecisionIntent 进行结构执行。

---

## 8. 架构层级关系

```text
OS
 └── Tokio
       └── World
             ├── Commander
             └── Worker
```

内部生命模型对称：

```text
Commander
 ├── state
 ├── phase
 ├── cognition
 ├── memory
 ├── receiver
 └── sender
```

```text
Worker
 ├── state
 ├── phase
 ├── cognition
 ├── action
 ├── memory
 ├── receiver
 └── sender
```

唯一差异：

> Commander 无 Action 层

---

## 9. 阶段 1 冻结结论

Commander：

- 是完整 Agent
- 不拆 runtime
- 不持有系统结构信息
- 不持有 Worker 信息
- 不执行 Action
- 不维护队列
- 不使用优先级
- 决策完全由 Cognition 产生
- 只生成 DecisionIntent

结构干净。
边界清晰。
职责单一。
