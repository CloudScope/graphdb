# PaySentry — what the experiment found

The capstone's purpose, from [DESIGN.md §1](../DESIGN.md): Lesson 10 ended on a
claim rather than a demonstration —

> The realistic answer to "TigerGraph vs. Raphtory" for this exact domain is
> **both, doing different jobs**.

PaySentry builds that architecture and then tries to falsify the claim. This is
the answer. Numbers are from the `medium` profile (5,000 accounts, 195k
transactions, 90 days, 22–30 planted rings per typology); full tables in
[`evaluation-medium.md`](evaluation-medium.md).

---

## 1. The hypothesis, and what actually happened

DESIGN.md §4.3 predicted, before any code ran, which engine would catch what.

| Typology | Predicted (hot / warm) | Hot path | Raphtory | Verdict |
|---|---|---:|---:|---|
| `structuring` | yes / yes | 92% | 48% | ✅ as predicted |
| `circular_layering` | low precision / yes | 0% | 100% | ✅ as predicted |
| `mule_fan_in_out` | no / yes | 13% | 90% | ✅ as predicted |
| `dormant_burst` | no / yes | 0% | 96% | ✅ as predicted |
| `device_sharing_ring` | yes / **no** | 100% | **96%** | ❌ **prediction wrong** |

**Four of five confirmed.** The miss is worth more than the hits.

Device-sharing rings were predicted to be invisible to Raphtory — "pure
current-state, no time reasoning required". Raphtory found 96% of them anyway,
through the `ring_cohesion` detector running community detection over the
**device co-use layer**. That is not a temporal capability. It is a *modelling*
choice: I gave Raphtory a second graph layer of account-pairs-sharing-hardware,
and community detection over it finds rings whether or not time is involved.

The honest reading: the prediction confused *"a question about current state"*
with *"a question a temporal engine cannot answer"*. Those are different. An
analytics engine handed the right graph will answer plenty of non-temporal
questions — it just cannot answer them *on the authorization path*, which is the
distinction that actually matters.

---

## 2. What the second engine buys

Compared against the **hot path alone** — the only single-engine architecture
actually available, since Raphtory has no live write path and cannot serve an
authorization at all.

| | Hot path alone | + Raphtory |
|---|---:|---:|
| mean ring recall across 5 typologies | **41%** | **96%** |

**Three of five typologies are effectively invisible to the hot path**
(`circular_layering` 0%, `dormant_burst` 0%, `mule_fan_in_out` 13%) and well
detected by Raphtory (100%, 96%, 90%).

All three are defined by the **order and spacing of events** rather than by the
state those events leave behind:

- a layering cycle is a loop whose hops happen *in an order money could follow*;
- a mule is defined by the *interval* between the last inflow and the first
  outflow;
- dormancy is a *comparison of two widely separated windows*.

Current state contains the inflows and the outflows. It does not contain the gap
between them. That is the dividing line, and it is where the second engine earns
its complexity.

> A caveat against over-claiming: measured as "union vs. the better single
> engine", the gain is only +4%, because Raphtory alone scores well on most
> typologies. That framing is misleading as an *architecture* argument — it
> quietly treats "Raphtory alone" as a deployable system. It is not one.

---

## 3. The central measurement

The sharpest result, and the one the dataset was designed around. Identical
window, hop bound, and seeds. The **only** difference is whether hop timestamps
must increase.

| Cycle search | Real rings | Decoy rings *(must be 0)* | Accounts flagged |
|---|---:|---:|---:|
| time-respecting | **22/22** | **0/22** | 89 |
| static *(control)* | 22/22 | **22/22** | 1,450 |

Identical recall. But the static search also flags **every single decoy** — loops
that are structurally real and temporally impossible, where money could not have
flowed round in any order — and raises **16.3× more accounts** for an analyst to
work through.

This measurement exists only because the generator plants decoys with two or more
cyclic descents, guaranteeing no rotation of them is increasing. Without that
control, both searches would score identically and the comparison would collapse.

**This is what "time as a first-class dimension" is worth**, stated as a number
rather than an argument.

---

## 4. Precision

