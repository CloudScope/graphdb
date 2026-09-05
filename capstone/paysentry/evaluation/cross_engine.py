"""Compare two hot-path runs of the same transactions on different stores.

The engines are compared on the transactions they *both* screened, which is what
makes the comparison meaningful when one run was sampled to control cloud cost.
Agreement is reported at three levels because they fail differently: identical
decisions is what an operator cares about, identical scores is what says the two
implementations really compute the same thing, and identical signal sets is what
localizes a disagreement to a specific check.
"""

from __future__ import annotations

import json
from pathlib import Path


def _load(path: Path) -> dict[str, dict]:
    return {json.loads(line)["txn_id"]: json.loads(line) for line in path.open()}


def _signature(record: dict) -> list[tuple[str, float]]:
    return sorted((s["kind"], round(s["strength"], 6)) for s in record["signals"])


def compare(a_path: Path, b_path: Path, a_name: str, b_name: str,
            note: str = "") -> str:
    a, b = _load(a_path), _load(b_path)
    common = sorted(set(a) & set(b))
    if not common:
        return "_No transactions screened by both engines._"

    same_decision = sum(1 for k in common if a[k]["decision"] == b[k]["decision"])
    same_score = sum(1 for k in common if abs(a[k]["score"] - b[k]["score"]) < 1e-6)
    same_signals = sum(1 for k in common if _signature(a[k]) == _signature(b[k]))

    def p50(records: dict) -> float:
        values = sorted(r["latency_ms"] for r in records.values())
        return values[len(values) // 2] if values else 0.0

    lines = [
        f"Both engines screened the same {len(common):,} transactions from the same "
        f"event log, against the same loaded data.",
        "",
        "| Measure | Agreement |",
        "|---|---:|",
        f"| Identical decision | {same_decision:,}/{len(common):,} "
        f"({same_decision / len(common):.2%}) |",
        f"| Identical score | {same_score:,}/{len(common):,} "
        f"({same_score / len(common):.2%}) |",
        f"| Identical signal set | {same_signals:,}/{len(common):,} "
        f"({same_signals / len(common):.2%}) |",
        "",
        "| Engine | Screening latency p50 |",
        "|---|---:|",
        f"| {a_name} | {p50(a):.2f} ms |",
        f"| {b_name} | {p50(b):.2f} ms |",
        "",
        "Identical decisions are expected **by construction**: the signal formulas "
        "live once in `store/screening.py`, and each store only *measures* the "
        "graph its own way — SQL joins versus GSQL accumulators. Had that maths "
        "been duplicated into GSQL, no comparison in this report would be "
        "interpretable.",
    ]
    if note:
        lines += ["", note]
    return "\n".join(lines)
