"""Pytest fixtures: force MOCK_MODE so tests don't hit real APIs/SSH."""

import os
import sys
from pathlib import Path

# Set MOCK_MODE before any tools modules get imported
os.environ.setdefault("MOCK_MODE", "true")

# Make sure project root is importable (so `from tools.*` and `from agent` work)
ROOT = Path(__file__).parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
