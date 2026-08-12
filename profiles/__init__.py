import json
import os
from pathlib import Path

_DIR = Path(__file__).resolve().parent


def _env_var_for(name: str) -> str:
    return f"STOCKBEAT_API_KEY_{name.upper().replace('-', '_')}"


def load_profile(name: str) -> dict:
    """Load a trading agent profile from JSON.

    The API key comes from the environment (STOCKBEAT_API_KEY_<NAME_UPPER>, e.g.
    STOCKBEAT_API_KEY_TECHNICAL_EXAMPLE) and is deliberately never read from the
    profile JSON. Profiles are tracked in git, so a fallback field there is
    exactly how a live key ends up in a public commit.

    Raises ValueError when the key is unset, rather than proceeding with an
    empty string and surfacing an opaque 401 later in the run.
    """
    path = _DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"No profile: {path}")

    profile = json.loads(path.read_text())
    env_var = _env_var_for(name)
    api_key = os.getenv(env_var, "")
    if not api_key:
        raise ValueError(
            f"Missing API key for profile {name!r}. Set {env_var} in your .env file."
        )
    profile["api_key"] = api_key
    return profile
