#!/usr/bin/env python3
"""
HITL approval flow demo — agent proposes a BGP config change, operator approves.

Run:
  python scripts/hitl_demo.py           # interactive — pauses for approval
  HITL_AUTO_APPROVE=true python ...     # CI-friendly — auto-approves
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from tools.config_tools import generate_bgp_config
from tools.hitl import propose_change, await_decision


def main():
    # Simulate the agent producing a proposed config from a natural-language ask
    proposed = generate_bgp_config(
        device="leaf3",
        vendor="frr",
        asn=4200000005,
        peers=[
            {"ip": "10.10.5.1", "asn": 4200000001, "description": "spine1"},
            {"ip": "10.10.6.1", "asn": 4200000002, "description": "spine2"},
        ],
    )

    record = propose_change(
        summary="Add leaf3 with eBGP sessions to spine1/spine2",
        diff=proposed,
        target="leaf3",
        impact="medium",
        proposed_by="agent/claude-sonnet-4-6",
    )

    print(f"Proposed change staged: approvals/{record['id']}.json")
    print(f"Summary: {record['summary']}")
    print(f"Target:  {record['target']}")
    print(f"Impact:  {record['impact']}")
    print()
    print("Diff preview:")
    print("─" * 60)
    print(proposed)
    print("─" * 60)
    print()
    print("Operator action:")
    print(f"  python -c \"from tools.hitl import approve; approve('{record['id']}', reviewer='pradeep')\"")
    print(f"  python -c \"from tools.hitl import deny;    deny('{record['id']}',    reviewer='pradeep', note='REASON')\"")
    print()

    print("Polling for decision (Ctrl-C to abort)...")
    decision = await_decision(record["id"], timeout_seconds=60)
    print(f"\nDecision: {decision}")

    if decision == "approved":
        print("→ would now call deploy.py / push to git / etc.")
    elif decision == "denied":
        print("→ change discarded; agent should ask operator for clarification.")
    elif decision == "timeout":
        print("→ no decision in 60s; change remains pending in approvals/")


if __name__ == "__main__":
    main()
