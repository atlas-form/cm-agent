# Rust Agent 重构执行步骤

本文是后续 AI / Codex 改代码时的执行清单。

唯一设计依据：

- `architectures/00_FINAL_DESIGN.md`

不要再参考旧的永久生命体设计。

## Phase 1：协议上下文

目标：让所有消息具备多用户/多会话隔离能力。

需要增加或预留：

- `UserId`
- `WorkspaceId`
- `AgentId`
- `SessionId`
- `TaskId`

要求：

- `Message` 或外层 envelope 必须能携带 session context。
- 不允许只靠全局 sender 判断消息属于谁。
- 第一阶段可以先简单实现，不做复杂鉴权。

## Phase 2：AgentManager

目标：增加全局管理器。

职责：

- 创建 `AgentSession`
- 查找 active session
- 注册 active session
- 回收 session
- 持有共享依赖

禁止：

- 不做任务决策
- 不执行 action
- 不拼 prompt
- 不保存用户工作记忆

## Phase 3：AgentSession

目标：增加短生命 session 容器。

职责：

- 持有 session context
- 持有 `SessionContext`
- 创建 / 持有 `SessionRuntime`
- 管理本 session 的 WorkingMemory
- 在结束时触发 snapshot / event / memory 保存

第一阶段：

- 普通任务跑完即可关闭。
- TTL 保活可以先预留，不一定实现完整。

## Phase 4：SessionRuntime

目标：把真正的 agent runtime 放进 session。

职责：

- 使用 session 内 `SessionContext`
- 创建 Commander
- 创建 Workers
- 建立消息通道
- 投递 HumanCommand
- 等待 WorkerReport
- 收集最终输出
- 停止并释放 Commander / Worker

注意：

- `SessionRuntime` 才是真正的短生命 runtime。
- `AgentManager` 不是 runtime。

## Phase 5：删除 World，改为 SessionContext

目标：不再保留 `world` 作为核心概念。

修改方向：

- 原 `World` 内的 EndpointDirectory / WorkerCatalog / Blackboard / Extensions 移入 `SessionContext`。
- `get_worker_tx` / `list_worker_profiles` 这类全局查询要改成从 `SessionContext` 查询。
- 每个 session 有自己的 WorkerProfile 和 sender。
- 删除 `world` crate。

禁止：

- 不允许不同用户 session 共用同一个 `SessionContext`。
- 不允许继续引入全局 `world()` / `init_world()`。

## Phase 6：Commander / Worker 接入 SessionRuntime

目标：复用现有 Commander / Worker 思想，只改变生命周期。

要求：

- Commander 在 session 内创建。
- Worker 在 session 内创建。
- Commander 仍然是 session 内唯一决策者。
- Worker 仍然只和本 session Commander 通信。
- Worker 不跨 session 持有状态。

## Phase 7：最小 MemoryStore

目标：先建立抽象，不做复杂记忆系统。

需要：

```rust
trait MemoryStore {
    // 后续按需要补方法
}
```

第一阶段可以：

- 空实现
- 内存实现
- 简单文件实现

禁止：

- 不要把数据库表结构写进 agent core。
- 不要让 AgentManager 解释 memory 内容。

## Phase 8：最小可运行闭环

目标：跑通一次短生命 agent 调用。

流程：

```text
input
  -> AgentManager
  -> AgentSession
  -> SessionRuntime
  -> SessionContext
  -> Commander
  -> Worker
  -> WorkerReport
  -> Commander output
  -> SessionRuntime shutdown
```

验收：

- 能创建 session。
- 能启动 session runtime。
- 能创建 SessionContext / Commander / Worker。
- 能派发任务。
- Worker 能回报。
- session 能结束并释放。

## 不做事项

第一阶段不做：

- skill 系统
- Python role 编排
- 复杂长期 memory
- Web API
- 数据库持久化
- 多实例分布式
- WorkerPool 共享
- 前端事件流

## 最终提醒

只改生命周期，不推翻核心 agent 模型。

```text
旧：World / Commander / Worker 永久活着
新：SessionContext / Commander / Worker 在 SessionRuntime 内短暂活着
```

一句话：

> AgentManager 管 session；AgentSession 做隔离边界；SessionRuntime 跑真正的短生命 agent。
