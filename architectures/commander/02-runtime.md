# 一、Commander Runtime 的目标

它必须：

1. 接收消息
2. 构造 CognitionInput
3. 调用 Cognition
4. 根据结果生成决策
5. 向 Worker 发送命令（如果需要）
6. 进入等待状态

没有 Action 阶段。

---

# 二、Commander Runtime 状态机

最干净的状态模型如下：

```text
Idle
  ↓ (收到消息)
Processing
  ↓ (调用 cognition)
Deciding
  ↓
Dispatching (可选)
  ↓
Idle
```

但我们可以进一步简化。

因为 Commander 没有 Acting 阶段。

所以本质上它是：

```text
Idle
  ↓
Think
  ↓
Idle
```

Think 内部完成：

- 解析
- 判断
- 发送命令

---

# 三、消息驱动模型

Commander 必须响应三类消息：

1. HumanCommand
2. WorkerReport
3. SystemEvent（可选）

但阶段 1 我们只实现：

- HumanCommand
- WorkerReport

---

# 四、Commander Runtime 的核心循环

下面是**结构级实现草稿**（不是完整代码，而是可直接变成 Rust 的逻辑骨架）。

---

## 1️⃣ Runtime 枚举

```rust
enum CommanderState {
    Idle,
    Processing,
}
```

你其实可以不需要复杂状态。

因为 Commander 是同步认知型。

---

## 2️⃣ Runtime 主循环

```rust
pub async fn run(&mut self) {
    loop {
        match self.inbox.recv().await {
            Some(message) => {
                self.handle_message(message).await;
            }
            None => break,
        }
    }
}
```

---

## 3️⃣ 处理消息

```rust
async fn handle_message(&mut self, message: Message) {
    // 1. 构造 CognitionInput
    let input = self.build_cognition_input(message);

    // 2. 调用 cognition
    let output = self.cognition.process(input);

    // 3. 根据决策结果执行
    self.apply_decision(output).await;
}
```

Commander runtime 的本质就是：

> 收到消息 → 认知 → 应用结果

---

# 五、apply_decision 的结构

```rust
async fn apply_decision(&mut self, decision: Decision) {
    match decision {
        Decision::Dispatch { worker_id, task } => {
            self.send_to_worker(worker_id, task).await;
        }
        Decision::CancelAndReplace { worker_id, task } => {
            self.send_cancel(worker_id).await;
            self.send_to_worker(worker_id, task).await;
        }
        Decision::Ignore => {}
    }
}
```

没有队列。
没有优先级。
没有复杂状态推进。

---

# 六、为什么不需要复杂状态机？

因为：

- Worker 内部已经是状态机
- Commander 不执行 Action
- Commander 不推进时间
- Commander 只在消息到达时运行

所以它本质上是：

> 纯反应型认知节点

---

# 七、和 Worker Runtime 的对比

Worker：

```
Thinking → Acting → Thinking → …
```

Commander：

```
Receive → Think → Dispatch → Idle
```

层级非常清晰。

---

# 八、现在 Commander Runtime 的核心职责

它维护：

- WorkerRegistry
- WorkerStateView
- Cognition
- Inbox / Outbox

它不维护：

- 全局任务队列
- 优先级系统
- 调度树
- 多阶段推进器

---

# 九、现在可以正式冻结 Runtime v1

Commander Runtime v1 特征：

- 单线程逻辑
- 消息驱动
- 无内部调度
- 无时间主权
- 认知即决策

结构干净。
