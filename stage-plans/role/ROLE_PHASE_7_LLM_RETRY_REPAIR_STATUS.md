# Role Phase 7: LLM Retry + Repair Status

## Checklist

- [x] Remove completed Phase 6 role plan/status documents.
- [x] Add `CognitionRetryPolicy`.
- [x] Retry empty LLM output before returning worker-visible failure.
- [x] Repair malformed/schema-invalid JSON before returning worker-visible failure.
- [x] Preserve skill execution boundary after successful cognition.
- [x] Add focused unit tests.
- [x] Run formatting, clippy, tests, and bin checks.

## Notes

This phase is role-owned because it protects role cognition quality before worker action and skill bridging. It does not replace commander-level semantic evaluation or stage rework.
