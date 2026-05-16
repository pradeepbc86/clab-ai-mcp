"""
Append-only audit log for every tool invocation.

Each line is a single JSON object — write `audit.jsonl` next to the agent.
SIEM-friendly format (jsonl), works with `jq` / `vector` / Splunk forwarders.
"""

import json
import os
import time
import uuid
from pathlib import Path

AUDIT_PATH = Path(os.getenv("AUDIT_LOG", "audit.jsonl"))


def log_tool_call(
    tool_name: str,
    tool_input: dict,
    result: str,
    *,
    duration_ms: float | None = None,
    session_id: str | None = None,
    decision: str = "auto",
):
    """
    Append one structured record describing a tool invocation.

    Args:
        tool_name: name of the tool invoked
        tool_input: the LLM-supplied arguments
        result: stringified tool result (truncated to 4096 chars)
        duration_ms: wall-clock duration of the tool call
        session_id: agent session UUID (one per conversation)
        decision: 'auto' | 'approved' | 'denied' | 'rejected' (for HITL)
    """
    record = {
        "id": str(uuid.uuid4()),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "session_id": session_id or os.getenv("AGENT_SESSION_ID", "unknown"),
        "tool": tool_name,
        "input": tool_input,
        "result_preview": result[:4096] if isinstance(result, str) else str(result)[:4096],
        "duration_ms": duration_ms,
        "decision": decision,
    }
    AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")
    return record["id"]
