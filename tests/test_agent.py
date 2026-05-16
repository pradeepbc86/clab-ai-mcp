"""Unit tests for agent tool handling and mcp_server validation logic."""

import pytest
from unittest.mock import patch, MagicMock


# --- agent.py tool_handler ---

def test_tool_handler_bgp_summary():
    from agent import tool_handler
    result = tool_handler("get_bgp_summary", {"host": "leaf1"})
    assert "BGP" in result or "leaf1" in result


def test_tool_handler_peeringdb():
    from agent import tool_handler
    result = tool_handler("peeringdb_lookup", {"asn": 13335})
    assert "13335" in result


def test_tool_handler_rpki():
    from agent import tool_handler
    result = tool_handler("check_rpki", {"prefix": "1.1.1.0/24", "origin_as": 13335})
    assert "1.1.1.0/24" in result


def test_tool_handler_unknown():
    from agent import tool_handler
    result = tool_handler("nonexistent_tool", {})
    assert "not found" in result.lower()


# --- validation (used by mcp_server) ---

def test_valid_lab_node():
    from tools.validation import validate_host
    assert validate_host("spine1") == "spine1"
    assert validate_host("leaf1") == "leaf1"


def test_valid_ipv4():
    from tools.validation import validate_host
    assert validate_host("192.168.1.1") == "192.168.1.1"


def test_invalid_host_injection():
    from tools.validation import validate_host
    with pytest.raises(ValueError):
        validate_host("leaf1; rm -rf /")


def test_invalid_host_unknown():
    from tools.validation import validate_host
    with pytest.raises(ValueError):
        validate_host("unknown-device")


# --- config_tools.py ---

def test_generate_frr_config():
    from tools.config_tools import generate_bgp_config
    out = generate_bgp_config(
        device="leaf1", vendor="frr", asn=65001,
        peers=[{"ip": "10.10.1.1", "asn": 65000, "description": "spine1"}]
    )
    assert "65001" in out
    assert "10.10.1.1" in out


def test_generate_juniper_config():
    from tools.config_tools import generate_bgp_config
    out = generate_bgp_config(
        device="leaf1", vendor="juniper", asn=65001,
        peers=[{"ip": "10.10.1.1", "asn": 65000, "description": "spine1"}]
    )
    assert "65001" in out
    assert "junos" in out.lower() or "protocols" in out
