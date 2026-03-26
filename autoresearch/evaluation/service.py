from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List

from autoresearch.models import ExperimentResult
from autoresearch.utils import mean_or_zero


def score_results(results: List[ExperimentResult]) -> Dict[str, Any]:
    by_hypothesis: Dict[str, List[ExperimentResult]] = defaultdict(list)
    for result in results:
        by_hypothesis[result.hypothesis_id].append(result)

    scored: List[Dict[str, Any]] = []
    for hypothesis_id, group in by_hypothesis.items():
        oos_improvements = []
        trade_counts = []
        drawdown_deltas = []
        sharpe_deltas = []
        datasets = []
        for result in group:
            baseline = result.metrics["baseline_test"]
            selected = result.metrics["selected"]["test_metrics"]
            oos_improvements.append(selected["net_expectancy_pct"] - baseline["net_expectancy_pct"])
            trade_counts.append(selected["trade_count"])
            drawdown_deltas.append(baseline["max_drawdown_pct"] - selected["max_drawdown_pct"])
            sharpe_deltas.append(selected["sharpe_like"] - baseline["sharpe_like"])
            datasets.append(
                {
                    "dataset": result.dataset,
                    "engine": result.engine,
                    "oos_expectancy_delta": oos_improvements[-1],
                    "trade_count": selected["trade_count"],
                    "drawdown_delta": drawdown_deltas[-1],
                    "sharpe_delta": sharpe_deltas[-1],
                }
            )
        scored.append(
            {
                "hypothesis_id": hypothesis_id,
                "avg_oos_expectancy_delta": mean_or_zero(oos_improvements),
                "avg_trade_count": mean_or_zero(trade_counts),
                "avg_drawdown_delta": mean_or_zero(drawdown_deltas),
                "avg_sharpe_delta": mean_or_zero(sharpe_deltas),
                "dataset_breakdown": datasets,
                "regime_robustness": sum(1 for delta in oos_improvements if delta > 0) / len(oos_improvements)
                if oos_improvements
                else 0.0,
            }
        )
    scored.sort(key=lambda item: (item["avg_oos_expectancy_delta"], item["avg_drawdown_delta"]), reverse=True)
    return {"ranked": scored}
