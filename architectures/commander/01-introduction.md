# CommanderAgent 设计说明（阶段 1 冻结版）

---

## 1. 角色定位

CommanderAgent 是：

> 强中心化模型中的唯一决策者
> 系统的宏观认知层
> 负责任务分配与执行控制

Commander 是 Agent。
拥有自己的 runtime 与 cognition。
不拥有 action。

---

## 2. 系统位置

```
Human
  ↓
CommanderAgent
  ↓
WorkerAgent
```

在 World 结构中：

```
World
 ├── CommanderAgent
 ├── WorkerAgent A
 └── WorkerAgent B
```

World 仅负责生命周期与消息路由。
不参与决策。

---

## 3. Commander 的能力边界

### Commander 负责：

- 接收人类指令
- 解析任务语义
- 决定由哪个 Worker 执行
- 判断是否需要中断当前任务
- 向 Worker 发送命令
- 接收 Worker 执行结果
- 决定后续行为

### Commander 不负责：

- 具体任务执行
- 调用外部资源
- 推进 Worker 内部执行时间
- 管理线程或调度系统

---

## 4. Runtime 模型

Commander 是消息驱动的 Agent。

其内部运行循环为：

```
Receive Message
    ↓
Cognition
    ↓
Generate Decision
    ↓
Send Command (if needed)
    ↓
Idle / Wait
```

Commander 不存在 Acting 阶段。
只有认知与命令生成。

---

## 5. Worker 关系模型

### 5.1 Worker 执行模型（已冻结）

- 每个 Worker 同时只能执行一个任务
- Worker 不维护任务队列
- Worker 不进行优先级判断
- Worker 只执行当前任务
- Worker 支持被取消

---

### 5.2 Commander 对 Worker 的认知视图

Commander 必须维护：

```
WorkerStateView {
    worker_id
    status: Idle | Busy
    current_task: Option<TaskInfo>
}
```

该视图来自 Worker 报告。

Commander 不直接控制 Worker 内部状态。

---

## 6. 调度模型（阶段 1）

系统采用：

> 单任务占位决策模型

当新任务到达时：

1. 若 Worker Idle → 直接分配
2. 若 Worker Busy → 进入 Cognition 决策：

```
(current_task_context,
 new_task_context,
 system_context)
    ↓
Commander.Cognition
    ↓
Decision:
    - Keep current task
    - Cancel current task and replace
    - Ignore new task
```

不存在：

- 全局任务队列
- 优先级字段
- Worker 内部排队
- 多任务并发调度

---

## 7. Cognition 的职责

Commander 的 Cognition 负责：

- 能力匹配（选择 Worker）
- 任务重要性判断
- 是否中断当前任务
- 执行策略选择
- 执行完成后的下一步决策

Cognition 是决策核心。

不使用硬编码规则或优先级系统。

---

## 8. 架构哲学

当前阶段系统特征：

- 强中心化
- 单实例 Commander
- 单任务执行模型
- 无全局队列
- 无优先级系统
- 无分布式
- 无协商机制

系统目标：

> 构建最小可运行的强中心化智能电脑核心

---

## 9. 结构总结

```
Human
  ↓
CommanderAgent
  - Runtime
  - Cognition
  - WorkerRegistry
  - WorkerStateView
  ↓
WorkerAgent
  - Runtime
  - Cognition
  - Action
```

Commander 是宏观大脑。
Worker 是执行器。

架构冻结。
