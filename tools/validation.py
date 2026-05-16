"""Input validation primitives. Pure-Python, no mcp dependency — keeps tests light."""

import re

# Allowlist of valid lab hostnames. Update if topology changes.
LAB_NODES = frozenset({"spine1", "spine2", "leaf1", "leaf2"})

_IPV4_RE = re.compile(r"^(?:[0-9]{1,3}\.){3}[0-9]{1,3}$")
_PREFIX_RE = re.compile(r"^[0-9./]+$")


def validate_host(host: str) -> str:
    """Validate host is a known lab node or a plain IPv4 dotted-quad."""
    if not isinstance(host, str) or not host.strip():
        raise ValueError(f"Untrusted host: {host!r}. Empty / wrong type.")
    if host in LAB_NODES:
        return host
    if _IPV4_RE.match(host):
        # Ensure each octet is 0-255
        if all(0 <= int(o) <= 255 for o in host.split(".")):
            return host
    raise ValueError(f"Untrusted host: {host!r}. Must be a lab node name or IPv4 address.")


def validate_prefix(prefix: str) -> str:
    """Validate that a BGP prefix string contains only digits, dots, and slashes."""
    if not prefix:
        return prefix
    if not _PREFIX_RE.match(prefix):
        raise ValueError(f"Invalid prefix format: {prefix!r}")
    return prefix
