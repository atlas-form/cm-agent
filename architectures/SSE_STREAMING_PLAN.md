# SSE Streaming Plan

本文是下一阶段实现 SSE / streaming 输出的设计说明。

目标不是把 web server 写进 agent core。

目标是让 agent core 暴露统一的 session event stream，web server 只负责把 event 转成 SSE。

## 核心结构

现有结构不变：

```text
AgentManager
  -> AgentSession
      -> SessionRuntime
          -> SessionContext
          -> Commander
          -> Worker
```

新增输出通道：

```text
SessionRuntime
  -> SessionEvent channel
      -> web server
          -> SSE
```

## 核心原则

- agent core 不依赖 HTTP。
- agent core 不依赖 SSE 框架。
- `SessionRuntime` 只发 `SessionEvent`。
- web server 把 `SessionEvent` 映射为 SSE event。
- client 断开时，web server 要能取消 session。
- 慢客户端不能导致无限内存堆积。

## SessionEvent

第一版事件模型：

```rust
pub enum SessionEvent {
    Started {
        session_id: SessionId,
    },
    CommanderThinking {
        session_id: SessionId,
    },
    WorkerStarted {
        session_id: SessionId,
        worker_id: WorkerId,
        task_id: TaskId,
    },
    LlmChunk {
        session_id: SessionId,
        content: String,
    },
    WorkerFinished {
        session_id: SessionId,
        worker_id: WorkerId,
        task_id: TaskId,
    },
    Output {
        session_id: SessionId,
        content: String,
    },
    Failed {
        session_id: SessionId,
        reason: String,
    },
    Finished {
        session_id: SessionId,
    },
}
```

第一阶段可以先不接真实 LLM stream，只把 runtime 阶段事件发出来。

真实 LLM streaming 后续再把 `model-gateway-rs` 的 stream chunk 映射到 `SessionEvent::LlmChunk`。

## API 方向

保留普通一次性调用：

```rust
AgentManager::run_request(request) -> SessionResult
```

新增 streaming 调用：

```rust
AgentManager::run_stream(request) -> SessionEventStream
```

其中 `SessionEventStream` 可以先是：

```rust
pub type SessionEventStream = tokio::sync::mpsc::Receiver<SessionEvent>;
```

后续如果要更通用，再改成 `Stream<Item = SessionEvent>`。

## Backpressure

事件 channel 不要用无限队列。

建议第一版使用 bounded channel：

```rust
tokio::sync::mpsc::channel(1024)
```

策略：

- 关键事件不能丢：`Started / Output / Failed / Finished`
- 高频 token chunk 可以合并
- 如果 channel 满，优先等待，而不是无限堆内存

## Cancel

SSE client 断开后，web server 需要取消 session。

第一版可以支持：

```rust
AgentManager::cancel_session(session_id)
```

取消后：

- 给 `SessionRuntime` 发 shutdown signal
- 关闭 event channel
- 从 active session registry 移除

## 实现步骤

1. 在 core protocol 增加 `SessionEvent`。
2. 在 `SessionRuntimeInput` 增加可选 event sender。
3. `SessionRuntime` 启动时发送 `Started`。
4. Commander / Worker 关键阶段发送事件。
5. `run_request` 保持原样。
6. 新增 `run_stream`，返回 event receiver。
7. 新增 `src/bin/stream_smoke.rs`，不用 web，只打印 event stream。
8. 压测 streaming event channel，确认不会造成无限内存增长。

## 不做事项

第一版不做：

- web server
- HTTP SSE 框架绑定
- 真实 LLM token stream
- 前端协议
- 断点续传
- 多节点分布式 session

## 判断标准

本阶段成功标准：

```text
不用真实 LLM
不用 web server
一个 session 能输出完整事件流
10000 个 session event stream 不阻塞 runtime
普通 run_request 不受影响
```
