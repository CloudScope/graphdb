# PaySentry — Design Document

**A real-time payments fraud & AML detection platform built on TigerGraph + Raphtory.**

| | |
|---|---|
| Status | Design approved — implementation not started |
| Author | Course capstone (`graphdb` repo) |
| Date | 2026-09-05 |
| Supersedes | Nothing. Extends [Lesson 10](../course/10-tigergraph-vs-raphtory-evaluation.md) from argument into running code |

---

## 1. Why this exists

Lesson 10 ended on a claim rather than a demonstration:

> The realistic answer to "TigerGraph vs. Raphtory" for this exact domain is **both, doing different jobs**.

That is an assertion. This capstone turns it into a falsifiable one. PaySentry is a working two-engine fraud platform whose **evaluation harness measures, per fraud typology, what each engine can and cannot catch**. If the course's thesis is right, there will be typologies TigerGraph detects that Raphtory structurally cannot, and typologies Raphtory detects that TigerGraph structurally cannot — and the numbers will show it. If the thesis is wrong, the numbers will show that too.

That framing drives every design decision below. This is not "a fraud demo that happens to use two databases." It is an experiment with a fraud demo wrapped around it.

### 1.1 The business scenario

A mid-size digital payments provider moves account-to-account transfers. Two obligations pull in opposite directions:

1. **Regulatory / customer-facing (hard real-time).** Every transfer must be screened *before* it settles. A decision — `ALLOW` / `REVIEW` / `BLOCK` — is needed inside the payment authorization window. Miss the window and you either hold up legitimate customers or let fraud settle irreversibly.
2. **Compliance / investigative (deep, retrospective).** An AML analyst must be able to ask "which clusters of accounts, over the last quarter, moved money in a time-respecting cycle through shared devices?" Answering that requires reading the whole graph across months of history — a question you cannot ask on the authorization path without destroying it.

These are the two halves of Lesson 6's OLTP/OLAP split, and they are the reason two engines exist here rather than one.

### 1.2 Why a graph at all (Lesson 1's question, asked honestly)

Before reaching for any of this machinery, the Lesson 1 test: are the real queries deep, relationship-heavy traversals? For the hot path, partly — "does this transaction's device fingerprint appear on any account within 2 hops of a confirmed-fraud account" is a genuine traversal that a relational engine answers with self-joins that get worse with each hop. For the analytical path, unambiguously — time-respecting cycle detection over a 90-day window is not a SQL query anyone wants to write or run.

Had the answer been "most queries are one hop," the honest recommendation would have been Postgres and this document would not exist. It is worth stating that the design was tested against that question rather than assuming past it.

### 1.3 Non-goals

- **Not a production AML system.** No model governance, no case management, no SAR filing, no regulatory sign-off. Detection logic is illustrative.
- **Not a benchmark.** Savanna free tier and a laptop are not a fair performance comparison, and any latency numbers reported are descriptive, not competitive.
- **Not a Kafka deployment.** The machine running this has no Docker and no Java. Streaming is modelled with an append-only log (see §4.1), which is architecturally the same idea at 1% of the setup cost.
- **No ML models.** Detection is rule- and algorithm-based. Adding a GNN would obscure exactly the engine-capability question this exists to answer.

---

## 2. Architecture

