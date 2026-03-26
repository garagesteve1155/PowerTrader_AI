from __future__ import annotations

from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from autoresearch.models import ClosedTrade, MarketContextRow, ResearchDataset, TradeEvent
from autoresearch.utils import cutoff_timestamp, iso_now, load_baseline_config, parse_timestamp, read_json, read_jsonl, repo_root, safe_float


def _history_files(pattern: str) -> List[Path]:
    root = repo_root()
    files = [path for path in root.rglob(pattern) if path.is_file() and '.git' not in path.parts]
    return sorted(files)


def _canonical_modes(mode: str) -> List[str]:
    if mode in ('powertrader', 'both'):
        return ['powertrader']
    return [mode]


def _discover_history_sources(mode: str) -> Dict[str, List[Path]]:
    root = repo_root()
    all_trade_history = _history_files('trade_history.jsonl')
    all_simulator = _history_files('simulator_trades.json')
    all_active = _history_files('active_positions.json')
    all_pnl = _history_files('pnl_ledger.json')
    all_equity = _history_files('account_value_history.jsonl')

    if mode == 'paper':
        trade_history = [path for path in all_trade_history if 'paper' in path.parts]
        simulator = [path for path in all_simulator if 'paper' in path.parts or path.parent == root / 'hub_data' / 'paper']
        active = [path for path in all_active if 'paper' in path.parts]
        pnl = [path for path in all_pnl if 'paper' in path.parts]
        equity = [path for path in all_equity if 'paper' in path.parts]
    elif mode == 'live':
        trade_history = [path for path in all_trade_history if 'paper' not in path.parts]
        simulator = [path for path in all_simulator if 'paper' not in path.parts]
        active = [path for path in all_active if 'paper' not in path.parts]
        pnl = [path for path in all_pnl if 'paper' not in path.parts]
        equity = [path for path in all_equity if 'paper' not in path.parts]
    else:
        trade_history = all_trade_history
        simulator = all_simulator
        active = all_active
        pnl = all_pnl
        equity = all_equity

    return {
        'trade_history': trade_history,
        'simulator': simulator,
        'active_positions': active,
        'pnl_ledger': pnl,
        'equity_history': equity,
    }


def _mode_from_path(path: Path, fallback: str) -> str:
    text = str(path).lower()
    if 'paper' in text:
        return 'paper'
    if fallback == 'powertrader':
        return 'powertrader'
    return fallback


def _context_for_symbol(symbol: str, ts: float, coin_dirs: Dict[str, Path]) -> MarketContextRow:
    folder = coin_dirs.get(symbol.upper())
    if not folder:
        return MarketContextRow(symbol=symbol, ts=ts, entry_signal_strength=3.0, long_signal=3.0, short_signal=0.0)

    def _read_number(name: str, default: float = 0.0) -> float:
        path = folder / name
        if not path.exists():
            return default
        try:
            return safe_float(path.read_text(encoding='utf-8').strip(), default=default)
        except OSError:
            return default

    long_signal = _read_number('long_dca_signal.txt', 3.0)
    short_signal = _read_number('short_dca_signal.txt', 0.0)
    return MarketContextRow(
        symbol=symbol,
        ts=ts,
        entry_signal_strength=max(long_signal, 0.0),
        long_signal=long_signal,
        short_signal=short_signal,
    )


def _coin_dirs() -> Dict[str, Path]:
    root = repo_root()
    mapping: Dict[str, Path] = {'BTC': root}
    for name in ('BNB', 'DOGE', 'ETH', 'XRP'):
        path = root / name
        if path.exists():
            mapping[name] = path
    return mapping


