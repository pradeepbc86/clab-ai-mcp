# ADR 0002 — Hostname allowlist, not a full network sandbox

**Status:** Accepted
**Date:** 2026-05-16

## Context

The MCP server runs SSH commands against fabric devices. The LLM supplies the hostname argument, so we have to assume that argument is adversarial (prompt injection, jailbreak, or user-typed mistake).

## Decision

Strict input allowlist: `_validate_host()` rejects anything that isn't a known lab node name (`spine1` / `leaf1` / `leaf2`) or a plain IPv4 dotted-quad. All subprocess calls use the list-arg form (no `shell=True`).

## Rationale

A full network sandbox (seccomp / network namespaces / proxy with domain allowlist) is the right answer for an agent that has open shell access. We're not there: the agent only invokes a small set of named tools with typed parameters. The attack surface is the **parameter validation**, not arbitrary shell execution.

Defense in depth:
1. MCP-level: tool parameter schemas typed via Pydantic
2. Server-level: `_validate_host()` regex + allowlist
3. Subprocess-level: list-arg form (`subprocess.run(["ssh", host, ...])`), no shell
4. Test-level: `tests/test_injection.py` runs 20+ adversarial inputs against the validator

## Trade-offs accepted

- Adding a new lab node requires editing `LAB_NODES` in `mcp_server.py`. We accept this — the alternative (regex on hostnames) widens the attack surface, and lab-node turnover is low.
- If the agent grows to manage a real fleet, allowlist size becomes unwieldy. At that point we'd swap to NetBox-backed allowlist sourcing (the agent queries NetBox for the canonical device list and validates against that).
- We don't sandbox network egress from the agent host. If the agent host is compromised separately (not via the LLM), it can still talk to the internet. That's a host-hardening problem, not an MCP problem.

## See also

- OWASP LLM-01: Prompt Injection
- [Anthropic guidance: building safe agents](https://www.anthropic.com/news/agent-design)
