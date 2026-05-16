"""RPKI prefix validation via Cloudflare RPKI API."""

import json
import os
from pathlib import Path
import requests

MOCK_DIR = Path(__file__).parent.parent / "mocks"
MOCK_MODE = os.getenv("MOCK_MODE", "false").lower() == "true"


def check_rpki(prefix: str, origin_as: int) -> dict:
    """Validate a BGP prefix/origin-AS pair against RPKI."""
    if MOCK_MODE:
        data = json.loads((MOCK_DIR / "rpki_valid.json").read_text())
        data["response"]["prefix"] = prefix
        data["response"]["origin_as"] = f"AS{origin_as}"
        return {
            "prefix": prefix,
            "origin_as": origin_as,
            "status": data["response"]["status"],
            "raw": data,
        }
    url = f"https://rpki.cloudflare.com/api/v1/validity?asn=AS{origin_as}&prefix={prefix}"
    resp = requests.get(url, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return {
        "prefix": prefix,
        "origin_as": origin_as,
        "status": data.get("response", {}).get("status", "unknown"),
        "raw": data,
    }
