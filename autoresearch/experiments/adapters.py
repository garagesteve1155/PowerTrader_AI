from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Tuple

from autoresearch.utils import REPO_ROOT, dt_to_iso, safe_float, write_json


def _run_command(command: List[str], cwd: Path) -> Tuple[int, str, str]:
    completed = subprocess.run(
        command,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    return completed.returncode, completed.stdout, completed.stderr


def run_pt_whatif(symbol: str, start_date: str, end_date: str, capital: float, artifact_dir: Path) -> Dict[str, Any]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    command = ["python", str(REPO_ROOT / "pt_whatif.py"), symbol, start_date, end_date, str(capital)]
    code, stdout, stderr = _run_command(command, artifact_dir)
    output_file = artifact_dir / f"whatif_{symbol}_{start_date}_{end_date}.json"
    result: Dict[str, Any] = {
        "engine": "pt_whatif",
        "symbol": symbol,
        "command": command,
        "return_code": code,
        "stdout": stdout[-4000:],
        "stderr": stderr[-4000:],
        "metrics": {},
        "warnings": [],
        "artifacts": [],
    }
    if output_file.exists():
        copied = artifact_dir / output_file.name
        if output_file != copied:
            shutil.copyfile(output_file, copied)
        payload = json.loads(copied.read_text(encoding="utf-8"))
        result["metrics"] = {
            "return_pct": safe_float(payload.get("total_return_pct") or payload.get("roi_pct")),
            "hold_return_pct": safe_float(payload.get("hold_return_pct")),
            "trade_count": safe_float(payload.get("total_trades")),
            "win_rate": safe_float(payload.get("win_rate")),
            "max_drawdown_pct": safe_float(payload.get("max_drawdown_pct")),
        }
        result["artifacts"].append(str(copied))
    else:
        result["warnings"].append("pt_whatif did not produce the expected JSON artifact.")
    write_json(artifact_dir / "adapter_result_pt_whatif.json", result)
    return result


def run_pt_whatif_advanced(symbol: str, start_date: str, end_date: str, capital: float, artifact_dir: Path) -> Dict[str, Any]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    command = [
        "python",
        str(REPO_ROOT / "pt_whatif_advanced.py"),
        symbol,
        "--start",
        start_date,
        "--end",
        end_date,
        "--capital",
        str(capital),
    ]
    code, stdout, stderr = _run_command(command, artifact_dir)
    output_file = artifact_dir / f"whatif_advanced_{symbol}.json"
    result: Dict[str, Any] = {
        "engine": "pt_whatif_advanced",
        "symbol": symbol,
        "command": command,
        "return_code": code,
        "stdout": stdout[-4000:],
        "stderr": stderr[-4000:],
        "metrics": {},
        "warnings": [],
        "artifacts": [],
    }
    if output_file.exists():
        copied = artifact_dir / output_file.name
        if output_file != copied:
            shutil.copyfile(output_file, copied)
        payload = json.loads(copied.read_text(encoding="utf-8"))
        result["metrics"] = {
            "return_pct": safe_float(payload.get("total_return_pct")),
            "hold_return_pct": safe_float(payload.get("hold_return_pct")),
            "trade_count": safe_float(payload.get("trades_count")),
            "win_rate": safe_float(payload.get("win_rate")),
            "max_drawdown_pct": safe_float(payload.get("max_drawdown_pct")),
        }
        result["artifacts"].append(str(copied))
    else:
        result["warnings"].append("pt_whatif_advanced did not produce the expected JSON artifact.")
    write_json(artifact_dir / "adapter_result_pt_whatif_advanced.json", result)
    return result


def run_red_blue(scan_log_path: Path, artifact_dir: Path) -> Dict[str, Any]:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    absolute_scan_log = scan_log_path if scan_log_path.is_absolute() else REPO_ROOT / scan_log_path
    command = [
        "python",
        str(REPO_ROOT / "red_blue_simulator.py"),
        "--scan-log",
        str(absolute_scan_log),
        "--max-scans",
        "10",
    ]
    code, stdout, stderr = _run_command(command, artifact_dir)
    result: Dict[str, Any] = {
        "engine": "red_blue_simulator",
        "command": command,
        "return_code": code,
        "stdout": stdout[-4000:],
        "stderr": stderr[-4000:],
        "metrics": {},
        "warnings": [],
        "artifacts": [],
    }
    traders_dir = artifact_dir / "traders"
    red_trades = traders_dir / "red" / "trades.json"
    blue_trades = traders_dir / "blue" / "trades.json"
    copied_paths: List[str] = []
    for path in [red_trades, blue_trades]:
        if path.exists():
            artifact_dir.mkdir(parents=True, exist_ok=True)
            copied = artifact_dir / path.name.replace(".json", f"_{path.parent.name}.json")
            shutil.copyfile(path, copied)
            copied_paths.append(str(copied))
    result["artifacts"] = copied_paths
    if not copied_paths:
        result["warnings"].append("red_blue_simulator did not produce trader artifacts.")
    write_json(artifact_dir / "adapter_result_red_blue_simulator.json", result)
    return result


def normalize_tool_context(adapter_payload: Dict[str, Any], train_start: Any, test_end: Any) -> Dict[str, Any]:
    metrics = dict(adapter_payload.get("metrics", {}))
    metrics.setdefault("adapter_train_start", dt_to_iso(train_start if isinstance(train_start, float) else None))
    metrics.setdefault("adapter_test_end", dt_to_iso(test_end if isinstance(test_end, float) else None))
    return metrics
