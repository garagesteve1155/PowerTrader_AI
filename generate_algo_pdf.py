#!/usr/bin/env python3
"""
Generate a PDF document explaining the PowerTrader fitting and trading algorithm.
Uses reportlab for PDF layout and matplotlib for charts.
"""
import os, sys, json, math, tempfile
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm, cm
from reportlab.lib.colors import HexColor, black, white
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Image, Table, TableStyle,
    PageBreak, KeepTogether, HRFlowable,
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_JUSTIFY
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
COINS_DIR = os.path.join(BASE_DIR, "state", "coins")
OUT_PATH = os.path.join(BASE_DIR, "PowerTrader_Algorithm.pdf")
TF_CHOICES = ["1hour", "2hour", "4hour", "8hour", "12hour", "1day", "1week"]

# Colors
C_BG = "#0d1117"
C_ACCENT = "#58a6ff"
C_GREEN = "#3fb950"
C_RED = "#f85149"
C_ORANGE = "#d29922"
C_PURPLE = "#bc8cff"
C_MUTED = "#8b949e"
C_BLUE_LINE = "#58a6ff"
C_ORANGE_LINE = "#d29922"

# ─── helpers ───────────────────────────────────────────────────────────────

def load_memories(coin, tf):
    td = _load_training_data(coin)
    data = td.get(tf, {}).get("memories", "")
    entries = data.split("~")
    out = []
    for e in entries:
        parts = e.split("{}")
        pat_str = parts[0].strip()
        pat_vals = [float(x) for x in pat_str.split() if x]
        high = float(parts[1].strip()) if len(parts) > 1 else 0.0
        low = float(parts[2].strip()) if len(parts) > 2 else 0.0
        out.append({"pattern": pat_vals, "high": high, "low": low})
    return out

def _load_training_data(coin):
    path = os.path.join(COINS_DIR, coin, "training_data.json")
    with open(path) as f:
        return json.load(f)

def load_weights(coin, tf, kind=""):
    key = "weights_high" if kind == "high" else ("weights_low" if kind == "low" else "weights")
    td = _load_training_data(coin)
    raw = td.get(tf, {}).get(key, "")
    return [float(x) for x in raw.split() if x]

def load_threshold(coin, tf):
    td = _load_training_data(coin)
    return float(td.get(tf, {}).get("threshold", 1.0))

def load_bounds(coin):
    path = os.path.join(COINS_DIR, coin, "thinker_state.json")
    with open(path) as f:
        data = json.load(f)
    return data.get("low_bound_prices", []), data.get("high_bound_prices", [])

def save_fig(fig, prefix="chart"):
    path = os.path.join(tempfile.gettempdir(), f"pt_{prefix}.png")
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor=C_BG, edgecolor="none")
    plt.close(fig)
    return path

# ─── chart generators ──────────────────────────────────────────────────────

def chart_memory_format():
    fig, ax = plt.subplots(figsize=(7, 2.2), facecolor=C_BG)
    ax.set_facecolor(C_BG)
    ax.axis("off")
    y = 0.5
    box_style = dict(boxstyle="round,pad=0.4", facecolor="#161b22", edgecolor=C_ACCENT, lw=1.2)
    sep_style = dict(boxstyle="round,pad=0.15", facecolor="#161b22", edgecolor=C_MUTED, lw=0.8)
    ax.text(0.05, y, "close_pct_change  next_close_pct", fontsize=8, color=C_ACCENT,
            fontfamily="monospace", va="center", bbox=box_style, transform=ax.transAxes)
    ax.text(0.52, y, "{}", fontsize=9, color=C_MUTED, fontfamily="monospace",
            va="center", bbox=sep_style, transform=ax.transAxes)
    ax.text(0.58, y, "high_pct_change", fontsize=8, color=C_GREEN,
            fontfamily="monospace", va="center", bbox=box_style, transform=ax.transAxes)
    ax.text(0.76, y, "{}", fontsize=9, color=C_MUTED, fontfamily="monospace",
            va="center", bbox=sep_style, transform=ax.transAxes)
    ax.text(0.82, y, "low_pct_change", fontsize=8, color=C_RED,
            fontfamily="monospace", va="center", bbox=box_style, transform=ax.transAxes)
    ax.text(0.05, 0.12, "pattern (close % change)   ↑ predicted future high move   ↑ predicted future low move",
            fontsize=6.5, color=C_MUTED, fontfamily="monospace", transform=ax.transAxes)
    fig.tight_layout()
    return save_fig(fig, "memory_format")

def chart_memory_distribution(coin="BTC"):
    fig, axes = plt.subplots(1, 3, figsize=(7.5, 2.5), facecolor=C_BG)
    for ax in axes:
        ax.set_facecolor(C_BG)
        ax.tick_params(colors=C_MUTED, labelsize=6)
        for spine in ax.spines.values():
            spine.set_color("#30363d")

    memories = load_memories(coin, "4hour")
    patterns = [m["pattern"][-1] for m in memories]
    highs = [m["high"] for m in memories]
    lows = [m["low"] for m in memories]

    axes[0].hist(patterns, bins=40, color=C_ACCENT, alpha=0.7, edgecolor="#0d1117")
    axes[0].set_title("Close % Change", fontsize=7, color=C_ACCENT)
    axes[0].set_xlabel("% change", fontsize=6, color=C_MUTED)

    axes[1].hist(highs, bins=40, color=C_GREEN, alpha=0.7, edgecolor="#0d1117")
    axes[1].set_title("High % Change", fontsize=7, color=C_GREEN)
    axes[1].set_xlabel("% change", fontsize=6, color=C_MUTED)

    axes[2].hist(lows, bins=40, color=C_RED, alpha=0.7, edgecolor="#0d1117")
    axes[2].set_title("Low % Change", fontsize=7, color=C_RED)
    axes[2].set_xlabel("% change", fontsize=6, color=C_MUTED)

    fig.suptitle(f"{coin} 4-hour Memories: Stored Price Move Distributions (n={len(memories)})",
                 fontsize=8, color="white", y=1.02)
    fig.tight_layout()
    return save_fig(fig, "mem_dist")