```
                          ┌──────────────────────────────────┐
                          │   Synthetic Event Generator      │   Phase 1
                          │   (customers, accounts, devices, │
                          │    background traffic + 5        │
                          │    planted fraud typologies)     │
                          └───────────────┬──────────────────┘
                                          │ writes
                                          ▼
                          ┌──────────────────────────────────┐
                          │  events.jsonl  (append-only log) │  ← the real system
                          │  + labels.jsonl (ground truth)   │    of record
                          └───────────────┬──────────────────┘
                                          │ replayed at accelerated wall-clock
                                          ▼
            ┌─────────────────────────────────────────────────────────┐
            │                  Stream Replayer                        │   Phase 6
            └───────┬─────────────────────────────────────────┬───────┘
                    │ per event, synchronous                  │ (history already loaded)
                    ▼                                         │
   ═══════════ HOT PATH (OLTP) ═══════════                     │
   ┌──────────────────────────────────┐                        │
   │  GraphStore (interface)          │                        │
   │   ├─ SavannaStore  (TigerGraph)  │  Phase 2/3             │
   │   └─ LocalStore    (SQLite)      │                        │
   │                                  │                        │
   │  upsert txn  →  screen(txn)      │                        │
   │  installed GSQL queries          │                        │
   │  budget: p95 < 150 ms            │                        │
   └───────────────┬──────────────────┘                        │
                   │ ALLOW / REVIEW / BLOCK                    │
                   ▼                                           │
             decisions.jsonl                                   │
                                                               │
                   ▲                                           │
                   │ (5) risk scores read by next screening    │
                   │                                           │
   ┌───────────────┴──────────────────┐                        │
   │  (4) Feedback: write_risk()      │  Phase 6               │
   │  UPSERT Account.risk_score,      │                        │
   │  risk_reasons, ring_id           │                        │
   └───────────────▲──────────────────┘                        │
                   │                                           │
   ═══════════ WARM PATH (OLAP, scheduled) ═════════           │
   ┌───────────────┴──────────────────┐                        │
   │  Risk Scorer                     │  Phase 5               │
   │   aggregates temporal signals    │                        │
   └───────────────▲──────────────────┘                        │
                   │                                           │
   ┌───────────────┴──────────────────┐                        │
   │  Raphtory (local, in-process)    │  Phase 4/5             │
   │   layer "transfer": Acct→Acct    │◄───────────────────────┘
   │   layer "device":   Acct↔Acct    │   (3) extract edge list
   │                                  │       from GraphStore
   │   • temporally_reachable_nodes   │       (or straight from the log)
   │   • rolling() windowed pagerank  │
   │   • louvain over windows         │
   │   • temporal 3-node motifs       │
   │   • fan-in/fan-out holding time  │
   └──────────────────────────────────┘
```

**The load-bearing property of this diagram: raw data flows one way (TigerGraph → Raphtory), results flow the other (Raphtory → TigerGraph), and no analytics ever execute on the authorization path.** That is Lesson 9's workload isolation applied across two products instead of one.

### 2.1 Component responsibilities

| Component | Owns | Explicitly does not own |
|---|---|---|
| Event generator | Entity population, background traffic, planted typologies, ground-truth labels | Any detection logic |
| Append-only log | Durability. The actual system of record | Query capability |
| TigerGraph (Savanna) | Live screening, current-state graph, persisted risk attributes | Anything requiring time-ordered traversal or whole-graph iteration |
| Raphtory (local) | Temporal analytics over history; produces risk signals | Durability guarantees, the live authorization path, anything an application's writes depend on |
| Feedback writer | Moving Raphtory's conclusions into TigerGraph's attributes | Recomputing anything |
| Eval harness | Scoring both engines against ground truth, per typology | Being part of the runtime path |

### 2.2 Why the log is the system of record, not TigerGraph

Lesson 9 raised the possibility that the real system of record is the transaction log, with both engines as downstream consumers. This design takes that position deliberately, for a reason that matters in practice: **it makes both engines rebuildable.** Drop the Savanna workspace, replay the log, and the graph is back. Change the Raphtory model, replay, done. If TigerGraph were the sole system of record, every schema change would be a migration rather than a re-derivation — and on a free tier that gets torn down and recreated, that would be intolerable.

The cost is honest and worth naming: two consumers of one log can drift out of sync, and this design has no distributed transaction to prevent it. For a demonstration platform where the warm path is scheduled and eventually-consistent by construction, that is acceptable. For a real payments system it would not be, and the mitigation would be idempotent replay with a watermark — which §11 sketches but does not build.

---

## 3. Data model

The same domain is modelled **twice, differently**, and the difference is one of the primary things this capstone exists to make visible.

### 3.1 TigerGraph — schema-first property graph

Vertices:

