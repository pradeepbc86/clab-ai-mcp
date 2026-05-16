# ADR 0001 — MCP server, not LangChain / LlamaIndex

**Status:** Accepted
**Date:** 2026-05-16

## Context

Multiple frameworks expose tools to LLMs: LangChain (agents + tools), LlamaIndex (RAG-centric), CrewAI (multi-agent), and Anthropic's Model Context Protocol (MCP).

## Decision

Anthropic MCP via `mcp.server.fastmcp.FastMCP`.

## Rationale

| Dimension | LangChain | LlamaIndex | MCP |
|-----------|-----------|------------|-----|
| Protocol-level standard | Framework-specific | Framework-specific | Open spec (Anthropic, OpenAI adopting) |
| Transport | In-process | In-process | stdio / HTTP / WebSocket |
| Client portability | LangChain-only | LlamaIndex-only | Any MCP client (Claude Desktop, IDE plugins, custom hosts) |
| Tool definition | Decorator + schema | Decorator + schema | Decorator + schema (Pydantic-typed) |
| Bidirectional resources/prompts | No first-class concept | Limited | Yes — resources, prompts, tools, sampling |
| Auth / sandboxing | DIY | DIY | Transport-layer (stdio inherits parent process privileges) |
| Velocity of ecosystem (2026) | Active but fragmented | Active, RAG-focused | Growing fast post-Anthropic + OpenAI adoption |

The portability win is the key differentiator: the same `mcp_server.py` we ship here can be loaded into Claude Desktop, IDE extensions, or any future MCP-compatible host. A LangChain agent stays inside a Python process.

## Trade-offs accepted

- MCP is younger than LangChain — fewer tutorials, smaller plugin marketplace.
- The agent loop in `agent.py` is hand-rolled rather than `langgraph` / `crewai` orchestration. For 6 tools and a single-turn-with-tool-use loop, the hand-rolled version is ~40 lines and easy to audit.
- No built-in RAG plumbing. We'd add RAG via a separate MCP server (e.g., a vector-store server) if needed, keeping concerns separate.

## See also

- [Model Context Protocol spec](https://modelcontextprotocol.io/)
- [FastMCP Python implementation](https://github.com/jlowin/fastmcp)
