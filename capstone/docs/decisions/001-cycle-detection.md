# 001 — `temporally_reachable_nodes` is not a cycle detector

**Status:** accepted · **Phase:** 5

## Context

DESIGN.md §6.2 named `algorithms.temporally_reachable_nodes` as the way to find
time-respecting cycles: seed it at an account, and an account appearing in its
own reachable set has a time-respecting path back to itself.

That is true and useless. Measured on the `small` profile over a 72-hour window
with six hops:

| seeded from | self-returns |
|---|---|
| real circular-layering rings | 3/3 |
| decoy (non-time-respecting) rings | 3/3 |
| **random accounts** | **59/60** |

In a connected payments graph, almost everything reaches itself. The function
answers "can I get there from here", which is not the question. A layering ring
is a **tight** cycle: closed, short, and completed inside a bounded window.

## Decision

Use Raphtory for the windowing and the temporal index — `window()` plus
`edges.explode()` to get per-event edges, which is fast (1,941 events in 3ms) —
and run a bounded depth-first walk over those events in Python. The walk enforces
strictly increasing hop timestamps, a hop limit, and a total elapsed bound.

The **static control** is the identical walk with the time constraint switched
off, over the same window, the same hop bound, and the same seeds. Only the time
constraint differs, which is what makes the §4.3.2 comparison fair.

Two follow-on corrections came out of building it:

- **View span must exceed the cycle window.** With both at 72h, a cycle taking
  the full window straddles every boundary and is never wholly inside any view.
  The view is now `2 × cycle_window` stepping by `cycle_window / 2`, which
  guarantees containment.
- **Collect several cycles per seed, not the first.** A seed sitting in both a
  planted ring and an ordinary background loop would otherwise be credited with
  whichever the walk happened to reach first — an artefact of edge ordering.

## Addendum — temporal 3-node motifs were dropped, not deferred

DESIGN.md §6.2 listed `global_temporal_three_node_motif` /
`local_temporal_three_node_motifs` as a `layering_motif` signal. Probing the API
on known synthetic patterns settles what it can actually see:

| pattern (delta large enough to cover it) | motif counts for the centre node |
|---|---|
| `A->B->C->D` chain, centre B or C | none |
| `A->B, C->B, D->B` fan-in | none |
| `B->A, B->C, B->D` fan-out | none |
| `A->B->C->A` cycle | index 35 |

The name is precise and easy to misread: these are **three-edge, up-to-three-node**
motifs. Every star and chain case above involves four distinct nodes, so none of
them is a motif at all. The one pattern that does register is a 3-node temporal
cycle — which `detect_cycles` already finds, up to six hops, with the path and
amounts as evidence, and with the static control alongside it.

**Decision: drop the signal rather than defer it.** Implementing it would add a
strictly weaker duplicate of a detector already in place. `layering_motif` is
removed from `SignalKind` and from the scoring weights; guessing at the 40-slot
index ordering to salvage it would have produced a signal that looked plausible
and meant nothing.

## Consequences

- The cycle search is ours, not Raphtory's. Raphtory still supplies what it is
  actually good at here: the temporal index, windowing, and exploded events.
- Attribution in the evaluation must use the **transaction ids the detector
  cited**, not account membership. A decoy's accounts routinely appear in
  unrelated genuine cycles, and crediting the decoy for those would corrupt the
  control. This changed decoy detections from 2/3 to 0/3 — the difference between
  a broken control and a working one.
- DESIGN.md §6.2's row for this signal is superseded by this record.
