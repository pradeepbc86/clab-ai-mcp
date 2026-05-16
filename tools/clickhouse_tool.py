"""
Cross-project tool: query ClickHouse in clab-observability to answer
"why did this prefix flap" / "show me top churning prefixes last 24h".

This is the integration point that turns 4 disconnected repos into a
coherent platform.
"""

import json
import os
from pathlib import Path
import requests

CLICKHOUSE_URL = os.getenv("CLICKHOUSE_URL", "http://localhost:8123")
MOCK_DIR = Path(__file__).parent.parent / "mocks"
MOCK_MODE = os.getenv("MOCK_MODE", "false").lower() == "true"

# Allowlist of canned analytical queries — the LLM picks one by name and supplies
# parameters. This is a deliberately narrow surface; arbitrary SQL is not exposed
# to the LLM because that's an injection vector with high blast radius.
QUERIES = {
    "prefix_history": """
        SELECT timestamp, action, peer_ip, peer_asn
        FROM bgp_routes
        WHERE prefix = {prefix:String}
          AND timestamp >= now() - INTERVAL {hours:UInt32} HOUR
        ORDER BY timestamp DESC
        LIMIT 100
        FORMAT JSON
    """,
    "top_flapping_prefixes": """
        SELECT prefix, origin_as, count() AS state_changes
        FROM bgp_routes
        WHERE timestamp >= now() - INTERVAL {hours:UInt32} HOUR
        GROUP BY prefix, origin_as
        ORDER BY state_changes DESC
        LIMIT {limit:UInt32}
        FORMAT JSON
    """,
    "peer_route_counts": """
        SELECT peer_ip, peer_asn, count(DISTINCT prefix) AS prefix_count
        FROM bgp_routes
        WHERE timestamp >= now() - INTERVAL {minutes:UInt32} MINUTE
          AND withdrawn = 0
        GROUP BY peer_ip, peer_asn
        ORDER BY prefix_count DESC
        FORMAT JSON
    """,
    # Peer state for a single host — agent's get_bgp_summary uses this so it
    # reads from the telemetry pipeline instead of SSH-scraping.
    "peer_events_for_host": """
        SELECT timestamp, peer_ip, peer_asn, event_type, reason_text
        FROM bgp_peer_events
        WHERE monitor_ip = {host:String}
          AND timestamp >= now() - INTERVAL {minutes:UInt32} MINUTE
        ORDER BY timestamp DESC
        LIMIT 50
        FORMAT JSON
    """,
}


def query_clickhouse(query_name: str, **params) -> dict:
    """
    Execute a named query with parameters.

    Args:
        query_name: one of QUERIES.keys()
        **params: query-specific parameters (typed via ClickHouse Param syntax)

    Returns:
        dict with 'rows' (list of records) and 'meta' (column names, types)
    """
    if query_name not in QUERIES:
        return {"error": f"Unknown query {query_name!r}. Allowed: {list(QUERIES)}"}

    if MOCK_MODE:
        mock_file = MOCK_DIR / f"clickhouse_{query_name}.json"
        if mock_file.exists():
            return json.loads(mock_file.read_text())
        return {"rows": [], "meta": [], "mock": True, "note": "no mock file"}

    query = QUERIES[query_name]
    # ClickHouse parameter binding via the ?param_<name>= syntax
    param_query = {f"param_{k}": v for k, v in params.items()}
    try:
        resp = requests.post(
            CLICKHOUSE_URL, data=query, params=param_query, timeout=30,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}
