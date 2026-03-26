# TraderJojo Autoresearch

This package adds a weekly, approval-only research loop for TraderJojo. It analyzes live and paper history, diagnoses weak performance regimes, generates specific hypotheses, runs controlled experiments, scores the results, and emits human-reviewable recommendations without changing the live bot automatically.

## Workflow

Run the full loop:

```bash
python -m autoresearch.weekly --mode both --days 90
```

Run stages individually:

```bash
python diagnose_performance.py --mode both --days 90
python generate_hypotheses.py --mode both --days 90
python run_experiments.py --mode both --days 90
python score_results.py --mode both --days 90
python risk_review.py --mode both --days 90
python produce_report.py --mode both --days 90
```

## Inputs

- `hub_data/trade_history.jsonl`
- `hub_data/paper/trade_history.jsonl`
- `hub_data/active_positions.json`
- `hub_data/paper/active_positions.json`
- `orchestrator_log.json`
- `data/trading.db`
- `gui_settings.json`

## Outputs

Weekly outputs are written to `reports/autoresearch/weekly/<run_id>/`:

- `diagnostics_live.json`
- `diagnostics_paper.json`
- `hypotheses.json`
- `experiments.json`
- `scores.json`
- `risk_review.json`
- `report.md`
- `config_patches/*.patch.json`
- `artifacts/<hypothesis_id>/...`

## Design Notes

- Diagnostics pair historical entry and exit rows into closed trades and join nearby gate and decision context from the SQLite dashboard tables.
- Hypothesis generation always emits five single-parameter hypotheses for the MVP.
- Experiments wrap `pt_whatif.py`, `pt_whatif_advanced.py`, and `red_blue_simulator.py` for artifact capture and benchmark context, then normalize scoring with fee and slippage overlays.
- Recommendations are emitted as config patch suggestions only. Nothing is activated automatically.
