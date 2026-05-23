"""
PowerTrader local KuCoin data manager.

Launched as a subprocess by pt_controller. On start:
  - For each coin: backfill if no local data exists, topup if data exists.
  - Enters Normal (idle) state, then topups at a fixed interval derived
    from kucoin_local_topup_interval_hours config (e.g. 6 → every 6h).

Writes hub_data/data_manager_status.json at every state transition so the
web UI can display the current state without polling logs.

State values: Stopped | Backfill | Topup | Normal
"""

import json
import os
import time

import ccxt
import pandas as pd

from pt_env import PTEnv, TRAIN_TF_MINUTES, TRAIN_TF_CCXT, utcnow, utc_to_ts
import pt_errors
from pt_log import get_logger

log = get_logger("data-manager")

_env = PTEnv(os.path.dirname(os.path.abspath(__file__)))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lib_name(tf: int) -> str:
    return f"kucoin{tf}"


def _arctic_sym(coin: str) -> str:
    return f"{coin}_USDT"


def _ccxt_sym(coin: str) -> str:
    return f"{coin}/USDT"


def _get_arctic():
    import arcticdb as adb
    data_dir = _env.historic_data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    return adb.Arctic(f"lmdb:///{data_dir}")


def _write_status(state: str, coin: str = "", tf: int = 0,
                  error_coins: list = None, last_topup: str = ""):
    status = {
        "state": state,
        "coin": coin,
        "tf_minutes": tf,
        "last_topup": last_topup,
        "error_coins": error_coins or [],
        "ts": utcnow(),
    }
    path = _env.data_manager_status_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(status, indent=2))
        tmp.rename(path)
    except Exception as e:
        log.warning(f"status write failed: {e}")


def _topup_interval_seconds(cfg: dict) -> float:
    return max(1, int(cfg.get("kucoin_local_topup_interval_hours") or 6)) * 3600


# ---------------------------------------------------------------------------
# Backfill
# ---------------------------------------------------------------------------

