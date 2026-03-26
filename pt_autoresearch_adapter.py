"""Adapter to invoke the local PowerTrader autoresearch pipeline.

Provides simple callables: `run_pipeline`, `train`, `evaluate`, and `publish`.
Outputs are written to `reports/autoresearch/weekly/<run_id>/` by default.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent
RUNS_DIR = ROOT / 'reports' / 'autoresearch' / 'weekly'


def _ensure_runs_dir() -> None:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)


def _default_run_id() -> str:
    return time.strftime('%Y%m%d-%H%M%S')


def run_pipeline(mode: str = 'powertrader', days: Any = 'all', baseline_config: Optional[str] = None, run_id: Optional[str] = None) -> Dict[str, Any]:
    _ensure_runs_dir()
    run_id = run_id or _default_run_id()
    output_dir = RUNS_DIR / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        os.environ.get('PYTHON_EXE', sys.executable),
        '-m',
        'autoresearch.weekly',
        '--mode',
        str(mode),
        '--days',
        str(days),
        '--output-dir',
        str(output_dir),
    ]
    if baseline_config:
        cmd.extend(['--baseline-config', baseline_config])

    proc = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f'autoresearch pipeline failed: {proc.returncode}\nstdout:{proc.stdout}\nstderr:{proc.stderr}')

    result: Dict[str, Any] = {'output_dir': str(output_dir), 'stdout': proc.stdout.strip()}
    for name in ('hypotheses', 'experiments', 'scores', 'risk_review', 'metadata'):
        path = output_dir / f'{name}.json'
        if path.exists():
            result[name] = json.loads(path.read_text(encoding='utf-8'))
    for name in ('diagnostics_powertrader', 'diagnostics_paper', 'diagnostics_live'):
        path = output_dir / f'{name}.json'
        if path.exists():
            result[name] = json.loads(path.read_text(encoding='utf-8'))
    return result


def train(*args, **kwargs) -> Dict[str, Any]:
    return run_pipeline(*args, **kwargs)


def evaluate(output_dir: str) -> Dict[str, Any]:
    path = Path(output_dir)
    if not path.exists():
        raise FileNotFoundError(output_dir)
    out: Dict[str, Any] = {}
    for fname in ('experiments.json', 'scores.json', 'risk_review.json', 'metadata.json'):
        file_path = path / fname
        if file_path.exists():
            out[fname] = json.loads(file_path.read_text(encoding='utf-8'))
    return out


def publish(artifact_paths: List[str], dest_dir: Optional[str] = None) -> List[str]:
    dest_dir = dest_dir or str(ROOT / 'memories_published')
    dpath = Path(dest_dir)
    dpath.mkdir(parents=True, exist_ok=True)
    published = []
    for src in artifact_paths:
        srcp = Path(src)
        if not srcp.exists():
            raise FileNotFoundError(src)
        dest = dpath / srcp.name
        tmp = dpath / (srcp.name + '.next')
        shutil.copy2(srcp, tmp)
        os.replace(tmp, dest)
        published.append(str(dest))
    return published


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', default='powertrader')
    parser.add_argument('--days', default='all')
    parser.add_argument('--run-id', default=None)
    args = parser.parse_args()
    res = run_pipeline(mode=args.mode, days=args.days, run_id=args.run_id)
    print(json.dumps({'status': 'ok', 'output_dir': res.get('output_dir')}))
