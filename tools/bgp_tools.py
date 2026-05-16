"""BGP state retrieval via Netmiko SSH to FRR vtysh."""

import os
from pathlib import Path

from .validation import validate_host, validate_prefix

MOCK_DIR = Path(__file__).parent.parent / "mocks"
MOCK_MODE = os.getenv("MOCK_MODE", "false").lower() == "true"


def _connect(host: str):
    from netmiko import ConnectHandler
    return ConnectHandler(device_type="linux", host=host, username="root", timeout=10)


def get_bgp_summary(host: str) -> str:
    """SSH into host and return show bgp summary output."""
    host = validate_host(host)  # raises ValueError on adversarial input
    if MOCK_MODE:
        return (MOCK_DIR / "bgp_summary.txt").read_text()
    with _connect(host) as conn:
        return conn.send_command("vtysh -c 'show bgp summary'")


def get_bgp_routes(host: str, prefix: str = "") -> str:
    """Return BGP RIB, optionally filtered to a prefix."""
    host = validate_host(host)
    prefix = validate_prefix(prefix)
    if MOCK_MODE:
        return f"Mock BGP routes for {host} (prefix={prefix or 'all'})"
    cmd = f"vtysh -c 'show bgp ipv4 {prefix}'" if prefix else "vtysh -c 'show bgp ipv4'"
    with _connect(host) as conn:
        return conn.send_command(cmd)


def get_evpn_vni(host: str) -> str:
    """Return EVPN VNI table from host."""
    host = validate_host(host)
    if MOCK_MODE:
        return (
            "VNI      Type VxLAN IF              # MACs   # ARPs   # Remote VTEPs Tenant VRF\n"
            "10       L2   vxlan10             2        2        1             default"
        )
    with _connect(host) as conn:
        return conn.send_command("vtysh -c 'show evpn vni'")
