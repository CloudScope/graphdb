"""Render the evaluation as markdown.

The report answers three questions, in order of how much they matter:

1. Does each engine catch what the architecture says it should?
2. **What does the second engine actually add?** If the union barely beats the
   better single engine, the two-engine architecture is not justified for this
   workload, and the report has to say so.
3. What does treating time as a first-class dimension buy, measured rather than
   argued — the time-respecting vs static cycle comparison.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..config import Config
from ..models import Engine, SignalKind, Typology
from .metrics import (FRAUD_TYPOLOGIES, Detections, GroundTruth, cycle_comparison,
                      hot_path_accounts, precision, ring_recall)

# What DESIGN.md §4.3 predicted before any of this ran.
HYPOTHESIS = {
    Typology.STRUCTURING: ("yes", "yes"),
    Typology.CIRCULAR_LAYERING: ("low precision", "yes"),
    Typology.MULE_FAN_IN_OUT: ("no", "yes"),
    Typology.DEVICE_SHARING_RING: ("yes", "no"),
    Typology.DORMANT_BURST: ("no", "yes"),
}


def _pct(value: float) -> str:
    return f"{value:.0%}"


def render(cfg: Config, truth: GroundTruth, found: Detections,
           extra: dict | None = None, cross: str | None = None) -> str:
    hot = hot_path_accounts(found)
    warm = found.by_engine.get(Engine.RAPHTORY, set())
    union = hot | warm
    hot_label = "/".join(sorted(found.hot_engines)) or "local"

    lines: list[str] = []
    add = lines.append

    add(f"# PaySentry evaluation — profile `{cfg.profile.name}`")
    add("")
    add(f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · "
        f"{cfg.profile.accounts:,} accounts · {cfg.profile.transactions:,} transactions · "
        f"{cfg.profile.span_days}d span")
    add("")
    add(f"**Hot path executed by: `{hot_label}`.** Warm path: Raphtory. "
        + ("Note that `local` is the SQLite stand-in, not TigerGraph — these hot-path "
           "numbers say what the *checks* catch, not what a graph database adds."
           if hot_label == "local" else
           "Hot-path numbers come from a live TigerGraph workspace."))
    add("")
    add("Recall is measured per **ring** — catching one leg of a ring is catching "
        "the ring. Precision is measured per **account**, because that is what a "
        "false positive costs: an analyst opens a case. `decoy_cycle` is **not "
        "fraud**; detections on it are false positives, and that is the point of it.")
    add("")

    # -- 1. per-typology recall ------------------------------------------
    add("## Recall by typology")
    add("")
    add(f"| Typology | Rings | Hot path (`{hot_label}`) | Raphtory | Union | Predicted (hot / warm) |")
    add("|---|---:|---:|---:|---:|---|")
    for typology in sorted(FRAUD_TYPOLOGIES):
        h = ring_recall(truth, hot)[typology]
        w = ring_recall(truth, warm)[typology]
        u = ring_recall(truth, union)[typology]
        predicted = HYPOTHESIS.get(typology, ("?", "?"))
        add(f"| `{typology}` | {h.rings} | {_pct(h.recall)} | {_pct(w.recall)} | "
            f"{_pct(u.recall)} | {predicted[0]} / {predicted[1]} |")
    decoy = ring_recall(truth, union)[Typology.DECOY_CYCLE]
    add(f"| `decoy_cycle` *(control — lower is better)* | {decoy.rings} | "
        f"{_pct(ring_recall(truth, hot)[Typology.DECOY_CYCLE].recall)} | "
        f"{_pct(ring_recall(truth, warm)[Typology.DECOY_CYCLE].recall)} | "
        f"{_pct(decoy.recall)} | — |")
    add("")

    # -- 2. what the second engine adds -----------------------------------
    add("## What the second engine adds")
    add("")
    add("Measured against the **hot path alone**, because that is the only "
        "single-engine architecture actually available. Raphtory has no live "
        "write path and cannot serve an authorization (DESIGN.md §1.1), so "
        "\"Raphtory alone\" is not an option to compare against — treating it as "
        "one would understate what the second engine buys.")
    add("")
    add("| Typology | Hot path alone | + Raphtory | Gain |")
    add("|---|---:|---:|---:|")
    hot_only_total = union_total = 0.0
    for typology in sorted(FRAUD_TYPOLOGIES):
        h = ring_recall(truth, hot)[typology].recall
        u = ring_recall(truth, union)[typology].recall
        hot_only_total += h
        union_total += u
        add(f"| `{typology}` | {_pct(h)} | {_pct(u)} | {u - h:+.0%} |")
    n = len(FRAUD_TYPOLOGIES)
    add(f"| **mean** | **{_pct(hot_only_total / n)}** | **{_pct(union_total / n)}** | "
        f"**{(union_total - hot_only_total) / n:+.0%}** |")
    add("")

    unreachable = [t for t in sorted(FRAUD_TYPOLOGIES)
                   if ring_recall(truth, hot)[t].recall < 0.25
                   and ring_recall(truth, warm)[t].recall >= 0.75]
    if unreachable:
        add(f"**{len(unreachable)} of {n} typologies are effectively invisible to the "
            f"hot path** (<25% recall) and well detected by Raphtory (>=75%): "
            + ", ".join(f"`{t}`" for t in unreachable) + ". These are the ones "
            "defined by the order and spacing of events rather than by the state "
            "they leave behind, which is the dividing line the whole project "
            "exists to measure.")
    else:
        add("**No typology is reachable only via the temporal engine.** On this "
            "evidence the second engine is not justified for this workload.")
    add("")

    # Secondary, and easy to misread on its own.
    add("<details><summary>Union vs. best single engine (secondary)</summary>")
    add("")
    add("| Typology | Best single | Union | Gain |")
    add("|---|---:|---:|---:|")
    for typology in sorted(FRAUD_TYPOLOGIES):
        best = max(ring_recall(truth, hot)[typology].recall,
                   ring_recall(truth, warm)[typology].recall)
        u = ring_recall(truth, union)[typology].recall
        add(f"| `{typology}` | {_pct(best)} | {_pct(u)} | {u - best:+.0%} |")
    add("")
    add("This framing flatters the single-engine case by letting Raphtory count "
        "as a whole architecture on typologies it happens to cover. It is kept "
        "for completeness, not as the answer.")
    add("</details>")
    add("")

    # -- 3. the central comparison ----------------------------------------
    add("## Time-respecting vs static cycle detection")
    add("")
    add("Identical window, hop bound and seeds. The **only** difference is whether "
        "hop timestamps must increase. Rings are attributed by the transaction ids "
        "the detector actually cited, not by account membership.")
    add("")
    add("| Search | Real rings | Decoy rings *(must be 0)* | Accounts flagged | Account precision |")
    add("|---|---:|---:|---:|---:|")
    comparison = cycle_comparison(truth, found)
    for kind in (SignalKind.TIME_RESPECTING_CYCLE, SignalKind.STATIC_CYCLE):
        d = comparison[str(kind)]
        label = "time-respecting" if kind == SignalKind.TIME_RESPECTING_CYCLE else "static *(control)*"
        add(f"| {label} | {d['real_rings_caught']}/{d['real_rings']} | "
            f"{d['decoy_rings_caught']}/{d['decoy_rings']} | {d['flagged_accounts']:,} | "
            f"{d['precision']:.1%} |")
    add("")
    tr = comparison[str(SignalKind.TIME_RESPECTING_CYCLE)]
    st = comparison[str(SignalKind.STATIC_CYCLE)]
    if st["flagged_accounts"] and tr["flagged_accounts"]:
        ratio = st["flagged_accounts"] / tr["flagged_accounts"]
        add(f"Ignoring event order flags **{ratio:.1f}x more accounts** "
            f"({st['flagged_accounts']:,} vs {tr['flagged_accounts']:,}) and picks up "
            f"{st['decoy_rings_caught']} of {st['decoy_rings']} decoys — loops that are "
            f"structurally real but temporally impossible. That difference is what "
            f"time as a first-class dimension buys.")
    add("")

    # -- 4. precision ------------------------------------------------------
    add("## Precision")
    add("")
    add("| Engine | Flagged | True positives | Precision | Planted lookalikes | Decoys | Background |")
    add("|---|---:|---:|---:|---:|---:|---:|")
    for name, flagged in ((f"hot path ({hot_label})", hot), ("Raphtory", warm),
                          ("union", union)):
        p = precision(truth, flagged)
        add(f"| {name} | {p.flagged:,} | {p.true_positives:,} | {p.precision:.1%} | "
            f"{p.planted_lookalikes:,} | {p.decoys:,} | {p.background:,} |")
    add("")
    add("*Planted lookalikes* are the organic near-patterns — households sharing a "
        "device, housemates settling up in a loop, accounts legitimately returning "
        "from dormancy. They are deliberately hard negatives; tripping on them is a "
        "different failure from tripping on random background traffic.")
    add("")

    # -- 5. per-signal -----------------------------------------------------
    add("## Per-signal precision")
    add("")
    add("| Signal | Engine | Flagged | True positives | Precision |")
    add("|---|---|---:|---:|---:|")
    for kind in sorted(found.by_kind):
        flagged = found.by_kind[kind]
        p = precision(truth, flagged)
        engine = "Raphtory" if kind in {str(k) for k in (
            SignalKind.TIME_RESPECTING_CYCLE, SignalKind.STATIC_CYCLE,
            SignalKind.FAN_IN_OUT_HOLDING, SignalKind.DORMANT_BURST,
            SignalKind.PAGERANK_SPIKE, SignalKind.RING_COHESION)} else "hot path"
        note = " *(control)*" if kind == str(SignalKind.STATIC_CYCLE) else ""
        add(f"| `{kind}`{note} | {engine} | {len(flagged):,} | {p.true_positives:,} | "
            f"{p.precision:.1%} |")
    add("")

    if cross:
        add("## Cross-engine comparison")
        add("")
        add(cross)
        add("")

    if extra:
        add("## Run characteristics")
        add("")
        for key, value in extra.items():
            add(f"- **{key}**: {value}")
        add("")

    return "\n".join(lines)
