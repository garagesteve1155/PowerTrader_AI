"""
Push notifications for PowerTrader AI via ntfy.sh.

Configure ntfy_url in Settings (e.g. https://ntfy.sh/my-private-topic).
Leave blank to disable.

Hooks:
  pt_trader._record_trade()  → notify_trade()   (chart + fill details)
  pt_errors.emit()           → notify_error()   (errors and warnings)
"""

import io
import json
import os
import threading
from pt_env import utc_to_ts
import time
from typing import Optional

from pt_log import get_logger

log = get_logger("notify")

# Single shared Arctic store per process — LMDB does not allow multiple opens
_arctic_store = None
_arctic_lock  = threading.Lock()


def _get_arctic():
    global _arctic_store
    with _arctic_lock:
        if _arctic_store is None:
            try:
                import arcticdb as adb
                from pt_env import PTEnv
                env = PTEnv(os.path.dirname(os.path.abspath(__file__)))
                _arctic_store = adb.Arctic(f"lmdb:///{env.historic_data_dir}")
            except Exception as e:
                log.debug(f"Arctic init failed: {e}")
        return _arctic_store

# ── UI palette (matches style.css) ──────────────────────────────────────────
_BG      = "#0B0B14"
_SURFACE = "#111120"
_BORDER  = "#2A2A48"
_TEXT    = "#E4E4F0"
_MUTED   = "#8888A8"
_CYAN    = "#00D4FF"
_GOLD    = "#F0B429"
_GREEN   = "#00CC66"
_RED     = "#FF4466"
_PURPLE  = "#A855F7"


# ── Config ───────────────────────────────────────────────────────────────────

def _ntfy_url() -> str:
    try:
        from pt_env import PTEnv
        env = PTEnv(os.path.dirname(os.path.abspath(__file__)))
        cfg = env.get_config()
        if not cfg.get("notifications_enabled", True):
            return ""
        return str(cfg.get("ntfy_url") or "").strip()
    except Exception:
        return ""


# ── Chart generation ─────────────────────────────────────────────────────────