def chart_matching_example():
    fig, ax = plt.subplots(figsize=(7, 3), facecolor=C_BG)
    ax.set_facecolor(C_BG)
    for spine in ax.spines.values():
        spine.set_color("#30363d")
    ax.tick_params(colors=C_MUTED, labelsize=7)

    np.random.seed(42)
    current = np.array([0.5])
    n_mem = 8
    mem_patterns = current[0] + np.random.randn(n_mem) * 1.5
    diffs = np.abs((np.abs(current[0] - mem_patterns) / ((current[0] + mem_patterns) / 2)) * 100)
    threshold = 40.0
    matches = diffs <= threshold

    x = np.arange(n_mem)
    colors = [C_GREEN if m else "#30363d" for m in matches]
    bars = ax.bar(x, diffs, color=colors, edgecolor="#0d1117", alpha=0.8, width=0.6)
    ax.axhline(threshold, color=C_ORANGE, ls="--", lw=1.5, label=f"Threshold = {threshold:.0f}")
    ax.set_xlabel("Memory Index", fontsize=7, color=C_MUTED)
    ax.set_ylabel("% Difference", fontsize=7, color=C_MUTED)
    ax.set_title("Pattern Matching: Current Candle vs. Stored Memories", fontsize=8, color="white")
    ax.legend(fontsize=7, facecolor="#161b22", edgecolor="#30363d", labelcolor=C_MUTED)

    for i, (d, m) in enumerate(zip(diffs, matches)):
        ax.text(i, d + 1.5, "✓" if m else "✗", ha="center", fontsize=8,
                color=C_GREEN if m else C_RED)

    fig.tight_layout()
    return save_fig(fig, "matching")

def chart_threshold_adaptation():
    fig, ax = plt.subplots(figsize=(7, 2.5), facecolor=C_BG)
    ax.set_facecolor(C_BG)
    for spine in ax.spines.values():
        spine.set_color("#30363d")
    ax.tick_params(colors=C_MUTED, labelsize=7)

    # Simulate threshold adaptation
    np.random.seed(7)
    thresh = 1.0
    thresholds = [thresh]
    match_counts = []
    for _ in range(500):
        n_matches = max(0, int(np.random.poisson(thresh * 3)))
        match_counts.append(n_matches)
        if n_matches > 20:
            thresh -= 0.01 if thresh >= 0.1 else 0.001
        else:
            thresh += 0.01 if thresh >= 0.1 else 0.001
        thresh = max(0.0, min(thresh, 100.0))
        thresholds.append(thresh)

    ax.plot(thresholds, color=C_ACCENT, lw=1.2, label="Threshold")
    ax.axhline(20, color=C_ORANGE, ls=":", lw=0.8, alpha=0.5)
    ax.set_xlabel("Training Iteration", fontsize=7, color=C_MUTED)
    ax.set_ylabel("Threshold", fontsize=7, color=C_MUTED)
    ax.set_title("Adaptive Threshold: Auto-tunes to maintain ~20 matches per pattern", fontsize=8, color="white")
    ax.legend(fontsize=7, facecolor="#161b22", edgecolor="#30363d", labelcolor=C_MUTED)
    fig.tight_layout()
    return save_fig(fig, "threshold")

def chart_weight_adjustment():
    fig, ax = plt.subplots(figsize=(7, 2.8), facecolor=C_BG)
    ax.set_facecolor(C_BG)
    for spine in ax.spines.values():
        spine.set_color("#30363d")
    ax.tick_params(colors=C_MUTED, labelsize=7)

    steps = np.arange(20)
    np.random.seed(3)
    weight = 1.0
    weights = [weight]
    for i in range(19):
        actual = np.random.randn() * 2
        predicted = np.random.randn() * 1.5
        if actual > predicted * 1.1:
            weight = min(2.0, weight + 0.25)
        elif actual < predicted * 0.9:
            weight = max(0.0, weight - 0.25)
        weights.append(weight)

    ax.step(steps, weights, where="mid", color=C_ACCENT, lw=1.5)
    ax.fill_between(steps, weights, step="mid", alpha=0.15, color=C_ACCENT)
    ax.axhline(1.0, color=C_MUTED, ls=":", lw=0.8)
    ax.axhline(2.0, color=C_GREEN, ls=":", lw=0.8, alpha=0.4)
    ax.axhline(0.0, color=C_RED, ls=":", lw=0.8, alpha=0.4)
    ax.set_xlabel("Candle Steps (during training)", fontsize=7, color=C_MUTED)
    ax.set_ylabel("Memory Weight", fontsize=7, color=C_MUTED)
    ax.set_title("Weight Adjustment: Reinforced when prediction matches reality, weakened otherwise",
                 fontsize=8, color="white")
    ax.set_ylim(-0.3, 2.5)
    ax.text(19.5, 2.05, "cap=2.0", fontsize=6, color=C_GREEN, ha="right")
    ax.text(19.5, 0.05, "floor=0.0", fontsize=6, color=C_RED, ha="right")
    fig.tight_layout()
    return save_fig(fig, "weights")

