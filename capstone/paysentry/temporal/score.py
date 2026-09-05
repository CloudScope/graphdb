"""Aggregate temporal signals into a per-account risk score.

A transparent weighted sum with the weights in ``config.yaml``. Deliberately not
learned: the entire purpose of this project is to attribute detection capability
to an engine, and a learned scorer would make that attribution impossible to read
back out (DESIGN.md §6.3).

``STATIC_CYCLE`` is excluded from scoring by construction. It is a **control**,
not a detector — it exists to show what ignoring event order costs in precision,
and letting it contribute to a score would fold the control into the treatment.
"""

from __future__ import annotations

import collections
import hashlib

from ..config import Config
from ..models import RiskScore, RiskSignal, SignalKind

# Signals that measure the experiment rather than the risk.
CONTROL_SIGNALS = frozenset({SignalKind.STATIC_CYCLE})


def _ring_id(members: list[str]) -> str:
    digest = hashlib.sha1("|".join(sorted(members)).encode()).hexdigest()[:8]
    return f"RING-{digest}"


def score_accounts(cfg: Config, signals: list[RiskSignal],
                   scored_at: int) -> list[RiskScore]:
    """Fold signals into one score per account."""
    weights = cfg.detection.scoring.weights.as_dict()
    cap = cfg.detection.scoring.max_score

    by_account: dict[str, list[RiskSignal]] = collections.defaultdict(list)
    for signal in signals:
        if signal.kind in CONTROL_SIGNALS:
            continue
        by_account[signal.account_id].append(signal)

    scores: list[RiskScore] = []
    for account, account_signals in by_account.items():
        # Strongest instance per kind, so a detector that fires across many
        # rolling windows counts once rather than dominating by repetition.
        strongest: dict[str, RiskSignal] = {}
        for signal in account_signals:
            kind = str(signal.kind)
            if kind not in strongest or signal.strength > strongest[kind].strength:
                strongest[kind] = signal

        total = sum(weights.get(kind, 0.0) * signal.strength
                    for kind, signal in strongest.items())
        if total <= 0:
            continue

        ring = None
        cohesion = strongest.get(str(SignalKind.RING_COHESION))
        if cohesion is not None:
            ring = _ring_id(cohesion.evidence.get("members", [account]))

        reasons = sorted(
            f"{kind}({signal.strength:.2f})"
            for kind, signal in strongest.items() if weights.get(kind, 0.0) > 0)
        scores.append(RiskScore(
            account_id=account, score=round(min(total, cap), 2),
            reasons=reasons, ring_id=ring, scored_at=scored_at))

    scores.sort(key=lambda s: -s.score)
    return scores