def _price_chart(base: str, avg_cost_basis: float, exit_price: float,
                 side: str, account_history_path: str) -> Optional[bytes]:
    """
    Two-panel chart:
      Top:    1h price candles (last 4 days) with entry/exit lines
      Bottom: account equity curve (last 30 days)
    Returns PNG bytes or None if data is unavailable.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        import matplotlib.gridspec as gridspec
        import pandas as pd

        # ── Price data ────────────────────────────────────────────────────
        price_df = None
        try:
            store = _get_arctic()
            if store is not None:
                lib_name = "kucoin60"
                if lib_name in store.list_libraries():
                    lib = store.get_library(lib_name)
                    sym = f"{base}_USDT"
                    if sym in lib.list_symbols():
                        price_df = lib.read(sym).data.tail(96)  # 4 days of 1h
        except Exception as e:
            log.debug(f"price data unavailable for chart: {e}")

        # ── Equity curve data ─────────────────────────────────────────────
        equity_df = None
        try:
            if os.path.isfile(account_history_path):
                rows = []
                cutoff = time.time() - 30 * 86400
                with open(account_history_path, "r") as f:
                    for line in f:
                        try:
                            obj = json.loads(line)
                            ts_val = obj.get("ts", 0)
                            ts_f = utc_to_ts(ts_val) if isinstance(ts_val, str) else float(ts_val or 0)
                            if ts_f >= cutoff:
                                obj["ts"] = ts_f
                                rows.append(obj)
                        except Exception:
                            continue
                if rows:
                    equity_df = pd.DataFrame(rows)
                    equity_df["ts"] = pd.to_datetime(equity_df["ts"], unit="s", utc=True)
                    equity_df = equity_df.set_index("ts").sort_index()
        except Exception as e:
            log.debug(f"equity data unavailable for chart: {e}")

        if price_df is None and equity_df is None:
            return None

        # ── Layout ────────────────────────────────────────────────────────
        n_panels = (1 if price_df is not None else 0) + (1 if equity_df is not None else 0)
        fig = plt.figure(figsize=(7, 2.8 * n_panels), facecolor=_BG,
                         layout="constrained")
        gs = gridspec.GridSpec(n_panels, 1, figure=fig, hspace=0.45)
        panel = 0

        # ── Price panel ───────────────────────────────────────────────────
        if price_df is not None:
            ax = fig.add_subplot(gs[panel])
            panel += 1
            ax.set_facecolor(_SURFACE)

            ax.plot(price_df.index, price_df["close"],
                    color=_CYAN, linewidth=1.4, zorder=3)

            # Entry line
            if avg_cost_basis > 0:
                ax.axhline(avg_cost_basis, color=_GOLD, linewidth=1,
                           linestyle="--", alpha=0.85,
                           label=f"Entry  ${avg_cost_basis:,.4g}")

            # Exit / fill line
            if exit_price > 0:
                profitable = side == "sell" and exit_price >= avg_cost_basis
                ec = _GREEN if profitable else _RED
                label = ("Exit" if side == "sell" else "Buy") + f"  ${exit_price:,.4g}"
                ax.axhline(exit_price, color=ec, linewidth=1,
                           linestyle=":", alpha=0.9, label=label)
                ax.plot(price_df.index[-1], exit_price, "o",
                        color=ec, markersize=5, zorder=5)

            ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
            plt.setp(ax.get_xticklabels(), rotation=25, ha="right", fontsize=7)
            ax.yaxis.tick_right()
            ax.tick_params(colors=_MUTED, labelsize=7.5)
            for sp in ax.spines.values():
                sp.set_edgecolor(_BORDER)
            ax.grid(True, color=_BORDER, linewidth=0.4, alpha=0.6)
            ax.set_title(f"{base}/USDT · 1h · last 4 days",
                         color=_TEXT, fontsize=8.5, pad=5)
            ax.legend(fontsize=7.5, loc="upper left",
                      facecolor=_SURFACE, edgecolor=_BORDER,
                      labelcolor=_TEXT, framealpha=0.9)

        # ── Equity panel ──────────────────────────────────────────────────
        if equity_df is not None:
            ax2 = fig.add_subplot(gs[panel])
            ax2.set_facecolor(_SURFACE)

            vals = equity_df["total_account_value"].values
            color = _GREEN if vals[-1] >= vals[0] else _RED
            ax2.plot(equity_df.index, vals, color=color, linewidth=1.4)
            ax2.fill_between(equity_df.index, vals, vals.min(),
                             color=color, alpha=0.08)

            ax2.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
            plt.setp(ax2.get_xticklabels(), rotation=25, ha="right", fontsize=7)
            ax2.yaxis.tick_right()
            ax2.tick_params(colors=_MUTED, labelsize=7.5)
            for sp in ax2.spines.values():
                sp.set_edgecolor(_BORDER)
            ax2.grid(True, color=_BORDER, linewidth=0.4, alpha=0.6)
            ax2.set_title("Account equity · 30 days",
                          color=_TEXT, fontsize=8.5, pad=5)
            ax2.yaxis.set_major_formatter(
                plt.FuncFormatter(lambda v, _: f"${v:,.0f}")
            )

        # tight_layout incompatible with constrained_layout — omit
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=110,
                    facecolor=fig.get_facecolor(), bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return buf.read()

    except Exception as e:
        log.debug(f"chart generation failed: {e}")
        return None


# ── ntfy send ────────────────────────────────────────────────────────────────

def _ascii(s: str) -> str:
    """Strip non-ASCII characters and collapse whitespace for HTTP headers."""
    return " ".join(s.encode("ascii", errors="ignore").decode().split())


def _send(url: str, title: str, message: str, tags: str,
          priority: str, image_bytes: Optional[bytes]):
    try:
        import requests as req
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        if image_bytes:
            # Binary PUT — headers must be ASCII; tags render as emoji on device
            headers = {
                "Content-Type": "image/png",
                "Title":    _ascii(title),
                "Message":  _ascii(message.replace("\n", " · ")),
                "Priority": priority,
                "Filename": "chart.png",
            }
            if tag_list:
                headers["Tags"] = ",".join(tag_list)
            resp = req.put(url, data=image_bytes, headers=headers, timeout=20)
        else:
            # Plain POST — body is the Markdown message; metadata in headers
            headers = {
                "Content-Type": "text/markdown",
                "Title":    _ascii(title),
                "Priority": priority,
            }
            if tag_list:
                headers["Tags"] = ",".join(tag_list)
            resp = req.post(url, data=message.encode("utf-8"),
                            headers=headers, timeout=15)
        if resp.status_code >= 400:
            log.warning(f"ntfy returned {resp.status_code}: {resp.text[:120]}")
    except Exception as e:
        log.warning(f"ntfy send failed: {e}")


def _fire(title: str, message: str, tags: str = "", priority: str = "default",
          image_bytes: Optional[bytes] = None):
    url = _ntfy_url()
    if not url:
        return
    threading.Thread(
        target=_send,
        args=(url, title, message, tags, priority, image_bytes),
        daemon=True,
    ).start()


def _fire_with_chart(title: str, message: str, tags: str, priority: str,
                     base: str, avg_cost_basis: float, exit_price: float,
                     side: str, account_history_path: str):
    """Generate chart then send — runs entirely in a daemon thread."""
    url = _ntfy_url()
    if not url:
        return
    img = _price_chart(base, avg_cost_basis, exit_price, side, account_history_path)
    _send(url, title, message, tags, priority, img)


# ── Public API ───────────────────────────────────────────────────────────────

def notify_trade(side: str, symbol: str, qty: float,
                 price: Optional[float], avg_cost_basis: Optional[float],
                 pnl_pct: Optional[float], notional_usd: Optional[float],
                 tag: Optional[str], buying_power: float = 0.0,
                 account_history_path: str = ""):
    """Call from _record_trade. Fires in a daemon thread; never blocks."""
    if not _ntfy_url():
        return

    tag_u = str(tag or "").upper()
    if tag_u == "LTH":
        return  # LTH housekeeping trades are noise

    base = symbol.split("_")[0].upper()
    side_l = str(side).lower()
    cb = float(avg_cost_basis or 0)
    px = float(price or 0)
    pnl = float(pnl_pct or 0)

    price_s   = f"${px:,.4g}"     if px  else "?"
    basis_s   = f"${cb:,.4g}"     if cb  else "?"
    notional_s = f"${notional_usd:,.2f}" if notional_usd else ""
    bp_s      = f"${buying_power:,.2f}"

    if side_l == "sell":
        sign = "+" if pnl >= 0 else ""
        emoji = "✅" if pnl >= 0 else "❌"
        title = f"{base} Sold  {sign}{pnl:.2f}%"
        tags  = ("money_with_wings,chart_increasing"
                 if pnl >= 0 else "money_with_wings,chart_decreasing")
        priority = "high" if abs(pnl) > 5 else "default"
        lines = [
            f"{emoji} **{base}** sold `{qty:.6g}` @ `{price_s}`",
            f"**Entry** `{basis_s}`  ·  **PnL** {sign}{pnl:.2f}%",
        ]
        if notional_s:
            lines.append(f"**Notional**  `{notional_s}`")
        lines.append(f"**Buying Power**  `{bp_s}`")
        if tag_u not in ("", "SELL", "TRAIL_SELL"):
            lines.append(f"**Tag**  {tag_u}")

    elif side_l == "buy":
        label = "DCA" if tag_u == "DCA" else "Entry"
        emoji = "🔵" if tag_u == "DCA" else "🟢"
        title = f"{base} {label} @ {price_s}"
        tags  = "seedling"
        priority = "default"
        lines = [f"{emoji} **{base}** bought `{qty:.6g}` @ `{price_s}`"]
        if notional_s:
            lines.append(f"**Spent**  `{notional_s}`")
        lines.append(f"**Buying Power**  `{bp_s}`")

    else:
        return

    message = "\n".join(lines)

    threading.Thread(
        target=_fire_with_chart,
        args=(title, message, tags, priority, base, cb, px, side_l,
              account_history_path),
        daemon=True,
    ).start()


def _equity_chart(account_history_path: str) -> Optional[bytes]:
    """Standalone 30-day equity curve chart."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        import pandas as pd

        if not os.path.isfile(account_history_path):
            return None

        cutoff = time.time() - 30 * 86400
        rows = []
        with open(account_history_path, "r") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                    ts_val = obj.get("ts", 0)
                    ts_f = utc_to_ts(ts_val) if isinstance(ts_val, str) else float(ts_val or 0)
                    if ts_f >= cutoff:
                        obj["ts"] = ts_f
                        rows.append(obj)
                except Exception:
                    continue
        if not rows:
            return None

        df = pd.DataFrame(rows)
        df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True)
        df = df.set_index("ts").sort_index()
        vals = df["total_account_value"].values

        fig, ax = plt.subplots(figsize=(7, 2.8), facecolor=_BG,
                               layout="constrained")
        ax.set_facecolor(_SURFACE)

        color = _GREEN if vals[-1] >= vals[0] else _RED
        ax.plot(df.index, vals, color=color, linewidth=1.5)
        ax.fill_between(df.index, vals, vals.min(), color=color, alpha=0.09)

        start, end = vals[0], vals[-1]
        delta = end - start
        sign = "+" if delta >= 0 else ""
        ax.set_title(
            f"Account equity · 30 days  |  ${end:,.2f}  ({sign}${delta:,.2f})",
            color=_TEXT, fontsize=9, pad=6,
        )
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d"))
        plt.setp(ax.get_xticklabels(), rotation=25, ha="right", fontsize=7)
        ax.yaxis.tick_right()
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))
        ax.tick_params(colors=_MUTED, labelsize=7.5)
        for sp in ax.spines.values():
            sp.set_edgecolor(_BORDER)
        ax.grid(True, color=_BORDER, linewidth=0.4, alpha=0.6)

        # tight_layout incompatible with constrained_layout — omit
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=110,
                    facecolor=fig.get_facecolor(), bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    except Exception as e:
        log.debug(f"equity chart failed: {e}")
        return None