| Vertex | Primary id | Key attributes |
|---|---|---|
| `Customer` | `customer_id` | `name`, `country`, `kyc_level`, `onboarded_at` |
| `Account` | `account_id` | `opened_at`, `account_type`, `status`, **`risk_score`**, **`risk_reasons`**, **`ring_id`**, **`scored_at`** |
| `Device` | `device_id` | `fingerprint`, `os`, `first_seen` |
| `Merchant` | `merchant_id` | `name`, `mcc`, `country` |
| `Txn` | `txn_id` | `amount`, `currency`, `ts`, `channel`, `status` |

Edges: `OWNS` (Customer→Account), `SENT` (Account→Txn), `RECEIVED_BY` (Txn→Account), `VIA_DEVICE` (Txn→Device), `AT_MERCHANT` (Txn→Merchant), and `USED_DEVICE` (Account→Device).

Two modelling decisions worth defending:

- **`Txn` is a promoted node, not an edge.** Lesson 4's n-ary rule: a transaction relates a sender, a receiver, a device, and sometimes a merchant. That is a 4-way relationship, and an edge can only hold two endpoints. Promotion is forced, not stylistic.
- **`USED_DEVICE` is deliberate denormalization.** It is fully derivable by walking `Account → SENT → Txn → VIA_DEVICE → Device`. It exists because the single hottest screening query is device-sharing, and paying two hops on every authorization to re-derive a fact that never changes is the wrong trade. This is Lesson 4's "denormalize the hot path," with the accepted cost that it must be maintained on write.

The bold attributes on `Account` are **write-back targets** — the only fields Raphtory ever causes to change, and the entire surface area of the feedback loop.

### 3.2 Raphtory — temporal multilayer graph

No `Txn` node. Each transfer is **one timestamped edge**, and the timestamp gives the event its identity — the argument Lesson 8 made and Lab 8 demonstrated.

```
layer "transfer":  Account --[t=txn_ts, {amount, device_id, channel, txn_id}]--> Account
layer "device":    Account --[t=first_co_use, {device_id}]--> Account   (co-use, both directions)
```

Raphtory 0.17 supports layers natively (`add_edge(..., layer=...)`, `g.layer("transfer")`), which matters here: cycle detection must run over transfers only, while ring cohesion wants transfers *and* device co-use together. One graph, two views, no second ingest.

### 3.3 Why the two models diverge

| | TigerGraph | Raphtory |
|---|---|---|
| A transaction is | a vertex | an edge with a timestamp |
| Time is | an attribute you filter | the indexing dimension |
| Schema | declared before any load | inferred from what you ingest |
| Question it answers well | "what is true about this account *now*" | "what happened, in what order, over this interval" |

The temptation to build one canonical model and share it would be a design error. The models differ because the questions differ, and flattening them would forfeit the capability each engine is here to provide.

---

## 4. Synthetic data design

The dataset is not decoration. It is the experiment's control, and it is designed so that **each engine is provably necessary**.

### 4.1 Entity population (per profile)

| Profile | Customers | Accounts | Devices | Merchants | Transactions | Span | Purpose |
|---|---|---|---|---|---|---|---|
| `small` | 300 | 500 | 220 | 60 | ~20,000 | 30 d | Fast iteration, near-zero Savanna burn |
| `medium` | 3,000 | 5,000 | 2,200 | 400 | ~200,000 | 90 d | Default demo; all typologies visible |
| `large` | 30,000 | 50,000 | 21,000 | 2,500 | ~2,000,000 | 180 d | Feel Raphtory's memory ceiling and Savanna load time |

Generation is **seeded and deterministic** — `--seed` produces byte-identical output. Without that, an eval harness comparing two engines' recall is comparing noise.

Output is three files:
- `events.jsonl` — the append-only log; one JSON object per event, ordered by timestamp.
- `entities.json` — customers, accounts, devices, merchants (the static population).
- `labels.jsonl` — ground truth: `{txn_id, account_id, typology, ring_id}`. **Consumed only by the eval harness, never by any detector.**

### 4.2 Background traffic

Realistic enough that detectors cannot win by accident:

