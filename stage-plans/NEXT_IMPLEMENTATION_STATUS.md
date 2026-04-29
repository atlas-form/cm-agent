# Next Implementation Status

本文跟踪当前阶段计划完成情况。

阶段入口：

- `NEXT_IMPLEMENTATION_PLAN.md`

详细主线：

- `ROLE_PHASE_2_TASK_GRAPH_PLAN.md`

## 当前阶段

当前阶段名称：

```text
Role Phase 2: TaskGraph Multi-Agent Runtime
```

当前目标：

```text
RoleRoute
  -> TaskGraph
  -> Scheduler
  -> WorkerAssignment
  -> WorkerReport
  -> Evaluation / Rework
  -> Final Synthesis
```

## 状态总览

```text
status: planned
started: no
completed: no
```

## Checklist

### 1. TaskGraph Types

- [ ] 定义 `TaskGraph`
- [ ] 定义 `TaskNode`
- [ ] 定义 `TaskNodeId / TaskGraphId`
- [ ] 定义 `WorkerAssignment`
- [ ] 定义 `WorkerReport`
- [ ] 定义 `WorkerReportStatus`
- [ ] 定义 `Evaluation`

### 2. Protocol

- [ ] 新增 `Payload::WorkerAssignment`
- [ ] 新增 `Payload::WorkerReport`
- [ ] 保留旧 Worker report payload 兼容路径
- [ ] 更新相关 message 测试

### 3. Planner

- [ ] 新增规则版 planner
- [ ] 支持 `chat` 单节点
- [ ] 支持 `data -> ops`
- [ ] 支持 `ops -> creative`
- [ ] 支持 `data -> design -> ops`
- [ ] 支持 `engineering` 单节点
- [ ] 支持 `data -> accounting -> ops`
- [ ] 支持至少一个并行分支模板

### 4. Scheduler

- [ ] 找出无依赖 ready nodes
- [ ] 找出依赖已 passed 的 ready nodes
- [ ] 支持多个 ready nodes 并行派发
- [ ] 防止同一 worker 同时执行多个 node
- [ ] failed 节点的下游默认 skipped

### 5. Worker Runtime

- [ ] Worker 接收结构化 `WorkerAssignment`
- [ ] Worker cognition context 包含 assignment
- [ ] Worker 可读取 upstream reports
- [ ] Worker 返回结构化 `WorkerReport`
- [ ] 保留旧 `task:start:*` 兼容路径

### 6. Evaluation / Rework

- [ ] 实现 deterministic evaluator
- [ ] 支持非空输出检查
- [ ] 支持 placeholder / failure phrase 检查
- [ ] 支持 `open_questions` 缺口判断
- [ ] 支持一次返工
- [ ] 超过 `max_attempts` 后标记 failed

### 7. Commander Runtime

- [ ] Commander 创建 TaskGraph
- [ ] Commander 维护 node 状态
- [ ] Commander 调度 ready nodes
- [ ] Commander 接收 WorkerReport
- [ ] Commander 触发 Evaluation
- [ ] Commander 触发 Rework
- [ ] Commander 推进下游节点
- [ ] Commander 判断 TaskGraph 完成

### 8. Final Synthesis

- [ ] 只基于 passed reports 汇总
- [ ] failed / skipped 节点进入缺口说明
- [ ] primary-only / graph-only 输出路径清晰
- [ ] 不补充 worker 未提交的数据或外部结果

### 9. SessionEvent

- [ ] `TaskGraphPlanned`
- [ ] `TaskNodeReady`
- [ ] `TaskNodeStarted`
- [ ] `TaskNodeReported`
- [ ] `TaskNodeEvaluated`
- [ ] `TaskNodeReworkRequested`
- [ ] `TaskNodePassed`
- [ ] `TaskNodeFailed`
- [ ] `TaskGraphFinished`

### 10. Tests

- [ ] 单节点任务测试
- [ ] 串行任务测试
- [ ] 并行分支测试
- [ ] 返工测试
- [ ] 失败降级测试
- [ ] `cargo test` 全绿

## Notes

- 本阶段不实现 skill/tool/action。
- 本阶段不引入 LLM planner。
- 本阶段不引入 LLM evaluator。
- 旧 Python 只作为业务经验参考，不作为 runtime 蓝图。
