"""
Pydantic models for structured tool inputs and outputs.

Every tool returns one of these — never a free-form string. This lets the LLM
chain calls reliably (it knows the shape of the next tool's input) and lets us
add type-checked assertions in the evaluation harness.
"""

from typing import Literal, Optional
from pydantic import BaseModel, Field


# --- BGP tool outputs ---

class BGPPeerState(BaseModel):
    peer_ip: str
    peer_asn: int
    state: Literal["Established", "Idle", "Active", "Connect", "OpenSent", "OpenConfirm"]
    uptime_seconds: Optional[int] = None
    prefixes_received: Optional[int] = None
    description: Optional[str] = None


class BGPSummary(BaseModel):
    host: str
    local_asn: int
    router_id: str
    peers: list[BGPPeerState]
    total_peers: int
    established_peers: int


class EVPNVNIInfo(BaseModel):
    vni: int
    type: Literal["L2", "L3"]
    vxlan_if: str
    local_macs: int
    remote_vteps: int
    tenant_vrf: str


class EVPNState(BaseModel):
    host: str
    vnis: list[EVPNVNIInfo]


# --- RPKI ---

class RPKIValidity(BaseModel):
    prefix: str
    origin_as: int
    status: Literal["valid", "invalid", "not-found", "unknown"]
    vrp_matched: Optional[list[dict]] = None
    description: Optional[str] = None


# --- PeeringDB ---

class PeeringDBNetwork(BaseModel):
    asn: int
    found: bool
    name: Optional[str] = None
    website: Optional[str] = None
    info_type: Optional[str] = None
    policy_general: Optional[str] = None
    ix_count: int = 0
    pni_count: int = 0


# --- Citations ---

class Citation(BaseModel):
    """Every claim the agent makes should cite the tool_use_id it came from."""
    tool_use_id: str
    tool_name: str
    excerpt: str = Field(max_length=500)


class AgentAnswer(BaseModel):
    """Wraps an agent response with the citations that support it."""
    answer: str
    citations: list[Citation]
    confidence: Literal["high", "medium", "low"]
