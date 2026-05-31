"""
Push notifications for PowerTrader AI via ntfy.sh.

Configure ntfy_url in Settings (e.g. https://ntfy.sh/my-private-topic).
Leave blank to disable.

Hooks:
  pt_trader._record_trade()  → notify_trade()   (text-only fill details)
  pt_errors.emit()           → notify_error()   (errors and warnings)
"""

import json
import os
import threading
import time
from typing import Optional

from pt_log import get_logger

log = get_logger("notify")


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


# ── ntfy send ────────────────────────────────────────────────────────────────

def _ascii(s: str) -> str:
    """Strip non-ASCII characters and collapse whitespace for HTTP headers."""
    return " ".join(s.encode("ascii", errors="ignore").decode().split())


def _send(url: str, title: str, message: str, tags: str, priority: str):
    try:
        import requests as req
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
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


def _fire(title: str, message: str, tags: str = "", priority: str = "default"):
    url = _ntfy_url()
    if not url:
        return
    threading.Thread(
        target=_send,
        args=(url, title, message, tags, priority),
        daemon=True,
    ).start()


# ── Public API ───────────────────────────────────────────────────────────────

def notify_trade(side: str, symbol: str, qty: float,
                 price: Optional[float], avg_cost_basis: Optional[float],
                 pnl_pct: Optional[float], notional_usd: Optional[float],
                 tag: Optional[str], buying_power: float = 0.0):
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

    price_s    = f"${_fmt_price(px)}" if px else "?"
    basis_s    = f"${_fmt_price(cb)}" if cb else "?"
    notional_s = f"${notional_usd:,.2f}" if notional_usd else ""
    bp_s       = f"${buying_power:,.2f}"

    if side_l == "sell":
        sign = "+" if pnl >= 0 else ""
        title = f"{base} Sold  {sign}{pnl:.2f}%"
        tags  = ("money_with_wings,chart_increasing"
                 if pnl >= 0 else "money_with_wings,chart_decreasing")
        priority = "high" if abs(pnl) > 5 else "default"
        rows = [
            ("Side",         "SELL"),
            ("Qty",          f"{qty:.6g}"),
            ("Price",        price_s),
            ("Entry",        basis_s),
            ("PnL",          f"{sign}{pnl:.2f}%"),
        ]
        if notional_s:
            rows.append(("Notional",     notional_s))
        rows.append(("Buying Power", bp_s))
        if tag_u not in ("", "SELL", "TRAIL_SELL"):
            rows.append(("Tag",          tag_u))

    elif side_l == "buy":
        label = "DCA" if tag_u == "DCA" else "BUY"
        title = f"{base} {label} @ {price_s}"
        tags  = "seedling"
        priority = "default"
        rows = [
            ("Side",         label),
            ("Qty",          f"{qty:.6g}"),
            ("Price",        price_s),
        ]
        if notional_s:
            rows.append(("Spent",        notional_s))
        rows.append(("Buying Power", bp_s))

    else:
        return

    label_w = max(len(k) for k, _ in rows)
    body = "\n".join(f"{k:<{label_w}}  {v}" for k, v in rows)
    message = f"```\n{base}\n{body}\n```"

    _fire(title, message, tags=tags, priority=priority)


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


def _signed_money(v: float) -> str:
    return f"+${v:,.2f}" if v >= 0 else f"-${abs(v):,.2f}"


def _realized_from_ledger(exchange: str) -> float:
    try:
        from pt_env import PTEnv
        env = PTEnv(os.path.dirname(os.path.abspath(__file__)))
        ledger_path = str(env.pnl_ledger_path(exchange))
        if not os.path.isfile(ledger_path):
            return 0.0
        with open(ledger_path) as f:
            return float(json.load(f).get("total_realized_profit_usd", 0) or 0)
    except Exception as e:
        log.debug(f"realized ledger read failed for {exchange}: {e}")
        return 0.0


