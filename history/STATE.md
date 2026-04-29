# 项目状态快照（供 AI 启动快速上下文）

- 最后更新: 2026-02-28
- 项目类型: Rust workspace（`resolver = 3`）
- 工作区成员: `crates/*`（`crates/worker`、`crates/commander` 当前在根 `Cargo.toml` 被排除）
- 当前阶段: **Agent 框架已搭好，业务细节未开始**（`agent` crate 中主要是 commander-worker 的演示闭环）

## 1) 一句话理解项目

这是一个以 `agent` 为核心的多 crate 系统：

- `app` 作为项目统一入口（启动终端 UI）；
- `agent` 提供核心运行实体（`Commander` / `Worker`）；
- `agent-ui` 提供终端人机交互层（事件驱动 REPL）；
- `core` 提供统一协议与消息抽象；
- `world` 提供通信注册中心；
- `cognition` / `action` / `perception` 提供能力层；
- `agent-runtime`、`agent-llm`、`agent-utils`、`agent-error` 提供运行时骨架与基础设施。

当前整体仍是“**架构落地前期**”：核心接口与演示链路已存在，但多数细节能力尚未实现。

## 2) 当前真实架构分层

### A. 核心应用层：`crates/agent`

- 已实现两个长期运行实体：
  - `commander`：接收人类任务、调用 cognition、产出 `DecisionIntent`
  - `worker`：接收任务文本、调用 cognition 决策、驱动 action、回报完成/失败
- 二者都采用阻塞循环 + 消息驱动（`run()` 内轮询 inbox）
- 已具备简单内存结构（`TaskMemory`）用于 progress/state/error
- **定位**：现在是框架壳层和流程占位，不是最终业务逻辑实现

### B. 协议与通信层：`crates/core` + `crates/world`

- `core/protocol` 定义统一 `Message`、`Payload`、`AgentId/TaskId/WorkerId`
- `core/messaging` 定义 `MessageSend/MessageReceive`，并提供 `TokioInbox/TokioOutbox`
- `world` 已从“单一静态注册表”升级为可扩展 `WorldRuntime`
  - `EndpointDirectory`：通信端点目录（commander/worker tx）
  - `WorldBlackboard`：共享状态 KV（跨模块协作入口）
  - `WorldExtensions`：类型化扩展挂载（未来能力扩展入口）
  - `bootstrap`：`init_world/install_world/world()` 生命周期与兼容函数
- **定位**：通信基础可用，且具备运行时级扩展能力

### C. 能力层：`cognition` / `action` / `perception`

- `cognition`：结构化评估接口与引擎在，属于当前最完整能力模块之一
- `action`：生命周期模型完整，示例 action 可跑
- `perception`：基础输入输出与文本感知示例存在
- **定位**：通用能力接口齐全，但与 `agent` 主流程尚未深度融合

### D. 运行时层：`crates/agent-runtime`

- 已有 `epoch/ingress/buffering/arbitration` 模块与 `Runtime` 结构体
- 当前多为类型骨架/占位，调度语义尚未实现

### E. 交互层：`crates/agent-ui`

- 按 “AppState + AppEvent + Renderer + Loop” 的方式实现终端交互骨架
- 当前提供 `TerminalUiApp<S: AgentSession>`，可替换 session 对接真实 agent
- 内置 `EchoSession` 作为最小可运行 demo 后端

### F. 应用入口层：`crates/app`

- 提供项目统一可执行入口 `app`
- 当前由 `AppStartup` 统一编排三类启动：
  - `startup/world.rs`：初始化 world 与消息通道
  - `startup/agent.rs`：启动 commander/worker 运行循环
  - `startup/ui.rs`：启动 terminal UI，并通过 `AgentBridgeSession` 下发任务
- 当前通信模型：不使用 router，走 `UI -> Commander -> Worker -> Commander -> UI`
- 启动流程已从“仅 UI”升级为“UI + World + Agent”协同运行

## 3) `agent` 当前 demo 的真实情况

入口：`crates/agent/examples/commander_worker_tokio.rs`

- 使用 Tokio channel + world 注册中心搭建一个最小消息网络
- 启动 `Commander` 和 `Worker` 两个线程化循环
- `router` 任务负责在 commander 与 worker 之间转发（仅该 example 使用）
- `SimpleCommanderCognition` / `SimpleWorkerCognition` 为演示用 cognition
- worker 内 action 由 `decision_to_action` 映射到占位 `DecisionAction`
- 整体实现的是“**任务下发 -> 执行 -> 回报 -> 结束**”的演示链路

结论：该 example 主要用于验证框架流转，不代表业务能力已完成。

## 4) 已实现 vs 未实现（按当前代码）

已实现（可运行框架级）：

- workspace 多 crate 基础拆分
- commander/worker 消息驱动主循环
- core 协议与 tokio 消息封装
- world 可扩展运行时骨架（directory + blackboard + extensions）
- cognition/action/perception 的基础模型与示例
- terminal UI 人机交互骨架（事件解析 + 渲染 + 会话接口）
- app 统一启动入口（`cargo run -p app`）
- `agent` demo 闭环（演示级）

未开始或仅骨架：

- runtime 真实调度语义（epoch、buffering、arbitration 细节）
- action 的业务动作库与执行器编排
- commander 的任务编排策略（当前是最小映射）
- worker 的任务模型与错误恢复策略
- 长期 memory / state 快照 / 可观测性规范化
- 全链路测试与稳定性验证

## 5) 开发边界与约束（仍有效）

- 修改前先读 `architectures/` 与本文件
- cognition 只做判断，不直接执行副作用
- Prompt 放在 `prompts/`，不要硬编码
- LLM 消息构造走 `agent-utils::message::MessageBuilder`
- 错误统一走 `agent-error`
- 关键流程必须有日志
- 结构变化后同步更新 `history/STATE.md` 与 `history/FILE_INDEX.md`

## 6) 常用入口（当前有效）

- 查看工作区：`cargo metadata --no-deps`
- 跑 agent 演示：`cargo run -p agent --example commander_worker_tokio`
- 跑项目统一入口：`cargo run -p app`
- 跑 terminal UI 演示：`cargo run -p agent-ui --example repl`
- 跑 cognition 示例：`cargo run -p cognition --example basic_decision`
- 调试 llm 原始输出：`cargo run -p cognition --example llm_raw`
- 跑标准 chat completions 示例：`cargo run -p agent-llm --example chat_completions`

---

## 维护规则

- 任何 AI 新功能/结构性修改后，必须同步更新本文件。
- 本文件目标是“快速恢复当前真实状态”，描述必须和代码一致，不写计划性表述冒充已实现。
