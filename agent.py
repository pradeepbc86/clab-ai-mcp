#!/usr/bin/env python3
"""
Standalone Claude agent for network operations.
Uses tool_use loop to query BGP state, validate RPKI, look up PeeringDB,
and generate multi-vendor BGP configs.

Every tool call is appended to audit.jsonl. Every Claude turn's token usage
and computed USD cost is appended to telemetry.jsonl.
"""

import json
import os
import sys
import time
import uuid

from tools.audit import log_tool_call

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1024
MAX_ITERATIONS = 20  # cap agent-loop iterations (DoS / runaway protection)
SESSION_ID = os.environ.setdefault("AGENT_SESSION_ID", str(uuid.uuid4()))


def _get_client():
    """Lazy-import Anthropic so unit tests don't require the SDK installed."""
    from anthropic import Anthropic
    return Anthropic()

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
        "name": "get_bgp_routes",
        "description": "Get BGP RIB from a router, optionally filtered to a prefix.",
        "input_schema": {
            "type": "object",
            "properties": {
                "host": {"type": "string"},
                "prefix": {"type": "string", "description": "Optional IPv4 prefix filter, e.g. 10.0.0.0/24"},
            },
            "required": ["host"],
        },
    },
    {
        "name": "get_evpn_vni",
        "description": "Get EVPN VNI state from a router (show evpn vni).",
        "input_schema": {
            "type": "object",
            "properties": {"host": {"type": "string"}},
            "required": ["host"],
        },
    },
    {
        "name": "peeringdb_lookup",
        "description": "Look up ASN info on PeeringDB (name, IXP count, peering policy).",
        "input_schema": {
            "type": "object",
            "properties": {"asn": {"type": "integer"}},
            "required": ["asn"],
        },
    },
    {
        "name": "check_rpki",
        "description": "Validate a BGP prefix/origin-AS pair against Cloudflare RPKI API.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prefix": {"type": "string"},
                "origin_as": {"type": "integer"},
            },
            "required": ["prefix", "origin_as"],
        },
    },
    {
        "name": "generate_bgp_config",
        "description": "Generate a multi-vendor BGP peer config via Jinja2 templates.",
        "input_schema": {
            "type": "object",
            "properties": {
                "device": {"type": "string"},
                "vendor": {"type": "string", "enum": ["frr", "arista", "juniper"]},
                "asn": {"type": "integer"},
                "peers": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ip": {"type": "string"},
                            "asn": {"type": "integer"},
                            "description": {"type": "string"},
                        },
                    },
                },
            },
            "required": ["device", "vendor", "asn"],
        },
    },
    {
        "name": "query_clickhouse",
        "description": "Query clab-obs-telemetry's ClickHouse for BGP analytics. "
                       "Named queries: prefix_history (params: prefix, hours), "
                       "top_flapping_prefixes (params: hours, limit), "
                       "peer_route_counts (params: minutes).",
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
]


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
            result = generate_bgp_config(
                device=tool_input["device"],
                vendor=tool_input["vendor"],
                asn=tool_input["asn"],
                peers=tool_input.get("peers", []),
            )
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


def run_agent(query):
    """Run the Claude agent tool_use loop until completion (capped at MAX_ITERATIONS)."""
    from tools.telemetry import record_usage  # lazy

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
            tool_results.append(
                {"type": "tool_result", "tool_use_id": tu.id, "content": result}
            )

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": tool_results})

        response = client.messages.create(
            model=MODEL, max_tokens=MAX_TOKENS, tools=tools, messages=messages
        )
        record_usage(response, session_id=SESSION_ID, query=query)

    final_text = [b.text for b in response.content if hasattr(b, "text")]
    if final_text:
        print(final_text[0])


if __name__ == "__main__":
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "Check BGP neighbors on leaf1"
    run_agent(query)
