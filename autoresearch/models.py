from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Dict, List, Optional


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _serialize(item) for key, item in asdict(value).items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    return value


@dataclass
class TradeEvent:
    timestamp: float
    mode: str
    symbol: str
    side: str
    tag: Optional[str]
    qty: float
    price: float
    pnl_pct: Optional[float] = None
    realized_profit_usd: Optional[float] = None
    order_id: Optional[str] = None
    buying_power_before: Optional[float] = None
    buying_power_after: Optional[float] = None
    buying_power_delta: Optional[float] = None
    source: str = 'trade_history'

    def to_dict(self) -> Dict[str, Any]:
        return _serialize(self)


@dataclass
class MarketContextRow:
    symbol: str
    ts: float
    entry_signal_strength: float = 0.0
    long_signal: float = 0.0
    short_signal: float = 0.0
    change_24h: float = 0.0
    spread_pct: float = 0.0
    quote_volume_usdt: float = 0.0
    volume_ratio: float = 0.0
    body_pct: float = 0.0
    range_pct: float = 0.0
    near_high_ratio: float = 0.0
    decision_type: Optional[str] = None
    decision_reason: Optional[str] = None
    decision_confidence: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return _serialize(self)


@dataclass
class ClosedTrade:
    symbol: str
    mode: str
    entry_ts: float
    exit_ts: float
    hold_minutes: float
    pnl_pct: float
    pnl_usd: float
    entry_price: float
    exit_price: float
    exit_tag: Optional[str]
    dca_level: int
    source: str
    qty: float
    entry_order_id: Optional[str] = None
    exit_order_id: Optional[str] = None
    entry_buying_power: Optional[float] = None
    exit_buying_power: Optional[float] = None
    peak_pnl_pct: Optional[float] = None
    trough_pnl_pct: Optional[float] = None
    market_context: Optional[MarketContextRow] = None

    def to_dict(self) -> Dict[str, Any]:
        return _serialize(self)


@dataclass
class ResearchDataset:
    mode: str
    days: Any
    generated_at: str
    trade_events: List[TradeEvent] = field(default_factory=list)
    closed_trades: List[ClosedTrade] = field(default_factory=list)
    active_positions: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    baseline_config: Dict[str, Any] = field(default_factory=dict)
    active_experiment: Optional[Dict[str, Any]] = None
    experiment_registry: List[Dict[str, Any]] = field(default_factory=list)
    history_sources: List[str] = field(default_factory=list)
    provenance_counts: Dict[str, int] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _serialize(self)


@dataclass
class Hypothesis:
    hypothesis_id: str
    family: str
    title: str
    rationale: str
    parameter_target: Dict[str, Any]
    expected_effect: str
    experiment_plan: Dict[str, Any]
    guardrails: List[str]
    priority_score: float
    source_cause: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return _serialize(self)


@dataclass
class ExperimentResult:
    hypothesis_id: str
    dataset: str
    engine: str
    train_window: Dict[str, Any]
    test_window: Dict[str, Any]
    params: Dict[str, Any]
    metrics: Dict[str, Any]
    artifacts: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _serialize(self)


@dataclass
class Recommendation:
    rank: int
    hypothesis_id: str
    title: str
    evidence_summary: str
    config_patch: Optional[Dict[str, Any]]
    rollout_plan: List[str]
    rejection_reasons: List[str]
    status: str

    def to_dict(self) -> Dict[str, Any]:
        return _serialize(self)