- **Diurnal + weekly rhythm** — volume peaks 09:00–21:00 local, drops overnight, dips on weekends. A naive "3am transaction is suspicious" rule must not be free money.
- **Amount distribution** — log-normal, with deliberate clustering at round numbers (100, 500, 1000). This matters: it means structuring (§4.3.1) cannot be found by "amounts look round."
- **Payment mix** — salary credits (monthly, regular counterparty), retail purchases (merchant-directed), P2P transfers (small, bursty, within a social neighbourhood), bill payments (monthly, recurring).
- **Organic near-patterns** — a small population of legitimate accounts that *incidentally* share a device (family), transact in a triangle (housemates settling up), or go dormant then return (seasonal). **These generate the false positives**, and a detector that can't distinguish them from real rings will show it in precision.

That last bullet is the most important line in this section. Planting fraud against a clean background produces a system that scores 100% and proves nothing.

### 4.3 Planted typologies

Five, chosen so the detection burden splits across the two engines rather than pooling in one.

#### 4.3.1 Structuring / smurfing — *split*
One source account disperses to 8–15 mule accounts in amounts just under a 10,000 reporting threshold, inside a 48-hour window; mules then forward to a single collector.
Detectable by: **TigerGraph** (fan-out degree + amount clustering near threshold, shallow) *and* **Raphtory** (windowed burst).

#### 4.3.2 Circular layering — *Raphtory only*
4–6 accounts move funds in a closed cycle back to the origin within 72 hours, each hop retaining 90–98% of the previous amount (a fee skim).
**This is the discriminating case.** TigerGraph can find *a cycle* — a static closed loop is an ordinary traversal. What it cannot cheaply establish is whether the cycle is **time-respecting**: whether the hops actually happened in an order that lets money flow around it. The generator plants both real time-respecting cycles *and* decoy cycles whose edge timestamps are out of order (structurally identical, financially impossible). A static cycle query fires on both. `temporally_reachable_nodes` fires only on the real ones. The precision gap between the two is the single cleanest measurement in this project.

#### 4.3.3 Mule fan-in / fan-out — *Raphtory only*
An account receives from many sources and forwards ≥85% of the inflow within a 24-hour holding period.
The defining feature is *elapsed time between in and out*, per account, over a rolling window. Point-in-time state does not contain it.

#### 4.3.4 Device-sharing ring — *TigerGraph only*
5–8 accounts belonging to nominally unrelated customers transact from 1–2 shared device fingerprints.
Pure current-state, 1–2 hops, no time reasoning required. This is the case where reaching for Raphtory would be over-engineering, and it is included specifically so the results table has a row where TigerGraph wins outright.

#### 4.3.5 Dormant reactivation burst — *Raphtory only*
An account inactive ≥180 days suddenly transacts 20+ times within 6 hours.
Requires comparing activity across two widely separated windows — `rolling()` / `expanding()` territory.

**Expected outcome (the hypothesis this project tests).** Measured results are in
[`reports/evaluation-medium.md`](reports/evaluation-medium.md); where they differ
from this table, the report is right and this row is the prediction that was
wrong. Two design calls here were superseded during implementation — see
[`docs/decisions/`](docs/decisions/).

| Typology | TigerGraph | Raphtory |
|---|---|---|
| Structuring | ✅ | ✅ |
| Circular layering | ⚠️ low precision (decoys) | ✅ |
| Mule fan-in/out | ❌ | ✅ |
| Device-sharing ring | ✅ | ❌ (not modelled temporally) |
| Dormant burst | ❌ | ✅ |

If the implementation reproduces this table, Lesson 10's thesis holds. If it does not, the design document was wrong and the finding is more interesting than the demo.

---

## 5. Hot path — TigerGraph

### 5.1 Screening flow

Per arriving transaction, synchronously:

1. `UPSERT` the `Txn` vertex and its `SENT` / `RECEIVED_BY` / `VIA_DEVICE` / `AT_MERCHANT` edges, plus `USED_DEVICE` if new.
2. Run installed GSQL query `screenTransaction(txn_id)`.
3. Map the returned signals to a decision via thresholds in `config.yaml`.
4. Append to `decisions.jsonl`.

### 5.2 Installed GSQL queries

