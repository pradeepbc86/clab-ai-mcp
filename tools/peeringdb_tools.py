"""PeeringDB ASN and IX presence lookups."""

import json
import os
from pathlib import Path
import requests

from .cache import ttl_cache

BASE = "https://api.peeringdb.com/api"
MOCK_DIR = Path(__file__).parent.parent / "mocks"
MOCK_MODE = os.getenv("MOCK_MODE", "false").lower() == "true"


@ttl_cache(seconds=3600)  # PeeringDB updates infrequently; 1h cache is safe
def peeringdb_lookup(asn: int) -> dict:
    """Return network info for an ASN from PeeringDB."""
    if MOCK_MODE:
        data = json.loads((MOCK_DIR / "peeringdb_asn13335.json").read_text())
        net = data["data"][0]
        net["asn"] = asn
        return _format(asn, net)
    resp = requests.get(f"{BASE}/net?asn={asn}", timeout=10)
    resp.raise_for_status()
    nets = resp.json().get("data", [])
    if not nets:
        return {"asn": asn, "found": False}
    return _format(asn, nets[0])


def _format(asn: int, net: dict) -> dict:
    return {
        "asn": asn,
        "found": True,
        "name": net.get("name"),
        "website": net.get("website"),
        "info_type": net.get("info_type"),
        "policy_general": net.get("policy_general"),
        "ix_count": len(net.get("netixlan_set", [])),
        "pni_count": len(net.get("netfac_set", [])),
    }


def ix_presence(asn: int) -> list[dict]:
    """Return IXPs where an ASN is present."""
    resp = requests.get(f"{BASE}/netixlan?net__asn={asn}", timeout=10)
    resp.raise_for_status()
    return [
        {"ix_id": r["ixlan_id"], "ipv4": r.get("ipaddr4"), "speed": r.get("speed")}
        for r in resp.json().get("data", [])
    ]
