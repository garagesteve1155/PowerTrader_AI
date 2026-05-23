"""Business logic for PowerTrader_AI: subprocess management, training, settings.

Designed to be shared by pt_hub.py (tkinter) and pt_web.py (web app).
"""

import glob
import json
import os
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from pt_env import PTEnv, utcnow
from pt_models import CoinModel, SystemModel
import pt_errors


MAX_LOG_BYTES = 2 * 1024 * 1024  # 2 MB per log file


@dataclass
class ProcHandle:
    name: str
    proc: subprocess.Popen | None = None
    log_q: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=4000))
    log_file: Path | None = field(default=None, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None


def _reader_thread(proc: subprocess.Popen, q: queue.Queue, prefix: str,
                    on_exit: callable = None, log_path: Path | None = None):
    log_fh = None
    try:
        if log_path:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_fh = open(log_path, "a", encoding="utf-8", buffering=1)
    except Exception as e:
        pt_errors.emit(
            "controller", level="warning",
            message=f"Could not open log file {log_path}: {e}",
            detail=(
                "Process output will not be written to disk for this session. "
                "Live logs in the UI still work but will be lost on restart."
            ),
        )
        log_fh = None

    try:
        while True:
            line = proc.stdout.readline() if proc.stdout else ""
            if not line:
                if proc.poll() is not None:
                    break
                time.sleep(0.05)
                continue
            msg = line.rstrip()
            if q.full():
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
            try:
                q.put_nowait(msg)
            except queue.Full:
                pass
            if log_fh:
                try:
                    log_fh.write(msg + "\n")
                    if log_fh.tell() > MAX_LOG_BYTES:
                        log_fh.close()
                        _rotate_log(log_path)
                        log_fh = open(log_path, "a", encoding="utf-8", buffering=1)
                except Exception:
                    pass
    except Exception as e:
        pt_errors.emit(
            "controller", level="error",
            message=f"Log reader thread for {prefix.strip()} crashed: {e}",
            detail=(
                "The subprocess is still running but its output is no longer being captured. "
                "Live logs in the UI will be empty or stale. Restart the process to restore logging."
            ),
        )
    if log_fh:
        try:
            log_fh.close()
        except Exception:
            pass
    if on_exit:
        try:
            on_exit(proc.returncode)
        except Exception:
            pass


def _rotate_log(path: Path):
    prev = path.with_suffix(".prev.log")
    try:
        if prev.exists():
            prev.unlink()
        path.rename(prev)
    except Exception:
        pass


class ProcessController:
    """Manages thinker, trader, and trainer subprocesses."""

    def __init__(self, env: PTEnv):
        self.env = env
        self._thinker = ProcHandle(name="thinker")
        self._data_manager = ProcHandle(name="data-manager")
        self._traders: dict[str, ProcHandle] = {}
        self._trainers: dict[str, ProcHandle] = {}
        self._lock = threading.Lock()

    def _make_env(self) -> dict:
        env = os.environ.copy()
        env["POWERTRADER_CONFIG"] = str(self.env.config_path)
        return env

    def _log_dir(self) -> Path:
        return self.env.logs_dir()

    def _launch(self, handle: ProcHandle, script_path: str, args: list[str] | None = None,
                cwd: str | None = None, prefix: str = "",
                on_exit: callable = None, log_name: str | None = None) -> bool:
        if handle.alive:
            return True
        if not os.path.isfile(script_path):
            return False
        log_path = self._log_dir() / f"{log_name}.log" if log_name else None
        handle.log_file = log_path
        cmd = [sys.executable, "-u", script_path] + (args or [])
        try:
            handle.proc = subprocess.Popen(
                cmd,
                cwd=cwd or str(self.env.project_dir),
                env=self._make_env(),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            handle._thread = threading.Thread(
                target=_reader_thread,
                args=(handle.proc, handle.log_q, prefix, on_exit, log_path),
                daemon=True,
            )
            handle._thread.start()
            return True
        except Exception as e:
            pt_errors.emit(
                "controller", level="error",
                message=f"Failed to launch subprocess {handle.name}: {e}",
                detail=(
                    f"The {handle.name} process could not be started. "
                    "Check that the script path exists and Python has permission to execute it. "
                    "Trading and signal generation will not run until this is resolved."
                ),
            )
            return False

    def _stop(self, handle: ProcHandle):
        if handle.alive:
            try:
                handle.proc.terminate()
            except Exception:
                pass

    # -- Data Manager --

    def start_data_manager(self) -> bool:
        script = self.env.project_dir / "pt_data_manager.py"
        if not script.is_file():
            return False
        return self._launch(
            self._data_manager,
            str(script),
            prefix="[DATA] ",
            log_name="data-manager",
        )

    def stop_data_manager(self):
        self._stop(self._data_manager)

    def backfill_coin(self, coin: str) -> bool:
        script = self.env.project_dir / "pt_data_manager.py"
        if not script.is_file():
            return False
        h = ProcHandle(name=f"backfill-{coin.lower()}")
        return self._launch(h, str(script), args=["--coin", coin.upper()],
                            prefix=f"[BACKFILL:{coin}] ",
                            log_name=f"backfill-{coin.lower()}")

    @property
    def data_manager_running(self) -> bool:
        return self._data_manager.alive

    # -- Neural (thinker) --

    def start_thinker(self) -> bool:
        rr_path = self.env.thinker_ready_path()
        try:
            rr_path.parent.mkdir(parents=True, exist_ok=True)
            with open(rr_path, "w") as f:
                json.dump({"timestamp": utcnow(), "ready": False, "stage": "starting"}, f)
        except Exception:
            pass

        ar_path = self.env.thinker_autorestart_path()
        try:
            with open(ar_path, "w") as f:
                json.dump({
                    "timestamp": utcnow(),
                    "should_be_running": True,
                    "user_stopped_from_hub": False,
                    "last_auto_restart_ts": 0.0,
                }, f)
        except Exception:
            pass

        return self._launch(
            self._thinker,
            str(self.env.script_path("thinker")),
            prefix="[THINKER] ",
            log_name="thinker",
        )

    def stop_thinker(self):
        self._stop(self._thinker)
        ar_path = self.env.thinker_autorestart_path()
        try:
            with open(ar_path, "w") as f:
                json.dump({
                    "timestamp": utcnow(),
                    "should_be_running": False,
                    "user_stopped_from_hub": True,
                    "last_auto_restart_ts": 0.0,
                }, f)
        except Exception:
            pass

    @property
    def thinker_running(self) -> bool:
        return self._thinker.alive

    # -- Traders (one per exchange) --

    def _get_trader(self, exchange: str) -> ProcHandle:
        if exchange not in self._traders:
            self._traders[exchange] = ProcHandle(name=f"trader-{exchange}")
        return self._traders[exchange]

    def _trader_exchanges(self, exchange: str | None = None) -> list[str]:
        """Exchanges to run traders for, respecting trading mode."""
        if exchange:
            return [exchange]
        if self.env.trading_mode == "demo":
            return ["demo"]
        xk = self.env.exchange
        return [xk] if xk else []

    def start_trader(self, exchange: str | None = None) -> bool:
        ok = True
        for xk in self._trader_exchanges(exchange):
            h = self._get_trader(xk)
            result = self._launch(
                h,
                str(self.env.script_path("trader")),
                args=["--exchange", xk],
                prefix=f"[TRADER:{xk.upper()}] ",
                log_name=f"trader-{xk}",
            )
            ok = ok and result
        return ok

    def stop_trader(self, exchange: str | None = None):
        if exchange:
            h = self._traders.get(exchange)
            if h:
                self._stop(h)
        else:
            for h in self._traders.values():
                self._stop(h)

    def trader_running_for(self, exchange: str) -> bool:
        h = self._traders.get(exchange)
        return h.alive if h else False

    @property
    def trader_running(self) -> bool:
        return any(h.alive for h in self._traders.values())

    # -- Start/stop all --

    def start_all(self) -> dict:
        """Start data manager, thinker, then traders when ready."""
        self.start_data_manager()
        ok = self.start_thinker()
        if not ok:
            return {"ok": False, "error": "Failed to start thinker"}
        return {"ok": True, "message": "Thinker started, traders will start when ready"}

    def poll_ready_and_start_trader(self) -> bool:
        """Check thinker_ready and start all traders if ready."""
        sm = SystemModel(self.env)
        rr = sm.thinker_ready()
        if rr.get("ready"):
            started = False
            for xk in self._trader_exchanges():
                if not self.trader_running_for(xk):
                    self.start_trader(xk)
                    started = True
            return started or self.trader_running
        return False

    def stop_all(self):
        self.stop_trader()
        self.stop_thinker()
        self.stop_data_manager()

    # -- Training --

    def start_training(self, coin: str) -> bool:
        with self._lock:
            if coin in self._trainers and self._trainers[coin].alive:
                return True

            coin_dir = self.env.coin_dir(coin)
            coin_dir.mkdir(parents=True, exist_ok=True)

            trainer_name = os.path.basename(
                self.env.get_config()["script_neural_trainer"]
            )
            trainer_path = self.env.project_dir / trainer_name
            if not trainer_path.is_file():
                return False

            patterns = [
                "trainer_last_training_time.txt", "trainer_status.json",
                "trainer_failure_info.json", "trainer_last_start_time.txt",
                "killer.txt", "memories_*.txt", "memory_weights_*.txt",
                "neural_perfect_threshold_*.txt",
            ]
            for pat in patterns:
                for fp in glob.glob(str(coin_dir / pat)):
                    try:
                        os.remove(fp)
                    except Exception:
                        pass

            handle = ProcHandle(name=f"Trainer-{coin}")

            def _on_trainer_exit(returncode, _coin=coin, _dir=coin_dir):
                status_path = _dir / "trainer_status.json"
                failure_path = _dir / "trainer_failure_info.json"
                try:
                    status = json.loads(status_path.read_text()) if status_path.exists() else {}
                except Exception:
                    status = {}
                if status.get("state") == "TRAINING":
                    has_failure = False
                    try:
                        fi = json.loads(failure_path.read_text()) if failure_path.exists() else {}
                        has_failure = bool(fi.get("exception_type"))
                    except Exception:
                        pass
                    if not has_failure:
                        failure = {
                            "coin": _coin,
                            "state": "FAILED",
                            "exception_type": "ProcessDied",
                            "exception_message": f"Trainer exited with code {returncode} without reporting results",
                            "traceback": "",
                            "trainer_state": {},
                            "timestamp": int(time.time()),
                            "started_at": status.get("started_at", 0),
                        }
                        failure_path.write_text(json.dumps(failure, indent=2))
                    status["state"] = "FAILED"
                    status["timestamp"] = int(time.time())
                    status_path.write_text(json.dumps(status))

            ok = self._launch(
                handle,
                str(trainer_path),
                args=[coin],
                cwd=str(coin_dir),
                prefix=f"[{coin}] ",
                on_exit=_on_trainer_exit,
                log_name=f"trainer-{coin.lower()}",
            )
            if ok:
                self._trainers[coin] = handle
            return ok

    def train_all(self) -> dict[str, bool]:
        self.stop_thinker()
        results = {}
        for coin in self.env.coins:
            results[coin] = self.start_training(coin)
        return results

    def stop_training(self, coin: str):
        with self._lock:
            handle = self._trainers.get(coin)
            if handle:
                self._stop(handle)
            killer = self.env.killer_path(coin)
            try:
                killer.write_text("1")
            except Exception:
                pass

    def stop_all_training(self):
        for coin in list(self._trainers.keys()):
            self.stop_training(coin)

    def training_running(self, coin: str) -> bool:
        h = self._trainers.get(coin)
        return h.alive if h else False

    def any_training_running(self) -> bool:
        return any(h.alive for h in self._trainers.values())

    # -- Logs --

    def _resolve_handle(self, script: str) -> ProcHandle | None:
        if script == "thinker":
            return self._thinker
        if script == "data-manager":
            return self._data_manager
        if script.startswith("trader-"):
            xk = script.split("-", 1)[1]
            return self._traders.get(xk)
        if script.startswith("trainer-"):
            coin = script.split("-", 1)[1].upper()
            return self._trainers.get(coin)
        return None

    def _resolve_log_file(self, script: str) -> Path | None:
        h = self._resolve_handle(script)
        if h and h.log_file:
            return h.log_file
        name = script.replace("trainer-", "trainer-").lower()
        path = self._log_dir() / f"{name}.log"
        return path if path.exists() else None

    def get_logs(self, script: str, limit: int = 200) -> list[str]:
        h = self._resolve_handle(script)
        if not h:
            return []
        lines = []
        while not h.log_q.empty() and len(lines) < limit:
            try:
                lines.append(h.log_q.get_nowait())
            except queue.Empty:
                break
        return lines

    def peek_logs(self, script: str, limit: int = 200) -> list[str]:
        """Read logs from queue (live) or log file (persisted)."""
        h = self._resolve_handle(script)
        if h:
            with h.log_q.mutex:
                items = list(h.log_q.queue)
            if items:
                return items[-limit:]
        log_file = self._resolve_log_file(script)
        if log_file and log_file.exists():
            try:
                with open(log_file, "r", encoding="utf-8") as f:
                    all_lines = f.readlines()
                return [x.rstrip() for x in all_lines[-limit:]]
            except Exception:
                pass
        return []

    # -- Status summary --

    def status_summary(self) -> dict:
        training = {}
        for coin in self.env.coins:
            cm = CoinModel(self.env, coin)
            ts = cm.training_status()
            training[coin] = {
                "state": ts.get("state", "UNKNOWN"),
                "is_trained": cm.is_trained(),
                "running": self.training_running(coin),
            }

        traders = {}
        xk = self.env.exchange
        if xk:
            traders[xk] = {"running": self.trader_running_for(xk)}

        return {
            "thinker_running": self.thinker_running,
            "trader_running": self.trader_running,
            "data_manager_running": self.data_manager_running,
            "traders": traders,
            "any_training_running": self.any_training_running(),
            "training": training,
        }