| Query | What it does | Typology |
|---|---|---|
| `screenTransaction(txn_id)` | Orchestrator; calls the checks below and returns accumulated signals | — |
| `sharedDeviceRisk(account_id)` | Accounts reachable via `USED_DEVICE` within 2 hops; flags any with `risk_score` above threshold or confirmed fraud | 4.3.4 |
| `counterpartyRisk(account_id)` | 2-hop transfer neighbourhood; surfaces max/mean `risk_score` — **this is where Raphtory's write-back is consumed** | feedback |
| `velocityCheck(account_id)` | Count and sum of outbound `Txn` in the trailing 1h / 24h | 4.3.1 |
| `fanOutBurst(account_id)` | Distinct recipients in trailing 48h + count of amounts within 5% below threshold | 4.3.1 |

All are `INSTALL`ed ahead of time — Lesson 7's compiled-query model, with the trade it implies: fast repeated execution, and a reinstall required for every logic change.

### 5.3 Latency budget

| Segment | Target (p95) |
|---|---|
| Upsert | 60 ms |
| `screenTransaction` | 70 ms |
| Decision mapping | 5 ms |
| **Total** | **< 150 ms** |

Dominated by cloud round-trip, not query execution. `LocalStore` should land under 5 ms, and the gap between the two is a useful lesson in itself: managed cloud convenience costs a round trip that a co-located deployment does not.

**Measured (TigerGraph 4.2.5, free-tier Savanna, `small` profile):** bare round trip 222.1 ms p50; full `screenTransaction` 228.9 ms p50; **query execution 6.7 ms — 3% of the call**, network 97%. The prediction holds: the budget is missed by geography, not by the database. `LocalStore` measured 3.19 ms p50 for the same five checks. See [`docs/decisions/003-gsql-and-savanna.md`](docs/decisions/003-gsql-and-savanna.md).

### 5.4 The `GraphStore` abstraction

```python
class GraphStore(Protocol):
    def provision(self) -> None: ...
    def bulk_load(self, dataset: Dataset) -> LoadStats: ...
    def upsert_txn(self, txn: Txn) -> None: ...
    def screen(self, txn: Txn) -> ScreenResult: ...
    def export_transfers(self, since: int, until: int) -> pa.Table: ...
    def write_risk(self, scores: list[RiskScore]) -> None: ...
```

Two implementations: `SavannaStore` (pyTigerGraph 2.0.4 over REST++) and `LocalStore` (SQLite, same semantics).

`LocalStore` earns its keep three ways: development and tests run offline with zero credit burn; the eval harness can run end-to-end in CI-like conditions; and — the architecturally interesting one — **it makes TigerGraph's specific contribution measurable by subtraction.** Swap the store, rerun the eval, and whatever changes is what the graph database actually bought. `LocalStore` is a development aid, not a claim that SQLite is a graph database; it will get materially worse as hop count and data size grow, and demonstrating that is a legitimate use of the `large` profile.

`export_transfers` returns an Arrow table because Raphtory's `Graph.load_edges()` consumes anything implementing `__arrow_c_stream__` — which makes bulk ingest a single vectorized call instead of a per-edge Python loop.

---

## 6. Warm path — Raphtory

### 6.1 Build

```python
g = Graph()
g.load_edges(transfers_arrow, time="ts", src="src_account", dst="dst_account",
             properties=["amount", "device_id", "channel", "txn_id"], layer="transfer")
g.load_edges(device_pairs_arrow, time="first_co_use", src="a", dst="b",
             properties=["device_id"], layer="device")
```

Rebuilt from scratch each cycle in the default design. Raphtory can `save_to_file` / `load_from_file`, and incremental append is available if rebuild time becomes the bottleneck at the `large` profile — deferred until measured, not assumed.

### 6.2 Analytics

