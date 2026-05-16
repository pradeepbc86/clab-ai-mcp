#!/usr/bin/env python3
"""
MCP Server for network operations tools.
Exposes BGP, RPKI, PeeringDB, and config generation as Claude tools.
All implementations live in the `tools/` package and honor MOCK_MODE.
"""

import json

from mcp.server.fastmcp import FastMCP

from tools.bgp_tools import (
    get_bgp_summary as _bgp_summary,
    get_bgp_routes as _bgp_routes,
    get_evpn_vni as _evpn_vni,
)
from tools.rpki_tools import check_rpki as _rpki
from tools.peeringdb_tools import peeringdb_lookup as _peeringdb
from tools.config_tools import generate_bgp_config as _gen_config
from tools.clickhouse_tool import query_clickhouse as _clickhouse
from tools.validation import validate_host as _validate_host, validate_prefix as _validate_prefix

mcp = FastMCP("network-ops-agent")


@mcp.tool()
def get_bgp_summary(host: str) -> str:
    """Get BGP neighbor summary from a router via Netmiko SSH."""
    try:
        return _bgp_summary(_validate_host(host))
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def get_bgp_routes(host: str, prefix: str = "") -> str:
    """Get BGP routes from a router, optionally filtered by prefix."""
    try:
        host = _validate_host(host)
        prefix = _validate_prefix(prefix)
        return _bgp_routes(host, prefix)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def get_evpn_vni(host: str) -> str:
    """Get EVPN VNI state from a router."""
    try:
        return _evpn_vni(_validate_host(host))
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def peeringdb_lookup(asn: int) -> str:
    """Look up ASN info on PeeringDB (name, IXP count, peering policy)."""
    try:
        return json.dumps(_peeringdb(asn), indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def check_rpki(prefix: str, origin_as: int) -> str:
    """Validate prefix/ASN via Cloudflare RPKI API. Returns valid/invalid/not-found."""
    try:
        return json.dumps(_rpki(prefix, origin_as), indent=2)
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def generate_bgp_config(
    device: str, vendor: str, asn: int, peers: list[dict] | None = None
) -> str:
    """Generate a BGP peer config via Jinja2. vendor=frr|arista|juniper."""
    try:
        return _gen_config(device=device, vendor=vendor, asn=asn, peers=peers or [])
    except Exception as e:
        return f"Error: {e}"


@mcp.tool()
def query_clickhouse(query_name: str, **params) -> str:
    """Query clab-obs-telemetry's ClickHouse for BGP analytics.

    query_name is one of: prefix_history, top_flapping_prefixes, peer_route_counts.
    Each query has typed parameters — see tools/clickhouse_tool.py for the schema.
    """
    try:
        return json.dumps(_clickhouse(query_name, **params), indent=2)
    except Exception as e:
        return f"Error: {e}"


if __name__ == "__main__":
    mcp.run()
