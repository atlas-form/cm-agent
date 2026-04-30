# Role Phase 4 Status

阶段入口：

- `ROLE_PHASE_4_ROLE_COMPLETION_PLAN.md`

## 当前阶段

```text
Role Phase 4: Role Completion + Old Effect Alignment
```

## 状态总览

```text
status: completed
started: yes
completed: yes
```

## Checklist

### 1. Planner Stability

- [x] service 任务优先进入 service role
- [x] engineering/SLA/接口/性能任务优先进入 engineering role
- [x] web/SEO/关键词/收录任务优先进入 web role
- [x] 内容创意任务保持 `ops -> creative`
- [x] 详情页/预算/运营任务保持 `data -> design/accounting -> ops`
- [x] planner 回归测试覆盖上述场景

### 2. Evaluator Stability

- [x] 区分阻塞型 open questions 和非阻塞型 gaps
- [x] 正常风险说明不误判为拒答
- [x] SEO/web 术语不误判为外部搜索结果
- [x] 继续阻止编造工具/数据库/实时搜索结果
- [x] upstream evidence 检查继续生效

### 3. Prompt Polish

- [x] chat prompt 边界复核
- [x] ops prompt 边界复核
- [x] data prompt 边界复核
- [x] design prompt 边界复核
- [x] accounting prompt 边界复核
- [x] service prompt 边界复核
- [x] creative prompt 边界复核
- [x] engineering prompt 边界复核
- [x] web prompt 边界复核

### 4. Old Effect Alignment

- [x] 旧 Python role router 效果对照
- [x] 旧 Python quality checker 效果对照
- [x] 多 role 互补性检查
- [x] role 专业差异检查

### 5. Verification

- [x] `cargo test` 全绿
- [x] `cargo check --bins` 全绿
- [x] 远程 Ollama 9 role matrix 通过

## Notes

- 本阶段只做 role。
- 不做 skill/tool/action。
- skill 工作由其他进程处理。
