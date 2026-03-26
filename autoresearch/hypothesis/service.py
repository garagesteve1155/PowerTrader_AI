from __future__ import annotations

from typing import Any, Dict, List

from autoresearch.configs.catalog import VARIABLE_CATALOG
from autoresearch.models import Hypothesis
from autoresearch.utils import ensure_profile_patch_target, safe_float, slugify


def _get_by_path(config: Dict[str, Any], path: str, default: Any = None) -> Any:
    current: Any = config
    for part in path.split('.'):
        if not isinstance(current, dict):
            return default
        current = current.get(part)
    return default if current is None else current


def _set_by_path(path: str, value: Any) -> Dict[str, Any]:
    parts = path.split('.')
    nested: Any = value
    for part in reversed(parts):
        nested = {part: nested}
    return nested


def _find_source_cause(top_causes: List[Dict[str, Any]], cause_ids: List[str]) -> Dict[str, Any]:
    for cause_id in cause_ids:
        for cause in top_causes:
            if cause.get('cause_id') == cause_id:
                return cause
    return top_causes[0] if top_causes else {
        'title': 'General underperformance',
        'score': 0.0,
        'cause_id': 'general_underperformance',
    }


def _candidate_values(entry: Dict[str, Any], baseline_value: Any) -> List[Any]:
    if entry['path'] == 'dca_levels':
        baseline = list(baseline_value or [-2.5, -5.0, -10.0, -20.0, -30.0, -40.0, -50.0])
        tighter = [round(level * 0.85, 4) for level in baseline]
        looser = [round(level * 1.15, 4) for level in baseline]
        return [tighter, looser]

    if 'candidate_values' in entry:
        return list(entry['candidate_values'])

    baseline = safe_float(baseline_value, default=0.0)
    values: List[Any] = []
    for offset in entry.get('candidate_offsets', []):
        proposed = baseline + safe_float(offset, default=0.0)
        if entry.get('min') is not None:
            proposed = max(proposed, safe_float(entry['min']))
        if entry.get('max') is not None:
            proposed = min(proposed, safe_float(entry['max']))
        if isinstance(baseline_value, int):
            values.append(int(round(proposed)))
        else:
            values.append(round(proposed, 4))
    return values


def _expected_effect(family: str) -> str:
    effects = {
        'entry_gate': 'Filter weaker entry setups by requiring a stronger long signal before starting a trade.',
        'allocation': 'Change initial exposure per position without changing the core entry logic.',
        'dca_aggression': 'Reduce or increase how quickly average cost expands during drawdowns.',
        'dca_frequency': 'Change how often repeated averaging is allowed inside a rolling 24-hour window.',
        'profit_target': 'Change when the trailing-profit logic starts protecting gains.',
        'trailing_gap': 'Tighten or loosen how much profit is given back after the trailing line activates.',
        'dca_ladder': 'Move the hard DCA ladder tighter or looser while preserving the staged structure.',
    }
    return effects.get(family, 'Improve robustness while staying close to the native PowerTrader strategy.')


def generate_hypotheses(
    diagnostics_by_mode: Dict[str, Dict[str, Any]],
    baseline_config: Dict[str, Any],
) -> List[Hypothesis]:
    top_causes: List[Dict[str, Any]] = []
    for mode, diagnostics in diagnostics_by_mode.items():
        for cause in diagnostics.get('top_causes', []):
            enriched = dict(cause)
            enriched['mode'] = mode
            top_causes.append(enriched)
    top_causes = sorted(top_causes, key=lambda item: item.get('score', 0), reverse=True)

    hypotheses: List[Hypothesis] = []
    for entry in VARIABLE_CATALOG:
        baseline_value = _get_by_path(baseline_config, entry['path'])
        source_cause = _find_source_cause(top_causes, entry.get('cause_ids', []))
        candidate_values = _candidate_values(entry, baseline_value)
        default_value = candidate_values[0] if candidate_values else baseline_value
        config_patch = _set_by_path(entry['path'], default_value)
        hypothesis_id = f"hyp-{slugify(entry['path'])}"
        hypotheses.append(
            Hypothesis(
                hypothesis_id=hypothesis_id,
                family=entry['family'],
                title=entry['title'],
                rationale=(
                    f"{source_cause.get('title', 'Recent diagnostics')} suggests this native PowerTrader variable is worth retesting. "
                    f"Baseline `{entry['path']}` = `{baseline_value}`."
                ),
                parameter_target={
                    'target_file': ensure_profile_patch_target(),
                    'config_path': entry['path'],
                    'baseline_value': baseline_value,
                    'proposed_value': default_value,
                    'candidate_values': candidate_values,
                    'config_patch': config_patch,
                },
                expected_effect=_expected_effect(entry['family']),
                experiment_plan={
                    'engine': entry['engine'],
                    'walk_forward_splits': 3,
                    'selection_metric': 'avg_oos_expectancy_delta',
                    'min_trade_count': 3,
                },
                guardrails=[
                    'Reject if out-of-sample expectancy does not improve.',
                    'Reject if sample size remains too small to be decision-useful.',
                    'Reject if drawdown or profit giveback worsens materially.',
                ],
                priority_score=source_cause.get('score', 0.0),
                source_cause=source_cause,
            )
        )
    return hypotheses
