"""Read-only data models over the PowerTrader file-based state.

Each model reads JSON/text files written by pt_trainer, pt_thinker, or pt_trader
and returns structured Python dicts.  Models cache by file mtime so repeated
reads within the same second are free.
"""

import json
import time
from pathlib import Path

from pt_env import PTEnv, utc_to_ts


def _read_json(path: Path) -> dict | None:
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _norm_ts(record: dict) -> dict:
    """Convert a persisted ISO-8601 'ts' string to a float in-place for callers that do arithmetic."""
    raw = record.get("ts")
    if isinstance(raw, str):
        try:
            record["ts"] = utc_to_ts(raw)
        except Exception:
            record["ts"] = 0.0
    return record


def _read_jsonl(path: Path, limit: int = 250) -> list[dict]:
    try:
        with open(path) as f:
            lines = f.readlines()
        tail = lines if limit <= 0 else (lines[-limit:] if len(lines) > limit else lines)
        out = []
        for line in tail:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return out
    except FileNotFoundError:
        return []


def _read_int(path: Path) -> int:
    try:
        return int(Path(path).read_text().strip())
    except (FileNotFoundError, ValueError):
        return 0


def _read_float_list(path: Path) -> list[float]:
    try:
        text = Path(path).read_text().strip()
        if not text:
            return []
        return [float(x.strip()) for x in text.split(",") if x.strip()]
    except (FileNotFoundError, ValueError):
        return []


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return 0.0


class _MtimeCache:
    """Cache a value keyed on file mtime."""

    def __init__(self):
        self._cache: dict[str, tuple[float, object]] = {}

    def get(self, path: Path, reader):
        key = str(path)
        mt = _mtime(path)
        cached = self._cache.get(key)
        if cached and cached[0] == mt:
            return cached[1]
        val = reader(path)
        self._cache[key] = (mt, val)
        return val


_cache = _MtimeCache()


class CoinModel:
    """Read-only view of a single coin's state."""

    def __init__(self, env: PTEnv, coin: str):
        self.env = env
        self.coin = coin

    def long_signal(self) -> int:
        return _cache.get(self.env.long_signal_path(self.coin), _read_int)

    def short_signal(self) -> int:
        return _cache.get(self.env.short_signal_path(self.coin), _read_int)

    def long_price_levels(self) -> list[float]:
        data = _cache.get(self.env.thinker_state_path(self.coin), _read_json)
        vals = [float(v) for v in data.get("low_bound_prices", []) if v] if data else []
        return sorted(vals, reverse=True)

    def short_price_levels(self) -> list[float]:
        data = _cache.get(self.env.thinker_state_path(self.coin), _read_json)
        vals = [float(v) for v in data.get("high_bound_prices", []) if v] if data else []
        return sorted(vals)

    def training_status(self) -> dict:
        data = _cache.get(self.env.trainer_state_path(self.coin), _read_json)
        return data or {"coin": self.coin, "state": "UNKNOWN"}

    def training_failure(self) -> dict | None:
        data = _cache.get(self.env.trainer_state_path(self.coin), _read_json)
        if not data:
            return None
        failure = data.get("failure", {})
        return failure if failure else None

    def is_trained(self) -> bool:
        data = _cache.get(self.env.trainer_state_path(self.coin), _read_json)
        if not data:
            return False
        raw = data.get("last_training_time", "")
        try:
            ts = utc_to_ts(raw) if "T" in raw else float(raw)
            age_days = (time.time() - ts) / 86400
            if age_days > self.env.get_config()["training_staleness_days"]:
                return False
        except (ValueError, TypeError):
            return False
        return (self.env.training_data_path(self.coin)).exists()

    def last_trained_ts(self) -> float:
        data = _cache.get(self.env.trainer_state_path(self.coin), _read_json)
        if not data:
            return 0.0
        raw = data.get("last_training_time", "")
        try:
            return utc_to_ts(raw) if "T" in raw else float(raw)
        except (ValueError, TypeError):
            return 0.0

    def snapshot(self) -> dict:
        ts = self.training_status()
        return {
            "coin": self.coin,
            "long_signal": self.long_signal(),
            "short_signal": self.short_signal(),
            "long_price_levels": self.long_price_levels(),
            "short_price_levels": self.short_price_levels(),
            "is_trained": self.is_trained(),
            "training_state": ts.get("state", "UNKNOWN"),
            "last_trained_ts": self.last_trained_ts(),
        }