def _load_trade_events(mode: str, source_files: Dict[str, List[Path]], cutoff: float) -> Tuple[List[TradeEvent], List[str]]:
    events: List[TradeEvent] = []
    notes: List[str] = []
    for path in source_files['trade_history']:
        rows = read_jsonl(path)
        if not rows:
            continue
        notes.append(f'direct trade history: {path}')
        for row in rows:
            ts = parse_timestamp(row.get('ts') or row.get('timestamp'))
            if ts is None or ts < cutoff:
                continue
            symbol = str(row.get('symbol') or '').replace('-USDT', '').replace('-USD', '').upper()
            if not symbol:
                continue
            events.append(
                TradeEvent(
                    timestamp=ts,
                    mode=_mode_from_path(path, mode),
                    symbol=symbol,
                    side=str(row.get('side') or '').lower(),
                    tag=row.get('tag'),
                    qty=safe_float(row.get('qty')),
                    price=safe_float(row.get('price')),
                    pnl_pct=safe_float(row.get('pnl_pct')) if row.get('pnl_pct') is not None else None,
                    realized_profit_usd=safe_float(row.get('realized_profit_usd')) if row.get('realized_profit_usd') is not None else None,
                    order_id=row.get('order_id'),
                    buying_power_before=safe_float(row.get('buying_power_before')) if row.get('buying_power_before') is not None else None,
                    buying_power_after=safe_float(row.get('buying_power_after')) if row.get('buying_power_after') is not None else None,
                    buying_power_delta=safe_float(row.get('buying_power_delta')) if row.get('buying_power_delta') is not None else None,
                    source='trade_history_direct',
                )
            )
    return sorted(events, key=lambda item: item.timestamp), notes


def _pair_closed_trades(events: List[TradeEvent], coin_dirs: Dict[str, Path]) -> List[ClosedTrade]:
    inventory: Dict[str, Deque[TradeEvent]] = defaultdict(deque)
    trades: List[ClosedTrade] = []
    for event in events:
        if event.side == 'buy':
            inventory[event.symbol].append(event)
            continue
        if event.side != 'sell':
            continue
        matched: List[TradeEvent] = []
        remaining = event.qty
        while remaining > 0 and inventory[event.symbol]:
            lot = inventory[event.symbol][0]
            matched.append(lot)
            remaining -= lot.qty
            inventory[event.symbol].popleft()
        if not matched:
            continue
        qty = sum(item.qty for item in matched) or event.qty or 0.0
        entry_price = sum(item.qty * item.price for item in matched) / max(qty, 1e-9)
        entry_ts = min(item.timestamp for item in matched)
        pnl_pct = event.pnl_pct
        if pnl_pct is None and entry_price > 0:
            pnl_pct = ((event.price - entry_price) / entry_price) * 100.0
        pnl_usd = event.realized_profit_usd
        if pnl_usd is None:
            pnl_usd = (event.price - entry_price) * qty
        dca_level = max(len(matched) - 1, 0)
        trades.append(
            ClosedTrade(
                symbol=event.symbol,
                mode=event.mode,
                entry_ts=entry_ts,
                exit_ts=event.timestamp,
                hold_minutes=max((event.timestamp - entry_ts) / 60.0, 0.0),
                pnl_pct=safe_float(pnl_pct),
                pnl_usd=safe_float(pnl_usd),
                entry_price=entry_price,
                exit_price=event.price,
                exit_tag=event.tag,
                dca_level=dca_level,
                source='direct_trade_history',
                qty=qty,
                entry_order_id=matched[0].order_id,
                exit_order_id=event.order_id,
                entry_buying_power=matched[0].buying_power_after,
                exit_buying_power=event.buying_power_after,
                peak_pnl_pct=max(safe_float(event.pnl_pct), 0.0),
                trough_pnl_pct=min(safe_float(event.pnl_pct), 0.0),
                market_context=_context_for_symbol(event.symbol, entry_ts, coin_dirs),
            )
        )
    return trades


