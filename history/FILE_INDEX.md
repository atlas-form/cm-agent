# 项目关键文件索引（给 AI 的定点阅读入口）

> 目的：避免每次全仓扫描。先读本文件，再按任务定点打开对应源码。

## 0. 启动必读

- `history/STATE.md`：项目全局状态、模块边界、开发约束
- `protocal/AI_CODING_RULES.md`：AI 编码规则（强约束）
- `protocal/ARCH_HISTORY_REQUIRED.md`：编码前置要求（先读 architectures/history）

## 1. Workspace 与依赖

- `Cargo.toml`：workspace 成员、统一依赖、edition/version 配置
- `Cargo.lock`：依赖锁定结果（通常不作为逻辑入口）

## 1.1 项目入口（`crates/app`）

- `crates/app/README.md`：app 启动流程与模块职责说明
- `crates/app/FLOW.md`：app 启动/消息/关停时序图
- `crates/app/src/main.rs`：项目统一入口（启动 `AppStartup`）
- `crates/app/src/startup/mod.rs`：应用启动编排（world/agent/ui）
- `crates/app/src/startup/world.rs`：world 初始化与通道构建
- `crates/app/src/startup/agent.rs`：commander/worker 启动
- `crates/app/src/startup/ui.rs`：UI 会话桥接与启动
- `crates/app/src/startup/cognition.rs`：启动期示例 cognition

## 2. Agent 核心层（`crates/agent`）

- `crates/agent/src/lib.rs`：crate 导出入口（`commander` / `worker`）
- `crates/agent/src/commander/mod.rs`：commander 模块导出
- `crates/agent/src/commander/commander.rs`：`Commander` 主循环与决策映射
- `crates/agent/src/commander/state.rs`：`CommanderState` / `CommanderPhase`
- `crates/agent/src/commander/task.rs`：`CommanderTask`
- `crates/agent/src/commander/memory.rs`：`TaskMemory`（commander）
- `crates/agent/src/worker/mod.rs`：worker 模块导出
- `crates/agent/src/worker/worker.rs`：`Worker` 主循环与执行阶段
- `crates/agent/src/worker/action_bridge.rs`：cognition 决策到 action 的桥接
- `crates/agent/src/worker/state.rs`：`WorkerState` / `WorkerPhase`
- `crates/agent/src/worker/task.rs`：`Task`
- `crates/agent/src/worker/memory.rs`：`TaskMemory`（worker）
- `crates/agent/examples/commander_worker_tokio.rs`：当前主演示入口（commander-worker 闭环）

## 2.1 Terminal UI 层（`crates/agent-ui`）

- `crates/agent-ui/src/lib.rs`：UI crate 导出入口
- `crates/agent-ui/src/event.rs`：`UiEvent`（用户输入事件解析）
- `crates/agent-ui/src/app.rs`：`TerminalUiApp`（状态与事件循环）
- `crates/agent-ui/src/renderer.rs`：`TerminalRenderer`（终端渲染）
- `crates/agent-ui/src/session.rs`：`AgentSession`（与后端交互接口）
- `crates/agent-ui/examples/repl.rs`：最小人机交互示例

## 3. Core 协议与消息（`crates/core`）

- `crates/core/src/lib.rs`：core 导出入口
- `crates/core/src/protocol/mod.rs`：协议聚合导出
- `crates/core/src/protocol/id.rs`：`AgentId/WorkerId/TaskId/MessageId`
- `crates/core/src/protocol/message.rs`：`Message`
- `crates/core/src/protocol/payload.rs`：`Payload/DecisionIntent/ControlSignal/TaskSpec`
- `crates/core/src/messaging/mod.rs`：`MessageSend/MessageReceive`
- `crates/core/src/messaging/tokio_channel.rs`：Tokio channel 适配实现

## 4. World 注册中心（`crates/world`）

- `crates/world/src/lib.rs`：world 导出入口
- `crates/world/src/runtime.rs`：`WorldRuntime`（world 主运行时容器）
- `crates/world/src/directory.rs`：`EndpointDirectory`（通信端点目录）
- `crates/world/src/blackboard.rs`：`WorldBlackboard`（共享状态）
- `crates/world/src/extensions.rs`：`WorldExtensions`（类型化扩展挂载）
- `crates/world/src/bootstrap.rs`：world 生命周期与兼容 API

## 5. Action 模块（`crates/action`）

- `crates/action/src/lib.rs`：Action 模块导出入口
- `crates/action/src/traits/mod.rs`：核心 trait `Action`
- `crates/action/src/lifecycle/mod.rs`：生命周期状态机 `ActionLifecycle`
- `crates/action/src/input/mod.rs`：输入模型 `ActionInput/ActionType/ActionTarget`
- `crates/action/src/output/mod.rs`：输出模型 `ActionResult/ActionStatus`
- `crates/action/src/examples/simple.rs`：最小可运行 action 示例

## 6. Perception 模块（`crates/perception`）

- `crates/perception/src/lib.rs`：Perception 模块导出入口
- `crates/perception/src/traits.rs`：trait `Perception`
- `crates/perception/src/input/mod.rs`：输入刺激 `Stimulus/Modality/Signal`
- `crates/perception/src/output/mod.rs`：输出 `PerceptionOutput/Percept`
- `crates/perception/src/perceptions/text_utterance.rs`：文本片段聚合感知器

## 7. Cognition 模块（`crates/cognition`）

