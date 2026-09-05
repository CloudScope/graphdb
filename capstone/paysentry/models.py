"""Domain models shared by every component.

These types are the contract between phases. The generator writes them, the
stores load them, Raphtory reads them back, and the eval harness joins on them —
so they are deliberately plain: dataclasses with explicit ``to_dict``/``from_dict``
rather than a serialization framework. The event log is JSONL that a human should
be able to read with ``head``, and that constrains the shape more than any
library would.

Note what is *not* here: no ORM, no engine-specific types. ``Txn`` knows nothing
about vertices or timestamped edges. The two divergent data models of DESIGN.md
§3 are built in the store and temporal layers respectively, from these.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, Iterable, Iterator, Self


# --------------------------------------------------------------------------
# Enumerations
# --------------------------------------------------------------------------

class Typology(StrEnum):
    """The five planted fraud patterns, plus the label for legitimate traffic.

    The values are the ground-truth vocabulary: they appear in ``labels.jsonl``
    and become the row labels of the evaluation report's per-typology table
    (DESIGN.md §4.3), so they must stay stable once data has been generated.
    """

    LEGITIMATE = "legitimate"
    STRUCTURING = "structuring"
    CIRCULAR_LAYERING = "circular_layering"
    MULE_FAN_IN_OUT = "mule_fan_in_out"
    DEVICE_SHARING_RING = "device_sharing_ring"
    DORMANT_BURST = "dormant_burst"
    # A structurally identical cycle whose timestamps are out of order, so money
    # could never have flowed around it. Not fraud — a control. A static cycle
    # query fires on these; a time-respecting one must not (DESIGN.md §4.3.2).
    DECOY_CYCLE = "decoy_cycle"


class Decision(StrEnum):
    """Hot-path authorization outcome."""

    ALLOW = "allow"
    REVIEW = "review"
    BLOCK = "block"


class Channel(StrEnum):
    RETAIL_PURCHASE = "retail_purchase"
    P2P_TRANSFER = "p2p_transfer"
    BILL_PAYMENT = "bill_payment"
    SALARY_CREDIT = "salary_credit"


class AccountStatus(StrEnum):
    ACTIVE = "active"
    DORMANT = "dormant"
    FROZEN = "frozen"
    CLOSED = "closed"


class AccountType(StrEnum):
    PERSONAL = "personal"
    BUSINESS = "business"


class SignalKind(StrEnum):
    """Every detector output, hot or warm.

    Keeping both engines' signals in one enum is what lets the eval harness ask
    "which engine caught this" without special-casing either — the answer is a
    property of the signal, not of the code path that produced it.
    """

    # Hot path — TigerGraph / LocalStore (DESIGN.md §5.2)
    SHARED_DEVICE = "shared_device"
    COUNTERPARTY_RISK = "counterparty_risk"
    VELOCITY = "velocity"
    FAN_OUT_BURST = "fan_out_burst"
    NEAR_THRESHOLD = "near_threshold"
    # Warm path — Raphtory (DESIGN.md §6.2)
    TIME_RESPECTING_CYCLE = "time_respecting_cycle"
    STATIC_CYCLE = "static_cycle"          # the control, for the precision gap
    FAN_IN_OUT_HOLDING = "fan_in_out_holding"
    DORMANT_BURST = "dormant_burst"
    PAGERANK_SPIKE = "pagerank_spike"
    RING_COHESION = "ring_cohesion"


class Engine(StrEnum):
    """Which engine produced a signal. The unit of attribution for §8's report."""

    TIGERGRAPH = "tigergraph"
    RAPHTORY = "raphtory"
    LOCAL = "local"          # the SQLite fallback standing in for TigerGraph


# --------------------------------------------------------------------------
# Serialization helper
# --------------------------------------------------------------------------

class _Record:
    """Mixin giving dataclasses symmetric dict conversion for JSONL round-trips."""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[call-overload]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in data.items() if k in known})  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Static population
# --------------------------------------------------------------------------

@dataclass(slots=True)
class Customer(_Record):
    customer_id: str
    name: str
    country: str
    kyc_level: int
    onboarded_at: int          # epoch ms


@dataclass(slots=True)
class Account(_Record):
    account_id: str
    customer_id: str
    opened_at: int             # epoch ms
    account_type: str = AccountType.PERSONAL
    status: str = AccountStatus.ACTIVE
    # Write-back targets. These are the *only* fields Raphtory ever causes to
    # change, and they are the entire surface area of the feedback loop
    # (DESIGN.md §3.1, §7).
    risk_score: float = 0.0
    risk_reasons: list[str] = field(default_factory=list)
    ring_id: str | None = None
    scored_at: int | None = None