def _fmt_price(price: float) -> str:
    """Dynamic decimal places matching pt_trader._fmt_price behaviour."""
    if price == 0:
        return "0"
    if price >= 1000:
        return f"{price:,.2f}"
    if price >= 1:
        return f"{price:,.4f}"
    if price >= 0.01:
        return f"{price:.6f}"
    return f"{price:.8f}"


def _coin_chart_12h(base: str, position: dict) -> Optional[bytes]:
    """5-day 1h close chart with entry, sell level, and current mid."""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates

        price_df = None
        store = _get_arctic()
        if store is not None:
            try:
                lib_name = "kucoin60"
                if lib_name in store.list_libraries():
                    lib = store.get_library(lib_name)
                    sym = f"{base}_USDT"
                    if sym in lib.list_symbols():
                        df = lib.read(sym).data.tail(120)  # 5 days × 24h
                        if len(df) >= 4:
                            price_df = df
            except Exception:
                pass

        if price_df is None:
            return None

        entry = float(position.get("avg_cost_basis", 0) or 0)
        trail = float(position.get("trail_line", 0) or 0)
        buy   = float(position.get("current_buy_price", 0) or 0)
        sell  = float(position.get("current_sell_price", 0) or 0)
        mid   = (buy + sell) / 2 if (buy > 0 and sell > 0) else (sell or buy)
        pnl   = float(position.get("gain_loss_pct_sell", 0) or 0)

        fig, ax = plt.subplots(figsize=(7, 3.2), facecolor=_BG, layout="constrained")
        ax.set_facecolor(_SURFACE)

        ax.plot(price_df.index, price_df["close"],
                color=_CYAN, linewidth=1.4, zorder=3)

        if entry > 0:
            ax.axhline(entry, color=_GOLD, linewidth=1.1, linestyle="--",
                       alpha=0.9, label=f"Entry  {_fmt_price(entry)}", zorder=4)
        if trail > 0:
            ax.axhline(trail, color=_GREEN, linewidth=1.0, linestyle="--",
                       alpha=0.85, label=f"Sell   {_fmt_price(trail)}", zorder=4)
        if mid > 0:
            ax.axhline(mid, color=_CYAN, linewidth=0.8, linestyle=":",
                       alpha=0.6, label=f"Mid    {_fmt_price(mid)}", zorder=4)

        sign = "+" if pnl >= 0 else ""
        ax.set_title(f"{base}/USDT · 5d 1h  |  {sign}{pnl:.2f}%",
                     color=_TEXT, fontsize=9, pad=5)
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d %H:%M"))
        plt.setp(ax.get_xticklabels(), rotation=25, ha="right", fontsize=6.5)
        ax.yaxis.tick_right()
        ax.tick_params(colors=_MUTED, labelsize=7)
        for sp in ax.spines.values():
            sp.set_edgecolor(_BORDER)
        ax.grid(True, color=_BORDER, linewidth=0.4, alpha=0.6)

        ax.legend(fontsize=6.5, loc="upper left", facecolor=_SURFACE,
                  edgecolor=_BORDER, labelcolor=_TEXT, framealpha=0.9,
                  ncol=2)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=110,
                    facecolor=fig.get_facecolor(), bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        return buf.read()
    except Exception as e:
        log.debug(f"coin chart 12h failed for {base}: {e}")
        return None


