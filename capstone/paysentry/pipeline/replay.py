"""Replay the event log through the hot path.

Each event is upserted and then screened, in that order and synchronously — the
same sequence a real authorization would follow. Screening a transaction the
store has not yet seen would understate every self-referential signal (its own
velocity, its own device), so the write has to land first.

Every check is bounded by ``txn.ts`` (see ``store/screening.py``), so replaying
against a store that already holds the full history gives the same answers as
replaying into an empty one. ``--fresh`` does the latter anyway, because "the
bounds are right" is a claim worth being able to test rather than assert.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from ..config import Config
from ..models import Decision
from ..store.base import EntitySet, GraphStore, iter_events


@dataclass(slots=True)
class ReplayStats:
    screened: int = 0
    decisions: dict[str, int] = field(default_factory=dict)
    latencies_ms: list[float] = field(default_factory=list)
    elapsed_s: float = 0.0

    def percentile(self, p: float) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        index = min(int(p / 100 * len(ordered)), len(ordered) - 1)
        return ordered[index]

    def render(self, budget_ms: float) -> str:
        lines = [f"  screened   {self.screened:,} transactions in {self.elapsed_s:.1f}s "
                 f"({self.screened / max(self.elapsed_s, 1e-9):,.0f}/s)"]
        for decision in (Decision.ALLOW, Decision.REVIEW, Decision.BLOCK):
            count = self.decisions.get(decision, 0)
            share = count / self.screened if self.screened else 0
            lines.append(f"  {decision:10s} {count:>9,}  ({share:6.2%})")
        p95 = self.percentile(95)
        verdict = "within" if p95 <= budget_ms else "OVER"
        lines.append(f"  latency    p50={self.percentile(50):.2f}ms  "
                     f"p95={p95:.2f}ms  p99={self.percentile(99):.2f}ms  "
                     f"({verdict} the {budget_ms}ms budget)")
        return "\n".join(lines)


def replay(cfg: Config, store: GraphStore, limit: int | None = None,
           fresh: bool = False, progress: bool = True,
           sample: int | None = None) -> ReplayStats:
    """Stream the log through ``store``, writing decisions to disk."""
    say = print if progress else (lambda *a, **k: None)

    if fresh:
        say("  fresh start: provisioning an empty store")
        store.provision(drop=True)
        entities = EntitySet.load(cfg.paths.entities)
        store.bulk_load(entities, iter([]))

    # Evenly-spaced sampling for the cloud store, where a full replay costs real
    # workspace uptime. Every screening query is bounded by the transaction's own
    # timestamp against a fully-loaded store, so a sampled transaction sees
    # exactly the history it would have seen in a full replay — the sample loses
    # coverage, not correctness. Spread across the log rather than taken from the
    # front, so late transactions with deep history are represented.
    stride = 1
    if sample:
        total = sum(1 for _ in cfg.paths.events.open())
        stride = max(1, total // sample)
        if progress:
            print(f"  sampling every {stride}th transaction "
                  f"({total // stride:,} of {total:,})")

    stats = ReplayStats()
    started = time.perf_counter()
    with cfg.paths.decisions.open("w") as handle:
        for index, txn in enumerate(iter_events(cfg.paths.events, limit)):
            if index % stride:
                continue
            if not sample:
                store.upsert_txn(txn)
            result = store.screen(txn)
            handle.write(json.dumps(result.to_dict()) + "\n")

            stats.screened += 1
            stats.decisions[str(result.decision)] = \
                stats.decisions.get(str(result.decision), 0) + 1
            stats.latencies_ms.append(result.latency_ms)
            if progress and sample and stats.screened % 250 == 0:
                rate = stats.screened / (time.perf_counter() - started)
                print(f"    {stats.screened:,} screened ({rate:.1f}/s)")
            if stats.screened % 25_000 == 0:
                say(f"  ... {stats.screened:,} screened")
    store.commit() if hasattr(store, "commit") else None
    stats.elapsed_s = time.perf_counter() - started
    return stats