def chart_signal_pipeline():
    fig, ax = plt.subplots(figsize=(7.5, 3.5), facecolor=C_BG)
    ax.set_facecolor(C_BG)
    ax.axis("off")
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 5)

    box_kw = dict(boxstyle="round,pad=0.5", lw=1.5)
    def draw_box(x, y, text, color, w=1.8):
        ax.text(x, y, text, ha="center", va="center", fontsize=7, color="white",
                fontfamily="monospace", bbox=dict(**box_kw, facecolor="#161b22", edgecolor=color))
    def arrow(x1, y1, x2, y2):
        ax.annotate("", xy=(x2, y2), xytext=(x1, y1),
                     arrowprops=dict(arrowstyle="->", color=C_MUTED, lw=1.2))

    # Row 1: timeframes
    tfs = ["1h","2h","4h","8h","12h","1d","1w"]
    for i, tf in enumerate(tfs):
        draw_box(1.2 + i * 1.15, 4.3, tf, C_ACCENT, 0.9)
        arrow(1.2 + i * 1.15, 3.9, 1.2 + i * 1.15, 3.3)

    # Row 2: per-TF signal
    for i in range(7):
        draw_box(1.2 + i * 1.15, 2.9, "LONG\nSHORT\nWITHIN", C_PURPLE, 0.9)

    # Converge arrows
    for i in range(7):
        arrow(1.2 + i * 1.15, 2.5, 5.0, 1.6)

    # Row 3: aggregation
    draw_box(5.0, 1.3, "Count LONG (0–7)\nCount SHORT (0–7)", C_GREEN)

    # Row 4: signal files
    arrow(5.0, 0.8, 3.5, 0.2)
    arrow(5.0, 0.8, 6.5, 0.2)
    ax.text(3.5, 0.15, "long_dca_signal.txt", fontsize=7, color=C_GREEN,
            ha="center", fontfamily="monospace",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="#161b22", edgecolor=C_GREEN, lw=1))
    ax.text(6.5, 0.15, "short_dca_signal.txt", fontsize=7, color=C_RED,
            ha="center", fontfamily="monospace",
            bbox=dict(boxstyle="round,pad=0.3", facecolor="#161b22", edgecolor=C_RED, lw=1))

    fig.tight_layout()
    return save_fig(fig, "pipeline")

def chart_bounds_example(coin="BTC"):
    try:
        low_bounds, high_bounds = load_bounds(coin)
    except:
        return None
    try:
        price = float(open(os.path.join(COINS_DIR, coin, f"{coin}_current_price.txt")).read().strip())
    except:
        price = (low_bounds[0] + high_bounds[0]) / 2

    fig, ax = plt.subplots(figsize=(7, 3.5), facecolor=C_BG)
    ax.set_facecolor(C_BG)
    for spine in ax.spines.values():
        spine.set_color("#30363d")
    ax.tick_params(colors=C_MUTED, labelsize=7)

    x = np.arange(1, 8)
    ax.plot(x, sorted(low_bounds, reverse=True), "o-", color=C_BLUE_LINE, lw=1.5,
            markersize=5, label="Low Bounds (LONG lines)")
    ax.plot(x, sorted(high_bounds), "o-", color=C_ORANGE_LINE, lw=1.5,
            markersize=5, label="High Bounds (SHORT lines)")
    ax.axhline(price, color="white", ls="--", lw=1, alpha=0.7, label=f"Current ${price:,.0f}")

    # Label N1..N7
    for i, (lb, hb) in enumerate(zip(sorted(low_bounds, reverse=True), sorted(high_bounds))):
        ax.text(i+1, lb, f"  N{i+1}", fontsize=6, color=C_BLUE_LINE, va="center")
        ax.text(i+1, hb, f"  N{i+1}", fontsize=6, color=C_ORANGE_LINE, va="center")

    ax.set_xlabel("Level (N1 = closest to price, N7 = furthest)", fontsize=7, color=C_MUTED)
    ax.set_ylabel("Price ($)", fontsize=7, color=C_MUTED)
    ax.set_title(f"{coin} Live Prediction Bounds — {len(low_bounds)} timeframes active",
                 fontsize=8, color="white")
    ax.legend(fontsize=7, facecolor="#161b22", edgecolor="#30363d", labelcolor=C_MUTED, loc="lower left")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"${v:,.0f}"))
    fig.tight_layout()
    return save_fig(fig, "bounds")

def chart_trailing_pm():
    fig, ax = plt.subplots(figsize=(7, 3), facecolor=C_BG)
    ax.set_facecolor(C_BG)
    for spine in ax.spines.values():
        spine.set_color("#30363d")
    ax.tick_params(colors=C_MUTED, labelsize=7)

    np.random.seed(12)
    entry = 100.0
    pm_start = entry * 1.03  # 3% PM start
    gap = 0.005  # 0.5% gap

    # Simulate price path
    prices = [entry]
    for _ in range(80):
        prices.append(prices[-1] * (1 + np.random.randn() * 0.005 + 0.0008))

    prices = np.array(prices)
    t = np.arange(len(prices))

    trail_line = np.full_like(prices, pm_start)
    peak = 0.0
    active = False
    was_above = False
    sell_idx = None

    for i in range(len(prices)):
        if not active and prices[i] >= pm_start:
            active = True
            peak = prices[i]
        if active:
            if prices[i] > peak:
                peak = prices[i]
            new_line = peak * (1 - gap)
            if new_line < pm_start:
                new_line = pm_start
            trail_line[i] = new_line
            if was_above and prices[i] < new_line and sell_idx is None:
                sell_idx = i
            was_above = prices[i] >= new_line
        else:
            trail_line[i] = pm_start

    ax.plot(t, prices, color=C_ACCENT, lw=1.2, label="Price")
    ax.plot(t, trail_line, color=C_ORANGE, lw=1.2, ls="--", label="Trailing PM Line")
    ax.axhline(entry, color=C_MUTED, ls=":", lw=0.8, label=f"Entry ${entry:.0f}")
    ax.axhline(pm_start, color=C_GREEN, ls=":", lw=0.8, alpha=0.4, label=f"PM Start +3%")
    if sell_idx:
        ax.axvline(sell_idx, color=C_RED, ls="-", lw=1.5, alpha=0.6)
        ax.scatter([sell_idx], [prices[sell_idx]], color=C_RED, s=60, zorder=5)
        ax.text(sell_idx + 1, prices[sell_idx], "  SELL", color=C_RED, fontsize=7, va="center")

    ax.set_xlabel("Time", fontsize=7, color=C_MUTED)
    ax.set_ylabel("Price", fontsize=7, color=C_MUTED)
    ax.set_title("Trailing Profit Margin: Line follows peak up, sells when price crosses below",
                 fontsize=8, color="white")
    ax.legend(fontsize=6.5, facecolor="#161b22", edgecolor="#30363d", labelcolor=C_MUTED, loc="upper left")
    fig.tight_layout()
    return save_fig(fig, "trailing")

