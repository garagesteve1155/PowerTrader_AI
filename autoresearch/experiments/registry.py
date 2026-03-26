from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from autoresearch.utils import REPO_ROOT, iso_now, parse_timestamp, read_jsonl, write_json


REGISTRY_PATH = REPO_ROOT / "reports" / "autoresearch" / "experiment_registry.jsonl"


def registry_path() -> Path:
    return REGISTRY_PATH


def load_registry(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    rows = read_jsonl(path or REGISTRY_PATH)
    return [row for row in rows if isinstance(row, dict)]


def _is_active_for_mode(entry: Dict[str, Any], mode: str, as_of_ts: float) -> bool:
    if str(entry.get("mode") or "").lower() != mode.lower():
        return False
    start_ts = parse_timestamp(entry.get("applied_at"))
    if start_ts is None or start_ts > as_of_ts:
        return False
    end_ts = parse_timestamp(entry.get("ended_at"))
    if end_ts is not None and end_ts < as_of_ts:
        return False
    return bool(entry.get("active", True))


def active_experiment_for_mode(mode: str, as_of_ts: Optional[float] = None) -> Optional[Dict[str, Any]]:
    effective_ts = as_of_ts if as_of_ts is not None else parse_timestamp(iso_now())
    active = [row for row in load_registry() if _is_active_for_mode(row, mode, effective_ts or 0.0)]
    if not active:
        return None
    return max(active, key=lambda row: parse_timestamp(row.get("applied_at")) or 0.0)


def append_registry_entry(entry: Dict[str, Any], snapshot_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = dict(entry)
    if "recorded_at" not in serialized:
        serialized["recorded_at"] = iso_now()
    with path.open("a", encoding="utf-8") as handle:
        import json

        handle.write(json.dumps(serialized) + "\n")
    if snapshot_config is not None and serialized.get("experiment_id"):
        snapshot_path = path.parent / "experiments" / str(serialized["experiment_id"]) / "config_snapshot.json"
        write_json(snapshot_path, snapshot_config)
        serialized["config_snapshot_path"] = str(snapshot_path)
    return serialized
