from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Dict

from autoresearch.critic.service import risk_review
from autoresearch.diagnostics.service import diagnose_all
from autoresearch.evaluation.service import score_results
from autoresearch.experiments.service import run_experiments
from autoresearch.hypothesis.service import generate_hypotheses
from autoresearch.ingest.service import load_datasets
from autoresearch.pipeline import ASSUMPTIONS, run_weekly
from autoresearch.reports.service import render_report
from autoresearch.utils import default_run_id, load_baseline_config, normalize_days, read_json, resolve_output_dir, write_json


def build_parser(description: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument('--mode', choices=['powertrader', 'paper', 'live', 'both'], default='powertrader')
    parser.add_argument('--days', default='90')
    parser.add_argument('--output-dir')
    parser.add_argument('--run-id')
    parser.add_argument('--baseline-config')
    return parser


def prepare_output_dir(args: argparse.Namespace) -> Path:
    return resolve_output_dir(args.run_id or default_run_id(), args.output_dir)


def _normalized_days(args: argparse.Namespace) -> Any:
    return normalize_days(args.days)


def run_diagnose(args: argparse.Namespace) -> Dict[str, Any]:
    output_dir = prepare_output_dir(args)
    datasets = load_datasets(mode=args.mode, days=_normalized_days(args), baseline_config_path=args.baseline_config)
    diagnostics = diagnose_all(datasets)
    for mode, payload in diagnostics.items():
        write_json(output_dir / f'diagnostics_{mode}.json', payload)
    return diagnostics


def run_hypothesis_stage(args: argparse.Namespace) -> Any:
    output_dir = prepare_output_dir(args)
    diagnostics = {}
    for current_mode in (['powertrader'] if args.mode in ('powertrader', 'both') else [args.mode]):
        payload = read_json(output_dir / f'diagnostics_{current_mode}.json')
        if payload:
            diagnostics[current_mode] = payload
    if not diagnostics:
        diagnostics = run_diagnose(args)
    hypotheses = generate_hypotheses(diagnostics, load_baseline_config(args.baseline_config))
    write_json(output_dir / 'hypotheses.json', [item.to_dict() for item in hypotheses])
    return hypotheses


def run_experiment_stage(args: argparse.Namespace) -> Any:
    output_dir = prepare_output_dir(args)
    datasets = load_datasets(mode=args.mode, days=_normalized_days(args), baseline_config_path=args.baseline_config)
    hypotheses_data = read_json(output_dir / 'hypotheses.json', default=[])
    hypotheses = generate_hypotheses(diagnose_all(datasets), load_baseline_config(args.baseline_config))
    if hypotheses_data:
        hypothesis_ids = {item['hypothesis_id'] for item in hypotheses_data}
        hypotheses = [item for item in hypotheses if item.hypothesis_id in hypothesis_ids]
    results = run_experiments(datasets, hypotheses, output_dir)
    write_json(output_dir / 'experiments.json', [item.to_dict() for item in results])
    return results


def run_score_stage(args: argparse.Namespace) -> Dict[str, Any]:
    output_dir = prepare_output_dir(args)
    experiments = read_json(output_dir / 'experiments.json', default=[])
    from autoresearch.models import ExperimentResult

    results = [ExperimentResult(**item) for item in experiments]
    scores = score_results(results)
    write_json(output_dir / 'scores.json', scores)
    return scores


def run_risk_stage(args: argparse.Namespace) -> Dict[str, Any]:
    output_dir = prepare_output_dir(args)
    score_payload = read_json(output_dir / 'scores.json', default={})
    hypotheses_data = read_json(output_dir / 'hypotheses.json', default=[])
    from autoresearch.models import Hypothesis

    hypotheses = [Hypothesis(**item) for item in hypotheses_data]
    review = risk_review(hypotheses, score_payload)
    write_json(output_dir / 'risk_review.json', review)
    return review


def run_report_stage(args: argparse.Namespace) -> str:
    output_dir = prepare_output_dir(args)
    diagnostics = {}
    for current_mode in (['powertrader'] if args.mode in ('powertrader', 'both') else [args.mode]):
        payload = read_json(output_dir / f'diagnostics_{current_mode}.json')
        if payload:
            diagnostics[current_mode] = payload
    hypotheses_data = read_json(output_dir / 'hypotheses.json', default=[])
    experiments = read_json(output_dir / 'experiments.json', default=[])
    scores = read_json(output_dir / 'scores.json', default={})
    risk_payload = read_json(output_dir / 'risk_review.json', default={})
    from autoresearch.models import Hypothesis

    hypotheses = [Hypothesis(**item) for item in hypotheses_data]
    return render_report(output_dir, diagnostics, hypotheses, experiments, scores, risk_payload, ASSUMPTIONS)


def run_weekly_stage(args: argparse.Namespace) -> Dict[str, Any]:
    output_dir = prepare_output_dir(args)
    return run_weekly(args.mode, _normalized_days(args), output_dir, args.baseline_config)
