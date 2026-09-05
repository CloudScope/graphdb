# PaySentry evaluation — profile `small`

Generated 2026-09-05 09:57 UTC · 500 accounts · 20,000 transactions · 30d span

**Hot path executed by: `local`.** Warm path: Raphtory. Note that `local` is the SQLite stand-in, not TigerGraph — these hot-path numbers say what the *checks* catch, not what a graph database adds.

Recall is measured per **ring** — catching one leg of a ring is catching the ring. Precision is measured per **account**, because that is what a false positive costs: an analyst opens a case. `decoy_cycle` is **not fraud**; detections on it are false positives, and that is the point of it.

## Recall by typology

| Typology | Rings | Hot path (`local`) | Raphtory | Union | Predicted (hot / warm) |
|---|---:|---:|---:|---:|---|
| `circular_layering` | 3 | 0% | 100% | 100% | low precision / yes |
| `device_sharing_ring` | 3 | 100% | 100% | 100% | yes / no |
| `dormant_burst` | 4 | 0% | 100% | 100% | no / yes |
| `mule_fan_in_out` | 4 | 0% | 100% | 100% | no / yes |
| `structuring` | 3 | 33% | 100% | 100% | yes / yes |
| `decoy_cycle` *(control — lower is better)* | 3 | 0% | 33% | 33% | — |

## What the second engine adds

Measured against the **hot path alone**, because that is the only single-engine architecture actually available. Raphtory has no live write path and cannot serve an authorization (DESIGN.md §1.1), so "Raphtory alone" is not an option to compare against — treating it as one would understate what the second engine buys.

| Typology | Hot path alone | + Raphtory | Gain |
|---|---:|---:|---:|
| `circular_layering` | 0% | 100% | +100% |
| `device_sharing_ring` | 100% | 100% | +0% |
| `dormant_burst` | 0% | 100% | +100% |
| `mule_fan_in_out` | 0% | 100% | +100% |
| `structuring` | 33% | 100% | +67% |
| **mean** | **27%** | **100%** | **+73%** |

**3 of 5 typologies are effectively invisible to the hot path** (<25% recall) and well detected by Raphtory (>=75%): `circular_layering`, `dormant_burst`, `mule_fan_in_out`. These are the ones defined by the order and spacing of events rather than by the state they leave behind, which is the dividing line the whole project exists to measure.

<details><summary>Union vs. best single engine (secondary)</summary>

| Typology | Best single | Union | Gain |
|---|---:|---:|---:|
| `circular_layering` | 100% | 100% | +0% |
| `device_sharing_ring` | 100% | 100% | +0% |
| `dormant_burst` | 100% | 100% | +0% |
| `mule_fan_in_out` | 100% | 100% | +0% |
| `structuring` | 100% | 100% | +0% |

This framing flatters the single-engine case by letting Raphtory count as a whole architecture on typologies it happens to cover. It is kept for completeness, not as the answer.
</details>

## Time-respecting vs static cycle detection

Identical window, hop bound and seeds. The **only** difference is whether hop timestamps must increase. Rings are attributed by the transaction ids the detector actually cited, not by account membership.

| Search | Real rings | Decoy rings *(must be 0)* | Accounts flagged | Account precision |
|---|---:|---:|---:|---:|
| time-respecting | 3/3 | 0/3 | 116 | 26.7% |
| static *(control)* | 1/3 | 2/3 | 494 | 25.5% |

Ignoring event order flags **4.3x more accounts** (494 vs 116) and picks up 2 of 3 decoys — loops that are structurally real but temporally impossible. That difference is what time as a first-class dimension buys.

## Precision

| Engine | Flagged | True positives | Precision | Planted lookalikes | Decoys | Background |
|---|---:|---:|---:|---:|---:|---:|
| hot path (local) | 27 | 23 | 85.2% | 3 | 0 | 1 |
| Raphtory | 154 | 59 | 38.3% | 14 | 1 | 80 |
| union | 154 | 59 | 38.3% | 14 | 1 | 80 |

*Planted lookalikes* are the organic near-patterns — households sharing a device, housemates settling up in a loop, accounts legitimately returning from dormancy. They are deliberately hard negatives; tripping on them is a different failure from tripping on random background traffic.

## Per-signal precision

| Signal | Engine | Flagged | True positives | Precision |
|---|---|---:|---:|---:|
| `dormant_burst` | Raphtory | 4 | 4 | 100.0% |
| `fan_in_out_holding` | Raphtory | 5 | 5 | 100.0% |
| `fan_out_burst` | hot path | 3 | 3 | 100.0% |
| `pagerank_spike` | Raphtory | 10 | 2 | 20.0% |
| `ring_cohesion` | Raphtory | 27 | 23 | 85.2% |
| `shared_device` | hot path | 27 | 23 | 85.2% |
| `static_cycle` *(control)* | Raphtory | 494 | 126 | 25.5% |
| `time_respecting_cycle` | Raphtory | 116 | 31 | 26.7% |

## Cross-engine comparison

Both engines screened the same 1,502 transactions from the same event log, against the same loaded data.

| Measure | Agreement |
|---|---:|
| Identical decision | 1,500/1,502 (99.87%) |
| Identical score | 8/1,502 (0.53%) |
| Identical signal set | 8/1,502 (0.53%) |

| Engine | Screening latency p50 |
|---|---:|
| TigerGraph (Savanna) | 228.96 ms |
| SQLite (LocalStore) | 0.15 ms |

Identical decisions are expected **by construction**: the signal formulas live once in `store/screening.py`, and each store only *measures* the graph its own way — SQL joins versus GSQL accumulators. Had that maths been duplicated into GSQL, no comparison in this report would be interpretable.

