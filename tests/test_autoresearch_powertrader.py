import json
from pathlib import Path

from autoresearch.diagnostics.service import diagnose_all
from autoresearch.experiments.service import run_experiments
from autoresearch.hypothesis.service import generate_hypotheses
from autoresearch.ingest.service import load_datasets
from autoresearch.pipeline import run_weekly


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == '.jsonl':
        with path.open('w', encoding='utf-8') as handle:
            for row in payload:
                handle.write(json.dumps(row) + '\n')
    else:
        path.write_text(json.dumps(payload, indent=2), encoding='utf-8')


def test_ingest_prefers_direct_trade_history(monkeypatch, tmp_path):
    root = tmp_path
    _write(
        root / 'gui_settings.json',
        {
            'trade_start_level': 3,
            'start_allocation_pct': 0.005,
            'dca_multiplier': 2.0,
            'dca_levels': [-2.5, -5.0, -10.0],
            'max_dca_buys_per_24h': 2,
            'pm_start_pct_no_dca': 5.0,
            'pm_start_pct_with_dca': 2.5,
            'trailing_gap_pct': 0.5,
        },
    )
    _write(root / 'autoresearch' / 'configs' / 'powertrader_research_profile.json', {'trade_start_level': 3})
    _write(
        root / 'hub_data' / 'trade_history.jsonl',
        [
            {'timestamp': '2026-03-20T00:00:00Z', 'symbol': 'BTC-USDT', 'side': 'buy', 'qty': 1.0, 'price': 100.0},
            {'timestamp': '2026-03-20T02:00:00Z', 'symbol': 'BTC-USDT', 'side': 'sell', 'qty': 1.0, 'price': 105.0, 'pnl_pct': 5.0, 'realized_profit_usd': 5.0},
        ],
    )
    _write(root / 'simulator_trades.json', {'closed_trades': []})

    import autoresearch.utils as utils
    import autoresearch.ingest.service as ingest_service

    monkeypatch.setattr(utils, 'REPO_ROOT', root)
    monkeypatch.setattr(ingest_service, 'repo_root', lambda: root)

    datasets = load_datasets('powertrader', 'all')
    trades = datasets['powertrader'].closed_trades
    assert len(trades) == 1
    assert trades[0].source == 'direct_trade_history'


def test_ingest_reconstructs_simulator_history(monkeypatch, tmp_path):
    root = tmp_path
    _write(root / 'gui_settings.json', {})
    _write(root / 'autoresearch' / 'configs' / 'powertrader_research_profile.json', {'trade_start_level': 3})
    _write(
        root / 'simulator_trades.json',
        {
            'closed_trades': [
                {
                    'symbol': 'BTC-USDT',
                    'qty': 0.5,
                    'entry_price': 100.0,
                    'exit_price': 103.0,
                    'pnl': 1.5,
                    'pnl_pct': 3.0,
                    'timestamp': '2026-03-20T02:00:00Z',
                }
            ]
        },
    )

    import autoresearch.utils as utils
    import autoresearch.ingest.service as ingest_service

    monkeypatch.setattr(utils, 'REPO_ROOT', root)
    monkeypatch.setattr(ingest_service, 'repo_root', lambda: root)

    datasets = load_datasets('powertrader', 'all')
    trades = datasets['powertrader'].closed_trades
    assert len(trades) == 1
    assert trades[0].source == 'reconstructed_simulator'


def test_hypotheses_are_powertrader_native():
    diagnostics = {
        'powertrader': {
            'top_causes': [{'cause_id': 'profit_giveback', 'title': 'Trailing exits giving back open gains', 'score': 2.0}],
        }
    }
    baseline = {
        'trade_start_level': 3,
        'start_allocation_pct': 0.005,
        'dca_multiplier': 2.0,
        'dca_levels': [-2.5, -5.0, -10.0],
        'max_dca_buys_per_24h': 2,
        'pm_start_pct_no_dca': 5.0,
        'pm_start_pct_with_dca': 2.5,
        'trailing_gap_pct': 0.5,
    }
    hypotheses = generate_hypotheses(diagnostics, baseline)
    paths = {item.parameter_target['config_path'] for item in hypotheses}
    assert 'trailing_gap_pct' in paths
    assert 'trade_start_level' in paths
    assert 'exit_rules.max_loss_exit_pct' not in paths
    assert 'entry_rules.hot_coin_15m_min_pct' not in paths


