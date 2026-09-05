# PaySentry evaluation — profile `medium`

Generated 2026-09-05 09:56 UTC · 5,000 accounts · 200,000 transactions · 90d span

**Hot path executed by: `local`.** Warm path: Raphtory. Note that `local` is the SQLite stand-in, not TigerGraph — these hot-path numbers say what the *checks* catch, not what a graph database adds.

Recall is measured per **ring** — catching one leg of a ring is catching the ring. Precision is measured per **account**, because that is what a false positive costs: an analyst opens a case. `decoy_cycle` is **not fraud**; detections on it are false positives, and that is the point of it.

## Recall by typology

| Typology | Rings | Hot path (`local`) | Raphtory | Union | Predicted (hot / warm) |
|---|---:|---:|---:|---:|---|
| `circular_layering` | 22 | 0% | 100% | 100% | low precision / yes |
| `device_sharing_ring` | 24 | 100% | 96% | 100% | yes / no |
| `dormant_burst` | 28 | 0% | 96% | 96% | no / yes |
| `mule_fan_in_out` | 30 | 13% | 90% | 90% | no / yes |
| `structuring` | 25 | 92% | 48% | 96% | yes / yes |
| `decoy_cycle` *(control — lower is better)* | 22 | 0% | 5% | 5% | — |

## What the second engine adds

Measured against the **hot path alone**, because that is the only single-engine architecture actually available. Raphtory has no live write path and cannot serve an authorization (DESIGN.md §1.1), so "Raphtory alone" is not an option to compare against — treating it as one would understate what the second engine buys.

| Typology | Hot path alone | + Raphtory | Gain |
|---|---:|---:|---:|
| `circular_layering` | 0% | 100% | +100% |
| `device_sharing_ring` | 100% | 100% | +0% |
| `dormant_burst` | 0% | 96% | +96% |
| `mule_fan_in_out` | 13% | 90% | +77% |
| `structuring` | 92% | 96% | +4% |
| **mean** | **41%** | **96%** | **+55%** |

**3 of 5 typologies are effectively invisible to the hot path** (<25% recall) and well detected by Raphtory (>=75%): `circular_layering`, `dormant_burst`, `mule_fan_in_out`. These are the ones defined by the order and spacing of events rather than by the state they leave behind, which is the dividing line the whole project exists to measure.

<details><summary>Union vs. best single engine (secondary)</summary>

| Typology | Best single | Union | Gain |
|---|---:|---:|---:|
| `circular_layering` | 100% | 100% | +0% |
| `device_sharing_ring` | 100% | 100% | +0% |
| `dormant_burst` | 96% | 96% | +0% |
| `mule_fan_in_out` | 90% | 90% | +0% |
| `structuring` | 92% | 96% | +4% |

This framing flatters the single-engine case by letting Raphtory count as a whole architecture on typologies it happens to cover. It is kept for completeness, not as the answer.
</details>

## Time-respecting vs static cycle detection

Identical window, hop bound and seeds. The **only** difference is whether hop timestamps must increase. Rings are attributed by the transaction ids the detector actually cited, not by account membership.

| Search | Real rings | Decoy rings *(must be 0)* | Accounts flagged | Account precision |
|---|---:|---:|---:|---:|
| time-respecting | 22/22 | 0/22 | 89 | 31.5% |
| static *(control)* | 22/22 | 22/22 | 1,450 | 23.5% |

Ignoring event order flags **16.3x more accounts** (1,450 vs 89) and picks up 22 of 22 decoys — loops that are structurally real but temporally impossible. That difference is what time as a first-class dimension buys.

## Precision

| Engine | Flagged | True positives | Precision | Planted lookalikes | Decoys | Background |
|---|---:|---:|---:|---:|---:|---:|
| hot path (local) | 205 | 180 | 87.8% | 4 | 0 | 21 |
| Raphtory | 400 | 246 | 61.5% | 57 | 1 | 96 |
| union | 433 | 278 | 64.2% | 58 | 1 | 96 |

*Planted lookalikes* are the organic near-patterns — households sharing a device, housemates settling up in a loop, accounts legitimately returning from dormancy. They are deliberately hard negatives; tripping on them is a different failure from tripping on random background traffic.

## Per-signal precision

| Signal | Engine | Flagged | True positives | Precision |
|---|---|---:|---:|---:|
| `counterparty_risk` | hot path | 205 | 180 | 87.8% |
| `dormant_burst` | Raphtory | 27 | 27 | 100.0% |
| `fan_in_out_holding` | Raphtory | 26 | 26 | 100.0% |
| `fan_out_burst` | hot path | 23 | 23 | 100.0% |
| `near_threshold` | hot path | 24 | 23 | 95.8% |
| `pagerank_spike` | Raphtory | 100 | 28 | 28.0% |
| `ring_cohesion` | Raphtory | 173 | 148 | 85.5% |
| `shared_device` | hot path | 183 | 158 | 86.3% |
| `static_cycle` *(control)* | Raphtory | 1,450 | 341 | 23.5% |
| `time_respecting_cycle` | Raphtory | 89 | 28 | 31.5% |
| `velocity` | hot path | 23 | 23 | 100.0% |

