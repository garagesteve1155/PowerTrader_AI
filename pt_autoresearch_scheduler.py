"""Simple scheduler helpers for PowerTrader autoresearch cadence."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from pt_autoresearch_orchestrator import run_cycle


def evaluate_cycle(output_dir: str, min_expectancy_delta: float = 0.15, min_trade_count: int = 3) -> Dict[str, Any]:
    path = Path(output_dir)
    scores_f = path / 'scores.json'
    if not scores_f.exists():
        return {'published': [], 'reason': 'no_scores'}
    scores = json.loads(scores_f.read_text(encoding='utf-8'))
    ranked = scores.get('ranked', [])
    if not ranked:
        return {'published': [], 'reason': 'no_ranked'}
    top = ranked[0]
    expectancy = float(top.get('avg_oos_expectancy_delta', 0.0) or 0.0)
    trade_count = float(top.get('avg_trade_count', 0.0) or 0.0)
    if expectancy >= min_expectancy_delta and trade_count >= min_trade_count:
        return {
            'published': [],
            'reason': 'approved_for_review',
            'expectancy': expectancy,
            'trade_count': trade_count,
            'hypothesis_id': top.get('hypothesis_id'),
        }
    return {
        'published': [],
        'reason': 'criteria_not_met',
        'expectancy': expectancy,
        'trade_count': trade_count,
        'hypothesis_id': top.get('hypothesis_id'),
    }


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--cadence', choices=['6h', 'daily'], default='6h')
    parser.add_argument('--mode', default='powertrader')
    parser.add_argument('--days', default='all')
    parser.add_argument('--run-id', default=None)
    args = parser.parse_args()

    out = run_cycle(mode=args.mode, days=args.days, run_id=args.run_id, publish_artifacts=False)
    result = evaluate_cycle(
        out['pipeline'].get('output_dir'),
        min_expectancy_delta=0.10 if args.cadence == '6h' else 0.15,
        min_trade_count=3 if args.cadence == '6h' else 5,
    )
    print(json.dumps({'output_dir': out['pipeline'].get('output_dir'), 'result': result}))