def _position_detail_text(sym: str, position: dict, exchange: str) -> tuple[str, str]:
    """Returns (title, Markdown detail message) for a single open position."""
    base = sym.split("_")[0].upper()
    qty   = float(position.get("quantity", 0) or 0)
    cb    = float(position.get("avg_cost_basis", 0) or 0)
    buy   = float(position.get("current_buy_price", 0) or 0)
    sell  = float(position.get("current_sell_price", 0) or 0)
    pnl   = float(position.get("gain_loss_pct_sell", 0) or 0)
    val   = float(position.get("value_usd", 0) or 0)
    trail = float(position.get("trail_line", 0) or 0)
    dca_s = int(position.get("dca_triggered_stages", 0) or 0)
    dca_t = int(position.get("dca_total_stages", 0) or 0)
    dca_p = float(position.get("dca_line_price", 0) or 0)
    next_dca_disp = str(position.get("next_dca_display", "") or "")

    sign = "+" if pnl >= 0 else ""
    title = f"{base} {exchange.upper()} {sign}{pnl:.2f}%  USD{val:,.2f}"

    lines = [
        f"**Qty**         `{qty:,.6g}`",
        f"**Avg Cost**    `{_fmt_price(cb)}`",
    ]
    if buy > 0 or sell > 0:
        lines.append(f"**Bid / Ask**   `{_fmt_price(buy)}` / `{_fmt_price(sell)}`")
    if trail > 0:
        lines.append(f"**Sell Level**  `{_fmt_price(trail)}`")
    if dca_t > 0:
        lines.append(f"**DCA Stage**   {dca_s} / {dca_t}")
    if dca_p > 0:
        dca_label = f"`{_fmt_price(dca_p)}`"
        if next_dca_disp:
            dca_label += f"  _{next_dca_disp}_"
        lines.append(f"**Next DCA**    {dca_label}")

    return title, "\n".join(lines)


