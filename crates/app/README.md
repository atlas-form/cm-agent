# App Crate 说明

本文档用于说明 `crates/app` 在整个项目中的定位，以及当前启动流程与模块职责。

---

## 1. 这个 crate 是做什么的？

`app` 是整个 workspace 的**统一入口层**，负责把下面几类能力组织起来并启动：

- `world`：全局运行时上下文（端点目录、扩展能力、共享状态）
- `agent`：核心执行实体（`Commander` / `Worker`）
- `agent-ui`：终端人机交互界面

也就是说，`app` 不承担复杂业务决策本身，而是承担**启动编排（orchestration）**职责。

---

## 2. 代码结构（按职责拆分）

### 2.1 入口

- `src/main.rs`
  - 创建 `AppStartup`
  - 启动 UI 主循环
  - UI 退出后统一触发 shutdown

### 2.2 启动编排

- `src/startup/mod.rs`
  - 定义 `AppStartup`
  - 负责按顺序启动 world / agent / ui
  - 负责统一关停 runtime 任务

### 2.3 world 启动

- `src/startup/world.rs`
  - 初始化 world
  - 创建并返回四类消息通道：
    - commander 输入
    - commander 输出
    - worker 输入
    - worker 输出
  - 并把必要端点注册到 world

### 2.4 agent 启动

- `src/startup/agent.rs`
  - 用 `Commander` 和 `Worker` 创建两个长期循环实体
  - 每个实体接收自己的 inbox，发送到自己的 outbox
  - 以 `tokio::spawn_blocking` 运行 `run()` 循环

### 2.5 UI 启动

- `src/startup/ui.rs`
  - 定义 `AgentBridgeSession`（承接消息收发）
  - 负责把用户输入包装成 `Payload::HumanCommand` 发给 commander
  - 等待 commander 外部回报，并把结果回给 startup 层

### 2.6 启动期认知实现

- `src/startup/cognition.rs`
  - 当前提供 `SimpleCommanderCognition` / `SimpleWorkerCognition`
  - 作用是跑通框架流程（演示级），后续可替换为真实 cognition 逻辑

---

## 3. 当前完整流程（一步步）

1. `main()` 调用 `AppStartup::boot()`
2. `boot()` 初始化 world 与通道
3. `boot()` 启动 commander / worker 运行循环
4. `boot()` 构造 `AgentBridgeSession` 并创建 `TerminalUiApp`
5. `main()` 调用 `run_ui()`，进入终端交互
7. 用户输入任务后：
   - UI -> commander（`HumanCommand`）
   - commander -> world（查询 worker sender）
   - commander -> worker（`task:start:*`）
   - worker 执行后 -> commander（`WorkerReport*`）
   - commander -> UI（结果文本）
8. 用户退出 UI 后，`main()` 调用 `shutdown()`
9. `shutdown()` 发送关闭信号，等待各任务收尾

---

## 4. 当前边界与后续建议

### 当前边界

- 这是“可运行编排骨架”，不是最终业务实现
- cognition 与 action 仍是示例级策略
- UI 与 agent 通过文本回报协议衔接（便于快速打通）

### 建议的下一步

- 把 `startup/cognition.rs` 替换成真实 cognition engine
- 把 worker 的文本回报改成结构化协议事件
- 把 UI 的同步等待回报改为异步事件流（支持 streaming）
- 把 world 的 blackboard / extensions 纳入实际编排使用

---

## 5. 运行方式

统一入口启动：

```bash
cargo run -p app
```

---

如果后续你希望，我可以继续补一版：

- “时序图版”流程文档（包含消息字段）
- “模块依赖图”文档（谁依赖谁、谁不该依赖谁）