def chart_dca_levels():
    fig, ax = plt.subplots(figsize=(7, 3), facecolor=C_BG)
    ax.set_facecolor(C_BG)
    for spine in ax.spines.values():
        spine.set_color("#30363d")
    ax.tick_params(colors=C_MUTED, labelsize=7)

    entry = 100.0
    hard_levels = [-5, -10, -20, -30, -40, -50]
    neural_levels = [4, 5, 6, 7]  # N4, N5, N6, N7 for start_level=3

    # Simulated price decline
    t = np.linspace(0, 1, 100)
    prices = entry * (1 - 0.55 * t + 0.05 * np.sin(t * 20))

    ax.plot(t * 100, prices, color=C_ACCENT, lw=1.2, label="Price")
    ax.axhline(entry, color=C_MUTED, ls=":", lw=0.8)

    for i, hl in enumerate(hard_levels):
        level_price = entry * (1 + hl / 100)
        ax.axhline(level_price, color=C_RED, ls="--", lw=0.7, alpha=0.5)
        ax.text(101, level_price, f"  {hl}%", fontsize=6, color=C_RED, va="center")
        if i < len(neural_levels):
            ax.text(-5, level_price, f"N{neural_levels[i]}  ", fontsize=6, color=C_BLUE_LINE,
                    va="center", ha="right")

    # Mark DCA buys
    dca_prices_approx = [entry * (1 + hl / 100) for hl in hard_levels[:4]]
    dca_times = [20, 35, 55, 75]
    for dt, dp in zip(dca_times, dca_prices_approx):
        ax.scatter([dt], [dp], color=C_GREEN, s=50, zorder=5, marker="^")
        ax.text(dt, dp + 2, "DCA", fontsize=6, color=C_GREEN, ha="center")

    ax.set_xlabel("Time", fontsize=7, color=C_MUTED)
    ax.set_ylabel("Price", fontsize=7, color=C_MUTED)
    ax.set_title("DCA Triggers: Hard % levels (red) OR Neural levels (blue) — whichever hits first",
                 fontsize=8, color="white")
    ax.legend(fontsize=6.5, facecolor="#161b22", edgecolor="#30363d", labelcolor=C_MUTED)
    fig.tight_layout()
    return save_fig(fig, "dca")

def chart_real_thresholds():
    fig, ax = plt.subplots(figsize=(7, 2.5), facecolor=C_BG)
    ax.set_facecolor(C_BG)
    for spine in ax.spines.values():
        spine.set_color("#30363d")
    ax.tick_params(colors=C_MUTED, labelsize=7)

    coins = ["BTC", "ETH", "SOL", "XRP", "DOGE"]
    for coin in coins:
        vals = []
        labels = []
        for tf in TF_CHOICES:
            try:
                vals.append(load_threshold(coin, tf))
                labels.append(tf)
            except:
                pass
        if vals:
            ax.plot(labels, vals, "o-", lw=1.2, markersize=4, label=coin)

    ax.set_xlabel("Timeframe", fontsize=7, color=C_MUTED)
    ax.set_ylabel("Threshold", fontsize=7, color=C_MUTED)
    ax.set_title("Live Adaptive Thresholds Across Coins & Timeframes", fontsize=8, color="white")
    ax.legend(fontsize=6.5, facecolor="#161b22", edgecolor="#30363d", labelcolor=C_MUTED, ncol=5)
    fig.tight_layout()
    return save_fig(fig, "real_thresh")

def chart_memory_counts():
    fig, ax = plt.subplots(figsize=(7, 2.5), facecolor=C_BG)
    ax.set_facecolor(C_BG)
    for spine in ax.spines.values():
        spine.set_color("#30363d")
    ax.tick_params(colors=C_MUTED, labelsize=7)

    coins = ["BTC", "ETH", "SOL", "XRP", "DOGE"]
    width = 0.15
    x = np.arange(len(TF_CHOICES))
    for i, coin in enumerate(coins):
        counts = []
        for tf in TF_CHOICES:
            try:
                counts.append(len(load_memories(coin, tf)))
            except:
                counts.append(0)
        ax.bar(x + i * width, counts, width, label=coin, alpha=0.8)

    ax.set_xticks(x + width * 2)
    ax.set_xticklabels(TF_CHOICES, fontsize=6)
    ax.set_ylabel("Memory Count", fontsize=7, color=C_MUTED)
    ax.set_title("Stored Memories per Coin & Timeframe", fontsize=8, color="white")
    ax.legend(fontsize=6.5, facecolor="#161b22", edgecolor="#30363d", labelcolor=C_MUTED, ncol=5)
    fig.tight_layout()
    return save_fig(fig, "mem_counts")


# ─── PDF document ──────────────────────────────────────────────────────────

