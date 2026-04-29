# 最终设计定稿

本文是给后续 AI / Codex 执行重构用的最小核心说明。

最终结构已经定稿：

```text
AgentManager
  -> AgentSession
      -> SessionRuntime
          -> SessionContext
          -> Commander
          -> Worker
```

## 核心判断

`AgentManager` 和 `SessionRuntime` 不能混在一起。

`World` 不再作为独立 crate 或核心概念保留。

原来 `World` 里的 session 内共享资源，改名为 `SessionContext`，并归属于 `AgentSession / SessionRuntime`。

## AgentManager

全局多用户管理器。

它不是 runtime。

它只负责：

- 创建 `AgentSession`
- 恢复 `AgentSession`
- 注册 active session
- 根据 `SessionId` 查找 session
- 关闭 / 回收 session
- 持有共享依赖，如 LLM provider、MemoryStore、配置

它不负责：

- 思考
- 决策
- 执行 action
- 拼 prompt
- 保存用户工作记忆

## AgentSession

一次用户任务或一段连续对话的 session 容器。

它是多用户隔离边界。

它负责持有：

- `UserId`
- `WorkspaceId`
- `AgentId`
- `SessionId`
- `SessionContext`
- session 级 working memory

普通任务完成后可以销毁。

连续对话可以 TTL 保活。

长任务可以后台运行，完成后关闭。

## SessionRuntime

真正的短生命 agent runtime。

用户调用时启动。

任务完成后停止。

它负责在本 session 内运行：

- `Commander`
- `Worker`
- message loop
- action 推进
- worker report 汇总

`SessionRuntime` 不应该变成全局对象。

## SessionContext

`SessionContext` 替代原来的 `World`。

它不是 manager。

它不是 runtime。

它只保存当前 session 内部共享资源：

- commander / worker 通信端点
- worker catalog
- blackboard
- extensions
- 临时状态

禁止不同用户 session 共用同一个 `SessionContext`。

## Commander

仍然是强中心化唯一决策者。

但唯一性只在一个 session 内成立。

## Worker

仍然是执行生命体。

但 Worker 只在一个 session 内运行。

Worker 不跨 session 持有状态。

## 生命周期

```text
外部请求
  -> AgentManager 找到或创建 AgentSession
  -> AgentSession 启动 SessionRuntime
  -> SessionRuntime 使用 SessionContext 创建通信和状态边界
  -> SessionRuntime 创建 Commander / Worker
  -> Commander 决策
  -> Worker 执行
  -> WorkerReport 回 Commander
  -> Commander 输出结果
  -> 保存 event / memory / snapshot
  -> 停止 SessionRuntime
  -> 销毁 AgentSession 或 TTL 保活
```

## Crate 方向

目标 crate 边界：

```text
agent-manager
  AgentManager

agent-session
  AgentSession
  SessionRuntime
  SessionContext

agent
  Commander
  Worker

core
  Message
  Payload
  Id
  MessageContext
```

`world` crate 已删除。

`agent-runtime` crate 已拆分，不继续同时放 manager 和 runtime。

## 与 Python pipeline 的区别

Python 是：

```text
一次函数流程
role 编排
执行完结束
```

Rust 要做的是：

```text
短生命 runtime
内部有 SessionContext / Commander / Worker
通过 Message 通信
通过状态机推进
结束后保存状态
```

短生命不等于 pipeline。

## 重构原则

- 不照搬 Python agent。
- 不把 `AgentManager` 写成 pipeline。
- 不让全局对象持有用户记忆。
- 不保留全局 `World`。
- 不让 `SessionContext` 跨 session 共享。
- 不让 Worker 跨 session 持有状态。
- 不在第一阶段处理 skill。
- 不在第一阶段处理复杂长期 memory。
- LLM 暂时作为 app 启动阶段的 shared service 注册，后续再抽成更清晰的共享依赖。

## 一句话

> AgentManager 管多用户 session；AgentSession 是隔离边界；SessionRuntime 跑一次短生命 agent；SessionContext 替代旧 World。
