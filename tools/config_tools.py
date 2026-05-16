"""BGP config generation via Jinja2 templates."""

from pathlib import Path
from jinja2 import Environment, FileSystemLoader, StrictUndefined

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def generate_bgp_config(device: str, vendor: str, asn: int, peers: list[dict] | None = None) -> str:
    """
    Render a BGP peer config from a Jinja2 template.

    Args:
        device: hostname
        vendor: 'frr', 'arista', 'juniper' — maps to templates/<vendor>_bgp_peer.j2
        asn: local AS number
        peers: list of {'ip': str, 'asn': int, 'description': str}
    """
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        undefined=StrictUndefined,
    )
    template = env.get_template(f"{vendor}_bgp_peer.j2")
    return template.render(device=device, asn=asn, peers=peers or [])
