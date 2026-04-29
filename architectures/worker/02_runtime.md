# Worker Runtime 设计冻结文档（阶段 1）

## 1. 设计目标

本 Runtime 设计用于支持：

- 单任务模型
- Worker 自主掌控时间
- 消息驱动运行
- 认知（Cognition）与执行（Action）交替推进
- 可暂停（协作式）
- 结构简单、可扩展

当前阶段不考虑：

- 多任务调度
- 优先级抢占
- 并发 Action
- async runtime
- executor / poll / Future

---

## 2. 运行模型总览

Worker 是一个长期运行的 Actor：

```text
Worker.run():
    loop:
        处理消息

        if state == Running:
            runtime_step()
```

Idle 状态下阻塞等待消息。

Running 状态下进行认知-执行交替推进。

---

## 3. Runtime 的本质定义

> Runtime 是一个“认知-执行交替状态机”的推进循环。

它不是：

- 外部驱动的 step 调用器
- async executor
- poll 机制
- 多任务调度器

它只是 Worker 内部状态机的推进逻辑。

---

## 4. 内部状态模型

```rust
enum WorkerPhase {
    Thinking,   // 调用 cognition
    Acting,     // 执行当前 action
    Finished,
    Failed,
}
```

Worker 在 Running 状态下处于 Thinking 或 Acting 阶段。

---

## 5. runtime_step 语义

一次 runtime_step 只能推进一个阶段。

### Thinking 阶段

```text
调用 cognition
生成下一个 Action
如果有 Action → phase = Acting
如果无 Action → phase = Finished
```

### Acting 阶段

```text
调用 action.step()
如果 Pending → 保持 Acting
如果 Completed → phase = Thinking
如果 Failed → phase = Failed
```

---

## 6. Step 设计原则

一个 step 必须满足：

1. 有限时间内返回
2. 只推进一个阶段
3. 不掌控时间
4. 不包含内部无限循环

时间始终属于 Worker。

---

## 7. LLM 调用语义（阶段 1）

当前阶段允许：

- 同步 HTTP 调用
- step 可能阻塞
- pause 在 step 之间生效

语义说明：

> 暂停是协作式延迟生效，而非强制抢占。

未来可升级为后台线程模式，不影响 Runtime 结构。

---

## 8. 当前复杂度边界

不支持：

- 多任务
- 优先级
- 抢占式暂停
- 并发 Action
- async / Future / poll

当前目标是最小可运行、可扩展模型。

---

## 9. 一句话定义 Runtime

> Runtime 是一个由 Worker 控制时间的、认知与执行交替推进的有限状态机循环。
