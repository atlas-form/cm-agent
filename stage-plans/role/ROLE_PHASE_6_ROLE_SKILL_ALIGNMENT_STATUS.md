# Role Phase 6 Status

阶段入口：

- `ROLE_PHASE_6_ROLE_SKILL_ALIGNMENT_PLAN.md`

## 当前阶段

```text
Role Phase 6: Role-Skill Alignment
```

## 状态总览

```text
status: in_progress
started: yes
completed: no
```

## Checklist

### 1. Plan Hygiene

- [x] 删除已完成的 Phase 5 plan/status
- [x] 新建 Phase 6 role-skill alignment plan
- [x] 新建 Phase 6 checklist/status

### 2. Role Skill Catalog

- [x] 新建 role 侧 skill catalog
- [x] 按 role category 收敛 skill 清单
- [x] 为必要跨 role skill 增加显式 allowlist
- [x] chat 仅保留 handoff 类 skill

### 3. Prompt Alignment

- [x] RolePromptBuilder 注入 `available_skills`
- [x] 9 个 role prompt 展示可申请 skill 清单
- [x] prompt 测试覆盖 role skill 清单

### 4. Runtime Guard

- [x] worker 执行 skill 前检查 role allowlist
- [x] 未授权 skill 请求转成 `ValidationError`
- [x] 未授权 skill 不进入 executor
- [x] 未授权结果进入 risks/open_questions

### 5. Verification

- [x] `cargo fmt`
- [x] `cargo test`
- [x] `cargo check --bins`
- [ ] 远程 Ollama role matrix

## Notes

- 本阶段仍不实现 skill 本体。
- 目标是让 role 成为有专属 skill 边界的 agent。
