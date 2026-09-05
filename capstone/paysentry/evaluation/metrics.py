"""Score both engines against ground truth.

The unit of recall is the **ring**, not the transaction. Catching one leg of a
laundering ring is catching the ring — an analyst follows it from there — so
requiring every transaction to be flagged would understate both engines equally
and hide the difference this project exists to measure.

The unit of precision is the **account**, because that is what a false positive
costs: an analyst opens a case on an account. An account is a false positive when
it carries no fraud label, whether it is labelled ``legitimate`` (a deliberately
planted lookalike) or is unlabelled background. Those two are reported
separately, because a detector that trips on the hard planted lookalikes is
behaving differently from one that trips on random traffic.

``decoy_cycle`` is **not fraud**. A detection on a decoy is a false positive, and
that is the entire point of the control.
"""

from __future__ import annotations

import collections
import json
from dataclasses import dataclass, field
from pathlib import Path

from ..models import Engine, SignalKind, Typology

FRAUD_TYPOLOGIES = frozenset({
    Typology.STRUCTURING, Typology.CIRCULAR_LAYERING, Typology.MULE_FAN_IN_OUT,
    Typology.DEVICE_SHARING_RING, Typology.DORMANT_BURST,
})


@dataclass(slots=True)
class GroundTruth:
    account_typology: dict[str, str] = field(default_factory=dict)
    account_ring: dict[str, str] = field(default_factory=dict)
    ring_typology: dict[str, str] = field(default_factory=dict)
    ring_accounts: dict[str, set[str]] = field(default_factory=lambda: collections.defaultdict(set))
    ring_txns: dict[str, set[str]] = field(default_factory=lambda: collections.defaultdict(set))

    @classmethod
    def load(cls, path: Path) -> GroundTruth:
        truth = cls()
        for line in path.open():
            label = json.loads(line)
            ring, typology = label["ring_id"], label["typology"]
            truth.ring_typology[ring] = typology
            txn = label.get("txn_id")
            if txn:
                truth.ring_txns[ring].add(txn)
            account = label.get("account_id")
            if account:
                truth.ring_accounts[ring].add(account)
                # A fraud label wins over an incidental one, so an account that
                # appears in both a ring's txn labels and its account labels
                # resolves consistently.
                if account not in truth.account_typology or typology in FRAUD_TYPOLOGIES:
                    truth.account_typology[account] = typology
                    truth.account_ring[account] = ring
        return truth

    def rings_of(self, typology: str) -> set[str]:
        return {r for r, t in self.ring_typology.items() if t == typology}

    def is_fraud(self, account: str) -> bool:
        return self.account_typology.get(account) in FRAUD_TYPOLOGIES


@dataclass(slots=True)
class Detections:
    """Accounts flagged, by engine and by signal kind.

    ``evidence_txns`` keeps the transaction ids a signal actually cited. For
    cycle signals that is the difference between "this account is in a ring" and
    "the detector identified *this* ring" — a decoy member can easily appear in
    some unrelated genuine cycle, and attributing that to the decoy would credit
    the control with a detection it never made.
    """

    by_engine: dict[str, set[str]] = field(default_factory=lambda: collections.defaultdict(set))
    by_kind: dict[str, set[str]] = field(default_factory=lambda: collections.defaultdict(set))
    evidence_txns: dict[str, set[str]] = field(default_factory=lambda: collections.defaultdict(set))
    # Which store actually ran the hot path, so the report can say so.
    hot_engines: set[str] = field(default_factory=set)

    def add(self, account: str, engine: str, kind: str,
            txn_ids: list[str] | None = None) -> None:
        self.by_kind[kind].add(account)
        # The static-cycle control is recorded per-kind but must never count
        # toward Raphtory's engine totals. It is the experiment's comparison arm,
        # not a detector the system would act on, and folding it in inflated both
        # Raphtory's recall and its false-positive count.
        if kind != str(SignalKind.STATIC_CYCLE):
            self.by_engine[engine].add(account)
        if txn_ids:
            self.evidence_txns[kind].update(txn_ids)


