"""Prepare traderjojo ingest data from PowerTrader simulator files.

Converts `simulator_trades.json` into traderjojo `hub_data/paper/trade_history.jsonl`.
This is conservative: each closed trade becomes a buy event (entry) followed
by a sell event (exit) with a 60-second separation if no entry timestamp exists.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict


def iso_to_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def dt_to_iso(dt: datetime) -> str:
    return dt.isoformat()


def prepare(sim_path: str = "simulator_trades.json", traderjojo_root: str = "C:/repo/traderjojo") -> Dict[str, str]:
    repo = Path(__file__).resolve().parent
    sim = repo / sim_path
    if not sim.exists():
        raise FileNotFoundError(sim)
    payload = json.loads(sim.read_text())
    closed = payload.get("closed_trades", [])

    hub_dir = Path(traderjojo_root) / "hub_data" / "paper"
    hub_dir.mkdir(parents=True, exist_ok=True)
    out_path = hub_dir / "trade_history.jsonl"
    lines = []
    for t in closed:
        # Expect fields: symbol, qty, entry_price, exit_price, timestamp
        symbol = t.get("symbol")
        qty = t.get("qty")
        entry_price = t.get("entry_price")
        exit_price = t.get("exit_price")
        exit_ts = t.get("timestamp")
        try:
            exit_dt = iso_to_dt(exit_ts)
        except Exception:
            # fallback to now
            exit_dt = datetime.utcnow()
        entry_dt = exit_dt - timedelta(seconds=60)

        buy_event = {
            "timestamp": dt_to_iso(entry_dt),
            "symbol": symbol,
            "side": "buy",
            "qty": qty,
            "price": entry_price,
        }
        sell_event = {
            "timestamp": dt_to_iso(exit_dt),
            "symbol": symbol,
            "side": "sell",
            "qty": qty,
            "price": exit_price,
            "pnl_pct": t.get("pnl_pct", 0.0),
            "realized_profit_usd": t.get("pnl", 0.0),
        }
        lines.append(buy_event)
        lines.append(sell_event)

    # write JSONL
    with out_path.open("w", encoding="utf-8") as fh:
        for item in lines:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")

    # ensure active_positions exists
    active = hub_dir / "active_positions.json"
    if not active.exists():
        active.write_text(json.dumps({}), encoding="utf-8")

    return {"trade_history": str(out_path), "active_positions": str(active)}


if __name__ == "__main__":
    res = prepare()
    print(json.dumps(res, indent=2))
