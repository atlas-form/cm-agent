# Role Phase 3: Role Design Plan

第二阶段已经完成第一版 TaskGraph 多智能体 runtime：

```text
RoleRoute
  -> TaskGraph
  -> Scheduler
  -> WorkerAssignment
  -> WorkerReport
  -> Evaluation / Rework
  -> Final Synthesis
```

第三阶段当前不做 skill。

本阶段核心是 roles 的设计：

- role 的职责边界。
- role 的协作位置。
- role 的输入输出契约。
- role prompt 的专业度。
- role report 的结构化表达。
- Commander 如何评价不同 role 的工作是否完成。

skill/tool/action 以后会接进来，但不属于当前阶段实现范围。

## 与旧 Python 的关系

第三阶段不是照搬 Python pipeline。

但最终效果必须满足旧 Python 里已经验证过的角色体验：

- 用户能感受到不同岗位的专业差异。
- role 之间不是简单重复，而是能形成互补。
- 输出要可执行、可检查、有风险意识。
- 多 role 协作结果要能说明每个角色贡献了什么。
- 当缺数据、缺工具、缺上下文时，role 不能编造结果，必须明确说明缺口。
- 质量检查要阻止空泛、虚构、不可执行的回答通过。

旧 Python 中跟当前阶段最相关的参考：

- `old_code/server/src/core/role_router.py`
- `old_code/server/src/core/multi_agent.py`
- `old_code/server/src/core/quality_checker.py`
- `old_code/server/src/core/chat_pipeline.py`
- `old_code/tools/dispatch_role_skill_matrix_probe.py`
- `old_code/tools/role_skill_quality_probe.py`
- `old_code/tools/full_feature_satisfaction_probe.py`

旧 Python 的 skill 体系只作为未来兼容目标记录，不进入第三阶段开发。

## 阶段目标

第三阶段要把 “Worker + RoleProfile + RolePrompt + WorkerReport” 做成稳定角色层。

目标形态：

```text
TaskGraph Runtime
  -> RoleAssignment
  -> Role-specific Worker Prompt
  -> Structured RoleWorkOutput
  -> Role-aware Evaluation
  -> Role-aware Final Synthesis
```

## 完成定义

完成时必须满足：

- `cargo test` 全绿。
- 9 个默认 role 都有明确职责边界。
- 每个 role prompt 都能表达自己的专业目标、输出要求、风险边界。
- role worker 输出进入统一 `RoleWorkOutput` 结构。
- `WorkerReport` 能携带结构化 role output、evidence、open questions、risks。
- Commander evaluator 能基于 role 类型评价，而不是只看 content 字符串。
- Final synthesis 能按 role 汇总贡献、风险、缺口。
- Ollama smoke 能展示不同 role 的输出差异，而不是多个 worker 写同一种泛泛建议。
- 补旧效果兼容测试：primary/support role 选择、多 role 互补、质量检查。

## RoleWorkOutput

建议新增：

```rust
pub struct RoleWorkOutput {
    pub summary: String,
    pub findings: Vec<String>,
    pub recommendations: Vec<String>,
    pub evidence: Vec<String>,
    pub risks: Vec<String>,
    pub open_questions: Vec<String>,
}
```

字段语义：

- `summary`：本 role 的一句话结论。
- `findings`：本 role 的关键发现。
- `recommendations`：本 role 的建议或可执行动作。
- `evidence`：本 role 使用的输入、上游 report 或明确事实。
- `risks`：本 role 看到的风险、边界、失败条件。
- `open_questions`：阻止进一步判断的缺口。

当前阶段不放 `skill_requests`。

如果 role 发现需要工具或数据，应写入：

```text
open_questions / risks
```

而不是进入 skill 调用。

## WorkerReport Changes

当前：

```rust
pub struct WorkerReport {
    pub content: String,
    pub evidence: Vec<String>,
    pub open_questions: Vec<String>,
    pub status: WorkerReportStatus,
}
```

第三阶段建议升级为：

```rust
pub struct WorkerReport {
    pub content: String,
    pub role_output: Option<RoleWorkOutput>,
    pub evidence: Vec<String>,
    pub open_questions: Vec<String>,
    pub risks: Vec<String>,
    pub status: WorkerReportStatus,
}
```

保留 `content` 作为兼容和最终展示字段。

## Role Prompt Scope

必须更新：

```text
prompts/zh/cognition/worker_execution.md
prompts/zh/roles/chat.md
prompts/zh/roles/ops.md
prompts/zh/roles/data.md
prompts/zh/roles/service.md
prompts/zh/roles/creative.md
prompts/zh/roles/engineering.md
prompts/zh/roles/accounting.md
prompts/zh/roles/design.md
prompts/zh/roles/web.md
```

每个 role prompt 至少要说明：

- 本 role 负责什么。
- 本 role 不负责什么。
- 本 role 应该如何使用上游 report。
- 本 role 必须输出哪些结构化字段。
- 本 role 缺少数据时如何说明缺口。
- 本 role 的风险意识是什么。

## Role-Specific Evaluation

第三阶段 evaluator 不应该只检查：

```text
content 非空
```

而应该按 role 检查。

建议规则：

- `chat`：summary 非空，回答直接，不能过度业务化。
- `data`：findings 至少 2 条；没有真实数据时必须说明口径/数据缺口，不能编造数值。
- `ops`：recommendations 至少 3 条；必须有优先级、节奏或验收口径。
- `design`：必须有视觉/页面/素材层面的建议；不能只写运营策略。
- `accounting`：必须有成本/预算/ROI/风险相关判断。
- `creative`：必须有可直接使用的内容结构、脚本或创意方向。
- `service`：必须有话术、流程或升级路径。
- `engineering`：必须有技术方案、风险、回滚或监控建议。
- `web`：必须有 SEO/页面转化/关键词/监控指标相关建议。

通用规则：

- `summary` 非空。
- `open_questions` 非空时默认需要 rework 或 gap synthesis。
- `risks` 对分析/执行类 role 不应总为空。
- 依赖上游节点的 role 必须在 evidence 中引用上游 report。
- 不允许声称已获取外部数据、调用工具或执行操作。

## Final Synthesis

最终汇总要从“拼 worker 文本”升级为 role-aware synthesis：

- 按 role 展示贡献。
- 汇总共同结论。
- 汇总可执行动作。
- 汇总风险。
- 汇总缺口。
- 明确哪些结论来自上游 report，哪些只是当前 role 判断。

## Not Now

第三阶段不做：

- skill/tool/action 系统。
- `SkillRequest / SkillResult`。
- skill registry。
- tool loop。
- approval workflow。
- 数据库 memory。
- 远程工具调用。
- 完整 marketplace。
- LLM planner。
- LLM evaluator。

这些可以作为第四阶段或后续阶段。

## 建议执行顺序

1. 定义 `RoleWorkOutput`。
2. 扩展 `WorkerReport`，保持 `content` 兼容。
3. 更新 worker 输出解析，把 LLM JSON 映射为 `RoleWorkOutput`。
4. 更新 `worker_execution.md`，要求统一 JSON 输出。
5. 更新 9 个 role prompt。
6. 更新 evaluator，加入 role-specific checks。
7. 更新 synthesis，按 role output 汇总。
8. 增加测试：role output 解析、role-specific evaluator、upstream evidence、risk/open question。
9. 更新 Ollama smoke，让它展示结构化 role 协作效果。