def load_detections(decisions: Path | None, signals: Path | None,
                    min_score: float = 0.0) -> Detections:
    """Collect flagged accounts from a hot-path replay and a Raphtory run."""
    found = Detections()
    if decisions and decisions.exists():
        for line in decisions.open():
            result = json.loads(line)
            if result["decision"] == "allow":
                continue
            # Attribute to the engine that ACTUALLY produced the decision. This
            # was hardcoded to TIGERGRAPH, which silently credited TigerGraph for
            # a replay SQLite had run — the reports claimed a measurement that
            # had never been taken.
            engine = result.get("engine", Engine.LOCAL)
            found.hot_engines.add(engine)
            for signal in result["signals"]:
                found.add(result["account_id"], engine, signal["kind"])
    if signals and signals.exists():
        for line in signals.open():
            signal = json.loads(line)
            if signal["strength"] < min_score:
                continue
            found.add(signal["account_id"], Engine.RAPHTORY, signal["kind"],
                      signal.get("evidence", {}).get("txn_ids"))
    return found


@dataclass(slots=True)
class TypologyResult:
    typology: str
    rings: int
    caught: int

    @property
    def recall(self) -> float:
        return self.caught / self.rings if self.rings else 0.0


def ring_recall(truth: GroundTruth, flagged: set[str]) -> dict[str, TypologyResult]:
    """Per-typology ring recall for one set of flagged accounts."""
    results: dict[str, TypologyResult] = {}
    for typology in sorted({*FRAUD_TYPOLOGIES, Typology.DECOY_CYCLE}):
        rings = truth.rings_of(typology)
        caught = sum(1 for ring in rings if truth.ring_accounts[ring] & flagged)
        results[typology] = TypologyResult(typology, len(rings), caught)
    return results


@dataclass(slots=True)
class PrecisionResult:
    flagged: int
    true_positives: int
    planted_lookalikes: int      # organic near-patterns — the hard negatives
    decoys: int                  # the time-respecting control
    background: int              # everything else

    @property
    def precision(self) -> float:
        return self.true_positives / self.flagged if self.flagged else 0.0


def precision(truth: GroundTruth, flagged: set[str]) -> PrecisionResult:
    tp = lookalike = decoy = background = 0
    for account in flagged:
        typology = truth.account_typology.get(account)
        if typology in FRAUD_TYPOLOGIES:
            tp += 1
        elif typology == Typology.DECOY_CYCLE:
            decoy += 1
        elif typology == Typology.LEGITIMATE:
            lookalike += 1
        else:
            background += 1
    return PrecisionResult(len(flagged), tp, lookalike, decoy, background)


def hot_path_accounts(found: Detections) -> set[str]:
    """Accounts flagged by whichever store ran the hot path."""
    flagged: set[str] = set()
    for engine in (found.hot_engines or {Engine.LOCAL}):
        flagged |= found.by_engine.get(engine, set())
    return flagged


def cycle_comparison(truth: GroundTruth, found: Detections) -> dict[str, dict]:
    """The central measurement: time-respecting vs static cycle detection.

    Same window, same hop bound, same seeds — the only difference is whether
    event order is respected. Any gap between these two rows is what treating
    time as a first-class dimension actually buys (DESIGN.md §4.3.2).
    """
    out: dict[str, dict] = {}
    real = truth.rings_of(Typology.CIRCULAR_LAYERING)
    decoys = truth.rings_of(Typology.DECOY_CYCLE)
    for kind in (SignalKind.TIME_RESPECTING_CYCLE, SignalKind.STATIC_CYCLE):
        flagged = found.by_kind.get(str(kind), set())
        cited = found.evidence_txns.get(str(kind), set())
        # Attribution by cited transactions, not account membership: the question
        # is whether the detector traced *this* loop, not whether one of its
        # accounts turned up somewhere.
        hit = lambda rings: sum(1 for r in rings if truth.ring_txns[r] & cited)
        out[str(kind)] = {
            "flagged_accounts": len(flagged),
            "real_rings_caught": hit(real),
            "real_rings": len(real),
            "decoy_rings_caught": hit(decoys),
            "decoy_rings": len(decoys),
            "precision": precision(truth, flagged).precision,
        }
    return out