| Engine | Flagged | True positives | Precision |
|---|---:|---:|---:|
| hot path | 205 | 180 | **87.8%** |
| Raphtory | 400 | 246 | 61.5% |
| union | 433 | 278 | 64.2% |

The hot path is the more precise engine, which is as it should be — it decides in
real time and a false positive blocks a customer. Raphtory is looser and cheaper
to be loose with: its output is a queue for analysts, not a payment decision.

Of Raphtory's 154 false positives, **57 are the organic near-patterns planted
deliberately** — households sharing a device, housemates settling up in a loop,
accounts legitimately returning from dormancy. A detector that trips on those is
behaving differently from one that trips on random traffic, and the report
separates them for that reason.

Per-signal, four detectors reach 100% precision (`dormant_burst`,
`fan_in_out_holding`, `fan_out_burst`, `velocity`) and three more exceed 85%. The
weakest is `pagerank_spike` at 28%: PageRank over short windows is intrinsically
spiky, the median account already scores z=18, and no threshold separates
anything — so it is used as a *ranking* (top-K per period), which is how such a
signal is used in real triage anyway.

---

## 5. The two engines, in practice

Both stores ran the same five screening checks over the same data.

| | TigerGraph (Savanna) | SQLite (LocalStore) |
|---|---:|---:|
| identical decisions | 1,502 / 1,502 (100%) | — |
| screening latency p50 | 228.96 ms | 0.19 ms |
| of which query execution | **6.7 ms** | 0.19 ms |
| of which network | 222.1 ms (97%) | — |

TigerGraph executes all five checks in **6.7 ms**. The 150 ms budget is missed by
*geography* — a laptop talking to a cloud workspace — not by the database. A
co-located application server would not pay it.

Identical decisions are expected **by construction**: the signal formulas live
once, and each store only *measures* the graph its own way (SQL joins versus GSQL
accumulators). Duplicating that maths into GSQL would have made every comparison
here uninterpretable.

And the loop closes: on TigerGraph, transactions flipped from `allow` to `review`
purely because Raphtory's temporal analytics had written risk scores that the hot
path's counterparty check then read — with no analytics running on the
authorization path.

---

## 6. So: was Lesson 10 right?

**Yes, with one correction and one caveat.**

The claim that TigerGraph and Raphtory are not competing for the same slot holds
up. Three of five fraud typologies are unreachable from the authorization path at
any level of effort, because the information they depend on — event order,
elapsed time between events, activity across separated windows — is not present
in current state. And Raphtory cannot take the other job either: it has no live
write path, so it is not a candidate for the thing that has to answer in 150 ms.

**The correction:** "Raphtory can't do X" was wrong for device rings. What
determines whether a temporal engine can answer a question is partly the
*modelling* you hand it, not just the engine's nature. The device layer was a
design decision, and it changed the answer.

**The caveat, which matters for anyone reading this as advice:** none of this
demonstrates TigerGraph specifically. SQLite reproduced its decisions exactly at
this scale, and on a 5,000-account graph a distributed engine has nothing to bite
on. What the experiment shows is that **the OLTP/OLAP split is real** — not that
a distributed graph database is the right system of record for a workload this
size. Lesson 10's own fourth question still applies: most projects never reach
the scale where TigerGraph's architecture pays for itself, and this dataset is
nowhere near it.

The two-engine architecture is justified here. The choice of *which* system of
record is not settled by anything measured above, and this report should not be
read as settling it.

---

## 7. What would strengthen this

- **Scale.** Every number is from 5,000 accounts. The `large` profile (50,000
  accounts, ~2M transactions) is generated and verified but not evaluated — that
  is where SQLite should start to fall behind and TigerGraph's contribution
  should become visible by subtraction.
- **A full TigerGraph replay.** The cross-engine comparison is a 1,502-transaction
  sample; a full replay is ~75 minutes of cloud uptime.
- **Re-verifying the counterparty fix on TigerGraph.** Two asymmetries were found
  and fixed in the code (SQLite ignored hop-1 risky counterparties; GSQL ignored
  inbound ones) after the last successful TigerGraph run.
- **`pagerank_spike`** deserves either a better formulation or removal; at 28%
  precision it currently contributes more noise than signal.
