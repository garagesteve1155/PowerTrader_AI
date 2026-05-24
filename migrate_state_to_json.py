"""
One-time migration: consolidate per-coin flat files into training_data.json and trainer_state.json.

Safe to run while app is stopped. Old files are left in place until you run with --delete.
"""

import json
import os
import sys

TIMEFRAMES = ["1hour", "2hour", "4hour", "8hour", "12hour", "1day", "1week"]


def coin_dirs(coins_dir):
    for name in sorted(os.listdir(coins_dir)):
        path = os.path.join(coins_dir, name)
        if os.path.isdir(path):
            yield name, path


def read(path, default=""):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except FileNotFoundError:
        return default


def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def write_json_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def migrate_training_data(coin_path):
    """Build training_data.json from the 28 per-TF flat files."""
    out_path = os.path.join(coin_path, "training_data.json")
    # Start from existing file if present (preserve already-migrated data)
    td = read_json(out_path, {})

    for tf in TIMEFRAMES:
        tf_data = td.setdefault(tf, {})
        memories = read(os.path.join(coin_path, f"memories_{tf}.txt"))
        weights = read(os.path.join(coin_path, f"memory_weights_{tf}.txt"))
        weights_high = read(os.path.join(coin_path, f"memory_weights_high_{tf}.txt"))
        weights_low = read(os.path.join(coin_path, f"memory_weights_low_{tf}.txt"))
        threshold_raw = read(os.path.join(coin_path, f"neural_perfect_threshold_{tf}.txt"))

        if memories.strip():
            tf_data["memories"] = memories.strip()
        if weights.strip():
            tf_data["weights"] = weights.strip()
        if weights_high.strip():
            tf_data["weights_high"] = weights_high.strip()
        if weights_low.strip():
            tf_data["weights_low"] = weights_low.strip()
        try:
            tf_data["threshold"] = float(threshold_raw.strip())
        except (ValueError, AttributeError):
            tf_data.setdefault("threshold", 1.0)

    write_json_atomic(out_path, td)
    return td


def migrate_trainer_state(coin_path, coin):
    """Build trainer_state.json from the 4 trainer flat files."""
    out_path = os.path.join(coin_path, "trainer_state.json")

    # Read old files
    status = read_json(os.path.join(coin_path, "trainer_status.json"), {})
    failure = read_json(os.path.join(coin_path, "trainer_failure_info.json"), {})
    last_training = read(os.path.join(coin_path, "trainer_last_training_time.txt")).strip()
    last_start = read(os.path.join(coin_path, "trainer_last_start_time.txt")).strip()

    state = {
        "coin": status.get("coin", coin),
        "state": status.get("state", "UNKNOWN"),
        "started_at": status.get("started_at", last_start or ""),
        "timestamp": status.get("timestamp", ""),
        "last_training_time": status.get("finished_at", last_training) or last_training,
        "failure": failure,
    }
    if "finished_at" in status:
        state["finished_at"] = status["finished_at"]
    if "failed_at" in status:
        state["failed_at"] = status["failed_at"]
    if "error" in status:
        state["error"] = status["error"]

    write_json_atomic(out_path, state)
    return state


OLD_FILES = (
    [f"memories_{tf}.txt" for tf in TIMEFRAMES]
    + [f"memory_weights_{tf}.txt" for tf in TIMEFRAMES]
    + [f"memory_weights_high_{tf}.txt" for tf in TIMEFRAMES]
    + [f"memory_weights_low_{tf}.txt" for tf in TIMEFRAMES]
    + [f"neural_perfect_threshold_{tf}.txt" for tf in TIMEFRAMES]
    + [
        "trainer_status.json",
        "trainer_failure_info.json",
        "trainer_last_training_time.txt",
        "trainer_last_start_time.txt",
        "alerts_version.txt",
        "futures_long_onoff.txt",
        "futures_short_onoff.txt",
        "futures_long_profit_margin.txt",
        "futures_short_profit_margin.txt",
        "low_bound_prices.html",
        "high_bound_prices.html",
    ]
)


def main():
    delete = "--delete" in sys.argv
    script_dir = os.path.dirname(os.path.abspath(__file__))
    coins_dir = os.path.join(script_dir, "state", "coins")

    if not os.path.isdir(coins_dir):
        print(f"ERROR: {coins_dir} not found")
        sys.exit(1)

    coins = list(coin_dirs(coins_dir))
    print(f"Migrating {len(coins)} coins in {coins_dir}")
    if delete:
        print("--delete: old files will be removed after migration")

    for coin, coin_path in coins:
        td = migrate_training_data(coin_path, )
        ts = migrate_trainer_state(coin_path, coin)

        tfs_with_memories = [tf for tf in TIMEFRAMES if td.get(tf, {}).get("memories")]
        print(f"  {coin}: trainer_state={ts['state']!r} last_trained={ts['last_training_time']!r} tfs={len(tfs_with_memories)}/7")

        if delete:
            for fname in OLD_FILES:
                fpath = os.path.join(coin_path, fname)
                if os.path.exists(fpath):
                    os.remove(fpath)

    print("Done.")
    if not delete:
        print("Re-run with --delete to remove old flat files once you've verified the app works.")


if __name__ == "__main__":
    main()
