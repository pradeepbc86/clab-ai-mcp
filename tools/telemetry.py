"""
Token + cost telemetry for the Claude agent.

Tracks input/output tokens and rolls up USD cost per session.
Writes to telemetry.jsonl so downstream Grafana/Prometheus can scrape it.
"""

import json
import os
import time
from pathlib import Path

TELEMETRY_PATH = Path(os.getenv("TELEMETRY_LOG", "telemetry.jsonl"))

# Anthropic pricing as of 2026-05 — claude-sonnet-4-6
# Update if pricing changes; ideally pulled from a config file in real deployments.
PRICE_PER_MTOK_INPUT = 3.00   # $ per million input tokens
PRICE_PER_MTOK_OUTPUT = 15.00  # $ per million output tokens


def record_usage(
    response,
    *,
    session_id: str | None = None,
    query: str = "",
):
    """
    Record a single Anthropic response's token usage and computed cost.

    Args:
        response: the Anthropic Messages response object (has .usage)
        session_id: agent session UUID
        query: the user query that triggered this turn
    """
    usage = response.usage
    input_tokens = usage.input_tokens
    output_tokens = usage.output_tokens
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0

    # Non-cached input bills at full rate; cache reads bill at 10%
    chargeable_input = input_tokens - cache_read
    cost_usd = (
        (chargeable_input / 1_000_000) * PRICE_PER_MTOK_INPUT
        + (cache_read / 1_000_000) * PRICE_PER_MTOK_INPUT * 0.10
        + (output_tokens / 1_000_000) * PRICE_PER_MTOK_OUTPUT
    )

    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": session_id or os.getenv("AGENT_SESSION_ID", "unknown"),
        "query_preview": query[:200],
        "model": getattr(response, "model", "claude-sonnet-4-6"),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read,
        "cache_create_tokens": cache_create,
        "cost_usd": round(cost_usd, 6),
        "stop_reason": getattr(response, "stop_reason", None),
    }
    TELEMETRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    with TELEMETRY_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")
    return record
