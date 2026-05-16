# clab-ai-mcp

Claude API + MCP-Server driven network operations agent. Queries BGP state, validates RPKI, looks up PeeringDB info, and generates multi-vendor configs. Includes a mock-mode for running the agent without a live lab.

## Architecture

```
User query
    ↓
agent.py (Claude Sonnet 4.6 + tool_use loop)
    ↓
mcp_server.py (FastMCP, stdio transport)
    ↓
tools/
    ├── bgp_tools.py         → Netmiko SSH → FRR vtysh
    ├── rpki_tools.py        → Cloudflare RPKI API
    ├── peeringdb_tools.py   → PeeringDB REST API
    └── config_tools.py      → Jinja2 render (FRR/Juniper/Arista)

MOCK_MODE=true → reads from mocks/ instead of hitting SSH/APIs
```

## Tools

| Tool | Purpose |
|------|---------|
| **ContainerLab** | 3-node FRR lab (agent targets) |
| **FRRouting** | BGP control plane in lab containers |
| **Claude API** | LLM agent brain (`claude-sonnet-4-6`) |
| **MCP-Server** | Network tools as Claude tools (FastMCP, stdio) |
| **Netmiko** | SSH into devices, run vtysh commands |
| **Jinja2** | Multi-vendor config templating |
| **PeeringDB** | ASN/IX/peer info lookup |
| **RPKI** | BGP prefix validation (Cloudflare API) |
| **GitLab CI/CD** | Ruff lint + pytest |

## Demo Scenarios

```bash
# Standalone agent (requires ANTHROPIC_API_KEY)
python agent.py "Check BGP neighbors on leaf1"
python agent.py "Look up ASN 13335 on PeeringDB"
python agent.py "Is 1.1.1.0/24 from AS 13335 RPKI valid?"
python agent.py "Generate a Juniper BGP peer config"

# MCP Server for Claude Desktop
python mcp_server.py
# Then in Claude Desktop, the agent can use all network tools

# Run without a real lab (uses mocks/)
MOCK_MODE=true python agent.py "Check BGP on leaf1"
```

## Claude Desktop Setup

```json
{
  "mcpServers": {
    "network-ops": {
      "command": "python",
      "args": ["/path/to/clab-ai-mcp/mcp_server.py"]
    }
  }
}
```

## Files

- `mcp_server.py` — FastMCP server exposing network tools
- `agent.py` — Standalone Claude API agent loop
- `tools/` — bgp_tools, rpki_tools, peeringdb_tools, config_tools
- `templates/` — Jinja2 BGP peer templates (FRR, Juniper, Arista)
- `mocks/` — Sample JSON/text responses for MOCK_MODE
- `tests/` — pytest unit tests (host validation, tool dispatch, template render)
- `topology/` — 3-node FRR lab for testing
- `.gitlab-ci.yml` — Ruff lint + pytest stages
- `pyproject.toml` — Dependencies (anthropic, mcp, netmiko, jinja2, requests)

## Prerequisites

- Docker & ContainerLab
- Python 3.9+
- `ANTHROPIC_API_KEY` set in `.env` (or `MOCK_MODE=true` to skip)
- FRRouting Docker image: `frrouting/frr:9.1.0`

## Quick Start

```bash
# Deploy lab
make deploy

# Run agent against real lab
python agent.py "check BGP on spine1"

# Or run without lab using mock data
MOCK_MODE=true python agent.py "check BGP on spine1"

# Start MCP server (for Claude Desktop)
python mcp_server.py

# Run tests
pytest tests/ -v
```

## Tied to other repos

This project is the "crown jewel" — the LLM-driven control plane sits on top of the patterns built in:

- **`clab-fabric-evpn`** — the BGP/EVPN fabric the agent observes
- **`clab-automation`** — the Netbox SoT / config templates the agent can reason about
- **`clab-observability`** — the telemetry pipeline the agent's Airflow DAGs consume
