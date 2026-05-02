from __future__ import annotations
import os
import sys
import json
import time
import math
import queue
import threading
import subprocess
import shutil
import glob
import bisect
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk, filedialog, messagebox
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter
from matplotlib.transforms import blended_transform_factory

DARK_BG = "#070B10"
DARK_BG2 = "#0B1220"
DARK_PANEL = "#0E1626"
DARK_PANEL2 = "#121C2F"
DARK_BORDER = "#243044"
DARK_FG = "#C7D1DB"
DARK_MUTED = "#8B949E"
DARK_ACCENT = "#00FF66"   
DARK_ACCENT2 = "#00E5FF"   
DARK_SELECT_BG = "#17324A"
DARK_SELECT_FG = "#00FF66"


@dataclass
class _WrapItem:
    w: tk.Widget
    padx: Tuple[int, int] = (0, 0)
    pady: Tuple[int, int] = (0, 0)


class WrapFrame(ttk.Frame):

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self._items: List[_WrapItem] = []
        self._reflow_pending = False
        self._in_reflow = False
        self.bind("<Configure>", self._schedule_reflow)

    def add(self, widget: tk.Widget, padx=(0, 0), pady=(0, 0)) -> None:
        self._items.append(_WrapItem(widget, padx=padx, pady=pady))
        self._schedule_reflow()

    def clear(self, destroy_widgets: bool = True) -> None:

        for it in list(self._items):
            try:
                it.w.grid_forget()
            except Exception:
                pass
            if destroy_widgets:
                try:
                    it.w.destroy()
                except Exception:
                    pass
        self._items = []
        self._schedule_reflow()

    def _schedule_reflow(self, event=None) -> None:
        if self._reflow_pending:
            return
        self._reflow_pending = True
        self.after_idle(self._reflow)

    def _reflow(self) -> None:
        if self._in_reflow:
            self._reflow_pending = False
            return

        self._reflow_pending = False
        self._in_reflow = True
        try:
            width = self.winfo_width()
            if width <= 1:
                return
            usable_width = max(1, width - 6)

            for it in self._items:
                it.w.grid_forget()

            row = 0
            col = 0
            x = 0

            for it in self._items:
                reqw = max(it.w.winfo_reqwidth(), it.w.winfo_width())

                needed = 10 + reqw + it.padx[0] + it.padx[1]

                if col > 0 and (x + needed) > usable_width:
                    row += 1
                    col = 0
                    x = 0

                it.w.grid(row=row, column=col, sticky="w", padx=it.padx, pady=it.pady)
                x += needed
                col += 1
        finally:
            self._in_reflow = False


class NeuralSignalTile(ttk.Frame):

    def __init__(self, parent: tk.Widget, coin: str, bar_height: int = 52, levels: int = 8, trade_start_level: int = 3):
        super().__init__(parent)
        self.coin = coin

        self._hover_on = False
        self._normal_canvas_bg = DARK_PANEL2
        self._hover_canvas_bg = DARK_PANEL
        self._normal_border = DARK_BORDER
        self._hover_border = DARK_ACCENT2
        self._normal_fg = DARK_FG
        self._hover_fg = DARK_ACCENT2

        self._levels = max(2, int(levels))             
        self._display_levels = self._levels - 1        

        self._bar_h = int(bar_height)
        self._bar_w = 12
        self._gap = 16
        self._pad = 6

        self._base_fill = DARK_PANEL
        self._long_fill = "blue"
        self._short_fill = "orange"

        self.title_lbl = ttk.Label(self, text=coin)
        self.title_lbl.pack(anchor="center")

        w = (self._pad * 2) + (self._bar_w * 2) + self._gap
        h = (self._pad * 2) + self._bar_h

        self.canvas = tk.Canvas(
            self,
            width=w,
            height=h,
            bg=self._normal_canvas_bg,
            highlightthickness=1,
            highlightbackground=self._normal_border,
        )
        self.canvas.pack(padx=2, pady=(2, 0))

        x0 = self._pad
        x1 = x0 + self._bar_w
        x2 = x1 + self._gap
        x3 = x2 + self._bar_w
        yb = self._pad + self._bar_h

        # Build segmented bars: 7 segments for levels 1..7 (level 0 is "no highlight")
        self._long_segs: List[int] = []
        self._short_segs: List[int] = []

        for seg in range(self._display_levels):
            # seg=0 is bottom segment (level 1), seg=display_levels-1 is top segment (level 7)
            y_top = int(round(yb - ((seg + 1) * self._bar_h / self._display_levels)))
            y_bot = int(round(yb - (seg * self._bar_h / self._display_levels)))

            self._long_segs.append(
                self.canvas.create_rectangle(
                    x0, y_top, x1, y_bot,
                    fill=self._base_fill,
                    outline=DARK_BORDER,
                    width=1,
                )
            )
            self._short_segs.append(
                self.canvas.create_rectangle(
                    x2, y_top, x3, y_bot,
                    fill=self._base_fill,
                    outline=DARK_BORDER,
                    width=1,
                )
            )

        # Trade-start marker line (boundary before the trade-start level).
        # Example: trade_start_level=3 => line after 2nd block (between 2 and 3).
        self._trade_line_geom = (x0, x1, x2, x3, yb)
        self._trade_line_long = self.canvas.create_line(x0, yb, x1, yb, fill=DARK_FG, width=2)
        self._trade_line_short = self.canvas.create_line(x2, yb, x3, yb, fill=DARK_FG, width=2)
        self._trade_start_level = 3
        self.set_trade_start_level(trade_start_level)


        self.value_lbl = ttk.Label(self, text="L:0 S:0")
        self.value_lbl.pack(anchor="center", pady=(1, 0))

        self.set_values(0, 0)

    def set_hover(self, on: bool) -> None:
        """Visually highlight the tile on hover (like a button hover state)."""
        if bool(on) == bool(self._hover_on):
            return
        self._hover_on = bool(on)

        try:
            if self._hover_on:
                self.canvas.configure(
                    bg=self._hover_canvas_bg,
                    highlightbackground=self._hover_border,
                    highlightthickness=2,
                )
                self.title_lbl.configure(foreground=self._hover_fg)
                self.value_lbl.configure(foreground=self._hover_fg)
            else:
                self.canvas.configure(
                    bg=self._normal_canvas_bg,
                    highlightbackground=self._normal_border,
                    highlightthickness=1,
                )
                self.title_lbl.configure(foreground=self._normal_fg)
                self.value_lbl.configure(foreground=self._normal_fg)
        except Exception:
            pass

    def set_trade_start_level(self, level: Any) -> None:
        """Move the marker line to the boundary before the chosen start level."""
        self._trade_start_level = self._clamp_trade_start_level(level)
        self._update_trade_lines()

    def _clamp_trade_start_level(self, value: Any) -> int:
        try:
            v = int(float(value))
        except Exception:
            v = 3
        # Trade starts at levels 1..display_levels (usually 1..7)
        return max(1, min(v, self._display_levels))

    def _update_trade_lines(self) -> None:
        try:
            x0, x1, x2, x3, yb = self._trade_line_geom
        except Exception:
            return

        k = max(0, min(int(self._trade_start_level) - 1, self._display_levels))
        y = int(round(yb - (k * self._bar_h / self._display_levels)))

        try:
            self.canvas.coords(self._trade_line_long, x0, y, x1, y)
            self.canvas.coords(self._trade_line_short, x2, y, x3, y)
        except Exception:
            pass



    def _clamp_level(self, value: Any) -> int:
        try:
            v = int(float(value))
        except Exception:
            v = 0
        return max(0, min(v, self._levels - 1))  # logical clamp: 0..7

    def _set_level(self, seg_ids: List[int], level: int, active_fill: str) -> None:
        # Reset all segments to base
        for rid in seg_ids:
            self.canvas.itemconfigure(rid, fill=self._base_fill)

        # Level 0 -> show nothing (no highlight)
        if level <= 0:
            return

        # Level 1..7 -> fill from bottom up through the current level
        idx = level - 1  # level 1 maps to seg index 0
        if idx < 0:
            return
        if idx >= len(seg_ids):
            idx = len(seg_ids) - 1

        for i in range(idx + 1):
            self.canvas.itemconfigure(seg_ids[i], fill=active_fill)


    def set_values(self, long_sig: Any, short_sig: Any) -> None:
        ls = self._clamp_level(long_sig)
        ss = self._clamp_level(short_sig)

        self.value_lbl.config(text=f"L:{ls} S:{ss}")
        self._set_level(self._long_segs, ls, self._long_fill)
        self._set_level(self._short_segs, ss, self._short_fill)









# -----------------------------
# Settings / Paths
# -----------------------------

DEFAULT_SETTINGS = {
    "main_neural_dir": "",
    "coins": ['BTC', 'ETH', 'BNB', 'PAXG', 'SOL', 'XRP', 'DOGE'],

    # Long-term holdings symbols (optional): used ONLY for UI grouping.
    # IMPORTANT: No amounts are stored anymore. The bot will auto-ignore any extra
    # holdings beyond its tracked "bot-owned" position qty.
    # Format: ["BTC", "ETH"]
    "long_term_holdings": ['BTC', 'ETH', 'BNB', 'PAXG', 'SOL', 'XRP', 'DOGE'],

    # % of realized trade profits to automatically grow long-term holdings.
    # When the selected % of profits accumulates to $0.50+, the trader will buy
    # the long-term coin that is furthest below its daily 200 EMA.
    "lth_profit_alloc_pct": 50.0,

    "trade_start_level": 4,
    "start_allocation_pct": 0.5,

    "dca_multiplier": 2.0,
    "dca_levels": [-5.0, -10.0, -20.0, -30.0, -40.0, -50.0, -50.0],
    "max_dca_buys_per_24h": 1,

    # --- Trailing PM settings (editable; hot-reload friendly) ---
    "pm_start_pct_no_dca": 3.0,
    "pm_start_pct_with_dca": 3.0,
    "trailing_gap_pct": 0.1,

    "hub_data_dir": "",

    "script_neural_runner2": "pt_thinker.py",
    "script_neural_trainer": "pt_trainer.py",
    "script_trader": "pt_trader.py",


    # Chart timeframe options (must exist for brand-new installs)
    "timeframes": ["1min", "5min", "15min", "30min", "1hour", "2hour", "4hour", "8hour", "12hour", "1day", "1week"],
    "default_timeframe": "1hour",


    "ui_refresh_seconds": 1.0,
    "chart_refresh_seconds": 4.0,
    "candles_limit": 250,

    "auto_start_scripts": False,
}















SETTINGS_FILE = "gui_settings.json"


def _safe_read_json(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _safe_write_json(path: str, data: dict) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


_TRADE_HISTORY_CACHE: Dict[str, dict] = {}
_DCA_24H_CACHE: Dict[str, tuple] = {}


def _trade_history_file_sig(path: str) -> Optional[Tuple[int, int]]:
    try:
        st = os.stat(path)
        mtime_ns = int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000)))
        return (mtime_ns, int(st.st_size))
    except Exception:
        return None