def notify_positions_summary(positions: dict, account: dict,
                              account_history_path: str, exchange: str = ""):
    """Hourly open-positions digest. Called from manage_trades time gate."""
    if not _ntfy_url():
        return

    open_pos = {
        sym: p for sym, p in positions.items()
        if isinstance(p, dict) and float(p.get("quantity", 0) or 0) > 1e-12
    }

    total = float(account.get("total_account_value", 0) or 0)
    bp    = float(account.get("buying_power", 0) or 0)
    pct   = float(account.get("percent_in_trade", 0) or 0)
    n     = len(open_pos)

    def _send_all():
        url = _ntfy_url()
        if not url:
            return

        # Per-position: chart image + detail text
        for sym, p in open_pos.items():
            base = sym.split("_")[0].upper()
            title, detail = _position_detail_text(sym, p, exchange)

            img = _coin_chart_12h(base, p)
            if img:
                chart_title   = _ascii(f"{base} 12h chart")
                chart_message = _ascii(title)
                _send(url, chart_title, chart_message, tags="chart_with_upwards_trend",
                      priority="low", image_bytes=img)

            _send(url, title, detail, tags="", priority="low", image_bytes=None)

        # Account summary with equity chart
        acct_title = (
            f"{n} position{'s' if n != 1 else ''} open" if n else "No open positions"
        )
        acct_msg = (
            f"**Account**       `{total:,.2f}`\n"
            f"**Buying Power**  `{bp:,.2f}`\n"
            f"**In Trade**      {pct:.1f}%"
        )
        img = _equity_chart(account_history_path)
        _send(url, acct_title, acct_msg, tags="bar_chart", priority="low",
              image_bytes=img)

    threading.Thread(target=_send_all, daemon=True).start()