def notify_positions_summary(positions: dict, account: dict, exchange: str = ""):
    """Single combined Kraken summary: account row + one line per open coin."""
    if not _ntfy_url():
        return
    if exchange.lower() != "kraken":
        return

    open_pos = {
        sym: p for sym, p in positions.items()
        if isinstance(p, dict) and float(p.get("quantity", 0) or 0) > 1e-12
    }

    total    = float(account.get("total_account_value", 0) or 0)
    bp       = float(account.get("buying_power", 0) or 0)
    holdings = float(account.get("holdings_sell_value", 0) or 0)
    pct      = float(account.get("percent_in_trade", 0) or 0)

    unrealized = 0.0
    for p in open_pos.values():
        qty  = float(p.get("quantity", 0) or 0)
        cb   = float(p.get("avg_cost_basis", 0) or 0)
        sell = float(p.get("current_sell_price", 0) or 0)
        if qty > 0 and cb > 0 and sell > 0:
            unrealized += (sell - cb) * qty
    realized = _realized_from_ledger(exchange)

    # Account row (7 cols): Portfolio | Total | BuyPower | Holdings | Inv% | Realized | Unrealized
    acct_header = (
        f"{'Portfolio':<9} {'Total':>10} {'BuyPower':>10} {'Holdings':>10} "
        f"{'Inv%':>6} {'Realized':>10} {'Unreal':>10}"
    )
    acct_row = (
        f"{'Kraken':<9} {f'${total:,.2f}':>10} {f'${bp:,.2f}':>10} {f'${holdings:,.2f}':>10} "
        f"{pct:>5.1f}% {_signed_money(realized):>10} {_signed_money(unrealized):>10}"
    )

    lines = ["```", acct_header, acct_row]

    if open_pos:
        lines.append("")
        # Per-coin: coin | $position | %pnl | avg_cost | mid_price | sell_level | #DCAs | next_DCA
        coin_header = (
            f"{'Coin':<5} {'Pos':>9} {'PnL%':>7} {'AvgCost':>11} "
            f"{'Mid':>11} {'Sell':>11} {'DCAs':>5}  {'NextDCA':<14}"
        )
        lines.append(coin_header)
        for sym, p in sorted(open_pos.items()):
            base    = sym.split("_")[0].upper()
            pos_val = float(p.get("value_usd", 0) or 0)
            pnl     = float(p.get("gain_loss_pct_sell", 0) or 0)
            cb      = float(p.get("avg_cost_basis", 0) or 0)
            buy     = float(p.get("current_buy_price", 0) or 0)
            sell    = float(p.get("current_sell_price", 0) or 0)
            mid     = (buy + sell) / 2 if (buy > 0 and sell > 0) else (sell or buy)
            trail   = float(p.get("trail_line", 0) or 0)
            dca_s   = int(p.get("dca_triggered_stages", 0) or 0)
            dca_t   = int(p.get("dca_total_stages", 0) or 0)
            nxt     = str(p.get("next_dca_display", "") or "—")
            pnl_s   = f"{'+' if pnl >= 0 else ''}{pnl:.2f}%"
            dcas_s  = f"{dca_s}/{dca_t}" if dca_t else "—"
            lines.append(
                f"{base:<5} {f'${pos_val:,.2f}':>9} {pnl_s:>7} {_fmt_price(cb):>11} "
                f"{_fmt_price(mid):>11} {_fmt_price(trail):>11} {dcas_s:>5}  {nxt:<14}"
            )

    lines.append("```")
    message = "\n".join(lines)

    n = len(open_pos)
    title = (
        f"Kraken · {n} position{'s' if n != 1 else ''}" if n
        else "Kraken · no positions"
    )
    _fire(title, message, tags="bar_chart", priority="low")


def test_notify():
    """Send a representative fake trade + fake positions summary for review.

    Bypasses the `notifications_enabled` config flag — if ntfy_url is set,
    the test fires regardless. The override is intentionally not restored:
    test_notify is meant to be invoked as a one-shot from a short-lived
    process (e.g. `python3 -c ...`), and the daemon HTTP threads need the
    override to still be in place when they run.
    """
    try:
        from pt_env import PTEnv
        env = PTEnv(os.path.dirname(os.path.abspath(__file__)))
        url = str(env.get_config().get("ntfy_url") or "").strip()
    except Exception:
        url = ""
    if not url:
        log.warning("ntfy_url is not configured — nothing to test")
        return

    global _ntfy_url
    _ntfy_url = lambda: url

    # Fake trade notification (sell, +3.2%)
    notify_trade(
        side="sell", symbol="BTC_USD", qty=0.00124,
        price=98420.0, avg_cost_basis=95300.0, pnl_pct=3.2,
        notional_usd=122.04, tag="TRAIL_SELL", buying_power=1204.50,
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
        "total_account_value": 1066.79,
        "buying_power": 767.59,
        "holdings_sell_value": 299.20,
        "percent_in_trade": 26.4,
    }
    notify_positions_summary(fake_positions, fake_account, exchange="kraken")

    # Let daemon HTTP threads flush before caller exits
    time.sleep(3)


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
