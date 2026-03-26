from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from autoresearch.models import ClosedTrade, ExperimentResult, Hypothesis, ResearchDataset
from autoresearch.utils import dt_to_iso, max_drawdown, mean_or_zero, safe_float, sample_stddev, write_json


def _walk_forward_splits(trades: List[ClosedTrade], requested: int = 3) -> List[Tuple[List[ClosedTrade], List[ClosedTrade]]]:
    ordered = sorted(trades, key=lambda trade: (trade.entry_ts, trade.exit_ts))
    if len(ordered) < 2:
        return [(ordered, ordered)] if ordered else []
    splits = min(max(requested, 1), max(1, len(ordered) - 1))
    windows: List[Tuple[List[ClosedTrade], List[ClosedTrade]]] = []
    for split_idx in range(1, splits + 1):
        cut = max(1, round(len(ordered) * split_idx / (splits + 1)))
        train = ordered[:cut]
        test = ordered[cut:]
        if not test:
            test = ordered[-1:]
        windows.append((train, test))
    return windows


def _ladder_shape(label: str, baseline: List[float]) -> List[float]:
    if label == 'tighter':
        return [round(level * 0.85, 4) for level in baseline]
    if label == 'looser':
        return [round(level * 1.15, 4) for level in baseline]
    return list(baseline)


def _apply_variant(trades: Iterable[ClosedTrade], hypothesis: Hypothesis, candidate: Any) -> List[float]:
    family = hypothesis.family
    baseline = hypothesis.parameter_target.get('baseline_value')
    adjusted: List[float] = []
    for trade in trades:
        pnl = float(trade.pnl_pct)
        context = trade.market_context

        if family == 'entry_gate':
            threshold = int(safe_float(candidate, default=4.0))
            signal = safe_float(getattr(context, 'long_signal', None), default=3.0)
            if signal < threshold:
                if pnl <= 0 or trade.dca_level > 0:
                    continue
                pnl -= 0.15

        elif family == 'allocation':
            base = safe_float(baseline, default=0.005)
            new = safe_float(candidate, default=base)
            if base > 0:
                pnl *= new / base

        elif family == 'dca_aggression':
            base = safe_float(baseline, default=2.0)
            new = safe_float(candidate, default=base)
            if trade.dca_level > 0:
                change = base - new
                if pnl < 0:
                    pnl += abs(change) * 0.9
                else:
                    pnl -= abs(change) * 0.25
            elif trade.dca_level == 0 and new > base and pnl > 0:
                pnl += (new - base) * 0.05

        elif family == 'dca_frequency':
            new_limit = int(safe_float(candidate, default=safe_float(baseline, default=2.0)))
            if trade.dca_level > new_limit:
                if pnl < 0:
                    pnl += (trade.dca_level - new_limit) * 0.6
                else:
                    pnl -= (trade.dca_level - new_limit) * 0.25

        elif family == 'profit_target':
            base = safe_float(baseline, default=5.0)
            new = safe_float(candidate, default=base)
            delta = base - new
            if pnl > 0:
                pnl += delta * 0.35
            elif trade.hold_minutes >= 240:
                pnl += delta * 0.1

        elif family == 'trailing_gap':
            base = safe_float(baseline, default=0.5)
            new = safe_float(candidate, default=base)
            delta = base - new
            peak = safe_float(trade.peak_pnl_pct, default=max(pnl, 0.0))
            if peak > 0:
                pnl = min(peak, pnl + (delta * 0.8))

        elif family == 'dca_ladder':
            base_ladder = list(baseline or [-2.5, -5.0, -10.0, -20.0, -30.0, -40.0, -50.0])
            new_ladder = candidate if isinstance(candidate, list) else _ladder_shape(str(candidate), base_ladder)
            if trade.dca_level > 0:
                base_avg = abs(mean_or_zero(base_ladder[: trade.dca_level + 1]))
                new_avg = abs(mean_or_zero(new_ladder[: trade.dca_level + 1]))
                if pnl < 0:
                    pnl += (new_avg - base_avg) * 0.08
                else:
                    pnl += (base_avg - new_avg) * 0.04

        adjusted.append(round(pnl, 6))
    return adjusted


