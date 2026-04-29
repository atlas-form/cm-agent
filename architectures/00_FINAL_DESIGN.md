# 最终设计定稿

本文是给后续 AI / Codex 执行重构用的最小核心说明。

不要重新推翻现有 Rust 架构。

最终思想只有两点：

1. 把原来的单用户永久生命改成短生命。
2. 在外面加一个全局 `AgentManager`。

## 核心结构

```text
AgentManager
  -> AgentSession
      -> SessionRuntime
          -> World
              -> Commander
              -> Worker
```

## 核心定义

### AgentManager

全局管理器。

它不是 runtime。

它不思考、不决策、不执行任务。

它只负责：

- 创建 `AgentSession`
- 恢复 `AgentSession`
- 注册 active session
- 根据 `SessionId` 查找 session
- 关闭 / 回收 session
- 持有共享依赖，如 LLM provider、MemoryStore、配置

### AgentSession

一次用户任务或一段连续对话的运行上下文。

它是短生命的。

普通任务完成后可以销毁。

连续对话可以 TTL 保活。

长任务可以后台运行，完成后关闭。

### SessionRuntime

真正的短生命 agent runtime。

用户调用时启动。

任务完成后停止。

它负责在 session 内运行：

- `World`
- `Commander`
- `Worker`
- message loop
- action 推进
- worker report 汇总

### World

不再是全局永久单例。

World 属于某个 `SessionRuntime`。

World 只保存当前 session 内的通信端点和 WorkerProfile。

### Commander

仍然是强中心化唯一决策者。

但唯一性只在一个 session 内成立。

### Worker

仍然是执行生命体。

但 Worker 只在一个 session 内运行。

## 生命周期

```text
外部请求
  -> AgentManager 找到或创建 AgentSession
  -> AgentSession 启动 SessionRuntime
  -> SessionRuntime 创建 World / Commander / Worker
  -> Commander 决策
  -> Worker 执行
  -> WorkerReport 回 Commander
  -> Commander 输出结果
  -> 保存 event / memory / snapshot
  -> 停止 SessionRuntime
  -> 销毁 AgentSession 或 TTL 保活
```

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
内部有 World / Commander / Worker
通过 Message 通信
通过状态机推进
结束后保存状态
```

短生命不等于 pipeline。

## 重构原则

- 不照搬 Python agent。
- 不把 `AgentManager` 写成 pipeline。
- 不让全局对象持有用户记忆。
- 不让 World 全局共享。
- 不让 Worker 跨 session 持有状态。
- 不在第一阶段处理 skill。
- 不在第一阶段处理复杂长期 memory。
- LLM 先沿用已有实现。

## 第一阶段代码目标

1. 增加 `AgentManager`。
2. 增加 `AgentSession`。
3. 增加 `SessionRuntime`。
4. 让 `World` 从全局概念变成 session 内对象。
5. 让 `Commander / Worker` 在 `SessionRuntime` 内创建和运行。
6. 给协议加 `SessionId / UserId / AgentId / WorkspaceId` 这类上下文。
7. 增加最小 `MemoryStore` trait，但第一阶段可以先空实现或文件实现。
8. 保持现有 Commander / Worker / Action / Cognition 思想不变。

## 一句话

> AgentManager 管 session；AgentSession 里跑真正的短生命 agent runtime；任务结束后保存状态并销毁。
