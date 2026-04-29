# Agent 协同机制总结

## 1. 总体结论

这个项目的 Agent 协同是一个“**主 Agent 先回答 + 支持 Agent 补充**”的流式编排模型，核心由 `chat_pipeline` 驱动，`multi_agent` 负责支持角色调度。

- 主入口：`POST /api/chat/stream`
- 主编排：`server/src/core/chat_pipeline.py`
- 协同调度：`server/src/core/multi_agent.py`
- 角色路由：`server/src/core/intent.py`
- 技能注册：`server/src/skills/registry.py`

## 2. 协同流程（端到端）

一次多 Agent 协作的实际链路如下：

1. 用户消息进入 `chat_stream`。
2. `intent.analyze_intent()` 先决定：
   - 主角色 `primary_role`
   - 支持角色 `support_roles`
   - 复杂度层级 `tier`（quick/single/multi）
3. 主 Agent 执行完整流程：
   - 选特性（feature budget）
   - 加载上下文（历史/记忆/信任/产品）
   - 构建 prompt
   - 可选 tool use 循环
4. 主 Agent 输出后，进入协同判定：
   - `tier == TIER_MULTI` 且有支持角色，或
   - 从主回复里再次识别到额外角色信号（动态扩展）
5. 满足条件时，调用 `dispatch_support_agents()`：
   - 给每个支持 Agent 注入“用户问题 + 主 Agent 回复摘要”
   - 要求“只补充不重复”
   - 逐个流式输出补充内容
6. 协作结果回到主流，最终保存：
   - 主回复 + 支持回复拼接入同一条 assistant 消息
   - metadata 记录 support_roles、contributions 等调度信息

## 3. 角色怎么被选出来

### 3.1 初始路由（规则引擎）

`intent.py` 使用关键词打分，不走 LLM：

- `primary_role`：最高分角色
- `support_roles`：次高分角色（最多 3 个）
- 若没有支持角色，会按 `ROLE_AFFINITY` 补一个默认协作角色

### 3.2 二次扩展（主回复信号）

主 Agent 回答后，`_detect_needed_roles_from_reply()` 会扫描回复中的角色信号词（如“漏斗/成本核算/主图”等），可追加支持角色（总数仍受上限约束）。

这让系统从“只看用户提问”升级到“也看主 Agent 判断结果”。

## 4. 支持 Agent 如何执行

`multi_agent._run_support_agent_stream()` 的关键行为：

- 会构建支持角色专属 system prompt（同样加载记忆/信任/平台/产品上下文）
- 注入主 Agent 回复作为上下文（截断后输入）
- 明确要求“不要重复主 Agent 内容”
- 可使用工具，但支持 Agent 工具轮次被限制为 `min(MAX_TOOL_ROUNDS, 1)`（最多 1 轮）

输出通过 SSE 事件持续推给前端：

- `collab_start`
- `collab_agent_start`
- `collab_token`
- `collab_agent_done`
- `collab_done`

## 5. 工具和协同 Skill 的关系

技能注册中心 `get_tools_for_role()` 的规则是：

- 当前角色技能 + `coordination` 技能（始终可用）

这意味着每个 Agent 都能调用协同类工具：

- `coordination_agent_handoff`
- `coordination_task_orchestration`
- `coordination_status_query`

这些更偏“协同辅助决策工具”，真正的多 Agent 编排仍由 `chat_pipeline + multi_agent` 主流程控制。

## 6. 前端如何感知协作

前端通过 `client/src/core/sse-event-router.js` 识别协同事件并分发到 UI。  
因此用户能看到：

- 主 Agent 阶段（intent、tools、primary_done）
- 支持 Agent 启动/输出/完成
- 协作汇总结果

## 7. 关键开关与约束

### 7.1 运行开关（`server/src/config.py`）

- `ENABLE_HANDOFF=1`：是否启用多 Agent 协作调度
- `ENABLE_TOOL_USE=1`：是否允许工具调用
- `MAX_TOOL_ROUNDS`：主 Agent 工具循环上限

### 7.2 关键约束

- 支持角色最多 3 个（路由上限）
- 若主回复是“追问用户信息”（`_is_asking_user`），会跳过多 Agent 协作
- 支持 Agent 当前是**顺序执行**（代码注释明确为了 SSE 顺序清晰），不是并发 fan-out

## 8. 当前实现里的注意点

1. `server/config/skill_chains.json` 目前更像独立配置资产，未直接接入主协同管道。
2. `coordination_status_query` 返回的是静态状态模板，不是实时系统监控。
3. 协同稳定性主要依赖规则路由 + 主回复信号词，优点是快，缺点是语义覆盖有限。

## 9. 关键代码定位

- `server/src/routes/chat.py`：SSE 入口与事件输出
- `server/src/core/chat_pipeline.py`：主流程编排（含协同触发与结果合并）
- `server/src/core/multi_agent.py`：支持 Agent 调度与流式输出
- `server/src/core/intent.py`：主/支持角色路由与 tier 判定
- `server/src/skills/registry.py`：角色工具集构建（含 coordination 常驻）
- `server/src/skills/coordination.py`：协同类技能实现
- `client/src/core/sse-event-router.js`：协同事件前端路由
