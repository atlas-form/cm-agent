# Skill 状态

## 当前状态

- 旧 Python 内置 skill 清单已迁移为 Rust staged specs。
- `staged_specs()` 当前暴露 93 个 skill spec。
- 当前代码仍然刻意不接 registry、role routing、tool call、approval flow 或平台动作。
- 第二阶段运行时实现已进入计划阶段。

## 已验证

- `cargo fmt --check`
- `cargo check`
- `cargo test staged_specs_include_first_port_batch`

## 下一步 Skill 工作

- 按 `SKILL_PHASE_2_RUNTIME_PLAN.md` 执行。
- 先实现 registry 和本地 executor，再考虑接 role/tool loop。
- search、DB、LLM、Python worker、平台写动作必须保持清晰 adapter 边界。
- 做 skill 工作时，不修改 role、memory、commander 或通用 stage plan。
