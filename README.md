# clab-llm-netops-agent

Claude API + MCP-Server driven network operations agent. Queries BGP state, validates RPKI, looks up PeeringDB info, generates multi-vendor configs. Airflow DAG for daily BGP health checks.

## Architecture

```
Airflow DAG (@daily)
    ↓
agent.py (claude-sonnet-4-6 + tool_use loop)
    ↓
mcp_server.py (FastMCP, stdio transport)
    ├── get_bgp_summary(host)         → Netmiko SSH → FRR vtysh
    ├── get_bgp_routes(host, prefix)  → parsed BGP RIB
    ├── get_evpn_vni(host)            → EVPN VNI state
    ├── generate_bgp_config(vars)     → Jinja2 render
    ├── check_rpki(prefix, origin_as) → Cloudflare RPKI API
    └── peeringdb_lookup(asn)         → PeeringDB REST API
```

## Tools

| Tool | Purpose |
|------|---------|
| **ContainerLab** | FRR lab nodes (agent targets) |
| **FRRouting** | BGP/OSPF in lab containers |
| **Claude API** | LLM agent brain (claude-sonnet-4-6) |
| **MCP-Server** | Network tools as Claude tools (stdio) |
| **Netmiko** | SSH into devices, run vtysh commands |
| **Jinja2** | Config template rendering |
| **PeeringDB** | ASN/IX/peer info lookup |
| **RPKI** | BGP prefix validation (Cloudflare API) |
| **Airflow** | DAG scheduler for daily health checks |
| **GitLab CI/CD** | Lint + unit tests |

## Demo Scenarios

```bash
# Standalone agent
python agent.py "Check BGP neighbors on leaf1"
python agent.py "Look up ASN 13335 on PeeringDB"
python agent.py "Is 1.1.1.0/24 from AS 13335 RPKI valid?"
python agent.py "Generate a Juniper BGP peer config for AS 64501 at 10.1.1.1"

# MCP Server for Claude Desktop
python mcp_server.py
# Then in Claude Desktop, the agent can use all network tools

# Airflow DAG (daily)
airflow dags test bgp_health_check
```

## Claude Desktop Setup

```json
{
  "mcpServers": {
    "network-ops": {
      "command": "python",
      "args": ["/path/to/clab-llm-netops-agent/mcp_server.py"]
    }
  }
}
```

## Files

- `mcp_server.py` — FastMCP server exposing network tools
- `agent.py` — Standalone Claude API agent loop
- `tools/` — BGP, RPKI, PeeringDB, config generation tools
- `templates/` — Jinja2 config templates (FRR, Juniper, Arista, Cisco)
- `airflow/` — DAG for daily BGP health checks
- `topology/` — 3-node FRR lab for testing
- `.gitlab-ci.yml` — Lint + unit tests
- `pyproject.toml` — Dependencies

## Prerequisites

- Docker & ContainerLab
- Python 3.9+ (anthropic, mcp, netmiko, jinja2, airflow)
- ANTHROPIC_API_KEY set in .env
- FRRouting Docker image: `frrouting/frr:9.1.0`

## Quick Start

```bash
# Deploy lab
make deploy

# Run standalone agent
python agent.py "check BGP on spine1"

# Start MCP server
python mcp_server.py

# Schedule Airflow DAG
airflow dags trigger bgp_health_check

# Validate
make validate
```
