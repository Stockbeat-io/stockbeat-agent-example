import logging
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent
LOG_DIR = ROOT / "logs"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("false", "0", "no", "")


# --- Connections ---
STOCKBEAT_API_KEY = os.getenv("STOCKBEAT_API_KEY", "")
STOCKBEAT_BASE_URL = os.getenv("STOCKBEAT_BASE_URL", "https://stockbeat.io")

# --- LLM provider ---
# "ollama" (local, free, the default), "openai-compatible" (OpenAI, LM Studio,
# vLLM, Groq, OpenRouter, Together — they differ only in base_url), "anthropic"
# (pay-as-you-go API key), "claude-cli" (shells out to an already-logged-in
# Claude Code CLI, so a Pro/Max subscription works with no API key at all).
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "ollama").strip().lower()
LLM_API_KEY = os.getenv("LLM_API_KEY", "")

# claude-cli takes no base_url — it does not speak HTTP. Its model default is a
# full id rather than an alias like "sonnet" because LLM_MODEL is also what gets
# stamped into StockBeat's `llm_model` field, where "sonnet" would record
# nothing useful about which model actually made the call.
_PROVIDER_DEFAULTS = {
    "ollama": ("http://localhost:11434", "llama3.1"),
    "openai-compatible": ("https://api.openai.com", ""),
    "anthropic": ("https://api.anthropic.com", "claude-sonnet-4-6"),
    "claude-cli": ("", "claude-sonnet-4-6"),
    # No model default on purpose: Cursor's model IDs vary by account, and
    # LLM_MODEL is what gets stamped onto every trade as `llm_model`. Guessing
    # would either fail mid-run or record a lie about which model decided.
    "cursor-cli": ("", ""),
    "codex-cli": ("", "gpt-5.1-codex-max"),
}
_base_default, _model_default = _PROVIDER_DEFAULTS.get(LLM_PROVIDER, ("", ""))

LLM_BASE_URL = os.getenv("LLM_BASE_URL") or _base_default
LLM_MODEL = os.getenv("LLM_MODEL") or _model_default
# Overrides the binary name for any CLI provider; also accepts an absolute path.
# Not polish: Cursor renamed its binary from `cursor-agent` to `agent` and kept
# both working, so which name exists depends on how old the install is.
LLM_CLI_BINARY = os.getenv("LLM_CLI_BINARY", "").strip()
FRED_API_KEY = os.getenv("FRED_API_KEY", "")

# StockBeat records which model made each trading decision and rejects these
# actions without `llm_model`. The clients stamp it from LLM_MODEL inside
# submit_trade rather than at the call sites, so a new caller cannot forget it:
# the client is Python, and a missing required field never self-corrects the way
# a prompt would. CANCEL_ORDER is excluded — it is not a new trading decision.
ACTIONS_REQUIRING_LLM_MODEL = frozenset(
    {"BUY", "SELL", "CLOSE_STOCK", "BUY_LIMIT", "STOP_LOSS"})

# DRY_RUN gates all StockBeat write (POST) calls. Default ON.
DRY_RUN = _env_bool("DRY_RUN", True)

# --- Agent identity (set by --agent CLI flag) ---
AGENT_NAME = os.getenv("STOCKBEAT_AGENT_NAME", "default")
AGENT_PERSONA = ""  # set at runtime by main.py


def agent_memory_dir() -> Path:
    """Return the memory directory for the current agent."""
    return Path.home() / ".stockbeat-agent" / AGENT_NAME / "memory"


def agent_log_dir() -> Path:
    """Return the log directory for the current agent (with mkdir)."""
    d = LOG_DIR / AGENT_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def cli_workspace_dir() -> Path:
    """An empty directory for the subscription-CLI providers to run in.

    Codex reads AGENTS.md and Cursor reads .cursor/rules from their working
    directory. A cron run launched from this repo would otherwise drag
    trading-agent instructions into four text-generation calls that need none of
    it — cost, latency, and a real risk of the CLI trying to act on them.
    Neither CLI has a flag that disables project context, so they get pointed
    somewhere with none. User-global config (~/.codex/AGENTS.md) still applies.
    """
    d = Path.home() / ".stockbeat-agent" / "cli-workspace"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --- Risk ranges (LLM decides within these bounds) ---
CASH_TARGET_MIN = 10            # floor: never 0% cash
CASH_TARGET_MAX = 50            # ceiling: never hoard >50%
CASH_TARGET_DEFAULT = 30        # fallback when Judge omits risk_assessment
POSITION_PCT_MIN = 5            # floor: don't micro-size trades
POSITION_PCT_MAX = 20           # ceiling: never >20% in one stock
POSITION_PCT_DEFAULT = 10       # fallback when Judge omits
STOP_LOSS_MIN_PCT = 0.03        # tightest stop: 3% below entry
STOP_LOSS_MAX_PCT = 0.15        # widest stop: 15% below entry
STOP_LOSS_DEFAULT_PCT = 0.06    # fallback when Judge omits stop_loss_price
MIN_TRADE_USD = 1000
MAX_TRADES_NORMAL = 3
# Calendar days a position must be held before a discretionary SELL is allowed.
# Stop-losses are exempt, so genuine breaks can still be cut immediately.
MIN_HOLDING_DAYS = 3
INITIAL_BUILD_MAX_ACTIONS = 10
INITIAL_BUILD_MIN_TOKENS = 15
TARGET_MIN_UPSIDE = 1.05  # target_price must be >= 5% above entry
TARGET_MAX_UPSIDE = 2.5   # target_price must be <= 2.5x entry
WHY_MIN_LEN = 200
WHY_MAX_LEN = 400
HORIZON_MIN = 1
HORIZON_MAX = 90
COMMENT_MAX_AGE_HOURS = 24
COMMENT_REPLY_MAX_LEN = 300

# --- FRED macro series IDs ---
FRED_SERIES = {
    "fed_funds": "FEDFUNDS",
    "cpi": "CPIAUCSL",
    "ten_year": "DGS10",
    "gdp_growth": "A191RL1Q225SBEA",
}

# --- Walking-skeleton fixed candidate universe (replaced by full S&P 500 later) ---
SKELETON_TICKERS = ["AAPL", "MSFT", "NVDA", "JPM", "XOM", "JNJ", "PG", "WMT"]

# Shortlist sizing for the full screen
PRELIM_BULL = 40
PRELIM_BEAR = 15
SHORTLIST_BULL = 10
SHORTLIST_BEAR = 5
MAX_PER_SECTOR = 3
SCAN_SAMPLE_SIZE = 120


def get_logger() -> logging.Logger:
    logger = logging.getLogger("stockbeat")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s | %(message)s")

        stream = logging.StreamHandler()
        stream.setFormatter(fmt)
        logger.addHandler(stream)

        try:
            from datetime import date
            log_dir = LOG_DIR / AGENT_NAME if AGENT_NAME != "default" else LOG_DIR
            log_dir.mkdir(parents=True, exist_ok=True)
            fileh = logging.FileHandler(log_dir / f"{date.today().isoformat()}.log")
            fileh.setFormatter(fmt)
            logger.addHandler(fileh)
        except Exception:
            pass  # never let logging setup break a run
    return logger
