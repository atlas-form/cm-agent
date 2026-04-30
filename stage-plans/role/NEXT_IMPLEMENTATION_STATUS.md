# Next Implementation Status

本文跟踪当前阶段计划完成情况。

阶段入口：

- `NEXT_IMPLEMENTATION_PLAN.md`

详细主线：

- `role/ROLE_PHASE_4_ROLE_STATUS.md`

## 当前阶段

当前阶段名称：

```text
Role Phase 4: Role Completion + Old Effect Alignment
```

当前目标：

```text
Role Planning
  -> Role Prompt Boundaries
  -> Role-aware Evaluation
  -> Old Effect Alignment
  -> Role Matrix Regression
```

## 状态总览

```text
status: planned
started: no
completed: no
```

## Checklist

### 1. Planner Stability

- [ ] service 任务优先进入 service role
- [ ] engineering/SLA/接口/性能任务优先进入 engineering role
- [ ] web/SEO/关键词/收录任务优先进入 web role
- [ ] 内容创意任务保持 `ops -> creative`
- [ ] 详情页/预算/运营任务保持 `data -> design/accounting -> ops`
- [ ] planner 回归测试覆盖上述场景

### 2. Evaluator Stability

- [ ] 区分阻塞型 open questions 和非阻塞型 gaps
- [ ] 正常风险说明不误判为拒答
- [ ] SEO/web 术语不误判为外部搜索结果
- [ ] 继续阻止编造工具/数据库/实时搜索结果
- [ ] upstream evidence 检查继续生效

### 3. Prompt Polish

- [ ] chat prompt 边界复核
- [ ] ops prompt 边界复核
- [ ] data prompt 边界复核
- [ ] design prompt 边界复核
- [ ] accounting prompt 边界复核
- [ ] service prompt 边界复核
- [ ] creative prompt 边界复核
- [ ] engineering prompt 边界复核
- [ ] web prompt 边界复核

### 4. Old Effect Alignment

- [ ] 旧 Python role router 效果对照
- [ ] 旧 Python quality checker 效果对照
- [ ] 多 role 互补性检查
- [ ] role 专业差异检查

### 5. Verification

- [ ] `cargo test` 全绿
- [ ] `cargo check --bins` 全绿
- [ ] 远程 Ollama 9 role matrix 通过

## Notes

- 第四阶段只做 role completion 和旧效果对齐。
- 第四阶段参考旧 Python roles/quality 效果，但不照搬旧 pipeline。
- 当前阶段不做 skill/tool/action。
- skill 工作由其他进程处理。
