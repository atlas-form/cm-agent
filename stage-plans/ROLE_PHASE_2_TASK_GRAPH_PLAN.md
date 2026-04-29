# Role Phase 2: TaskGraph Runtime Plan

当前已完成的是 role 第一阶段：

```text
RoleRoute -> primary worker -> support workers -> final synthesis
```

第二阶段要升级为真正的多智能体任务图 runtime：

```text
RoleRoute -> TaskGraph -> Scheduler -> WorkerReports -> Evaluation -> Synthesis
```

本文只描述未完成的下一步实现。

## 完成定义

完成时必须满足：

- `cargo test` 全绿。
- Commander 能生成规则版 `TaskGraph`。
- Commander 能调度单节点、串行节点和并行分支。
- Worker 使用结构化 assignment/report。
- Commander 能评价 report。
- Commander 能触发一次返工。
- 下游节点能读取上游 report。
- 最终输出只基于 passed report，并说明 failed / skipped 节点。

## Core Types

新增到 `src/agent/commander/task_graph.rs`，除非后续 API 需要公开。

```rust
pub struct TaskGraph {
    pub graph_id: TaskGraphId,
    pub root_task: String,
    pub nodes: Vec<TaskNode>,
}

pub struct TaskNode {
    pub id: TaskNodeId,
    pub role: RoleId,
    pub title: String,
    pub objective: String,
    pub input_refs: Vec<TaskNodeId>,
    pub acceptance: Vec<String>,
    pub max_attempts: u8,
}

pub struct WorkerAssignment {
    pub node_id: TaskNodeId,
    pub worker_id: WorkerId,
    pub role: RoleId,
    pub attempt: u8,
    pub objective: String,
    pub inputs: Vec<WorkerReport>,
    pub rework_instruction: Option<String>,
}

pub struct WorkerReport {
    pub node_id: TaskNodeId,
    pub worker_id: WorkerId,
    pub role: RoleId,
    pub content: String,
    pub evidence: Vec<String>,
    pub open_questions: Vec<String>,
    pub status: WorkerReportStatus,
}

pub struct Evaluation {
    pub node_id: TaskNodeId,
    pub passed: bool,
    pub score: f32,
    pub reasons: Vec<String>,
    pub rework_instruction: Option<String>,
}
```

## Protocol

新增 payload：

```rust
Payload::WorkerAssignment {
    assignment: WorkerAssignment,
}

Payload::WorkerReport {
    report: WorkerReport,
}
```

保留旧 payload 作为过渡兼容：

```rust
Payload::WorkerReportStarted
Payload::WorkerReportFinished
Payload::WorkerReportFailed
```

第一版可以让结构化 report 驱动 TaskGraph，同时继续把 started / finished 映射成现有 `SessionEvent`。

## SessionEvent

新增事件：

```text
TaskGraphPlanned
TaskNodeReady
TaskNodeStarted
TaskNodeReported
TaskNodeEvaluated
TaskNodeReworkRequested
TaskNodePassed
TaskNodeFailed
TaskGraphFinished
```

事件只描述 runtime 状态，不包含 HTTP / SSE 细节。

## Commander Modules

建议新增：

```text
src/agent/commander/task_graph.rs
src/agent/commander/planner.rs
src/agent/commander/scheduler.rs
src/agent/commander/evaluator.rs
src/agent/commander/synthesis.rs
```

职责：

- `planner.rs`：根据用户输入和 `RoleRoute` 生成规则版 `TaskGraph`。
- `scheduler.rs`：找出依赖已通过的 ready nodes，支持并行 dispatch。
- `evaluator.rs`：deterministic evaluation。
- `synthesis.rs`：只基于 passed reports 生成最终输出。

## Planner Rules

第一版不使用 LLM planner。

规则建议：

- 普通问题：`chat`
- 数据分析类：`data -> ops`
- 内容生产类：`ops -> creative`
- 设计转化类：`data -> design -> ops`
- 技术问题：`engineering`
- 预算经营类：`data -> accounting -> ops`
- 复合任务：允许生成分支，例如 `data -> design + accounting`

## Scheduler Rules

- `input_refs` 为空的节点立即 ready。
- 所有依赖节点 passed 后，下游节点 ready。
- 多个 ready nodes 可以并行派发。
- 同一 worker 同一时间只执行一个 node。
- failed 节点的下游默认 skipped。

## Evaluator Rules

第一版 deterministic：

- `content` 非空。
- 不包含明显 placeholder。
- 不包含“无法处理/不知道/无数据”等失败表达，除非节点本身允许说明缺口。
- `open_questions` 为空，或者 synthesis 能明确呈现为缺口。

返工规则：

- 默认 `max_attempts = 2`。
- failed 且 attempt 未耗尽时生成 `rework_instruction`。
- 超过 attempts 后标记 node failed。

## Worker Changes

Worker 输入从裸文本：

```text
task:start:...
```

升级为结构化：

```rust
Payload::WorkerAssignment { assignment }
```

Worker 输出：

```rust
Payload::WorkerReport { report }
```

Worker 必须：

- 使用自己的 `RoleProfile`。
- 使用自己的 role prompt。
- 读取 `assignment.inputs`。
- 只提交本节点产物。
- 不调度其他 worker。

## Tests

必须补这些测试：

1. 单节点任务

输入：

```text
解释一下 agent session 是什么
```

期望：

```text
chat 单节点 -> passed -> final synthesis
```

2. 串行任务

输入：

```text
分析转化率下降并给运营调整方案
```

期望：

```text
data -> ops
ops 能读取 data report
```

3. 并行分支

输入：

```text
基于数据诊断做详情页优化和预算风险评估
```

期望：

```text
data -> design + accounting
design / accounting 可并行
```

4. 返工

模拟 worker 第一次返回空内容。

期望：

```text
evaluation failed -> rework -> 第二次 passed
```

5. 失败降级

模拟 worker 两次失败。

期望：

```text
node failed
下游依赖节点 skipped
final synthesis 说明缺口
```

## 推荐顺序

1. 定义 TaskGraph 类型和 protocol payload。
2. 给 planner / scheduler / evaluator 写纯单元测试。
3. 改 Worker 支持 `WorkerAssignment` 和 `WorkerReport`。
4. 改 Commander 用 TaskGraph 调度单节点。
5. 支持串行依赖。
6. 支持并行 ready nodes。
7. 支持 evaluation failed 后返工一次。
8. 支持 failed / skipped 节点进入 final synthesis。
9. 接入 TaskGraph `SessionEvent`。
