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
- Commander / Worker async task 生命周期
- action 推进
- session 输出等待与关闭

`SessionRuntime` 不应该变成全局对象。

`SessionRuntime` 必须使用 async 执行模型。

禁止用长期 blocking loop 承载 Commander / Worker。

长时间等待 LLM / action / IO 时必须 `await`，不能占用 blocking thread。

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

Commander 在 `SessionRuntime` 内作为 async task 运行。

Commander 可以维护自己的内部 actor loop / state machine，用于接收消息、决策、调度 worker、汇总 worker report。

但 Commander 不负责创建 session 边界，也不持有跨 session runtime 生命周期。

默认 Commander 使用 fast route：

```text
HumanCommand
  -> RoleRouter
  -> worker.{primary_runtime_role}
```

因为当前产品内置固定 9 个 roles，Commander 不应该默认调用 LLM 来挑选 worker。

LLM Commander 只作为诊断、实验或后续复杂 fallback 使用。

## Worker

仍然是执行生命体。

但 Worker 只在一个 session 内运行。

Worker 不跨 session 持有状态。

Worker 在 `SessionRuntime` 内作为 async task 运行。

Worker 可以维护自己的内部 actor loop / state machine，用于接收 assignment、调用 cognition、推进 action、返回 report。

但 Worker 不负责创建或管理 `SessionRuntime`。

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

项目最终是一个独立 library crate，不再是 workspace 多 crate。

Web server 以后只依赖这一个 crate：

```toml
cm-agent = "0.1"
```

crate 内部模块边界：

```text
src/agent_manager
  AgentManager

src/agent_session
  AgentSession
  SessionRuntime
  SessionContext

src/agent
  Commander
  Worker

src/roles
  RoleProfile
  RoleCatalog
  RoleRouter
  RolePrompt
  RoleCollaborationPlan
  RoleContribution

src/core
  Message
  Payload
  Id
  MessageContext
```

`world` crate 已删除。

`agent-runtime` crate 已拆分，不继续同时放 manager 和 runtime。

`app` / `agent-ui` 已删除，不作为库的一部分。

本地 web 使用示例放在：

```text
src/bin/web_request_example.rs
src/bin/web_stream_example.rs
```

## 默认 Roles

这个项目不是通用空 agent 框架。

library 初始化时默认带业务角色：

```text
chat
ops
data
service
creative
engineering
accounting
design
web
```

每个 `AgentSession` 启动时，`SessionRuntime` 会按 `RoleCatalog` 创建一组 session 内短生命 Worker：

```text
worker.chat
worker.ops
worker.data
worker.service
worker.creative
worker.engineering
worker.accounting
worker.design
worker.web
```

`RoleProfile` 只描述角色身份、能力、关键词和偏好动作。

真正执行的是：

```text
Worker + RoleProfile + RolePrompt + Cognition
```

`Commander` 使用 `RoleRouter` 选择 primary role 和 support roles。

普通提问、解释、闲聊、概念说明，默认进入 `chat` role。

primary role 先执行，support roles 后续并发补充，最后由 Commander 汇总 `RoleContribution`。

role 协作过程通过 `SessionEvent` 暴露，web server 可转换为 SSE。

## Prompt 规则

Prompt 内容必须放在 markdown 文件里。

Rust 代码只负责：

- 选择 prompt 文件路径
- 调用 `agent_utils::prompt::Prompt`
- 渲染 `{{key}}` 变量
- 提取 `JSON schema:`
- 把 prompt 注入 cognition context 或 system message

禁止在 Rust 代码里硬编码大段 prompt 文案。

允许在 Rust 代码里出现的 prompt 相关内容只有：

- prompt 文件路径
- 模板变量名
- fallback 错误信息
- 很短的结构性标签

Role prompt 应放在：

```text
prompts/zh/roles/
  chat.md
  ops.md
  data.md
  service.md
  creative.md
  engineering.md
  accounting.md
  design.md
  web.md
```

Cognition prompt 应放在：

```text
prompts/zh/cognition/
  commander_routing.md
  worker_execution.md
```

如果要新增 prompt，先新增 md 文件，再让 Rust 加载它。

不要把 prompt 写进 `src/roles/prompt.rs`、Commander、Worker 或 CognitionEngine。

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
- LLM 后续作为 library 的共享依赖注入，不再依赖 app 启动层。

## 一句话

> AgentManager 管多用户 session；AgentSession 是隔离边界；SessionRuntime 跑一次短生命 agent；SessionContext 替代旧 World。
