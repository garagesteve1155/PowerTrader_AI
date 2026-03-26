from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, pstdev
from typing import Any, Dict, Iterable, List, Optional


REPO_ROOT = Path(__file__).resolve().parent.parent


def repo_root() -> Path:
    return REPO_ROOT


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso_now() -> str:
    return now_utc().isoformat().replace('+00:00', 'Z')


def default_run_id() -> str:
    return now_utc().strftime('%Y%m%dT%H%M%SZ')


def normalize_days(days: Any) -> Any:
    if isinstance(days, str) and days.strip().lower() == 'all':
        return 'all'
    try:
        return int(days)
    except (TypeError, ValueError):
        return 90


def parse_timestamp(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        numeric = float(value)
        if numeric > 10_000_000_000:
            numeric /= 1000.0
        return numeric
    except (TypeError, ValueError):
        pass

    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace('Z', '+00:00')
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def dt_to_iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace('+00:00', 'Z')


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    with path.open('r', encoding='utf-8') as handle:
        return json.load(handle)


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open('r', encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as handle:
        json.dump(payload, handle, indent=2)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _merge_dict(base: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _extract_powertrader_runtime_settings(raw: Dict[str, Any]) -> Dict[str, Any]:
    keys = (
        'trade_start_level',
        'start_allocation_pct',
        'dca_multiplier',
        'dca_levels',
        'max_dca_buys_per_24h',
        'pm_start_pct_no_dca',
        'pm_start_pct_with_dca',
        'trailing_gap_pct',
    )
    return {key: raw[key] for key in keys if key in raw}


def load_baseline_config(path: Optional[str]) -> Dict[str, Any]:
    root = repo_root()
    default_profile = read_json(root / 'autoresearch' / 'configs' / 'powertrader_research_profile.json', default={}) or {}
    gui_settings = read_json(root / 'gui_settings.json', default={}) or {}
    baseline = _merge_dict(default_profile, _extract_powertrader_runtime_settings(gui_settings))
    if not path:
        return baseline
    config_path = Path(path)
    if not config_path.is_absolute():
        config_path = root / config_path
    user_payload = read_json(config_path, default={}) or {}
    return _merge_dict(baseline, user_payload)


def resolve_output_dir(run_id: str, output_dir: Optional[str]) -> Path:
    if output_dir:
        out = Path(output_dir)
        if not out.is_absolute():
            out = repo_root() / out
        return out
    return repo_root() / 'reports' / 'autoresearch' / 'weekly' / run_id


def slugify(value: str) -> str:
    return re.sub(r'[^a-z0-9]+', '-', value.lower()).strip('-')


def cutoff_timestamp(days: Any) -> float:
    normalized = normalize_days(days)
    if normalized == 'all':
        return 0.0
    return (now_utc() - timedelta(days=int(normalized))).timestamp()


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == '':
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def hold_bucket(minutes: float) -> str:
    if minutes < 15:
        return 'sub_15m'
    if minutes < 60:
        return '15m_1h'
    if minutes < 240:
        return '1h_4h'
    if minutes < 1440:
        return '4h_1d'
    return '1d_plus'


def pnl_bucket(pnl_pct: float) -> str:
    if pnl_pct <= -5:
        return 'loss_gt_5'
    if pnl_pct < 0:
        return 'loss_0_5'
    if pnl_pct < 3:
        return 'win_0_3'
    return 'win_gt_3'


def mean_or_zero(values: Iterable[float]) -> float:
    values = list(values)
    return mean(values) if values else 0.0


def sharpe_like(returns: List[float]) -> float:
    if len(returns) < 2:
        return 0.0
    sigma = pstdev(returns)
    if math.isclose(sigma, 0.0):
        return 0.0
    return mean(returns) / sigma


def max_drawdown(returns: List[float]) -> float:
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for ret in returns:
        equity *= 1.0 + (ret / 100.0)
        peak = max(peak, equity)
        if peak > 0:
            drawdown = ((peak - equity) / peak) * 100.0
            worst = max(worst, drawdown)
    return worst


def ensure_profile_patch_target() -> str:
    return 'autoresearch/configs/powertrader_research_profile.json'


def sample_stddev(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = mean(values)
    variance = sum((value - avg) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)


def sorted_top(items: Iterable[Dict[str, Any]], key: str, limit: int) -> List[Dict[str, Any]]:
    return sorted(items, key=lambda item: item.get(key, 0), reverse=True)[:limit]
