"""Lightweight orchestrator to run the local autoresearch pipeline and optionally publish artifacts."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from pt_autoresearch_adapter import publish, run_pipeline


def run_cycle(mode: str = 'powertrader', days: str = 'all', baseline_config: Optional[str] = None, run_id: Optional[str] = None, publish_artifacts: bool = False) -> Dict[str, object]:
    res = run_pipeline(mode=mode, days=days, baseline_config=baseline_config, run_id=run_id)
    output_dir = Path(res.get('output_dir'))
    published: List[str] = []
    artifacts_dir = output_dir / 'artifacts'
    if publish_artifacts and artifacts_dir.exists():
        files = [str(path) for path in artifacts_dir.rglob('*') if path.is_file()]
        if files:
            published = publish(files)
    return {'pipeline': res, 'published': published}


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', default='powertrader')
    parser.add_argument('--days', default='all')
    parser.add_argument('--run-id', default=None)
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    out = run_cycle(mode=args.mode, days=args.days, run_id=args.run_id, publish_artifacts=args.publish)
    print(json.dumps({'status': 'ok', 'published': out['published'], 'output_dir': out['pipeline'].get('output_dir')}))