class AccountModel:
    """Read-only view of trader status, positions, P&L, and trade history."""

    def __init__(self, env: PTEnv, exchange: str):
        self.env = env
        self.exchange = exchange

    def trader_status(self) -> dict | None:
        return _cache.get(self.env.trader_status_path(self.exchange), _read_json)

    def account_summary(self) -> dict:
        ts = self.trader_status()
        if not ts or "account" not in ts:
            return {}
        return ts["account"]

    def positions(self) -> dict:
        ts = self.trader_status()
        if not ts or "positions" not in ts:
            return {}
        return {k: v for k, v in ts["positions"].items() if v.get("quantity", 0) > 0}

    def all_positions(self) -> dict:
        ts = self.trader_status()
        if not ts or "positions" not in ts:
            return {}
        return ts["positions"]

    def pnl(self) -> dict:
        data = _cache.get(self.env.pnl_ledger_path(self.exchange), _read_json)
        return data or {}

    def trade_history(self, limit: int = 250) -> list[dict]:
        return [_norm_ts(r) for r in _read_jsonl(self.env.trade_history_path(self.exchange), limit)]

    def account_value_history(self, limit: int = 500) -> list[dict]:
        return [_norm_ts(r) for r in _read_jsonl(self.env.account_history_path(self.exchange), limit)]

    def lth_holdings(self) -> dict[str, dict]:
        """Aggregate LTH holdings from trade history (tag='LTH' buys minus sells)."""
        trades = self.trade_history(limit=0)
        coins: dict[str, dict] = {}
        for tr in trades:
            if (tr.get("tag") or "").upper() != "LTH":
                continue
            side = (tr.get("side") or "").lower()
            if side not in ("buy", "sell"):
                continue
            sym = (tr.get("symbol") or "").upper()
            base = sym.split("_")[0] if sym else ""
            if not base:
                continue
            c = coins.setdefault(base, {"qty": 0.0, "cost_usd": 0.0, "trades": 0})
            qty = float(tr.get("qty") or 0)
            cost = float(tr.get("notional_usd") or 0) + float(tr.get("fees_usd") or 0)
            if side == "buy":
                c["qty"] += qty
                c["cost_usd"] += cost
            else:
                frac = min(1.0, qty / c["qty"]) if c["qty"] > 0 else 1.0
                c["qty"] -= qty
                c["cost_usd"] -= c["cost_usd"] * frac
            c["trades"] += 1
        return {k: v for k, v in coins.items() if v["qty"] > 1e-12}

    def dca_24h_by_coin(self) -> dict[str, int]:
        trades = self.trade_history(limit=0)
        now = time.time()
        window_floor = now - 86400

        last_sell_ts: dict[str, float] = {}
        for tr in trades:
            sym = (tr.get("symbol") or "").upper()
            base = sym.split("_")[0] if sym else ""
            if not base or (tr.get("side") or "").lower() != "sell":
                continue
            ts = float(tr.get("ts", 0))
            if ts > last_sell_ts.get(base, 0):
                last_sell_ts[base] = ts

        out: dict[str, int] = {}
        for tr in trades:
            sym = (tr.get("symbol") or "").upper()
            base = sym.split("_")[0] if sym else ""
            if not base:
                continue
            if (tr.get("side") or "").lower() != "buy":
                continue
            if (tr.get("tag") or "").upper() != "DCA":
                continue
            ts = float(tr.get("ts", 0))
            start_ts = max(window_floor, last_sell_ts.get(base, 0))
            if ts >= start_ts:
                out[base] = out.get(base, 0) + 1
        return out


class SystemModel:
    """Read-only view of system-level state: runner readiness, script health."""

    def __init__(self, env: PTEnv):
        self.env = env

    def thinker_ready(self) -> dict:
        data = _cache.get(self.env.thinker_ready_path(), _read_json)
        return data or {"ready": False, "stage": "unknown"}

    def thinker_autorestart(self) -> dict:
        data = _cache.get(self.env.thinker_autorestart_path(), _read_json)
        return data or {}

    def ema200(self) -> dict:
        data = _cache.get(self.env.ema200_path(), _read_json)
        return data or {}

    def config(self) -> dict:
        return self.env.get_config()
