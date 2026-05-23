#!/usr/bin/env python3
"""
One-time migration: convert all Unix float timestamps in state files to ISO-8601 UTC strings.
Run with: python migrate_timestamps.py
"""
import json, os, glob
from datetime import datetime, timezone

def to_iso(val):
    if isinstance(val, (int, float)) and val > 0:
        return datetime.fromtimestamp(val, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return val

def migrate_jsonl(path, ts_fields=("ts",)):
    lines_in = open(path).readlines()
    with open(path, "w") as f:
        for line in lines_in:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                for field in ts_fields:
                    if field in obj:
                        obj[field] = to_iso(obj[field])
                f.write(json.dumps(obj) + "\n")
            except Exception:
                f.write(line + "\n")

def migrate_json(path, ts_fields):
    try:
        d = json.loads(open(path).read())
    except Exception:
        return
    changed = False
    for field in ts_fields:
        if field in d and isinstance(d[field], (int, float)):
            d[field] = to_iso(d[field])
            changed = True
    if changed:
        open(path, "w").write(json.dumps(d, indent=2))

ROOT = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(ROOT, "state", "hub_data")

# JSONL files
for p in glob.glob(os.path.join(STATE, "exchanges", "*", "trade_history.jsonl")):
    print(f"Migrating {p}"); migrate_jsonl(p, ("ts",))
for p in glob.glob(os.path.join(STATE, "exchanges", "*", "account_value_history.jsonl")):
    print(f"Migrating {p}"); migrate_jsonl(p, ("ts",))
errors_path = os.path.join(STATE, "errors.jsonl")
if os.path.exists(errors_path):
    print(f"Migrating {errors_path}"); migrate_jsonl(errors_path, ("ts",))

# pnl_ledger (also has nested lth_last_buy.ts)
for p in glob.glob(os.path.join(STATE, "exchanges", "*", "pnl_ledger.json")):
    print(f"Migrating {p}")
    d = json.loads(open(p).read())
    if "last_updated_ts" in d:
        d["last_updated_ts"] = to_iso(d["last_updated_ts"])
    if isinstance(d.get("lth_last_buy"), dict) and "ts" in d["lth_last_buy"]:
        d["lth_last_buy"]["ts"] = to_iso(d["lth_last_buy"]["ts"])
    open(p, "w").write(json.dumps(d, indent=2))

# trader_status
for p in glob.glob(os.path.join(STATE, "exchanges", "*", "trader_status.json")):
    print(f"Migrating {p}"); migrate_json(p, ("timestamp",))

# exchange_state orders (ts inside each order)
for p in glob.glob(os.path.join(STATE, "exchanges", "*", "exchange_state.json")):
    print(f"Migrating {p}")
    try:
        d = json.loads(open(p).read())
        orders = d.get("orders", {})
        for sym in orders:
            for o in orders[sym]:
                if "ts" in o:
                    o["ts"] = to_iso(o["ts"])
        open(p, "w").write(json.dumps(d, indent=2))
    except Exception as e:
        print(f"  skipped: {e}")

# Other hub_data files
migrate_json(os.path.join(STATE, "lth_daily_ema200.json"), ("ts",))
migrate_json(os.path.join(STATE, "thinker_ready.json"), ("timestamp",))

autorestart = os.path.join(STATE, "thinker_autorestart_state.json")
if os.path.exists(autorestart):
    d = json.loads(open(autorestart).read())
    for f in ("timestamp", "last_auto_restart_ts"):
        if f in d:
            d[f] = to_iso(d[f])
    open(autorestart, "w").write(json.dumps(d, indent=2))

dm = os.path.join(STATE, "data_manager_status.json")
if os.path.exists(dm):
    print(f"Migrating {dm}"); migrate_json(dm, ("ts", "last_topup"))

# coins/*/trainer_status.json
for p in glob.glob(os.path.join(ROOT, "state", "coins", "*", "trainer_status.json")):
    print(f"Migrating {p}"); migrate_json(p, ("started_at", "finished_at", "timestamp"))

# coins/*/trainer_last_*.txt (raw Unix float strings)
for p in glob.glob(os.path.join(ROOT, "state", "coins", "*", "trainer_last_*.txt")):
    print(f"Migrating {p}")
    try:
        raw = open(p).read().strip()
        val = float(raw) if raw else 0.0
        if val > 0:
            open(p, "w").write(datetime.fromtimestamp(val, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    except Exception as e:
        print(f"  skipped: {e}")

print("Done.")
