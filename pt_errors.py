"""Shared error bus for PowerTrader_AI.

Any process (thinker, trader, exchange adapters, controller) calls emit() to
record a structured error/warning to hub_data/errors.jsonl. The web UI reads
this file and displays a filterable error log.

Each write is a single JSON line append — atomic on Linux for payloads under
~4 KB, so multiple processes can write concurrently without corruption.
"""

import json
import os
import time
from datetime import datetime
from pt_env import utcnow

_path_cache: str | None = None


def _errors_path() -> str:
    global _path_cache
    if _path_cache is None:
        from pt_env import PTEnv
        env = PTEnv(os.path.dirname(os.path.abspath(__file__)))
        _path_cache = str(env.errors_path())
    return _path_cache


def emit(component: str, message: str, level: str = "error", detail: str = "") -> None:
    """Log a structured error/warning to stdout and to errors.jsonl."""
    _now = datetime.now()
    _ts = _now.strftime("%Y%m%d:%H%M%S") + f".{_now.microsecond // 1000:03d}"
    print(f"{_ts} {level.upper():<8} [{component}] {message}", flush=True)
    try:
        path = _errors_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        entry = json.dumps({
            "ts": utcnow(),
            "component": component,
            "level": level,
            "message": message,
            "detail": detail,
        })
        with open(path, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass  # Never fail trying to log

    try:
        import pt_notify
        pt_notify.notify_error(component, level, message, detail)
    except Exception:
        pass


def trim(max_lines: int = 500) -> None:
    """Trim errors.jsonl to the most recent max_lines entries. Called at web startup."""
    try:
        path = _errors_path()
        if not os.path.isfile(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) <= max_lines:
            return
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines[-max_lines:])
    except Exception:
        pass
