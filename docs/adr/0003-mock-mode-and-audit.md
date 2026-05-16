# ADR 0003 — MOCK_MODE for reproducibility, audit log for observability

**Status:** Accepted
**Date:** 2026-05-16

## Context

Two pain points with LLM-driven agents:

1. **Reproducibility.** Real BGP / RPKI / PeeringDB calls are slow, flaky, and require credentials. CI runs and demos shouldn't need a live lab.
2. **Observability.** When an agent makes the wrong call, you need to be able to ask "what did the AI do and why?" weeks later.

## Decision

- `MOCK_MODE=true` env var routes every tool call to a deterministic fixture under `mocks/`. The same agent code runs against mocks (tests, CI, demos) or real targets (interactive use).
- Every tool invocation writes a structured record to `audit.jsonl` via `tools/audit.py`. Every Claude turn writes token usage + computed USD cost to `telemetry.jsonl` via `tools/telemetry.py`.

## Rationale

**MOCK_MODE design choices:**
- Single env var, no config file — easy to flip in CI
- Mocks live in `mocks/` as committed JSON/text — reviewable as part of the repo
- Each tool function has an explicit `if MOCK_MODE: return load_fixture()` branch — visible in the code, not hidden in a decorator

**Audit log design choices:**
- JSONL format — append-only, line-delimited, friendly to Splunk / Vector / `jq`
- One record per tool call, not one per agent turn — fine-grained for forensics
- Truncate `result_preview` to 4096 chars to keep records bounded
- Include `session_id` so all calls within one agent run can be reconstructed
- `decision` field reserved for HITL gates (`auto` / `approved` / `denied` / `rejected`)

**Cost telemetry design choices:**
- Per-turn token counts, not aggregated — supports cardinality-aware billing analysis
- Cache-read tokens billed at 10% per Anthropic pricing — explicit in the math
- USD cost computed at write time using current rates; rate changes don't retroactively rewrite history

## Trade-offs accepted

- MOCK_MODE drift: mocks can become stale if the real API response shape changes. We mitigate by including a `test_real_api_response_shape.py` we can run manually against real endpoints to catch drift (left as TODO).
- Audit log can grow unbounded. We rely on the operator rotating `audit.jsonl` (logrotate). For larger deployments, replace the file sink with a Vector pipeline → S3.
- USD rates hardcoded in `tools/telemetry.py`. Acceptable for a portfolio lab; in a fleet you'd pull rates from a config service.

## See also

- [Anthropic API pricing](https://www.anthropic.com/pricing)
- [Vector logs pipeline](https://vector.dev/)
