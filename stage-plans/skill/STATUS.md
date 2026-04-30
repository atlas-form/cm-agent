# Skill Status

## Current State

- Old Python built-in skill inventory has been migrated into staged Rust specs.
- `staged_specs()` currently exposes 93 skill specs.
- The work intentionally does not wire skills into registry, role routing, tool calls, approval flow, or platform actions.

## Verification

- `cargo fmt --check`
- `cargo check`
- `cargo test staged_specs_include_first_port_batch`

## Next Skill-Only Work

- Define a skill registry plan inside this directory before implementation.
- Keep search, DB, LLM, and platform write skills deferred until their runtime adapters and approval boundaries are explicit.
- Do not edit role, memory, commander, or general stage plans from skill work.

