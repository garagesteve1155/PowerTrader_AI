"""
Pure-function port of pt_thinker.py scoring + voting math.

This module exposes the deterministic decision math the production thinker
applies per timeframe, without the file I/O, kline cache, CWD rotation,
or live-API dependencies. Bit-for-bit semantic equivalence with pt_thinker
is the goal — the same training_data + same current (open, close) +
same prior bounds must produce the same long/short signal.

Phases of one full sweep
------------------------
1) score_tf(td_for_tf, open, close, persisted_threshold=None)
     → (high_diff_frac, low_diff_frac, perfect_status)
2) compute_tf_prices(close, high_diff, low_diff, perfect_status)
     → (high_tf_price, low_tf_price)
3) rebuild_bounds(high_tf_prices, low_tf_prices, perfects)
     → (high_bound_prices, low_bound_prices)
4) vote_one(current_price, high_bound, low_bound, high_tf, low_tf)
     → "long" / "short" / "none"

Engine orchestration (driving these per 5min bar across TFs) lives in
backtest/engine.py — added in Phase 3e.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Tuple


def _strip_noise(s: str) -> str:
    """Mirror of pt_thinker._strip_noise — drop list-serialisation artefacts."""
    return (
        s.replace("'", "")
        .replace(",", "")
        .replace('"', "")
        .replace("]", "")
        .replace("[", "")
    )


def _mem_field_pct(line: str, idx: int) -> float:
    """Mirror of pt_thinker._mem_field_pct: field idx as fraction (÷100)."""
    return float(_strip_noise(line.split("{}")[idx]).replace(" ", "")) / 100.0


@dataclass
class ParsedTFMemory:
    """Parsed memory bank for one timeframe."""
    memory_list: list[str]
    weight_list: list[str]
    high_weight_list: list[str]
    low_weight_list: list[str]
    threshold: float


def parse_tf_training_data(td_for_tf: dict) -> ParsedTFMemory:
    """Parse the wire-format training_data section for one timeframe."""
    return ParsedTFMemory(
        memory_list=_strip_noise(td_for_tf.get("memories", "")).split("~"),
        weight_list=_strip_noise(td_for_tf.get("weights", "")).split(" "),
        high_weight_list=_strip_noise(td_for_tf.get("weights_high", "")).split(" "),
        low_weight_list=_strip_noise(td_for_tf.get("weights_low", "")).split(" "),
        threshold=float(td_for_tf.get("threshold", 1.0)),
    )


def score_tf(
    parsed: ParsedTFMemory,
    open_price: float,
    close_price: float,
    persisted_threshold: Optional[float] = None,
) -> Tuple[float, float, str]:
    """
    Match current candle against the memory bank for one timeframe.

    Replicates pt_thinker.py:631-766 numerics exactly:
      - current_candle = 100*(close-open)/open
      - relative-diff match: abs((abs(a-b)/((a+b)/2))*100) <= threshold
      - high/low diffs (as fractions) come from memory fields 1, 2 ÷ 100
      - moves accumulate only when corresponding weight != 0
      - aggregate = mean of accumulated weighted moves

    Returns (high_final_moves_frac, low_final_moves_frac, perfect_status)
    where perfect_status ∈ {"active", "inactive"}.
    """
    if open_price == 0:
        return 0.0, 0.0, "inactive"

    threshold = (
        float(persisted_threshold) if persisted_threshold is not None
        else parsed.threshold
    )
    current_candle = 100.0 * (close_price - open_price) / open_price

    high_moves: list[float] = []
    low_moves: list[float] = []
    any_perfect = False

    for mem_ind, mem_str in enumerate(parsed.memory_list):
        pattern_str = _strip_noise(mem_str.split("{}")[0])
        parts = pattern_str.split(" ")
        try:
            memory_candle = float(parts[0])
        except (ValueError, IndexError):
            continue

        if current_candle == 0.0 and memory_candle == 0.0:
            diff = 0.0
        else:
            denom = (current_candle + memory_candle) / 2.0
            if denom == 0:
                diff = 0.0
            else:
                diff = abs(abs(current_candle - memory_candle) / denom * 100.0)

        if diff <= threshold:
            any_perfect = True
            # Weights are space-separated; tolerate short lists by skipping
            try:
                w = float(parsed.weight_list[mem_ind])
                hw = float(parsed.high_weight_list[mem_ind])
                lw = float(parsed.low_weight_list[mem_ind])
            except (ValueError, IndexError):
                continue

            high_pct = _mem_field_pct(mem_str, 1)
            low_pct = _mem_field_pct(mem_str, 2)

            if hw != 0.0:
                high_moves.append(high_pct * hw)
            if lw != 0.0:
                low_moves.append(low_pct * lw)

    if not any_perfect or not high_moves or not low_moves:
        return 0.0, 0.0, "inactive"

    return sum(high_moves) / len(high_moves), sum(low_moves) / len(low_moves), "active"


def compute_tf_prices(
    close_price: float,
    high_diff: float,
    low_diff: float,
    perfect_status: str,
) -> Tuple[float, float]:
    """
    Translate (close, high_diff, low_diff) into the (high_tf, low_tf) prices
    used downstream by the bound-rebuild stage.

    Mirrors pt_thinker:773-799: if a timeframe is "inactive" both prices
    collapse to the close (so the bound margin will be a flat ±0.5% band
    around it; the rebuild treats inactive TFs as fixed placeholders).

    Clamp: the weighted-memory aggregate can occasionally produce
    `low_diff < -1` (or `high_diff < -1`), giving a non-positive predicted
    price. A negative bound has no economic meaning AND it triggers an
    infinite loop in rebuild_bounds (the gap-walk nudges by *multiplying*
    by 0.9995, which moves negatives toward zero — the wrong direction
    for the sort ordering, so the inversion check fires forever). When
    that happens, treat the prediction as a no-op for the bar by
    collapsing both prices to close_price, matching the inactive path.
    """
    if perfect_status == "inactive":
        return close_price, close_price
    ht = close_price + close_price * high_diff
    lt = close_price + close_price * low_diff
    if ht <= 0 or lt <= 0:
        return close_price, close_price
    return ht, lt


def rebuild_bounds(
    high_tf_prices: list[float],
    low_tf_prices: list[float],
    perfects: list[str],
    distance_pct: float = 0.5,
) -> Tuple[list[float], list[float]]:
    """
    Mirror of pt_thinker.py:1012-1143 bound rebuild.

    Steps:
      1) Apply ±distance_pct margin to each TF's high/low prices.
         Inactive TFs become flat placeholders (0.01 low / 1e17 high) so
         they never trigger a vote downstream.
      2) Sort high bounds ascending and low bounds descending; remember
         the original TF index ordering.
      3) Walk neighbouring sorted pairs. If two consecutive bounds are
         within (0.25 + gap_modifier)% of each other, or the ordering is
         inverted, nudge the second bound by ±0.05% and re-check from
         the same index. gap_modifier grows by 0.25 per advance.
      4) Restore original-TF ordering and return.
    """
    n = len(high_tf_prices)
    if n == 0 or len(low_tf_prices) != n or len(perfects) != n:
        return [], []

    INACTIVE_LOW = 0.01
    INACTIVE_HIGH = 99999999999999999
    margin = distance_pct / 100.0

    low_bounds: list[float] = []
    high_bounds: list[float] = []
    for i in range(n):
        if perfects[i] != "inactive":
            low_bounds.append(low_tf_prices[i] - low_tf_prices[i] * margin)
            high_bounds.append(high_tf_prices[i] + high_tf_prices[i] * margin)
        else:
            low_bounds.append(INACTIVE_LOW)
            high_bounds.append(INACTIVE_HIGH)

    sorted_low = sorted(low_bounds, reverse=True)
    sorted_high = sorted(high_bounds)

    # Original-index lookup (matches pt_thinker semantics: index in unsorted list)
    og_low_index_list = [low_bounds.index(v) for v in sorted_low]
    og_high_index_list = [high_bounds.index(v) for v in sorted_high]

    # Gap enforcement walk
    og_index = 0
    gap_modifier = 0.0
    while og_index < len(sorted_low) - 1:
        skip = (
            sorted_low[og_index] == INACTIVE_LOW
            or sorted_low[og_index + 1] == INACTIVE_LOW
            or sorted_high[og_index] == INACTIVE_HIGH
            or sorted_high[og_index + 1] == INACTIVE_HIGH
        )
        if not skip:
            try:
                low_perc_diff = abs(
                    (sorted_low[og_index] - sorted_low[og_index + 1])
                    / ((sorted_low[og_index] + sorted_low[og_index + 1]) / 2.0)
                ) * 100.0
            except ZeroDivisionError:
                low_perc_diff = 0.0
            try:
                high_perc_diff = abs(
                    (sorted_high[og_index] - sorted_high[og_index + 1])
                    / ((sorted_high[og_index] + sorted_high[og_index + 1]) / 2.0)
                ) * 100.0
            except ZeroDivisionError:
                high_perc_diff = 0.0

            if (
                low_perc_diff < 0.25 + gap_modifier
                or sorted_low[og_index + 1] > sorted_low[og_index]
            ):
                nudged = sorted_low[og_index + 1] - sorted_low[og_index + 1] * 0.0005
                sorted_low[og_index + 1] = nudged
                continue

            if (
                high_perc_diff < 0.25 + gap_modifier
                or sorted_high[og_index + 1] < sorted_high[og_index]
            ):
                nudged = sorted_high[og_index + 1] + sorted_high[og_index + 1] * 0.0005
                sorted_high[og_index + 1] = nudged
                continue

        og_index += 1
        gap_modifier += 0.25

    # Restore to original TF order. When duplicate placeholders collapse
    # the index lookup (e.g. multiple inactive TFs), pad with the original
    # placeholder values so the output stays length n — matches what
    # pt_thinker does via _pad_to_len after this routine.
    out_low: list[float] = []
    out_high: list[float] = []
    for og_index in range(n):
        try:
            out_low.append(sorted_low[og_low_index_list.index(og_index)])
        except ValueError:
            out_low.append(INACTIVE_LOW)
        try:
            out_high.append(sorted_high[og_high_index_list.index(og_index)])
        except ValueError:
            out_high.append(INACTIVE_HIGH)
    return out_high, out_low


def vote_one(
    current_price: float,
    high_bound: float,
    low_bound: float,
    high_tf_price: float,
    low_tf_price: float,
) -> str:
    """
    Mirror of pt_thinker.py:882-1008 voting.

    - SHORT if current > high_bound AND high_tf != low_tf (i.e. the TF
      produced a real prediction, not the inactive collapse).
    - LONG  if current < low_bound  AND high_tf != low_tf.
    - NONE  otherwise.
    """
    distinct = high_tf_price != low_tf_price
    if current_price > high_bound and distinct:
        return "short"
    if current_price < low_bound and distinct:
        return "long"
    return "none"


@dataclass
class ThinkerState:
    """Per-coin persistent state between sweeps."""
    high_tf_prices: list[float] = field(default_factory=list)
    low_tf_prices: list[float] = field(default_factory=list)
    high_bound_prices: list[float] = field(default_factory=list)
    low_bound_prices: list[float] = field(default_factory=list)
    perfects: list[str] = field(default_factory=list)

    @classmethod
    def fresh(cls, n_tfs: int) -> "ThinkerState":
        """Initial state — matches pt_thinker.py:373-374 placeholders."""
        return cls(
            high_tf_prices=[0.0] * n_tfs,
            low_tf_prices=[0.0] * n_tfs,
            low_bound_prices=[0.0] * n_tfs,
            high_bound_prices=[99999999999999999] * n_tfs,
            perfects=["inactive"] * n_tfs,
        )