def test_notify(account_history_path: str = ""):
    """Send a representative fake trade + fake positions summary for review."""
    url = _ntfy_url()
    if not url:
        log.warning("ntfy_url is not configured — nothing to test")
        return

    # Fake trade notification (sell, +3.2%)
    notify_trade(
        side="sell", symbol="BTC_USD", qty=0.00124,
        price=98420.0, avg_cost_basis=95300.0, pnl_pct=3.2,
        notional_usd=122.04, tag="TRAIL_SELL", buying_power=1204.50,
        account_history_path=account_history_path,
    )

    # Fake positions summary
    fake_positions = {
        "ETH_USDT": {
            "quantity": 0.041, "avg_cost_basis": 3280.0,
            "current_buy_price": 3238.0, "current_sell_price": 3241.0,
            "gain_loss_pct_sell": -1.18, "value_usd": 132.88,
            "dca_triggered_stages": 1, "dca_total_stages": 6,
            "trail_line": 3420.0, "dca_line_price": 2952.0,
            "next_dca_display": "-10.00% / N3",
        },
        "SOL_USDT": {
            "quantity": 1.26, "avg_cost_basis": 141.35,
            "current_buy_price": 142.2, "current_sell_price": 142.5,
            "gain_loss_pct_sell": 0.81, "value_usd": 179.55,
            "dca_triggered_stages": 0, "dca_total_stages": 6,
            "trail_line": 148.0, "dca_line_price": 127.22,
            "next_dca_display": "-10.00% / N2",
        },
    }
    fake_account = {
        "total_account_value": 12450.0,
        "buying_power": 1204.50,
        "percent_in_trade": 7.6,
    }
    notify_positions_summary(fake_positions, fake_account, account_history_path,
                             exchange="demo")


def notify_error(component: str, level: str, message: str, detail: str = ""):
    """Call from pt_errors.emit for error/warning level events."""
    if level not in ("error", "warning"):
        return
    priority = "urgent" if level == "error" else "high"
    emoji    = "🚨" if level == "error" else "⚠️"
    title    = f"PowerTrader {level.title()} [{component}]"
    tags     = "rotating_light" if level == "error" else "warning"
    body     = f"{emoji} " + message + (f"\n\n{detail}" if detail else "")
    _fire(title, body, tags=tags, priority=priority)
