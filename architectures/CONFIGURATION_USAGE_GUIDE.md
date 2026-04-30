# Configuration Usage Guide

本项目的目标不是让用户通过修改 Rust 代码来适配业务细节，而是把可变的业务策略沉淀到配置和 prompt 文件中。用户提出目标，AI 负责调整配置，用户负责运行场景并验证效果。

## 核心原则

- 用户不需要手动编辑 roles、graph、prompt 文件。
- AI 根据用户的业务目标、测试反馈和旧系统对齐要求修改配置。
- 用户通过真实输入测试结果，判断角色选择、协作顺序、输出质量是否符合预期。
- 业务细节优先通过配置解决，只有配置能力不足时才修改 Rust 代码。
- 如果新增角色、改变角色协作规则、调整角色输出风格还必须改 Rust 代码，说明当前阶段不能冻结。

## 可配置范围

### Roles

配置文件：

- `config/roles.json`
- `config/roles-example.json`

roles 配置定义系统有哪些角色。一个 role 至少包含：

- `id`：稳定角色 ID，例如 `role.ops-strategist`
- `name`：角色展示名，例如 `运营策略师`
- `runtime_role`：运行时角色名，例如 `ops`
- `priority`：路由评分排序参考
- `domains`：适用业务域
- `keywords`：用于轻量路由和任务识别
- `preferred_actions`：角色偏好的动作类型
- `required_capabilities`：角色核心能力
- `optional_capabilities`：可选协作能力

调整 roles 适合解决：

- 新增一个专用角色，例如千川投放专员、私域运营、供应链计划员。
- 修改角色命中关键词。
- 调整角色优先级。
- 调整角色能力边界。
- 让 graph 可以引用新的 `runtime_role`。

注意：`config/roles.json` 是完整角色目录，不是增量扩展文件。启用自定义 roles 时，需要包含系统希望可用的全部角色。

### Task Graph

配置文件：

- `config/task-graph-rules.json`
- `config/task-graph-rules-example.json`

graph 配置定义某类任务应该调用哪些 roles，以及这些 roles 的依赖关系。一个 graph rule 通常包含：

- `id`：规则 ID
- `description`：规则用途说明
- `match_any`：命中任一关键词即可加分
- `match_all_any`：每组至少命中一个关键词才满足
- `nodes`：任务节点列表
- `depends_on`：节点依赖关系，用来决定串行或并行
- `objective`：该节点交给 role 的目标

调整 graph 适合解决：

- 某类任务应该由哪些角色参与。
- 哪些角色可以并行执行。
- 哪些角色必须等待上游结果。
- 最终整合角色是谁。
- 不同业务场景使用不同协作流程。

例如咖啡抖音五一转化任务可以配置为：

```text
data
accounting depends_on=data
creative depends_on=data
ops depends_on=accounting,creative
```

这表示数据诊断先做，预算和创意并行做，运营最后整合。

### Role Prompts

配置目录：

- `prompts/zh/roles/*.md`

每个 role 的 prompt 文件名来自 `runtime_role`：

```text
runtime_role = ops
prompt file = prompts/zh/roles/ops.md
```

role prompt 用来定义：

- 角色身份
- 职责边界
- 禁止事项
- 输出要求
- 如何引用上游 worker 结果
- 如何形成结构化 `role_output`

调整 role prompt 适合解决：

- 某个角色输出太泛。
- 某个角色越界做了其他角色的工作。
- 某个角色没有引用上游结果。
- 某个角色输出格式不稳定。
- 某个角色需要更贴近旧 Python 项目的业务效果。

### Cognition Prompts

配置目录：

- `prompts/zh/cognition/*.md`
- `prompts/en/cognition/*.md`

cognition prompt 用来控制 commander 路由、worker 执行、最终总结等通用推理行为。

调整 cognition prompt 适合解决：

- commander 路由不稳定。
- worker 没有严格返回 JSON。
- 最终总结没有正确综合各角色结果。
- 输出中泄漏了不应该展示的内部上下文。

## 当前运行语义

当前系统不是初始化所有角色 worker。

实际流程是：

```text
用户输入
  -> RoleRouter 和 task graph 做轻量预选
  -> 只初始化本次任务需要的 workers
  -> commander 正式运行
  -> commander 使用 graph 调度这些 workers
  -> worker 输出结果
  -> 评估、必要时 rework
  -> 最终总结
```

这已经避免了角色很多时全量初始化的问题。

当前还不是完全动态的：

```text
只启动 commander
  -> commander 决定 graph
  -> runtime 动态创建 worker
```

如果未来要做到完全 commander-driven worker lifecycle，需要再增加 WorkerFactory/WorkerPool 这类运行时抽象。

## AI 修改配置的工作流

用户提出需求时，AI 应按这个顺序处理：

1. 判断是 role、graph、prompt，还是 Rust runtime 能力问题。
2. 优先修改 `config/*.json` 或 `prompts/**/*.md`。
3. 只有配置无法表达时，才修改 Rust 代码。
4. 修改后用真实任务输入做 smoke test。
5. 对比用户预期或旧项目效果。
6. 如果输出不满意，继续调整配置或 prompt，而不是直接改业务代码。

## 用户验证方式

用户只需要关注这些问题：

- 这次任务有没有选对 roles？
- roles 的执行顺序是否合理？
- 可以并行的角色是否并行了？
- 下游角色是否使用了上游角色结果？
- 输出是否符合业务预期？
- 是否比旧 Python 项目的效果更清晰、更可控？

用户不需要关心：

- Rust 结构体如何组织。
- worker loop 如何启动。
- prompt loader 如何读取文件。
- JSON 解析和校验细节。

## 不能冻结的判断标准

如果出现以下情况，roles/graph/prompt 配置化还没有完成：

- 新增普通业务角色必须修改 Rust 代码。
- 修改角色关键词必须修改 Rust 代码。
- 修改角色协作顺序必须修改 Rust 代码。
- 调整角色职责边界必须修改 Rust 代码。
- 调整角色输出格式必须修改 Rust 代码。
- graph 引用了配置中的 role，但 runtime 无法按需初始化。
- prompt 文件缺失时没有清晰错误信息或测试覆盖。

如果只是新增新的 runtime 机制，例如动态 WorkerPool、跨 session worker 复用、外部服务集成，则可以进入后续阶段处理。

## 推荐冻结标准

roles/graph/prompt 这一层可以冻结的最低标准：

- `config/roles.json` 能完整定义默认角色目录。
- `config/task-graph-rules.json` 能定义常见任务的角色编排。
- `prompts/zh/roles/*.md` 能按 `runtime_role` 覆盖每个角色的业务边界。
- 修改 roles、graph、prompt 后不需要改 Rust 代码即可改变业务行为。
- 测试能验证角色选择、graph 编排、worker 输出、rework 和最终总结。
- 用户真实业务输入能跑出稳定、可解释的多角色协作结果。