def _read_trade_history_jsonl(path: str, tail: Optional[int] = None) -> List[dict]:
    """
    Reads hub_data/trade_history.jsonl written by pt_trader.py.
    Returns a list of dicts (only buy/sell rows).

    Uses a file-signature cache so repeated GUI refreshes do NOT keep reparsing
    the entire file on the Tk main thread when the file has not changed.
    """
    sig = _trade_history_file_sig(path)
    if sig is None:
        return []

    cached = _TRADE_HISTORY_CACHE.get(path)
    if cached and cached.get("sig") == sig:
        rows = cached.get("rows", []) or []
        return list(rows[-tail:]) if tail else list(rows)

    out: List[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    obj = json.loads(ln)
                    side = str(obj.get("side", "")).lower().strip()
                    if side not in ("buy", "sell"):
                        continue
                    out.append(obj)
                except Exception:
                    continue
    except Exception:
        out = []

    _TRADE_HISTORY_CACHE[path] = {
        "sig": sig,
        "rows": out,
    }

    return list(out[-tail:]) if tail else list(out)


def _compute_dca_24h_by_coin(path: str, now_ts: Optional[float] = None) -> Dict[str, int]:
    """
    Cached helper for the Current Trades table.
    Recomputes at most once per 5-second bucket unless trade_history.jsonl changed.
    """
    if not path:
        return {}

    sig = _trade_history_file_sig(path)
    if sig is None:
        return {}

    now_ts = float(now_ts if now_ts is not None else time.time())
    now_bucket = int(now_ts // 5)

    cached = _DCA_24H_CACHE.get(path)
    if cached and cached[0] == sig and cached[1] == now_bucket:
        return dict(cached[2])

    trades = _read_trade_history_jsonl(path)
    out: Dict[str, int] = {}

    try:
        window_floor = now_ts - (24 * 3600)

        last_sell_ts: Dict[str, float] = {}
        for tr in trades:
            sym = str(tr.get("symbol", "")).upper().strip()
            base = sym.split("-")[0].strip() if sym else ""
            if not base:
                continue

            side = str(tr.get("side", "")).lower().strip()
            if side != "sell":
                continue

            try:
                tsf = float(tr.get("ts", 0))
            except Exception:
                continue

            prev = float(last_sell_ts.get(base, 0.0))
            if tsf > prev:
                last_sell_ts[base] = tsf

        for tr in trades:
            sym = str(tr.get("symbol", "")).upper().strip()
            base = sym.split("-")[0].strip() if sym else ""
            if not base:
                continue

            side = str(tr.get("side", "")).lower().strip()
            if side != "buy":
                continue

            tag = str(tr.get("tag") or "").upper().strip()
            if tag != "DCA":
                continue

            try:
                tsf = float(tr.get("ts", 0))
            except Exception:
                continue

            start_ts = max(window_floor, float(last_sell_ts.get(base, 0.0)))
            if tsf >= start_ts:
                out[base] = int(out.get(base, 0)) + 1
    except Exception:
        out = {}

    _DCA_24H_CACHE[path] = (sig, now_bucket, dict(out))
    return dict(out)


def _ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)



def _fmt_money(x: float) -> str:
    """Format a USD *amount* (account value, position value, etc.) as dollars with 2 decimals."""
    try:
        return f"${float(x):,.2f}"
    except Exception:
        return "N/A"


def _fmt_price(x: Any) -> str:
    """
    Format a USD *price/level* with dynamic decimals based on magnitude.
    Examples:
      50234.12   -> $50,234.12
      123.4567   -> $123.457
      1.234567   -> $1.2346
      0.06234567 -> $0.062346
      0.00012345 -> $0.00012345
    """
    try:
        if x is None:
            return "N/A"

        v = float(x)
        if not math.isfinite(v):
            return "N/A"

        sign = "-" if v < 0 else ""
        av = abs(v)

        # Choose decimals by magnitude (more detail for smaller prices).
        if av >= 1000:
            dec = 2
        elif av >= 100:
            dec = 3
        elif av >= 1:
            dec = 4
        elif av >= 0.1:
            dec = 5
        elif av >= 0.01:
            dec = 6
        elif av >= 0.001:
            dec = 7
        else:
            dec = 8

        s = f"{av:,.{dec}f}"
        if "." in s:
            s = s.rstrip("0").rstrip(".")

        return f"{sign}${s}"
    except Exception:
        return "N/A"


def _fmt_pct(x: float) -> str:
    try:
        return f"{float(x):+.2f}%"
    except Exception:
        return "N/A"


def _now_str() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


# -----------------------------
# Neural folder detection
# -----------------------------

def build_coin_folders(main_dir: str, coins: List[str]) -> Dict[str, str]:
    """
    Mirrors your convention:
      BTC uses main_dir directly
      other coins typically have subfolders inside main_dir (auto-detected)

    Returns { "BTC": "...", "ETH": "...", ... }
    """
    out: Dict[str, str] = {}
    main_dir = main_dir or os.getcwd()

    # BTC folder
    out["BTC"] = main_dir

    # Auto-detect subfolders
    if os.path.isdir(main_dir):
        for name in os.listdir(main_dir):
            p = os.path.join(main_dir, name)
            if not os.path.isdir(p):
                continue
            sym = name.upper().strip()
            if sym in coins and sym != "BTC":
                out[sym] = p

    # Fallbacks for missing ones
    for c in coins:
        c = c.upper().strip()
        if c not in out:
            out[c] = os.path.join(main_dir, c)  # best-effort fallback

    return out


def read_price_levels_from_html(path: str) -> List[float]:
    """
    pt_thinker writes a python-list-like string into low_bound_prices.html / high_bound_prices.html.

    Example (commas often remain):
        "43210.1, 43100.0, 42950.5"

    So we normalize separators before parsing.
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read().strip()

        if not raw:
            return []

        # Normalize common separators that pt_thinker can leave behind
        raw = (
            raw.replace(",", " ")
               .replace("[", " ")
               .replace("]", " ")
               .replace("'", " ")
        )

        vals: List[float] = []
        for tok in raw.split():
            try:
                v = float(tok)

                # Filter obvious sentinel values used by pt_thinker for "inactive" slots
                if v <= 0:
                    continue
                if v >= 9e15:  # pt_thinker uses 99999999999999999
                    continue


                vals.append(v)
            except Exception:
                pass

        # De-dupe while preserving order (small rounding to avoid float-noise duplicates)
        out: List[float] = []
        seen = set()
        for v in vals:
            key = round(v, 12)
            if key in seen:
                continue
            seen.add(key)
            out.append(v)

        return out
    except Exception:
        return []



def read_int_from_file(path: str) -> int:
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        return int(float(raw))
    except Exception:
        return 0


def read_short_signal(folder: str) -> int:
    txt = os.path.join(folder, "short_dca_signal.txt")
    if os.path.isfile(txt):
        return read_int_from_file(txt)
    else:
        return 0


# -----------------------------
# Candle fetching (KuCoin)
# -----------------------------

class CandleFetcher:
    """
    Uses kucoin-python if available; otherwise falls back to KuCoin REST via requests.
    """
    def __init__(self):
        self._mode = "kucoin_client"
        self._market = None
        try:
            from kucoin.client import Market  # type: ignore
            self._market = Market(url="https://api.kucoin.com")
        except Exception:
            self._mode = "rest"
            self._market = None

        if self._mode == "rest":
            import requests  # local import
            self._requests = requests

        # Small in-memory cache to keep timeframe switching snappy.
        # key: (pair, timeframe, limit) -> (saved_time_epoch, candles)
        self._cache: Dict[Tuple[str, str, int], Tuple[float, List[dict]]] = {}
        self._cache_ttl_seconds: float = 10.0


    def get_klines(self, symbol: str, timeframe: str, limit: int = 120) -> List[dict]:
        """
        Returns candles oldest->newest as:
          [{"ts": int, "open": float, "high": float, "low": float, "close": float}, ...]
        """
        symbol = symbol.upper().strip()

        # Your neural uses USDT pairs on KuCoin (ex: BTC-USDT)
        pair = f"{symbol}-USDT"
        limit = int(limit or 0)

        now = time.time()
        cache_key = (pair, timeframe, limit)
        cached = self._cache.get(cache_key)
        if cached and (now - float(cached[0])) <= float(self._cache_ttl_seconds):
            return cached[1]

        # rough window (timeframe-dependent) so we get enough candles
        tf_seconds = {
            "1min": 60, "5min": 300, "15min": 900, "30min": 1800,
            "1hour": 3600, "2hour": 7200, "4hour": 14400, "8hour": 28800, "12hour": 43200,
            "1day": 86400, "1week": 604800
        }.get(timeframe, 3600)

        end_at = int(now)
        start_at = end_at - (tf_seconds * max(200, (limit + 50) if limit else 250))

        if self._mode == "kucoin_client" and self._market is not None:
            try:
                # IMPORTANT: limit the server response by passing startAt/endAt.
                # This avoids downloading a huge default kline set every switch.
                try:
                    raw = self._market.get_kline(pair, timeframe, startAt=start_at, endAt=end_at)  # type: ignore
                except Exception:
                    # fallback if that client version doesn't accept kwargs
                    raw = self._market.get_kline(pair, timeframe)  # returns newest->oldest

                candles: List[dict] = []
                for row in raw:
                    # KuCoin kline row format:
                    # [time, open, close, high, low, volume, turnover]
                    ts = int(float(row[0]))
                    o = float(row[1]); c = float(row[2]); h = float(row[3]); l = float(row[4])
                    candles.append({"ts": ts, "open": o, "high": h, "low": l, "close": c})
                candles.sort(key=lambda x: x["ts"])
                if limit and len(candles) > limit:
                    candles = candles[-limit:]

                self._cache[cache_key] = (now, candles)
                return candles
            except Exception:
                return []

        # REST fallback
        try:
            url = "https://api.kucoin.com/api/v1/market/candles"
            params = {"symbol": pair, "type": timeframe, "startAt": start_at, "endAt": end_at}
            resp = self._requests.get(url, params=params, timeout=10)
            j = resp.json()
            data = j.get("data", [])  # newest->oldest
            candles: List[dict] = []
            for row in data:
                ts = int(float(row[0]))
                o = float(row[1]); c = float(row[2]); h = float(row[3]); l = float(row[4])
                candles.append({"ts": ts, "open": o, "high": h, "low": l, "close": c})
            candles.sort(key=lambda x: x["ts"])
            if limit and len(candles) > limit:
                candles = candles[-limit:]

            self._cache[cache_key] = (now, candles)
            return candles
        except Exception:
            return []



# -----------------------------
# Chart widget
# -----------------------------

class CandleChart(ttk.Frame):
    def __init__(
        self,
        parent: tk.Widget,
        fetcher: CandleFetcher,
        coin: str,
        settings_getter,
        trade_history_path: str,
    ):
        super().__init__(parent)
        self.fetcher = fetcher
        self.coin = coin
        self.settings_getter = settings_getter
        self.trade_history_path = trade_history_path

        cfg = self.settings_getter() or {}

        tfs = cfg.get("timeframes") or ["1min", "5min", "15min", "30min", "1hour", "2hour", "4hour", "8hour", "12hour", "1day", "1week"]
        if isinstance(tfs, str):
            tfs = [x.strip() for x in tfs.replace("\n", ",").split(",")]
        if not isinstance(tfs, (list, tuple)):
            tfs = ["1min", "5min", "15min", "30min", "1hour", "2hour", "4hour", "8hour", "12hour", "1day", "1week"]
        tfs = [str(x).strip() for x in tfs if str(x).strip()] or ["1min", "5min", "15min", "30min", "1hour", "2hour", "4hour", "8hour", "12hour", "1day", "1week"]

        dtf = str(cfg.get("default_timeframe") or "").strip()
        if (not dtf) or (dtf not in tfs):
            dtf = tfs[0] if tfs else "1hour"

        self.timeframe_var = tk.StringVar(value=dtf)


        top = ttk.Frame(self)
        top.pack(fill="x", padx=6, pady=6)

        ttk.Label(top, text=f"{coin} chart").pack(side="left")

        ttk.Label(top, text="Timeframe:").pack(side="left", padx=(12, 4))
        self.tf_combo = ttk.Combobox(
            top,
            textvariable=self.timeframe_var,
            values=list(tfs),
            state="readonly",
            width=10,
        )
        self.tf_combo.pack(side="left")


        # Debounce rapid timeframe changes so redraws don't stack
        self._tf_after_id = None

        def _debounced_tf_change(*_):
            try:
                if self._tf_after_id:
                    self.after_cancel(self._tf_after_id)
            except Exception:
                pass

            def _do():
                # Ask the hub to refresh charts on the next tick (single refresh)
                try:
                    self.event_generate("<<TimeframeChanged>>", when="tail")
                except Exception:
                    pass

            self._tf_after_id = self.after(120, _do)

        self.tf_combo.bind("<<ComboboxSelected>>", _debounced_tf_change)


        self.neural_status_label = ttk.Label(top, text="Neural: N/A")
        self.neural_status_label.pack(side="left", padx=(12, 0))

        self.last_update_label = ttk.Label(top, text="Last: N/A")
        self.last_update_label.pack(side="right")

        # Figure
        # IMPORTANT: keep a stable DPI and resize the figure to the widget's pixel size.
        # On Windows scaling, trying to "sync DPI" via winfo_fpixels("1i") can produce the
        # exact right-side blank/covered region you're seeing.
        self.fig = Figure(figsize=(6.5, 3.5), dpi=100)
        self.fig.patch.set_facecolor(DARK_BG)

        # Reserve bottom space so date+time x tick labels are always visible
        # Also reserve right space so the price labels (Bid/Ask/DCA/Sell) can sit outside the plot.
        # Also reserve a bit of top space so the title never gets clipped.
        self.fig.subplots_adjust(bottom=0.20, right=0.87, top=0.8)

        self.ax = self.fig.add_subplot(111)
        self._apply_dark_chart_style()
        self.ax.set_title(f"{coin}", color=DARK_FG)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        canvas_w = self.canvas.get_tk_widget()
        canvas_w.configure(bg=DARK_BG)

        # Remove horizontal padding here so the chart widget truly fills the container.
        canvas_w.pack(fill="both", expand=True, padx=0, pady=(0, 6))

        # Keep the matplotlib figure EXACTLY the same pixel size as the Tk widget.
        # FigureCanvasTkAgg already sizes its backing PhotoImage to e.width/e.height.
        # Multiplying by tk scaling here makes the renderer larger than the PhotoImage,
        # which produces the "blank/covered strip" on the right.
        self._last_canvas_px = (0, 0)
        self._resize_after_id = None

        def _on_canvas_configure(e):
            try:
                w = int(e.width)
                h = int(e.height)
                if w <= 1 or h <= 1:
                    return

                if (w, h) == self._last_canvas_px:
                    return
                self._last_canvas_px = (w, h)

                dpi = float(self.fig.get_dpi() or 100.0)
                self.fig.set_size_inches(w / dpi, h / dpi, forward=True)

                # Debounce redraws during live resize
                if self._resize_after_id:
                    try:
                        self.after_cancel(self._resize_after_id)
                    except Exception:
                        pass
                self._resize_after_id = self.after_idle(self.canvas.draw_idle)
            except Exception:
                pass

        canvas_w.bind("<Configure>", _on_canvas_configure, add="+")







        self._last_refresh = 0.0


    def _apply_dark_chart_style(self) -> None:
        """Apply dark styling (called on init and after every ax.clear())."""
        try:
            self.fig.patch.set_facecolor(DARK_BG)
            self.ax.set_facecolor(DARK_PANEL)
            self.ax.tick_params(colors=DARK_FG)
            for spine in self.ax.spines.values():
                spine.set_color(DARK_BORDER)
            self.ax.grid(True, color=DARK_BORDER, linewidth=0.6, alpha=0.35)
        except Exception:
            pass

    def preload_refresh_data(
        self,
        coin_folders: Dict[str, str],
        current_buy_price: Optional[float] = None,
        current_sell_price: Optional[float] = None,
        trail_line: Optional[float] = None,
        dca_line_price: Optional[float] = None,
        avg_cost_basis: Optional[float] = None,
    ) -> dict:
        """
        Prepare everything needed for a chart refresh OFF the Tk main thread.
        No widget/matplotlib mutation happens here.
        """
        cfg = self.settings_getter() or {}

        tf = self.timeframe_var.get().strip()
        limit = int(cfg.get("candles_limit", 120))

        candles = self.fetcher.get_klines(self.coin, tf, limit=limit)

        folder = coin_folders.get(self.coin, "")
        low_path = os.path.join(folder, "low_bound_prices.html")
        high_path = os.path.join(folder, "high_bound_prices.html")
        long_sig_path = os.path.join(folder, "long_dca_signal.txt")
        short_sig_path = os.path.join(folder, "short_dca_signal.txt")

        long_levels = read_price_levels_from_html(low_path) if (folder and os.path.isfile(low_path)) else []
        short_levels = read_price_levels_from_html(high_path) if (folder and os.path.isfile(high_path)) else []
        long_sig = read_int_from_file(long_sig_path) if (folder and os.path.isfile(long_sig_path)) else 0
        short_sig = read_int_from_file(short_sig_path) if (folder and os.path.isfile(short_sig_path)) else 0

        last_ts = None
        try:
            mts = []
            for p in (low_path, high_path, long_sig_path, short_sig_path):
                if os.path.isfile(p):
                    mts.append(float(os.path.getmtime(p)))
            if mts:
                last_ts = max(mts)
        except Exception:
            last_ts = None

        trades: List[dict] = []
        try:
            trades = _read_trade_history_jsonl(self.trade_history_path) if self.trade_history_path else []
        except Exception:
            trades = []

        return {
            "tf": tf,
            "candles": candles,
            "long_levels": long_levels,
            "short_levels": short_levels,
            "long_sig": long_sig,
            "short_sig": short_sig,
            "last_ts": last_ts,
            "trades": trades,
            "current_buy_price": current_buy_price,
            "current_sell_price": current_sell_price,
            "trail_line": trail_line,
            "dca_line_price": dca_line_price,
            "avg_cost_basis": avg_cost_basis,
        }

    def apply_refresh_data(self, payload: dict) -> None:
        """
        Apply a prepared chart refresh ON the Tk main thread.
        """
        tf = str(payload.get("tf", self.timeframe_var.get().strip()))
        candles = payload.get("candles", []) or []
        long_levels = payload.get("long_levels", []) or []
        short_levels = payload.get("short_levels", []) or []
        long_sig = int(payload.get("long_sig", 0) or 0)
        short_sig = int(payload.get("short_sig", 0) or 0)
        last_ts = payload.get("last_ts", None)
        trades = payload.get("trades", []) or []

        current_buy_price = payload.get("current_buy_price", None)
        current_sell_price = payload.get("current_sell_price", None)
        trail_line = payload.get("trail_line", None)
        dca_line_price = payload.get("dca_line_price", None)
        avg_cost_basis = payload.get("avg_cost_basis", None)

        # --- Avoid full ax.clear() (expensive). Just clear artists. ---
        try:
            self.ax.lines.clear()
            self.ax.patches.clear()
            self.ax.collections.clear()  # scatter dots live here
            self.ax.texts.clear()        # labels/annotations live here
        except Exception:
            self.ax.cla()
            self._apply_dark_chart_style()

        if not candles:
            self.ax.set_title(f"{self.coin} ({tf}) - no candles", color=DARK_FG)
            self.canvas.draw_idle()
            self.neural_status_label.config(
                text=f"Neural: long={long_sig} short={short_sig} | levels L={len(long_levels)} S={len(short_levels)}"
            )
            self.last_update_label.config(text="Last: N/A")
            return

        xs = getattr(self, "_xs", None)
        if not xs or len(xs) != len(candles):
            xs = list(range(len(candles)))
            self._xs = xs

        rects = []
        for i, c in enumerate(candles):
            o = float(c["open"])
            cl = float(c["close"])
            h = float(c["high"])
            l = float(c["low"])

            up = cl >= o
            candle_color = "green" if up else "red"

            self.ax.plot([i, i], [l, h], linewidth=1, color=candle_color)

            bottom = min(o, cl)
            height = abs(cl - o)
            if height < 1e-12:
                height = 1e-12

            rects.append(
                Rectangle(
                    (i - 0.35, bottom),
                    0.7,
                    height,
                    facecolor=candle_color,
                    edgecolor=candle_color,
                    linewidth=1,
                    alpha=0.9,
                )
            )

        for r in rects:
            self.ax.add_patch(r)

        try:
            y_low = min(float(c["low"]) for c in candles)
            y_high = max(float(c["high"]) for c in candles)
            pad = (y_high - y_low) * 0.03
            if not math.isfinite(pad) or pad <= 0:
                pad = max(abs(y_low) * 0.001, 1e-6)
            self.ax.set_ylim(y_low - pad, y_high + pad)
        except Exception:
            pass

        for lv in long_levels:
            try:
                self.ax.axhline(y=float(lv), linewidth=1, color="blue", alpha=0.8)
            except Exception:
                pass

        for lv in short_levels:
            try:
                self.ax.axhline(y=float(lv), linewidth=1, color="orange", alpha=0.8)
            except Exception:
                pass

        try:
            if trail_line is not None and float(trail_line) > 0:
                self.ax.axhline(y=float(trail_line), linewidth=1.5, color="green", alpha=0.95)
        except Exception:
            pass

        try:
            if dca_line_price is not None and float(dca_line_price) > 0:
                self.ax.axhline(y=float(dca_line_price), linewidth=1.5, color="red", alpha=0.95)
        except Exception:
            pass

        try:
            if avg_cost_basis is not None and float(avg_cost_basis) > 0:
                self.ax.axhline(y=float(avg_cost_basis), linewidth=1.5, color="yellow", alpha=0.95)
        except Exception:
            pass

        try:
            if current_buy_price is not None and float(current_buy_price) > 0:
                self.ax.axhline(y=float(current_buy_price), linewidth=1.5, color="purple", alpha=0.95)
        except Exception:
            pass

        try:
            if current_sell_price is not None and float(current_sell_price) > 0:
                self.ax.axhline(y=float(current_sell_price), linewidth=1.5, color="teal", alpha=0.95)
        except Exception:
            pass

        try:
            trans = blended_transform_factory(self.ax.transAxes, self.ax.transData)
            used_y: List[float] = []
            y0, y1 = self.ax.get_ylim()
            y_pad = max((y1 - y0) * 0.012, 1e-9)

            def _label_right(y: Optional[float], tag: str, color: str) -> None:
                if y is None:
                    return
                try:
                    yy = float(y)
                    if (not math.isfinite(yy)) or yy <= 0:
                        return
                except Exception:
                    return

                for prev in used_y:
                    if abs(yy - prev) < y_pad:
                        yy = prev + y_pad
                used_y.append(yy)

                self.ax.text(
                    1.01,
                    yy,
                    f"{tag} {_fmt_price(yy)}",
                    transform=trans,
                    ha="left",
                    va="center",
                    fontsize=8,
                    color=color,
                    bbox=dict(
                        facecolor=DARK_BG2,
                        edgecolor=color,
                        boxstyle="round,pad=0.18",
                        alpha=0.85,
                    ),
                    zorder=20,
                    clip_on=False,
                )

            _label_right(current_buy_price, "ASK", "purple")
            _label_right(current_sell_price, "BID", "teal")
            _label_right(avg_cost_basis, "AVG", "yellow")
            _label_right(dca_line_price, "DCA", "red")
            _label_right(trail_line, "SELL", "green")
        except Exception:
            pass

        try:
            if trades:
                candle_ts = [int(c["ts"]) for c in candles]
                t_min = float(candle_ts[0])
                t_max = float(candle_ts[-1])

                coin_upper = self.coin.upper().strip()

                for tr in trades:
                    sym = str(tr.get("symbol", "")).upper()
                    base = sym.split("-")[0].strip() if sym else ""
                    if base != coin_upper:
                        continue

                    tts = tr.get("ts", None)
                    if tts is None:
                        continue
                    try:
                        tts = float(tts)
                    except Exception:
                        continue
                    if tts < t_min or tts > t_max:
                        continue

                    side = str(tr.get("side", "")).lower().strip()
                    tag = str(tr.get("tag") or "").upper().strip()

                    if side == "buy":
                        label = "DCA" if tag == "DCA" else "BUY"
                        color = "purple" if tag == "DCA" else "red"
                    elif side == "sell":
                        label = "SELL"
                        color = "green"
                    else:
                        continue

                    i = bisect.bisect_left(candle_ts, tts)
                    if i <= 0:
                        idx = 0
                    elif i >= len(candle_ts):
                        idx = len(candle_ts) - 1
                    else:
                        idx = i if abs(candle_ts[i] - tts) < abs(tts - candle_ts[i - 1]) else (i - 1)

                    y = None
                    try:
                        p = tr.get("price", None)
                        if p is not None and float(p) > 0:
                            y = float(p)
                    except Exception:
                        y = None

                    if y is None:
                        try:
                            y = float(candles[idx].get("close", 0.0))
                        except Exception:
                            y = None

                    if y is None:
                        continue

                    x = idx
                    self.ax.scatter([x], [y], s=35, color=color, zorder=6)
                    self.ax.annotate(
                        label,
                        (x, y),
                        textcoords="offset points",
                        xytext=(0, 10),
                        ha="center",
                        fontsize=8,
                        color=DARK_FG,
                        zorder=7,
                    )
        except Exception:
            pass

        self.ax.set_xlim(-0.5, (len(candles) - 0.5) + 0.6)
        self.ax.set_title(f"{self.coin} ({tf})", color=DARK_FG)

        n = len(candles)
        want = 5
        if n <= want:
            idxs = list(range(n))
        else:
            step = (n - 1) / float(want - 1)
            idxs = []
            last = -1
            for j in range(want):
                i = int(round(j * step))
                if i <= last:
                    i = last + 1
                if i >= n:
                    i = n - 1
                idxs.append(i)
                last = i

        tick_x = [xs[i] for i in idxs]
        tick_lbl = [
            time.strftime("%Y-%m-%d\n%H:%M", time.localtime(int(candles[i].get("ts", 0))))
            for i in idxs
        ]

        try:
            self.ax.minorticks_off()
            self.ax.set_xticks(tick_x)
            self.ax.set_xticklabels(tick_lbl)
            self.ax.tick_params(axis="x", labelsize=8)
        except Exception:
            pass

        self.canvas.draw_idle()

        self.neural_status_label.config(
            text=f"Neural: long={long_sig} short={short_sig} | levels L={len(long_levels)} S={len(short_levels)}"
        )

        if last_ts:
            self.last_update_label.config(text=f"Last: {time.strftime('%H:%M:%S', time.localtime(float(last_ts)))}")
        else:
            self.last_update_label.config(text="Last: N/A")

    def refresh(
        self,
        coin_folders: Dict[str, str],
        current_buy_price: Optional[float] = None,
        current_sell_price: Optional[float] = None,
        trail_line: Optional[float] = None,
        dca_line_price: Optional[float] = None,
        avg_cost_basis: Optional[float] = None,
    ) -> None:
        """
        Backward-compatible wrapper.
        """
        payload = self.preload_refresh_data(
            coin_folders,
            current_buy_price=current_buy_price,
            current_sell_price=current_sell_price,
            trail_line=trail_line,
            dca_line_price=dca_line_price,
            avg_cost_basis=avg_cost_basis,
        )
        self.apply_refresh_data(payload)


# -----------------------------
# Account Value chart widget
# -----------------------------

class AccountValueChart(ttk.Frame):
    def __init__(self, parent: tk.Widget, history_path: str, trade_history_path: str, max_points: int = 250):
        super().__init__(parent)
        self.history_path = history_path
        self.trade_history_path = trade_history_path
        # Hard-cap to 250 points max (account value chart only)
        self.max_points = min(int(max_points or 0) or 250, 250)
        self._last_mtime: Optional[float] = None


        top = ttk.Frame(self)
        top.pack(fill="x", padx=6, pady=6)

        ttk.Label(top, text="Account value").pack(side="left")
        self.last_update_label = ttk.Label(top, text="Last: N/A")
        self.last_update_label.pack(side="right")

        self.fig = Figure(figsize=(6.5, 3.5), dpi=100)
        self.fig.patch.set_facecolor(DARK_BG)

        # Reserve bottom space so date+time x tick labels are always visible
        # Also reserve right space so the price labels (Bid/Ask/DCA/Sell) can sit outside the plot.
        # Also reserve a bit of top space so the title never gets clipped.
        self.fig.subplots_adjust(bottom=0.25, right=0.87, top=0.8)

        self.ax = self.fig.add_subplot(111)
        self._apply_dark_chart_style()
        self.ax.set_title("Account Value", color=DARK_FG)

        self.canvas = FigureCanvasTkAgg(self.fig, master=self)
        canvas_w = self.canvas.get_tk_widget()
        canvas_w.configure(bg=DARK_BG)

        # Remove horizontal padding here so the chart widget truly fills the container.
        canvas_w.pack(fill="both", expand=True, padx=0, pady=(0, 6))

        # Keep the matplotlib figure EXACTLY the same pixel size as the Tk widget.
        # FigureCanvasTkAgg already sizes its backing PhotoImage to e.width/e.height.
        # Multiplying by tk scaling here makes the renderer larger than the PhotoImage,
        # which produces the "blank/covered strip" on the right.
        self._last_canvas_px = (0, 0)
        self._resize_after_id = None

        def _on_canvas_configure(e):
            try:
                w = int(e.width)
                h = int(e.height)
                if w <= 1 or h <= 1:
                    return

                if (w, h) == self._last_canvas_px:
                    return
                self._last_canvas_px = (w, h)

                dpi = float(self.fig.get_dpi() or 100.0)
                self.fig.set_size_inches(w / dpi, h / dpi, forward=True)

                # Debounce redraws during live resize
                if self._resize_after_id:
                    try:
                        self.after_cancel(self._resize_after_id)
                    except Exception:
                        pass
                self._resize_after_id = self.after_idle(self.canvas.draw_idle)
            except Exception:
                pass

        canvas_w.bind("<Configure>", _on_canvas_configure, add="+")








    def _apply_dark_chart_style(self) -> None:
        try:
            self.fig.patch.set_facecolor(DARK_BG)
            self.ax.set_facecolor(DARK_PANEL)
            self.ax.tick_params(colors=DARK_FG)
            for spine in self.ax.spines.values():
                spine.set_color(DARK_BORDER)
            self.ax.grid(True, color=DARK_BORDER, linewidth=0.6, alpha=0.35)
        except Exception:
            pass

    def preload_refresh_data(self) -> dict:
        path = self.history_path

        # mtime cache so we don't prepare a redraw if nothing changed
        try:
            m_hist = os.path.getmtime(path)
        except Exception:
            m_hist = None

        try:
            m_trades = os.path.getmtime(self.trade_history_path) if self.trade_history_path else None
        except Exception:
            m_trades = None

        candidates = [m for m in (m_hist, m_trades) if m is not None]
        mtime = max(candidates) if candidates else None

        if (
            getattr(self, "_drawn_once", False)
            and mtime is not None
            and self._last_mtime == mtime
        ):
            return {"skip": True, "mtime": mtime}

        points: List[Tuple[float, float]] = []

        try:
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    for ln in f:
                        try:
                            obj = json.loads(ln)
                            ts = obj.get("ts", None)
                            v = obj.get("total_account_value", None)
                            if ts is None or v is None:
                                continue

                            tsf = float(ts)
                            vf = float(v)

                            if (not math.isfinite(tsf)) or (not math.isfinite(vf)) or (vf <= 0.0):
                                continue

                            points.append((tsf, vf))
                        except Exception:
                            continue
        except Exception:
            points = []

        if points:
            points.sort(key=lambda x: x[0])

            dedup: List[Tuple[float, float]] = []
            for tsf, vf in points:
                if dedup and tsf == dedup[-1][0]:
                    dedup[-1] = (tsf, vf)
                else:
                    dedup.append((tsf, vf))
            points = dedup

        max_keep = min(max(2, int(self.max_points or 250)), 250)
        n = len(points)

        if n > max_keep:
            first_pt = points[0]
            last_pt = points[-1]

            mid_points = points[1:-1]
            mid_n = len(mid_points)
            keep_mid = max_keep - 2

            if keep_mid <= 0 or mid_n <= 0:
                points = [first_pt, last_pt]
            elif mid_n <= keep_mid:
                points = [first_pt] + mid_points + [last_pt]
            else:
                bucket_size = mid_n / float(keep_mid)
                new_mid: List[Tuple[float, float]] = []

                for i in range(keep_mid):
                    start = int(i * bucket_size)
                    end = int((i + 1) * bucket_size)
                    if end <= start:
                        end = start + 1
                    if start >= mid_n:
                        break
                    if end > mid_n:
                        end = mid_n

                    bucket = mid_points[start:end]
                    if not bucket:
                        continue

                    avg_ts = sum(p[0] for p in bucket) / len(bucket)
                    avg_val = sum(p[1] for p in bucket) / len(bucket)
                    new_mid.append((avg_ts, avg_val))

                points = [first_pt] + new_mid + [last_pt]

        markers: List[Tuple[int, float, str, str]] = []

        try:
            trades = _read_trade_history_jsonl(self.trade_history_path) if self.trade_history_path else []
            if trades and points:
                ts_list = [float(p[0]) for p in points]
                ys = [round(p[1], 2) for p in points]
                t_min = ts_list[0]
                t_max = ts_list[-1]

                for tr in trades:
                    tts = tr.get("ts")
                    try:
                        tts = float(tts)
                    except Exception:
                        continue
                    if tts < t_min or tts > t_max:
                        continue

                    side = str(tr.get("side", "")).lower().strip()
                    tag = str(tr.get("tag", "")).upper().strip()

                    if side == "buy":
                        action_label = "DCA" if tag == "DCA" else "BUY"
                        color = "purple" if tag == "DCA" else "red"
                    elif side == "sell":
                        action_label = "SELL"
                        color = "green"
                    else:
                        continue

                    sym = str(tr.get("symbol", "")).upper().strip()
                    coin_tag = (sym.split("-")[0].split("/")[0].strip() if sym else "") or (sym or "?")
                    label = f"{coin_tag} {action_label}"

                    i = bisect.bisect_left(ts_list, tts)
                    if i <= 0:
                        idx = 0
                    elif i >= len(ts_list):
                        idx = len(ts_list) - 1
                    else:
                        idx = i if abs(ts_list[i] - tts) < abs(tts - ts_list[i - 1]) else (i - 1)

                    markers.append((idx, ys[idx], label, color))

                marker_cap = 120
                if len(markers) > marker_cap:
                    step = (len(markers) - 1) / float(marker_cap - 1)
                    sampled: List[Tuple[int, float, str, str]] = []
                    last_k = -1
                    for j in range(marker_cap):
                        k = int(round(j * step))
                        if k <= last_k:
                            k = last_k + 1
                        if k >= len(markers):
                            k = len(markers) - 1
                        sampled.append(markers[k])
                        last_k = k
                    markers = sampled
        except Exception:
            markers = []

        return {
            "skip": False,
            "mtime": mtime,
            "points": points,
            "markers": markers,
        }

    def apply_refresh_data(self, payload: dict) -> None:
        if not isinstance(payload, dict):
            return

        if payload.get("skip", False):
            return

        self._last_mtime = payload.get("mtime", None)
        self._drawn_once = True

        points = payload.get("points", []) or []
        markers = payload.get("markers", []) or []

        try:
            self.ax.lines.clear()
            self.ax.patches.clear()
            self.ax.collections.clear()
            self.ax.texts.clear()
        except Exception:
            self.ax.cla()
            self._apply_dark_chart_style()

        if not points:
            self.ax.set_title("Account Value - no data", color=DARK_FG)
            self.last_update_label.config(text="Last: N/A")
            self.canvas.draw_idle()
            return

        xs = list(range(len(points)))
        ys = [round(float(p[1]), 2) for p in points]

        self.ax.plot(xs, ys, linewidth=1.5)

        for x, y, label, color in markers:
            try:
                self.ax.scatter([x], [y], s=30, color=color, zorder=6)
                self.ax.annotate(
                    label,
                    (x, y),
                    textcoords="offset points",
                    xytext=(0, 10),
                    ha="center",
                    fontsize=8,
                    color=DARK_FG,
                    zorder=7,
                )
            except Exception:
                pass

        try:
            self.ax.yaxis.set_major_formatter(FuncFormatter(lambda y, _pos: f"${y:,.2f}"))
        except Exception:
            pass

        n = len(points)
        want = 5
        if n <= want:
            idxs = list(range(n))
        else:
            step = (n - 1) / float(want - 1)
            idxs = []
            last = -1
            for j in range(want):
                i = int(round(j * step))
                if i <= last:
                    i = last + 1
                if i >= n:
                    i = n - 1
                idxs.append(i)
                last = i

        tick_x = [xs[i] for i in idxs]
        tick_lbl = [time.strftime("%Y-%m-%d\n%H:%M:%S", time.localtime(points[i][0])) for i in idxs]
        try:
            self.ax.minorticks_off()
            self.ax.set_xticks(tick_x)
            self.ax.set_xticklabels(tick_lbl)
            self.ax.tick_params(axis="x", labelsize=8)
        except Exception:
            pass

        self.ax.set_xlim(-0.5, (len(points) - 0.5) + 0.6)

        try:
            self.ax.set_title(f"Account Value ({_fmt_money(ys[-1])})", color=DARK_FG)
        except Exception:
            self.ax.set_title("Account Value", color=DARK_FG)

        try:
            self.last_update_label.config(
                text=f"Last: {time.strftime('%H:%M:%S', time.localtime(points[-1][0]))}"
            )
        except Exception:
            self.last_update_label.config(text="Last: N/A")

        self.canvas.draw_idle()

    def refresh(self) -> None:
        payload = self.preload_refresh_data()
        self.apply_refresh_data(payload)



# -----------------------------
# Hub App
# -----------------------------

@dataclass
class ProcInfo:
    name: str
    path: str
    proc: Optional[subprocess.Popen] = None



@dataclass
class LogProc:
    """
    A running process with a live log queue for stdout/stderr lines.
    """
    info: ProcInfo
    log_q: "queue.Queue[str]"
    thread: Optional[threading.Thread] = None
    is_trainer: bool = False
    coin: Optional[str] = None



class PowerTraderHub(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PowerTrader - Hub")
        self.geometry("1400x820")

        # Hard minimum window size so the UI can't be shrunk to a point where panes vanish.
        # (Keeps things usable even if someone aggressively resizes.)
        self.minsize(980, 640)

        # Debounce map for panedwindow clamp operations
        self._paned_clamp_after_ids: Dict[str, str] = {}

        # Force one and only one theme: dark mode everywhere.
        self._apply_forced_dark_mode()

        self.settings = self._load_settings()

        self.project_dir = os.path.abspath(os.path.dirname(__file__))

        main_dir = str(self.settings.get("main_neural_dir") or "").strip()
        if main_dir and not os.path.isabs(main_dir):
            main_dir = os.path.abspath(os.path.join(self.project_dir, main_dir))
        if (not main_dir) or (not os.path.isdir(main_dir)):
            main_dir = self.project_dir
        self.settings["main_neural_dir"] = main_dir


        # hub data dir
        hub_dir = self.settings.get("hub_data_dir") or os.path.join(self.project_dir, "hub_data")
        self.hub_dir = os.path.abspath(hub_dir)
        _ensure_dir(self.hub_dir)

        # file paths written by pt_trader.py (after edits below)
        self.trader_status_path = os.path.join(self.hub_dir, "trader_status.json")
        self.trade_history_path = os.path.join(self.hub_dir, "trade_history.jsonl")
        self.pnl_ledger_path = os.path.join(self.hub_dir, "pnl_ledger.json")
        self.account_value_history_path = os.path.join(self.hub_dir, "account_value_history.jsonl")

        # file written by pt_thinker.py (runner readiness gate used for Start All)
        self.runner_ready_path = os.path.join(self.hub_dir, "runner_ready.json")


        # internal: when Start All is pressed, we start the runner first and only start the trader once ready
        self._auto_start_trader_pending = False

        # Neural auto-restart state:
        # - should_be_running: hub intends thinker to keep running
        # - user_stopped_from_hub: thinker was explicitly stopped from THIS hub UI
        # - last_auto_restart_ts: cooldown so we don't hammer-restart in a tight loop
        self._neural_should_be_running = False
        self._neural_user_stopped_from_hub = False
        self._neural_last_auto_restart_ts = 0.0
        self._neural_restart_cooldown_seconds = 5.0

        # IMPORTANT:
        # Reset hub-side restart flags on every hub startup so a previous session cannot
        # accidentally make this new session auto-restart thinker.
        self._reset_neural_autorestart_state_on_startup()

        # cache latest trader status so charts can overlay buy/sell lines
        self._last_positions: Dict[str, dict] = {}

        # account value chart widget (created in _build_layout)
        self.account_chart = None



        # coin folders (neural outputs)
        self.coins = [c.upper().strip() for c in self.settings["coins"]]

        # Chart coins = configured coins PLUS any coins listed as long-term holdings.
        # (We keep charts separate from self.coins so trading allocation math stays correct.)
        try:
            lth_cfg = self.settings.get("long_term_holdings") or []
            if isinstance(lth_cfg, str):
                lth_cfg = [x.strip() for x in lth_cfg.replace("\n", ",").split(",")]
            if not isinstance(lth_cfg, (list, tuple)):
                lth_cfg = []
            lth_coins = [str(x).upper().strip() for x in lth_cfg if str(x).strip()]
        except Exception:
            lth_coins = []


        base_set = set(self.coins)
        extras = sorted(set(lth_coins) - base_set)
        self.chart_coins = list(self.coins) + extras

        # On startup (like on Settings-save), create missing alt folders and copy the trainer into them.
        self._ensure_alt_coin_folders_and_trainer_on_startup()

        # Rebuild folder maps after potential folder creation
        self.coin_folders = build_coin_folders(self.settings["main_neural_dir"], self.coins)
        self.chart_coin_folders = build_coin_folders(self.settings["main_neural_dir"], self.chart_coins)

        # scripts
        self.proc_neural = ProcInfo(
            name="Neural Runner",
            path=os.path.abspath(os.path.join(self.project_dir, self.settings["script_neural_runner2"]))
        )
        self.proc_trader = ProcInfo(
            name="Trader",
            path=os.path.abspath(os.path.join(self.project_dir, self.settings["script_trader"]))
        )

        self.proc_trainer_path = os.path.abspath(os.path.join(self.project_dir, self.settings["script_neural_trainer"]))

        # live log queues
        # Bounded on purpose: prevents runaway subprocess spam from making the GUI
        # spend huge amounts of time draining logs on the Tk main thread.
        self.runner_log_q: "queue.Queue[str]" = queue.Queue(maxsize=4000)
        self.trader_log_q: "queue.Queue[str]" = queue.Queue(maxsize=4000)

        # trainers: coin -> LogProc
        self.trainers: Dict[str, LogProc] = {}

        self.fetcher = CandleFetcher()

        # Visible coin-chart refresh is prepared off the Tk thread, then applied on the Tk thread.
        # This keeps hover/click/scroll responsive while data/network/file work happens.
        # IMPORTANT: only allow one visible-chart worker at a time. Otherwise timed refreshes
        # can stack background workers and the hub's RAM can balloon.
        self._chart_refresh_lock = threading.Lock()
        self._chart_refresh_request_id = 0
        self._chart_refresh_inflight_id = 0
        self._chart_refresh_result: Optional[dict] = None

        # Account-value chart refresh is also prepared off the Tk thread.
        # The old code rebuilt/parsing the entire account-value history on the Tk thread,
        # which is what caused the periodic UI stalls.
        # IMPORTANT: only allow one account-chart worker at a time for the same reason.
        self._account_chart_refresh_lock = threading.Lock()
        self._account_chart_refresh_request_id = 0
        self._account_chart_refresh_inflight_id = 0
        self._account_chart_refresh_result: Optional[dict] = None

        self._build_menu()
        self._build_layout()

        # Refresh charts immediately when a timeframe is changed (don't wait for the throttle).
        self.bind_all("<<TimeframeChanged>>", self._on_timeframe_changed)

        self._last_chart_refresh = 0.0

        if bool(self.settings.get("auto_start_scripts", False)):
            self.start_all_scripts()

        self.after(250, self._tick)

        self.protocol("WM_DELETE_WINDOW", self._on_close)


    # ---- forced dark mode ----

    def _apply_forced_dark_mode(self) -> None:
        """Force a single, global, non-optional dark theme."""
        # Root background (handles the areas behind ttk widgets)
        try:
            self.configure(bg=DARK_BG)
        except Exception:
            pass

        # Defaults for classic Tk widgets (Text/Listbox/Menu) created later
        try:
            self.option_add("*Text.background", DARK_PANEL)
            self.option_add("*Text.foreground", DARK_FG)
            self.option_add("*Text.insertBackground", DARK_FG)
            self.option_add("*Text.selectBackground", DARK_SELECT_BG)
            self.option_add("*Text.selectForeground", DARK_SELECT_FG)

            self.option_add("*Listbox.background", DARK_PANEL)
            self.option_add("*Listbox.foreground", DARK_FG)
            self.option_add("*Listbox.selectBackground", DARK_SELECT_BG)
            self.option_add("*Listbox.selectForeground", DARK_SELECT_FG)

            self.option_add("*Menu.background", DARK_BG2)
            self.option_add("*Menu.foreground", DARK_FG)
            self.option_add("*Menu.activeBackground", DARK_SELECT_BG)
            self.option_add("*Menu.activeForeground", DARK_SELECT_FG)
        except Exception:
            pass

        style = ttk.Style(self)

        # Pick a theme that is actually recolorable (Windows 'vista' theme ignores many color configs)
        try:
            style.theme_use("clam")
        except Exception:
            pass

        # Base defaults
        try:
            style.configure(".", background=DARK_BG, foreground=DARK_FG)
        except Exception:
            pass

        # Containers / text
        for name in ("TFrame", "TLabel", "TCheckbutton", "TRadiobutton"):
            try:
                style.configure(name, background=DARK_BG, foreground=DARK_FG)
            except Exception:
                pass

        try:
            style.configure("TLabelframe", background=DARK_BG, foreground=DARK_FG, bordercolor=DARK_BORDER)
            style.configure("TLabelframe.Label", background=DARK_BG, foreground=DARK_ACCENT)
        except Exception:
            pass

        try:
            style.configure("TSeparator", background=DARK_BORDER)
        except Exception:
            pass

        # Buttons
        try:
            style.configure(
                "TButton",
                background=DARK_BG2,
                foreground=DARK_FG,
                bordercolor=DARK_BORDER,
                focusthickness=1,
                focuscolor=DARK_ACCENT,
                padding=(10, 6),
            )
            style.map(
                "TButton",
                background=[
                    ("active", DARK_PANEL2),
                    ("pressed", DARK_PANEL),
                    ("disabled", DARK_BG2),
                ],
                foreground=[
                    ("active", DARK_ACCENT),
                    ("disabled", DARK_MUTED),
                ],
                bordercolor=[
                    ("active", DARK_ACCENT2),
                    ("focus", DARK_ACCENT),
                ],
            )
        except Exception:
            pass

        # Entries / combos
        try:
            style.configure(
                "TEntry",
                fieldbackground=DARK_PANEL,
                foreground=DARK_FG,
                bordercolor=DARK_BORDER,
                insertcolor=DARK_FG,
            )
        except Exception:
            pass

        try:
            style.configure(
                "TCombobox",
                fieldbackground=DARK_PANEL,
                background=DARK_PANEL,
                foreground=DARK_FG,
                bordercolor=DARK_BORDER,
                arrowcolor=DARK_ACCENT,
            )
            style.map(
                "TCombobox",
                fieldbackground=[
                    ("readonly", DARK_PANEL),
                    ("focus", DARK_PANEL2),
                ],
                foreground=[("readonly", DARK_FG)],
                background=[("readonly", DARK_PANEL)],
            )
        except Exception:
            pass

        # Notebooks
        try:
            style.configure("TNotebook", background=DARK_BG, bordercolor=DARK_BORDER)
            style.configure("TNotebook.Tab", background=DARK_BG2, foreground=DARK_FG, padding=(10, 6))
            style.map(
                "TNotebook.Tab",
                background=[
                    ("selected", DARK_PANEL),
                    ("active", DARK_PANEL2),
                ],
                foreground=[
                    ("selected", DARK_ACCENT),
                    ("active", DARK_ACCENT2),
                ],
            )

            # Charts tabs need to wrap to multiple lines. ttk.Notebook can't do that,
            # so we hide the Notebook's native tabs and render our own wrapping tab bar.
            #
            # IMPORTANT: the layout must exclude Notebook.tab entirely, and on some themes
            # you must keep Notebook.padding for proper sizing; otherwise the tab strip
            # can still render.
            style.configure("HiddenTabs.TNotebook", tabmargins=0)
            style.layout(
                "HiddenTabs.TNotebook",
                [
                    (
                        "Notebook.padding",
                        {
                            "sticky": "nswe",
                            "children": [
                                ("Notebook.client", {"sticky": "nswe"}),
                            ],
                        },
                    )
                ],
            )

            # Wrapping chart-tab buttons (normal + selected)
            style.configure(
                "ChartTab.TButton",
                background=DARK_BG2,
                foreground=DARK_FG,
                bordercolor=DARK_BORDER,
                padding=(10, 6),
            )
            style.map(
                "ChartTab.TButton",
                background=[("active", DARK_PANEL2), ("pressed", DARK_PANEL)],
                foreground=[("active", DARK_ACCENT2)],
                bordercolor=[("active", DARK_ACCENT2), ("focus", DARK_ACCENT)],
            )

            style.configure(
                "ChartTabSelected.TButton",
                background=DARK_PANEL,
                foreground=DARK_ACCENT,
                bordercolor=DARK_ACCENT2,
                padding=(10, 6),
            )
        except Exception:
            pass


        # Treeview (Current Trades table)
        try:
            style.configure(
                "Treeview",
                background=DARK_PANEL,
                fieldbackground=DARK_PANEL,
                foreground=DARK_FG,
                bordercolor=DARK_BORDER,
                lightcolor=DARK_BORDER,
                darkcolor=DARK_BORDER,
            )
            style.map(
                "Treeview",
                background=[("selected", DARK_SELECT_BG)],
                foreground=[("selected", DARK_SELECT_FG)],
            )

            style.configure("Treeview.Heading", background=DARK_BG2, foreground=DARK_ACCENT, relief="flat")
            style.map(
                "Treeview.Heading",
                background=[("active", DARK_PANEL2)],
                foreground=[("active", DARK_ACCENT2)],
            )
        except Exception:
            pass

        # Panedwindows / scrollbars
        try:
            style.configure("TPanedwindow", background=DARK_BG)
        except Exception:
            pass

        for sb in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
            try:
                style.configure(
                    sb,
                    background=DARK_BG2,
                    troughcolor=DARK_BG,
                    bordercolor=DARK_BORDER,
                    arrowcolor=DARK_ACCENT,
                )
            except Exception:
                pass

    # ---- settings ----

    def _load_settings(self) -> dict:
        settings_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), SETTINGS_FILE)
        data = _safe_read_json(settings_path)
        if not isinstance(data, dict):
            data = {}

        merged = dict(DEFAULT_SETTINGS)
        merged.update(data)

        # normalize coins
        merged["coins"] = [c.upper().strip() for c in merged.get("coins", [])]

        # normalize long-term holdings symbols (optional UI grouping)
        lth = merged.get("long_term_holdings", []) or []
        if isinstance(lth, str):
            lth = [x.strip() for x in lth.replace("\n", ",").split(",")]
        if not isinstance(lth, (list, tuple)):
            lth = []
        cleaned = []
        seen = set()
        for v in lth:
            sym = str(v).upper().strip()
            if not sym or sym in seen:
                continue
            seen.add(sym)
            cleaned.append(sym)
        merged["long_term_holdings"] = cleaned

        # normalize LTH profit allocation %
        try:
            p = float(str(merged.get("lth_profit_alloc_pct", DEFAULT_SETTINGS.get("lth_profit_alloc_pct", 0.0)) or 0.0).replace("%", "").strip())
        except Exception:
            p = float(DEFAULT_SETTINGS.get("lth_profit_alloc_pct", 0.0) or 0.0)
        if p < 0.0:
            p = 0.0
        if p > 100.0:
            p = 100.0
        merged["lth_profit_alloc_pct"] = p

        # ---- normalize chart timeframes + default timeframe (fixes new-user crash) ----
        tfs = merged.get("timeframes", None)
        if tfs is None:
            tfs = DEFAULT_SETTINGS.get("timeframes", [])

        if isinstance(tfs, str):
            tfs = [x.strip() for x in tfs.replace("\n", ",").split(",")]

        if not isinstance(tfs, (list, tuple)):
            tfs = []

        tf_clean = []
        tf_seen = set()
        for v in tfs:
            s = str(v).strip()
            if not s or s in tf_seen:
                continue
            tf_seen.add(s)
            tf_clean.append(s)

        if not tf_clean:
            tf_clean = list(DEFAULT_SETTINGS.get("timeframes", ["1hour"]))

        merged["timeframes"] = tf_clean

        dtf = str(merged.get("default_timeframe") or "").strip()
        if (not dtf) or (dtf not in merged["timeframes"]):
            dtf = merged["timeframes"][0] if merged["timeframes"] else "1hour"
        merged["default_timeframe"] = dtf

        # Best-effort: write back healed settings so the file becomes self-contained
        try:
            if data != merged:
                _safe_write_json(settings_path, merged)
        except Exception:
            pass

        return merged




    def _save_settings(self) -> None:
        settings_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), SETTINGS_FILE)
        _safe_write_json(settings_path, self.settings)


    def _settings_getter(self) -> dict:
        return self.settings

    def _ensure_alt_coin_folders_and_trainer_on_startup(self) -> None:
        """
        Startup behavior (mirrors Settings-save behavior):
        - For every alt coin in the coin list that does NOT have its folder yet:
            - create the folder
            - copy neural_trainer.py from the MAIN (BTC) folder into the new folder
        """
        try:
            coins = [str(c).strip().upper() for c in (self.settings.get("coins") or []) if str(c).strip()]
            main_dir = (self.settings.get("main_neural_dir") or self.project_dir or os.getcwd()).strip()

            trainer_name = os.path.basename(str(self.settings.get("script_neural_trainer", "neural_trainer.py")))

            # Source trainer: MAIN folder (BTC folder)
            src_main_trainer = os.path.join(main_dir, trainer_name)

            # Best-effort fallback if the main folder doesn't have it (keeps behavior robust)
            src_cfg_trainer = str(self.settings.get("script_neural_trainer", trainer_name))
            src_trainer_path = src_main_trainer if os.path.isfile(src_main_trainer) else src_cfg_trainer

            for coin in coins:
                if coin == "BTC":
                    continue  # BTC uses main folder; no per-coin folder needed

                coin_dir = os.path.join(main_dir, coin)

                created = False
                if not os.path.isdir(coin_dir):
                    os.makedirs(coin_dir, exist_ok=True)
                    created = True

                # Only copy into folders created at startup (per your request)
                if created:
                    dst_trainer_path = os.path.join(coin_dir, trainer_name)
                    if (not os.path.isfile(dst_trainer_path)) and os.path.isfile(src_trainer_path):
                        shutil.copy2(src_trainer_path, dst_trainer_path)
        except Exception:
            pass

    # ---- menu / layout ----


    def _build_menu(self) -> None:
        menubar = tk.Menu(
            self,
            bg=DARK_BG2,
            fg=DARK_FG,
            activebackground=DARK_SELECT_BG,
            activeforeground=DARK_SELECT_FG,
            bd=0,
            relief="flat",
        )

        m_scripts = tk.Menu(
            menubar,
            tearoff=0,
            bg=DARK_BG2,
            fg=DARK_FG,
            activebackground=DARK_SELECT_BG,
            activeforeground=DARK_SELECT_FG,
        )
        m_scripts.add_command(label="Start All", command=self.start_all_scripts)
        m_scripts.add_command(label="Stop All", command=self.stop_all_scripts)
        m_scripts.add_separator()
        m_scripts.add_command(label="Start Neural Runner", command=self.start_neural)
        m_scripts.add_command(label="Stop Neural Runner", command=self.stop_neural)
        m_scripts.add_separator()
        m_scripts.add_command(label="Start Trader", command=self.start_trader)
        m_scripts.add_command(label="Stop Trader", command=self.stop_trader)
        menubar.add_cascade(label="Scripts", menu=m_scripts)

        m_settings = tk.Menu(
            menubar,
            tearoff=0,
            bg=DARK_BG2,
            fg=DARK_FG,
            activebackground=DARK_SELECT_BG,
            activeforeground=DARK_SELECT_FG,
        )
        m_settings.add_command(label="Settings...", command=self.open_settings_dialog)
        menubar.add_cascade(label="Settings", menu=m_settings)

        m_file = tk.Menu(
            menubar,
            tearoff=0,
            bg=DARK_BG2,
            fg=DARK_FG,
            activebackground=DARK_SELECT_BG,
            activeforeground=DARK_SELECT_FG,
        )
        m_file.add_command(label="Exit", command=self._on_close)
        menubar.add_cascade(label="File", menu=m_file)

        self.config(menu=menubar)


    def _build_layout(self) -> None:
        outer = ttk.Panedwindow(self, orient="horizontal")
        outer.pack(fill="both", expand=True)

        # LEFT + RIGHT panes (fit to window height; scrolling handled only by inner widgets)
        left = ttk.Frame(outer)
        right = ttk.Frame(outer)

        outer.add(left, weight=1)
        outer.add(right, weight=2)

        # Prevent the outer (left/right) panes from being collapsible to 0 width
        try:
            outer.paneconfigure(left, minsize=360)
            outer.paneconfigure(right, minsize=520)
        except Exception:
            pass

        # No full-pane scrolling: just a padded inner container that always fits the pane height
        left_inner = ttk.Frame(left)
        left_inner.pack(fill="both", expand=True, padx=8, pady=8)

        right_inner = ttk.Frame(right)
        right_inner.pack(fill="both", expand=True, padx=8, pady=8)

        # LEFT: fixed top area (Account + Training + Controls) with tabs underneath
        left_side = ttk.Frame(left_inner)
        left_side.pack(fill="both", expand=True)

        # RIGHT: vertical split (Charts on top, Trades+History underneath)
        right_split = ttk.Panedwindow(right_inner, orient="vertical")
        right_split.pack(fill="both", expand=True)


        # Keep references so we can clamp sash positions later
        self._pw_outer = outer
        self._pw_left_split = None  # left no longer uses a Panedwindow (tabs instead)
        self._pw_right_split = right_split

        # Clamp panes when the user releases a sash or the window resizes
        outer.bind("<Configure>", lambda e: self._schedule_paned_clamp(self._pw_outer))
        outer.bind("<ButtonRelease-1>", lambda e: (
            setattr(self, "_user_moved_outer", True),
            self._schedule_paned_clamp(self._pw_outer),
        ))

        right_split.bind("<Configure>", lambda e: self._schedule_paned_clamp(self._pw_right_split))
        right_split.bind("<ButtonRelease-1>", lambda e: (
            setattr(self, "_user_moved_right_split", True),
            self._schedule_paned_clamp(self._pw_right_split),
        ))

        # Set a startup default width that matches the screenshot (so left has room for Neural Levels).
        def _init_outer_sash_once():
            try:
                if getattr(self, "_did_init_outer_sash", False):
                    return

                # If the user already moved it, never override it.
                if getattr(self, "_user_moved_outer", False):
                    self._did_init_outer_sash = True
                    return

                total = outer.winfo_width()
                if total <= 2:
                    self.after(10, _init_outer_sash_once)
                    return

                min_left = 360
                min_right = 520
                desired_left = 470  # ~matches your screenshot
                target = max(min_left, min(total - min_right, desired_left))
                outer.sashpos(0, int(target))

                self._did_init_outer_sash = True
            except Exception:
                pass

        self.after_idle(_init_outer_sash_once)

        # Global safety: on some themes/platforms, the mouse events land on the sash element,
        # not the panedwindow widget, so the widget-level binds won't always fire.
        self.bind_all("<ButtonRelease-1>", lambda e: (
            self._schedule_paned_clamp(getattr(self, "_pw_outer", None)),
            self._schedule_paned_clamp(getattr(self, "_pw_right_split", None)),
            self._schedule_paned_clamp(getattr(self, "_pw_right_bottom_split", None)),
        ))



        # ----------------------------
        # LEFT: TOP (Account + Training + Controls) — stays ABOVE the tab area
        # ----------------------------
        top_controls = ttk.LabelFrame(left_side, text="Controls / Health")
        top_controls.pack(fill="x", expand=False)

        # Layout requirement:
        #   - Buttons (full width) ABOVE
        #   - Dual section BELOW:
        #       LEFT  = Status + Account + Profit
        #       RIGHT = Training
        buttons_bar = ttk.Frame(top_controls)
        buttons_bar.pack(fill="x", expand=False, padx=0, pady=0)

        info_row = ttk.Frame(top_controls)
        info_row.pack(fill="x", expand=False, padx=0, pady=0)

        # LEFT column (status + account/profit)
        controls_left = ttk.Frame(info_row)
        controls_left.pack(side="left", fill="both", expand=True)

        # RIGHT column (training)
        training_section = ttk.LabelFrame(info_row, text="Training")
        training_section.pack(side="right", fill="both", expand=False, padx=6, pady=6)

        training_left = ttk.Frame(training_section)
        training_left.pack(side="left", fill="both", expand=True)

        # Train coin selector (so you can choose what "Train Selected" targets)
        train_row = ttk.Frame(training_left)
        train_row.pack(fill="x", padx=6, pady=(6, 0))

        self.train_coin_var = tk.StringVar(value=(self.coins[0] if self.coins else ""))
        ttk.Label(train_row, text="Train coin:").pack(side="left")
        self.train_coin_combo = ttk.Combobox(
            train_row,
            textvariable=self.train_coin_var,
            values=self.coins,
            width=8,
            state="readonly",
        )
        self.train_coin_combo.pack(side="left", padx=(6, 0))

        def _sync_train_coin(*_):
            try:
                # keep the Trainers tab dropdown in sync (if present)
                self.trainer_coin_var.set(self.train_coin_var.get())
            except Exception:
                pass

        self.train_coin_combo.bind("<<ComboboxSelected>>", _sync_train_coin)
        _sync_train_coin()



        # Fixed controls bar (stable layout; no wrapping/reflow on resize)
        # Wrapped in a scrollable canvas so buttons are never cut off when the window is resized.
        btn_scroll_wrap = ttk.Frame(buttons_bar)
        btn_scroll_wrap.pack(fill="x", expand=False, padx=6, pady=6)

        btn_canvas = tk.Canvas(btn_scroll_wrap, bg=DARK_BG, highlightthickness=0, bd=0, height=1)
        btn_scroll_y = ttk.Scrollbar(btn_scroll_wrap, orient="vertical", command=btn_canvas.yview)
        btn_scroll_x = ttk.Scrollbar(btn_scroll_wrap, orient="horizontal", command=btn_canvas.xview)
        btn_canvas.configure(yscrollcommand=btn_scroll_y.set, xscrollcommand=btn_scroll_x.set)


        btn_scroll_wrap.grid_columnconfigure(0, weight=1)
        btn_scroll_wrap.grid_rowconfigure(0, weight=0)

        btn_canvas.grid(row=0, column=0, sticky="ew")
        btn_scroll_y.grid(row=0, column=1, sticky="ns")
        btn_scroll_x.grid(row=1, column=0, sticky="ew")


        # Start hidden; we only show scrollbars when needed.
        btn_scroll_y.grid_remove()
        btn_scroll_x.grid_remove()

        btn_inner = ttk.Frame(btn_canvas)
        _btn_inner_id = btn_canvas.create_window((0, 0), window=btn_inner, anchor="nw")

        def _btn_update_scrollbars(event=None):
            try:
                # Always keep scrollregion accurate
                btn_canvas.configure(scrollregion=btn_canvas.bbox("all"))
                sr = btn_canvas.bbox("all")
                if not sr:
                    return

                # --- KEY FIX ---
                # Resize the canvas height to the buttons' requested height so there is no
                # dead/empty gap above the horizontal scrollbar.
                try:
                    desired_h = max(1, int(btn_inner.winfo_reqheight()))
                    cur_h = int(btn_canvas.cget("height") or 0)
                    if cur_h != desired_h:
                        btn_canvas.configure(height=desired_h)
                except Exception:
                    pass

                x0, y0, x1, y1 = sr
                cw = btn_canvas.winfo_width()
                ch = btn_canvas.winfo_height()

                need_x = (x1 - x0) > (cw + 1)
                need_y = (y1 - y0) > (ch + 1)

                if need_x:
                    btn_scroll_x.grid()
                else:
                    btn_scroll_x.grid_remove()
                    btn_canvas.xview_moveto(0)

                if need_y:
                    btn_scroll_y.grid()
                else:
                    btn_scroll_y.grid_remove()
                    btn_canvas.yview_moveto(0)
            except Exception:
                pass


        def _btn_canvas_on_configure(event=None):
            try:
                # Keep the inner window pinned to top-left
                btn_canvas.coords(_btn_inner_id, 0, 0)
            except Exception:
                pass
            _btn_update_scrollbars()

        btn_inner.bind("<Configure>", _btn_update_scrollbars)
        btn_canvas.bind("<Configure>", _btn_canvas_on_configure)

        # The original button layout (unchanged), placed inside the scrollable inner frame.
        btn_bar = ttk.Frame(btn_inner)
        btn_bar.pack(fill="x", expand=False)

        # Keep groups left-aligned; the spacer column absorbs extra width.
        btn_bar.grid_columnconfigure(0, weight=0)
        btn_bar.grid_columnconfigure(1, weight=0)
        btn_bar.grid_columnconfigure(2, weight=1)

        BTN_W = 14

        # (Start All button moved into the left-side info section above Account.)
        train_group = ttk.Frame(btn_bar)
        train_group.grid(row=0, column=0, sticky="w", padx=(0, 18), pady=(0, 6))


        # One more pass after layout so scrollbars reflect the true initial size.
        self.after_idle(_btn_update_scrollbars)






        self.lbl_neural = ttk.Label(controls_left, text="Neural: stopped")
        self.lbl_neural.pack(anchor="w", padx=6, pady=(0, 2))

        self.lbl_trader = ttk.Label(controls_left, text="Trader: stopped")
        self.lbl_trader.pack(anchor="w", padx=6, pady=(0, 6))

        self.lbl_last_status = ttk.Label(controls_left, text="Last status: N/A")
        self.lbl_last_status.pack(anchor="w", padx=6, pady=(0, 2))


        # ----------------------------
        # Training section (everything training-specific lives here)
        # ----------------------------
        train_buttons_row = ttk.Frame(training_left)
        train_buttons_row.pack(fill="x", padx=6, pady=(6, 6))

        ttk.Button(train_buttons_row, text="Train Selected", width=BTN_W, command=self.train_selected_coin).pack(anchor="w", pady=(0, 6))
        ttk.Button(train_buttons_row, text="Train All", width=BTN_W, command=self.train_all_coins).pack(anchor="w")

        # Training status (per-coin + gating reason)
        self.lbl_training_overview = ttk.Label(training_left, text="Training: N/A")
        self.lbl_training_overview.pack(anchor="w", padx=6, pady=(0, 2))

        self.lbl_flow_hint = ttk.Label(training_left, text="Flow: Train → Start All")
        self.lbl_flow_hint.pack(anchor="w", padx=6, pady=(0, 6))

        self.training_list = tk.Listbox(
            training_left,
            height=5,
            bg=DARK_PANEL,
            fg=DARK_FG,
            selectbackground=DARK_SELECT_BG,
            selectforeground=DARK_SELECT_FG,
            highlightbackground=DARK_BORDER,
            highlightcolor=DARK_ACCENT,
            activestyle="none",
        )
        self.training_list.pack(fill="both", expand=True, padx=6, pady=(0, 6))


        # Start All (moved here: LEFT side of the dual section, directly above Account)
        start_all_row = ttk.Frame(controls_left)
        start_all_row.pack(fill="x", padx=6, pady=(0, 6))

        self.btn_toggle_all = ttk.Button(
            start_all_row,
            text="Start All",
            width=BTN_W,
            command=self.toggle_all_scripts,
        )
        self.btn_toggle_all.pack(side="left")


        # Account info (LEFT column, under status)
        acct_box = ttk.LabelFrame(controls_left, text="Account")
        acct_box.pack(fill="x", padx=6, pady=6)


        self.lbl_acct_total_value = ttk.Label(acct_box, text="Total Account Value: N/A")
        self.lbl_acct_total_value.pack(anchor="w", padx=6, pady=(2, 0))

        self.lbl_acct_holdings_value = ttk.Label(acct_box, text="Holdings Value: N/A")
        self.lbl_acct_holdings_value.pack(anchor="w", padx=6, pady=(2, 0))

        self.lbl_acct_buying_power = ttk.Label(acct_box, text="Buying Power: N/A")
        self.lbl_acct_buying_power.pack(anchor="w", padx=6, pady=(2, 0))

        self.lbl_acct_percent_in_trade = ttk.Label(acct_box, text="Percent In Trade: N/A")
        self.lbl_acct_percent_in_trade.pack(anchor="w", padx=6, pady=(2, 0))

        # DCA affordability
        self.lbl_acct_dca_spread = ttk.Label(acct_box, text="DCA Levels (spread): N/A")
        self.lbl_acct_dca_spread.pack(anchor="w", padx=6, pady=(2, 0))

        self.lbl_acct_dca_single = ttk.Label(acct_box, text="DCA Levels (single): N/A")
        self.lbl_acct_dca_single.pack(anchor="w", padx=6, pady=(2, 0))

        self.lbl_pnl = ttk.Label(acct_box, text="Total realized: N/A")
        self.lbl_pnl.pack(anchor="w", padx=6, pady=(2, 0))

        self.lbl_lth_profit_bucket = ttk.Label(acct_box, text="LTH profit bucket: N/A")
        self.lbl_lth_profit_bucket.pack(anchor="w", padx=6, pady=(2, 2))



        # ----------------------------
        # LEFT: Tabs (Long/Short things + Live Output)
        # ----------------------------
        left_tabs_frame = ttk.Frame(left_side)
        left_tabs_frame.pack(fill="both", expand=True, padx=0, pady=(6, 0))

        self.left_nb = ttk.Notebook(left_tabs_frame)
        self.left_nb.pack(fill="both", expand=True, padx=0, pady=0)

        # ----------------------------
        # TAB 1: Long/Short things (Neural Levels)
        # ----------------------------
        longshort_tab = ttk.Frame(self.left_nb)
        self.left_nb.add(longshort_tab, text="Long/Short")

        neural_box = ttk.LabelFrame(longshort_tab, text="Neural Levels (0–7)")
        neural_box.pack(fill="both", expand=True, padx=6, pady=6)

        legend = ttk.Frame(neural_box)
        legend.pack(fill="x", padx=6, pady=(4, 0))

        ttk.Label(legend, text="Level bars: 0 = bottom, 7 = top").pack(side="left")
        ttk.Label(legend, text="   ").pack(side="left")
        ttk.Label(legend, text="Blue = Long").pack(side="left")
        ttk.Label(legend, text="  ").pack(side="left")
        ttk.Label(legend, text="Orange = Short").pack(side="left")

        self.lbl_neural_overview_last = ttk.Label(legend, text="Last: N/A")
        self.lbl_neural_overview_last.pack(side="right")

        # Scrollable area for tiles (auto-hides the scrollbar if everything fits)
        neural_viewport = ttk.Frame(neural_box)
        neural_viewport.pack(fill="both", expand=True, padx=6, pady=(4, 6))
        neural_viewport.grid_rowconfigure(0, weight=1)
        neural_viewport.grid_columnconfigure(0, weight=1)

        self._neural_overview_canvas = tk.Canvas(
            neural_viewport,
            bg=DARK_PANEL2,
            highlightthickness=1,
            highlightbackground=DARK_BORDER,
            bd=0,
        )
        self._neural_overview_canvas.grid(row=0, column=0, sticky="nsew")

        self._neural_overview_scroll = ttk.Scrollbar(
            neural_viewport,
            orient="vertical",
            command=self._neural_overview_canvas.yview,
        )
        self._neural_overview_scroll.grid(row=0, column=1, sticky="ns")

        self._neural_overview_canvas.configure(yscrollcommand=self._neural_overview_scroll.set)

        self.neural_wrap = WrapFrame(self._neural_overview_canvas)
        self._neural_overview_window = self._neural_overview_canvas.create_window(
            (0, 0),
            window=self.neural_wrap,
            anchor="nw",
        )

        def _update_neural_overview_scrollbars(event=None) -> None:
            """Update scrollregion + hide/show the scrollbar depending on overflow."""
            try:
                c = self._neural_overview_canvas
                win = self._neural_overview_window

                c.update_idletasks()
                bbox = c.bbox(win)
                if not bbox:
                    self._neural_overview_scroll.grid_remove()
                    return

                c.configure(scrollregion=bbox)
                content_h = int(bbox[3] - bbox[1])
                view_h = int(c.winfo_height())

                if content_h > (view_h + 1):
                    self._neural_overview_scroll.grid()
                else:
                    self._neural_overview_scroll.grid_remove()
                    try:
                        c.yview_moveto(0)
                    except Exception:
                        pass
            except Exception:
                pass

        def _on_neural_canvas_configure(e) -> None:
            # Keep the inner wrap frame exactly the canvas width so wrapping is correct.
            try:
                self._neural_overview_canvas.itemconfigure(self._neural_overview_window, width=int(e.width))
            except Exception:
                pass
            _update_neural_overview_scrollbars()

        self._neural_overview_canvas.bind("<Configure>", _on_neural_canvas_configure, add="+")
        self.neural_wrap.bind("<Configure>", _update_neural_overview_scrollbars, add="+")
        self._update_neural_overview_scrollbars = _update_neural_overview_scrollbars

        # Mousewheel scroll inside the tiles area
        def _wheel(e):
            try:
                if self._neural_overview_scroll.winfo_ismapped():
                    self._neural_overview_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
            except Exception:
                pass

        self._neural_overview_canvas.bind("<Enter>", lambda _e: self._neural_overview_canvas.focus_set(), add="+")
        self._neural_overview_canvas.bind("<MouseWheel>", _wheel, add="+")

        # tiles by coin
        self.neural_tiles: Dict[str, NeuralSignalTile] = {}
        # small cache: path -> (mtime, value)
        self._neural_overview_cache: Dict[str, Tuple[float, Any]] = {}

        self._rebuild_neural_overview()
        try:
            self.after_idle(self._update_neural_overview_scrollbars)
        except Exception:
            pass



        # ----------------------------
        # TAB 2: Live Output (unchanged live log content)
        # ----------------------------
        logs_tab = ttk.Frame(self.left_nb)
        self.left_nb.add(logs_tab, text="Live Output")

        # Half-size fixed-width font for live logs (Runner/Trader/Trainers)
        _base = tkfont.nametofont("TkFixedFont")
        _half = max(6, int(round(abs(int(_base.cget("size"))) / 2.0)))
        self._live_log_font = _base.copy()
        self._live_log_font.configure(size=_half)

        logs_frame = ttk.LabelFrame(logs_tab, text="Live Output")
        logs_frame.pack(fill="both", expand=True, padx=6, pady=6)

        self.logs_nb = ttk.Notebook(logs_frame)
        self.logs_nb.pack(fill="both", expand=True, padx=6, pady=6)


        # Runner tab
        runner_tab = ttk.Frame(self.logs_nb)
        self.logs_nb.add(runner_tab, text="Runner")
        self.runner_text = tk.Text(
            runner_tab,
            height=8,
            wrap="none",
            font=self._live_log_font,
            bg=DARK_PANEL,
            fg=DARK_FG,
            insertbackground=DARK_FG,
            selectbackground=DARK_SELECT_BG,
            selectforeground=DARK_SELECT_FG,
            highlightbackground=DARK_BORDER,
            highlightcolor=DARK_ACCENT,
        )

        runner_scroll = ttk.Scrollbar(runner_tab, orient="vertical", command=self.runner_text.yview)
        self.runner_text.configure(yscrollcommand=runner_scroll.set)
        self.runner_text.pack(side="left", fill="both", expand=True)
        runner_scroll.pack(side="right", fill="y")

        # Trader tab
        trader_tab = ttk.Frame(self.logs_nb)
        self.logs_nb.add(trader_tab, text="Trader")
        self.trader_text = tk.Text(
            trader_tab,
            height=8,
            wrap="none",
            font=self._live_log_font,
            bg=DARK_PANEL,
            fg=DARK_FG,
            insertbackground=DARK_FG,
            selectbackground=DARK_SELECT_BG,
            selectforeground=DARK_SELECT_FG,
            highlightbackground=DARK_BORDER,
            highlightcolor=DARK_ACCENT,
        )

        trader_scroll = ttk.Scrollbar(trader_tab, orient="vertical", command=self.trader_text.yview)
        self.trader_text.configure(yscrollcommand=trader_scroll.set)
        self.trader_text.pack(side="left", fill="both", expand=True)
        trader_scroll.pack(side="right", fill="y")

        # Trainers tab (multi-coin)
        trainer_tab = ttk.Frame(self.logs_nb)
        self.logs_nb.add(trainer_tab, text="Trainers")

        top_bar = ttk.Frame(trainer_tab)
        top_bar.pack(fill="x", padx=6, pady=6)

        self.trainer_coin_var = tk.StringVar(value=(self.coins[0] if self.coins else "BTC"))
        ttk.Label(top_bar, text="Coin:").pack(side="left")
        self.trainer_coin_combo = ttk.Combobox(
            top_bar,
            textvariable=self.trainer_coin_var,
            values=self.coins,
            state="readonly",
            width=8
        )
        self.trainer_coin_combo.pack(side="left", padx=(6, 12))

        ttk.Button(top_bar, text="Start Trainer", command=self.start_trainer_for_selected_coin).pack(side="left")
        ttk.Button(top_bar, text="Stop Trainer", command=self.stop_trainer_for_selected_coin).pack(side="left", padx=(6, 0))

        self.trainer_status_lbl = ttk.Label(top_bar, text="(no trainers running)")
        self.trainer_status_lbl.pack(side="left", padx=(12, 0))

        self.trainer_text = tk.Text(
            trainer_tab,
            height=8,
            wrap="none",
            font=self._live_log_font,
            bg=DARK_PANEL,
            fg=DARK_FG,
            insertbackground=DARK_FG,
            selectbackground=DARK_SELECT_BG,
            selectforeground=DARK_SELECT_FG,
            highlightbackground=DARK_BORDER,
            highlightcolor=DARK_ACCENT,
        )

        trainer_scroll = ttk.Scrollbar(trainer_tab, orient="vertical", command=self.trainer_text.yview)
        self.trainer_text.configure(yscrollcommand=trainer_scroll.set)
        self.trainer_text.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=(0, 6))
        trainer_scroll.pack(side="right", fill="y", padx=(0, 6), pady=(0, 6))






        # ----------------------------
        # RIGHT TOP: Charts (tabs)
        # ----------------------------
        charts_frame = ttk.LabelFrame(right_split, text="Charts (Neural lines overlaid)")
        self._charts_frame = charts_frame

        # Multi-row "tabs" (WrapFrame)
        self.chart_tabs_bar = WrapFrame(charts_frame)
        # Keep left padding, remove right padding so tabs can reach the edge
        self.chart_tabs_bar.pack(fill="x", padx=(6, 0), pady=(6, 0))

        # Page container (no ttk.Notebook, so there are NO native tabs to show)
        self.chart_pages_container = ttk.Frame(charts_frame)
        # Keep left padding, remove right padding so charts fill to the edge
        self.chart_pages_container.pack(fill="both", expand=True, padx=(6, 0), pady=(0, 6))


        self._chart_tab_buttons: Dict[str, ttk.Button] = {}
        self.chart_pages: Dict[str, ttk.Frame] = {}
        self._current_chart_page: str = "ACCOUNT"

        def _show_page(name: str) -> None:
            self._current_chart_page = name
            # hide all pages
            for f in self.chart_pages.values():
                try:
                    f.pack_forget()
                except Exception:
                    pass
            # show selected
            f = self.chart_pages.get(name)
            if f is not None:
                f.pack(fill="both", expand=True)

            # style selected tab
            for txt, b in self._chart_tab_buttons.items():
                try:
                    b.configure(style=("ChartTabSelected.TButton" if txt == name else "ChartTab.TButton"))
                except Exception:
                    pass

            # Immediately refresh the newly shown coin chart so candles appear right away
            # (even if trader/neural scripts are not running yet).
            try:
                tab = str(name or "").strip().upper()
                if tab and tab != "ACCOUNT":
                    coin = tab
                    chart = self.charts.get(coin)
                    if chart:
                        def _do_refresh_visible():
                            try:
                                # Ensure chart coin folders exist (best-effort; fast)
                                try:
                                    chart_coins = list(getattr(self, "chart_coins", []) or [])
                                    cf_sig = (self.settings.get("main_neural_dir"), tuple(chart_coins))
                                    if getattr(self, "_chart_coin_folders_sig", None) != cf_sig:
                                        self._chart_coin_folders_sig = cf_sig
                                        self.chart_coin_folders = build_coin_folders(self.settings["main_neural_dir"], chart_coins)
                                except Exception:
                                    pass

                                pos = self._last_positions.get(coin, {}) if isinstance(self._last_positions, dict) else {}
                                buy_px = pos.get("current_buy_price", None)
                                sell_px = pos.get("current_sell_price", None)
                                trail_line = pos.get("trail_line", None)
                                dca_line_price = pos.get("dca_line_price", None)
                                avg_cost_basis = pos.get("avg_cost_basis", None)

                                chart.refresh(
                                    getattr(self, "chart_coin_folders", self.coin_folders),
                                    current_buy_price=buy_px,
                                    current_sell_price=sell_px,
                                    trail_line=trail_line,
                                    dca_line_price=dca_line_price,
                                    avg_cost_basis=avg_cost_basis,
                                )


                            except Exception:
                                pass

                        self.after(1, _do_refresh_visible)
            except Exception:
                pass


        self._show_chart_page = _show_page  # used by _rebuild_coin_chart_tabs()

        # ACCOUNT page
        acct_page = ttk.Frame(self.chart_pages_container)
        self.chart_pages["ACCOUNT"] = acct_page

        acct_btn = ttk.Button(
            self.chart_tabs_bar,
            text="ACCOUNT",
            style="ChartTab.TButton",
            command=lambda: self._show_chart_page("ACCOUNT"),
        )
        self.chart_tabs_bar.add(acct_btn, padx=(0, 6), pady=(0, 6))
        self._chart_tab_buttons["ACCOUNT"] = acct_btn

        self.account_chart = AccountValueChart(
            acct_page,
            self.account_value_history_path,
            self.trade_history_path,
        )
        self.account_chart.pack(fill="both", expand=True)

        # Coin pages
        self.charts: Dict[str, CandleChart] = {}
        for coin in (getattr(self, "chart_coins", None) or self.coins):
            page = ttk.Frame(self.chart_pages_container)
            self.chart_pages[coin] = page

            btn = ttk.Button(
                self.chart_tabs_bar,
                text=coin,
                style="ChartTab.TButton",
                command=lambda c=coin: self._show_chart_page(c),
            )
            self.chart_tabs_bar.add(btn, padx=(0, 6), pady=(0, 6))
            self._chart_tab_buttons[coin] = btn

            chart = CandleChart(page, self.fetcher, coin, self._settings_getter, self.trade_history_path)
            chart.pack(fill="both", expand=True)
            self.charts[coin] = chart


        # show initial page
        self._show_chart_page("ACCOUNT")





        # ----------------------------
        # RIGHT BOTTOM: Current Trades + Long-term Holdings + Trade History (tabbed)
        # ----------------------------
        # Instead of stacking 3 panes vertically (which forces everything to get squished on smaller heights),
        # put them in a Notebook so only one section is visible at a time. This guarantees the chart area
        # always has enough vertical room, and nothing overlaps.
        right_bottom_tabs = ttk.Notebook(right_split)
        self._right_bottom_tabs = right_bottom_tabs
        self._pw_right_bottom_split = None  # no longer used (kept for clamp calls elsewhere)

        # ----------------------------
        # TAB 1: Current Trades
        # ----------------------------
        trades_tab = ttk.Frame(right_bottom_tabs)
        right_bottom_tabs.add(trades_tab, text="Current Trades")

        trades_frame = ttk.LabelFrame(trades_tab, text="Current Trades")
        trades_frame.pack(fill="both", expand=True, padx=6, pady=6)

        cols = (
            "coin",
            "qty",
            "value",          # <-- right after qty
            "avg_cost",
            "buy_price",
            "buy_pnl",
            "sell_price",
            "sell_pnl",
            "dca_stages",
            "dca_24h",
            "next_dca",
            "trail_line",     # keep trail line column
        )

        header_labels = {
            "coin": "Coin",
            "qty": "Qty",
            "value": "Value",
            "avg_cost": "Avg Cost",
            "buy_price": "Ask Price",
            "buy_pnl": "DCA PnL",
            "sell_price": "Bid Price",
            "sell_pnl": "Sell PnL",
            "dca_stages": "DCA Stage",
            "dca_24h": "DCA 24h",
            "next_dca": "Next DCA",
            "trail_line": "Trail Line",
        }

        trades_table_wrap = ttk.Frame(trades_frame)
        trades_table_wrap.pack(fill="both", expand=True, padx=6, pady=6)

        self.trades_tree = ttk.Treeview(
            trades_table_wrap,
            columns=cols,
            show="headings",
            height=10
        )
        for c in cols:
            self.trades_tree.heading(c, text=header_labels.get(c, c))
            self.trades_tree.column(c, width=110, anchor="center", stretch=True)

        # Reasonable starting widths (they will be dynamically scaled on resize)
        self.trades_tree.column("coin", width=70)
        self.trades_tree.column("qty", width=95)
        self.trades_tree.column("value", width=110)
        self.trades_tree.column("next_dca", width=160)
        self.trades_tree.column("dca_stages", width=90)
        self.trades_tree.column("dca_24h", width=80)

        ysb = ttk.Scrollbar(trades_table_wrap, orient="vertical", command=self.trades_tree.yview)
        xsb = ttk.Scrollbar(trades_table_wrap, orient="horizontal", command=self.trades_tree.xview)
        self.trades_tree.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)

        self.trades_tree.pack(side="top", fill="both", expand=True)
        xsb.pack(side="bottom", fill="x")
        ysb.pack(side="right", fill="y")

        def _resize_trades_columns(*_):
            # Scale the initial column widths proportionally so the table always fits the current window.
            try:
                total_w = int(self.trades_tree.winfo_width())
            except Exception:
                return
            if total_w <= 1:
                return

            try:
                sb_w = int(ysb.winfo_width() or 0)
            except Exception:
                sb_w = 0

            avail = max(200, total_w - sb_w - 8)

            base = {
                "coin": 70,
                "qty": 95,
                "value": 110,
                "avg_cost": 110,
                "buy_price": 110,
                "buy_pnl": 110,
                "sell_price": 110,
                "sell_pnl": 110,
                "dca_stages": 90,
                "dca_24h": 80,
                "next_dca": 160,
                "trail_line": 120,
            }

            total_base = sum(base.values()) or 1
            scale = avail / float(total_base)

            for c, bw in base.items():
                try:
                    self.trades_tree.column(c, width=max(60, int(bw * scale)))
                except Exception:
                    pass

        self.trades_tree.bind("<Configure>", _resize_trades_columns, add="+")

        # ----------------------------
        # TAB 2: Long-term Holdings
        # ----------------------------
        lth_tab = ttk.Frame(right_bottom_tabs)
        right_bottom_tabs.add(lth_tab, text="Long-term Holdings")

        lth_frame = ttk.LabelFrame(lth_tab, text="Long-term Holdings (ignored by trader)")
        lth_frame.pack(fill="both", expand=True, padx=6, pady=6)

        lth_wrap = ttk.Frame(lth_frame)
        lth_wrap.pack(fill="both", expand=True, padx=6, pady=6)

        self.lth_tree = ttk.Treeview(
            lth_wrap,
            columns=("coin", "qty", "value"),
            show="headings",
            height=6,
        )
        self.lth_tree.heading("coin", text="Coin")
        self.lth_tree.heading("qty", text="LTH Qty")
        self.lth_tree.heading("value", text="Value")

        self.lth_tree.column("coin", width=90, anchor="center")
        self.lth_tree.column("qty", width=140, anchor="center")
        self.lth_tree.column("value", width=140, anchor="center")

        ysb_lth = ttk.Scrollbar(lth_wrap, orient="vertical", command=self.lth_tree.yview)
        xsb_lth = ttk.Scrollbar(lth_wrap, orient="horizontal", command=self.lth_tree.xview)
        self.lth_tree.configure(yscrollcommand=ysb_lth.set, xscrollcommand=xsb_lth.set)

        self.lth_tree.pack(side="left", fill="both", expand=True)
        ysb_lth.pack(side="right", fill="y")
        xsb_lth.pack(side="bottom", fill="x")

        # ----------------------------
        # TAB 3: Trade History
        # ----------------------------
        hist_tab = ttk.Frame(right_bottom_tabs)
        right_bottom_tabs.add(hist_tab, text="Trade History")

        hist_frame = ttk.LabelFrame(hist_tab, text="Trade History (scroll)")
        hist_frame.pack(fill="both", expand=True, padx=6, pady=6)

        hist_wrap = ttk.Frame(hist_frame)
        hist_wrap.pack(fill="both", expand=True, padx=6, pady=6)

        self.hist_list = tk.Text(
            hist_wrap,
            height=8,
            wrap="none",
            font=self._live_log_font,
            bg=DARK_PANEL,
            fg=DARK_FG,
            insertbackground=DARK_FG,
            selectbackground=DARK_SELECT_BG,
            selectforeground=DARK_SELECT_FG,
            highlightbackground=DARK_BORDER,
            highlightcolor=DARK_ACCENT,
        )
        ysb2 = ttk.Scrollbar(hist_wrap, orient="vertical", command=self.hist_list.yview)
        xsb2 = ttk.Scrollbar(hist_wrap, orient="horizontal", command=self.hist_list.xview)
        self.hist_list.configure(yscrollcommand=ysb2.set, xscrollcommand=xsb2.set)

        self.hist_list.pack(side="left", fill="both", expand=True)
        ysb2.pack(side="right", fill="y")
        xsb2.pack(side="bottom", fill="x")

        # Assemble right side
        right_split.add(charts_frame, weight=4)
        right_split.add(right_bottom_tabs, weight=2)


        try:
            # Give charts more guaranteed height so candles + labels never get squished.
            right_split.paneconfigure(charts_frame, minsize=420)
            right_split.paneconfigure(right_bottom_tabs, minsize=220)
        except Exception:
            pass

        # Startup defaults (never override if user already dragged).
        def _init_right_split_sash_once():
            try:
                if getattr(self, "_did_init_right_split_sash", False):
                    return

                if getattr(self, "_user_moved_right_split", False):
                    self._did_init_right_split_sash = True
                    return

                try:
                    right_split.update_idletasks()
                except Exception:
                    pass

                total = right_split.winfo_height()
                if total <= 2:
                    self.after(10, _init_right_split_sash_once)
                    return

                min_top = 420
                min_bottom = 220

                # Default: 66.6% charts, 33.4% bottom tabs
                target = int(round(total * (2.0 / 3.0)))
                target = max(min_top, min(total - min_bottom, target))

                right_split.sashpos(0, int(target))
                self._did_init_right_split_sash = True
            except Exception:
                pass

        self.after(50, _init_right_split_sash_once)




        # Initial clamp once everything is laid out
        self.after_idle(lambda: (
            self._schedule_paned_clamp(getattr(self, "_pw_outer", None)),
            self._schedule_paned_clamp(getattr(self, "_pw_left_split", None)),
            self._schedule_paned_clamp(getattr(self, "_pw_right_split", None)),
        ))



        # status bar
        self.status = ttk.Label(self, text="Ready", anchor="w")
        self.status.pack(fill="x", side="bottom")



    # ---- panedwindow anti-collapse helpers ----

    def _schedule_paned_clamp(self, pw: ttk.Panedwindow) -> None:
        """
        Debounced clamp so we don't fight the geometry manager mid-resize.

        IMPORTANT: use `after(1, ...)` instead of `after_idle(...)` so it still runs
        while the mouse is held during sash dragging (Tk often doesn't go "idle"
        until after the drag ends, which is exactly when panes can vanish).
        """
        try:
            if not pw or not int(pw.winfo_exists()):
                return
        except Exception:
            return

        key = str(pw)
        if key in self._paned_clamp_after_ids:
            return

        def _run():
            try:
                self._paned_clamp_after_ids.pop(key, None)
            except Exception:
                pass
            self._clamp_panedwindow_sashes(pw)

        try:
            self._paned_clamp_after_ids[key] = self.after(1, _run)
        except Exception:
            pass


    def _clamp_panedwindow_sashes(self, pw: ttk.Panedwindow) -> None:
        """
        Enforces each pane's configured 'minsize' by clamping sash positions.

        NOTE:
        ttk.Panedwindow.paneconfigure(pane) typically returns dict values like:
            {"minsize": ("minsize", "minsize", "Minsize", "140"), ...}
        so we MUST pull the last element when it's a tuple/list.
        """
        try:
            if not pw or not int(pw.winfo_exists()):
                return

            panes = list(pw.panes())
            if len(panes) < 2:
                return

            orient = str(pw.cget("orient"))
            total = pw.winfo_height() if orient == "vertical" else pw.winfo_width()
            if total <= 2:
                return

            def _get_minsize(pane_id) -> int:
                try:
                    cfg = pw.paneconfigure(pane_id)
                    ms = cfg.get("minsize", 0)

                    # ttk returns tuples like ('minsize','minsize','Minsize','140')
                    if isinstance(ms, (tuple, list)) and ms:
                        ms = ms[-1]

                    # sometimes it's already int/float-like, sometimes it's a string
                    return max(0, int(float(ms)))
                except Exception:
                    return 0

            mins: List[int] = [_get_minsize(p) for p in panes]

            # If total space is smaller than sum(mins), we still clamp as best-effort
            # by scaling mins down proportionally but never letting a pane hit 0.
            if sum(mins) >= total:
                # best-effort: keep every pane at least 24px so it can’t disappear
                floor = 24
                mins = [max(floor, m) for m in mins]

                # if even floors don't fit, just stop here (window minsize should prevent this)
                if sum(mins) >= total:
                    return

            # Two-pass clamp so constraints settle even with multiple sashes
            for _ in range(2):
                for i in range(len(panes) - 1):
                    min_pos = sum(mins[: i + 1])
                    max_pos = total - sum(mins[i + 1 :])

                    try:
                        cur = int(pw.sashpos(i))
                    except Exception:
                        continue

                    new = max(min_pos, min(max_pos, cur))
                    if new != cur:
                        try:
                            pw.sashpos(i, new)
                        except Exception:
                            pass


        except Exception:
            pass



    # ---- process control ----


    def _reader_thread(self, proc: subprocess.Popen, q: "queue.Queue[str]", prefix: str) -> None:
        def _push(msg: str) -> None:
            # Never let log spam block the reader or the GUI.
            # If the queue is full, drop the oldest line and keep the newest.
            while True:
                try:
                    q.put_nowait(msg)
                    return
                except queue.Full:
                    try:
                        q.get_nowait()
                    except Exception:
                        return
                except Exception:
                    return

        try:
            # line-buffered text mode
            while True:
                line = proc.stdout.readline() if proc.stdout else ""
                if not line:
                    if proc.poll() is not None:
                        break
                    time.sleep(0.05)
                    continue
                _push(f"{prefix}{line.rstrip()}")
        except Exception:
            pass
        finally:
            _push(f"{prefix}[process exited]")

    def _start_process(self, p: ProcInfo, log_q: Optional["queue.Queue[str]"] = None, prefix: str = "") -> None:
        if p.proc and p.proc.poll() is None:
            return
        if not os.path.isfile(p.path):
            messagebox.showerror("Missing script", f"Cannot find: {p.path}")
            return

        env = os.environ.copy()
        env["POWERTRADER_HUB_DIR"] = self.hub_dir  # so rhcb writes where GUI reads

        try:
            p.proc = subprocess.Popen(
                [sys.executable, "-u", p.path],  # -u for unbuffered prints
                cwd=self.project_dir,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            if log_q is not None:
                t = threading.Thread(target=self._reader_thread, args=(p.proc, log_q, prefix), daemon=True)
                t.start()
        except Exception as e:
            messagebox.showerror("Failed to start", f"{p.name} failed to start:\n{e}")


    def _stop_process(self, p: ProcInfo) -> None:
        if not p.proc or p.proc.poll() is not None:
            return
        try:
            p.proc.terminate()
        except Exception:
            pass

    def start_neural(self) -> None:
        # Hub intent: thinker should now be kept running unless the user explicitly stops it.
        self._mark_neural_started_by_hub()

        # Reset runner-ready gate file (prevents stale "ready" from a prior run)
        try:
            with open(self.runner_ready_path, "w", encoding="utf-8") as f:
                json.dump({"timestamp": time.time(), "ready": False, "stage": "starting"}, f)
        except Exception:
            pass

        self._start_process(self.proc_neural, log_q=self.runner_log_q, prefix="[RUNNER] ")

    def start_trader(self) -> None:

        # Before starting the trader, ensure we have a clean separation between:
        #   - user manual / long-term holdings
        #   - bot-owned (active-trade) holdings
        # This allows cost basis + DCA stages to be reconstructed correctly after restarts.
        try:
            ok = self._ensure_bot_order_ids_for_current_holdings()
            if not ok:
                return
        except Exception:
            pass

        self._start_process(self.proc_trader, log_q=self.trader_log_q, prefix="[TRADER] ")


    # -----------------------------
    # Neural auto-restart state
    # -----------------------------

    def _neural_autorestart_state_path(self) -> str:
        return os.path.join(self.hub_dir, "neural_autorestart_state.json")

    def _write_neural_autorestart_state(self) -> None:
        try:
            _safe_write_json(
                self._neural_autorestart_state_path(),
                {
                    "timestamp": time.time(),
                    "should_be_running": bool(getattr(self, "_neural_should_be_running", False)),
                    "user_stopped_from_hub": bool(getattr(self, "_neural_user_stopped_from_hub", False)),
                    "last_auto_restart_ts": float(getattr(self, "_neural_last_auto_restart_ts", 0.0) or 0.0),
                },
            )
        except Exception:
            pass

    def _reset_neural_autorestart_state_on_startup(self) -> None:
        self._neural_should_be_running = False
        self._neural_user_stopped_from_hub = False
        self._neural_last_auto_restart_ts = 0.0
        self._write_neural_autorestart_state()

    def _mark_neural_started_by_hub(self) -> None:
        self._neural_should_be_running = True
        self._neural_user_stopped_from_hub = False
        self._write_neural_autorestart_state()

    def _mark_neural_stopped_by_user(self) -> None:
        self._neural_should_be_running = False
        self._neural_user_stopped_from_hub = True
        self._write_neural_autorestart_state()

    # -----------------------------
    # Bot order ownership picker (startup: choose bot-owned orders for currently-held coins)
    # -----------------------------

    def _bot_order_ids_path(self) -> str:
        return os.path.join(self.hub_dir, "bot_order_ids.json")

    def _load_bot_order_ids(self) -> Dict[str, List[str]]:
        try:
            path = self._bot_order_ids_path()
            if os.path.isfile(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
                if isinstance(data, dict):
                    out: Dict[str, List[str]] = {}
                    for k, v in data.items():
                        sym = str(k).upper().strip()
                        if not sym:
                            continue
                        if isinstance(v, list):
                            out[sym] = [str(x).strip() for x in v if str(x).strip()]
                    return out
        except Exception:
            pass
        return {}

    def _save_bot_order_ids(self, data: Dict[str, List[str]]) -> None:
        try:
            path = self._bot_order_ids_path()
            tmp = f"{path}.tmp"
            cleaned: Dict[str, List[str]] = {}
            for k, v in (data or {}).items():
                sym = str(k).upper().strip()
                if not sym:
                    continue
                if not isinstance(v, list):
                    continue
                cleaned[sym] = sorted({str(x).strip() for x in v if str(x).strip()})
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cleaned, f, indent=2)
            os.replace(tmp, path)
        except Exception:
            pass

    def _bot_order_ids_from_trade_history(self) -> Dict[str, set]:
        """
        trade_history.jsonl is bot-generated, so these order_ids are safe to auto-preselect.

        IMPORTANT:
        Only preselect the order_ids that belong to the *current open trade* per coin,
        i.e. orders AFTER the most recent bot SELL for that coin.
        """
        out: Dict[str, set] = {}
        try:
            path = os.path.join(self.hub_dir, "trade_history.jsonl")
            if not os.path.isfile(path):
                return out

            last_sell_ts: Dict[str, float] = {}
            rows: List[dict] = []

            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = (line or "").strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue

                    sym_full = str(obj.get("symbol") or "").strip().upper()
                    base = sym_full.split("-")[0].strip() if sym_full else ""
                    if not base:
                        continue

                    rows.append(obj)

                    side = str(obj.get("side", "")).lower().strip()
                    if side != "sell":
                        continue

                    try:
                        ts_f = float(obj.get("ts", 0.0) or 0.0)
                    except Exception:
                        ts_f = 0.0

                    prev = float(last_sell_ts.get(base, 0.0) or 0.0)
                    if ts_f > prev:
                        last_sell_ts[base] = ts_f

            for obj in rows:
                oid = str(obj.get("order_id") or "").strip()
                if not oid:
                    continue

                sym_full = str(obj.get("symbol") or "").strip().upper()
                base = sym_full.split("-")[0].strip() if sym_full else ""
                if not base:
                    continue

                try:
                    ts_f = float(obj.get("ts", 0.0) or 0.0)
                except Exception:
                    ts_f = 0.0

                if ts_f > float(last_sell_ts.get(base, 0.0) or 0.0):
                    out.setdefault(base, set()).add(oid)

        except Exception:
            pass
        return out


    def _rh_load_api_creds(self) -> Optional[tuple]:
        """Read cb_key.txt + cb_secret.txt (saved by the Setup Wizard)."""
        try:
            key_path = os.path.join(self.project_dir, "cb_key.txt")
            sec_path = os.path.join(self.project_dir, "cb_secret.txt")
            if not os.path.isfile(key_path) or not os.path.isfile(sec_path):
                return None
            with open(key_path, "r", encoding="utf-8") as f:
                api_key = (f.read() or "").strip()
            with open(sec_path, "r", encoding="utf-8") as f:
                api_secret = (f.read() or "").strip()
            if not api_key or not api_secret:
                return None
            return (api_key, api_secret)
        except Exception:
            return None

    def _rh_make_request(self, method: str, path: str, body: str = "") -> Optional[dict]:
        """Signed Coinbase API request (read-only for this UI feature)."""
        try:
            import requests
            import hmac
            import hashlib
        except Exception:
            return None

        try:
            import base64
        except Exception:
            return None

        creds = self._rh_load_api_creds()
        if not creds:
            return None
        api_key, api_secret = creds

        try:
            ts = str(int(time.time()))
            message = f"{ts}{method}{path}{body}"
            
            signature = hmac.new(
                api_secret.encode('utf-8'),
                message.encode('utf-8'),
                hashlib.sha256
            ).digest()
            signature_b64 = base64.b64encode(signature).decode('utf-8')

            headers = {
                "CB-ACCESS-KEY": api_key,
                "CB-ACCESS-SIGN": signature_b64,
                "CB-ACCESS-TIMESTAMP": ts,
                "Content-Type": "application/json",
            }

            base_url = "https://api.coinbase.com"
            url = f"{base_url}{path}"

            if str(method).upper() == "GET":
                resp = requests.get(url, headers=headers, timeout=15)
            else:
                resp = requests.request(str(method).upper(), url, headers=headers, data=body, timeout=15)

            if resp.status_code >= 400:
                return None
            return resp.json()
        except Exception:
            return None

    def _rh_get_holdings(self) -> Optional[list]:
        data = self._rh_make_request("GET", "/api/v3/brokerage/accounts")
        if not data or not isinstance(data, dict):
            return None
        res = data.get("accounts", None)
        if not isinstance(res, list):
            return None
        return res

    def _rh_get_orders(self, full_symbol: str, limit: int = 50) -> Optional[list]:
        sym = str(full_symbol).upper().strip()
        if not sym:
            return None
        # Coinbase uses product_id format: BTC-USD, ETH-USD, etc.
        product_id = sym if "-USD" in sym else f"{sym}-USD"
        data = self._rh_make_request("GET", f"/api/v3/brokerage/orders/historical/batch?product_id={product_id}&limit={int(limit)}")
        if not data or not isinstance(data, dict):
            return None
        res = data.get("orders", None)
        if not isinstance(res, list):
            return None
        return res[: int(limit) if limit else 50]

    def _pick_bot_orders_for_coin(self, base_symbol: str, preselect_ids: Optional[List[str]] = None) -> Optional[set]:
        """
        Fetch the recent order history for this coin and open the selection modal.

        Returns:
          - set([...]) of selected order IDs
          - None if the user cancels
        """
        sym = str(base_symbol).upper().strip()
        if not sym:
            return set()

        symbol_full = f"{sym}-USD"

        orders = self._rh_get_orders(symbol_full, limit=50)
        if orders is None:
            try:
                messagebox.showwarning(
                    "Order history unavailable",
                    f"Could not fetch order history for {symbol_full}.\n\n"
                    "The picker will still open, but it will be empty.\n"
                    "If you save with nothing selected, ALL holdings of this coin will be treated as long-term/manual."
                )
            except Exception:
                pass
            orders = []

        try:
            pre = {str(x).strip() for x in (preselect_ids or []) if str(x).strip()}
        except Exception:
            pre = set()

        return self._pick_bot_orders_modal(sym, orders, pre)


    def _pick_bot_orders_modal(self, base_symbol: str, orders: list, preselected_ids: set) -> Optional[set]:
        """Modal UI: user selects which API-history orders belong to the BOT (active trading)."""
        base = str(base_symbol).upper().strip()
        if not base:
            return set()

        win = tk.Toplevel(self)
        win.title(f"Select bot orders — {base}")
        win.configure(bg=DARK_BG)
        win.geometry("980x620")
        win.minsize(860, 540)
        win.transient(self)
        try:
            win.grab_set()
        except Exception:
            pass

        help_txt = (
            "Select the buy orders for this coin's current auto trade only.\n\n"
            "Do not select sell orders.\n\n"
            "Anything not selected will be treated as manual or long-term.\n\n"
            "If you select none, the bot will ignore this coin and treat all current holdings as manual/long-term."
        )


        ttk.Label(win, text=help_txt, justify="left").pack(anchor="w", padx=12, pady=(12, 8))

        frame = ttk.Frame(win)
        frame.pack(fill="both", expand=True, padx=12, pady=8)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        lb = tk.Listbox(
            frame,
            selectmode="extended",
            height=18,
            exportselection=False,  # IMPORTANT: prevents selection from being clobbered by focus/selection changes
        )
        lb.grid(row=0, column=0, sticky="nsew")
        sb = ttk.Scrollbar(frame, orient="vertical", command=lb.yview)
        sb.grid(row=0, column=1, sticky="ns")
        lb.configure(yscrollcommand=sb.set)

        # Normalize preselected IDs (must match actual order IDs, not indices)
        try:
            pre_ids = {str(x).strip() for x in (preselected_ids or set()) if str(x).strip()}
        except Exception:
            pre_ids = set()

        # Build display rows
        order_ids: List[str] = []
        for o in (orders or [])[:50]:
            oid = str(o.get("id") or "").strip()
            created = str(o.get("created_at") or "").replace("T", " ").replace("Z", "")
            side = str(o.get("side") or "").upper()
            state = str(o.get("state") or "")

            qty = 0.0
            px = None
            try:
                exs = o.get("executions", []) or []
                for ex in exs:
                    qty += float(ex.get("quantity") or 0.0)
                    if px is None:
                        px = float(ex.get("effective_price") or 0.0)
            except Exception:
                pass

            row = f"{created:19}  {side:4}  qty={qty:.10f}  px={px if px is not None else '—'}  state={state}  id={oid}"
            lb.insert("end", row)
            order_ids.append(oid)

        # Preselect (stable + deterministic)
        try:
            lb.selection_clear(0, "end")

            # Ensure Tk has processed pending geometry/focus before we apply selection
            try:
                win.update_idletasks()
            except Exception:
                pass

            first_sel = None
            for i, oid in enumerate(order_ids):
                if oid and (oid in pre_ids):
                    lb.selection_set(i)
                    if first_sel is None:
                        first_sel = i

            # Scroll so the first selected row is visible
            if first_sel is not None:
                lb.see(first_sel)
        except Exception:
            pass


        out = {"selected": None}

        def _select_all():
            try:
                lb.selection_set(0, "end")
            except Exception:
                pass

        def _select_none():
            try:
                lb.selection_clear(0, "end")
            except Exception:
                pass

        def _save_and_close():
            try:
                idxs = list(lb.curselection())
                sel = set()
                for i in idxs:
                    if 0 <= int(i) < len(order_ids):
                        oid = str(order_ids[int(i)]).strip()
                        if oid:
                            sel.add(oid)
                out["selected"] = sel
            except Exception:
                out["selected"] = set()
            try:
                win.destroy()
            except Exception:
                pass

        def _cancel():
            out["selected"] = None
            try:
                win.destroy()
            except Exception:
                pass

        btns = ttk.Frame(win)
        btns.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(btns, text="Select All", command=_select_all).pack(side="left")
        ttk.Button(btns, text="Select None", command=_select_none).pack(side="left", padx=8)
        ttk.Button(btns, text="Save", command=_save_and_close).pack(side="right")
        ttk.Button(btns, text="Cancel", command=_cancel).pack(side="right", padx=8)

        win.protocol("WM_DELETE_WINDOW", _cancel)

        # Block until closed
        try:
            self.wait_window(win)
        except Exception:
            pass

        return out.get("selected")

    def _ensure_bot_order_ids_for_current_holdings(self) -> bool:
        """
        Startup-only bot-order picker.

        Goal:
          - Let you manually trade / have long-term/manual holdings WITHOUT breaking the bot's cost basis.
          - We do NOT store "long term amounts" anymore.
          - Instead, we store ONLY the bot-owned order IDs for the *current* trade per coin.
          - Anything in your account holdings beyond the bot-owned position qty is treated as
            long-term / manual and ignored by the bot automatically.

        Behavior:
          - On trader start, for EACH currently-held coin (qty > 0), show the order-selection popup.
          - If holdings cannot be fetched (Hub creds/session not available), fall back to:
              * coins present in bot_order_ids.json
              * coins present in trade_history.jsonl (current open trade)
          - The popup lets you select the orders that belong to the bot's *current* trade.
          - We save the selected order IDs to bot_order_ids.json.
          - We NEVER auto-add/update any long-term amounts in settings.
        """
        try:
            existing = self._load_bot_order_ids()
            from_hist = self._bot_order_ids_from_trade_history()

            # NOTE: _rh_get_holdings() returns a LIST of holdings objects (not a dict with "results").
            holdings_list = self._rh_get_holdings()

            held_coins: List[str] = []

            if holdings_list is not None and isinstance(holdings_list, list):
                for h in (holdings_list or []):
                    if not isinstance(h, dict):
                        continue

                    sym = str(h.get("asset_code") or "").upper().strip()
                    if not sym or sym in ("USD", "USDT", "USDC"):
                        continue

                    # Some RH payloads use different keys; accept any of these.
                    raw_qty = h.get("total_quantity", None)
                    if raw_qty is None:
                        raw_qty = h.get("quantity", None)
                    if raw_qty is None:
                        raw_qty = h.get("quantity_available", None)

                    try:
                        qty = float(raw_qty or 0.0)
                    except Exception:
                        qty = 0.0

                    if qty > 1e-12:
                        held_coins.append(sym)
            else:
                # Hub couldn't fetch holdings (creds/session). Still show the popups using local state.
                held_coins = sorted(set(list(existing.keys()) + list(from_hist.keys())))

            if not held_coins:
                return True

            changed = False

            for sym in sorted(set(held_coins)):
                # Prefer CURRENT-TRADE hints from bot trade_history first, because existing saved
                # selections can be stale after a completed trade or manual activity.
                preselect = set(from_hist.get(sym, []) or [])
                if not preselect:
                    preselect = set(existing.get(sym, []) or [])

                # Show the picker every startup so you can correct it after manual activity.
                selected = self._pick_bot_orders_for_coin(sym, preselect_ids=sorted(preselect))
                if selected is None:
                    # user cancelled - treat as "don't start trader"
                    return False

                selected_ids = [str(x).strip() for x in (selected or []) if str(x).strip()]
                if existing.get(sym, []) != selected_ids:
                    existing[sym] = selected_ids
                    changed = True

            if changed:
                self._save_bot_order_ids(existing)

            return True

        except Exception as e:
            print(e)
            while True:
                continue





    def stop_neural(self) -> None:
        self._mark_neural_stopped_by_user()
        self._stop_process(self.proc_neural)



    def stop_trader(self) -> None:
        self._stop_process(self.proc_trader)

    def toggle_all_scripts(self) -> None:
        neural_running = bool(self.proc_neural.proc and self.proc_neural.proc.poll() is None)
        trader_running = bool(self.proc_trader.proc and self.proc_trader.proc.poll() is None)

        # If anything is running (or we're waiting on runner readiness), toggle means "stop"
        if neural_running or trader_running or bool(getattr(self, "_auto_start_trader_pending", False)):
            self.stop_all_scripts()
            return

        # Otherwise, toggle means "start"
        self.start_all_scripts()

    def _read_runner_ready(self) -> Dict[str, Any]:
        try:
            if os.path.isfile(self.runner_ready_path):
                with open(self.runner_ready_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {"ready": False}

    def _poll_runner_ready_then_start_trader(self) -> None:
        # Cancelled or already started
        if not bool(getattr(self, "_auto_start_trader_pending", False)):
            return

        # If runner died, stop waiting
        if not (self.proc_neural.proc and self.proc_neural.proc.poll() is None):
            self._auto_start_trader_pending = False
            return

        st = self._read_runner_ready()
        if bool(st.get("ready", False)):
            self._auto_start_trader_pending = False

            # Start trader if not already running
            if not (self.proc_trader.proc and self.proc_trader.proc.poll() is None):
                self.start_trader()
            return

        # Not ready yet — keep polling
        try:
            self.after(250, self._poll_runner_ready_then_start_trader)
        except Exception:
            pass

    def start_all_scripts(self) -> None:
        # Enforce flow: Train → Neural → (wait for runner READY) → Trader
        all_trained = all(self._coin_is_trained(c) for c in self.coins) if self.coins else False
        if not all_trained:
            messagebox.showwarning(
                "Training required",
                "All coins must be trained before starting Neural Runner.\n\nUse Train All first."
            )
            return

        self._auto_start_trader_pending = True
        self.start_neural()

        # Wait for runner to signal readiness before starting trader
        try:
            self.after(250, self._poll_runner_ready_then_start_trader)
        except Exception:
            pass


    def _coin_is_trained(self, coin: str) -> bool:
        coin = coin.upper().strip()
        folder = self.coin_folders.get(coin, "")
        if not folder or not os.path.isdir(folder):
            return False

        # If trainer reports it's currently training, it's not "trained" yet.
        try:
            st = _safe_read_json(os.path.join(folder, "trainer_status.json"))
            if isinstance(st, dict) and str(st.get("state", "")).upper() == "TRAINING":
                return False
        except Exception:
            pass

        stamp_path = os.path.join(folder, "trainer_last_training_time.txt")
        try:
            if not os.path.isfile(stamp_path):
                return False
            with open(stamp_path, "r", encoding="utf-8") as f:
                raw = (f.read() or "").strip()
            ts = float(raw) if raw else 0.0
            if ts <= 0:
                return False
            return (time.time() - ts) <= (14 * 24 * 60 * 60)
        except Exception:
            return False

    def _running_trainers(self) -> List[str]:
        running: List[str] = []

        # Trainers launched by this GUI instance
        for c, lp in self.trainers.items():
            try:
                if lp.info.proc and lp.info.proc.poll() is None:
                    running.append(c)
            except Exception:
                pass

        # Trainers launched elsewhere: look at per-coin status file
        for c in self.coins:
            try:
                coin = (c or "").strip().upper()
                folder = self.coin_folders.get(coin, "")
                if not folder or not os.path.isdir(folder):
                    continue

                status_path = os.path.join(folder, "trainer_status.json")
                st = _safe_read_json(status_path)

                if isinstance(st, dict) and str(st.get("state", "")).upper() == "TRAINING":
                    stamp_path = os.path.join(folder, "trainer_last_training_time.txt")

                    try:
                        if os.path.isfile(stamp_path) and os.path.isfile(status_path):
                            if os.path.getmtime(stamp_path) >= os.path.getmtime(status_path):
                                continue
                    except Exception:
                        pass

                    running.append(coin)
            except Exception:
                pass

        # de-dupe while preserving order
        out: List[str] = []
        seen = set()
        for c in running:
            cc = (c or "").strip().upper()
            if cc and cc not in seen:
                seen.add(cc)
                out.append(cc)
        return out



    def _training_status_map(self) -> Dict[str, str]:
        """
        Returns {coin: "TRAINED" | "TRAINING" | "NOT TRAINED"}.

        Cached by a compact signature of the relevant per-coin files so the GUI
        does not hit the filesystem for every coin on every UI tick.
        """
        try:
            sig_parts: List[Tuple[str, Optional[int], Optional[int]]] = []

            for c in self.coins:
                coin = str(c).upper().strip()
                folder = self.coin_folders.get(coin, "")
                if not folder or not os.path.isdir(folder):
                    sig_parts.append((coin, None, None))
                    continue

                status_path = os.path.join(folder, "trainer_status.json")
                stamp_path = os.path.join(folder, "trainer_last_training_time.txt")

                status_sig = _trade_history_file_sig(status_path)
                stamp_sig = _trade_history_file_sig(stamp_path)

                status_key = None if status_sig is None else int(status_sig[0])
                stamp_key = None if stamp_sig is None else int(stamp_sig[0])

                sig_parts.append((coin, status_key, stamp_key))

            sig = tuple(sig_parts)

            if getattr(self, "_last_training_status_map_sig", None) == sig:
                cached = getattr(self, "_last_training_status_map_cache", None)
                if isinstance(cached, dict):
                    return dict(cached)

            running = set(self._running_trainers())
            out: Dict[str, str] = {}
            for c in self.coins:
                if c in running:
                    out[c] = "TRAINING"
                elif self._coin_is_trained(c):
                    out[c] = "TRAINED"
                else:
                    out[c] = "NOT TRAINED"

            self._last_training_status_map_sig = sig
            self._last_training_status_map_cache = dict(out)
            return out
        except Exception:
            running = set(self._running_trainers())
            out: Dict[str, str] = {}
            for c in self.coins:
                if c in running:
                    out[c] = "TRAINING"
                elif self._coin_is_trained(c):
                    out[c] = "TRAINED"
                else:
                    out[c] = "NOT TRAINED"
            return out

    def train_selected_coin(self) -> None:
        coin = (getattr(self, 'train_coin_var', self.trainer_coin_var).get() or "").strip().upper()

        if not coin:
            return
        # Reuse the trainers pane runner — start trainer for selected coin
        self.start_trainer_for_selected_coin()

    def train_all_coins(self) -> None:
        # Start trainers for every coin (in parallel)
        for c in self.coins:
            self.trainer_coin_var.set(c)
            self.start_trainer_for_selected_coin()

    def start_trainer_for_selected_coin(self) -> None:
        coin = (self.trainer_coin_var.get() or "").strip().upper()
        if not coin:
            return

        # Stop the Neural Runner before any training starts (training modifies artifacts the runner reads)
        self.stop_neural()

        # --- IMPORTANT ---
        # Match the trader's folder convention:
        #   BTC runs from the main neural folder
        #   Alts run from their own coin subfolder
        coin_cwd = self.coin_folders.get(coin, self.project_dir)

        # Use the trainer script that lives INSIDE that coin's folder so outputs land in the right place.
        trainer_name = os.path.basename(str(self.settings.get("script_neural_trainer", "pt_trainer.py")))

        # If an alt coin folder doesn't exist yet, create it and copy the trainer script from the main (BTC) folder.
        # (Also: overwrite to avoid running stale trainer copies in alt folders.)

        if coin != "BTC":
            try:
                if not os.path.isdir(coin_cwd):
                    os.makedirs(coin_cwd, exist_ok=True)

                src_main_folder = self.coin_folders.get("BTC", self.project_dir)
                src_trainer_path = os.path.join(src_main_folder, trainer_name)
                dst_trainer_path = os.path.join(coin_cwd, trainer_name)

                if os.path.isfile(src_trainer_path):
                    shutil.copy2(src_trainer_path, dst_trainer_path)
            except Exception:
                pass

        trainer_path = os.path.join(coin_cwd, trainer_name)

        if not os.path.isfile(trainer_path):
            messagebox.showerror(
                "Missing trainer",
                f"Cannot find trainer for {coin} at:\n{trainer_path}"
            )
            return

        if coin in self.trainers and self.trainers[coin].info.proc and self.trainers[coin].info.proc.poll() is None:
            return


        try:
            patterns = [
                "trainer_last_training_time.txt",
                "trainer_status.json",
                "trainer_last_start_time.txt",
                "killer.txt",
                "memories_*.txt",
                "memory_weights_*.txt",
                "neural_perfect_threshold_*.txt",
            ]


            deleted = 0
            for pat in patterns:
                for fp in glob.glob(os.path.join(coin_cwd, pat)):
                    try:
                        os.remove(fp)
                        deleted += 1
                    except Exception:
                        pass

            if deleted:
                try:
                    self.status.config(text=f"Deleted {deleted} training file(s) for {coin} before training")
                except Exception:
                    pass
        except Exception:
            pass

        q: "queue.Queue[str]" = queue.Queue(maxsize=2000)
        info = ProcInfo(name=f"Trainer-{coin}", path=trainer_path)

        env = os.environ.copy()
        env["POWERTRADER_HUB_DIR"] = self.hub_dir

        try:
            # IMPORTANT: pass `coin` so pt_trainer trains the correct market instead of defaulting to BTC
            info.proc = subprocess.Popen(
                [sys.executable, "-u", info.path, coin],

                cwd=coin_cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            t = threading.Thread(target=self._reader_thread, args=(info.proc, q, f"[{coin}] "), daemon=True)
            t.start()

            self.trainers[coin] = LogProc(info=info, log_q=q, thread=t, is_trainer=True, coin=coin)
        except Exception as e:
            messagebox.showerror("Failed to start", f"Trainer for {coin} failed to start:\n{e}")




    def stop_trainer_for_selected_coin(self) -> None:
        coin = (self.trainer_coin_var.get() or "").strip().upper()
        lp = self.trainers.get(coin)
        if not lp or not lp.info.proc or lp.info.proc.poll() is not None:
            return
        try:
            lp.info.proc.terminate()
        except Exception:
            pass


    def stop_all_scripts(self) -> None:
        # Cancel any pending "wait for runner then start trader"
        self._auto_start_trader_pending = False

        self.stop_neural()
        self.stop_trader()

        # Also reset the runner-ready gate file (best-effort)
        try:
            with open(self.runner_ready_path, "w", encoding="utf-8") as f:
                json.dump({"timestamp": time.time(), "ready": False, "stage": "stopped"}, f)
        except Exception:
            pass


    def _on_timeframe_changed(self, event) -> None:
        """
        Immediate redraw request when the user changes a timeframe in any CandleChart.
        The heavy preparation work happens off the Tk thread.
        """
        try:
            chart = getattr(event, "widget", None)
            if not isinstance(chart, CandleChart):
                return

            coin = getattr(chart, "coin", None)
            if not coin:
                return

            self._queue_visible_chart_refresh(force_coin=str(coin).strip().upper())

            # Keep the periodic refresh behavior consistent.
            self._last_chart_refresh = time.time()
        except Exception:
            pass

    def _get_selected_chart_coin(self) -> Optional[str]:
        selected_tab = None

        try:
            selected_tab = getattr(self, "_current_chart_page", None)
        except Exception:
            selected_tab = None

        if not selected_tab:
            try:
                if hasattr(self, "nb") and self.nb:
                    selected_tab = self.nb.tab(self.nb.select(), "text")
            except Exception:
                selected_tab = None

        if not selected_tab:
            return None

        selected_tab = str(selected_tab).strip().upper()
        if not selected_tab or selected_tab == "ACCOUNT":
            return None
        return selected_tab

    def _queue_visible_chart_refresh(self, force_coin: Optional[str] = None) -> None:
        coin = (force_coin or self._get_selected_chart_coin() or "").strip().upper()
        if not coin:
            return

        chart = self.charts.get(coin)
        if not chart:
            return

        try:
            chart_coins = list(getattr(self, "chart_coins", []) or [])
            chart_cf_sig = (self.settings.get("main_neural_dir"), tuple(chart_coins))
            if getattr(self, "_chart_coin_folders_sig", None) != chart_cf_sig:
                self._chart_coin_folders_sig = chart_cf_sig
                self.chart_coin_folders = build_coin_folders(self.settings["main_neural_dir"], chart_coins)
        except Exception:
            try:
                self.chart_coin_folders = build_coin_folders(
                    self.settings["main_neural_dir"],
                    list(getattr(self, "chart_coins", []) or []),
                )
            except Exception:
                return

        pos = self._last_positions.get(coin, {}) if isinstance(self._last_positions, dict) else {}
        buy_px = pos.get("current_buy_price", None)
        sell_px = pos.get("current_sell_price", None)
        trail_line = pos.get("trail_line", None)
        dca_line_price = pos.get("dca_line_price", None)
        avg_cost_basis = pos.get("avg_cost_basis", None)

        with self._chart_refresh_lock:
            self._chart_refresh_request_id += 1
            req_id = self._chart_refresh_request_id

            # Do not stack multiple background refresh workers.
            # Keep only the latest requested id and let the next tick launch again.
            if self._chart_refresh_inflight_id:
                return

            self._chart_refresh_inflight_id = req_id

        t = threading.Thread(
            target=self._chart_refresh_worker,
            args=(req_id, coin, buy_px, sell_px, trail_line, dca_line_price, avg_cost_basis),
            daemon=True,
        )
        t.start()

    def _chart_refresh_worker(
        self,
        req_id: int,
        coin: str,
        buy_px: Optional[float],
        sell_px: Optional[float],
        trail_line: Optional[float],
        dca_line_price: Optional[float],
        avg_cost_basis: Optional[float],
    ) -> None:
        payload: Optional[dict] = None

        try:
            chart = self.charts.get(coin)
            if not chart:
                return

            payload = chart.preload_refresh_data(
                getattr(self, "chart_coin_folders", self.coin_folders),
                current_buy_price=buy_px,
                current_sell_price=sell_px,
                trail_line=trail_line,
                dca_line_price=dca_line_price,
                avg_cost_basis=avg_cost_basis,
            )
        except Exception:
            payload = None
        finally:
            with self._chart_refresh_lock:
                try:
                    if isinstance(payload, dict) and req_id >= self._chart_refresh_request_id:
                        self._chart_refresh_result = {
                            "request_id": req_id,
                            "coin": coin,
                            "payload": payload,
                        }
                finally:
                    if self._chart_refresh_inflight_id == req_id:
                        self._chart_refresh_inflight_id = 0

    def _apply_pending_chart_refresh(self) -> None:
        result = None
        with self._chart_refresh_lock:
            result = self._chart_refresh_result
            self._chart_refresh_result = None

        if not result:
            return

        req_id = int(result.get("request_id", 0) or 0)
        coin = str(result.get("coin", "")).strip().upper()
        payload = result.get("payload", None)

        if not coin or not isinstance(payload, dict):
            return

        with self._chart_refresh_lock:
            if req_id < self._chart_refresh_request_id:
                return

        # Only apply if that coin is still the visible chart page.
        selected_coin = self._get_selected_chart_coin()
        if selected_coin != coin:
            return

        chart = self.charts.get(coin)
        if not chart:
            return

        try:
            chart.apply_refresh_data(payload)
        except Exception:
            pass

    def _queue_account_chart_refresh(self) -> None:
        chart = self.account_chart
        if not chart:
            return

        with self._account_chart_refresh_lock:
            self._account_chart_refresh_request_id += 1
            req_id = self._account_chart_refresh_request_id

            # Do not stack multiple background refresh workers.
            # Keep only the latest requested id and let the next tick launch again.
            if self._account_chart_refresh_inflight_id:
                return

            self._account_chart_refresh_inflight_id = req_id

        t = threading.Thread(
            target=self._account_chart_refresh_worker,
            args=(req_id,),
            daemon=True,
        )
        t.start()

    def _account_chart_refresh_worker(self, req_id: int) -> None:
        payload: Optional[dict] = None

        try:
            chart = self.account_chart
            if not chart:
                return

            payload = chart.preload_refresh_data()
        except Exception:
            payload = None
        finally:
            with self._account_chart_refresh_lock:
                try:
                    if isinstance(payload, dict) and req_id >= self._account_chart_refresh_request_id:
                        self._account_chart_refresh_result = {
                            "request_id": req_id,
                            "payload": payload,
                        }
                finally:
                    if self._account_chart_refresh_inflight_id == req_id:
                        self._account_chart_refresh_inflight_id = 0

    def _apply_pending_account_chart_refresh(self) -> None:
        result = None
        with self._account_chart_refresh_lock:
            result = self._account_chart_refresh_result
            self._account_chart_refresh_result = None

        if not result:
            return

        req_id = int(result.get("request_id", 0) or 0)
        payload = result.get("payload", None)

        if not isinstance(payload, dict):
            return

        with self._account_chart_refresh_lock:
            if req_id < self._account_chart_refresh_request_id:
                return

        chart = self.account_chart
        if not chart:
            return

        try:
            chart.apply_refresh_data(payload)
        except Exception:
            pass

    # ---- refresh loop ----
    def _drain_queue_to_text(
        self,
        q: "queue.Queue[str]",
        txt: tk.Text,
        max_lines: int = 2500,
        max_batch: int = 200,
    ) -> None:
        """
        Drain log output into a Text widget without monopolizing the Tk main thread.

        Key behavior stays the same:
        - logs still appear in order
        - auto-scroll still happens when already near bottom
        - old lines are still trimmed

        Performance change:
        - bound the work by BOTH item count and a tiny time budget, so hover/scroll/clicks
          stay responsive even when subprocesses are spamming stdout.
        """
        batch: List[str] = []
        start = time.perf_counter()
        max_seconds = 0.008
        max_chars = 16000
        char_count = 0

        try:
            while len(batch) < max_batch:
                if (time.perf_counter() - start) >= max_seconds:
                    break

                item = q.get_nowait()
                batch.append(item)
                char_count += len(item)

                if char_count >= max_chars:
                    break
        except queue.Empty:
            pass
        except Exception:
            pass

        if not batch:
            return

        try:
            _y0, y1 = txt.yview()
            was_near_bottom = bool(y1 >= 0.98)
        except Exception:
            was_near_bottom = True

        try:
            txt.insert("end", "\n".join(batch) + "\n")
        except Exception:
            return

        try:
            current = int(txt.index("end-1c").split(".")[0])
            overflow = current - int(max_lines)
            if overflow > 0:
                txt.delete("1.0", f"{overflow}.0")
        except Exception:
            pass

        if was_near_bottom:
            try:
                txt.see("end")
            except Exception:
                pass

    def _tick(self) -> None:
        # process labels
        neural_running = bool(self.proc_neural.proc and self.proc_neural.proc.poll() is None)
        trader_running = bool(self.proc_trader.proc and self.proc_trader.proc.poll() is None)

        # Auto-restart thinker if:
        # - the hub intended it to be running
        # - the user did NOT explicitly stop it from the hub
        # - the process is currently dead
        #
        # This covers the exact case where the OS / machine randomly kills the thinker process.
        if neural_running:
            # Once it's confirmed alive again, clear the restart cooldown marker.
            self._neural_last_auto_restart_ts = 0.0
            self._write_neural_autorestart_state()
        else:
            should_restart_neural = (
                bool(getattr(self, "_neural_should_be_running", False))
                and (not bool(getattr(self, "_neural_user_stopped_from_hub", False)))
            )

            if should_restart_neural:
                now_ts = time.time()
                last_ts = float(getattr(self, "_neural_last_auto_restart_ts", 0.0) or 0.0)
                cooldown = float(getattr(self, "_neural_restart_cooldown_seconds", 5.0) or 5.0)

                if (now_ts - last_ts) >= cooldown:
                    self._neural_last_auto_restart_ts = now_ts
                    self._write_neural_autorestart_state()

                    try:
                        self.start_neural()
                    except Exception:
                        pass

                    neural_running = bool(self.proc_neural.proc and self.proc_neural.proc.poll() is None)

        self.lbl_neural.config(text=f"Neural: {'running' if neural_running else 'stopped'}")
        self.lbl_trader.config(text=f"Trader: {'running' if trader_running else 'stopped'}")

        # Start All is now a toggle (Start/Stop)
        try:
            if hasattr(self, "btn_toggle_all") and self.btn_toggle_all:
                if neural_running or trader_running or bool(getattr(self, "_auto_start_trader_pending", False)):
                    self.btn_toggle_all.config(text="Stop All")
                else:
                    self.btn_toggle_all.config(text="Start All")
        except Exception:
            pass

        # --- flow gating: Train -> Start All ---
        status_map = self._training_status_map()
        all_trained = all(v == "TRAINED" for v in status_map.values()) if status_map else False

        # Disable Start All until training is done (but always allow it if something is already running/pending,
        # so the user can still stop everything).
        can_toggle_all = True
        if (not all_trained) and (not neural_running) and (not trader_running) and (not self._auto_start_trader_pending):
            can_toggle_all = False

        try:
            self.btn_toggle_all.configure(state=("normal" if can_toggle_all else "disabled"))
        except Exception:
            pass

        # Training overview + per-coin list
        try:
            training_running = [c for c, s in status_map.items() if s == "TRAINING"]
            not_trained = [c for c, s in status_map.items() if s == "NOT TRAINED"]

            if training_running:
                self.lbl_training_overview.config(text=f"Training: RUNNING ({', '.join(training_running)})")
            elif not_trained:
                self.lbl_training_overview.config(text=f"Training: REQUIRED ({len(not_trained)} not trained)")
            else:
                self.lbl_training_overview.config(text="Training: READY (all trained)")

            # show each coin status (ONLY redraw the list if it actually changed)
            sig = tuple((c, status_map.get(c, "N/A")) for c in self.coins)
            if getattr(self, "_last_training_sig", None) != sig:
                self._last_training_sig = sig
                self.training_list.delete(0, "end")
                for c, st in sig:
                    self.training_list.insert("end", f"{c}: {st}")

            # show gating hint (Start All handles the runner->ready->trader sequence)
            if not all_trained:
                self.lbl_flow_hint.config(text="Flow: Train All required → then Start All")
            elif self._auto_start_trader_pending:
                self.lbl_flow_hint.config(text="Flow: Starting runner → waiting for ready → trader will auto-start")
            elif neural_running or trader_running:
                self.lbl_flow_hint.config(text="Flow: Running (use the button to stop)")
            else:
                self.lbl_flow_hint.config(text="Flow: Start All")
        except Exception:
            pass

        # neural overview bars (mtime-cached inside)
        self._refresh_neural_overview()

        # trader status -> current trades table (now mtime-cached inside)
        self._refresh_trader_status()

        # pnl ledger -> realized profit (now mtime-cached inside)
        self._refresh_pnl()

        # trade history (now mtime-cached inside)
        self._refresh_trade_history()

        # Apply any finished chart refreshes prepared by worker threads.
        self._apply_pending_account_chart_refresh()
        self._apply_pending_chart_refresh()

        # charts (throttle)
        now = time.time()
        if (now - self._last_chart_refresh) >= float(self.settings.get("chart_refresh_seconds", 10.0)):
            # Only rebuild coin_folders when inputs change (avoids directory scans every refresh)
            try:
                cf_sig = (self.settings.get("main_neural_dir"), tuple(self.coins))
                if getattr(self, "_coin_folders_sig", None) != cf_sig:
                    self._coin_folders_sig = cf_sig
                    self.coin_folders = build_coin_folders(self.settings["main_neural_dir"], self.coins)
            except Exception:
                try:
                    self.coin_folders = build_coin_folders(self.settings["main_neural_dir"], self.coins)
                except Exception:
                    pass

            # Queue charts for async preparation instead of rebuilding them on the Tk thread.
            try:
                self._queue_account_chart_refresh()
            except Exception:
                pass

            try:
                self._queue_visible_chart_refresh()
            except Exception:
                pass

            self._last_chart_refresh = now

        # drain logs into panes
        self._drain_queue_to_text(self.runner_log_q, self.runner_text)
        self._drain_queue_to_text(self.trader_log_q, self.trader_text)

        # trainer logs: show selected trainer output
        try:
            sel = (self.trainer_coin_var.get() or "").strip().upper()
            running = [c for c, lp in self.trainers.items() if lp.info.proc and lp.info.proc.poll() is None]
            self.trainer_status_lbl.config(text=f"running: {', '.join(running)}" if running else "(no trainers running)")

            lp = self.trainers.get(sel)
            if lp:
                self._drain_queue_to_text(lp.log_q, self.trainer_text)
        except Exception:
            pass

        self.status.config(text=f"{_now_str()} | hub_dir={self.hub_dir}")
        self.after(int(float(self.settings.get("ui_refresh_seconds", 1.0)) * 1000), self._tick)



    def _refresh_trader_status(self) -> None:
        # mtime cache: still skip completely if the file did not change
        try:
            mtime = os.path.getmtime(self.trader_status_path)
        except Exception:
            mtime = None

        if getattr(self, "_last_trader_status_mtime", object()) == mtime:
            return
        self._last_trader_status_mtime = mtime

        data = _safe_read_json(self.trader_status_path)

        # lazy per-tree caches: iid -> tuple(values)
        if not hasattr(self, "_trades_tree_cache"):
            self._trades_tree_cache = {}
        if not hasattr(self, "_lth_tree_cache"):
            self._lth_tree_cache = {}

        def _clear_tree(tree: ttk.Treeview, cache_attr: str) -> None:
            try:
                children = tree.get_children()
                if children:
                    tree.delete(*children)
            except Exception:
                try:
                    for iid in tree.get_children():
                        tree.delete(iid)
                except Exception:
                    pass
            setattr(self, cache_attr, {})

        def _sync_tree(tree: ttk.Treeview, cache_attr: str, rows: List[Tuple[str, Tuple[Any, ...]]]) -> None:
            """
            rows = [(iid, values_tuple), ...]
            Updates only changed rows, deletes removed rows, inserts missing rows,
            and preserves the requested order via move().
            """
            cache: Dict[str, Tuple[Any, ...]] = getattr(self, cache_attr, {}) or {}
            desired_iids = [iid for iid, _vals in rows]
            desired_set = set(desired_iids)

            # delete removed
            for iid in list(cache.keys()):
                if iid not in desired_set:
                    try:
                        tree.delete(iid)
                    except Exception:
                        pass
                    cache.pop(iid, None)

            # insert/update/reorder
            for idx, (iid, vals) in enumerate(rows):
                if cache.get(iid) != vals:
                    if iid in cache:
                        try:
                            tree.item(iid, values=vals)
                        except Exception:
                            try:
                                tree.delete(iid)
                            except Exception:
                                pass
                            try:
                                tree.insert("", idx, iid=iid, values=vals)
                            except Exception:
                                try:
                                    tree.insert("", "end", iid=iid, values=vals)
                                except Exception:
                                    pass
                    else:
                        try:
                            tree.insert("", idx, iid=iid, values=vals)
                        except Exception:
                            try:
                                tree.insert("", "end", iid=iid, values=vals)
                            except Exception:
                                pass
                    cache[iid] = vals

                try:
                    tree.move(iid, "", idx)
                except Exception:
                    pass

            setattr(self, cache_attr, cache)

        if not data:
            self.lbl_last_status.config(text="Last status: N/A (no trader_status.json yet)")

            try:
                self.lbl_acct_total_value.config(text="Total Account Value: N/A")
                self.lbl_acct_holdings_value.config(text="Holdings Value: N/A")
                self.lbl_acct_buying_power.config(text="Buying Power: N/A")
                self.lbl_acct_percent_in_trade.config(text="Percent In Trade: N/A")
                self.lbl_acct_dca_spread.config(text="DCA Levels (spread): N/A")
                self.lbl_acct_dca_single.config(text="DCA Levels (single): N/A")
            except Exception:
                pass

            _clear_tree(self.trades_tree, "_trades_tree_cache")
            _clear_tree(self.lth_tree, "_lth_tree_cache")
            return

        ts = data.get("timestamp")
        try:
            if isinstance(ts, (int, float)):
                self.lbl_last_status.config(text=f"Last status: {time.strftime('%H:%M:%S', time.localtime(ts))}")
            else:
                self.lbl_last_status.config(text="Last status: (unknown timestamp)")
        except Exception:
            self.lbl_last_status.config(text="Last status: (timestamp parse error)")

        # --- account summary ---
        acct = data.get("account", {}) or {}
        try:
            total_val = float(acct.get("total_account_value", 0.0) or 0.0)
            self._last_total_account_value = total_val

            self.lbl_acct_total_value.config(
                text=f"Total Account Value: {_fmt_money(acct.get('total_account_value', None))}"
            )
            self.lbl_acct_holdings_value.config(
                text=f"Holdings Value: {_fmt_money(acct.get('holdings_sell_value', None))}"
            )
            self.lbl_acct_buying_power.config(
                text=f"Buying Power: {_fmt_money(acct.get('buying_power', None))}"
            )

            pit = acct.get("percent_in_trade", None)
            try:
                pit_txt = f"{float(pit):.2f}%"
            except Exception:
                pit_txt = "N/A"
            self.lbl_acct_percent_in_trade.config(text=f"Percent In Trade: {pit_txt}")

            # -------------------------
            # DCA affordability
            # -------------------------
            coins = getattr(self, "coins", None) or []
            n = len(coins)
            spread_levels = 0.0
            single_levels = 0.0

            if total_val > 0.0:
                alloc_pct = float(self.settings.get("start_allocation_pct", 0.005) or 0.005)
                if alloc_pct < 0.0:
                    alloc_pct = 0.0
                alloc_frac = alloc_pct / 100.0

                dca_mult = float(self.settings.get("dca_multiplier", 2.0) or 2.0)
                if dca_mult < 0.0:
                    dca_mult = 0.0
                dca_factor = 1.0 + dca_mult

                alloc_spread = total_val * alloc_frac
                if alloc_spread < 0.5:
                    alloc_spread = 0.5

                required_spread = alloc_spread * n
                if required_spread > 0.0 and total_val >= required_spread and dca_factor > 1.0:
                    spread_levels = math.log(total_val / required_spread) / math.log(dca_factor)
                    if spread_levels < 0.0:
                        spread_levels = 0.0

                alloc_single = total_val * alloc_frac
                if alloc_single < 0.5:
                    alloc_single = 0.5

                required_single = alloc_single
                if required_single > 0.0 and total_val >= required_single and dca_factor > 1.0:
                    single_levels = math.log(total_val / required_single) / math.log(dca_factor)
                    if single_levels < 0.0:
                        single_levels = 0.0

            self.lbl_acct_dca_spread.config(text=f"DCA Levels (spread): {spread_levels:.2f}")
            self.lbl_acct_dca_single.config(text=f"DCA Levels (single): {single_levels:.2f}")

        except Exception:
            pass

        positions = data.get("positions", {}) or {}
        self._last_positions = positions

        dca_24h_by_coin: Dict[str, int] = {}
        try:
            dca_24h_by_coin = (
                _compute_dca_24h_by_coin(self.trade_history_path, now_ts=time.time())
                if self.trade_history_path else {}
            )
        except Exception:
            dca_24h_by_coin = {}

        # Update headings ONCE, not once per row
        try:
            max_dca_24h = int(float(
                self.settings.get(
                    "max_dca_buys_per_24h",
                    DEFAULT_SETTINGS.get("max_dca_buys_per_24h", 2),
                ) or 2
            ))
        except Exception:
            max_dca_24h = int(DEFAULT_SETTINGS.get("max_dca_buys_per_24h", 2) or 2)
        if max_dca_24h < 0:
            max_dca_24h = 0

        try:
            self.trades_tree.heading("dca_24h", text=f"DCA 24h (max {max_dca_24h})")
        except Exception:
            pass

        try:
            pm0 = float(self.settings.get("pm_start_pct_no_dca", DEFAULT_SETTINGS.get("pm_start_pct_no_dca", 5.0)) or 5.0)
            pm1 = float(self.settings.get("pm_start_pct_with_dca", DEFAULT_SETTINGS.get("pm_start_pct_with_dca", 2.5)) or 2.5)
            tg = float(self.settings.get("trailing_gap_pct", DEFAULT_SETTINGS.get("trailing_gap_pct", 0.5)) or 0.5)
            self.trades_tree.heading("trail_line", text=f"Trail Line (start {pm0:g}/{pm1:g}%, gap {tg:g}%)")
        except Exception:
            pass

        # -------------------------
        # Long-term holdings table
        # -------------------------
        lth_rows_for_tree: List[Tuple[str, Tuple[Any, ...]]] = []
        lth_rows_sort: List[Tuple[str, float, Optional[float], str]] = []

        for coin, pos in (positions or {}).items():
            sym = str(coin).upper().strip()
            if not sym:
                continue

            try:
                rq = float(pos.get("lth_reserved_qty", 0.0) or 0.0)
            except Exception:
                rq = 0.0

            if rq <= 0.0:
                continue

            sp = None
            try:
                sp_try = float(pos.get("current_sell_price", 0.0) or 0.0)
                if sp_try > 0.0:
                    sp = sp_try
            except Exception:
                pass

            if sp is None:
                try:
                    sp_try = float(pos.get("current_buy_price", 0.0) or 0.0)
                    if sp_try > 0.0:
                        sp = sp_try
                except Exception:
                    pass

            value_usd = None
            if sp is not None:
                try:
                    value_usd = float(rq) * float(sp)
                except Exception:
                    value_usd = None

            qty_disp = f"{rq:.10f}".rstrip("0").rstrip(".")
            if not qty_disp:
                qty_disp = "0"

            lth_rows_sort.append((sym, rq, value_usd, qty_disp))

        try:
            lth_rows_sort.sort(key=lambda r: (-float(r[2]) if r[2] is not None else float("inf"), r[0]))
        except Exception:
            pass

        for coin, _rq, value_usd, qty_disp in lth_rows_sort:
            lth_rows_for_tree.append((
                f"lth:{coin}",
                (
                    coin,
                    qty_disp,
                    _fmt_money(value_usd),
                ),
            ))

        _sync_tree(self.lth_tree, "_lth_tree_cache", lth_rows_for_tree)

        # -------------------------
        # Current trades table
        # -------------------------
        trade_rows_for_tree: List[Tuple[str, Tuple[Any, ...]]] = []

        for sym, pos in positions.items():
            coin = str(sym).upper().strip()
            qty = pos.get("quantity", 0.0)

            try:
                if float(qty) <= 0.0:
                    continue
            except Exception:
                continue

            value = pos.get("value_usd", 0.0)
            avg_cost = pos.get("avg_cost_basis", 0.0)

            buy_price = pos.get("current_buy_price", 0.0)
            buy_pnl = pos.get("gain_loss_pct_buy", 0.0)

            sell_price = pos.get("current_sell_price", 0.0)
            sell_pnl = pos.get("gain_loss_pct_sell", 0.0)

            dca_stages = pos.get("dca_triggered_stages", 0)
            dca_24h = int(dca_24h_by_coin.get(coin, 0))
            dca_24h_display = f"{dca_24h}/{max_dca_24h}"

            next_dca = pos.get("next_dca_display", "")
            trail_line = pos.get("trail_line", 0.0)

            trade_rows_for_tree.append((
                f"trade:{coin}",
                (
                    coin,
                    f"{float(qty):.8f}".rstrip("0").rstrip("."),
                    _fmt_money(value),
                    _fmt_price(avg_cost),
                    _fmt_price(buy_price),
                    _fmt_pct(buy_pnl),
                    _fmt_price(sell_price),
                    _fmt_pct(sell_pnl),
                    dca_stages,
                    dca_24h_display,
                    next_dca,
                    _fmt_price(trail_line),
                ),
            ))

        _sync_tree(self.trades_tree, "_trades_tree_cache", trade_rows_for_tree)

        # -------------------------------------------------
        # Keep chart tabs in sync with:
        #   - the configured trading coin list, PLUS
        #   - any coins currently held in-trade, PLUS
        #   - any coins reserved as long-term holdings
        # -------------------------------------------------
        try:
            base = [c.upper().strip() for c in (self.settings.get("coins") or []) if str(c).strip()]
            base_set = set(base)

            lth_cfg = self.settings.get("long_term_holdings") or []
            if isinstance(lth_cfg, str):
                lth_cfg = [x.strip() for x in lth_cfg.replace("\n", ",").split(",")]
            if not isinstance(lth_cfg, (list, tuple)):
                lth_cfg = []

            lth_set = set()
            for v in (lth_cfg or []):
                sym = str(v).upper().strip()
                if sym and sym not in ("USD", "USDT", "USDC"):
                    lth_set.add(sym)

            held_set = set()
            for sym, pos2 in (positions or {}).items():
                c = str(sym).upper().strip()
                if not c:
                    continue
                try:
                    qty2 = float(pos2.get("quantity", 0.0) or 0.0)
                except Exception:
                    qty2 = 0.0
                try:
                    lth_qty2 = float(pos2.get("lth_reserved_qty", 0.0) or 0.0)
                except Exception:
                    lth_qty2 = 0.0
                if qty2 > 0.0 or lth_qty2 > 0.0:
                    held_set.add(c)

            extras = sorted((lth_set | held_set) - base_set)
            desired = base + extras

            if desired != list(getattr(self, "chart_coins", []) or []):
                self.chart_coins = desired

                try:
                    self.chart_coin_folders = build_coin_folders(
                        self.settings.get("main_neural_dir") or self.project_dir,
                        self.chart_coins,
                    )
                    self._chart_coin_folders_sig = (self.settings.get("main_neural_dir"), tuple(self.chart_coins))
                except Exception:
                    pass

                try:
                    self._rebuild_coin_chart_tabs()
                except Exception:
                    pass
        except Exception:
            pass











    def _refresh_pnl(self) -> None:
        # mtime cache: avoid reading/parsing every tick
        try:
            mtime = os.path.getmtime(self.pnl_ledger_path)
        except Exception:
            mtime = None

        if getattr(self, "_last_pnl_mtime", object()) == mtime:
            return
        self._last_pnl_mtime = mtime

        data = _safe_read_json(self.pnl_ledger_path)
        if not data:
            self.lbl_pnl.config(text="Total realized: N/A")
            self.lbl_lth_profit_bucket.config(text="LTH profit bucket: N/A")
            return

        total = float(data.get("total_realized_profit_usd", 0.0))
        self.lbl_pnl.config(text=f"Total realized: ${total:,.4f}")

        

        bucket_usd = float(data.get("lth_profit_bucket_usd", 0.0) or 0.0)
        bucket_cents = bucket_usd * 100.0
        self.lbl_lth_profit_bucket.config(
            text=f"LTH profit bucket: {bucket_cents:.2f}¢ / 50.00¢  ({_fmt_money(bucket_usd)})"
        )



    def _refresh_trade_history(self) -> None:
        # mtime/signature cache: avoid rereading/reparsing the file during GUI refreshes
        sig = _trade_history_file_sig(self.trade_history_path)
        if getattr(self, "_last_trade_history_sig", object()) == sig:
            return
        self._last_trade_history_sig = sig

        if sig is None:
            self.hist_list.delete("1.0", "end")
            self.hist_list.insert("end", "(no trade_history.jsonl yet)")
            return

        try:
            rows = _read_trade_history_jsonl(self.trade_history_path, tail=250)
        except Exception:
            rows = []

        out_lines: List[str] = []

        for obj in reversed(rows):
            try:
                ts = obj.get("ts", None)
                tss = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts)) if isinstance(ts, (int, float)) else "?"
                side = str(obj.get("side", "")).upper()
                tag = str(obj.get("tag", "") or "").upper()

                sym = obj.get("symbol", "")
                qty = obj.get("qty", "")
                px = obj.get("price", None)
                pnl = obj.get("realized_profit_usd", None)
                pnl_pct = obj.get("pnl_pct", None)

                px_txt = _fmt_price(px) if px is not None else "N/A"

                action = side
                if tag:
                    action = f"{side}/{tag}"

                txt = f"{tss} | {action:10s} {sym:5s} | qty={qty} | px={px_txt}"

                show_trade_pnl_pct = None
                if side == "SELL":
                    show_trade_pnl_pct = pnl_pct
                elif side == "BUY" and tag == "DCA":
                    show_trade_pnl_pct = pnl_pct

                if show_trade_pnl_pct is not None:
                    try:
                        txt += f" | pnl@trade={_fmt_pct(float(show_trade_pnl_pct))}"
                    except Exception:
                        txt += f" | pnl@trade={show_trade_pnl_pct}"

                if pnl is not None:
                    try:
                        txt += f" | realized={float(pnl):+.2f}"
                    except Exception:
                        txt += f" | realized={pnl}"

                out_lines.append(txt)
            except Exception:
                try:
                    out_lines.append(json.dumps(obj))
                except Exception:
                    pass

        self.hist_list.delete("1.0", "end")
        if out_lines:
            self.hist_list.insert("end", "\n".join(out_lines) + "\n")
        else:
            self.hist_list.insert("end", "(no trade rows yet)")



    def _refresh_coin_dependent_ui(self, prev_coins: List[str]) -> None:
        """
        After settings change: refresh every coin-driven UI element:
          - Training dropdown (Train coin)
          - Trainers tab dropdown (Coin)
          - Neural overview tiles: add/remove tiles to match current trading coin list
          - Chart tabs: show ALL configured coins PLUS any coins held in-trade or reserved as long-term holdings
        """
        # Rebuild trading coin list + folders (used by allocation math and neural tiles)
        self.coins = [c.upper().strip() for c in (self.settings.get("coins") or []) if c.strip()]
        self.coin_folders = build_coin_folders(self.settings.get("main_neural_dir") or self.project_dir, self.coins)

        # Rebuild chart coin list (charts should include held/LTH coins even if not in trading list)
        try:
            base = list(self.coins)
            base_set = set(base)

            # long_term_holdings is SYMBOLS ONLY now (no qty/amount dict)
            lth_cfg = self.settings.get("long_term_holdings") or []
            if isinstance(lth_cfg, str):
                lth_cfg = [x.strip() for x in lth_cfg.replace("\n", ",").split(",")]
            if not isinstance(lth_cfg, (list, tuple)):
                lth_cfg = []

            lth_set = set()
            for v in lth_cfg:
                sym = str(v).upper().strip()
                if sym and sym not in ("USD", "USDT", "USDC"):
                    lth_set.add(sym)

            # If trader status already loaded, include any coins held in-trade or reserved for LTH
            held_set = set()
            for sym, pos in (getattr(self, "_last_positions", {}) or {}).items():
                c = str(sym).upper().strip()
                if not c:
                    continue
                try:
                    qty = float(pos.get("quantity", 0.0) or 0.0)
                except Exception:
                    qty = 0.0
                try:
                    lth_qty = float(pos.get("lth_reserved_qty", 0.0) or 0.0)
                except Exception:
                    lth_qty = 0.0
                if qty > 0.0 or lth_qty > 0.0:
                    held_set.add(c)

            extras = sorted((lth_set | held_set) - base_set)
            new_chart_coins = base + extras
        except Exception:
            new_chart_coins = list(self.coins)

        prev_chart = list(getattr(self, "chart_coins", []) or [])
        self.chart_coins = new_chart_coins

        # Keep a separate folder map for chart coins (so extra coins don't fall back to cwd paths)
        try:
            self.chart_coin_folders = build_coin_folders(self.settings.get("main_neural_dir") or self.project_dir, self.chart_coins)
            self._chart_coin_folders_sig = (self.settings.get("main_neural_dir"), tuple(self.chart_coins))
        except Exception:
            pass

        # Refresh coin dropdowns (they don't auto-update)
        try:
            # Training pane dropdown
            if hasattr(self, "train_coin_combo") and self.train_coin_combo.winfo_exists():
                self.train_coin_combo["values"] = self.coins
                cur = (self.train_coin_var.get() or "").strip().upper() if hasattr(self, "train_coin_var") else ""
                if self.coins and cur not in self.coins:
                    self.train_coin_var.set(self.coins[0])

            # Trainers tab dropdown
            if hasattr(self, "trainer_coin_combo") and self.trainer_coin_combo.winfo_exists():
                self.trainer_coin_combo["values"] = self.coins
                cur = (self.trainer_coin_var.get() or "").strip().upper() if hasattr(self, "trainer_coin_var") else ""
                if self.coins and cur not in self.coins:
                    self.trainer_coin_var.set(self.coins[0])

            # Keep both selectors aligned if both exist
            if hasattr(self, "train_coin_var") and hasattr(self, "trainer_coin_var"):
                if self.train_coin_var.get():
                    self.trainer_coin_var.set(self.train_coin_var.get())
        except Exception:
            pass

        # Rebuild neural overview tiles (if the widget exists)
        try:
            if hasattr(self, "neural_wrap") and self.neural_wrap.winfo_exists():
                self._rebuild_neural_overview()
                self._refresh_neural_overview()
        except Exception:
            pass

        # Rebuild chart tabs if the *chart coin* list changed
        try:
            if set(prev_chart) != set(self.chart_coins):
                self._rebuild_coin_chart_tabs()
        except Exception:
            pass



    def _rebuild_neural_overview(self) -> None:
        """
        Recreate the coin tiles in the left-side Neural Signals box to match self.coins.
        Uses WrapFrame so it automatically breaks into multiple rows.
        Adds hover highlighting and click-to-open chart.
        """
        if not hasattr(self, "neural_wrap") or self.neural_wrap is None:
            return

        # Clear old tiles
        try:
            if hasattr(self.neural_wrap, "clear"):
                self.neural_wrap.clear(destroy_widgets=True)
            else:
                for ch in list(self.neural_wrap.winfo_children()):
                    ch.destroy()
        except Exception:
            pass

        self.neural_tiles = {}

        for coin in (self.coins or []):
            tile = NeuralSignalTile(self.neural_wrap, coin, trade_start_level=int(self.settings.get("trade_start_level", 3) or 3))


            # --- Hover highlighting (real, visible) ---
            def _on_enter(_e=None, t=tile):
                try:
                    t.set_hover(True)
                except Exception:
                    pass

            def _on_leave(_e=None, t=tile):
                # Avoid flicker: when moving between child widgets, ignore "leave" if pointer is still inside tile.
                try:
                    x = t.winfo_pointerx()
                    y = t.winfo_pointery()
                    w = t.winfo_containing(x, y)
                    while w is not None:
                        if w == t:
                            return
                        w = getattr(w, "master", None)
                except Exception:
                    pass

                try:
                    t.set_hover(False)
                except Exception:
                    pass

            tile.bind("<Enter>", _on_enter, add="+")
            tile.bind("<Leave>", _on_leave, add="+")
            try:
                for w in tile.winfo_children():
                    w.bind("<Enter>", _on_enter, add="+")
                    w.bind("<Leave>", _on_leave, add="+")
            except Exception:
                pass

            # --- Click: open chart page ---
            def _open_coin_chart(_e=None, c=coin):
                try:
                    fn = getattr(self, "_show_chart_page", None)
                    if callable(fn):
                        fn(str(c).strip().upper())
                except Exception:
                    pass

            tile.bind("<Button-1>", _open_coin_chart, add="+")
            try:
                for w in tile.winfo_children():
                    w.bind("<Button-1>", _open_coin_chart, add="+")
            except Exception:
                pass

            self.neural_wrap.add(tile, padx=(0, 6), pady=(0, 6))
            self.neural_tiles[coin] = tile

        # Layout and scrollbar refresh
        try:
            self.neural_wrap._schedule_reflow()
        except Exception:
            pass

        try:
            fn = getattr(self, "_update_neural_overview_scrollbars", None)
            if callable(fn):
                self.after_idle(fn)
        except Exception:
            pass






    def _refresh_neural_overview(self) -> None:
        """
        Update each coin tile with long/short neural signals.
        Uses mtime caching so it's cheap to call every UI tick.
        """
        if not hasattr(self, "neural_tiles"):
            return

        # Keep coin_folders aligned with current settings/coins
        try:
            sig = (str(self.settings.get("main_neural_dir") or ""), tuple(self.coins or []))
            if getattr(self, "_coin_folders_sig", None) != sig:
                self._coin_folders_sig = sig
                self.coin_folders = build_coin_folders(self.settings.get("main_neural_dir") or self.project_dir, self.coins)
        except Exception:
            pass

        if not hasattr(self, "_neural_overview_cache"):
            self._neural_overview_cache = {}  # path -> (mtime, value)

        def _cached(path: str, loader, default: Any):
            try:
                mtime = os.path.getmtime(path)
            except Exception:
                return default, None

            hit = self._neural_overview_cache.get(path)
            if hit and hit[0] == mtime:
                return hit[1], mtime

            v = loader(path)
            self._neural_overview_cache[path] = (mtime, v)
            return v, mtime

        def _load_short_from_memory_json(path: str) -> int:
            try:
                obj = _safe_read_json(path) or {}
                return int(float(obj.get("short_dca_signal", 0)))
            except Exception:
                return 0

        latest_ts = None

        for coin, tile in list(self.neural_tiles.items()):
            folder = ""
            try:
                folder = (self.coin_folders or {}).get(coin, "")
            except Exception:
                folder = ""

            if not folder or not os.path.isdir(folder):
                tile.set_values(0, 0)
                continue

            long_sig = 0
            short_sig = 0
            mt_candidates: List[float] = []

            # Long signal
            long_path = os.path.join(folder, "long_dca_signal.txt")
            if os.path.isfile(long_path):
                long_sig, mt = _cached(long_path, read_int_from_file, 0)
                if mt:
                    mt_candidates.append(float(mt))

            # Short signal (prefer txt; fallback to memory.json)
            short_txt = os.path.join(folder, "short_dca_signal.txt")
            if os.path.isfile(short_txt):
                short_sig, mt = _cached(short_txt, read_int_from_file, 0)
                if mt:
                    mt_candidates.append(float(mt))
            else:
                mem = os.path.join(folder, "memory.json")
                if os.path.isfile(mem):
                    short_sig, mt = _cached(mem, _load_short_from_memory_json, 0)
                    if mt:
                        mt_candidates.append(float(mt))

            tile.set_values(long_sig, short_sig)

            if mt_candidates:
                mx = max(mt_candidates)
                latest_ts = mx if (latest_ts is None or mx > latest_ts) else latest_ts

        # Update "Last:" label
        try:
            if hasattr(self, "lbl_neural_overview_last") and self.lbl_neural_overview_last.winfo_exists():
                if latest_ts:
                    self.lbl_neural_overview_last.config(
                        text=f"Last: {time.strftime('%H:%M:%S', time.localtime(float(latest_ts)))}"
                    )
                else:
                    self.lbl_neural_overview_last.config(text="Last: N/A")
        except Exception:
            pass



    def _rebuild_coin_chart_tabs(self) -> None:
        """
        Ensure the Charts multi-row tab bar + pages match self.chart_coins
        (configured coins + any held-in-trade / long-term-holdings extras).
        Keeps the ACCOUNT page intact and preserves the currently selected page when possible.
        """
        charts_frame = getattr(self, "_charts_frame", None)
        if charts_frame is None or (hasattr(charts_frame, "winfo_exists") and not charts_frame.winfo_exists()):
            return

        # Decide which coins should have chart tabs
        raw = list(getattr(self, "chart_coins", None) or self.coins or [])
        raw = [str(c).upper().strip() for c in raw if str(c).strip()]

        # De-dupe while preserving order
        seen = set()
        chart_coins: List[str] = []
        for c in raw:
            if c == "ACCOUNT":
                continue
            if c in seen:
                continue
            seen.add(c)
            chart_coins.append(c)

        # Remember selected page (coin or ACCOUNT)
        selected = getattr(self, "_current_chart_page", "ACCOUNT")
        if selected not in (["ACCOUNT"] + list(chart_coins)):
            selected = "ACCOUNT"

        # Destroy existing tab bar + pages container (clean rebuild)
        try:
            if hasattr(self, "chart_tabs_bar") and self.chart_tabs_bar.winfo_exists():
                self.chart_tabs_bar.destroy()
        except Exception:
            pass

        try:
            if hasattr(self, "chart_pages_container") and self.chart_pages_container.winfo_exists():
                self.chart_pages_container.destroy()
        except Exception:
            pass

        # Recreate
        self.chart_tabs_bar = WrapFrame(charts_frame)
        self.chart_tabs_bar.pack(fill="x", padx=6, pady=(6, 0))

        self.chart_pages_container = ttk.Frame(charts_frame)
        self.chart_pages_container.pack(fill="both", expand=True, padx=6, pady=(0, 6))

        self._chart_tab_buttons = {}
        self.chart_pages = {}
        self._current_chart_page = selected

        def _show_page(name: str) -> None:
            self._current_chart_page = name
            for f in self.chart_pages.values():
                try:
                    f.pack_forget()
                except Exception:
                    pass
            f = self.chart_pages.get(name)
            if f is not None:
                f.pack(fill="both", expand=True)

            for txt, b in self._chart_tab_buttons.items():
                try:
                    b.configure(style=("ChartTabSelected.TButton" if txt == name else "ChartTab.TButton"))
                except Exception:
                    pass

        self._show_chart_page = _show_page

        # ACCOUNT page
        acct_page = ttk.Frame(self.chart_pages_container)
        self.chart_pages["ACCOUNT"] = acct_page

        acct_btn = ttk.Button(
            self.chart_tabs_bar,
            text="ACCOUNT",
            style="ChartTab.TButton",
            command=lambda: self._show_chart_page("ACCOUNT"),
        )
        self.chart_tabs_bar.add(acct_btn, padx=(0, 6), pady=(0, 6))
        self._chart_tab_buttons["ACCOUNT"] = acct_btn

        self.account_chart = AccountValueChart(
            acct_page,
            self.account_value_history_path,
            self.trade_history_path,
        )
        self.account_chart.pack(fill="both", expand=True)

        # Coin pages
        self.charts = {}
        for coin in chart_coins:
            page = ttk.Frame(self.chart_pages_container)
            self.chart_pages[coin] = page

            btn = ttk.Button(
                self.chart_tabs_bar,
                text=coin,
                style="ChartTab.TButton",
                command=lambda c=coin: self._show_chart_page(c),
            )
            self.chart_tabs_bar.add(btn, padx=(0, 6), pady=(0, 6))
            self._chart_tab_buttons[coin] = btn

            chart = CandleChart(page, self.fetcher, coin, self._settings_getter, self.trade_history_path)
            chart.pack(fill="both", expand=True)
            self.charts[coin] = chart

        # Restore selection
        self._show_chart_page(selected)





    # ---- settings dialog ----

    def open_settings_dialog(self) -> None:

        win = tk.Toplevel(self)
        win.title("Settings")
        # Big enough for the bottom buttons on most screens + still scrolls if someone resizes smaller.
        win.geometry("860x680")
        win.minsize(760, 560)
        win.configure(bg=DARK_BG)

        # Scrollable settings content (auto-hides the scrollbar if everything fits),
        # using the same pattern as the Neural Levels scrollbar.
        viewport = ttk.Frame(win)
        viewport.pack(fill="both", expand=True, padx=12, pady=12)
        viewport.grid_rowconfigure(0, weight=1)
        viewport.grid_columnconfigure(0, weight=1)

        settings_canvas = tk.Canvas(
            viewport,
            bg=DARK_BG,
            highlightthickness=1,
            highlightbackground=DARK_BORDER,
            bd=0,
        )
        settings_canvas.grid(row=0, column=0, sticky="nsew")

        settings_scroll = ttk.Scrollbar(
            viewport,
            orient="vertical",
            command=settings_canvas.yview,
        )
        settings_scroll.grid(row=0, column=1, sticky="ns")

        settings_canvas.configure(yscrollcommand=settings_scroll.set)

        frm = ttk.Frame(settings_canvas)
        settings_window = settings_canvas.create_window((0, 0), window=frm, anchor="nw")

        def _update_settings_scrollbars(event=None) -> None:
            """Update scrollregion + hide/show the scrollbar depending on overflow."""
            try:
                c = settings_canvas
                win_id = settings_window

                c.update_idletasks()
                bbox = c.bbox(win_id)
                if not bbox:
                    settings_scroll.grid_remove()
                    return

                c.configure(scrollregion=bbox)
                content_h = int(bbox[3] - bbox[1])
                view_h = int(c.winfo_height())

                if content_h > (view_h + 1):
                    settings_scroll.grid()
                else:
                    settings_scroll.grid_remove()
                    try:
                        c.yview_moveto(0)
                    except Exception:
                        pass
            except Exception:
                pass

        def _on_settings_canvas_configure(e) -> None:
            # Keep the inner frame exactly the canvas width so wrapping is correct.
            try:
                settings_canvas.itemconfigure(settings_window, width=int(e.width))
            except Exception:
                pass
            _update_settings_scrollbars()

        settings_canvas.bind("<Configure>", _on_settings_canvas_configure, add="+")
        frm.bind("<Configure>", _update_settings_scrollbars, add="+")

        # Mousewheel scrolling when the mouse is over the settings window.
        def _wheel(e):
            try:
                if settings_scroll.winfo_ismapped():
                    settings_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
            except Exception:
                pass

        settings_canvas.bind("<Enter>", lambda _e: settings_canvas.focus_set(), add="+")
        settings_canvas.bind("<MouseWheel>", _wheel, add="+")  # Windows / Mac
        settings_canvas.bind("<Button-4>", lambda _e: settings_canvas.yview_scroll(-3, "units"), add="+")  # Linux
        settings_canvas.bind("<Button-5>", lambda _e: settings_canvas.yview_scroll(3, "units"), add="+")   # Linux



        # Make the entry column expand
        frm.columnconfigure(0, weight=0)  # labels
        frm.columnconfigure(1, weight=1)  # entries
        frm.columnconfigure(2, weight=0)  # browse buttons

        def add_row(r: int, label: str, var: tk.Variable, browse: Optional[str] = None):
            """
            browse: "dir" to attach a directory chooser, else None.
            """
            ttk.Label(frm, text=label).grid(row=r, column=0, sticky="w", padx=(0, 10), pady=6)

            ent = ttk.Entry(frm, textvariable=var)
            ent.grid(row=r, column=1, sticky="ew", pady=6)

            if browse == "dir":
                def do_browse():
                    picked = filedialog.askdirectory()
                    if picked:
                        var.set(picked)
                ttk.Button(frm, text="Browse", command=do_browse).grid(row=r, column=2, sticky="e", padx=(10, 0), pady=6)
            else:
                # keep column alignment consistent
                ttk.Label(frm, text="").grid(row=r, column=2, sticky="e", padx=(10, 0), pady=6)

        main_dir_var = tk.StringVar(value=self.settings["main_neural_dir"])
        coins_var = tk.StringVar(value=",".join(self.settings["coins"]))

        # Long-term (ignored) holdings symbols (comma list: BTC,ETH,...)
        _lth = self.settings.get("long_term_holdings", []) or []
        if isinstance(_lth, str):
            _lth = [x.strip() for x in _lth.replace("\n", ",").split(",")]
        if not isinstance(_lth, (list, tuple)):
            _lth = []
        _lth_syms = []
        seen = set()
        for _sym in _lth:
            _sym = str(_sym).upper().strip()
            if not _sym or _sym in seen:
                continue
            seen.add(_sym)
            _lth_syms.append(_sym)
        long_term_holdings_var = tk.StringVar(value=",".join(_lth_syms))

        # % of realized trade profits to auto-buy into long-term holdings
        lth_profit_alloc_pct_var = tk.StringVar(value=str(self.settings.get("lth_profit_alloc_pct", DEFAULT_SETTINGS.get("lth_profit_alloc_pct", 0.0))))


        trade_start_level_var = tk.StringVar(value=str(self.settings.get("trade_start_level", 3)))

        start_alloc_pct_var = tk.StringVar(value=str(self.settings.get("start_allocation_pct", 0.005)))

        dca_mult_var = tk.StringVar(value=str(self.settings.get("dca_multiplier", 2.0)))
        _dca_levels = self.settings.get("dca_levels", DEFAULT_SETTINGS.get("dca_levels", []))
        if not isinstance(_dca_levels, list):
            _dca_levels = DEFAULT_SETTINGS.get("dca_levels", [])
        dca_levels_var = tk.StringVar(value=",".join(str(x) for x in _dca_levels))
        max_dca_var = tk.StringVar(value=str(self.settings.get("max_dca_buys_per_24h", DEFAULT_SETTINGS.get("max_dca_buys_per_24h", 2))))

        # --- Trailing PM settings (editable; hot-reload friendly) ---
        pm_no_dca_var = tk.StringVar(value=str(self.settings.get("pm_start_pct_no_dca", DEFAULT_SETTINGS.get("pm_start_pct_no_dca", 5.0))))
        pm_with_dca_var = tk.StringVar(value=str(self.settings.get("pm_start_pct_with_dca", DEFAULT_SETTINGS.get("pm_start_pct_with_dca", 2.5))))
        trailing_gap_var = tk.StringVar(value=str(self.settings.get("trailing_gap_pct", DEFAULT_SETTINGS.get("trailing_gap_pct", 0.5))))

        hub_dir_var = tk.StringVar(value=self.settings.get("hub_data_dir", ""))



        neural_script_var = tk.StringVar(value=self.settings["script_neural_runner2"])
        trainer_script_var = tk.StringVar(value=self.settings.get("script_neural_trainer", "pt_trainer.py"))
        trader_script_var = tk.StringVar(value=self.settings["script_trader"])

        ui_refresh_var = tk.StringVar(value=str(self.settings["ui_refresh_seconds"]))
        chart_refresh_var = tk.StringVar(value=str(self.settings["chart_refresh_seconds"]))
        candles_limit_var = tk.StringVar(value=str(self.settings["candles_limit"]))
        auto_start_var = tk.BooleanVar(value=bool(self.settings.get("auto_start_scripts", False)))

        r = 0
        add_row(r, "Main neural folder:", main_dir_var, browse="dir"); r += 1
        add_row(r, "Coins (comma):", coins_var); r += 1
        add_row(r, "Long-term holdings (symbols, optional):", long_term_holdings_var); r += 1
        add_row(r, "LTH auto-buy from profits (%):", lth_profit_alloc_pct_var); r += 1
        add_row(r, "Trade start level (1-7):", trade_start_level_var); r += 1


        # Start allocation % (shows approx $/coin using the last known account value; always displays the $0.50 minimum)
        ttk.Label(frm, text="Start allocation %:").grid(row=r, column=0, sticky="w", padx=(0, 10), pady=6)
        ttk.Entry(frm, textvariable=start_alloc_pct_var).grid(row=r, column=1, sticky="ew", pady=6)

        start_alloc_hint_var = tk.StringVar(value="")
        ttk.Label(frm, textvariable=start_alloc_hint_var).grid(row=r, column=2, sticky="w", padx=(10, 0), pady=6)

        def _update_start_alloc_hint(*_):
            # Parse % (allow "0.01" or "0.01%")
            try:
                pct_txt = (start_alloc_pct_var.get() or "").strip().replace("%", "")
                pct = float(pct_txt) if pct_txt else 0.0
            except Exception:
                pct = float(self.settings.get("start_allocation_pct", 0.005) or 0.005)

            if pct < 0.0:
                pct = 0.0

            # Use the last account value we saw in trader_status.json (no extra API calls).
            try:
                total_val = float(getattr(self, "_last_total_account_value", 0.0) or 0.0)
            except Exception:
                total_val = 0.0

            coins_list = [c.strip().upper() for c in (coins_var.get() or "").split(",") if c.strip()]
            n_coins = len(coins_list) if coins_list else 1

            per_coin = 0.0
            if total_val > 0.0:
                per_coin = total_val * (pct / 100.0)
            if per_coin < 0.5:
                per_coin = 0.5

            if total_val > 0.0:
                start_alloc_hint_var.set(f"≈ {_fmt_money(per_coin)} per coin (min $0.50)")
            else:
                start_alloc_hint_var.set("≈ $0.50 min per coin (needs account value)")

        _update_start_alloc_hint()
        start_alloc_pct_var.trace_add("write", _update_start_alloc_hint)
        coins_var.trace_add("write", _update_start_alloc_hint)

        r += 1

        add_row(r, "DCA levels (% list):", dca_levels_var); r += 1

        add_row(r, "DCA multiplier:", dca_mult_var); r += 1

        add_row(r, "Max DCA buys / coin (rolling 24h):", max_dca_var); r += 1

        add_row(r, "Trailing PM start % (no DCA):", pm_no_dca_var); r += 1
        add_row(r, "Trailing PM start % (with DCA):", pm_with_dca_var); r += 1
        add_row(r, "Trailing gap % (behind peak):", trailing_gap_var); r += 1

        add_row(r, "Hub data dir (optional):", hub_dir_var, browse="dir"); r += 1




        ttk.Separator(frm, orient="horizontal").grid(row=r, column=0, columnspan=3, sticky="ew", pady=10); r += 1

        add_row(r, "pt_thinker.py path:", neural_script_var); r += 1
        add_row(r, "pt_trainer.py path:", trainer_script_var); r += 1
        add_row(r, "pt_trader.py path:", trader_script_var); r += 1

        # --- Coinbase API setup (writes cb_key.txt + cb_secret.txt used by pt_trader.py) ---
        def _api_paths() -> Tuple[str, str]:
            key_path = os.path.join(self.project_dir, "cb_key.txt")
            secret_path = os.path.join(self.project_dir, "cb_secret.txt")
            return key_path, secret_path

        def _read_api_files() -> Tuple[str, str]:
            key_path, secret_path = _api_paths()
            try:
                with open(key_path, "r", encoding="utf-8") as f:
                    k = (f.read() or "").strip()
            except Exception:
                k = ""
            try:
                with open(secret_path, "r", encoding="utf-8") as f:
                    s = (f.read() or "").strip()
            except Exception:
                s = ""
            return k, s

        api_status_var = tk.StringVar(value="")

        def _refresh_api_status() -> None:
            key_path, secret_path = _api_paths()
            k, s = _read_api_files()

            missing = []
            if not k:
                missing.append("cb_key.txt (API Key)")
            if not s:
                missing.append("cb_secret.txt (API Secret)")

            if missing:
                api_status_var.set("Not configured ❌ (missing " + ", ".join(missing) + ")")
            else:
                api_status_var.set("Configured ✅ (credentials found)")

        def _open_api_folder() -> None:
            """Open the folder where cb_key.txt / cb_secret.txt live."""
            try:
                folder = os.path.abspath(self.project_dir)
                if os.name == "nt":
                    os.startfile(folder)  # type: ignore[attr-defined]
                    return
                if sys.platform == "darwin":
                    subprocess.Popen(["open", folder])
                    return
                subprocess.Popen(["xdg-open", folder])
            except Exception as e:
                messagebox.showerror("Couldn't open folder", f"Tried to open:\n{self.project_dir}\n\nError:\n{e}")

        def _clear_api_files() -> None:
            """Delete cb_key.txt / cb_secret.txt (with a big confirmation)."""
            key_path, secret_path = _api_paths()
            if not messagebox.askyesno(
                "Delete API credentials?",
                "This will delete:\n"
                f"  {key_path}\n"
                f"  {secret_path}\n\n"
                "After deleting, the trader can NOT authenticate until you run the setup wizard again.\n\n"
                "Are you sure you want to delete these files?"
            ):
                return

            try:
                if os.path.isfile(key_path):
                    os.remove(key_path)
                if os.path.isfile(secret_path):
                    os.remove(secret_path)
            except Exception as e:
                messagebox.showerror("Delete failed", f"Couldn't delete the files:\n\n{e}")
                return

            _refresh_api_status()
            messagebox.showinfo("Deleted", "Deleted cb_key.txt and cb_secret.txt.")

        def _open_robinhood_api_wizard() -> None:
            """
            Beginner-friendly wizard for Coinbase API credentials setup.

            What we store:
              - cb_key.txt    = your Coinbase API Key
              - cb_secret.txt = your Coinbase API Secret (treat like a password)
            """
            import webbrowser

            wiz = tk.Toplevel(win)
            wiz.title("Coinbase API Setup")
            wiz.geometry("900x650")
            wiz.minsize(800, 550)
            wiz.configure(bg=DARK_BG)

            # Scrollable content
            viewport = ttk.Frame(wiz)
            viewport.pack(fill="both", expand=True, padx=12, pady=12)
            viewport.grid_rowconfigure(0, weight=1)
            viewport.grid_columnconfigure(0, weight=1)

            wiz_canvas = tk.Canvas(viewport, bg=DARK_BG, highlightthickness=1, highlightbackground=DARK_BORDER, bd=0)
            wiz_canvas.grid(row=0, column=0, sticky="nsew")

            wiz_scroll = ttk.Scrollbar(viewport, orient="vertical", command=wiz_canvas.yview)
            wiz_scroll.grid(row=0, column=1, sticky="ns")
            wiz_canvas.configure(yscrollcommand=wiz_scroll.set)

            container = ttk.Frame(wiz_canvas)
            wiz_window = wiz_canvas.create_window((0, 0), window=container, anchor="nw")
            container.columnconfigure(0, weight=1)

            def _update_wiz_scrollbars(event=None):
                try:
                    c = wiz_canvas
                    c.update_idletasks()
                    bbox = c.bbox(wiz_window)
                    if not bbox:
                        wiz_scroll.grid_remove()
                        return
                    c.configure(scrollregion=bbox)
                    content_h = int(bbox[3] - bbox[1])
                    view_h = int(c.winfo_height())
                    if content_h > (view_h + 1):
                        wiz_scroll.grid()
                    else:
                        wiz_scroll.grid_remove()
                except Exception:
                    pass

            def _on_wiz_canvas_configure(e):
                try:
                    wiz_canvas.itemconfigure(wiz_window, width=int(e.width))
                except Exception:
                    pass
                _update_wiz_scrollbars()

            wiz_canvas.bind("<Configure>", _on_wiz_canvas_configure, add="+")
            container.bind("<Configure>", _update_wiz_scrollbars, add="+")

            def _wheel(e):
                try:
                    if wiz_scroll.winfo_ismapped():
                        wiz_canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
                except Exception:
                    pass

            wiz_canvas.bind("<Enter>", lambda _e: wiz_canvas.focus_set(), add="+")
            wiz_canvas.bind("<MouseWheel>", _wheel, add="+")
            wiz_canvas.bind("<Button-4>", lambda _e: wiz_canvas.yview_scroll(-3, "units"), add="+")
            wiz_canvas.bind("<Button-5>", lambda _e: wiz_canvas.yview_scroll(3, "units"), add="+")

            key_path, secret_path = _api_paths()
            existing_api_key, existing_api_secret = _read_api_files()

            def _open_in_file_manager(path: str):
                try:
                    p = os.path.abspath(path)
                    if os.name == "nt":
                        os.startfile(p)
                        return
                    if sys.platform == "darwin":
                        subprocess.Popen(["open", p])
                        return
                    subprocess.Popen(["xdg-open", p])
                except Exception as e:
                    messagebox.showerror("Couldn't open folder", f"Error: {e}")

            def _copy_to_clipboard(txt: str):
                try:
                    wiz.clipboard_clear()
                    wiz.clipboard_append(txt)
                    messagebox.showinfo("Copied", "Copied to clipboard.")
                except Exception:
                    pass

            intro = (
                "Coinbase API Setup for PowerTrader\n\n"
                "This wizard helps you create and store your Coinbase API credentials.\n\n"
                "What you'll do:\n"
                "  1) Open Coinbase and create an API Key with Crypto Trading permissions\n"
                "  2) Copy your API Key and Secret here\n"
                "  3) Save them to files (cb_key.txt and cb_secret.txt)\n\n"
                "Requirements:\n"
                "  • Coinbase account (verify your identity first)\n"
                "  • API Key with 'View' and 'Trade' permissions\n"
                "  • Keep your API Secret private — treat it like a password\n"
            )
            intro_lbl = ttk.Label(container, text=intro, justify="left", wraplength=750)
            intro_lbl.grid(row=0, column=0, sticky="ew", pady=(0, 15))

            btns_frame = ttk.Frame(container)
            btns_frame.grid(row=1, column=0, sticky="ew", pady=(0, 15))
            ttk.Button(btns_frame, text="Open Coinbase API Portal", command=lambda: webbrowser.open("https://www.coinbase.com/settings/api")).pack(side="left", padx=5)
            ttk.Button(btns_frame, text="Open Folder", command=lambda: _open_in_file_manager(self.project_dir)).pack(side="left", padx=5)

            # Step 1: API Key
            step1 = ttk.LabelFrame(container, text="Step 1 — API Key")
            step1.grid(row=2, column=0, sticky="ew", pady=(0, 10))
            step1.columnconfigure(0, weight=1)

            ttk.Label(step1, text="Paste your Coinbase API Key:", justify="left").grid(row=0, column=0, sticky="w", padx=10, pady=(8, 4))
            key_var = tk.StringVar(value=existing_api_key or "")
            key_ent = ttk.Entry(step1, textvariable=key_var, width=60)
            key_ent.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))

            # Step 2: API Secret
            step2 = ttk.LabelFrame(container, text="Step 2 — API Secret")
            step2.grid(row=3, column=0, sticky="ew", pady=(0, 10))
            step2.columnconfigure(0, weight=1)

            ttk.Label(step2, text="Paste your Coinbase API Secret:", justify="left").grid(row=0, column=0, sticky="w", padx=10, pady=(8, 4))
            secret_var = tk.StringVar(value=existing_api_secret or "")
            secret_ent = ttk.Entry(step2, textvariable=secret_var, width=60, show="•")
            secret_ent.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))

            # Step 3: Save
            step3 = ttk.LabelFrame(container, text="Step 3 — Save")
            step3.grid(row=4, column=0, sticky="ew")
            step3.columnconfigure(0, weight=1)

            ack_var = tk.BooleanVar(value=False)
            ttk.Checkbutton(
                step3,
                text="I understand the API Secret is PRIVATE and I won't share it.",
                variable=ack_var
            ).grid(row=0, column=0, sticky="w", padx=10, pady=(10, 6))

            def do_save():
                key = (key_var.get() or "").strip()
                secret = (secret_var.get() or "").strip()

                if not key or not secret:
                    messagebox.showerror("Missing info", "Please paste both API Key and Secret.")
                    return
                if not ack_var.get():
                    messagebox.showerror("Acknowledgment", "Please check the acknowledgment box.")
                    return

                try:
                    with open(key_path, "w", encoding="utf-8") as f:
                        f.write(key)
                    with open(secret_path, "w", encoding="utf-8") as f:
                        f.write(secret)
                    messagebox.showinfo("Saved", f"✅ Credentials saved:\n  {key_path}\n  {secret_path}")
                    _refresh_api_status()
                    wiz.destroy()
                except Exception as e:
                    messagebox.showerror("Save failed", f"Error: {e}")

            save_btn = ttk.Button(step3, text="Save Credentials", command=do_save)
            save_btn.grid(row=1, column=0, sticky="w", padx=10, pady=(0, 12))

            _update_wiz_scrollbars()

        ttk.Label(frm, text="Coinbase API:").grid(row=r, column=0, sticky="w", padx=(0, 10), pady=6)

        api_row = ttk.Frame(frm)
        api_row.grid(row=r, column=1, columnspan=2, sticky="ew", pady=6)
        api_row.columnconfigure(0, weight=1)

        ttk.Label(api_row, textvariable=api_status_var).grid(row=0, column=0, sticky="w")
        ttk.Button(api_row, text="Setup Wizard", command=_open_robinhood_api_wizard).grid(row=0, column=1, sticky="e", padx=(10, 0))
        ttk.Button(api_row, text="Open Folder", command=_open_api_folder).grid(row=0, column=2, sticky="e", padx=(8, 0))
        ttk.Button(api_row, text="Clear", command=_clear_api_files).grid(row=0, column=3, sticky="e", padx=(8, 0))

        r += 1

        _refresh_api_status()


        ttk.Separator(frm, orient="horizontal").grid(row=r, column=0, columnspan=3, sticky="ew", pady=10); r += 1


        add_row(r, "UI refresh seconds:", ui_refresh_var); r += 1
        add_row(r, "Chart refresh seconds:", chart_refresh_var); r += 1
        add_row(r, "Candles limit:", candles_limit_var); r += 1

        chk = ttk.Checkbutton(frm, text="Auto start scripts on GUI launch", variable=auto_start_var)
        chk.grid(row=r, column=0, columnspan=3, sticky="w", pady=(10, 0)); r += 1

        btns = ttk.Frame(frm)
        btns.grid(row=r, column=0, columnspan=3, sticky="ew", pady=14)
        btns.columnconfigure(0, weight=1)

        def save():
            try:
                # Track coins before changes so we can detect newly added coins
                prev_coins = set([str(c).strip().upper() for c in (self.settings.get("coins") or []) if str(c).strip()])

                self.settings["main_neural_dir"] = main_dir_var.get().strip()
                self.settings["coins"] = [c.strip().upper() for c in coins_var.get().split(",") if c.strip()]

                # Long-term (ignored) holdings symbols (comma list: BTC,ETH,...)
                lth_raw = (long_term_holdings_var.get() or "").strip()
                lth = []
                if lth_raw:
                    seen = set()
                    for part in lth_raw.replace("\n", ",").split(","):
                        sym = part.strip().upper()
                        if not sym or sym in seen:
                            continue
                        seen.add(sym)
                        lth.append(sym)
                self.settings["long_term_holdings"] = lth

                # LTH auto-buy from profits (% of realized profit)
                try:
                    ptxt = (lth_profit_alloc_pct_var.get() or "").strip().replace("%", "")
                    p = float(ptxt) if ptxt else 0.0
                except Exception:
                    p = float(self.settings.get("lth_profit_alloc_pct", DEFAULT_SETTINGS.get("lth_profit_alloc_pct", 0.0)) or 0.0)
                if p < 0.0:
                    p = 0.0
                if p > 100.0:
                    p = 100.0
                self.settings["lth_profit_alloc_pct"] = p


                self.settings["trade_start_level"] = max(1, min(int(float(trade_start_level_var.get().strip())), 7))


                sap = (start_alloc_pct_var.get() or "").strip().replace("%", "")
                self.settings["start_allocation_pct"] = max(0.0, float(sap or 0.0))

                dm = (dca_mult_var.get() or "").strip()
                try:
                    dm_f = float(dm)
                except Exception:
                    dm_f = float(self.settings.get("dca_multiplier", DEFAULT_SETTINGS.get("dca_multiplier", 2.0)) or 2.0)
                if dm_f < 0.0:
                    dm_f = 0.0
                self.settings["dca_multiplier"] = dm_f

                raw_dca = (dca_levels_var.get() or "").replace(",", " ").split()
                dca_levels = []
                for tok in raw_dca:
                    try:
                        dca_levels.append(float(tok))
                    except Exception:
                        pass
                if not dca_levels:
                    dca_levels = list(DEFAULT_SETTINGS.get("dca_levels", []))
                self.settings["dca_levels"] = dca_levels

                md = (max_dca_var.get() or "").strip()
                try:
                    md_i = int(float(md))
                except Exception:
                    md_i = int(self.settings.get("max_dca_buys_per_24h", DEFAULT_SETTINGS.get("max_dca_buys_per_24h", 2)) or 2)
                if md_i < 0:
                    md_i = 0
                self.settings["max_dca_buys_per_24h"] = md_i


                # --- Trailing PM settings ---
                try:
                    pm0 = float((pm_no_dca_var.get() or "").strip().replace("%", "") or 0.0)
                except Exception:
                    pm0 = float(self.settings.get("pm_start_pct_no_dca", DEFAULT_SETTINGS.get("pm_start_pct_no_dca", 5.0)) or 5.0)
                if pm0 < 0.0:
                    pm0 = 0.0
                self.settings["pm_start_pct_no_dca"] = pm0

                try:
                    pm1 = float((pm_with_dca_var.get() or "").strip().replace("%", "") or 0.0)
                except Exception:
                    pm1 = float(self.settings.get("pm_start_pct_with_dca", DEFAULT_SETTINGS.get("pm_start_pct_with_dca", 2.5)) or 2.5)
                if pm1 < 0.0:
                    pm1 = 0.0
                self.settings["pm_start_pct_with_dca"] = pm1

                try:
                    tg = float((trailing_gap_var.get() or "").strip().replace("%", "") or 0.0)
                except Exception:
                    tg = float(self.settings.get("trailing_gap_pct", DEFAULT_SETTINGS.get("trailing_gap_pct", 0.5)) or 0.5)
                if tg < 0.0:
                    tg = 0.0
                self.settings["trailing_gap_pct"] = tg



                self.settings["hub_data_dir"] = hub_dir_var.get().strip()




                self.settings["script_neural_runner2"] = neural_script_var.get().strip()
                self.settings["script_neural_trainer"] = trainer_script_var.get().strip()
                self.settings["script_trader"] = trader_script_var.get().strip()

                self.settings["ui_refresh_seconds"] = float(ui_refresh_var.get().strip())
                self.settings["chart_refresh_seconds"] = float(chart_refresh_var.get().strip())
                self.settings["candles_limit"] = int(float(candles_limit_var.get().strip()))
                self.settings["auto_start_scripts"] = bool(auto_start_var.get())
                self._save_settings()

                # If new coin(s) were added and their training folder doesn't exist yet,
                # create the folder and copy neural_trainer.py into it RIGHT AFTER saving settings.
                try:
                    new_coins = [c.strip().upper() for c in (self.settings.get("coins") or []) if c.strip()]
                    added = [c for c in new_coins if c and c not in prev_coins]

                    main_dir = self.settings.get("main_neural_dir") or self.project_dir
                    trainer_name = os.path.basename(str(self.settings.get("script_neural_trainer", "neural_trainer.py")))

                    # Best-effort resolve source trainer path:
                    # Prefer trainer living in the main (BTC) folder; fallback to the configured trainer path.
                    src_main_trainer = os.path.join(main_dir, trainer_name)
                    src_cfg_trainer = str(self.settings.get("script_neural_trainer", trainer_name))
                    src_trainer_path = src_main_trainer if os.path.isfile(src_main_trainer) else src_cfg_trainer

                    for coin in added:
                        if coin == "BTC":
                            continue  # BTC uses main folder; no per-coin folder needed

                        coin_dir = os.path.join(main_dir, coin)
                        if not os.path.isdir(coin_dir):
                            os.makedirs(coin_dir, exist_ok=True)

                        dst_trainer_path = os.path.join(coin_dir, trainer_name)
                        if (not os.path.isfile(dst_trainer_path)) and os.path.isfile(src_trainer_path):
                            shutil.copy2(src_trainer_path, dst_trainer_path)
                except Exception:
                    pass

                # Refresh all coin-driven UI (dropdowns + chart tabs)
                self._refresh_coin_dependent_ui(prev_coins)

                messagebox.showinfo("Saved", "Settings saved.")
                win.destroy()


            except Exception as e:
                messagebox.showerror("Error", f"Failed to save settings:\n{e}")


        ttk.Button(btns, text="Save", command=save).pack(side="left")
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="left", padx=8)


    # ---- close ----

    def _on_close(self) -> None:
        # Don’t force kill; just stop if running (you can change this later)
        try:
            self.stop_all_scripts()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    app = PowerTraderHub()
    app.mainloop()