def build_pdf():
    doc = SimpleDocTemplate(
        OUT_PATH, pagesize=A4,
        leftMargin=20*mm, rightMargin=20*mm,
        topMargin=18*mm, bottomMargin=18*mm,
    )

    styles = getSampleStyleSheet()
    s_title = ParagraphStyle("DocTitle", parent=styles["Title"], fontSize=22,
                             spaceAfter=6*mm, textColor=HexColor(C_ACCENT))
    s_h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=16,
                          spaceBefore=8*mm, spaceAfter=3*mm, textColor=HexColor(C_ACCENT))
    s_h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=12,
                          spaceBefore=5*mm, spaceAfter=2*mm, textColor=HexColor(C_PURPLE))
    s_body = ParagraphStyle("Body", parent=styles["Normal"], fontSize=9.5,
                            leading=14, alignment=TA_JUSTIFY, spaceAfter=2*mm)
    s_mono = ParagraphStyle("Mono", parent=styles["Normal"], fontSize=8,
                            fontName="Courier", leading=11, spaceAfter=2*mm,
                            textColor=HexColor(C_MUTED))
    s_caption = ParagraphStyle("Caption", parent=styles["Normal"], fontSize=7.5,
                               alignment=TA_CENTER, textColor=HexColor(C_MUTED),
                               spaceBefore=1*mm, spaceAfter=3*mm)
    s_bullet = ParagraphStyle("Bullet", parent=s_body, leftIndent=15,
                              bulletIndent=5, spaceBefore=1*mm, spaceAfter=1*mm)

    story = []
    W = doc.width

    def img(path, w=W):
        if path and os.path.exists(path):
            return Image(path, width=w, height=w * 0.42)
        return Spacer(1, 5*mm)

    # ── Title ──
    story.append(Paragraph("PowerTrader: Fitting &amp; Trading Algorithm", s_title))
    story.append(Paragraph("A technical reference derived entirely from source code analysis", s_caption))
    story.append(HRFlowable(width="100%", color=HexColor("#30363d")))
    story.append(Spacer(1, 5*mm))

    # ── 1. Overview ──
    story.append(Paragraph("1. System Overview", s_h1))
    story.append(Paragraph(
        "PowerTrader is a pattern-matching trading system. It has three core components that run "
        "as separate processes and communicate through the filesystem:", s_body))
    story.append(Paragraph(
        "<b>Trainer</b> (<i>pt_trainer.py</i>) — Fetches historical candle data from KuCoin for each of "
        "7 timeframes. Walks through price history candle-by-candle, building a database of "
        "\"memories\" — observed price patterns paired with what happened next. Adjusts memory "
        "weights based on prediction accuracy. Adapts a matching threshold to control selectivity.",
        s_bullet))
    story.append(Paragraph(
        "<b>Thinker</b> (<i>pt_thinker.py</i>) — The real-time inference engine (\"neural runner\"). "
        "Continuously cycles through all 7 timeframes per coin. For each, it compares the current "
        "candle's shape to stored memories. When matches are found, it averages their weighted "
        "predictions to estimate where price will go. Produces a LONG, SHORT, or WITHIN signal per "
        "timeframe. Counts total LONGs and SHORTs across all 7 to produce an aggregate signal strength (0–7).",
        s_bullet))
    story.append(Paragraph(
        "<b>Trader</b> (<i>pt_trader.py</i>) — Reads the signal files written by the Thinker. Uses them "
        "as a gate: only enters new positions when the long signal is strong enough and the short "
        "signal is zero. Manages open positions with configurable DCA (dollar-cost averaging) at "
        "hard percentage loss levels or neural price levels, and exits via a trailing profit margin.",
        s_bullet))
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(
        "The key insight: no machine learning model is trained in the traditional sense. Instead, the "
        "system stores raw historical patterns and retrieves them by similarity — closer to a "
        "nearest-neighbor lookup than a neural network. The \"neural\" name is a branding choice, not "
        "an architecture description.", s_body))

    # ── 2. Training / Fitting ──
    story.append(PageBreak())
    story.append(Paragraph("2. Training: Building the Memory Database", s_h1))

    story.append(Paragraph("2.1 Data Acquisition", s_h2))
    story.append(Paragraph(
        "The trainer fetches historical OHLCV candle data from KuCoin's public API for the coin being "
        "trained (e.g., BTC-USDT). It requests up to 100,000 candles in paginated batches of ~1,500, "
        "working backwards in time. This is done independently for each of the 7 timeframes:", s_body))
    story.append(Paragraph(
        "<font face='Courier' size='8'>1hour · 2hour · 4hour · 8hour · 12hour · 1day · 1week</font>",
        s_mono))
    story.append(Paragraph(
        "Each candle provides: open, close, high, low, volume. The trainer computes percentage "
        "change features for each:", s_body))
    story.append(Paragraph(
        "<font face='Courier' size='8'>close_pct = 100 × (close - open) / open</font><br/>"
        "<font face='Courier' size='8'>high_pct  = 100 × (high - open) / open</font><br/>"
        "<font face='Courier' size='8'>low_pct   = 100 × (low - open) / open</font>",
        s_mono))
    story.append(Paragraph(
        "This normalization is critical — by converting absolute prices to percentage changes, "
        "the same memory can match patterns whether BTC is at $30k or $90k.", s_body))

    story.append(Paragraph("2.2 Memory Structure", s_h2))
    story.append(Paragraph(
        "Each memory is a text record stored in <font face='Courier'>memories_{tf}.txt</font>, separated "
        "by <font face='Courier'>~</font>. The format is:", s_body))
    story.append(img(chart_memory_format(), W))
    story.append(Paragraph("Fig 1. Memory record format: pattern features separated from prediction targets by {} delimiters", s_caption))
    story.append(Paragraph(
        "The <b>pattern</b> portion contains the close percentage change(s) of the observed candle(s) — "
        "this is what gets matched against. The last element is the \"next candle\" close % change that "
        "was actually observed. The <b>high</b> and <b>low</b> fields store what the next candle's "
        "high and low percentage changes were — these are the prediction targets.", s_body))
    story.append(Paragraph(
        "With <font face='Courier'>number_of_candles = [2]</font> (the current setting), each pattern "
        "consists of a single candle's close % change plus the next candle's close % change. The system "
        "uses these as a 1-candle lookback context.", s_body))

    story.append(Paragraph("2.3 The Training Loop", s_h2))
    story.append(Paragraph(
        "The trainer walks through historical price data candle-by-candle, from past to present. "
        "At each step it:", s_body))
    story.append(Paragraph("1. Extracts the current candle's close % change as the <b>query pattern</b>.", s_bullet))
    story.append(Paragraph("2. Compares this pattern against every stored memory using <b>percentage difference</b>.", s_bullet))
    story.append(Paragraph("3. If any memory's pattern is within the <b>adaptive threshold</b>, it's a \"match\".", s_bullet))
    story.append(Paragraph("4. <b>If matches exist</b>: the system computes weighted-average predicted moves, "
                           "then checks how the <i>actual</i> next candle compared. It adjusts the weights of "
                           "matching memories up or down based on accuracy.", s_bullet))
    story.append(Paragraph("5. <b>If no matches exist</b>: the current candle + what actually happened next "
                           "is stored as a <b>new memory</b> with weight 1.0.", s_bullet))
    story.append(Paragraph("6. Advance to the next candle and repeat.", s_bullet))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(
        "The training processes all 7 timeframes sequentially. For each timeframe, it does an initial "
        "\"warm-up\" pass on the 1-hour data (building context), then two passes on the target timeframe — "
        "first on the early half, then the full dataset.", s_body))

    story.append(Paragraph("2.4 Pattern Matching (Similarity Metric)", s_h2))
    story.append(Paragraph(
        "The similarity between the current candle and a stored memory is computed as:", s_body))
    story.append(Paragraph(
        "<font face='Courier' size='8'>difference = |current - memory| / ((current + memory) / 2) × 100</font>",
        s_mono))
    story.append(Paragraph(
        "This is a symmetric percentage difference (similar to MAPE). A difference of 0 means "
        "identical candle shapes; higher values mean less similarity. The candle is a \"match\" if "
        "the difference is ≤ the adaptive threshold for that timeframe.", s_body))
    story.append(img(chart_matching_example(), W))
    story.append(Paragraph("Fig 2. Pattern matching: only memories within the threshold (green) contribute to the prediction", s_caption))

    story.append(PageBreak())
    story.append(Paragraph("2.5 Adaptive Threshold", s_h2))
    story.append(Paragraph(
        "The threshold is not fixed — it self-adjusts during training to maintain a useful number of "
        "matches per pattern. The logic:", s_body))
    story.append(Paragraph(
        "• If more than 20 memories match → threshold <b>decreases</b> (become more selective)<br/>"
        "• If 20 or fewer match → threshold <b>increases</b> (become less selective)<br/>"
        "• Step size: ±0.01 when threshold ≥ 0.1, ±0.001 below 0.1<br/>"
        "• Bounded between 0.0 and 100.0",
        s_body))
    story.append(Paragraph(
        "This creates a self-regulating system: coins with many similar candle shapes (low volatility) "
        "get a tight threshold, while coins with diverse patterns get a wider one.", s_body))
    story.append(img(chart_threshold_adaptation(), W))
    story.append(Paragraph("Fig 3. Threshold converges to a level that maintains a moderate number of matches", s_caption))

    # Real threshold data
    story.append(img(chart_real_thresholds(), W))
    story.append(Paragraph("Fig 4. Live thresholds from your trained models — shorter timeframes have wider thresholds (more pattern diversity)", s_caption))

    story.append(Paragraph("2.6 Weight Adjustment", s_h2))
    story.append(Paragraph(
        "When memories match the current candle, the trainer checks what actually happened vs. what "
        "the memory predicted. Three independent weights are adjusted:", s_body))
    story.append(Paragraph(
        "<b>Close weight</b> — stored in <font face='Courier'>memory_weights_{tf}.txt</font><br/>"
        "<b>High weight</b> — stored in <font face='Courier'>memory_weights_high_{tf}.txt</font><br/>"
        "<b>Low weight</b> — stored in <font face='Courier'>memory_weights_low_{tf}.txt</font>",
        s_body))
    story.append(Paragraph(
        "For each matching memory, the adjustment rule is:<br/><br/>"
        "• If actual move > predicted move × 1.1 (underpredicted by 10%+): weight += 0.25<br/>"
        "• If actual move &lt; predicted move × 0.9 (overpredicted by 10%+): weight −= 0.25<br/>"
        "• Otherwise (prediction within 10% of actual): weight unchanged<br/><br/>"
        "Close weights are clamped to [−2.0, 2.0]. High/low weights are clamped to [0.0, 2.0]. "
        "All new memories start with weight 1.0.",
        s_body))
    story.append(img(chart_weight_adjustment(), W))
    story.append(Paragraph("Fig 5. Weight evolution: accurate memories are reinforced, poor predictions are dampened", s_caption))

    # Memory counts
    story.append(img(chart_memory_counts(), W))
    story.append(Paragraph("Fig 6. Actual memory counts across your trained coins — typically 500–650 per timeframe", s_caption))

    # ── 3. Inference ──
    story.append(PageBreak())
    story.append(Paragraph("3. Inference: From Candles to Trading Signals", s_h1))

    story.append(Paragraph("3.1 The Thinker Loop", s_h2))
    story.append(Paragraph(
        "The Thinker (<font face='Courier'>pt_thinker.py</font>) runs continuously, cycling through "
        "each configured coin. For each coin, it steps through the 7 timeframes one at a time. "
        "After completing a full sweep of all 7, it produces output signals.", s_body))
    story.append(Paragraph(
        "For each timeframe step, the Thinker:", s_body))
    story.append(Paragraph("1. Fetches the most recent candle from KuCoin.", s_bullet))
    story.append(Paragraph("2. Computes the candle's close % change: <font face='Courier'>100 × (close − open) / open</font>.", s_bullet))
    story.append(Paragraph("3. Loads the stored memories and weights for this timeframe.", s_bullet))
    story.append(Paragraph("4. Reads the adaptive threshold from <font face='Courier'>neural_perfect_threshold_{tf}.txt</font>.", s_bullet))
    story.append(Paragraph("5. Compares the current candle against each stored memory using the same "
                           "percentage-difference metric as training.", s_bullet))
    story.append(Paragraph("6. Collects all matching memories (difference ≤ threshold) and their associated "
                           "weighted predictions.", s_bullet))

    story.append(Paragraph("3.2 Computing Predicted Moves", s_h2))
    story.append(Paragraph(
        "When matching memories are found, the Thinker computes three weighted averages:", s_body))
    story.append(Paragraph(
        "<font face='Courier' size='8'>final_moves = Σ(pattern_close_pct × close_weight) / n_matches</font><br/>"
        "<font face='Courier' size='8'>high_final_moves = Σ(high_pct × high_weight) / n_matches</font><br/>"
        "<font face='Courier' size='8'>low_final_moves = Σ(low_pct × low_weight) / n_matches</font>",
        s_mono))
    story.append(Paragraph(
        "Zero-weight memories are excluded from their respective averages. The predicted future prices are:", s_body))
    story.append(Paragraph(
        "<font face='Courier' size='8'>predicted_high = current_close + (current_close × high_final_moves)</font><br/>"
        "<font face='Courier' size='8'>predicted_low  = current_close + (current_close × low_final_moves)</font>",
        s_mono))

    story.append(Paragraph("3.3 Price Bounds &amp; Signal Classification", s_h2))
    story.append(Paragraph(
        "The predicted prices are converted to <b>bounds</b> with a 0.5% distance margin:", s_body))
    story.append(Paragraph(
        "<font face='Courier' size='8'>low_bound  = predicted_low  − (predicted_low × 0.005)</font><br/>"
        "<font face='Courier' size='8'>high_bound = predicted_high + (predicted_high × 0.005)</font>",
        s_mono))
    story.append(Paragraph(
        "The bounds are then sorted across all 7 timeframes and spread apart to ensure a minimum 0.25% "
        "gap between adjacent levels. This creates a ladder of N1–N7 price levels for both longs and shorts. "
        "The signal for each timeframe is:", s_body))
    story.append(Paragraph(
        "• <b>LONG</b> — current price is below the low bound (price is cheap relative to prediction)<br/>"
        "• <b>SHORT</b> — current price is above the high bound (price is expensive relative to prediction)<br/>"
        "• <b>WITHIN</b> — current price is between the bounds (no strong directional signal)<br/>"
        "• <b>INACTIVE</b> — no matching memories were found for this timeframe",
        s_body))

    story.append(img(chart_bounds_example("BTC"), W))
    story.append(Paragraph("Fig 7. Live BTC prediction bounds — blue (LONG) lines below price, orange (SHORT) lines above", s_caption))

    story.append(Paragraph("3.4 Signal Aggregation", s_h2))
    story.append(Paragraph(
        "After completing a full sweep of all 7 timeframes, the Thinker counts the total number of "
        "LONG and SHORT signals and writes them to files:", s_body))
    story.append(Paragraph(
        "<font face='Courier' size='8'>long_dca_signal.txt  → count of timeframes signaling LONG  (0–7)</font><br/>"
        "<font face='Courier' size='8'>short_dca_signal.txt → count of timeframes signaling SHORT (0–7)</font>",
        s_mono))
    story.append(Paragraph(
        "A value of 7 means all timeframes agree — the strongest possible signal. A value of 0 means "
        "no timeframes are signaling in that direction.", s_body))
    story.append(img(chart_signal_pipeline(), W * 1.05))
    story.append(Paragraph("Fig 8. Signal pipeline: 7 independent timeframe assessments aggregate into a single strength score", s_caption))

    # Memory distribution
    story.append(img(chart_memory_distribution(), W))
    story.append(Paragraph("Fig 9. Distribution of stored prediction targets in BTC 4h memories — "
                           "the raw material for weighted averaging", s_caption))

    # ── 4. Trading ──
    story.append(PageBreak())
    story.append(Paragraph("4. Trading: From Signals to Positions", s_h1))

    story.append(Paragraph("4.1 Entry Gate", s_h2))
    story.append(Paragraph(
        "The Trader reads the signal files and applies a strict gate before entering any new position:", s_body))
    story.append(Paragraph(
        "<font face='Courier' size='8'>ENTER if: long_signal ≥ TRADE_START_LEVEL  AND  short_signal == 0</font>",
        s_mono))
    story.append(Paragraph(
        "<font face='Courier'>TRADE_START_LEVEL</font> is configurable (default 4, range 1–7). "
        "A setting of 4 means at least 4 out of 7 timeframes must be signaling LONG, and <i>zero</i> "
        "must be signaling SHORT. This is deliberately conservative — it only trades when there is "
        "broad consensus across timeframes and no contradicting signals.", s_body))

    story.append(Paragraph("4.2 Position Sizing", s_h2))
    story.append(Paragraph(
        "Each new position is sized as a percentage of total account value:", s_body))
    story.append(Paragraph(
        "<font face='Courier' size='8'>allocation = total_account_value × (start_allocation_pct / 100)</font>",
        s_mono))
    story.append(Paragraph(
        "The default <font face='Courier'>start_allocation_pct</font> is 0.5%, meaning a $10,000 account "
        "would start each position at $50. This conservative sizing allows the DCA mechanism to scale "
        "in with larger amounts if the price drops.", s_body))

    story.append(Paragraph("4.3 Dollar-Cost Averaging (DCA)", s_h2))
    story.append(Paragraph(
        "If an open position goes into loss, the Trader may add to it (\"DCA\") at predefined trigger levels. "
        "Two independent triggers exist — whichever fires first activates the DCA:", s_body))
    story.append(Paragraph(
        "<b>Hard % triggers</b> — fixed loss percentages from cost basis: [-5%, -10%, -20%, -30%, -40%, -50%, ...]<br/>"
        "<b>Neural level triggers</b> — when the long signal reaches a higher neural level AND price has "
        "actually reached the corresponding neural price line. For <font face='Courier'>trade_start_level=4</font>, "
        "the neural DCA stages are N5, N6, N7 (the levels below the start level).",
        s_body))
    story.append(Paragraph(
        "Each DCA buy is sized at: <font face='Courier'>current_position_value × dca_multiplier</font> "
        "(default 2×). This means each DCA buy is larger than the previous, heavily weighting the "
        "lower prices — a classic Martingale-influenced approach that rapidly lowers the average entry.", s_body))
    story.append(Paragraph(
        "Rate limit: maximum <font face='Courier'>max_dca_buys_per_24h</font> DCA buys per coin per rolling 24-hour window.",
        s_body))
    story.append(img(chart_dca_levels(), W))
    story.append(Paragraph("Fig 10. DCA trigger levels: hard % (red) and neural (blue) — first to fire triggers the buy", s_caption))

    story.append(Paragraph("4.4 Trailing Profit Margin (Exit)", s_h2))
    story.append(Paragraph(
        "Exits are controlled by a trailing profit margin mechanism — there is no fixed take-profit target. "
        "The system:", s_body))
    story.append(Paragraph(
        "1. Computes a <b>PM start line</b> above the average entry: cost_basis × (1 + pm_start_pct%). "
        "Different thresholds for positions with DCA vs. without (default: +3% without DCA, +3% with DCA).",
        s_bullet))
    story.append(Paragraph(
        "2. When price rises above the PM start line, <b>trailing activates</b>. The system records the peak price.",
        s_bullet))
    story.append(Paragraph(
        "3. The trailing line = peak × (1 − trailing_gap_pct%). Default gap is 0.1%, so the line "
        "stays very close to the peak.",
        s_bullet))
    story.append(Paragraph(
        "4. As price continues up, the peak and trailing line ratchet up with it. The line can never move down.",
        s_bullet))
    story.append(Paragraph(
        "5. <b>SELL triggers</b> when price drops from above the trailing line to below it. This means "
        "the system captures most of an upward move, only giving back the trailing gap amount.",
        s_bullet))
    story.append(img(chart_trailing_pm(), W))
    story.append(Paragraph("Fig 11. Trailing PM: line follows price up, sell triggers on the first cross below", s_caption))

    # ── 5. End-to-End Example ──
    story.append(PageBreak())
    story.append(Paragraph("5. End-to-End Example", s_h1))
    story.append(Paragraph(
        "Here is a complete walkthrough of how a trade might unfold for BTC:", s_body))

    # Build a table for the example
    example_data = [
        ["Step", "Action", "Detail"],
        ["1", "Thinker scans BTC",
         "Current 1h candle: open=$77,500, close=$77,200 → pct_change = −0.39%. "
         "Compares against 568 stored 1h memories. With threshold 35.6, finds ~15 matches. "
         "Weighted average predicts: high +0.8%, low −1.2%."],
        ["2", "Repeat × 7 TFs",
         "Thinker completes all 7 timeframes. 5 timeframes signal LONG, 0 signal SHORT. "
         "Writes long_dca_signal.txt = 5, short_dca_signal.txt = 0."],
        ["3", "Trader reads signals",
         "With trade_start_level = 4: long (5) ≥ 4 ✓, short (0) == 0 ✓. Entry gate PASSES."],
        ["4", "Initial buy",
         "Account = $10,000. allocation = $10,000 × 0.5% = $50. "
         "Buys $50 of BTC at $77,200."],
        ["5", "Price drops −6%",
         "BTC falls to $72,568. Hard DCA trigger at −5% fires. "
         "Current position value ≈ $47. DCA buy = $47 × 2 = $94 at $72,568. "
         "New avg entry ≈ $73,959."],
        ["6", "Price recovers",
         "BTC rises to $76,300. Position PnL = +3.2%. "
         "PM start line (with DCA) = $73,959 × 1.03 = $76,178. "
         "Price > PM line → trailing ACTIVATES. Peak = $76,300."],
        ["7", "Trailing ratchets up",
         "BTC reaches $77,100. Peak = $77,100. "
         "Trail line = $77,100 × (1 − 0.001) = $77,023."],
        ["8", "Exit",
         "BTC pulls back to $76,950 (below $77,023). "
         "TRAIL_SELL fires. Position closed at ~+4.0% profit."],
    ]

    t = Table(example_data, colWidths=[30, 80, W - 130])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), HexColor("#161b22")),
        ("TEXTCOLOR", (0, 0), (-1, 0), HexColor(C_ACCENT)),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("LEADING", (0, 0), (-1, -1), 11),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("GRID", (0, 0), (-1, -1), 0.5, HexColor("#30363d")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(t)

    # ── 6. Key Properties ──
    story.append(Spacer(1, 8*mm))
    story.append(Paragraph("6. Key Properties of the Algorithm", s_h1))
    story.append(Paragraph(
        "<b>Non-parametric</b> — No model weights are learned in the ML sense. The system stores raw "
        "patterns and retrieves them by similarity. This is conceptually similar to k-nearest-neighbors "
        "with adaptive k (controlled by the threshold).", s_body))
    story.append(Paragraph(
        "<b>Self-regulating threshold</b> — The match threshold adapts during training so the system "
        "always works with a manageable number of matches, regardless of the coin's volatility profile.",
        s_body))
    story.append(Paragraph(
        "<b>Multi-timeframe consensus</b> — The entry gate requires agreement across multiple "
        "timeframes, acting as a noise filter. Short-term noise on a single timeframe won't trigger "
        "trades; only broad alignment does.", s_body))
    story.append(Paragraph(
        "<b>Asymmetric risk management</b> — Small initial positions with aggressive DCA on drops means "
        "the system sizes up only at lower prices. The trailing exit captures upside without a fixed target.",
        s_body))
    story.append(Paragraph(
        "<b>Staleness protection</b> — Coins that haven't been trained within 14 days have their signals "
        "zeroed out by the Thinker, preventing stale memories from triggering trades.", s_body))
    story.append(Paragraph(
        "<b>Weight decay</b> — Memories that consistently mispredict have their weights reduced toward zero, "
        "effectively silencing them without deleting them from the database. Accurate memories accumulate "
        "weight up to the cap of 2.0, giving them more influence on predictions.", s_body))

    doc.build(story)
    print(f"PDF written to: {OUT_PATH}")

if __name__ == "__main__":
    build_pdf()