| Signal | Raphtory API | Typology |
|---|---|---|
| Time-respecting cycle | `temporally_reachable_nodes(g.layer("transfer"), max_hops=6, start_time=t0, seed_nodes=candidates)` — a node reaching **itself** is a genuine time-respecting cycle | 4.3.2 |
| Static-cycle control | Same cycle search ignoring order, for the precision comparison in §4.3.2 | 4.3.2 |
| Fan-in/out holding time | Per node over `g.rolling(window="24h")`: inbound sum, outbound sum, elapsed in→out | 4.3.3 |
| Dormancy burst | `g.expanding()` activity profile; long gap followed by dense burst | 4.3.5 |
| Windowed centrality | `pagerank` / `degree_centrality` per `rolling()` window; flag rank spikes | 4.3.1 |
| Ring cohesion | `louvain` over the last 30 days on both layers; small dense communities with high internal flow | 4.3.2/4.3.4 |
| Layering motifs | `global_temporal_three_node_motif` / `local_temporal_three_node_motifs` for chain-shaped temporal motifs | 4.3.2 |

Cycle search is **seeded, not exhaustive** — `temporally_reachable_nodes` runs from accounts already flagged by cheaper signals (velocity, community, motif participation), not from all 50,000. Whole-graph all-pairs temporal reachability does not fit in a laptop's memory at the `large` profile, and pretending otherwise would produce a design that only works on the demo.

### 6.3 Risk scoring

Each signal emits `RiskSignal(account_id, kind, strength ∈ [0,1], window, evidence)`. Signals aggregate into `RiskScore(account_id, score ∈ [0,100], reasons: list[str], ring_id: str | None)`.

Aggregation is a **transparent weighted sum with weights in `config.yaml`** — chosen over anything learned because the point of this project is to attribute detection capability to an engine, and a learned scorer would make attribution impossible.

---

## 7. Feedback loop

Batched `UPSERT` of `risk_score`, `risk_reasons`, `ring_id`, `scored_at` onto `Account`.

Four rules the loop must obey:

1. **Results only, never raw data.** Nothing Raphtory computed as an intermediate goes back — only final per-account scores.
2. **Idempotent.** Rerunning a cycle over the same window converges to the same attribute values.
3. **Stamped.** `scored_at` lets the hot path discount stale scores rather than trusting them indefinitely.
4. **Never blocking.** A failed write-back degrades detection quality; it must never fail an authorization.

Closing the loop is what makes this a system rather than two scripts: `counterpartyRisk` (§5.2) reads `risk_score`, so a ring Raphtory found on Monday changes how TigerGraph screens a transaction on Tuesday — with zero analytics running on the authorization path.

---

## 8. Evaluation harness

The deliverable that makes this a capstone rather than a demo. Replays a full dataset, collects detections from both engines, joins against `labels.jsonl`, and emits:

- **Per-typology precision / recall / F1, per engine** — the §4.3 table, filled in with measurements.
- **Union vs. best-single-engine recall** — quantifying what the second engine actually adds. If the union barely beats the better single engine, the two-engine architecture is not justified for this workload, and the honest report says so.
- **Time-respecting vs. static cycle precision** — the §4.3.2 measurement.
- **Latency distribution** — hot path p50/p95/p99, `SavannaStore` vs `LocalStore`.
- **Cost** — Savanna credits consumed per run.

Output: a markdown report in `capstone/reports/`, plus a published artifact when there are numbers worth looking at.

---

## 9. Repository layout

```
capstone/
├── DESIGN.md                     ← this document
├── README.md                     ← quickstart
├── requirements.txt
├── config.yaml                   ← thresholds, windows, weights, profiles
├── .env.example                  ← TG_HOST / TG_USER / TG_PASS / TG_GRAPH
├── .gitignore                    ← .env, data/, .venv/, reports/*.json
├── docs/
│   ├── tigergraph-setup.md
│   ├── raphtory-notes.md
│   └── decisions/                ← ADRs
├── data/                         ← generated, gitignored
├── reports/
└── paysentry/
    ├── config.py
    ├── models.py                 ← Customer, Account, Device, Txn, RiskSignal, RiskScore
    ├── timeutil.py
    ├── generator/                ← Phase 1
    │   ├── population.py
    │   ├── background.py
    │   ├── typologies.py
    │   └── generate.py
    ├── store/                    ← Phase 2/3
    │   ├── base.py               ← GraphStore protocol
    │   ├── savanna.py
    │   ├── local.py
    │   └── gsql/
    │       ├── schema.gsql
    │       ├── loading_jobs.gsql
    │       └── queries/*.gsql
    ├── temporal/                 ← Phase 4/5
    │   ├── extract.py
    │   ├── build.py
    │   ├── signals.py
    │   └── score.py
    ├── feedback/writeback.py     ← Phase 6
    ├── pipeline/
    │   ├── replay.py
    │   └── orchestrator.py
    ├── evaluation/
    │   ├── metrics.py
    │   └── report.py
    └── cli.py                    ← paysentry generate | provision | load | replay | analyze | evaluate
```

