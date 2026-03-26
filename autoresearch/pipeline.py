from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from autoresearch.critic.service import risk_review
from autoresearch.diagnostics.service import diagnose_all
from autoresearch.evaluation.service import score_results
from autoresearch.experiments.service import run_experiments
from autoresearch.hypothesis.service import generate_hypotheses
from autoresearch.ingest.service import load_datasets
from autoresearch.models import ExperimentResult, Hypothesis
from autoresearch.reports.service import render_report
from autoresearch.utils import load_baseline_config, write_json


ASSUMPTIONS = [
    'PowerTrader autoresearch uses this repo and nearby backups as the canonical historical source for v1.',
    'The v1 experiment engine is a local walk-forward replay over normalized closed-trade cohorts, not a live deployment system.',
    'Recommendations remain approval-only; no experiment result is applied automatically to runtime config.',
]


def _serialize_experiments(results: List[ExperimentResult]) -> List[Dict[str, Any]]:
    return [result.to_dict() for result in results]


def _serialize_hypotheses(hypotheses: List[Hypothesis]) -> List[Dict[str, Any]]:
    return [hypothesis.to_dict() for hypothesis in hypotheses]


def run_weekly(mode: str, days: Any, output_dir: Path, baseline_config_path: str | None = None) -> Dict[str, Any]:
    datasets = load_datasets(mode=mode, days=days, baseline_config_path=baseline_config_path)
    baseline_config = load_baseline_config(baseline_config_path)
    diagnostics_by_mode = diagnose_all(datasets)
    hypotheses = generate_hypotheses(diagnostics_by_mode, baseline_config=baseline_config)
    experiment_results = run_experiments(datasets, hypotheses, output_dir)
    scores = score_results(experiment_results)
    risk_payload = risk_review(hypotheses, scores)

    for current_mode, diagnostics in diagnostics_by_mode.items():
        write_json(output_dir / f'diagnostics_{current_mode}.json', diagnostics)
    write_json(output_dir / 'hypotheses.json', _serialize_hypotheses(hypotheses))
    write_json(output_dir / 'experiments.json', _serialize_experiments(experiment_results))
    write_json(output_dir / 'scores.json', scores)
    write_json(output_dir / 'risk_review.json', risk_payload)
    write_json(
        output_dir / 'metadata.json',
        {
            'mode': mode,
            'days': days,
            'baseline_config_path': baseline_config_path,
            'datasets': {
                current_mode: {
                    'history_sources': dataset.history_sources,
                    'provenance_counts': dataset.provenance_counts,
                    'notes': dataset.notes,
                }
                for current_mode, dataset in datasets.items()
            },
        },
    )
    render_report(
        output_dir=output_dir,
        diagnostics_by_mode=diagnostics_by_mode,
        hypotheses=hypotheses,
        experiments=_serialize_experiments(experiment_results),
        score_payload=scores,
        risk_payload=risk_payload,
        assumptions=ASSUMPTIONS,
    )
    return {
        'diagnostics': diagnostics_by_mode,
        'hypotheses': _serialize_hypotheses(hypotheses),
        'experiments': _serialize_experiments(experiment_results),
        'scores': scores,
        'risk_review': risk_payload,
        'output_dir': str(output_dir),
    }
