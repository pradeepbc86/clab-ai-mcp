#!/usr/bin/env python3
"""
Standalone Claude agent for network operations.
Uses tool_use to query BGP state, RPKI, PeeringDB, and generate configs.
"""

import os
import sys
from anthropic import Anthropic

client = Anthropic()

tools = [
    {
        "name": "get_bgp_summary",
        "description": "Get BGP neighbor summary from a router",
        "input_schema": {
            "type": "object",
            "properties": {
                "host": {"type": "string", "description": "Device hostname or IP"}
            },
            "required": ["host"]
        }
    },
    {
        "name": "peeringdb_lookup",
        "description": "Look up ASN information on PeeringDB",
        "input_schema": {
            "type": "object",
            "properties": {
                "asn": {"type": "integer", "description": "AS Number"}
            },
            "required": ["asn"]
        }
    },
    {
        "name": "check_rpki",
        "description": "Validate BGP prefix/origin-AS via RPKI",
        "input_schema": {
            "type": "object",
            "properties": {
                "prefix": {"type": "string", "description": "BGP prefix"},
                "origin_as": {"type": "integer", "description": "Origin AS"}
            },
            "required": ["prefix", "origin_as"]
        }
    }
]

def tool_handler(tool_name, tool_input):
    """Simulate tool execution"""
    if tool_name == "get_bgp_summary":
        return f"BGP Summary for {tool_input['host']}: Neighbors Established"
    elif tool_name == "peeringdb_lookup":
        return f"ASN {tool_input['asn']}: IXPs: 48, PNI: 200+"
    elif tool_name == "check_rpki":
        return f"{tool_input['prefix']} from AS{tool_input['origin_as']}: RPKI Valid"
    return "Tool not found"

def run_agent(query):
    """Run Claude agent loop"""
    print(f"\n>>> {query}\n")

    messages = [{"role": "user", "content": query}]

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        tools=tools,
        messages=messages
    )

    while response.stop_reason == "tool_use":
        tool_use = [b for b in response.content if b.type == "tool_use"][0]
        tool_result = tool_handler(tool_use.name, tool_use.input)

        messages.append({"role": "assistant", "content": response.content})
        messages.append({
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_use.id, "content": tool_result}]
        })

        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            tools=tools,
            messages=messages
        )

    final_text = [b.text for b in response.content if hasattr(b, "text")]
    if final_text:
        print(final_text[0])

if __name__ == "__main__":
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
    else:
        query = "Check BGP neighbors on leaf1"

    run_agent(query)