def test_replay_engine_selects_candidate_result():
    from autoresearch.models import ClosedTrade, Hypothesis, ResearchDataset

    dataset = ResearchDataset(
        mode='powertrader',
        days='all',
        generated_at='2026-03-26T00:00:00Z',
        closed_trades=[
            ClosedTrade('BTC', 'powertrader', 1, 2, 60, -4.0, -4.0, 100, 96, 'EXIT', 1, 'direct_trade_history', 1.0),
            ClosedTrade('ETH', 'powertrader', 3, 4, 60, 2.0, 2.0, 100, 102, 'EXIT', 0, 'direct_trade_history', 1.0),
            ClosedTrade('XRP', 'powertrader', 5, 6, 60, -3.0, -3.0, 100, 97, 'EXIT', 2, 'direct_trade_history', 1.0),
            ClosedTrade('BNB', 'powertrader', 7, 8, 60, 1.0, 1.0, 100, 101, 'EXIT', 0, 'direct_trade_history', 1.0),
        ],
    )
    hypothesis = Hypothesis(
        hypothesis_id='hyp-max-dca-buys-per-24h',
        family='dca_frequency',
        title='Adjust DCA rate limit',
        rationale='test',
        parameter_target={'config_path': 'max_dca_buys_per_24h', 'baseline_value': 2, 'proposed_value': 1, 'candidate_values': [1, 3]},
        expected_effect='test',
        experiment_plan={'engine': 'powertrader_replay', 'walk_forward_splits': 2},
        guardrails=[],
        priority_score=1.0,
        source_cause={},
    )
    results = run_experiments({'powertrader': dataset}, [hypothesis], Path('tmp_test_output'))
    assert results
    assert results[0].metrics['selected']['candidate'] in [1, 3]


def test_end_to_end_weekly_report(monkeypatch, tmp_path):
    root = tmp_path
    _write(
        root / 'gui_settings.json',
        {
            'trade_start_level': 3,
            'start_allocation_pct': 0.005,
            'dca_multiplier': 2.0,
            'dca_levels': [-2.5, -5.0, -10.0],
            'max_dca_buys_per_24h': 2,
            'pm_start_pct_no_dca': 5.0,
            'pm_start_pct_with_dca': 2.5,
            'trailing_gap_pct': 0.5,
        },
    )
    _write(
        root / 'autoresearch' / 'configs' / 'powertrader_research_profile.json',
        {
            'trade_start_level': 3,
            'start_allocation_pct': 0.005,
            'dca_multiplier': 2.0,
            'dca_levels': [-2.5, -5.0, -10.0],
            'max_dca_buys_per_24h': 2,
            'pm_start_pct_no_dca': 5.0,
            'pm_start_pct_with_dca': 2.5,
            'trailing_gap_pct': 0.5,
        },
    )
    _write(
        root / 'hub_data' / 'trade_history.jsonl',
        [
            {'timestamp': '2026-03-20T00:00:00Z', 'symbol': 'BTC-USDT', 'side': 'buy', 'qty': 1.0, 'price': 100.0},
            {'timestamp': '2026-03-20T02:00:00Z', 'symbol': 'BTC-USDT', 'side': 'sell', 'qty': 1.0, 'price': 105.0, 'pnl_pct': 5.0, 'realized_profit_usd': 5.0},
            {'timestamp': '2026-03-21T00:00:00Z', 'symbol': 'ETH-USDT', 'side': 'buy', 'qty': 1.0, 'price': 100.0},
            {'timestamp': '2026-03-21T02:00:00Z', 'symbol': 'ETH-USDT', 'side': 'sell', 'qty': 1.0, 'price': 97.0, 'pnl_pct': -3.0, 'realized_profit_usd': -3.0},
        ],
    )

    import autoresearch.utils as utils
    import autoresearch.ingest.service as ingest_service

    monkeypatch.setattr(utils, 'REPO_ROOT', root)
    monkeypatch.setattr(ingest_service, 'repo_root', lambda: root)

    output_dir = root / 'reports' / 'autoresearch' / 'weekly' / 'testrun'
    result = run_weekly('powertrader', 'all', output_dir)
    assert (output_dir / 'report.md').exists()
    assert result['scores']['ranked']
    report = (output_dir / 'report.md').read_text(encoding='utf-8')
    assert 'PowerTrader Autoresearch Report' in report
    assert 'Adjust trailing gap' in report or 'Adjust no-DCA profit trigger' in report