@dataclass(slots=True)
class Device(_Record):
    device_id: str
    fingerprint: str
    os: str
    first_seen: int            # epoch ms


@dataclass(slots=True)
class Merchant(_Record):
    merchant_id: str
    name: str
    mcc: str                   # merchant category code
    country: str


@dataclass(slots=True)
class Txn(_Record):
    """A single transfer. The unit of both the event log and the hot path.

    ``merchant_id`` is optional because only retail purchases have one — which is
    exactly why ``Txn`` is a promoted vertex in TigerGraph rather than an edge
    (DESIGN.md §3.1): sender, receiver, device and merchant is a 4-way
    relationship, and an edge holds two endpoints.
    """

    txn_id: str
    src_account: str
    dst_account: str
    amount: float
    ts: int                    # epoch ms
    channel: str
    device_id: str
    merchant_id: str | None = None
    currency: str = "USD"
    status: str = "posted"


# --------------------------------------------------------------------------
# Ground truth — read only by the evaluation harness, never by a detector
# --------------------------------------------------------------------------

@dataclass(slots=True)
class Label(_Record):
    """One ground-truth fact about a planted pattern.

    ``ring_id`` groups the accounts and transactions of a single planted
    instance, so recall can be measured per ring rather than per transaction —
    catching one leg of a ring is catching the ring.
    """

    ring_id: str
    typology: str
    account_id: str | None = None
    txn_id: str | None = None


# --------------------------------------------------------------------------
# Detection outputs
# --------------------------------------------------------------------------

@dataclass(slots=True)
class RiskSignal(_Record):
    """One detector firing. ``strength`` is normalized to [0, 1].

    ``evidence`` carries whatever made the call explainable — the cycle path, the
    peer accounts on a shared device, the holding time. It is for humans and for
    the report; nothing downstream branches on its contents.
    """

    account_id: str
    kind: str
    strength: float
    engine: str
    window_start: int | None = None
    window_end: int | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.strength <= 1.0:
            raise ValueError(
                f"signal strength must be in [0,1], got {self.strength} "
                f"for {self.kind} on {self.account_id}"
            )


@dataclass(slots=True)
class RiskScore(_Record):
    """Aggregated per-account risk. The payload of the feedback loop (§7)."""

    account_id: str
    score: float               # 0..100
    reasons: list[str] = field(default_factory=list)
    ring_id: str | None = None
    scored_at: int | None = None


@dataclass(slots=True)
class ScreenResult(_Record):
    """The hot path's verdict on one transaction.

    ``latency_ms`` is recorded per call rather than sampled, because the §5.3
    budget is stated as a p95 and you cannot compute a percentile from an average.
    """

    txn_id: str
    account_id: str
    decision: str
    score: float
    signals: list[RiskSignal] = field(default_factory=list)
    latency_ms: float = 0.0
    engine: str = Engine.LOCAL

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["signals"] = [s.to_dict() if isinstance(s, RiskSignal) else s
                           for s in self.signals]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Self:
        payload = dict(data)
        payload["signals"] = [RiskSignal.from_dict(s) for s in payload.get("signals", [])]
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in payload.items() if k in known})


# --------------------------------------------------------------------------
# Bulk containers
# --------------------------------------------------------------------------

@dataclass(slots=True)
class LoadStats(_Record):
    """What a ``bulk_load`` actually wrote. Compared across stores in Phase 2."""

    vertices: dict[str, int] = field(default_factory=dict)
    edges: dict[str, int] = field(default_factory=dict)
    elapsed_s: float = 0.0

    @property
    def total_vertices(self) -> int:
        return sum(self.vertices.values())

    @property
    def total_edges(self) -> int:
        return sum(self.edges.values())


@dataclass(slots=True)
class Dataset:
    """A generated dataset held in memory.

    Transactions are an ``Iterable`` rather than a ``list`` on purpose: the
    ``large`` profile is two million of them, and the generator streams to disk
    rather than materializing them all (DESIGN.md §4.1).
    """

    profile: str
    seed: int
    customers: list[Customer] = field(default_factory=list)
    accounts: list[Account] = field(default_factory=list)
    devices: list[Device] = field(default_factory=list)
    merchants: list[Merchant] = field(default_factory=list)
    transactions: Iterable[Txn] = ()
    labels: list[Label] = field(default_factory=list)

    def iter_transactions(self) -> Iterator[Txn]:
        return iter(self.transactions)

    def summary(self) -> dict[str, int]:
        return {
            "customers": len(self.customers),
            "accounts": len(self.accounts),
            "devices": len(self.devices),
            "merchants": len(self.merchants),
            "labels": len(self.labels),
        }