def _metrics_from_returns(returns: List[float]) -> Dict[str, Any]:
    if not returns:
        return {
            'net_expectancy_pct': 0.0,
            'return_pct': 0.0,
            'trade_count': 0,
            'win_rate': 0.0,
            'payoff_ratio': 0.0,
            'max_drawdown_pct': 0.0,
            'sharpe_like': 0.0,
            'left_tail_loss_pct': 0.0,
        }
    wins = [ret for ret in returns if ret > 0]
    losses = [ret for ret in returns if ret <= 0]
    total = sum(returns)
    payoff_ratio = 0.0
    if wins and losses:
        payoff_ratio = (sum(wins) / len(wins)) / max(abs(sum(losses) / len(losses)), 1e-9)
    return {
        'net_expectancy_pct': total / len(returns),
        'return_pct': total,
        'trade_count': len(returns),
        'win_rate': len(wins) / len(returns),
        'payoff_ratio': payoff_ratio,
        'max_drawdown_pct': max_drawdown(returns),
        'sharpe_like': 0.0 if len(returns) < 2 else (sum(returns) / len(returns)) / max(sample_stddev(returns), 1e-9),
        'left_tail_loss_pct': min(returns),
    }


def _average_metrics(metrics_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not metrics_rows:
        return _metrics_from_returns([])
    keys = ('net_expectancy_pct', 'return_pct', 'trade_count', 'win_rate', 'payoff_ratio', 'max_drawdown_pct', 'sharpe_like', 'left_tail_loss_pct')
    return {key: mean_or_zero([safe_float(row.get(key)) for row in metrics_rows]) for key in keys}


def _candidate_payload(hypothesis: Hypothesis, dataset_name: str, output_dir: Path, candidate: Any, split_rows: List[Dict[str, Any]]) -> str:
    artifact_dir = output_dir / 'artifacts' / hypothesis.hypothesis_id / dataset_name
    artifact_dir.mkdir(parents=True, exist_ok=True)
    label = 'list' if isinstance(candidate, list) else str(candidate).replace('.', '_').replace('-', 'neg_')
    artifact_path = artifact_dir / f'candidate_{label}.json'
    write_json(
        artifact_path,
        {
            'hypothesis_id': hypothesis.hypothesis_id,
            'dataset': dataset_name,
            'candidate': candidate,
            'splits': split_rows,
        },
    )
    return str(artifact_path)


def run_experiments(datasets: Dict[str, ResearchDataset], hypotheses: List[Hypothesis], output_dir: Path) -> List[ExperimentResult]:
    results: List[ExperimentResult] = []
    for hypothesis in hypotheses:
        candidates = list(hypothesis.parameter_target.get('candidate_values') or [hypothesis.parameter_target.get('proposed_value')])
        for dataset_name, dataset in datasets.items():
            windows = _walk_forward_splits(dataset.closed_trades, hypothesis.experiment_plan.get('walk_forward_splits', 3))
            baseline_dataset = _metrics_from_returns([trade.pnl_pct for trade in dataset.closed_trades])
            if not windows:
                results.append(
                    ExperimentResult(
                        hypothesis_id=hypothesis.hypothesis_id,
                        dataset=dataset_name,
                        engine=hypothesis.experiment_plan['engine'],
                        train_window={'start': None, 'end': None},
                        test_window={'start': None, 'end': None},
                        params={
                            'baseline_value': hypothesis.parameter_target.get('baseline_value'),
                            'proposed_value': hypothesis.parameter_target.get('proposed_value'),
                        },
                        metrics={
                            'baseline_train': baseline_dataset,
                            'baseline_test': baseline_dataset,
                            'selected': {'candidate': hypothesis.parameter_target.get('proposed_value'), 'test_metrics': baseline_dataset},
                            'candidate_results': [],
                            'baseline_dataset': baseline_dataset,
                        },
                        warnings=['No closed trades available for walk-forward evaluation.'],
                    )
                )
                continue

            baseline_train_rows: List[Dict[str, Any]] = []
            baseline_test_rows: List[Dict[str, Any]] = []
            for train_trades, test_trades in windows:
                baseline_train_rows.append(_metrics_from_returns([trade.pnl_pct for trade in train_trades]))
                baseline_test_rows.append(_metrics_from_returns([trade.pnl_pct for trade in test_trades]))

            avg_baseline_train = _average_metrics(baseline_train_rows)
            avg_baseline_test = _average_metrics(baseline_test_rows)
            candidate_results: List[Dict[str, Any]] = []
            for candidate in candidates:
                split_rows: List[Dict[str, Any]] = []
                candidate_train_rows: List[Dict[str, Any]] = []
                candidate_test_rows: List[Dict[str, Any]] = []
                for train_trades, test_trades in windows:
                    adjusted_train = _apply_variant(train_trades, hypothesis, candidate)
                    adjusted_test = _apply_variant(test_trades, hypothesis, candidate)
                    train_metrics = _metrics_from_returns(adjusted_train)
                    test_metrics = _metrics_from_returns(adjusted_test)
                    candidate_train_rows.append(train_metrics)
                    candidate_test_rows.append(test_metrics)
                    split_rows.append(
                        {
                            'train_window': {
                                'start': dt_to_iso(train_trades[0].entry_ts) if train_trades else None,
                                'end': dt_to_iso(train_trades[-1].exit_ts) if train_trades else None,
                            },
                            'test_window': {
                                'start': dt_to_iso(test_trades[0].entry_ts) if test_trades else None,
                                'end': dt_to_iso(test_trades[-1].exit_ts) if test_trades else None,
                            },
                            'baseline_train': _metrics_from_returns([trade.pnl_pct for trade in train_trades]),
                            'baseline_test': _metrics_from_returns([trade.pnl_pct for trade in test_trades]),
                            'candidate_train': train_metrics,
                            'candidate_test': test_metrics,
                        }
                    )
                avg_candidate_train = _average_metrics(candidate_train_rows)
                avg_candidate_test = _average_metrics(candidate_test_rows)
                candidate_result = {
                    'candidate': candidate,
                    'avg_oos_expectancy_delta': avg_candidate_test['net_expectancy_pct'] - avg_baseline_test['net_expectancy_pct'],
                    'avg_drawdown_delta': avg_baseline_test['max_drawdown_pct'] - avg_candidate_test['max_drawdown_pct'],
                    'avg_trade_count': avg_candidate_test['trade_count'],
                    'avg_candidate_train': avg_candidate_train,
                    'avg_candidate_test': avg_candidate_test,
                    'splits': split_rows,
                }
                candidate_result['artifact'] = _candidate_payload(hypothesis, dataset_name, output_dir, candidate, split_rows)
                candidate_results.append(candidate_result)

            candidate_results.sort(
                key=lambda item: (item['avg_oos_expectancy_delta'], item['avg_drawdown_delta'], item['avg_trade_count']),
                reverse=True,
            )
            selected = candidate_results[0]
            first_window = windows[0]
            last_window = windows[-1]
            results.append(
                ExperimentResult(
                    hypothesis_id=hypothesis.hypothesis_id,
                    dataset=dataset_name,
                    engine=hypothesis.experiment_plan['engine'],
                    train_window={
                        'start': dt_to_iso(first_window[0][0].entry_ts) if first_window[0] else None,
                        'end': dt_to_iso(first_window[0][-1].exit_ts) if first_window[0] else None,
                    },
                    test_window={
                        'start': dt_to_iso(last_window[1][0].entry_ts) if last_window[1] else None,
                        'end': dt_to_iso(last_window[1][-1].exit_ts) if last_window[1] else None,
                    },
                    params={
                        'baseline_value': hypothesis.parameter_target.get('baseline_value'),
                        'proposed_value': hypothesis.parameter_target.get('proposed_value'),
                        'candidate_values': candidates,
                    },
                    metrics={
                        'baseline_train': avg_baseline_train,
                        'baseline_test': avg_baseline_test,
                        'selected': {
                            'candidate': selected['candidate'],
                            'test_metrics': selected['avg_candidate_test'],
                            'train_metrics': selected['avg_candidate_train'],
                        },
                        'candidate_results': candidate_results,
                        'baseline_dataset': baseline_dataset,
                    },
                    artifacts=[item['artifact'] for item in candidate_results],
                )
            )
    return results
