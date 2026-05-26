"""FastAPI web server for PowerTrader_AI.

Run:

    python pt_web.py [--port 8080]

    conda activate powertrader && cd ~/dev/code/git/PowerTrader_AI && python pt_web.py --port 8088

Provides REST API + WebSocket for real-time updates, serving the web/ frontend.

Modes
-----
Demo    : PaperExchange runs as the primary account, shown as "Demo" in the UI.
Trading : ShadowedExchange wraps each real exchange; shadow account tracks
          frictionless baseline alongside real fills.
"""

import argparse
import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pandas as pd
import pt_errors
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pt_controller import ProcessController
from pt_env import TRAIN_TF_MINUTES, PTEnv, utcnow, utc_to_ts
from pt_models import AccountModel, CoinModel, SystemModel

PROJECT_DIR = Path(__file__).resolve().parent
WEB_DIR = PROJECT_DIR / "web"

_pre = argparse.ArgumentParser(add_help=False)
_pre.add_argument("--config", default=None)
_pre.add_argument("--state_dir", default=None)
_pre_args = _pre.parse_known_args()[0]
_config_path = _pre_args.config
_data_dir = Path(_pre_args.state_dir).resolve() if _pre_args.state_dir else PROJECT_DIR

env = PTEnv(project_dir=_data_dir, config_path=_config_path)
ctrl = ProcessController(env)
app = FastAPI(title="PowerTrader Web")

_exchanges: dict = {}  # real-exchange-key → Exchange (PaperExchange or ShadowedExchange)
_last_mid_price: dict = {}  # coin -> last known good mid price


def _get_exchange(xk: str):
    """Get or create the top-level Exchange for the given key.

    'demo'         → PaperExchange(key='demo')
    '<real_xk>'   → ShadowedExchange(RealExchange, PaperExchange(key='shadow'))
    """
    if xk not in _exchanges:
        try:
            cfg = env.get_config()
            if xk == "demo":
                from exchange_paper import PaperExchange
                _exchanges[xk] = PaperExchange(
                    key="demo",
                    starting_usd=float(cfg.get("demo_starting_usd") or 0),
                    price_source=cfg.get("live_price_source", "kucoin"),
                    state_path=str(env.exchange_state_path("demo")),
                )
            else:
                import importlib
                mod = importlib.import_module(f"exchange_{xk}")
                real = mod.create_exchange()
                from exchange_paper import PaperExchange, ShadowedExchange
                shadow_usd = float(
                    cfg.get("shadow_starting_usd") or cfg.get("control_starting_usd") or 0
                )
                shadow = PaperExchange(
                    key="shadow",
                    starting_usd=shadow_usd,
                    price_source=cfg.get("live_price_source", "kucoin"),
                    state_path=str(env.exchange_state_path("shadow")),
                )
                _exchanges[xk] = ShadowedExchange(real, shadow)
        except Exception as e:
            print(f"[Exchange] failed to create {xk}: {e}")
            pt_errors.emit(
                "pt_web", level="error",
                message=f"Failed to initialise exchange '{xk}': {e}",
                detail="The exchange adapter could not be created. Balance, position, and trading data will be unavailable until the issue is resolved (bad API keys, missing config, or exchange module error).",
            )
            return None
    return _exchanges.get(xk)


def _real_exchange():
    """Return the single ShadowedExchange in Trading mode, or None."""
    return _get_exchange(env.exchange) if env.exchange else None


def _get_account(xk: str):
    """Return the Exchange/PaperExchange account for a given account key."""
    if xk == "demo":
        return _get_exchange("demo")
    if xk == "shadow":
        ex = _real_exchange()
        return ex._shadow if ex else None
    return _real_exchange()  # real exchange key (e.g. "kraken")


def _active_accounts() -> list[str]:
    """All account keys active for the current mode.

    Demo mode    : ["demo"]
    Trading mode : ["kraken", "shadow"]
    """
    if env.trading_mode == "demo":
        return ["demo"]
    ex = _real_exchange()
    return [acc.key for acc in ex.all_accounts()] if ex else []


def _shadow_sync_exchange() -> str:
    """The real exchange to sync the shadow starting balance from."""
    cfg = env.get_config()
    xk = (cfg.get("shadow_sync_exchange") or cfg.get("control_sync_exchange") or "").strip().lower()
    if not xk:
        xk = env.exchange or ""
    return xk


def _shadow_xk() -> str:
    """Key for the paper account in the current mode: 'demo' or 'shadow'."""
    return "demo" if env.trading_mode == "demo" else "shadow"


# ── Static files ──

app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(WEB_DIR / "index.html"))


# ── Helpers ──

def _account_for_exchange(xk: str) -> dict:
    acct = AccountModel(env, xk)
    summary = acct.account_summary()
    pnl = acct.pnl()
    positions = acct.positions()
    unrealized = 0.0
    for sym, pos in positions.items():
        qty = float(pos.get("quantity", 0) or 0)
        cost_basis = float(pos.get("avg_cost_basis", 0) or 0)
        sell_price = float(pos.get("current_sell_price", 0) or 0)
        if qty > 0 and cost_basis > 0 and sell_price > 0:
            unrealized += (sell_price - cost_basis) * qty
    return {
        "exchange": xk,
        "account": summary,
        "pnl": {
            "total_realized_profit_usd": pnl.get("total_realized_profit_usd", 0),
            "unrealized_profit_usd": round(unrealized, 2),
            "lth_profit_bucket_usd": pnl.get("lth_profit_bucket_usd", 0),
        },
    }


