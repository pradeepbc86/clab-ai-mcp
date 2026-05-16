"""
Human-in-the-loop approval gate for write-effecting tools.

The agent proposes a change (e.g., a generated config diff). The HITL flow:
  1. Renders the proposed change as a diff
  2. Writes a 'pending' record to approvals/<id>.json
  3. Blocks (or returns) until the operator approves/denies via CLI or web

In MOCK_MODE / non-interactive contexts, AUTO_APPROVE env var can flip this
to auto-approve (useful for CI). Real deployments wire this to Slack /
ServiceNow / PagerDuty.
"""

import json
import os
import time
import uuid
from pathlib import Path

APPROVALS_DIR = Path(os.getenv("APPROVALS_DIR", "approvals"))
AUTO_APPROVE = os.getenv("HITL_AUTO_APPROVE", "false").lower() == "true"


def propose_change(
    *,
    summary: str,
    diff: str,
    target: str,
    impact: str = "unknown",
    proposed_by: str = "agent",
) -> dict:
    """
    Stage a change for human approval. Returns the pending record.

    Args:
        summary: one-line description of the change
        diff: the unified diff of what would be applied
        target: which device(s) / fabric component the change touches
        impact: 'low' | 'medium' | 'high' — drives approval routing
        proposed_by: 'agent' | username
    """
    record = {
        "id": str(uuid.uuid4()),
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "status": "pending",
        "summary": summary,
        "target": target,
        "impact": impact,
        "proposed_by": proposed_by,
        "diff": diff,
    }
    APPROVALS_DIR.mkdir(parents=True, exist_ok=True)
    path = APPROVALS_DIR / f"{record['id']}.json"
    path.write_text(json.dumps(record, indent=2))
    return record


def await_decision(approval_id: str, *, timeout_seconds: int = 0) -> str:
    """
    Return the operator's decision ('approved' | 'denied').

    Behavior:
      - HITL_AUTO_APPROVE=true → returns 'approved' immediately (CI mode)
      - timeout_seconds=0 → returns 'pending' without blocking (caller polls)
      - timeout_seconds>0 → polls the file every 2s until status changes
    """
    if AUTO_APPROVE:
        _set_status(approval_id, "approved", "auto-approved via HITL_AUTO_APPROVE")
        return "approved"

    path = APPROVALS_DIR / f"{approval_id}.json"
    if not path.exists():
        return "not-found"

    if timeout_seconds == 0:
        return json.loads(path.read_text())["status"]

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        record = json.loads(path.read_text())
        if record["status"] != "pending":
            return record["status"]
        time.sleep(2)
    return "timeout"


def approve(approval_id: str, *, reviewer: str, note: str = "") -> dict:
    """Mark a pending change as approved."""
    return _set_status(approval_id, "approved", note, reviewer=reviewer)


def deny(approval_id: str, *, reviewer: str, note: str = "") -> dict:
    """Mark a pending change as denied."""
    return _set_status(approval_id, "denied", note, reviewer=reviewer)


def _set_status(approval_id: str, status: str, note: str = "", reviewer: str = "system") -> dict:
    path = APPROVALS_DIR / f"{approval_id}.json"
    record = json.loads(path.read_text())
    record["status"] = status
    record["reviewer"] = reviewer
    record["review_note"] = note
    record["reviewed_ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    path.write_text(json.dumps(record, indent=2))
    return record
