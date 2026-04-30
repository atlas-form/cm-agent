# Role Phase 7: LLM Retry + Repair Policy

## Goal

Add a central retry and repair policy before worker reports and before skill execution.

This phase handles unstable raw LLM output:

- empty response
- malformed JSON
- truncated JSON
- schema drift against the role prompt template
- transient LLM request error

Semantic rework remains a commander/evaluator concern. Phase 7 only guarantees that role cognition either returns stable structured output or fails with attempt evidence.

## Scope

- Add a default retry policy to `CognitionEngine`.
- Retry up to 3 total LLM attempts by default.
- Use repair prompts after decode/schema failures.
- Keep skill execution downstream of successful cognition only.
- Add unit tests for empty output retry, decode repair, and final failure.
- Do not change API, CRUD, web server, memory, or skill internals.

## Design

`CognitionEngine` owns the raw-output reliability boundary:

1. Render the original cognition input once.
2. Call the LLM.
3. If output is empty, retry with a strict JSON-only instruction.
4. If output cannot be decoded, ask the LLM to repair the previous answer into valid JSON.
5. Return `CognitionResult::Success` only after the decoder accepts the output.
6. Return `CognitionResult::Failure` after attempts are exhausted, including attempt count and last failure detail.

This keeps workers simple: a worker should not execute skills or report completion from unstable cognition output.

## Non-goals

- No semantic quality loop in this phase.
- No role prompt refinement in this phase.
- No skill planning or skill execution changes.
- No memory changes.
