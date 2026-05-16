# Threat Model — clab-ai-mcp

STRIDE-style threat model for the LLM-driven network operations agent.

## System overview

```
┌──────────────────────┐
│  Operator (you)      │
└──────────┬───────────┘
           │ stdin / claude-desktop / `python agent.py "..."`
           ▼
┌──────────────────────┐    HTTPS    ┌─────────────────────┐
│  agent.py / Claude   │ ──────────► │  Anthropic API      │
│  Desktop + MCP host  │             └─────────────────────┘
└──────────┬───────────┘
           │ stdio (in-process) or stdio (subprocess)
           ▼
┌──────────────────────┐
│  mcp_server.py       │
│  (FastMCP, this repo)│
└──────────┬───────────┘
           │ Python function calls
           ▼
┌──────────────────────┐    HTTPS    ┌─────────────────────┐
│  tools/              │ ──────────► │ PeeringDB API       │
│  - bgp_tools.py      │             │ Cloudflare RPKI API │
│  - rpki_tools.py     │             └─────────────────────┘
│  - peeringdb_tools.py│
│  - config_tools.py   │    SSH      ┌─────────────────────┐
│  - validation.py     │ ──────────► │ FRR lab nodes       │
└──────────────────────┘             │ (clab containers)   │
                                     └─────────────────────┘
```

## Trust boundaries

| # | Boundary | Crossing | Trust level transition |
|---|---------|----------|------------------------|
| 1 | Operator → MCP host | stdin / stdio | Full trust (operator owns the host) |
| 2 | MCP host → Anthropic API | HTTPS | Trusted (Anthropic Terms + TLS) |
| 3 | Anthropic API → tool_use schemas | structured JSON | **Untrusted output** — model may attempt prompt injection of downstream tools |
| 4 | mcp_server → tools/ | Python calls | Trusted within process |
| 5 | tools/ → fabric SSH | TCP+SSH | Trusted target, untrusted command construction |
| 6 | tools/ → external APIs | HTTPS | Trusted endpoints, untrusted response content |

The critical boundary is **#3** — the LLM's output (`tool_use` block) becomes input to our tool dispatcher. Treat it as adversarial.

## Assets

| Asset | Sensitivity | Notes |
|-------|-------------|-------|
| Fabric device SSH credentials | High | Stored as env vars (`DEVICE_PASSWORD`) — would be Vault/SOPS in production |
| Anthropic API key | High | Per-account billing target; theft = financial damage |
| Audit log (`audit.jsonl`) | Medium | Contains tool inputs/outputs; may leak network topology |
| Telemetry log (`telemetry.jsonl`) | Low | Tokens + cost only |
| Approvals queue (`approvals/`) | Medium | Pending changes — tampering could ship unreviewed configs |
| Mock data (`mocks/`) | None | Public reference data |

## STRIDE analysis

### S — Spoofing

| Threat | Mitigation |
|--------|------------|
| Attacker impersonates the operator to the MCP server | MCP stdio inherits parent-process privileges; rely on OS-level user isolation. **Documented limitation:** no MCP-level auth (see ADR 0002). |
| Attacker spoofs a fabric device to receive SSH credentials | Out of scope — covered by SSH host-key checking (we use `StrictHostKeyChecking=no` for lab; real deployments must verify known_hosts) |
| Attacker spoofs Anthropic API endpoint | TLS + certificate pinning (handled by `anthropic` SDK) |

### T — Tampering

| Threat | Mitigation |
|--------|------------|
| LLM-supplied tool arguments tampered with shell metacharacters | `tools/validation.py` allowlist + `subprocess.run([list])`, never `shell=True`. 23 adversarial inputs covered in `tests/test_injection.py`. |
| Attacker modifies `audit.jsonl` after the fact | Append-only file, OS file permissions. Production would ship to immutable storage (S3 with object lock, Splunk HEC). |
| Tampered `approvals/*.json` to bypass HITL | File mtime + reviewer field tracked. Production would use a signed approval token (HMAC over the diff). |

### R — Repudiation

| Threat | Mitigation |
|--------|------------|
| "I didn't propose that change" / "The agent didn't say that" | Every tool call logged with `session_id` + timestamp. Every Claude response's token usage logged. Reconstruction is possible. |
| Operator denies approving a change | `approvals/*.json` records `reviewer` field; in production this would be SSO-authenticated. |

### I — Information disclosure

| Threat | Mitigation |
|--------|------------|
| LLM exfiltrates fabric topology through prompt injection | Tool outputs are bounded (`result_preview` truncated to 4096 chars); operator visually reviews each model turn. |
| Audit log contains sensitive prefixes / ASNs sent to a future LLM context | Truncation in audit; rotate audit.jsonl with `logrotate`. |
| PeeringDB / RPKI lookups leak our IP to those services | Acceptable — these are public APIs anyone queries. |

### D — Denial of service

| Threat | Mitigation |
|--------|------------|
| Attacker spams tool calls to burn the operator's Anthropic credit | `MAX_TOKENS=1024` per turn caps individual cost. **No rate limit** at the agent loop layer — gap. |
| Attacker triggers infinite tool_use loop | Loop terminates when `stop_reason != "tool_use"`; no max-iteration guard. **Gap.** |
| Subprocess SSH never returns | `timeout=10` on every subprocess call. |
| External API (PeeringDB / RPKI) hangs | `timeout=10` on requests. |

### E — Elevation of privilege

| Threat | Mitigation |
|--------|------------|
| LLM induces tool to run arbitrary code | Tools are explicitly enumerated; tool dispatch is a string-match switch. No `eval()` / `exec()` / shell. |
| MCP server compromise → pivots to fabric | MCP server has SSH access to lab nodes. **Defense in depth:** lab SSH keys should be specific to the MCP host and not reused. Production would use per-tool short-lived credentials (Vault dynamic secrets). |
| Compromised tools/ module → arbitrary Python | Same Python process trust boundary as the operator's shell. No additional isolation. |

## Known gaps (deliberately accepted)

1. **No agent-loop iteration limit** — `while response.stop_reason == "tool_use"` could loop forever. Easy fix: add `max_iterations=20`.
2. **No rate limit on tool calls** — at the loop layer. Anthropic API has its own server-side limits; relying on those.
3. **MCP stdio has no authentication** — see ADR 0002. Acceptable for single-user lab; documented for multi-user.
4. **Approvals file not cryptographically signed** — tampering possible by anyone with FS access. Production needs HMAC + key rotation.

## Out of scope

- Host hardening (assume the host running the agent is patched)
- Network egress filtering (assume HTTPS to Anthropic / PeeringDB / Cloudflare is allowed)
- TLS pinning beyond default `anthropic` SDK behavior
- LLM provider integrity (assume Anthropic itself isn't compromised)
