import json
from pathlib import Path

PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"
REQUIRED_KEYS = {"name", "display_name", "api_key", "persona", "schedule_hour", "schedule_minute"}


def test_all_profiles_exist():
    names = ["momentum-mike", "value-vicky", "steady-eddie", "sector-sam", "contrarian-cathy"]
    for name in names:
        path = PROFILES_DIR / f"{name}.json"
        assert path.exists(), f"Missing profile: {path}"


def test_profile_schema():
    for path in PROFILES_DIR.glob("*.json"):
        data = json.loads(path.read_text())
        missing = REQUIRED_KEYS - set(data.keys())
        assert not missing, f"{path.name} missing keys: {missing}"
        assert isinstance(data["persona"], str) and len(data["persona"]) > 50, \
            f"{path.name} persona too short"
        assert 0 <= data["schedule_hour"] <= 23
        assert 0 <= data["schedule_minute"] <= 59


def test_no_duplicate_schedules():
    schedules = []
    for path in PROFILES_DIR.glob("*.json"):
        data = json.loads(path.read_text())
        schedules.append((data["schedule_hour"], data["schedule_minute"]))
    assert len(schedules) == len(set(schedules)), "Duplicate schedule times"


def test_unique_personas():
    personas = []
    for path in PROFILES_DIR.glob("*.json"):
        data = json.loads(path.read_text())
        personas.append(data["persona"])
    assert len(personas) == len(set(personas)), "Duplicate personas"


# --- Tests for profile loader ---
from profiles import load_profile


def test_load_profile_returns_all_keys():
    p = load_profile("momentum-mike")
    assert p["name"] == "momentum-mike"
    assert p["display_name"] == "Momentum Mike"
    assert "persona" in p
    assert "schedule_hour" in p


def test_load_profile_unknown_raises():
    import pytest
    with pytest.raises(FileNotFoundError):
        load_profile("nonexistent-agent")


def test_load_profile_risk_overrides():
    p = load_profile("momentum-mike")
    assert p["risk_overrides"]["cash_target_default"] == 15