def _resample(raw: list[dict], interval_s: int = 600) -> list[dict]:
    """Resample irregular account history to fixed time intervals via linear interpolation."""
    if len(raw) < 2:
        return raw
    t0 = raw[0]["ts"]
    t1 = raw[-1]["ts"]
    first_bin = int(t0 // interval_s) * interval_s + interval_s
    if first_bin > t1:
        return raw
    out = []
    j = 0
    t = first_bin
    while t <= t1:
        while j < len(raw) - 1 and raw[j + 1]["ts"] < t:
            j += 1
        a, b = raw[j], raw[j + 1] if j + 1 < len(raw) else raw[j]
        dt = b["ts"] - a["ts"]
        if dt > 0:
            frac = (t - a["ts"]) / dt
            val = a["total_account_value"] + frac * (b["total_account_value"] - a["total_account_value"])
        else:
            val = a["total_account_value"]
        out.append({"ts": t, "total_account_value": val})
        t += interval_s
    return out


# ── REST API ──

@app.get("/api/status")
async def api_status():
    env.reload()
    sm = SystemModel(env)
    ctrl_status = ctrl.status_summary()
    rr = sm.thinker_ready()

    active = _active_accounts()
    exchanges_data = {}
    for xk in active:
        exchanges_data[xk] = _account_for_exchange(xk)

    dm_state = "Stopped"
    dm_status_path = env.data_manager_status_path()
    if ctrl_status["data_manager_running"] and dm_status_path.exists():
        try:
            dm_state = json.loads(dm_status_path.read_text()).get("state", "Running") or "Running"
        except Exception:
            dm_state = "Running"

    return {
        "system": {
            "thinker_running": ctrl_status["thinker_running"],
            "trader_running": ctrl_status["trader_running"],
            "data_manager_running": ctrl_status["data_manager_running"],
            "data_manager_state": dm_state,
            "traders": ctrl_status.get("traders", {}),
            "thinker_ready": rr.get("ready", False),
            "thinker_stage": rr.get("stage", "unknown"),
            "any_training_running": ctrl_status["any_training_running"],
        },
        "exchanges": exchanges_data,
        "exchange_list": active,
        "trading_mode": env.trading_mode,
        "coins": env.coins,
    }


@app.get("/api/coins")
async def api_coins():
    env.reload()
    coins = []
    ctrl_status = ctrl.status_summary()

    active = _active_accounts()
    _now = time.time()
    _PRICE_STALE_SEC = 300  # ignore prices from status files older than 5 minutes
    status_by_xk = {}
    positions_by_xk = {}
    for xk in active:
        acct = AccountModel(env, xk)
        ts = acct.trader_status() or {}
        status_by_xk[xk] = ts
        positions_by_xk[xk] = ts.get("positions", {})

    for coin in env.coins:
        cm = CoinModel(env, coin)
        snap = cm.snapshot()

        snap["positions"] = {}
        mid_prices = []
        for xk in active:
            ts = status_by_xk[xk]
            pos = positions_by_xk[xk].get(coin, {})
            snap["positions"][xk] = pos if pos.get("quantity", 0) > 0 else None
            _ts_val = ts.get("timestamp", 0) or 0
            _ts_f = utc_to_ts(_ts_val) if isinstance(_ts_val, str) else float(_ts_val)
            age = _now - _ts_f
            if age > _PRICE_STALE_SEC:
                continue
            buy = pos.get("current_buy_price", 0)
            sell = pos.get("current_sell_price", 0)
            mid = (buy + sell) / 2 if (buy and sell) else buy or sell
            if mid > 0:
                mid_prices.append(mid)

        if mid_prices:
            _last_mid_price[coin] = mid_prices[0]
        snap["mid_price"] = _last_mid_price.get(coin, 0)
        snap["training_running"] = ctrl_status["training"].get(coin, {}).get("running", False)
        fail = cm.training_failure()
        if fail and fail.get("exception_type"):
            snap["training_failure"] = fail
        coins.append(snap)
    return {"coins": coins}


@app.get("/api/coins/{coin}")
async def api_coin_detail(coin: str):
    coin = coin.upper()
    cm = CoinModel(env, coin)

    result = {**cm.snapshot(), "positions": {}, "trades": {}}
    for xk in _active_accounts():
        acct = AccountModel(env, xk)
        positions = acct.all_positions()
        pos = positions.get(coin, {})
        result["positions"][xk] = pos if pos.get("quantity", 0) > 0 else None

        trades = acct.trade_history(limit=500)
        coin_trades = [t for t in trades if t.get("symbol", "").startswith(f"{coin}_")]
        result["trades"][xk] = coin_trades[-50:]

    return result


@app.get("/api/positions")
async def api_positions():
    out = {}
    dca = {}
    lth = {}
    for xk in _active_accounts():
        acct = AccountModel(env, xk)
        out[xk] = acct.all_positions()
        dca[xk] = acct.dca_24h_by_coin()
        lth[xk] = acct.lth_holdings()
    return {"positions": out, "dca_24h": dca, "lth": lth}


@app.get("/api/trades")
async def api_trades(limit: int = 250, exchange: str = ""):
    result = {}
    targets = [exchange] if exchange else _active_accounts()
    for xk in targets:
        acct = AccountModel(env, xk)
        result[xk] = acct.trade_history(limit=limit)
    return {"trades": result}


@app.get("/api/account-history")
async def api_account_history(hours: float = 0, start: int = None, end: int = None):
    """Return account value history for all exchanges, resampled to regular intervals."""
    result = {}
    for xk in _active_accounts():
        acct = AccountModel(env, xk)
        raw = acct.account_value_history(limit=0)

        if start and end:
            # Windowed fetch for adaptive zoom: resample to ~400 points in the window
            raw = [r for r in raw if start <= r.get("ts", 0) <= end]
            if len(raw) >= 2:
                span = raw[-1]["ts"] - raw[0]["ts"]
                interval = max(60, int(span / 400))
            else:
                interval = 600
        elif hours > 0:
            cutoff = time.time() - hours * 3600
            raw = [r for r in raw if r.get("ts", 0) >= cutoff]
            if hours <= 24:
                interval = 600
            elif hours <= 168:
                interval = 3600
            else:
                interval = 14400
        else:
            # ALL: target ~400 points across full span
            if len(raw) >= 2:
                span = raw[-1]["ts"] - raw[0]["ts"]
                interval = max(900, int(span / 400))
            else:
                interval = 900

        resampled = _resample(raw, interval) if raw else []
        if raw and resampled and raw[-1]["ts"] > resampled[-1]["ts"]:
            resampled.append(raw[-1])
        result[xk] = resampled
    return {"history": result}


@app.get("/api/comparison")
async def api_comparison():
    """Per-coin comparison across exchanges."""
    env.reload()
    active = _active_accounts()
    coins_out = []
    for coin in env.coins:
        row = {"coin": coin}
        for xk in active:
            acct = AccountModel(env, xk)
            positions = acct.all_positions()
            pos = positions.get(coin, {})
            trades = acct.trade_history(limit=0)
            coin_trades = [t for t in trades if t.get("symbol", "").startswith(f"{coin}_")]
            total_fees = sum(float(t.get("fees_usd", 0) or 0) for t in coin_trades)
            pnl_pct = pos.get("gain_loss_pct_buy", 0) if pos.get("quantity", 0) > 0 else 0
            value = pos.get("value_usd", 0) if pos.get("quantity", 0) > 0 else 0
            row[xk] = {
                "value_usd": value,
                "pnl_pct": pnl_pct,
                "total_fees": total_fees,
                "trade_count": len(coin_trades),
            }
        coins_out.append(row)

    usdt_row = {"coin": "USDT"}
    for xk in active:
        acct = AccountModel(env, xk)
        summary = acct.account_summary()
        usdt_row[xk] = {
            "value_usd": summary.get("buying_power", 0),
            "pnl_pct": 0,
            "total_fees": 0,
            "trade_count": 0,
        }
    coins_out.append(usdt_row)

    coins_out.sort(key=lambda r: r["coin"])

    totals = {}
    for xk in active:
        acct = AccountModel(env, xk)
        pnl = acct.pnl()
        totals[xk] = {
            "realized_profit": pnl.get("total_realized_profit_usd", 0),
            "total_fees": sum(c[xk]["total_fees"] for c in coins_out),
        }

    return {"coins": coins_out, "totals": totals, "exchanges": active}


@app.get("/api/discovered-exchanges")
async def api_discovered_exchanges():
    from exchange_api import discover_exchanges
    return {"exchanges": discover_exchanges()}


@app.get("/api/config")
async def api_config():
    return env.get_config()


@app.get("/api/config/schema")
async def api_config_schema():
    from pt_env import CONFIG_SCHEMA
    return CONFIG_SCHEMA


@app.put("/api/config")
async def api_save_config(data: dict):
    try:
        env.set_config(data)
        return {"ok": True}
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    except OSError as e:
        import pt_errors
        pt_errors.emit(
            "pt_web", level="error",
            message=f"Failed to save config: {e}",
            detail=(
                "The settings file could not be written. If running in Docker, "
                "ensure pt_config.json is bind-mounted as a file (not a directory) "
                "and that the host path is writable."
            ),
        )
        return {"ok": False, "error": str(e)}


# Legacy aliases so any cached browser tabs still work
@app.get("/api/settings")
async def api_settings_compat():
    return await api_config()


@app.put("/api/settings")
async def api_save_settings_compat(data: dict):
    return await api_save_config(data)


@app.post("/api/start-all")
async def api_start_all():
    return ctrl.start_all()


@app.post("/api/stop-all")
async def api_stop_all():
    ctrl.stop_all()
    return {"ok": True}


@app.post("/api/data-manager/start")
async def api_data_manager_start():
    ok = ctrl.start_data_manager()
    return {"ok": ok}


@app.post("/api/data-manager/stop")
async def api_data_manager_stop():
    ctrl.stop_data_manager()
    return {"ok": True}


@app.post("/api/data-manager/backfill/{coin}")
async def api_data_manager_backfill(coin: str):
    coin = coin.upper()
    if coin not in env.coins:
        return {"ok": False, "error": f"{coin} not in configured coins"}
    ok = ctrl.backfill_coin(coin)
    return {"ok": ok}


@app.get("/api/data-manager/stats")
async def api_data_manager_stats():
    """Per-(coin, tf) stats from the local kucoin ArcticDB libraries."""
    try:
        import arcticdb as adb
        store = adb.Arctic(f"lmdb:///{env.historic_data_dir}")
        known_libs = set(store.list_libraries())

        status_path = env.data_manager_status_path()
        error_coins: list = []
        if status_path.exists():
            try:
                error_coins = json.loads(status_path.read_text()).get("error_coins", [])
            except Exception:
                pass

        now = pd.Timestamp.now(tz="UTC")
        rows = []
        for tf in TRAIN_TF_MINUTES:
            lib_name = f"kucoin{tf}"
            if lib_name not in known_libs:
                for coin in env.coins:
                    rows.append({"coin": coin, "tf_minutes": tf, "error": "No library"})
                continue
            lib = store.get_library(lib_name)
            symbols = set(lib.list_symbols())
            for coin in env.coins:
                sym = f"{coin}_USDT"
                if coin in error_coins:
                    rows.append({"coin": coin, "tf_minutes": tf, "error": "Not available on KuCoin"})
                    continue
                if sym not in symbols:
                    rows.append({"coin": coin, "tf_minutes": tf, "error": "No data"})
                    continue
                try:
                    df = lib.read(sym, columns=[]).data
                    last_ts = df.index[-1]
                    age_minutes = int((now - last_ts).total_seconds() / 60)
                    rows.append({
                        "coin": coin, "tf_minutes": tf,
                        "rows": len(df),
                        "first": str(df.index[0].date()),
                        "last": str(last_ts),
                        "age_minutes": age_minutes,
                        "error": None,
                    })
                except Exception as e:
                    rows.append({"coin": coin, "tf_minutes": tf, "error": str(e)})
        interval_h = int(env.get_config().get("kucoin_local_topup_interval_hours") or 6)
        return {"rows": rows, "topup_interval_minutes": interval_h * 60, "error": None}
    except Exception as e:
        return {"rows": [], "topup_interval_minutes": 360, "error": str(e)}


@app.get("/api/data-manager/chart/{coin}/{tf_minutes}")
async def api_data_manager_chart(coin: str, tf_minutes: int, limit: int = 1500,
                                  start: int = None, end: int = None):
    """Return OHLCV candles from the local ArcticDB store."""
    import math
    try:
        import arcticdb as adb
        store = adb.Arctic(f"lmdb:///{env.historic_data_dir}")
        lib_name = f"kucoin{tf_minutes}"
        if lib_name not in store.list_libraries():
            return {"candles": [], "error": "Library not found"}
        lib = store.get_library(lib_name)
        sym = f"{coin}_USDT"
        df = lib.read(sym).data
        if df.empty:
            return {"candles": [], "effective_minutes": tf_minutes, "total_rows": 0}

        if start and end:
            t0 = pd.Timestamp(start, unit="s", tz="UTC")
            t1 = pd.Timestamp(end,   unit="s", tz="UTC")
            df = df.loc[t0:t1]

        total = len(df)
        effective_minutes = tf_minutes
        if total > limit:
            factor = math.ceil(total / limit)
            effective_minutes = factor * tf_minutes
            df = (
                df.resample(f"{effective_minutes}min")
                .agg({"open": "first", "high": "max", "low": "min", "close": "last"})
                .dropna()
            )

        candles = [
            {
                "time": int(ts.timestamp()),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
            }
            for ts, row in df.iterrows()
        ]
        return {"candles": candles, "effective_minutes": effective_minutes, "total_rows": total}
    except Exception as e:
        return {"candles": [], "error": str(e)}


@app.post("/api/start-thinker")
async def api_start_thinker():
    ok = ctrl.start_thinker()
    return {"ok": ok}


@app.post("/api/stop-thinker")
async def api_stop_thinker():
    ctrl.stop_thinker()
    return {"ok": True}


@app.post("/api/start-trader")
async def api_start_trader():
    ok = ctrl.start_trader()
    return {"ok": ok}


@app.post("/api/stop-trader")
async def api_stop_trader():
    ctrl.stop_trader()
    return {"ok": True}


@app.post("/api/start-trader/{exchange}")
async def api_start_trader_xk(exchange: str):
    ok = ctrl.start_trader(exchange.lower())
    return {"ok": ok}


@app.post("/api/stop-trader/{exchange}")
async def api_stop_trader_xk(exchange: str):
    ctrl.stop_trader(exchange.lower())
    return {"ok": True}


@app.post("/api/train-all")
async def api_train_all():
    results = ctrl.train_all()
    return {"ok": True, "results": results}


@app.post("/api/train/{coin}")
async def api_train_coin(coin: str):
    ok = ctrl.start_training(coin.upper())
    return {"ok": ok}


@app.post("/api/stop-training/{coin}")
async def api_stop_training(coin: str):
    ctrl.stop_training(coin.upper())
    return {"ok": True}


@app.get("/api/logs/{script}")
async def api_logs(script: str, limit: int = 200):
    lines = ctrl.peek_logs(script, limit)
    return {"lines": lines}


@app.get("/api/errors")
async def api_errors(limit: int = 200, level: str = "", component: str = "", since: float = 0):
    path = env.errors_path()
    if not path.exists():
        return {"errors": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        entries = []
        for line in lines:
            try:
                e = json.loads(line.strip())
                if level and e.get("level") != level:
                    continue
                if component and e.get("component") != component:
                    continue
                _ets = e.get("ts", 0)
                _ets_f = utc_to_ts(_ets) if isinstance(_ets, str) else float(_ets or 0)
                if since and _ets_f < since:
                    continue
                entries.append(e)
            except Exception:
                pass
        return {"errors": entries[-limit:]}
    except Exception as e:
        return {"errors": [], "error": str(e)}


@app.delete("/api/errors")
async def api_clear_errors():
    path = env.errors_path()
    try:
        path.write_text("")
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True}


@app.post("/api/notify/test")
async def api_notify_test():
    cfg = env.get_config()
    if not str(cfg.get("ntfy_url") or "").strip():
        return {"ok": False, "error": "ntfy_url is not configured in Settings"}
    try:
        import pt_notify
        xk = env.exchange or "demo"
        history_path = str(env.account_history_path(xk))
        pt_notify.test_notify(account_history_path=history_path)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _refresh_exchange_balance(xk: str, write_history: bool = True):
    """Query account for current balance and update trader_status file."""
    account = _get_account(xk)
    if not account:
        return
    try:
        total_value = account.get_account_value() or 0
        buying_power = account.get_buying_power() or 0
        holdings_value = total_value - buying_power
        pct = (holdings_value / total_value * 100) if total_value > 0 else 0

        status_path = env.trader_status_path(xk)
        existing = {}
        if status_path.exists():
            try:
                with open(status_path) as f:
                    existing = json.load(f)
            except Exception:
                pass

        existing.setdefault("account", {}).update({
            "total_account_value": total_value,
            "buying_power": buying_power,
            "holdings_sell_value": holdings_value,
            "holdings_buy_value": holdings_value,
            "percent_in_trade": pct,
        })
        existing.setdefault("positions", {})

        status_path.parent.mkdir(parents=True, exist_ok=True)
        with open(status_path, "w") as f:
            json.dump(existing, f)

        if write_history and total_value > 0:
            hist_path = env.account_history_path(xk)
            with open(hist_path, "a") as f:
                f.write(json.dumps({"ts": utcnow(), "total_account_value": total_value}) + "\n")
    except Exception as e:
        print(f"[Balance] {xk} refresh failed: {e}")
        pt_errors.emit(
            "pt_web", level="warning",
            message=f"Balance refresh failed for '{xk}': {e}",
            detail="Could not read the current account balance from the exchange adapter. The UI may show stale balance and portfolio values until the next successful refresh.",
        )


@app.post("/api/clear-account-history")
async def api_clear_account_history():
    """Delete account value history files for all exchanges."""
    for xk in _active_accounts():
        path = env.account_history_path(xk)
        try:
            if path.exists():
                path.write_text("")
        except Exception:
            pass
    return {"ok": True}


@app.post("/api/reset-all")
async def api_reset_all():
    """Stop everything, close all positions, wipe all state, reset to sync-source USD balance."""
    ctrl.stop_all()

    # Close real exchange positions
    ex = _real_exchange()
    if ex:
        for coin, qty in ex.get_holdings().items():
            try:
                ex.place_sell(f"{coin}_USD", qty)
            except Exception:
                pass

    sync_xk = _shadow_sync_exchange()
    exchange_sync = _get_exchange(sync_xk) if sync_xk else None
    balance = exchange_sync.get_buying_power() if exchange_sync else 0

    for xk in _active_accounts():
        for path in [env.trade_history_path(xk), env.account_history_path(xk)]:
            try:
                path.write_text("")
            except Exception:
                pass
        try:
            env.pnl_ledger_path(xk).write_text(json.dumps({
                "total_realized_profit_usd": 0.0,
                "open_positions": {},
                "lth_profit_bucket_usd": 0.0,
            }, indent=2))
        except Exception:
            pass
        try:
            env.bot_order_ids_path(xk).write_text("{}")
        except Exception:
            pass
        try:
            env.trader_status_path(xk).write_text(json.dumps(
                {"account": {}, "positions": {}}, indent=2
            ))
        except Exception:
            pass

    shadow_key = _shadow_xk()
    shadow_state = env.exchange_state_path(shadow_key)
    try:
        shadow_state.write_text(json.dumps({
            "usd_balance": balance or 0,
            "holdings": {},
            "orders": {},
            "position_cost": {},
        }, indent=2))
    except Exception:
        pass

    _exchanges.clear()
    for xk in _active_accounts():
        _refresh_exchange_balance(xk)

    return {"ok": True, "balance": balance}


@app.post("/api/close-all")
async def api_close_all():
    """Sell all positions on all real exchanges (or demo in demo mode)."""
    ctrl.stop_trader()
    results = {}

    if env.trading_mode == "demo":
        exchange = _get_exchange("demo")
        if exchange:
            holdings = exchange.get_holdings()
            trades = []
            for coin, qty in holdings.items():
                symbol = f"{coin}_USD"
                result = exchange.place_sell(symbol, qty)
                sold = result is not None
                trades.append({"coin": coin, "qty": qty, "sold": sold})
                if sold:
                    _record_close_trade("demo", coin, symbol, qty, result, tag="CLOSE_ALL")
            _refresh_exchange_balance("demo")
            _clear_trader_positions("demo")
            results["demo"] = {"ok": True, "trades": trades}
        return {"ok": True, "results": results}

    ex = _real_exchange()
    if not ex:
        return {"ok": False, "error": "no real exchange configured"}
    xk = ex.key
    holdings = ex.get_holdings()
    trades = []
    for coin, qty in holdings.items():
        symbol = f"{coin}_USD"
        result = ex.place_sell(symbol, qty)  # auto-mirrors to shadow
        sold = result is not None
        trades.append({"coin": coin, "qty": qty, "sold": sold})
        if sold:
            _record_close_trade(xk, coin, symbol, qty, result)
    _refresh_exchange_balance(xk)
    _clear_trader_positions(xk)
    results[xk] = {"ok": True, "trades": trades}

    _clear_trader_positions("shadow")
    return {"ok": True, "results": results}


@app.post("/api/close-coin/{coin}/{exchange}")
async def api_close_coin(coin: str, exchange: str):
    """Sell a single coin's position on a specific exchange."""
    coin = coin.upper()
    xk = exchange.lower()

    if xk == "shadow":
        return {"ok": False, "error": "Shadow positions are managed automatically"}

    if xk == "demo":
        ex = _get_exchange("demo")
        if not ex:
            return {"ok": False, "error": "No demo exchange"}
        holdings = ex.get_holdings()
        qty = holdings.get(coin, 0)
        symbol = f"{coin}_USD"
        if qty <= 0:
            return {"ok": False, "error": f"No {coin} position in demo"}
        result = ex.place_sell(symbol, qty)
        if not result:
            return {"ok": False, "error": "Demo sell failed"}
        _record_close_trade("demo", coin, symbol, qty, result, tag="CLOSE")
        _clear_coin_position("demo", coin)
        _refresh_exchange_balance("demo")
        return {"ok": True, "coin": coin, "exchange": "demo", "qty": qty}

    if xk != env.exchange:
        return {"ok": False, "error": f"Unknown exchange: {xk}"}

    ex = _get_exchange(xk)
    if not ex:
        return {"ok": False, "error": "No exchange"}

    holdings = ex.get_holdings()
    qty = holdings.get(coin, 0)
    symbol = f"{coin}_USD"

    if qty > 0:
        result = ex.place_sell(symbol, qty)  # auto-mirrors to shadow
        if not result:
            return {"ok": False, "error": "Sell failed"}
        _record_close_trade(xk, coin, symbol, qty, result, tag="CLOSE")
    else:
        ledger = {}
        try:
            lp = env.pnl_ledger_path(xk)
            if lp.exists():
                ledger = json.loads(lp.read_text())
        except Exception:
            pass
        has_ledger = float(
            (ledger.get("open_positions") or {}).get(coin, {}).get("qty", 0) or 0
        ) > 1e-12
        status = {}
        try:
            sp = env.trader_status_path(xk)
            if sp.exists():
                status = json.loads(sp.read_text())
        except Exception:
            pass
        has_status = float(
            (status.get("positions") or {}).get(coin, {}).get("quantity", 0) or 0
        ) > 1e-12
        if not has_ledger and not has_status:
            return {"ok": False, "error": f"No {coin} position on {xk}"}
        if has_ledger:
            (ledger.get("open_positions") or {}).pop(coin, None)
            try:
                env.pnl_ledger_path(xk).write_text(json.dumps(ledger, indent=2))
            except Exception:
                pass

    _clear_coin_position(xk, coin)
    _clear_coin_position("shadow", coin)
    _refresh_exchange_balance(xk)

    return {"ok": True, "coin": coin, "exchange": xk, "qty": qty}


def _clear_coin_position(xk: str, coin: str):
    """Zero out a single coin's position in trader_status file."""
    path = env.trader_status_path(xk)
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
        pos = (data.get("positions") or {}).get(coin)
        if isinstance(pos, dict):
            pos["quantity"] = 0
            pos["value_usd"] = 0
            pos["avg_cost_basis"] = 0
            pos["gain_loss_pct_buy"] = 0
            pos["gain_loss_pct_sell"] = 0
        path.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def _clear_trader_positions(xk: str):
    """Zero out positions in trader_status file after close-all."""
    path = env.trader_status_path(xk)
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
        for pos in (data.get("positions") or {}).values():
            if isinstance(pos, dict):
                pos["quantity"] = 0
                pos["value_usd"] = 0
                pos["avg_cost_basis"] = 0
                pos["gain_loss_pct_buy"] = 0
                pos["gain_loss_pct_sell"] = 0
        path.write_text(json.dumps(data, indent=2))
    except Exception:
        pass


def _record_close_trade(xk: str, coin: str, symbol: str, qty: float, result,
                        tag: str = "CLOSE_ALL"):
    """Record a close sell in trade history and update PnL ledger."""
    ts = utcnow()
    price = result.avg_price
    notional = result.notional_usd or (price * qty if price else None)
    fees = result.fees_usd

    ledger_path = env.pnl_ledger_path(xk)
    try:
        ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
    except Exception:
        ledger = {}

    pos = (ledger.get("open_positions") or {}).get(coin)
    pos_qty = float(pos.get("qty", 0) or 0) if isinstance(pos, dict) else 0.0
    pos_cost = float(pos.get("usd_cost", 0) or 0) if isinstance(pos, dict) else 0.0

    frac = min(1.0, qty / pos_qty) if pos_qty > 0 else 1.0
    cost_used = pos_cost * frac
    avg_cost = cost_used / qty if qty > 0 else None

    realized = None
    if notional is not None:
        fee_val = float(fees) if fees is not None else 0.0
        realized = (notional - fee_val) - cost_used

    pnl_pct = None
    if realized is not None and cost_used > 0:
        pnl_pct = (realized / cost_used) * 100

    if isinstance(pos, dict):
        pos["usd_cost"] = pos_cost - cost_used
        pos["qty"] = pos_qty - qty
        if float(pos.get("qty", 0) or 0) <= 1e-12:
            ledger.get("open_positions", {}).pop(coin, None)

    if realized is not None:
        ledger["total_realized_profit_usd"] = float(
            ledger.get("total_realized_profit_usd", 0) or 0
        ) + realized

    try:
        ledger_path.write_text(json.dumps(ledger, indent=2))
    except Exception as e:
        pt_errors.emit(
            "pt_web", level="error",
            message=f"PnL ledger write failed for '{xk}' after close trade: {e}",
            detail="The realized-profit ledger could not be saved. The trade was executed on the exchange but the PnL record may be out of date. Check disk space and file permissions.",
        )

    entry = {
        "ts": ts,
        "side": "sell",
        "tag": tag,
        "symbol": symbol,
        "qty": qty,
        "price": price,
        "notional_usd": notional,
        "net_usd": (notional - (float(fees) if fees else 0.0)) if notional else None,
        "avg_cost_basis": avg_cost,
        "pnl_pct": pnl_pct,
        "fees_usd": fees,
        "fees_missing": fees is None,
        "fees_fallback_applied_usd": 0.0,
        "realized_profit_usd": realized,
        "order_id": result.order_id,
        "position_cost_used_usd": cost_used,
        "position_cost_after_usd": float((ledger.get("open_positions") or {}).get(coin, {}).get("usd_cost", 0) or 0),
    }
    history_path = env.trade_history_path(xk)
    try:
        with open(history_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception as e:
        pt_errors.emit(
            "pt_web", level="warning",
            message=f"Trade history write failed for '{xk}': {e}",
            detail="A close-trade record could not be appended to trade history. The trade was executed on the exchange but will not appear in the Trades tab or PnL calculations.",
        )

    # Clear bot order IDs for this coin so the next trade cycle starts fresh.
    # Without this, stale order IDs from the closed position inflate the cost
    # basis when the position is rebuilt from selected orders on next startup.
    try:
        ids_path = env.bot_order_ids_path(xk)
        if ids_path.exists():
            ids = json.loads(ids_path.read_text())
            ids.pop(coin, None)
            ids_path.write_text(json.dumps(ids, indent=2))
    except Exception:
        pass


@app.post("/api/sync-shadow")
async def api_sync_shadow():
    """Reset shadow/demo balance to match the configured sync-source exchange. Requires traders stopped, no positions."""
    if ctrl.trader_running:
        return {"ok": False, "error": "Traders must be stopped"}

    ex = _real_exchange()
    if ex:
        xk = ex.key
        holdings = ex.get_holdings()
        pnl = json.loads(env.pnl_ledger_path(xk).read_text()) if env.pnl_ledger_path(xk).exists() else {}
        open_pos = pnl.get("open_positions") or {}
        has_real = any(
            float((open_pos.get(coin) or {}).get("qty", 0) or 0) > 1e-12
            for coin in holdings
        )
        if has_real:
            return {"ok": False, "error": f"{xk} has open positions"}

    sync_xk = _shadow_sync_exchange()
    exchange_sync = _get_exchange(sync_xk) if sync_xk else None
    if not exchange_sync:
        return {"ok": False, "error": f"Cannot connect to sync exchange ({sync_xk or 'none configured'})"}

    buying_power = exchange_sync.get_buying_power()
    if not buying_power or buying_power <= 0:
        return {"ok": False, "error": f"{sync_xk} has no USD balance"}

    shadow_key = _shadow_xk()
    state_path = env.exchange_state_path(shadow_key)
    with open(state_path, "w") as f:
        json.dump({"usd_balance": buying_power, "holdings": {}, "orders": {}, "position_cost": {}}, f, indent=2)

    # Clear cached exchange so shadow re-initialises from the new state file
    _exchanges.pop(shadow_key if env.trading_mode == "demo" else (env.exchange or ""), None)

    _refresh_exchange_balance(shadow_key)
    print(f"[Sync] {shadow_key} balance set to ${buying_power:,.2f} from {sync_xk}")
    return {"ok": True, "balance": buying_power}


@app.get("/api/candles/{coin}")
async def api_candles(coin: str, timeframe: str = "1hour", limit: int = 250,
                      source: str = ""):
    """Fetch candle data for charting. Source: kraken or kucoin (auto from settings)."""
    coin = coin.upper()
    env.reload()
    price_source = source or env.get_config()["live_price_source"]

    if price_source == "kraken":
        return _candles_kraken(coin, timeframe, limit)
    return _candles_kucoin(coin, timeframe, limit)


def _candles_kraken(coin: str, timeframe: str, limit: int) -> dict:
    tf_map = {
        "1min": "1m", "5min": "5m", "15min": "15m", "30min": "30m",
        "1hour": "1h", "2hour": "2h", "4hour": "4h",
        "8hour": "8h", "12hour": "12h", "1day": "1d", "1week": "1w",
    }
    ccxt_tf = tf_map.get(timeframe, "1h")
    try:
        import ccxt
        kraken = ccxt.kraken({"enableRateLimit": True})
        ohlcv = kraken.fetch_ohlcv(f"{coin}/USDT", ccxt_tf, limit=limit)
        candles = []
        for bar in ohlcv:
            candles.append({
                "time": int(bar[0] / 1000),
                "open": float(bar[1]),
                "high": float(bar[2]),
                "low": float(bar[3]),
                "close": float(bar[4]),
                "volume": float(bar[5]),
            })
        return {"candles": candles}
    except Exception:
        return _candles_kucoin(coin, timeframe, limit)


def _candles_kucoin(coin: str, timeframe: str, limit: int) -> dict:
    tf_map = {
        "1min": "1min", "5min": "5min", "15min": "15min", "30min": "30min",
        "1hour": "1hour", "2hour": "2hour", "4hour": "4hour",
        "8hour": "8hour", "12hour": "12hour", "1day": "1day", "1week": "1week",
    }
    tf_seconds = {
        "1min": 60, "5min": 300, "15min": 900, "30min": 1800,
        "1hour": 3600, "2hour": 7200, "4hour": 14400,
        "8hour": 28800, "12hour": 43200, "1day": 86400, "1week": 604800,
    }
    kc_tf = tf_map.get(timeframe, "1hour")
    secs  = tf_seconds.get(timeframe, 3600)
    try:
        import time as _time
        import requests as _req
        start_at = int(_time.time()) - limit * secs
        url = (f"https://api.kucoin.com/api/v1/market/candles"
               f"?type={kc_tf}&symbol={coin}-USDT&startAt={start_at}")
        resp = _req.get(url, timeout=10)
        data = resp.json()
        klines = data.get("data", [])
        candles = []
        for k in reversed(klines):
            candles.append({
                "time": int(k[0]),
                "open": float(k[1]),
                "close": float(k[2]),
                "high": float(k[3]),
                "low": float(k[4]),
                "volume": float(k[5]),
            })
        return {"candles": candles[-limit:]}
    except Exception as e:
        return {"candles": [], "error": str(e)}


# ── WebSocket for real-time updates ──

class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


ws_manager = ConnectionManager()


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws_manager.connect(ws)
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(ws)


async def _file_watcher():
    """Poll key files and broadcast changes to WebSocket clients."""
    mtimes: dict[str, float] = {}
    balance_tick = 0

    def _check(path: Path, key: str) -> bool:
        try:
            mt = path.stat().st_mtime
        except FileNotFoundError:
            return False
        old = mtimes.get(key, 0)
        if mt != old:
            mtimes[key] = mt
            return True
        return False

    while True:
        if not ws_manager.active:
            await asyncio.sleep(2)
            continue

        try:
            env.reload()

            for xk in _active_accounts():
                if _check(env.trader_status_path(xk), f"trader_status_{xk}"):
                    acct = AccountModel(env, xk)
                    ts = acct.trader_status()
                    if ts:
                        await ws_manager.broadcast({
                            "type": "trader_status",
                            "exchange": xk,
                            "data": ts,
                        })
                        xd = _account_for_exchange(xk)
                        await ws_manager.broadcast({
                            "type": "pnl",
                            "exchange": xk,
                            "data": xd["pnl"],
                        })

                if _check(env.pnl_ledger_path(xk), f"pnl_{xk}"):
                    xd = _account_for_exchange(xk)
                    await ws_manager.broadcast({
                        "type": "pnl",
                        "exchange": xk,
                        "data": xd["pnl"],
                    })

            signals_changed = False
            for coin in env.coins:
                if _check(env.long_signal_path(coin), f"long_{coin}"):
                    signals_changed = True
                if _check(env.short_signal_path(coin), f"short_{coin}"):
                    signals_changed = True

            if signals_changed:
                coins_data = {}
                for coin in env.coins:
                    cm = CoinModel(env, coin)
                    coins_data[coin] = {
                        "long": cm.long_signal(),
                        "short": cm.short_signal(),
                        "long_prices": cm.long_price_levels(),
                        "short_prices": cm.short_price_levels(),
                    }
                await ws_manager.broadcast({"type": "signals", "data": coins_data})

            if _check(env.thinker_ready_path(), "thinker_ready"):
                sm = SystemModel(env)
                rr = sm.thinker_ready()
                await ws_manager.broadcast({"type": "thinker_ready", "data": rr})
                if rr.get("ready") and ctrl.thinker_running:
                    ctrl.poll_ready_and_start_trader()

            dm_status_path = env.data_manager_status_path()
            if _check(dm_status_path, "data_manager_status"):
                try:
                    dm_data = json.loads(dm_status_path.read_text())
                except Exception:
                    dm_data = {}
                await ws_manager.broadcast({"type": "data_manager_status", "data": dm_data})

            errors_path = env.errors_path()
            if _check(errors_path, "errors"):
                try:
                    with open(errors_path, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    if lines:
                        entry = json.loads(lines[-1].strip())
                        await ws_manager.broadcast({"type": "error_event", "data": entry})
                except Exception:
                    pass

            traders_status = {}
            for xk in _active_accounts():
                traders_status[xk] = {"running": ctrl.trader_running_for(xk)}
            sys_status = {
                "thinker_running": ctrl.thinker_running,
                "trader_running": ctrl.trader_running,
                "data_manager_running": ctrl.data_manager_running,
                "traders": traders_status,
                "any_training_running": ctrl.any_training_running(),
            }
            await ws_manager.broadcast({"type": "system", "data": sys_status})

            balance_tick += 1
            if balance_tick >= 20:
                balance_tick = 0
                # Refresh shadow status via tick() when trader is not running
                ex = _real_exchange()
                if ex and not ctrl.trader_running_for(ex.key):
                    try:
                        ex.tick()
                    except Exception:
                        pass
                for xk in _active_accounts():
                    if not ctrl.trader_running_for(xk):
                        _refresh_exchange_balance(xk, write_history=False)

        except Exception as e:
            pt_errors.emit(
                "pt_web", level="warning",
                message=f"File-watcher iteration failed: {e}",
                detail="The background loop that pushes live updates to the UI encountered an error. Real-time WebSocket updates (trader status, signals, balances) may have been missed this cycle; they will resume on the next iteration.",
            )

        await asyncio.sleep(1.5)


def _init_exchange_balances():
    """Seed initial shadow state if needed, then refresh all balances."""
    env.reload()

    shadow_key = _shadow_xk()
    shadow_state = env.exchange_state_path(shadow_key)
    if not shadow_state.exists():
        cfg = env.get_config()
        cfg_key = "demo_starting_usd" if shadow_key == "demo" else "shadow_starting_usd"
        starting = float(cfg.get(cfg_key) or 0)
        if starting <= 0:
            sync_xk = _shadow_sync_exchange()
            if sync_xk:
                exchange_sync = _get_exchange(sync_xk)
                starting = (exchange_sync.get_buying_power() or 0) if exchange_sync else 0
                print(f"[Init] {shadow_key} starting balance from {sync_xk}: ${starting:,.2f}")
        if starting > 0:
            with open(shadow_state, "w") as f:
                json.dump({"usd_balance": starting, "holdings": {}, "orders": {}, "position_cost": {}}, f)
            print(f"[Init] wrote {shadow_state}")

    for xk in _active_accounts():
        _refresh_exchange_balance(xk)
        status_path = env.trader_status_path(xk)
        if status_path.exists():
            try:
                with open(status_path) as f:
                    data = json.load(f)
                total = data.get("account", {}).get("total_account_value", 0)
                print(f"[Init] {xk}: ${total:,.2f}")
            except Exception:
                pass


def _migrate_hub_data_flat_to_subdirs():
    """One-time migration: move flat hub_data/{name}_{xk}.ext into hub_data/exchanges/{xk}/{name}.ext."""
    import re
    hub = env.hub_data_dir
    if not hub.exists():
        return
    patterns = [
        (re.compile(r'^trader_status_(.+)\.json$'),            "trader_status.json"),
        (re.compile(r'^trade_history_(.+)\.jsonl$'),           "trade_history.jsonl"),
        (re.compile(r'^pnl_ledger_(.+)\.json$'),               "pnl_ledger.json"),
        (re.compile(r'^account_value_history_(.+)\.jsonl$'),   "account_value_history.jsonl"),
        (re.compile(r'^bot_order_ids_(.+)\.json$'),            "bot_order_ids.json"),
        (re.compile(r'^(.+)_exchange_state\.json$'),           "exchange_state.json"),
    ]
    moved = 0
    for f in sorted(hub.iterdir()):
        if not f.is_file():
            continue
        for pattern, new_name in patterns:
            m = pattern.match(f.name)
            if m:
                xk = m.group(1)
                dest_dir = hub / "exchanges" / xk
                dest_dir.mkdir(parents=True, exist_ok=True)
                dest = dest_dir / new_name
                if not dest.exists():
                    f.rename(dest)
                    print(f"[Migrate] {f.name} → exchanges/{xk}/{new_name}")
                    moved += 1
                break
    if moved:
        print(f"[Migrate] Moved {moved} state files into subdirectories")


def _migrate_control_to_shadow():
    """One-time migration: rename hub_data/exchanges/control/ → hub_data/exchanges/shadow/."""
    hub = env.hub_data_dir
    src = hub / "exchanges" / "control"
    dst = hub / "exchanges" / "shadow"
    if src.exists() and not dst.exists():
        src.rename(dst)
        print("[Migrate] exchanges/control → exchanges/shadow")


@asynccontextmanager
async def lifespan(app):
    pt_errors.trim(max_lines=500)
    _migrate_hub_data_flat_to_subdirs()
    _migrate_control_to_shadow()
    _init_exchange_balances()
    asyncio.create_task(_file_watcher())
    yield

app.router.lifespan_context = lifespan


# ── Entry point ──

if __name__ == "__main__":
    import uvicorn
    parser = argparse.ArgumentParser(description="PowerTrader Web UI")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--host", type=str, default="0.0.0.0")
    parser.add_argument("--config", default=None, help="Path to config JSON (default: pt_config.json)")
    parser.add_argument("--state_dir", default=None, help="Directory containing pt_config.json and state/ (default: directory of pt_web.py)")
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port)
