# Python Agent 代码抽取说明

本目录是从下面这个旧 Python 项目中抽取出来的参考代码：

`/Users/ancient/tmp/commerce-agents`

抽取目标：把旧项目中和 agent 有关的代码放到当前 Rust 项目旁边，方便后续分析、拆解和重构。这里的代码只作为行为参考，不代表 Rust 新实现要沿用 Python 的工程结构。

## 抽取范围

已包含的主要部分：

- `server/src/llm_client.py`：LLM 调用、流式输出、tool-call 处理、fallback、熔断等逻辑。
- `server/src/core/`：主聊天管线、多 Agent 协作、角色路由、领域路由、提示词构建、质量检查、上下文和记忆辅助逻辑。
- `server/src/skills/`：技能基类、技能注册器、各角色技能、搜索 provider。
- `server/packages/`：角色、领域、技能 manifest，用于路由和技能注册。
- 部分 `server/src/services/`：任务编排、handoff、skill runtime、action adapter、工具审批、package runtime、记忆、长任务和 autopilot 相关逻辑。
- 部分 `server/src/routes/`：只保留 agent/chat/skills/orchestration/kernel/execution 等入口，方便追踪调用链。
- `introduction/`：和模型接入、协作、技能、项目架构有关的说明文档。
- `tools/`：用于理解 agent 行为的探针、质量检查和运行时测试脚本。

## 刻意不作为重构目标的部分

下面这些旧项目代码不应直接影响 Rust 架构设计：

- 产品、素材、活动、工作区、用户、认证、平台连接等 CRUD/API 模块。
- 前端代码。
- 数据库表结构本身。

其中 `database.py` 和 `models.py` 被保留下来，只是因为很多 agent 相关模块会引用它们，方便后续读代码时不断链。

## 重构原则

这个目录里的 Python 代码只用来回答两个问题：

1. 旧 agent 到底做过哪些事情？
2. 哪些行为值得在 Rust 版本中重新设计实现？

Rust 新实现应该继续遵守当前项目已有的边界：

- `world`
- `agent`
- `cognition`
- `action`
- `agent-llm`
- `agent-runtime`

不要直接照搬 Python 的大管线结构。目标是把旧项目的能力拆成 Rust 风格的模块、状态机、Action 和边界清晰的服务。
