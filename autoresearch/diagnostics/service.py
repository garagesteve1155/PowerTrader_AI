from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, Iterable, List

from autoresearch.models import ClosedTrade, ResearchDataset
from autoresearch.utils import hold_bucket, mean_or_zero, pnl_bucket, safe_float, sorted_top


def _breakdown(trades: Iterable[ClosedTrade], key_fn) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[ClosedTrade]] = defaultdict(list)
    for trade in trades:
        grouped[key_fn(trade)].append(trade)

    rows: List[Dict[str, Any]] = []
    for key, bucket in grouped.items():
        pnl_values = [trade.pnl_pct for trade in bucket]
        drawdowns = [safe_float(trade.trough_pnl_pct, default=min(trade.pnl_pct, 0.0)) for trade in bucket]
        rows.append(
            {
                'key': key,
                'count': len(bucket),
                'expectancy_pct': mean_or_zero(pnl_values),
                'total_pnl_usd': sum(trade.pnl_usd for trade in bucket),
                'win_rate': sum(1 for trade in bucket if trade.pnl_pct > 0) / len(bucket),
                'avg_hold_minutes': mean_or_zero([trade.hold_minutes for trade in bucket]),
                'drawdown_contribution': abs(sum(drawdowns)),
            }
        )
    return sorted(rows, key=lambda item: (item['count'], item['expectancy_pct']), reverse=True)


def _make_cause(cause_id: str, title: str, trades: List[ClosedTrade]) -> Dict[str, Any]:
    expectancy = mean_or_zero([trade.pnl_pct for trade in trades])
    frequency = len(trades)
    drawdown = abs(sum(safe_float(trade.trough_pnl_pct, default=min(trade.pnl_pct, 0.0)) for trade in trades))
    confidence = min(1.0, frequency / 5.0)
    score = max(0.0, (abs(min(expectancy, 0.0)) * 4.0) + frequency + (drawdown * 0.1)) * confidence
    return {
        'cause_id': cause_id,
        'title': title,
        'score': score,
        'count': frequency,
        'expectancy_pct': expectancy,
        'avg_hold_minutes': mean_or_zero([trade.hold_minutes for trade in trades]),
        'drawdown_contribution': drawdown,
        'confidence': confidence,
        'sample_symbols': sorted({trade.symbol for trade in trades})[:5],
    }


def _cause_candidates(dataset: ResearchDataset) -> List[Dict[str, Any]]:
    trades = dataset.closed_trades
    causes: List[Dict[str, Any]] = []
    if not trades:
        return causes

    weak_entries = [
        trade for trade in trades
        if trade.market_context and trade.market_context.long_signal <= max(safe_float(dataset.baseline_config.get('trade_start_level'), 3.0), 3.0)
        and trade.pnl_pct <= 0
    ]
    if weak_entries:
        causes.append(_make_cause('weak_entries', 'Weak entry-gate trades', weak_entries))

    dca_pressure = [trade for trade in trades if trade.dca_level > 0 and trade.pnl_pct < 0]
    if dca_pressure:
        causes.append(_make_cause('dca_pressure', 'DCA pressure on losing trades', dca_pressure))

    long_holds = [trade for trade in trades if trade.hold_minutes >= 240 and trade.pnl_pct <= 2.0]
    if long_holds:
        causes.append(_make_cause('long_holds', 'Long holds with limited payoff', long_holds))

    profit_giveback = [
        trade for trade in trades
        if safe_float(trade.peak_pnl_pct, default=0.0) >= safe_float(dataset.baseline_config.get('pm_start_pct_with_dca'), 2.5)
        and trade.pnl_pct < safe_float(trade.peak_pnl_pct, default=trade.pnl_pct)
    ]
    if profit_giveback:
        causes.append(_make_cause('profit_giveback', 'Trailing exits giving back open gains', profit_giveback))

    drawdown_pressure = [trade for trade in trades if trade.pnl_pct < 0]
    if drawdown_pressure:
        causes.append(_make_cause('drawdown_pressure', 'Capital drawdown pressure', drawdown_pressure))

    while len(causes) < 5:
        worst = sorted(trades, key=lambda trade: trade.pnl_pct)[: max(1, min(3, len(trades)))]
        causes.append(_make_cause(f'fallback_{len(causes) + 1}', f'General underperformance pocket {len(causes) + 1}', worst))
    return sorted(causes, key=lambda item: item['score'], reverse=True)[:5]


def _trade_summary(trades: List[ClosedTrade]) -> Dict[str, Any]:
    return {
        'closed_trade_count': len(trades),
        'avg_expectancy_pct': mean_or_zero([trade.pnl_pct for trade in trades]),
        'total_realized_pnl_usd': sum(trade.pnl_usd for trade in trades),
        'avg_hold_minutes': mean_or_zero([trade.hold_minutes for trade in trades]),
        'win_rate': (sum(1 for trade in trades if trade.pnl_pct > 0) / len(trades)) if trades else 0.0,
        'avg_dca_level': mean_or_zero([float(trade.dca_level) for trade in trades]),
    }


def diagnose_dataset(dataset: ResearchDataset) -> Dict[str, Any]:
    trades = dataset.closed_trades
    diagnostics = {
        'mode': dataset.mode,
        'generated_at': dataset.generated_at,
        'days': dataset.days,
        'summary': {
            **_trade_summary(trades),
            'trade_event_count': len(dataset.trade_events),
            'active_position_count': len(dataset.active_positions),
            'history_source_count': len(dataset.history_sources),
            'provenance_counts': dict(dataset.provenance_counts),
        },
        'baseline_rules': dataset.baseline_config,
        'notes': list(dataset.notes),
        'breakdowns': {
            'by_symbol': _breakdown(trades, lambda trade: trade.symbol),
            'by_exit_reason': _breakdown(trades, lambda trade: trade.exit_tag or 'UNKNOWN'),
            'by_hold_bucket': _breakdown(trades, lambda trade: hold_bucket(trade.hold_minutes)),
            'by_pnl_bucket': _breakdown(trades, lambda trade: pnl_bucket(trade.pnl_pct)),
            'by_dca_level': _breakdown(trades, lambda trade: str(trade.dca_level)),
            'by_source': _breakdown(trades, lambda trade: trade.source),
        },
        'top_causes': sorted_top(_cause_candidates(dataset), 'score', 5),
        'history_sources': dataset.history_sources,
    }
    return diagnostics


def diagnose_all(datasets: Dict[str, ResearchDataset]) -> Dict[str, Dict[str, Any]]:
    return {mode: diagnose_dataset(dataset) for mode, dataset in datasets.items()}
