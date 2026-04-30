# Role Phase 4: Role Completion + Old Effect Alignment

第四阶段仍然只做 role，不做 skill。

skill 相关工作由其他进程处理；本目录只维护 roles 的下一阶段计划。

## Current Baseline

第三阶段已经完成 role runtime 主骨架：

```text
Commander
  -> TaskGraph
  -> Role Worker
  -> RoleWorkOutput
  -> Evaluation / Rework
  -> Final Synthesis
```

已验证：

- 9 个默认 role worker 已存在。
- `RoleWorkOutput` 已接入 `WorkerReport`。
- TaskGraph 支持依赖、并行、rework、skip。
- Evaluator 支持 role-specific checks。
- Final synthesis 能按 role 汇总贡献。
- 远程 Ollama `10.100.11.245:11434 gemma4:26b` 已完成 9 role matrix。

## Boundary

本阶段明确不做：

- skill/tool/action。
- skill registry。
- tool loop。
- approval workflow。
- 外部平台写操作。
- 数据库 memory。
- LLM planner。
- LLM evaluator。

## Goals

### 1. Role Planning Stability

- service / engineering / web 这类明确领域任务要优先进入对应 role。
- 避免被 “方案 / 风险 / 标题 / 设计” 等泛词误导。
- 保留数据诊断类多 role graph。
- 保留内容创意类 `ops -> creative` graph。
- 保留详情页/预算/运营类 `data -> design/accounting -> ops` graph。

### 2. Role Evaluator Stabilization

- 区分阻塞型 open questions 和非阻塞型 gaps。
- 减少正常风险说明被误判成拒答。
- 减少 SEO/web 场景被误判为“编造搜索结果”。
- 继续阻止 role 编造外部工具、数据库、实时搜索结果。
- 保持 upstream evidence 检查。

### 3. Role Prompt Polish

- 复核 9 个 role prompt 的职责边界。
- 明确每个 role 如何使用上游 report。
- 保持当前 `RoleWorkOutput` contract 不变。
- 不进入 skill prompt。
- 不把 prompt 写成旧 Python 业务细节大全。

### 4. Old Python Effect Alignment

只对齐 role 效果，不对齐旧 pipeline：

- 不同 role 输出应体现专业差异。
- 多 role 之间应互补，不重复。
- 输出应可执行、可检查。
- 风险和缺口应明确。
- 质量检查应能阻止空泛、虚构、不可执行回答。

参考：

- `old_code/server/src/core/role_router.py`
- `old_code/server/src/core/multi_agent.py`
- `old_code/server/src/core/quality_checker.py`
- `old_code/tools/dispatch_role_skill_matrix_probe.py`
- `old_code/tools/full_feature_satisfaction_probe.py`

## Verification

必须持续通过：

```bash
cargo test
cargo check --bins
OLLAMA_BASE_URL=http://10.100.11.245:11434 OLLAMA_MODEL=gemma4:26b cargo run --bin ollama_task_graph_smoke -- --matrix
```

## Completion Definition

本阶段完成时：

- `ROLE_PHASE_4_ROLE_STATUS.md` 全部勾选。
- 9 role matrix 继续通过。
- planner 对 service / engineering / web / creative / data graph 均有回归测试。
- evaluator 对 open questions、fabrication、upstream evidence 有回归测试。
- 没有新增 skill/tool/action 相关实现。
