# Skill 第二阶段：运行时计划

第一阶段已经把旧 Python 内置 skill 清单迁移为 Rust staged specs，并补了第一版 deterministic/deferred executor。

当前进展：已经新增 `src/skills/runtime.rs`，并通过 `cargo fmt --check`、`cargo check`、`cargo test staged_specs_include_first_port_batch`、`cargo test skills::runtime`。

第二阶段目标是把这些 staged specs 收进真正的 skill runtime 边界，但暂时不接 role planning，也不接 LLM tool-call loop。

## 边界

本阶段要做：

- 定义 Rust skill registry。
- 通过 registry API 暴露 staged specs。
- 通过本地 executor 执行 Rust-native deterministic skill。
- 对 deferred skill 返回明确的 `deferred`、`requires_adapter` 或 `pending_approval` 结果。
- 为 Python worker、search、LLM、DB、平台动作准备 adapter contract。

本阶段不做：

- 不修改 role planning。
- 不修改 commander task graph 行为。
- 不新增 LLM tool-call loop。
- 不执行平台写动作。
- adapter 边界明确前，不调用 Python worker、search、DB 或 LLM。
- 不编辑非 skill 的 stage plan。

## 设计方向

```text
staged_specs()
  -> SkillRegistry
  -> SkillRequest
  -> SkillExecutor
  -> SkillExecutionResult

SkillExecutor
  -> RustNativeExecutor
  -> DeferredExecutor
  -> future PythonWorkerExecutor
  -> future SearchExecutor
  -> future LlmExecutor
  -> future PlatformExecutor
```

Rust 负责：

- registry 和按 skill id 路由。
- request validation。
- timeout 和 cancellation 边界。
- risk classification。
- approval status。
- audit metadata。
- 稳定 JSON result envelope。

Python 只作为可选兼容通道：

- 数据科学依赖重的 skill。
- 需要 Python 库的复杂分析 pipeline。
- 过渡期兼容旧 skill 实现。

## 核心类型

- [x] `SkillId`
- [x] `SkillRequest`
- [x] `SkillExecutionStatus`
- [x] `SkillExecutionResult`
- [x] `SkillRuntimeError`
- [x] `SkillRegistry`
- [x] `SkillExecutor`
- [x] `SkillExecutorSet`

## Registry

- [x] 加载全部 `staged_specs()`。
- [x] 保证 skill id 唯一。
- [x] 支持按 id 查询。
- [x] 支持列出全部 specs。
- [x] 支持按 category 过滤。
- [x] 支持按 tag 过滤。
- [x] 支持按 execution mode 过滤：native / deferred / adapter。
- [x] 补 duplicate id 和 lookup miss 测试。

## Request Validation

- [ ] 校验 request id / skill id。
- [x] 校验 required input fields。
- [x] 校验 JSON object input。
- [x] 返回结构化 validation error。
- [x] 保留未知 optional fields，供后续 adapter 使用。
- [x] 补 missing required fields 测试。

## 本地执行

- [x] 路由 Rust-native `ops` executor。
- [x] 路由 Rust-native `accounting` executor。
- [x] 路由 Rust-native `data` executor。
- [x] 路由已有 struct-based native skills。
- [x] adapter-only specs 返回 `deferred` result。
- [x] unknown id 返回 `not_found`。
- [ ] 每个 native category 至少补一个 executor 测试。

## Deferred 与 Adapter Contract

- [x] 定义 adapter kind enum：`python_worker`、`search`、`llm`、`db`、`platform`。
- [x] 将 P2 / search / platform skill 标记为 deferred，并附 adapter requirement。
- [x] 平台写 skill 标记为 `pending_approval`。
- [ ] 定义未来 Python worker 调用的 JSON envelope。
- [x] 本阶段不执行真实外部进程。

## 风险与审批

- [x] read-only skill 标记为低风险。
- [x] search / LLM skill 标记为中风险。
- [x] platform write / external agent call 标记为高风险。
- [x] result metadata 中包含 approval requirement。
- [x] 高风险动作不执行。

## 验证

- [x] `cargo fmt --check`
- [x] `cargo check`
- [x] registry unit tests。
- [x] executor unit tests。
- [x] deferred / approval unit tests。
- [ ] 现有 role / task graph 测试不受影响。

## 完成标准

第二阶段完成时必须满足：

- 93 个 staged specs 都能通过 `SkillRegistry` 查询。
- Rust-native 第一版 skill 能通过 `SkillExecutor` 执行。
- deferred skill 返回明确结构化结果，不伪装成已执行。
- 平台写 skill 在未来 approval/runtime integration 之前不能执行。
- 不修改 role、commander、memory 或通用 stage-plan 文件。
