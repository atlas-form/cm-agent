# Role Phase 5: Role-Skill Bridge

第五阶段只做 role 侧接入 skill 的桥梁，不实现 skill 本身。

## Baseline

已完成：

- roles/commander/task graph 已具备多角色编排、并行、依赖、rework、summary。
- skills runtime 已由其他进程提供 `SkillRegistry`、`SkillExecutorSet`、`SkillRequest`、`SkillExecutionResult`。
- 当前缺口是 worker cognition 只能产出 role report，不能把 role 判断连接到 skill runtime。

## Goal

让 role worker 能以最小协议接入 skill：

```text
Worker Cognition
  -> skill_requests
  -> Rust SkillExecutorSet
  -> skill execution results
  -> WorkerReport evidence / risks / open_questions
  -> Commander evaluation / rework
```

## Scope

本阶段只负责：

- worker prompt 增加 `skill_requests` 协议。
- worker runtime 解析 role 提出的 skill 请求。
- worker runtime 调用已有 `SkillExecutorSet`。
- skill 成功结果追加为 evidence。
- deferred / pending approval / validation / failed 结果追加为 risks 和 open_questions。
- 保持 commander/evaluator 可以用现有质量规则继续判断是否需要 rework。

本阶段不负责：

- 新增或修改 skill 业务实现。
- memory 模块。
- platform 写操作审批流。
- search/LLM/db adapter 的真实接入。
- 按旧 Python pipeline 复制流程。

## Design Rules

- role 只能提出 skill 请求，不能假装 skill 已经完成。
- Rust runtime 是 skill 执行的唯一可信来源。
- successful skill result 进入 evidence。
- incomplete skill result 进入 risks/open_questions，交给 commander evaluator 判断是否重做或等待。
- WorkerReport 结构先保持兼容，不新增强耦合字段；桥梁结果先复用 evidence/risks/open_questions。

## Verification

必须通过：

```bash
cargo test
cargo check --bins
```

建议继续用远程 Ollama 做 role matrix：

```bash
OLLAMA_BASE_URL=http://10.100.11.245:11434 OLLAMA_MODEL=gemma4:26b cargo run --bin ollama_task_graph_smoke -- --matrix
```

## Completion Definition

- `ROLE_PHASE_5_ROLE_SKILL_BRIDGE_STATUS.md` checklist 全部完成。
- worker 能解析 `skill_requests`。
- worker 能执行 native/local skill。
- worker 不把 deferred/pending/failed skill 当成成功 evidence。
- prompt 明确 skill 请求和 runtime 执行边界。
- 全量测试和 bin check 通过。
