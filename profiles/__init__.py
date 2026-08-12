import json
import os
from pathlib import Path

_DIR = Path(__file__).resolve().parent


def load_profile(name: str) -> dict:
    """Load a trading agent profile from JSON.

    The api_key is resolved from environment variable
    STOCKBEAT_API_KEY_<NAME_UPPER> (e.g., STOCKBEAT_API_KEY_MOMENTUM_MIKE).
    Falls back to the value in the JSON file.
    """
    path = _DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"No profile: {path}")
    profile = json.loads(path.read_text())
    env_key = f"STOCKBEAT_API_KEY_{name.upper().replace('-', '_')}"
    profile["api_key"] = os.getenv(env_key, profile.get("api_key", ""))
    return profile