def _backfill_coin(client, arctic, coin: str, error_coins: list):
    """Backfill all timeframes for a single coin. Logs errors per TF."""
    a_sym = _arctic_sym(coin)
    c_sym = _ccxt_sym(coin)

    if c_sym not in client.markets:
        log.warning(f"[Backfill] {coin}: not available on KuCoin — skipping")
        if coin not in error_coins:
            error_coins.append(coin)
        return

    for tf in TRAIN_TF_MINUTES:
        lib_name = _lib_name(tf)
        ccxt_tf = TRAIN_TF_CCXT[tf]
        tf_ms = tf * 60 * 1000

        if lib_name not in arctic.list_libraries():
            arctic.create_library(lib_name)
        lib = arctic.get_library(lib_name)

        if a_sym in lib.list_symbols():
            continue  # already have data; topup handles this

        log.info(f"[Backfill] {coin} {lib_name}")
        try:
            all_candles = []
            end_ms = int(pd.Timestamp.now(tz="UTC").timestamp() * 1000)

            while True:
                since_ms = end_ms - (1500 * tf_ms)
                candles = client.fetch_ohlcv(c_sym, ccxt_tf, since=since_ms, limit=1500)
                time.sleep(0.4)
                if not candles:
                    break
                all_candles.extend(candles)
                oldest = candles[0][0]
                if oldest >= since_ms + tf_ms:
                    break
                end_ms = oldest - 1

            if not all_candles:
                log.warning(f"[Backfill] {coin} {lib_name}: no candles returned")
                continue

            seen: set = set()
            deduped = [c for c in all_candles if not (c[0] in seen or seen.add(c[0]))]

            df = pd.DataFrame(deduped,
                              columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df = df.set_index("timestamp").sort_index().astype(float)

            lib.write(a_sym, df, prune_previous_versions=True)
            log.info(f"[Backfill] {coin} {lib_name}: {len(df)} rows "
                     f"({df.index[0].date()} → {df.index[-1].date()})")

        except Exception:
            log.exception(f"[Backfill] {coin} {lib_name} error")


# ---------------------------------------------------------------------------
# Topup
# ---------------------------------------------------------------------------

def _topup_coin(client, arctic, coin: str, error_coins: list):
    """Topup all timeframes for a single coin that already has data."""
    a_sym = _arctic_sym(coin)
    c_sym = _ccxt_sym(coin)

    if c_sym not in client.markets:
        log.warning(f"[Topup] {coin}: not available on KuCoin")
        if coin not in error_coins:
            error_coins.append(coin)
        return

    for tf in TRAIN_TF_MINUTES:
        lib_name = _lib_name(tf)
        ccxt_tf = TRAIN_TF_CCXT[tf]

        if lib_name not in arctic.list_libraries():
            continue
        lib = arctic.get_library(lib_name)
        if a_sym not in lib.list_symbols():
            continue

        try:
            last_ts = lib.read(a_sym, columns=[]).data.index[-1]
            since_ms = int(last_ts.timestamp() * 1000) + 1

            all_candles = []
            seen_ts: set = set()
            while True:
                candles = client.fetch_ohlcv(c_sym, ccxt_tf, since=since_ms, limit=1500)
                time.sleep(0.4)
                if not candles:
                    break
                new = [c for c in candles if c[0] not in seen_ts]
                if not new:
                    break
                all_candles.extend(new)
                seen_ts.update(c[0] for c in new)
                since_ms = candles[-1][0] + 1

            if not all_candles:
                log.debug(f"[Topup] {coin} {lib_name}: up to date")
                continue

            df = pd.DataFrame(all_candles,
                              columns=["timestamp", "open", "high", "low", "close", "volume"])
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            df = df.set_index("timestamp").sort_index().astype(float)
            df = df[df.index > last_ts]

            if df.empty:
                log.debug(f"[Topup] {coin} {lib_name}: up to date")
                continue

            lib.append(a_sym, df)
            log.info(f"[Topup] {coin} {lib_name}: +{len(df)} rows → {df.index[-1]}")

        except Exception:
            log.exception(f"[Topup] {coin} {lib_name} error")


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def _get_client():
    client = ccxt.kucoin({"enableRateLimit": True})
    client.timeout = 30000
    client.load_markets()
    return client


def run():
    log.info("starting")
    error_coins: list = []

    # ── Initial pass: backfill missing, topup existing ────────────────────
    cfg = _env.get_config()
    coins: list[str] = [str(c).upper() for c in cfg.get("coins", []) if str(c).strip()]
    interval_s = _topup_interval_seconds(cfg)

    log.info(f"{len(coins)} coins, topup every {interval_s/3600:.1f}h")

    try:
        client = _get_client()
        arctic = _get_arctic()
    except Exception as e:
        log.error(f"startup failed: {e}")
        pt_errors.emit(
            "data_manager", level="error",
            message=f"Data manager startup failed: {e}",
            detail="Could not connect to KuCoin or open the ArcticDB store. Historic OHLCV data will not be backfilled or topped up until the issue is resolved.",
        )
        _write_status("Normal", error_coins=["startup failed"])
        return

    # Categorise coins
    coins_to_backfill = []
    coins_to_topup = []
    for coin in coins:
        a_sym = _arctic_sym(coin)
        lib60 = _lib_name(60)
        try:
            if lib60 in arctic.list_libraries():
                lib = arctic.get_library(lib60)
                if a_sym in lib.list_symbols():
                    coins_to_topup.append(coin)
                    continue
        except Exception:
            pass
        coins_to_backfill.append(coin)

    log.info(f"{len(coins_to_backfill)} to backfill, {len(coins_to_topup)} to topup")

    # Backfill
    for coin in coins_to_backfill:
        _write_status("Backfill", coin=coin, error_coins=error_coins)
        _backfill_coin(client, arctic, coin, error_coins)

    # Topup
    for coin in coins_to_topup:
        _write_status("Topup", coin=coin, error_coins=error_coins)
        _topup_coin(client, arctic, coin, error_coins)

    if error_coins:
        log.warning(f"problem coins: {', '.join(error_coins)}")

    last_topup = utcnow()
    _write_status("Normal", error_coins=error_coins, last_topup=last_topup)
    log.info(f"initial pass complete — next topup in {interval_s/3600:.1f}h")

    # ── Scheduled topup loop ──────────────────────────────────────────────
    while True:
        time.sleep(60)  # wake every minute to check if interval elapsed

        # Re-read config each cycle so changes are picked up
        cfg = _env.get_config()
        coins = [str(c).upper() for c in cfg.get("coins", []) if str(c).strip()]
        interval_s = _topup_interval_seconds(cfg)

        if time.time() - utc_to_ts(last_topup) < interval_s:
            continue

        log.info("scheduled topup starting")
        error_coins = []
        try:
            client = _get_client()
            arctic = _get_arctic()
        except Exception as e:
            log.error(f"client error: {e}")
            pt_errors.emit(
                "data_manager", level="warning",
                message=f"Scheduled topup failed to connect: {e}",
                detail="Could not reach KuCoin or open the ArcticDB store for the scheduled topup. Historic data will be stale until the next successful topup cycle.",
            )
            continue

        for coin in coins:
            _write_status("Topup", coin=coin, error_coins=error_coins)
            a_sym = _arctic_sym(coin)
            lib60 = _lib_name(60)
            try:
                has_data = (lib60 in arctic.list_libraries() and
                            a_sym in arctic.get_library(lib60).list_symbols())
            except Exception:
                has_data = False

            if has_data:
                _topup_coin(client, arctic, coin, error_coins)
            else:
                _backfill_coin(client, arctic, coin, error_coins)

        last_topup = utcnow()
        _write_status("Normal", error_coins=error_coins, last_topup=last_topup)
        log.info(f"topup complete — next in {interval_s/3600:.1f}h")


def run_single(coin: str):
    """One-shot backfill for a single coin. Used by pt_web backfill endpoint."""
    coin = coin.upper()
    log.info(f"one-shot backfill: {coin}")

    # Read existing status so we preserve error_coins from the running data manager
    path = _env.data_manager_status_path()
    try:
        existing = json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        existing = {}
    error_coins: list = list(existing.get("error_coins") or [])

    try:
        client = _get_client()
        arctic = _get_arctic()
    except Exception as e:
        log.error(f"startup failed: {e}")
        return
    _write_status("Backfill", coin=coin, error_coins=error_coins)
    _backfill_coin(client, arctic, coin, error_coins)
    # Restore prior state (Normal or Stopped) with merged error_coins
    prior_state = existing.get("state", "Normal")
    if prior_state not in ("Normal", "Topup", "Backfill"):
        prior_state = "Normal"
    _write_status(prior_state, error_coins=error_coins,
                  last_topup=existing.get("last_topup", ""))
    log.info(f"one-shot backfill complete: {coin}")


if __name__ == "__main__":
    import argparse as _argparse
    _p = _argparse.ArgumentParser()
    _p.add_argument("--coin", default=None, help="Backfill a single coin and exit")
    _args = _p.parse_args()

    if _args.coin:
        try:
            run_single(_args.coin)
        except Exception:
            log.exception("fatal")
        finally:
            _write_status("Stopped")
    else:
        try:
            run()
        except KeyboardInterrupt:
            log.info("stopped")
        except Exception as e:
            log.exception("fatal")
            pt_errors.emit(
                "data_manager", level="error",
                message=f"Data manager crashed: {e}",
                detail="The data manager process encountered an unhandled exception and has stopped. Historic OHLCV data will not be updated until it is restarted.",
            )
        finally:
            _write_status("Stopped")
