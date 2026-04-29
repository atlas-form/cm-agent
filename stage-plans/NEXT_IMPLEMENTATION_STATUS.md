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
status: implemented
started: yes
completed: yes
```

## Checklist

### 1. TaskGraph Types

- [x] 定义 `TaskGraph`
- [x] 定义 `TaskNode`
- [x] 定义 `TaskNodeId / TaskGraphId`
- [x] 定义 `WorkerAssignment`
- [x] 定义 `WorkerReport`
- [x] 定义 `WorkerReportStatus`
- [x] 定义 `Evaluation`

### 2. Protocol

- [x] 新增 `Payload::WorkerAssignment`
- [x] 新增 `Payload::WorkerReport`
- [x] 保留旧 Worker report payload 兼容路径
- [x] 更新相关 message 测试

### 3. Planner

- [x] 新增规则版 planner
- [x] 支持 `chat` 单节点
- [x] 支持 `data -> ops`
- [x] 支持 `ops -> creative`
- [x] 支持 `data -> design -> ops`
- [x] 支持 `engineering` 单节点
- [x] 支持 `data -> accounting -> ops`
- [x] 支持至少一个并行分支模板

### 4. Scheduler

- [x] 找出无依赖 ready nodes
- [x] 找出依赖已 passed 的 ready nodes
- [x] 支持多个 ready nodes 并行派发
- [x] 防止同一 worker 同时执行多个 node
- [x] failed 节点的下游默认 skipped

### 5. Worker Runtime

- [x] Worker 接收结构化 `WorkerAssignment`
- [x] Worker cognition context 包含 assignment
- [x] Worker 可读取 upstream reports
- [x] Worker 返回结构化 `WorkerReport`
- [x] 保留旧 `task:start:*` 兼容路径

### 6. Evaluation / Rework

- [x] 实现 deterministic evaluator
- [x] 支持非空输出检查
- [x] 支持 placeholder / failure phrase 检查
- [x] 支持 `open_questions` 缺口判断
- [x] 支持一次返工
- [x] 超过 `max_attempts` 后标记 failed

### 7. Commander Runtime

- [x] Commander 创建 TaskGraph
- [x] Commander 维护 node 状态
- [x] Commander 调度 ready nodes
- [x] Commander 接收 WorkerReport
- [x] Commander 触发 Evaluation
- [x] Commander 触发 Rework
- [x] Commander 推进下游节点
- [x] Commander 判断 TaskGraph 完成

### 8. Final Synthesis

- [x] 只基于 passed reports 汇总
- [x] failed / skipped 节点进入缺口说明
- [x] primary-only / graph-only 输出路径清晰
- [x] 不补充 worker 未提交的数据或外部结果

### 9. SessionEvent

- [x] `TaskGraphPlanned`
- [x] `TaskNodeReady`
- [x] `TaskNodeStarted`
- [x] `TaskNodeReported`
- [x] `TaskNodeEvaluated`
- [x] `TaskNodeReworkRequested`
- [x] `TaskNodePassed`
- [x] `TaskNodeFailed`
- [x] `TaskGraphFinished`

### 10. Tests

- [x] 单节点任务测试
- [x] 串行任务测试
- [x] 并行分支测试
- [x] 返工测试
- [x] 失败降级测试
- [x] `cargo test` 全绿

## Notes

- 本阶段不实现 skill/tool/action。
- 本阶段不引入 LLM planner。
- 本阶段不引入 LLM evaluator。
- 旧 Python 只作为业务经验参考，不作为 runtime 蓝图。
- 已完成第一版 TaskGraph runtime，并补齐严格串行模板、同 worker 并发保护和 `open_questions` 缺口判断。
