# Next Implementation Status

本文跟踪当前阶段计划完成情况。

阶段入口：

- `NEXT_IMPLEMENTATION_PLAN.md`

详细主线：

- `ROLE_PHASE_3_ROLE_DESIGN_PLAN.md`

## 当前阶段

当前阶段名称：

```text
Role Phase 3: Role Design + Role Contract
```

当前目标：

```text
TaskGraph Runtime
  -> Structured RoleWorkOutput
  -> WorkerReport upgrade
  -> Role-specific Prompts
  -> Role-aware Evaluation
  -> Role-aware Final Synthesis
```

## 状态总览

```text
status: completed
started: yes
completed: yes
```

## Checklist

### 1. Role Output Contract

- [x] 定义 `RoleWorkOutput`
- [x] 明确 `summary / findings / recommendations / evidence / risks / open_questions` 字段语义
- [x] 对照旧 Python quality checker 明确兼容字段

### 2. Protocol

- [x] 扩展 `WorkerReport.role_output`
- [x] 扩展 `WorkerReport.risks`
- [x] 保留 `WorkerReport.content` 兼容路径
- [x] 更新 serde / event 输出兼容性

### 3. Worker Runtime

- [x] Worker 解析 LLM JSON 为 `RoleWorkOutput`
- [x] Worker 将 role output 映射到 `WorkerReport`
- [x] Worker 保留 role prompt 和上游 report context
- [x] Worker 对无法判断的内容写入 open questions / risks
- [x] 保留旧纯文本 fallback

### 4. Prompts

- [x] 更新 `worker_execution.md`
- [x] 更新 `chat` role prompt
- [x] 更新 `ops` role prompt
- [x] 更新 `data` role prompt
- [x] 更新 `design` role prompt
- [x] 更新 `accounting` role prompt
- [x] 更新其余 role prompt 的最低契约

### 5. Evaluator

- [x] 优先评价 `RoleWorkOutput`
- [x] 检查 `summary` 非空
- [x] 检查 role-specific findings / recommendations
- [x] 检查 upstream evidence 引用
- [x] 检查 `open_questions`
- [x] 检查 `risks`
- [x] 防止 role 编造外部数据或工具执行结果

### 6. Final Synthesis

- [x] 汇总 `summary`
- [x] 汇总 `findings`
- [x] 汇总 `recommendations`
- [x] 汇总 `risks`
- [x] 汇总 `open_questions`
- [x] 按 role 展示贡献
- [x] 不编造外部工具或数据结果

### 7. Tests

- [x] RoleWorkOutput 解析测试
- [x] WorkerReport 兼容测试
- [x] role-specific evaluator 测试
- [x] upstream evidence 测试
- [x] risks / open questions 测试
- [x] role-aware synthesis 测试
- [x] Ollama smoke bin 更新
- [x] `cargo test` 全绿
- [x] `cargo check --bins` 全绿

## Notes

- 第三阶段只做 roles 设计和契约化。
- 第三阶段方案必须参考旧 Python 的业务效果，但不照搬旧 pipeline。
- 不新增更多默认 roles。
- 当前阶段不做 skill/tool/action。
