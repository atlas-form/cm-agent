# Streaming Follow-up Plan

基础 session event stream 已完成：

- `SessionEvent`
- bounded event channel
- `AgentManager::run_stream`
- `AgentSession::run_stream`
- runtime lifecycle events
- worker started / finished events
- role collaboration events

本文只保留未完成的 streaming 后续事项。

## 目标

让 agent core 继续只暴露 `SessionEvent`，不引入 HTTP / SSE 框架依赖。

外部 web server 负责：

- 把 `SessionEvent` 映射为 SSE event。
- 在 client 断开时取消 session。
- 根据业务需要处理重连、鉴权和前端协议。

agent core 负责：

- 可取消正在运行的 session。
- 输出真实 LLM token chunk。
- 保持 bounded backpressure，不让慢客户端无限堆内存。

## 未完成项

### 1. Cancel session

新增 API：

```rust
AgentManager::cancel_session(session_id: &SessionId) -> Result<()>
```

需要调整：

- `AgentManager.active_sessions` 不能再是 `HashMap<SessionId, ()>`。
- active registry 需要保存可取消 handle，例如 shutdown sender / abort handle / session handle。
- `run_stream` 产生的后台任务必须能收到 cancel signal。
- cancel 后需要关闭 event channel，并从 active registry 移除 session。

第一版建议：

```rust
struct ActiveSession {
    shutdown_tx: tokio::sync::watch::Sender<bool>,
    task: tokio::task::JoinHandle<()>,
}
```

验收：

- stream 正在运行时调用 `cancel_session`，runtime 停止。
- receiver 最终结束或收到 `SessionEvent::Failed { reason: "cancelled" }`。
- `active_session_count()` 回到 `0`。

### 2. LLM token stream

当前已有事件：

```rust
SessionEvent::LlmChunk {
    session_id: SessionId,
    content: String,
}
```

缺失：

- cognition 层的 streaming trait 或可选 streaming 方法。
- `model-gateway-rs` stream chunk 到 `SessionEvent::LlmChunk` 的映射。
- Commander / Worker 如何传递 event sender。
- token chunk 合并策略。

第一版建议：

- 保留现有 `Cognition::evaluate`。
- 新增可选 streaming adapter，不强制所有 cognition 实现。
- 只在真实 LLM cognition 中发 `LlmChunk`。
- 非 streaming cognition 继续只返回最终 output。

验收：

- 使用真实 LLM smoke example 时能看到 `llm_chunk` event。
- 普通 fake cognition 测试不受影响。
- token chunk 不能跳过最终 `Output / Finished`。

### 3. Backpressure stress

当前 event channel 已是 bounded。

还需要补：

- 慢消费者测试。
- 高频 `LlmChunk` 合并或等待策略。
- 关键事件不丢失的测试。

规则：

- `Started / Output / Failed / Finished` 不能丢。
- `LlmChunk` 可以合并。
- 不使用 unbounded queue 承载 streaming token。

验收：

- 模拟慢 receiver 时内存不无限增长。
- 关键事件仍能到达。
- runtime 不因为普通慢客户端永久卡死。

## 不做

本阶段不做：

- HTTP server
- SSE 框架绑定
- 前端协议
- 断点续传
- 多节点分布式 session
- 数据库持久化 event log
