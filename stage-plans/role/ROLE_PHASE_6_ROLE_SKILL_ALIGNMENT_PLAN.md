# Role Phase 6: Role-Skill Alignment

第六阶段继续只做 role 侧工作：把 role 和已存在的 skill runtime 真正对齐起来。

## Baseline

Phase 5 已完成最小桥梁：

- worker cognition 可以输出 `skill_requests`。
- worker runtime 可以调用 `SkillExecutorSet`。
- skill 成功结果进入 evidence。
- deferred / pending / failed 结果进入 risks 和 open_questions。

当前缺口：

- role prompt 还不知道自己有哪些 skill 可申请。
- worker runtime 还没有 role-skill 边界校验。
- role 可能凭模型记忆猜 skill id，导致通用 worker 倾向回潮。

## Goal

让每个 role 成为有明确工具边界的专用 agent：

```text
RoleProfile
  -> RoleSkillCatalog
  -> role prompt available_skills
  -> skill_requests
  -> role-skill allowlist validation
  -> SkillExecutorSet
  -> WorkerReport
```

## Scope

本阶段负责：

- 建立 role 侧 skill catalog。
- 为每个 role 生成可申请 skill 清单。
- 在 role prompt 中注入 `available_skills`。
- worker runtime 执行前校验 skill 是否属于当前 role。
- 非授权 skill 请求转成 validation risk/open_question，而不是执行。
- 保持 `WorkerReport` schema 不变。

本阶段不负责：

- 新增 skill 实现。
- 修改 skill 业务逻辑。
- memory 模块。
- search/db/platform adapter 真实接入。
- 旧 Python pipeline 复刻。

## Design Rules

- role 只能申请自己 catalog 中列出的 skill。
- 同 role category 的 skill 默认允许。
- 少量跨 role skill 可以通过明确 allowlist 允许，例如 web 使用 CTR scorer、creative 使用 SEO scorer。
- chat 默认不做业务 skill，只保留 handoff 类 coordination skill。
- 未授权 skill 不能执行，只能进入 risk/open_question。

## Verification

必须通过：

```bash
cargo test
cargo check --bins
```

建议继续跑远程 Ollama matrix：

```bash
OLLAMA_BASE_URL=http://10.100.11.245:11434 OLLAMA_MODEL=gemma4:26b cargo run --bin ollama_task_graph_smoke -- --matrix
```

## Completion Definition

- `ROLE_PHASE_6_ROLE_SKILL_ALIGNMENT_STATUS.md` checklist 全部完成。
- role prompt 包含专属 `available_skills`。
- worker runtime 拒绝未授权 skill。
- role-skill catalog 有回归测试。
- 现有全量测试通过。
