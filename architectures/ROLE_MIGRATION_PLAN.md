# Role 迁移实现计划

本文说明如何从 `old_code` 迁移 Python agent 的 role 语义。

前提：Rust 骨架已经完成，外部只通过 `cm_agent::api` 使用。

目标不是照搬 Python pipeline，而是把旧项目中的岗位能力、路由规则、协作语义补到 Rust agent 骨架里。

## 当前落地状态

已完成：

- `src/roles` 已建立。
- 内置 9 个 role profile 已接入默认 `AgentManagerConfig`。
- 每个 session 会启动一组短生命 role workers。
- `Commander` 已接入 `RoleRouter`，可以选择 primary/support roles。
- `Worker` 已持有 `RoleProfile`，并在 cognition context 中注入 role 信息和 role prompt。
- `RoleCognitionFactory` 已支持按 role 创建 worker cognition。
- primary role 完成后，Commander 可以调度 support roles。
- Worker cognition 输出会作为 `RoleContribution` 回流给 Commander。
- role 协作过程会输出 `SessionEvent`，web server 可以映射成 SSE。

未做：

- skill/tool 系统。
- 复杂长期 memory。
- 质量重试、trust scorer、proactive engine。
- token 级 role 输出流。
- 复杂最终 synthesis。

## 旧代码判断

Python 旧项目里真正有价值的 agent 内容主要在：

- `old_code/server/packages/roles/*/manifest.yaml`
- `old_code/server/src/core/role_router.py`
- `old_code/server/src/core/prompt_builder.py`
- `old_code/server/src/core/multi_agent.py`
- `old_code/server/src/core/chat_pipeline.py`

其中：

- `role_router.py` 是岗位目录、别名归一、关键词打分、推荐 role。
- `prompt_builder.py` 是按 role 构造系统提示词。
- `multi_agent.py` 是主角色完成后，并发调度 support roles 补充分析。
- `chat_pipeline.py` 是旧 Python web pipeline，大部分不应该照搬。

旧代码不能算真正 agent runtime，但它提供了 role 业务语义。

## 新目录

需要新增：

```text
src/roles/
  lib.rs
  profile.rs
  catalog.rs
  router.rs
  prompt.rs
  collaboration.rs
```

职责：

- `profile.rs`：定义 `RoleId` / `RoleProfile` / `RoleAction`。
- `catalog.rs`：加载内置 roles。
- `router.rs`：根据用户输入选择 primary role / support roles。
- `prompt.rs`：根据 role profile 构造短 prompt。
- `collaboration.rs`：定义多 role 协作计划，不直接执行 LLM。

不要放到 `agent_manager` 或 `agent_session` 里。

理由：

- Role 是业务语义层。
- Manager 是多用户管理器。
- SessionRuntime 是执行器。
- Commander 才使用 RoleRouter 做决策。

## 第一阶段 RoleProfile

先把旧 manifest 变成 Rust 内置 role profiles。

旧代码已有 6 个 manifest：

```text
ops          运营策略师
data         数据分析师
service      客户成功专员
creative     内容创意师
engineering  技术架构师
accounting   财务分析师
```

旧代码里还隐含了：

```text
design
web
```

但这两个没有 manifest，第一阶段可以作为 fallback role profile 补齐。

Rust 新增：

```text
chat         对话助手
```

`chat` 用来承接普通问答、解释、概念说明、闲聊、澄清类输入。

它不是 skill，也不是工具执行角色。

RoleProfile 最小字段：

```rust
pub struct RoleProfile {
    pub id: RoleId,
    pub name: String,
    pub runtime_role: String,
    pub priority: u32,
    pub domains: Vec<String>,
    pub keywords: Vec<String>,
    pub preferred_actions: Vec<RoleAction>,
    pub required_capabilities: Vec<String>,
    pub optional_capabilities: Vec<String>,
}
```

第一阶段建议使用代码内置常量，不急着做外部 yaml/json 加载。

## 第二阶段 RoleRouter

从 Python `suggest_roles(...)` 迁移最小可用版本。

输入：

```rust
pub struct RoleRouteInput {
    pub message: String,
    pub domain_id: Option<String>,
    pub action: Option<RoleAction>,
    pub max_roles: usize,
}
```

输出：

```rust
pub struct RoleRoute {
    pub primary_role: RoleId,
    pub support_roles: Vec<RoleId>,
    pub scores: Vec<RoleScore>,
}
```

