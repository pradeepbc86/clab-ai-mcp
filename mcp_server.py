#!/usr/bin/env python3
"""
MCP Server for network operations tools.
Exposes BGP, RPKI, PeeringDB, and config generation as Claude tools.
"""

from mcp.server.fastmcp import FastMCP
import subprocess
import requests
import json

mcp = FastMCP("network-ops-agent")

@mcp.tool()
def get_bgp_summary(host: str) -> str:
    """Get BGP neighbor summary from a router via SSH"""
    try:
        cmd = f"ssh admin@{host} 'vtysh -c \"show bgp summary\"'"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        return result.stdout or result.stderr
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
def get_bgp_routes(host: str, prefix: str = "") -> str:
    """Get BGP routes from a router"""
    try:
        cmd = f"ssh admin@{host} 'vtysh -c \"show bgp ipv4 {prefix}\"'"
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        return result.stdout or result.stderr
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
def peeringdb_lookup(asn: int) -> str:
    """Look up ASN info on PeeringDB"""
    try:
        resp = requests.get(f"https://api.peeringdb.com/api/asn/{asn}", timeout=10)
        data = resp.json()
        return json.dumps(data, indent=2)
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
def check_rpki(prefix: str, origin_as: int) -> str:
    """Validate prefix/ASN via RPKI (Cloudflare API)"""
    try:
        resp = requests.get(
            f"https://rpki.cloudflare.com/api/v1/validity?asn=AS{origin_as}&prefix={prefix}",
            timeout=10
        )
        data = resp.json()
        return json.dumps(data, indent=2)
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
def generate_bgp_config(device: str, vendor: str, asn: int) -> str:
    """Generate BGP config using Jinja2 template"""
    from jinja2 import Environment, FileSystemLoader
    try:
        env = Environment(loader=FileSystemLoader('templates'))
        template = env.get_template(f"{vendor}/bgp_peer.j2")
        config = template.render(device=device, asn=asn)
        return config
    except Exception as e:
        return f"Error: {e}"

if __name__ == "__main__":
    mcp.run()