Raphtory runs entirely in-process from `capstone/.venv` — no server, no
container, consistent with a machine that has neither Docker nor Java.

**A precision worth keeping.** "Raphtory has no server" would be wrong: 0.17
ships `raphtory.graphql.GraphServer`, which binds a port, loads saved graphs from
a working directory, serves GraphQL with a browsable UI, and accepts remote
mutations via `RemoteGraph` / `RemoteUpdate`. `paysentry serve` runs it. What is
true is narrower and is what this design actually relies on: **PaySentry uses
Raphtory embedded**, and even its server mode lives inside the process that
started it and dies with it — no daemon, no replication, no failover, no
transactional write guarantees, no latency contract. That is a difference of
degree from a system of record rather than of kind, and §2.2's reasoning depends
only on the degree. See
[Lesson 10's correction](../course/10-tigergraph-vs-raphtory-evaluation.md#a-correction-raphtory-does-have-a-server).

---

## 10. Configuration & operations

### 10.1 Secrets
Savanna credentials live in `capstone/.env` (gitignored); `.env.example` is committed. No credential is ever written to `config.yaml`, a report, or a log line.

### 10.2 Savanna free tier
Confirmed from TigerGraph's published quota policy: the Free plan allows a max workspace size of TG-4, one read-write workspace, and two read-only workspaces per account. **Free-credit amount and expiry, and idle-suspend behaviour, are not documented publicly and must be read off the console at signup** — `docs/tigergraph-setup.md` will record what the account actually shows rather than guessing.

Operational consequences baked into the design:
- Default to `LocalStore`; `SavannaStore` is opt-in per command.
- `small` profile for all development; `medium` only for demo runs.
- Assume the workspace may be suspended between sessions — every command must tolerate a cold start and re-provision idempotently.
- Provisioning is re-runnable end-to-end, because the workspace *will* get torn down and recreated at least once.

### 10.3 Versions
Verified on this machine (2026-09-05): Python 3.13.2, `raphtory` **0.17.0** (installs clean; `temporally_reachable_nodes`, `louvain`, `rolling`/`expanding`, layers, and Arrow `load_edges` all present), `pyTigerGraph` **2.0.4**. Both APIs have historically drifted between releases — `requirements.txt` pins exact versions, and `docs/raphtory-notes.md` records any signature that moves.

---

## 11. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Savanna free credits exhausted mid-project | Hot path unavailable | `LocalStore` default; `small` profile for dev; credits tracked per run in the eval report |
| Savanna workspace auto-suspends between sessions | Commands fail on cold start | Idempotent re-provisioning; retry-with-backoff on first connect |
| GSQL syntax drifts from what's written | Provisioning fails | Read errors and adjust — the same expectation Lab 7 set; every deviation recorded in `docs/` |
| Raphtory API drifts | Analytics break | Pinned version; signatures verified against 0.17.0 before being written into code |
| `large` profile exceeds laptop memory | Analytics OOM | Seeded (not exhaustive) cycle search; windowed subgraphs; `large` is explicitly a stress profile, and hitting the ceiling is a documented finding, not a bug |
| Planted fraud too easy to detect | Results prove nothing | Organic near-patterns in the background (§4.2); decoy non-time-respecting cycles; precision reported alongside recall |
| Two consumers of one log drift apart | Inconsistent detections | Accepted for a scheduled warm path; watermark-based idempotent replay is the documented fix if it bites |
| Scope creep into a real AML product | Never finishes | §1.3 non-goals are binding |

---

## 12. Delivery plan

Seven phases. Each has a deliverable and an exit criterion; nothing proceeds until its exit criterion is met.

| Phase | Deliverable | Exit criterion |
|---|---|---|
| **0 — Scaffold** | Package layout, `config.py`, `models.py`, `timeutil.py`, venv, pinned deps, `.env.example`, CLI skeleton | `paysentry --help` runs; venv has raphtory 0.17.0 + pyTigerGraph 2.0.4 |
| **1 — Data generation** | Full generator: population, background traffic, 5 typologies, ground truth | `paysentry generate --profile small --seed 42` twice → identical output; every typology present in `labels.jsonl` |
| **2 — TigerGraph provisioning & load** | GSQL schema, loading jobs, `SavannaStore.provision/bulk_load`, `LocalStore` equivalent | Both stores load `small` and report matching vertex/edge counts |
| **3 — Hot path** | 5 installed GSQL queries, `screen()` on both stores, decision mapping | `paysentry replay --profile small --store local` produces `decisions.jsonl`; device-ring typology detected; Savanna p95 measured |
| **4 — Raphtory ingest** | `extract.py`, `build.py`, multilayer temporal graph from Arrow | Raphtory node/edge counts reconcile with the store; a `window()` and a `snapshot_at()` return the expected slices |
| **5 — Temporal analytics** | All §6.2 signals + `score.py` | Circular-layering, fan-in/out, and dormant-burst typologies each detected above chance; time-respecting vs. static precision gap measured |
| **6 — Closed loop** | `writeback.py`, orchestrator, accelerated stream replay | A Raphtory-derived `risk_score` demonstrably changes a later `screenTransaction` decision — end to end, on Savanna |
| **7 — Evaluation & write-up** | Eval harness, per-typology report, published artifact, course README updated | §4.3 hypothesis table filled with real measurements; two-engine value quantified — whichever way it comes out |

Phases 1 and 4–5 need no cloud account and can proceed while Savanna signup is pending; only Phases 2, 3, 6, and 7 touch it.

---

## 13. Decisions log

| # | Decision | Alternative rejected | Why |
|---|---|---|---|
| 1 | Payments fraud / AML domain | Network monitoring, recommendations, dispatch | Continues the course's running example; Lesson 10 already specifies this architecture, so the app is the thesis under test |
| 2 | Append-only log as system of record | TigerGraph as system of record | Makes both engines rebuildable from scratch — essential on a free tier that will be torn down |
| 3 | `GraphStore` protocol with a SQLite fallback | Direct TigerGraph coupling | Offline dev, zero credit burn, and it makes TigerGraph's contribution measurable by subtraction |
| 4 | Two divergent data models | One shared canonical model | Vertex-vs-timestamped-edge is the capability difference; flattening it forfeits what each engine is here for |
| 5 | File-based streaming | Kafka | Machine has no Docker/Java; the log abstraction is architecturally equivalent for this purpose |
| 6 | Transparent weighted-sum scoring | Learned model | Attribution of detection capability to an engine requires an interpretable scorer |
| 7 | Seeded, not exhaustive, cycle search | All-pairs temporal reachability | All-pairs does not fit in laptop memory at the `large` profile |
| 8 | Decoy non-time-respecting cycles in the data | Only genuine cycles | Without decoys, static and temporal cycle detection score identically and the central comparison collapses |
| 9 | Tiered `small`/`medium`/`large` profiles | Single fixed dataset | Dev speed, credit control, and a real scale ceiling to measure |

---

## 14. Open questions

1. **Savanna free-tier credit and idle-suspend specifics** — undocumented publicly; resolve at signup (Phase 2) and record in `docs/tigergraph-setup.md`.
2. **Warm-path cadence** — the design assumes scheduled batch. Whether a shorter cycle (e.g. every 15 simulated minutes) is worth the rebuild cost is a Phase 6 measurement, not a Phase 0 assumption.
3. **Raphtory rebuild vs. incremental append** at the `large` profile — decide with a timing number in Phase 5, not now.