第一版规则：

- keyword 命中加分
- preferred_actions 命中加分
- domain 命中加分
- priority 用于同分排序
- 默认 primary role 是 `chat`
- support roles 是 primary 之后的高分角色

不要一开始迁移 Python 里所有特殊 keyword 规则。

## 第三阶段 Commander 接入 RoleRouter

当前 Commander 只知道 worker profile。

下一步改成：

```text
Commander 收到 HumanCommand
  -> RoleRouter 选 primary_role / support_roles
  -> primary_role 生成主任务
  -> 派发给对应 Worker
```

当前已经不只派发 primary role。

primary role 完成后，Commander 会按 `RoleRoute` 里的 support roles 派发补充任务。

## 第四阶段 Worker Role 化

当前只有一个 `worker-1`。

后续 WorkerProfile 应该来自 RoleProfile：

```text
worker.ops
worker.data
worker.service
worker.creative
worker.engineering
worker.accounting
worker.design
worker.web
worker.chat
```

SessionRuntime 启动时，根据 RoleCatalog 注册多个 worker profile。

每个 Worker 仍然是 session 内短生命 Worker。

## 第五阶段 Role Prompt

不要直接照搬 Python `prompt_builder.py` 的长 prompt。

按 Rust 结构重写短 prompt，但 prompt 文案必须放在 md 文件里：

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

Rust 代码不能硬编码大段 prompt。

`src/roles/prompt.rs` 只负责：

- 根据 `runtime_role` 选择 md 文件
- 使用 `agent_utils::prompt::Prompt::load_from_repo(...)` 加载
- 使用 `Prompt::render(...)` 渲染变量
- 返回渲染后的 prompt

允许的模板变量包括：

```text
{{role_name}}
{{runtime_role}}
{{required_capabilities}}
{{optional_capabilities}}
{{preferred_actions}}
{{task}}
{{facts}}
```

旧 prompt_builder 只作为素材，不作为最终实现。

如果以后新增 role，必须同时新增对应 md prompt 文件。

## 第六阶段 Support Roles 协作

旧 Python `multi_agent.py` 的核心语义已经保留第一版：

```text
primary role 先完成
support roles 并发补充
最后合并贡献
```

Rust 里由 Commander 编排：

```text
Commander
  -> dispatch primary Worker
  -> collect primary output
  -> dispatch support Workers concurrently
  -> collect role contribution
  -> synthesize final output
```

对应事件：

```text
collaboration_started
collaboration_worker_started
collaboration_worker_finished
collaboration_finished
```

这些事件已经扩展到 `SessionEvent`。

## 暂时不做

第一轮不要做：

- skill 系统
- tool_use 循环
- 数据库 memory
- 质量重试
- trust scorer
- proactive engine
- prompt injectors 全量迁移
- Python chat_pipeline 大函数迁移

这些属于后续血肉，不是 role 骨架。

## 推荐实现顺序

### Phase A：默认角色初始化

这个项目不是通用 agent 框架，而是带默认业务角色的 agent library。

外部 web server 不应该手动注册内置角色。

第一步先实现：

1. 新增 `src/roles`。
2. 写 `RoleProfile` / `RoleCatalog`。
3. 内置 9 个 role profile。
4. `AgentManagerConfig` 默认持有 `RoleCatalog::builtin()`。
5. `AgentManager` 创建 session 时，把 `RoleCatalog` 转成 `WorkerProfile`。
6. `SessionRuntime` 使用这些 worker profiles 初始化 `SessionContext.worker_catalog`。

外部使用保持简单：

```rust
let manager = AgentManager::new(config);
```

或者后续提供：

```rust
let manager = AgentManager::default_with_ollama(...);
```

### Phase B：角色路由

1. 写 `RoleRouter::route(...)`。
2. 给 RoleRouter 写单元测试。
3. Commander 接入 RoleRouter，但先只派发 primary role。

### Phase C：多 role worker 与协作

1. SessionRuntime 为每个 role 注册 worker profile。
2. 后续把单 worker loop 扩展成多 role worker loop。
3. support roles 并发协作。
4. 扩展 `SessionEvent` 的 collab 事件。

## 核心原则

Role 是业务语义，不是 runtime。

RoleRouter 只推荐角色，不执行任务。

Commander 才根据 role route 做调度。

Worker 只执行被分配的 role task。

Python 旧代码只提供语义素材，不决定 Rust 架构。
