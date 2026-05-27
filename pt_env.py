"""Centralized environment, path resolution, and config management for PowerTrader_AI.

All file paths used for inter-process communication live here so that
pt_hub, pt_web, pt_models, and pt_controller share a single source of truth.

Config is read from pt_config.json (formerly gui_settings.json).  Callers should
use get_config() for a cached, mtime-fresh snapshot and set_config() to write changes.
"""

import json
import os
import threading
from datetime import datetime, timezone as _tz
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------

def utcnow() -> str:
    """Current UTC time as ISO-8601 string: '2026-05-22T14:32:00Z'"""
    return datetime.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def ts_to_utc(ts: float) -> str:
    """Convert Unix float timestamp to ISO-8601 UTC string."""
    return datetime.fromtimestamp(ts, _tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def utc_to_ts(s: str) -> float:
    """Convert ISO-8601 UTC string to Unix float timestamp."""
    return datetime.fromisoformat(s.rstrip("Z")).replace(tzinfo=_tz.utc).timestamp()


# ---------------------------------------------------------------------------
# Timeframe constants
# ---------------------------------------------------------------------------

TRAIN_TF_NAMES   = ["1hour", "2hour", "4hour", "8hour", "12hour", "1day", "1week"]
TRAIN_TF_MINUTES = [60, 120, 240, 480, 720, 1440, 10080]
TRAIN_TF_CCXT    = {60: "1h",    120: "2h",   240: "4h",   480: "8h",
                    720: "12h",  1440: "1d",  10080: "1w"}
TF_MINUTE_TO_NAME_MAP  = dict(zip(TRAIN_TF_MINUTES, TRAIN_TF_NAMES))

DISPLAY_TIMEFRAMES = [
    "1min", "5min", "15min", "30min",
    "1hour", "2hour", "4hour", "8hour", "12hour", "1day", "1week",
]
VALID_DATA_SOURCES = ["kucoin_local", "kucoin", "binance", "kraken", "kucoin_live_api"]
_VALID_PRICE_SOURCES = ["kraken", "kucoin"]


# ---------------------------------------------------------------------------
# CONFIG_DEFAULTS — canonical starting values; also used to bootstrap a missing file
# ---------------------------------------------------------------------------

CONFIG_DEFAULTS: dict[str, Any] = {
    # Coins
    "coins": ["BTC", "ETH", "BNB", "PAXG", "SOL", "XRP", "DOGE", "TRX", "HYPE",
              "ADA", "XMR", "XLM", "LINK", "ZEC", "AVAX", "DOT", "CRO", "BCH",
              "LTC", "SUI", "SHIB", "TAO", "MNT", "UNI", "NEAR", "POL", "ATOM",
              "ALGO", "HBAR", "ONDO"],
    "trading_mode": "demo",
    "exchange": "",
    "shadow_sync_exchange": "",
    "excluded_coins": [],
    "long_term_holdings": ["BTC", "ETH", "SOL"],

    # Trading
    "trade_start_level": 2,
    "start_allocation_pct": 1.0,
    "dca_multiplier": 1.0,
    "dca_levels": [-5, -10, -20, -30, -40, -50],
    "max_dca_buys_per_24h": 1,

    # Trailing profit
    "pm_start_pct_no_dca": 3.0,
    "pm_start_pct_with_dca": 3.0,
    "trailing_gap_pct": 0.5,

    # LTH
    "lth_profit_alloc_pct": 50.0,

    # Data sources
    "live_price_source": "kucoin",
    "training_data_source": "kucoin_local",

    # Paths / scripts
    "thinker_dir": "state",
    "hub_data_dir": "",
    "kucoin_local_data_dir": "state/historic_data/kucoin",
    "script_thinker": "pt_thinker.py",
    "script_neural_trainer": "pt_trainer.py",
    "script_trader": "pt_trader.py",

    # Timeframes
    "timeframes": DISPLAY_TIMEFRAMES,
    "default_timeframe": "1hour",

    # UI preferences
    "ui_refresh_seconds": 10,
    "chart_refresh_seconds": 4,
    "candles_limit": 300,
    "ui_font_size": 16,
    "ui_log_font_size": 11,

    # Notifications
    "notifications_enabled": True,
    "ntfy_url": "",
    "ntfy_summary_interval_minutes": 60,

    # Shadow / Demo exchange
    "shadow_starting_usd": 0,
    "demo_starting_usd": 10000,

    # Startup
    "auto_start_scripts": False,

    # Training
    "training_staleness_days": 14,

    # Data manager
    "kucoin_local_topup_interval_hours": 6,
}


# ---------------------------------------------------------------------------
# CONFIG_SCHEMA — validation rules and UI metadata for each field
#
# Keys per entry:
#   type       : "int" | "float" | "list_float" | "list_str" | "enum" | "bool" | "str"
#   min / max  : numeric bounds (inclusive)
#   each_max   : per-element bound for list_float (e.g. each_max=0 → all negative)
#   min_len    : minimum list length
#   options    : valid choices for enum
#   label      : human-readable field name for the UI
#   hint       : helper text shown below the input
#   group      : UI group heading (None = internal, not shown in settings pane)
# ---------------------------------------------------------------------------

CONFIG_SCHEMA: dict[str, dict] = {
    # ── General ───────────────────────────────────────────────────────────
    "coins": {
        "type": "list_str", "min_len": 1,
        "label": "Active Coins",
        "hint": "Comma-separated symbols to trade (e.g. BTC, ETH, SOL)",
        "group": "General",
    },
    "trading_mode": {
        "type": "enum", "options": ["demo", "trading"],
        "ui_widget": "radio",
        "label": "Mode",
        "hint": "Demo: frictionless simulated account. Trading: live exchange(s) with shadow baseline.",
        "group": "General",
    },
    "exchange": {
        "type": "str",
        "label": "Real Exchange",
        "hint": "Single real exchange for Trading mode (e.g. kraken). Greyed out in Demo mode.",
        "group": "General",
    },
    "excluded_coins": {
        "type": "list_str",
        "label": "Excluded Coins",
        "hint": "Coins excluded from bot trading (still tracked)",
        "group": "General",
    },
    "live_price_source": {
        "type": "enum", "options": _VALID_PRICE_SOURCES,
        "label": "Live Price Source",
        "hint": "Exchange used for live price feeds",
        "group": "General",
    },
    # ── Trading ───────────────────────────────────────────────────────────
    "trade_start_level": {
        "type": "int", "min": 1, "max": 11,
        "label": "Trade Start Level",
        "hint": "Min long-voting timeframes required to open a new position (1–11)",
        "group": "Trading",
    },
    "start_allocation_pct": {
        "type": "float", "min": 0.01,
        "label": "Start Allocation %",
        "hint": "% of buying power used for the initial entry (e.g. 1 = 1%)",
        "group": "Trading",
    },
    "dca_multiplier": {
        "type": "float", "min": 0,
        "label": "DCA Multiplier",
        "hint": "Each DCA buy = account value × Start Allocation % × this multiplier. 1.0 = same size as the opening trade, 2.0 = double. Set to 0 to disable DCA.",
        "group": "Trading",
    },
    "dca_levels": {
        "type": "list_float", "each_max": 0, "min_len": 1,
        "label": "DCA Levels (%)",
        "hint": "Negative price-drop % thresholds for each DCA buy (e.g. -5, -10, -20, -30)",
        "group": "Trading",
    },
    "max_dca_buys_per_24h": {
        "type": "int", "min": 0,
        "label": "Max DCA Buys / 24 h",
        "hint": "Maximum number of DCA top-ups per coin per 24-hour window (0 = unlimited)",
        "group": "Trading",
    },

    # ── Trailing Profit ───────────────────────────────────────────────────
    "pm_start_pct_no_dca": {
        "type": "float", "min": 0,
        "label": "PM Start % (no DCA)",
        "hint": "Profit % needed to activate the trailing stop when no DCA was used",
        "group": "Trailing Profit",
    },
    "pm_start_pct_with_dca": {
        "type": "float", "min": 0,
        "label": "PM Start % (with DCA)",
        "hint": "Profit % needed to activate the trailing stop when DCA was used",
        "group": "Trailing Profit",
    },
    "trailing_gap_pct": {
        "type": "float", "min": 0,
        "label": "Trailing Gap %",
        "hint": "Sell line trails peak by this %. E.g. 0.5: peak=$100 → sell=$99.50",
        "group": "Trailing Profit",
    },

    # ── Long-Term Holdings ────────────────────────────────────────────────
    "long_term_holdings": {
        "type": "list_str",
        "label": "LTH Coins",
        "hint": "Coins held long-term; realised profits may auto-buy into these",
        "group": "Long-Term Holdings",
    },
    "lth_profit_alloc_pct": {
        "type": "float", "min": 0, "max": 100,
        "label": "LTH Profit Allocation %",
        "hint": "% of each realised profit automatically reinvested into LTH coins (0 = disabled)",
        "group": "Long-Term Holdings",
    },

    # ── Shadow / Demo Exchange ────────────────────────────────────────────
    "demo_starting_usd": {
        "type": "float", "min": 0,
        "label": "Demo Starting USD",
        "hint": "Starting balance for the frictionless demo account",
        "group": "Shadow Exchange",
    },
    "shadow_starting_usd": {
        "type": "float", "min": 0,
        "label": "Shadow Starting USD",
        "hint": "Starting balance for the shadow account in trading mode (0 = auto-sync from shadow_sync_exchange on first run)",
        "group": "Shadow Exchange",
    },
    "shadow_sync_exchange": {
        "type": "str",
        "label": "Sync Source Exchange",
        "hint": "Real exchange to sync the shadow starting balance from (leave blank to use the configured exchange)",
        "group": "Shadow Exchange",
    },

    # ── UI Preferences ────────────────────────────────────────────────────
    "default_timeframe": {
        "type": "enum", "options": DISPLAY_TIMEFRAMES,
        "label": "Default Chart Timeframe",
        "hint": "Timeframe shown when opening a coin chart",
        "group": "UI Preferences",
    },
    "ui_refresh_seconds": {
        "type": "int", "min": 1, "max": 60,
        "label": "Dashboard Refresh (s)",
        "hint": "How often the dashboard auto-refreshes (1–60 s)",
        "group": "UI Preferences",
    },
    "chart_refresh_seconds": {
        "type": "int", "min": 1, "max": 300,
        "label": "Chart Refresh (s)",
        "hint": "How often the coin chart polls for new candles (1–300 s)",
        "group": "UI Preferences",
    },
    "candles_limit": {
        "type": "int", "min": 50, "max": 1000,
        "label": "Chart Candle Count",
        "hint": "Number of candles to load on the chart (50–1000)",
        "group": "UI Preferences",
    },
    "ui_font_size": {
        "type": "int", "min": 10, "max": 24,
        "label": "Font Size (px)",
        "hint": "Dashboard font size in pixels (10–24)",
        "group": "UI Preferences",
    },
    "ui_log_font_size": {
        "type": "int", "min": 8, "max": 20,
        "label": "Log Font Size (px)",
        "hint": "Font size for log output panels in pixels (8–20)",
        "group": "UI Preferences",
    },

    # ── Notifications ─────────────────────────────────────────────────────
    "notifications_enabled": {
        "type": "bool",
        "label": "Notifications Enabled",
        "hint": "Master switch — turn off to suppress all push notifications.",
        "group": "Notifications",
    },
    "ntfy_url": {
        "type": "str",
        "label": "ntfy URL",
        "hint": "Full ntfy topic URL for push notifications, e.g. https://ntfy.sh/my-topic. Leave blank to disable.",
        "group": "Notifications",
    },
    "ntfy_summary_interval_minutes": {
        "type": "int", "min": 0, "max": 1440,
        "label": "Summary Interval (min)",
        "hint": "How often to send the positions summary notification (5–1440 min). Set to 0 to disable.",
        "group": "Notifications",
    },

    # ── Startup ───────────────────────────────────────────────────────────
    "auto_start_scripts": {
        "type": "bool",
        "label": "Auto-start Scripts",
        "hint": "Automatically start the thinker and traders when the app launches",
        "group": "Startup",
    },
    # ── Training ─────────────────────────────────────────────────────────
    "training_data_source": {
        "type": "enum", "options": VALID_DATA_SOURCES,
        "label": "Training Data Source",
        "hint": "Data source for the neural trainer",
        "group": "Training",
    },
    "training_staleness_days": {
        "type": "int", "min": 1,
        "label": "Training Staleness (days)",
        "hint": "Coins whose training is older than this are treated as untrained",
        "group": "Training",
    },

    # ── Data Manager ──────────────────────────────────────────────────────
    "kucoin_local_data_dir": {
        "type": "str",
        "label": "Local Data Directory",
        "hint": "Path to ArcticDB store for local KuCoin OHLCV data (relative to project dir or absolute)",
        "group": "Data Manager",
    },
    "kucoin_local_topup_interval_hours": {
        "type": "int", "min": 1, "max": 24,
        "label": "Topup Interval (hours)",
        "hint": "How often the data manager runs a topup (e.g. 6 = every 6 hours)",
        "group": "Data Manager",
    },

    # ── Internal (no UI group) ────────────────────────────────────────────
    "thinker_dir":           {"type": "str",       "group": None},
    "hub_data_dir":          {"type": "str",       "group": None},
    "script_thinker":        {"type": "str",       "group": None},
    "script_neural_trainer": {"type": "str",       "group": None},
    "script_trader":         {"type": "str",       "group": None},
    "timeframes":            {"type": "list_str",  "group": None},
}


def _validate_field(key: str, value: Any, rule: dict) -> str | None:
    """Return an error string if value violates rule, else None."""
    t = rule.get("type", "str")
    try:
        if t == "int":
            v = int(float(value))
            mn, mx = rule.get("min"), rule.get("max")
            if mn is not None and v < mn:
                return f"Must be ≥ {mn}"
            if mx is not None and v > mx:
                return f"Must be ≤ {mx}"

        elif t == "float":
            v = float(str(value).replace("%", "").strip())
            mn, mx = rule.get("min"), rule.get("max")
            if mn is not None and v < mn:
                return f"Must be ≥ {mn}"
            if mx is not None and v > mx:
                return f"Must be ≤ {mx}"

        elif t == "list_float":
            if isinstance(value, str):
                items = [x.strip() for x in value.split(",") if x.strip()]
            else:
                items = list(value)
            parsed = [float(x) for x in items]
            min_len = rule.get("min_len", 0)
            if len(parsed) < min_len:
                return f"Need at least {min_len} value(s)"
            each_min = rule.get("each_min")
            if each_min is not None and any(x < each_min for x in parsed):
                return f"All values must be ≥ {each_min}"
            each_max = rule.get("each_max")
            if each_max is not None and any(x > each_max for x in parsed):
                return f"All values must be ≤ {each_max}"

        elif t == "list_str":
            if isinstance(value, str):
                items = [x.strip() for x in value.split(",") if x.strip()]
            else:
                items = [str(x).strip() for x in value if str(x).strip()]
            min_len = rule.get("min_len", 0)
            if len(items) < min_len:
                return f"Need at least {min_len} item(s)"

        elif t == "enum":
            options = rule.get("options", [])
            if str(value) not in options:
                return f"Must be one of: {', '.join(options)}"

        elif t == "bool":
            if not isinstance(value, bool):
                return "Must be true or false"

    except (ValueError, TypeError) as e:
        return f"Invalid value: {e}"

    return None


# ---------------------------------------------------------------------------
# PTEnv
# ---------------------------------------------------------------------------

class PTEnv:
    """Resolves all PowerTrader file paths and manages pt_config.json."""

    def __init__(self, project_dir: str | Path | None = None,
                 config_path: str | Path | None = None):
        if project_dir is None:
            project_dir = Path(__file__).resolve().parent
        self.project_dir = Path(project_dir).resolve()

        # Accept either POWERTRADER_CONFIG (new) or POWERTRADER_GUI_SETTINGS (legacy)
        env_override = (
            os.environ.get("POWERTRADER_CONFIG")
            or os.environ.get("POWERTRADER_GUI_SETTINGS")
        )
        if config_path is not None:
            self.config_path = Path(config_path).resolve()
        elif env_override:
            self.config_path = Path(env_override).resolve()
        else:
            # Prefer pt_config.json; fall back to gui_settings.json for compat
            preferred = self.project_dir / "pt_config.json"
            legacy = self.project_dir / "gui_settings.json"
            self.config_path = preferred if preferred.exists() else legacy

        self._lock = threading.Lock()
        self._config_mtime: float | None = None
        self._config_cache: dict | None = None

        # Derived path fields (updated whenever config changes)
        self._main_dir: Path = self.project_dir / "state"
        self._hub_dir: Path = self._main_dir / "hub_data"

        # Initial load
        self.get_config()

    # ── Public config API ─────────────────────────────────────────────────

    def get_config(self) -> dict:
        """Return a fresh copy of config, reloading from disk if the file changed."""
        with self._lock:
            return self._read_config_locked()

    def set_config(self, patch: dict) -> None:
        """Validate patch, merge with current config, and write atomically."""
        errors: dict[str, str] = {}
        for key, value in patch.items():
            rule = CONFIG_SCHEMA.get(key)
            if rule is None:
                continue
            err = _validate_field(key, value, rule)
            if err:
                errors[key] = err
        if errors:
            raise ValueError(f"Config validation failed: {errors}")

        with self._lock:
            current = self._read_config_locked()
            merged = {**current, **patch}
            # Write directly to config_path. Rename/move to a bind-mounted file
            # fails on Docker with EBUSY (errno 16) even with shutil.move, so we
            # skip the temp-file pattern here. The lock ensures thread safety.
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(merged, f, indent=2)
            # Invalidate cache so next get_config() re-reads
            self._config_mtime = None
            self._config_cache = None

    def _read_config_locked(self) -> dict:
        """Must be called with self._lock held."""
        if self.config_path.exists():
            mtime = self.config_path.stat().st_mtime
            if mtime != self._config_mtime:
                try:
                    with open(self.config_path, encoding="utf-8") as f:
                        raw = json.load(f) or {}
                except Exception:
                    raw = {}
                self._config_cache = {**CONFIG_DEFAULTS, **raw}
                self._config_mtime = mtime
                self._update_derived_paths(self._config_cache)
        elif self._config_cache is None:
            self._config_cache = dict(CONFIG_DEFAULTS)
            self._update_derived_paths(self._config_cache)
        return dict(self._config_cache)

    def _update_derived_paths(self, cfg: dict) -> None:
        raw_main = str(cfg.get("thinker_dir") or "state").strip() or "state"
        if os.path.isabs(raw_main):
            self._main_dir = Path(raw_main)
        else:
            self._main_dir = (self.project_dir / raw_main).resolve()

        raw_hub = str(cfg.get("hub_data_dir") or "").strip()
        if raw_hub:
            self._hub_dir = (
                Path(raw_hub) if os.path.isabs(raw_hub)
                else (self.project_dir / raw_hub).resolve()
            )
        else:
            self._hub_dir = self._main_dir / "hub_data"

    # Backward-compat alias used by a few older call sites
    def reload(self) -> None:
        with self._lock:
            self._config_mtime = None
            self._config_cache = None
        self.get_config()

    # ── Properties ───────────────────────────────────────────────────────

    @property
    def config(self) -> dict:
        return self.get_config()

    # Legacy alias kept so existing callers (pt_models, pt_web) don't break
    @property
    def settings(self) -> dict:
        return self.get_config()

    # Legacy alias for config_path
    @property
    def settings_path(self) -> Path:
        return self.config_path

    @property
    def trading_mode(self) -> str:
        return (self.get_config().get("trading_mode") or "demo").strip().lower()

    @property
    def exchange(self) -> str:
        """Real exchange key for Trading mode (e.g. 'kraken'), or '' if none configured."""
        return str(self.get_config().get("exchange") or "").strip().lower()

    @property
    def coins(self) -> list[str]:
        return list(self.get_config().get("coins") or [])

    @property
    def main_dir(self) -> Path:
        # Ensure derived paths are current
        self.get_config()
        return self._main_dir

    @property
    def hub_data_dir(self) -> Path:
        self.get_config()
        return self._hub_dir

    @property
    def historic_data_dir(self) -> Path:
        raw = str(self.get_config().get("kucoin_local_data_dir") or "state/historic_data/kucoin").strip()
        p = Path(raw)
        return p if p.is_absolute() else (self.project_dir / p).resolve()

    @property
    def coins_dir(self) -> Path:
        return self.main_dir / "coins"

    def coin_dir(self, coin: str) -> Path:
        return self.coins_dir / coin

    # ── Per-coin signal/training files ────────────────────────────────────

    def long_signal_path(self, coin: str) -> Path:
        return self.coin_dir(coin) / "long_dca_signal.txt"

    def short_signal_path(self, coin: str) -> Path:
        return self.coin_dir(coin) / "short_dca_signal.txt"

    def thinker_state_path(self, coin: str) -> Path:
        return self.coin_dir(coin) / "thinker_state.json"

    def training_data_path(self, coin: str) -> Path:
        return self.coin_dir(coin) / "training_data.json"

    def trainer_state_path(self, coin: str) -> Path:
        return self.coin_dir(coin) / "trainer_state.json"

    def killer_path(self, coin: str) -> Path:
        return self.coin_dir(coin) / "killer.txt"

    # ── Hub-level state files ─────────────────────────────────────────────

    def hub_data_xk_dir(self, xk: str) -> Path:
        """Return (and create) the per-exchange subdirectory under hub_data/exchanges/."""
        d = self._hub_dir / "exchanges" / xk
        d.mkdir(parents=True, exist_ok=True)
        return d

    def exchange_state_path(self, xk: str) -> Path:
        return self.hub_data_xk_dir(xk) / "exchange_state.json"

    def thinker_ready_path(self) -> Path:
        return self._hub_dir / "thinker_ready.json"

    def data_manager_status_path(self) -> Path:
        return self._hub_dir / "data_manager_status.json"

    def errors_path(self) -> Path:
        return self._hub_dir / "errors.jsonl"

    def debug_trade_dumps_dir(self) -> Path:
        return self._hub_dir / "debug_trade_dumps"

    def logs_dir(self) -> Path:
        return self._hub_dir / "logs"

    def thinker_autorestart_path(self) -> Path:
        return self._hub_dir / "thinker_autorestart_state.json"

    def ema200_path(self) -> Path:
        return self._hub_dir / "lth_daily_ema200.json"

    def trader_status_path(self, exchange: str) -> Path:
        return self.hub_data_xk_dir(exchange) / "trader_status.json"

    def trade_history_path(self, exchange: str) -> Path:
        return self.hub_data_xk_dir(exchange) / "trade_history.jsonl"

    def pnl_ledger_path(self, exchange: str) -> Path:
        return self.hub_data_xk_dir(exchange) / "pnl_ledger.json"

    def account_history_path(self, exchange: str) -> Path:
        return self.hub_data_xk_dir(exchange) / "account_value_history.jsonl"

    def bot_order_ids_path(self, exchange: str) -> Path:
        return self.hub_data_xk_dir(exchange) / "bot_order_ids.json"

    # ── Script paths ──────────────────────────────────────────────────────

    def script_path(self, key: str) -> Path:
        mapping = {
            "thinker": "script_thinker",
            "trainer": "script_neural_trainer",
            "trader":  "script_trader",
        }
        cfg_key = mapping.get(key, key)
        filename = self.get_config().get(cfg_key, f"pt_{key}.py")
        p = Path(filename)
        return p if p.is_absolute() else self.project_dir / p