def _closed_trades_from_simulator(mode: str, source_files: Dict[str, List[Path]], cutoff: float, coin_dirs: Dict[str, Path]) -> Tuple[List[ClosedTrade], List[str]]:
    trades: List[ClosedTrade] = []
    notes: List[str] = []
    for path in source_files['simulator']:
        payload = read_json(path, default={}) or {}
        closed = payload.get('closed_trades') or []
        if not closed:
            continue
        notes.append(f'reconstructed simulator history: {path}')
        for index, row in enumerate(closed):
            exit_ts = parse_timestamp(row.get('timestamp'))
            if exit_ts is None or exit_ts < cutoff:
                continue
            symbol = str(row.get('symbol') or '').replace('-USDT', '').replace('-USD', '').upper()
            if not symbol:
                continue
            entry_price = safe_float(row.get('entry_price'))
            exit_price = safe_float(row.get('exit_price'))
            qty = safe_float(row.get('qty'))
            pnl_pct = row.get('pnl_pct')
            if pnl_pct is None and entry_price > 0:
                pnl_pct = ((exit_price - entry_price) / entry_price) * 100.0
            pnl_usd = row.get('pnl')
            if pnl_usd is None:
                pnl_usd = (exit_price - entry_price) * qty
            trades.append(
                ClosedTrade(
                    symbol=symbol,
                    mode=_mode_from_path(path, mode),
                    entry_ts=exit_ts,
                    exit_ts=exit_ts,
                    hold_minutes=0.0,
                    pnl_pct=safe_float(pnl_pct),
                    pnl_usd=safe_float(pnl_usd),
                    entry_price=entry_price,
                    exit_price=exit_price,
                    exit_tag='SIM_EXIT',
                    dca_level=0,
                    source='reconstructed_simulator',
                    qty=qty,
                    entry_order_id=f'{path.name}-entry-{index}',
                    exit_order_id=f'{path.name}-exit-{index}',
                    peak_pnl_pct=max(safe_float(pnl_pct), 0.0),
                    trough_pnl_pct=min(safe_float(pnl_pct), 0.0),
                    market_context=_context_for_symbol(symbol, exit_ts, coin_dirs),
                )
            )
    return trades, notes


def _aggregate_active_positions(paths: List[Path]) -> Dict[str, Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for path in paths:
        payload = read_json(path, default={}) or {}
        if isinstance(payload, dict):
            merged.update(payload)
    return merged


def _dataset_notes(source_files: Dict[str, List[Path]], direct_trade_count: int, reconstructed_trade_count: int) -> List[str]:
    notes = [
        f"found {len(source_files['trade_history'])} trade_history files",
        f"found {len(source_files['simulator'])} simulator files",
        f"found {len(source_files['equity_history'])} equity history files",
    ]
    if direct_trade_count == 0:
        notes.append('no direct buy/sell ledger was found in this checkout; simulator files are being used as reconstructed closed-trade history')
    if direct_trade_count + reconstructed_trade_count <= 1:
        notes.append('historical sample size is very small in the current workspace, so experiment outputs should be treated as scaffolding rather than decision-grade evidence')
    return notes


def load_dataset(mode: str, days: Any, baseline_config_path: Optional[str] = None) -> ResearchDataset:
    cutoff = cutoff_timestamp(days)
    baseline_config = load_baseline_config(baseline_config_path)
    source_files = _discover_history_sources(mode)
    coin_dirs = _coin_dirs()

    trade_events, direct_notes = _load_trade_events(mode, source_files, cutoff)
    direct_closed = _pair_closed_trades(trade_events, coin_dirs)
    reconstructed_closed, simulator_notes = _closed_trades_from_simulator(mode, source_files, cutoff, coin_dirs)

    closed_index = set()
    closed_trades: List[ClosedTrade] = []
    for trade in direct_closed + reconstructed_closed:
        key = (trade.symbol, round(trade.exit_ts, 3), round(trade.qty, 8), round(trade.exit_price, 8), trade.source)
        if key in closed_index:
            continue
        closed_index.add(key)
        closed_trades.append(trade)
    closed_trades.sort(key=lambda item: (item.entry_ts, item.exit_ts, item.symbol))

    provenance = Counter(trade.source for trade in closed_trades)
    history_sources = [str(path) for group in source_files.values() for path in group]
    notes = direct_notes + simulator_notes + _dataset_notes(source_files, len(direct_closed), len(reconstructed_closed))

    return ResearchDataset(
        mode=mode,
        days=days,
        generated_at=iso_now(),
        trade_events=trade_events,
        closed_trades=closed_trades,
        active_positions=_aggregate_active_positions(source_files['active_positions']),
        baseline_config=baseline_config,
        history_sources=history_sources,
        provenance_counts=dict(provenance),
        notes=notes,
    )


def load_datasets(mode: str, days: Any, baseline_config_path: Optional[str] = None) -> Dict[str, ResearchDataset]:
    return {
        current_mode: load_dataset(current_mode, days=days, baseline_config_path=baseline_config_path)
        for current_mode in _canonical_modes(mode)
    }
