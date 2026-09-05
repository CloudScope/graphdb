"""The ``GraphStore`` contract and its shared pieces.

One interface, two implementations: ``SavannaStore`` talks to TigerGraph over
REST++, and ``LocalStore`` answers the same questions from SQLite. The point is
not merely offline development, though that matters on a free tier — it is that
**TigerGraph's specific contribution becomes measurable by subtraction**. Swap
the store, rerun the evaluation, and whatever changes is what the graph database
actually bought (DESIGN.md §5.4).

``LocalStore`` is a development aid, not a claim that SQLite is a graph database.
It will get materially worse as hop count and data size grow, and demonstrating
that at the ``large`` profile is a legitimate result rather than a defect.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import pyarrow as pa

from ..config import Config
from ..models import (Account, Customer, Device, LoadStats, Merchant, RiskScore,
                      ScreenResult, Txn)

# Column layout of ``export_transfers``. Fixed here rather than in each store so
# the Raphtory ingest in Phase 4 has one schema to bind to, whichever store fed it.
TRANSFER_SCHEMA = pa.schema([
    ("txn_id", pa.string()),
    ("src_account", pa.string()),
    ("dst_account", pa.string()),
    ("ts", pa.int64()),
    ("amount", pa.float64()),
    ("device_id", pa.string()),
    ("channel", pa.string()),
])


@dataclass(slots=True)
class EntitySet:
    """The static population, as loaded from ``entities.json``."""

    customers: list[Customer]
    accounts: list[Account]
    devices: list[Device]
    merchants: list[Merchant]

    @classmethod
    def load(cls, path: Path) -> EntitySet:
        payload = json.loads(path.read_text())
        return cls(
            customers=[Customer.from_dict(c) for c in payload["customers"]],
            accounts=[Account.from_dict(a) for a in payload["accounts"]],
            devices=[Device.from_dict(d) for d in payload["devices"]],
            merchants=[Merchant.from_dict(m) for m in payload["merchants"]],
        )


def iter_events(path: Path, limit: int | None = None) -> Iterator[Txn]:
    """Stream ``events.jsonl`` as ``Txn`` objects, ascending by timestamp."""
    with path.open() as handle:
        for i, line in enumerate(handle):
            if limit is not None and i >= limit:
                return
            yield Txn.from_dict(json.loads(line))


class GraphStore(ABC):
    """What both engines' system-of-record side must be able to do."""

    name: str = "abstract"

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    # -- lifecycle -------------------------------------------------------
    @abstractmethod
    def provision(self, drop: bool = False, reinstall: bool = False) -> None:
        """Create the schema and install queries. Must be safe to re-run."""

    @abstractmethod
    def bulk_load(self, entities: EntitySet, events: Iterator[Txn]) -> LoadStats:
        """Load the static population and the full event history."""

    @abstractmethod
    def close(self) -> None: ...

    # -- hot path (Phase 3) ----------------------------------------------
    @abstractmethod
    def upsert_txn(self, txn: Txn) -> None:
        """Write one transaction and its edges, as it arrives."""

    @abstractmethod
    def screen(self, txn: Txn) -> ScreenResult:
        """Score one transaction against the live graph and decide."""

    # -- warm path handoff (Phase 4) -------------------------------------
    @abstractmethod
    def export_transfers(self, since: int, until: int) -> pa.Table:
        """Account-to-account transfers in ``[since, until)`` as an Arrow table.

        Arrow rather than dicts because Raphtory's ``Graph.load_edges()`` consumes
        anything implementing ``__arrow_c_stream__``, which turns ingest into one
        vectorized call instead of a per-edge Python loop.
        """

    # -- feedback loop (Phase 6) -----------------------------------------
    @abstractmethod
    def write_risk(self, scores: list[RiskScore]) -> int:
        """Persist Raphtory-derived risk onto accounts. Must be idempotent."""

    @abstractmethod
    def account_risk(self, account_ids: list[str]) -> dict[str, RiskScore]:
        """Read back persisted risk — what the hot path consumes."""

    # -- introspection ---------------------------------------------------
    @abstractmethod
    def counts(self) -> dict[str, int]:
        """Vertex and edge counts, for reconciling one store against the other."""

    def __enter__(self) -> GraphStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def open_store(cfg: Config, kind: str) -> GraphStore:
    """Factory. ``kind`` is ``"local"`` or ``"savanna"``."""
    if kind == "local":
        from .local import LocalStore
        return LocalStore(cfg)
    if kind == "savanna":
        from .savanna import SavannaStore
        return SavannaStore(cfg)
    raise ValueError(f"unknown store {kind!r}; expected 'local' or 'savanna'")
