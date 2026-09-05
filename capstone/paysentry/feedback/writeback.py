"""Push Raphtory-derived risk back into the store.

This is the join that makes PaySentry a system rather than two scripts. Raphtory
finds a ring on Monday; those scores land on ``Account.risk_score``; the hot
path's ``counterparty_risk`` check reads them on Tuesday and screens differently
— with no analytics ever running on the authorization path (DESIGN.md §7).

Four rules the loop obeys:

1. **Results only, never raw data.** Only final per-account scores cross back.
2. **Idempotent.** Rerunning over the same window converges to the same values.
3. **Stamped.** ``scored_at`` lets the hot path discount stale scores.
4. **Never blocking.** A failed write-back degrades detection quality; it must
   never fail an authorization.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..models import RiskScore
from ..store.base import GraphStore


@dataclass(slots=True)
class WritebackStats:
    scored: int
    written: int
    max_score: float
    rings: int


def load_scores(path: Path, min_score: float = 0.0) -> list[RiskScore]:
    return [score for line in path.open()
            if (score := RiskScore.from_dict(json.loads(line))).score >= min_score]


def write_back(store: GraphStore, scores: list[RiskScore]) -> WritebackStats:
    written = store.write_risk(scores)
    return WritebackStats(
        scored=len(scores),
        written=written,
        max_score=max((s.score for s in scores), default=0.0),
        rings=len({s.ring_id for s in scores if s.ring_id}),
    )
