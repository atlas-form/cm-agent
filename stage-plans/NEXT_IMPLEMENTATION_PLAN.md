# Next Implementation Plan

本文是当前唯一的下一步执行入口。

`../architectures/00_FINAL_DESIGN.md` 是长期设计定稿，不在这里重复。

已完成的历史计划已经删除，不再保留：

- 基础 `AgentManager -> AgentSession -> SessionRuntime` 重构
- `SessionContext` 替代 `World`
- 默认 9 个 role workers
- `RoleRouter`
- role prompt markdown
- primary/support collaboration
- 基础 `SessionEvent` stream

## 当前状态

当前可运行形态：

```text
AgentManager
  -> AgentSession
      -> SessionRuntime
          -> SessionContext
          -> Commander
          -> role workers
```

当前协作形态：

```text
RoleRoute
  -> TaskGraph
  -> Scheduler
  -> WorkerAssignment
  -> WorkerReport
  -> Evaluation / Rework
  -> Final Synthesis
```

下一阶段目标：

```text
TaskGraph Runtime
  -> Structured RoleWorkOutput
  -> WorkerReport upgrade
  -> Role-specific Prompts
  -> Role-aware Evaluation
  -> Role-aware Final Synthesis
```

## Priority 1: Role Design + Role Contract

详细计划见：

- `ROLE_PHASE_3_ROLE_DESIGN_PLAN.md`

这是第三阶段主线。

先做：

1. 定义 `RoleWorkOutput`。
2. 扩展 `WorkerReport`。
3. 修改 worker 输出解析。
4. 修改 role prompts 和 worker cognition prompt。
5. 改 evaluator，加入 role-specific checks。
6. 改 final synthesis，展示 role 贡献、建议、风险和缺口。
7. 补结构化输出、上游证据、角色差异和质量检查测试。

完成后，再进入 skill/tool/action 阶段。

## Priority 2: Streaming Follow-up

详细计划见：

- `../architectures/SSE_STREAMING_PLAN.md`

需要做：

1. `AgentManager::cancel_session(session_id)`。
2. active session registry 保存可取消 handle。
3. stream client 断开后可取消 runtime。
4. 接入真实 LLM token stream 到 `SessionEvent::LlmChunk`。
5. 补慢消费者和关键事件不丢失测试。

## Priority 3: Manager Lifecycle

当前 `AgentManager` 已能创建、注册、运行、回收短生命 session。

后续要补：

- active session 查找返回真实 handle。
- TTL keep-alive。
- session restore。
- 长任务后台状态查询。

这些应在 role contract 稳定后再做，避免 manager 先承载过多未定行为。

## Priority 4: Naming Cleanup

不影响功能，但建议在下一轮顺手清理：

- `Commander.world_id` 改成 `host_id` 或 `reply_to`。
- `"external-host"` 命名统一成明确的 host/system agent id。
- 文档中避免再使用 `World` 作为当前概念。

## Not Now

下一阶段仍然不做：

- skill/tool/action 系统
- 完整 skill marketplace
- 数据库 memory
- approval workflow
- distributed scheduler
- web server
- 前端事件协议
- LLM planner
- LLM evaluator