- `crates/cognition/src/lib.rs`：Cognition 模块导出入口
- `crates/cognition/src/traits.rs`：核心 trait `Cognition::evaluate`
- `crates/cognition/src/input/mod.rs`：输入快照 `CognitionInput`
- `crates/cognition/src/input/intent.rs`：意图模型 `Intent/IntentKind`
- `crates/cognition/src/input/context.rs`：上下文模型 `Context/Fact`
- `crates/cognition/src/output/mod.rs`：结果模型 `CognitionResult/CognitionOutput`
- `crates/cognition/src/output/decision.rs`：决策模型
- `crates/cognition/src/output/confidence.rs`：置信度包装
- `crates/cognition/src/output/rationale.rs`：可审计说明
- `crates/cognition/src/engine/mod.rs`：主执行链（prompt build -> llm -> decode）
- `crates/cognition/src/engine/builder.rs`：`CognitionEngineBuilder`
- `crates/cognition/src/engine/decoder.rs`：`JsonDecoder` 容错解析实现
- `crates/cognition/examples/basic_decision.rs`：结构化决策示例
- `crates/cognition/examples/llm_raw.rs`：原始模型输出调试示例

## 8. LLM 适配层（`crates/agent-llm`）

- `crates/agent-llm/src/lib.rs`：crate 导出入口
- `crates/agent-llm/src/llm/mod.rs`：trait `Llm`
- `crates/agent-llm/src/llm/chat_completions.rs`：`ChatCompletionsLlm` 标准 chat completions 实现
- `crates/agent-llm/src/model/llm.rs`：通用消息输入输出模型
- `crates/agent-llm/src/model/llm.rs`：通用消息输入输出模型
- `crates/agent-llm/src/model/role.rs`：角色枚举 `Role`
- `crates/agent-llm/examples/chat_completions.rs`：最小标准 chat completions 调用示例

## 9. 通用工具层（`crates/agent-utils`）

- `crates/agent-utils/src/lib.rs`：工具模块导出
- `crates/agent-utils/src/prompt/mod.rs`：prompt 加载与渲染
- `crates/agent-utils/src/message/mod.rs`：`MessageBuilder` 与模板消息构建
- `crates/agent-utils/src/logging/mod.rs`：tracing 初始化 + 日志宏

## 10. 统一错误层（`crates/agent-error`）

- `crates/agent-error/src/lib.rs`：统一错误导出入口
- `crates/agent-error/src/error/mod.rs`：顶层 `Error/ErrorKind/Result`
- `crates/agent-error/src/error/cognition.rs`：认知错误与失败原因
- `crates/agent-error/src/error/llm.rs`：LLM 错误
- `crates/agent-error/src/error/perception.rs`：感知错误与失败原因
- `crates/agent-error/src/error/prompt.rs`：Prompt 错误

## 11. Runtime 骨架（`crates/agent-runtime`）

- `crates/agent-runtime/src/lib.rs`：Runtime 结构体与模块导出
- `crates/agent-runtime/src/epoch.rs`：epoch 类型骨架
- `crates/agent-runtime/src/ingress.rs`：输入事件定义骨架
- `crates/agent-runtime/src/buffering.rs`：buffer/action 注册骨架
- `crates/agent-runtime/src/arbitration.rs`：仲裁单元骨架

## 12. Prompt 与设计文档

- `prompts/cognition/decision.md`：当前 cognition 决策模板（结构化 JSON 输出约束）
- `architectures/01_agent.md`：总览文档
- `architectures/commander/`：commander 架构说明
- `architectures/worker/`：worker 架构说明
- `architectures/world/`：world 架构说明

## 13. 常见任务 → 推荐阅读路径

- 跑通当前主演示链路：
  1) `crates/agent/examples/commander_worker_tokio.rs`
  2) `crates/agent/src/commander/commander.rs`
  3) `crates/agent/src/worker/worker.rs`
  4) `crates/world/src/world.rs`
  5) `crates/core/src/protocol/payload.rs`

- 改终端交互体验：
  1) `crates/agent-ui/src/event.rs`
  2) `crates/agent-ui/src/app.rs`
  3) `crates/agent-ui/src/renderer.rs`
  4) `crates/agent-ui/src/session.rs`

- 改项目启动流程：
  1) `crates/app/src/main.rs`
  2) `crates/app/src/startup/mod.rs`
  3) `crates/app/src/startup/world.rs`
  4) `crates/app/src/startup/agent.rs`
  5) `crates/app/src/startup/ui.rs`

- 改 commander 决策映射：
  1) `crates/agent/src/commander/commander.rs`
  2) `crates/cognition/src/output/decision.rs`
  3) `crates/core/src/protocol/payload.rs`

- 改 worker 执行阶段：
  1) `crates/agent/src/worker/worker.rs`
  2) `crates/agent/src/worker/action_bridge.rs`
  3) `crates/action/src/traits/mod.rs`

- 改 cognition 决策逻辑：
  1) `prompts/cognition/decision.md`
  2) `crates/cognition/src/engine/mod.rs`
  3) `crates/cognition/src/engine/decoder.rs`

- 改 prompt 渲染或日志基础设施：
  1) `crates/agent-utils/src/prompt/mod.rs`
  2) `crates/agent-utils/src/message/mod.rs`
  3) `crates/agent-utils/src/logging/mod.rs`

## 维护说明

- 新增核心模块/入口文件后，必须更新本文件。
- 本文件只做“索引与导航”，不替代 `history/STATE.md` 的架构状态描述。
