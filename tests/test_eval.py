"""
Evaluation harness — given scenario X, the agent's tool-dispatch should yield Y.

This isn't a unit test in the traditional sense. It's a regression suite for
agent *reasoning*: when given a question, did the agent invoke the right tools
in a sensible order? Run in MOCK_MODE so tests are deterministic and offline.

Each test exercises tool_handler directly with the expected tool calls,
asserting that:
1. The dispatch produces a non-empty result
2. The result contains the keywords we'd expect from a correctly answered case

This is what netclaw-tier projects do that toy projects don't.
"""

import json
import os

import pytest

os.environ["MOCK_MODE"] = "true"


@pytest.fixture
def tool_handler():
    from agent import tool_handler  # noqa: WPS433 (deferred import after MOCK_MODE)
    return tool_handler


# --- Scenarios ---------------------------------------------------------------

def test_scenario_who_is_asn_13335(tool_handler):
    """Q: 'Who owns AS 13335?' — agent should peeringdb_lookup it."""
    result = tool_handler("peeringdb_lookup", {"asn": 13335})
    data = json.loads(result)
    assert data["asn"] == 13335
    assert data["found"] is True
    assert "cloudflare" in data["name"].lower()


def test_scenario_is_1_1_1_0_24_rpki_valid(tool_handler):
    """Q: 'Is 1.1.1.0/24 from AS 13335 RPKI valid?'"""
    result = tool_handler("check_rpki", {"prefix": "1.1.1.0/24", "origin_as": 13335})
    data = json.loads(result)
    assert data["status"] == "valid"


def test_scenario_check_bgp_on_leaf1(tool_handler):
    """Q: 'Check BGP neighbors on leaf1.'"""
    result = tool_handler("get_bgp_summary", {"host": "leaf1"})
    assert "Established" in result or "Neighbor" in result


def test_scenario_check_evpn_on_leaf1(tool_handler):
    """Q: 'Show me the EVPN VNI state on leaf1.'"""
    result = tool_handler("get_evpn_vni", {"host": "leaf1"})
    assert "VNI" in result or "vxlan" in result


def test_scenario_generate_frr_peer_config(tool_handler):
    """Q: 'Generate a FRR BGP peer config for AS 65001 talking to AS 65000.'"""
    result = tool_handler("generate_bgp_config", {
        "device": "leaf5",
        "vendor": "frr",
        "asn": 65001,
        "peers": [{"ip": "10.10.10.1", "asn": 65000, "description": "spine-x"}],
    })
    assert "router bgp 65001" in result
    assert "10.10.10.1" in result
    assert "remote-as 65000" in result


def test_scenario_unknown_tool_returns_error(tool_handler):
    """Q: agent invents a tool name."""
    result = tool_handler("delete_all_routes", {})
    assert "not found" in result.lower() or "error" in result.lower()


def test_scenario_injection_attempt_is_blocked(tool_handler):
    """Q: prompt-injected hostname must not pass validation."""
    result = tool_handler("get_bgp_summary", {"host": "leaf1; rm -rf /"})
    assert "untrusted" in result.lower() or "error" in result.lower() or "tool error" in result.lower()


# --- Reasoning quality (sanity, not unit) ---

def test_eval_reasoning_about_flapping_prefix():
    """
    The agent should be able to combine clickhouse_tool + peeringdb_lookup to
    answer 'why is 192.168.10.0/24 flapping' — but the dispatch is up to the LLM.
    Here we just verify the underlying tools work in sequence.
    """
    from tools.clickhouse_tool import query_clickhouse
    from tools.peeringdb_tools import peeringdb_lookup

    history = query_clickhouse("prefix_history", prefix="192.168.10.0/24", hours=24)
    assert "data" in history or "rows" in history

    owner = peeringdb_lookup(13335)
    assert owner["found"] is True
