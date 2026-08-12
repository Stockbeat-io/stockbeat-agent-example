import json
from pathlib import Path

import pytest

from profiles import load_profile

PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"
REQUIRED_KEYS = {
    "name", "display_name", "persona", "persona_type",
    "schedule_hour", "schedule_minute",
}
# Wire values from the platform's VALID_PERSONAS. No other value is valid.
VALID_PERSONA_TYPES = {"Technical", "Fundamental", "Sentiment", "Macro", "Hybrid"}


def _profiles():
    return sorted(PROFILES_DIR.glob("*.json"))


def test_profiles_directory_is_populated_and_valid():
    paths = _profiles()
    assert paths, "no profiles found"
    for path in paths:
        data = json.loads(path.read_text())          # raises on malformed JSON
        assert data["name"] == path.stem, \
            f"{path.name}: name field {data['name']!r} does not match filename"


def test_profile_schema():
    for path in _profiles():
        data = json.loads(path.read_text())
        missing = REQUIRED_KEYS - set(data.keys())
        assert not missing, f"{path.name} missing keys: {missing}"
        assert isinstance(data["persona"], str) and len(data["persona"]) > 50, \
            f"{path.name} persona too short"
        assert 0 <= data["schedule_hour"] <= 23
        assert 0 <= data["schedule_minute"] <= 59


def test_persona_type_is_a_valid_wire_value():
    for path in _profiles():
        data = json.loads(path.read_text())
        assert data["persona_type"] in VALID_PERSONA_TYPES, \
            f"{path.name} has invalid persona_type {data['persona_type']!r}"


def test_no_profile_carries_an_api_key_field():
    """Profiles are tracked in git. An api_key field here is how a live key
    reaches a public commit — the loader reads the environment instead."""
    for path in _profiles():
        assert "api_key" not in json.loads(path.read_text()), \
            f"{path.name} must not carry an api_key field; use STOCKBEAT_API_KEY_<NAME>"


def test_no_duplicate_schedules():
    schedules = []
    for path in _profiles():
        data = json.loads(path.read_text())
        schedules.append((data["schedule_hour"], data["schedule_minute"]))
    assert len(schedules) == len(set(schedules)), "Duplicate schedule times"


def test_unique_personas():
    personas = [json.loads(p.read_text())["persona"] for p in _profiles()]
    assert len(personas) == len(set(personas)), "Duplicate personas"


# --- Loader ---

def test_load_profile_reads_key_from_env(monkeypatch):
    monkeypatch.setenv("STOCKBEAT_API_KEY_TECHNICAL_EXAMPLE", "sk_live_fromenv")
    p = load_profile("technical-example")
    assert p["name"] == "technical-example"
    assert p["api_key"] == "sk_live_fromenv"
    assert p["persona_type"] == "Technical"


def test_load_profile_missing_key_raises(monkeypatch):
    monkeypatch.delenv("STOCKBEAT_API_KEY_TECHNICAL_EXAMPLE", raising=False)
    with pytest.raises(ValueError, match="STOCKBEAT_API_KEY_TECHNICAL_EXAMPLE"):
        load_profile("technical-example")


def test_load_profile_unknown_raises():
    with pytest.raises(FileNotFoundError):
        load_profile("nonexistent-agent")


def test_load_profile_risk_overrides(monkeypatch):
    monkeypatch.setenv("STOCKBEAT_API_KEY_TECHNICAL_EXAMPLE", "k")
    p = load_profile("technical-example")
    assert p["risk_overrides"]["stop_loss_default_pct"] == 0.06


# Keep the old names as aliases so any external references still resolve
def test_all_profiles_exist():
    """All five example profiles are present on disk."""
    names = [
        "technical-example", "fundamental-example", "sentiment-example",
        "macro-example", "hybrid-example",
    ]
    for name in names:
        path = PROFILES_DIR / f"{name}.json"
        assert path.exists(), f"Missing profile: {path}"


def test_load_profile_returns_all_keys(monkeypatch):
    monkeypatch.setenv("STOCKBEAT_API_KEY_TECHNICAL_EXAMPLE", "sk_test")
    p = load_profile("technical-example")
    assert p["name"] == "technical-example"
    assert p["display_name"] == "Technical Example"
    assert "persona" in p
    assert "schedule_hour" in p
