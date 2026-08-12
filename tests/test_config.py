import importlib
from pathlib import Path

import config


def test_env_bool_default_used_when_unset(monkeypatch):
    monkeypatch.delenv("SOME_FLAG", raising=False)
    assert config._env_bool("SOME_FLAG", True) is True
    assert config._env_bool("SOME_FLAG", False) is False


def test_env_bool_falsey_strings(monkeypatch):
    for raw in ("false", "FALSE", "0", "no", ""):
        monkeypatch.setenv("SOME_FLAG", raw)
        assert config._env_bool("SOME_FLAG", True) is False


def test_env_bool_truthy_strings(monkeypatch):
    for raw in ("true", "1", "yes", "on"):
        monkeypatch.setenv("SOME_FLAG", raw)
        assert config._env_bool("SOME_FLAG", False) is True


def test_dry_run_defaults_on(monkeypatch):
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: None)
    reloaded = importlib.reload(config)
    assert reloaded.DRY_RUN is True


def test_risk_range_constants_present():
    assert config.CASH_TARGET_MIN == 10
    assert config.CASH_TARGET_MAX == 50
    assert config.CASH_TARGET_DEFAULT == 30
    assert config.POSITION_PCT_MIN == 5
    assert config.POSITION_PCT_MAX == 20
    assert config.POSITION_PCT_DEFAULT == 10
    assert config.STOP_LOSS_MIN_PCT == 0.03
    assert config.STOP_LOSS_MAX_PCT == 0.15
    assert config.STOP_LOSS_DEFAULT_PCT == 0.06


def test_risk_constants_present():
    assert config.CASH_TARGET_DEFAULT == 30
    assert config.MIN_TRADE_USD == 1000
    assert config.TARGET_MAX_UPSIDE == 2.5
    assert isinstance(config.SKELETON_TICKERS, list) and config.SKELETON_TICKERS


def test_get_logger_writes_to_dated_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LOG_DIR", tmp_path)
    # force re-create handlers
    logger = config.get_logger()
    for h in list(logger.handlers):
        logger.removeHandler(h)
    logger = config.get_logger()
    logger.info("hello-test")
    files = list(tmp_path.glob("*.log"))
    assert files, "expected a dated log file"
    assert "hello-test" in files[0].read_text()


def test_stockbeat_base_url_defaults_to_public_host(monkeypatch):
    monkeypatch.delenv("STOCKBEAT_BASE_URL", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **kw: None)
    reloaded = importlib.reload(config)
    assert reloaded.STOCKBEAT_BASE_URL == "https://stockbeat.io"


def test_no_internal_hosts_in_config():
    """Internal infrastructure hostnames must not ship publicly."""
    source = Path(config.__file__).read_text()
    for host in ("appspot.com", "stockbeat.app"):
        assert host not in source, f"Internal host {host!r} found in config.py"
