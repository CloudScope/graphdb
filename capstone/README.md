# PaySentry — Capstone

A real-time payments fraud & AML detection platform built on **TigerGraph (Savanna, cloud)** for live screening and **Raphtory (local, in-process)** for temporal analytics.

This is the course's conclusion turned into running code. [Lesson 10](../course/10-tigergraph-vs-raphtory-evaluation.md) argued that TigerGraph and Raphtory aren't competing for the same slot and that the realistic architecture uses **both, doing different jobs**. PaySentry builds that architecture and then tries to *falsify* the claim: its evaluation harness measures, per fraud typology, what each engine can and cannot catch.

**Read [DESIGN.md](DESIGN.md) first** — it's the full architecture, data model, and phase plan.

## Status

| Phase | What | Status |
|---|---|---|
| 0 | Scaffold, config, models, CLI skeleton | **Done** |
| 1 | Synthetic data generator + ground truth | **Done** |
| 2 | Store abstraction + bulk load | **Done — both stores**, counts reconcile exactly |
| 3 | Hot path — 5 screening checks | **Done — both stores**, 20/20 identical decisions |
| 4 | Raphtory ingest — multilayer temporal graph | **Done** |
| 5 | Temporal analytics + risk scoring | **Done** |
| 6 | Closed loop — write-back | **Done (LocalStore)** |
| 7 | Evaluation harness + report | **Done** — see [`reports/CONCLUSION.md`](reports/CONCLUSION.md) |

All of it runs. `--store local` (SQLite) is the default and costs nothing;
`--store savanna` runs against a real TigerGraph workspace and burns free-tier
credits. Verified live on TigerGraph 4.2.5.

## Quickstart

Phase 0 is built, so this works now:

```bash
cd capstone
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e .

.venv/bin/paysentry info --profile small
```

```bash
.venv/bin/paysentry generate --profile small --seed 42 --verify
```

`generate` writes four files under `data/<profile>/`: `events.jsonl` (the
append-only log — the system of record both engines consume), `entities.json`
(the static population), `labels.jsonl` (ground truth, read only by the
evaluation harness), and `manifest.json` (counts plus a SHA-256 per file, so
determinism is checkable rather than asserted). `--verify` re-reads the written
files and checks the eleven structural claims the later phases depend on.

Generation is deterministic: the same `--seed` produces byte-identical output.
The `large` profile is ~1.95M transactions in about 12s at ~490MB peak RSS.

`info` prints the resolved config, the dataset shape and planted ring counts for
the profile, the installed engine versions, whether Savanna credentials are
present, and which data files exist. Every other subcommand parses and validates
its arguments today but exits with a pointer to the phase that implements it.

Once later phases land:

```bash
paysentry generate --profile small --seed 42
paysentry replay   --profile small --store local
paysentry analyze  --profile small
paysentry evaluate --profile small
```

`--store local` uses the SQLite fallback and costs nothing. `--store savanna`
talks to TigerGraph and burns free-tier credits, so it is opt-in.

## Looking at the graph

```bash
paysentry show                          # what the temporal graph contains
paysentry show --compare-cycles         # real layering rings beside decoys
paysentry show --list                   # every planted ring
paysentry show --ring CIRC-0000         # one ring in detail
paysentry show --account ACC-0000145    # one account's timeline
```

A payments graph is not worth drawing whole — 5,000 accounts of mostly ordinary
traffic is a hairball. What *is* worth looking at is one planted ring next to its
decoy, which is where the central claim lives and fits in ten rows:

```
CIRC-0000   (circular_layering, 5 hops)   [shown from the start of the money's path]
  ACC-0000145 → ACC-0000034  t+   0.00h   56,068.37
  ACC-0000034 → ACC-0000309  t+  14.54h   51,459.54  ↑
  ACC-0000309 → ACC-0000399  t+  38.41h   47,052.55  ↑
  ACC-0000399 → ACC-0000305  t+  65.12h   44,362.52  ↑
  ACC-0000305 → ACC-0000145  t+  69.36h   41,442.76  ↑
                cyclic descents 1 · TIME-RESPECTING — money could flow round this loop

DECOY-0000   (decoy_cycle, 5 hops)
  ACC-0000053 → ACC-0000233  t+   5.60h   25,268.92
  ACC-0000233 → ACC-0000480  t+   0.00h   23,105.77  ↓ IMPOSSIBLE — earlier than the hop before
  ACC-0000480 → ACC-0000329  t+  56.42h   21,374.74  ↑
  ACC-0000329 → ACC-0000090  t+  44.62h   19,415.56  ↓ IMPOSSIBLE — earlier than the hop before
  ACC-0000090 → ACC-0000053  t+  30.06h   26,212.88  ↓ IMPOSSIBLE — earlier than the hop before
                cyclic descents 4 · NOT time-respecting — no starting point works
```

Both are closed loops of the same length between real accounts. A static cycle
query cannot tell them apart; a time-respecting search fires only on the first.
The amounts decaying ~9% per hop in the real ring are the fee skim.

Views read through Raphtory's own accessors (`window`, `edges.explode`), so what
you see is what the detectors saw.

**Interactive visuals.** Raphtory ships `to_pyvis()` and `to_networkx()`, but
both need dependencies this project does not pin (`pip install pyvis networkx`).
They are worth it for exploring a *small* subgraph — a single ring — and useless
on the whole graph.

### Serving the graph

Raphtory is normally **embedded**: the graph lives in the calling process and
dies with it. There is no daemon — which is why a server started inside a short
script is unreachable a moment later.

`GraphServer` is the other mode. It loads saved graphs from a working directory
and serves them over GraphQL with a browsable UI, for as long as the process
lives:

```bash
paysentry serve --profile small          # blocks; Ctrl-C to stop
# -> Raphtory GraphQL server on http://localhost:1736
```

```graphql
{ graph(path: "small") { nodes { list { name } } } }
{ graph(path: "small") { edges { list { src { name } dst { name } } } } }
```

Worth being precise about what this is: a query service over saved graphs, with
a cache and a TTL. It is **not** a distributed transactional store — no sharding,
no ACID guarantees across writes, no authorization-path latency budget, and the
server dies with the process that started it. The distinction from a system of
record is one of degree, not kind — but the degree is the whole argument, and
[Lesson 10](../course/10-tigergraph-vs-raphtory-evaluation.md#a-correction-raphtory-does-have-a-server)
draws the line.

## Verifying the engines

```bash
.venv/bin/paysentry check                      # both engines
.venv/bin/paysentry check --engine raphtory    # local only, no credentials needed
.venv/bin/paysentry check --engine savanna     # network + credentials
```

For **TigerGraph** this is a staged connection diagnosis — credentials present,
DNS, port, credential *type* (JWT vs database secret — they take different
pyTigerGraph arguments), authentication, query round-trip — stopping at the first
break and naming the cause.

For **Raphtory** there is no connection to test, so `check` verifies something
more useful, in four escalating steps:

1. the pinned version is installed and every API the detectors call still exists
   (Raphtory's API has moved between releases — see [`docs/raphtory-notes.md`](docs/raphtory-notes.md));
2. `analyze` pins rayon to one thread, without which Raphtory's parallel
   algorithms give different answers each run ([ADR 002](docs/decisions/002-nondeterministic-algorithms.md));
3. the built graph reconciles with the event log it came from;
4. **the five temporal views are exact** — `window`, `at`, `snapshot_at`,
   `before`, `after` are each recomputed independently from the raw timestamps
   and compared. Every temporal result in this project rests on those, and
   "the library is probably correct" is not a verification;
5. **the decoy control still holds** — time-respecting cycle search is re-run end
   to end and must trace every planted layering ring and **zero** decoys. If that
   stops being true, every conclusion in `reports/` is void, and this says so in
   one command.

Verified at all three profiles, up to ~1M temporal edges:

```
[PASS] graph reconciles with the log: 982,622 temporal edges vs 982,622 transfers
[PASS] temporal view: snapshot_at(mid): expected 491,312, got 491,312
[PASS] decoy control holds: traced 88/88 real layering rings and 0/87 decoys
```

## Configuration

- **`config.yaml`** — everything tunable: dataset profiles, background-traffic
  shape, typology parameters, detection thresholds, temporal windows, scoring
  weights, latency budgets. Committed, and validated on load: a payment mix that
  doesn't sum to 1, fewer than 24 hourly weights, inverted decision bands, or
  scoring weights that can never reach the block threshold all fail loudly rather
  than quietly skewing an evaluation.
- **`.env`** — Savanna credentials only. Gitignored; `.env.example` is committed.

No credential ever goes in `config.yaml`, and no tunable ever hides in `.env`.

## Headline result

The measurement the whole capstone exists to make (medium profile, 5,000 accounts,
195k transactions). Identical window, hop bound and seeds — the **only** difference
is whether hop timestamps must increase:

| Cycle search | Real layering rings | Decoy rings *(must be 0)* | Accounts flagged |
|---|---:|---:|---:|
| time-respecting | **22/22** | **0/22** | 89 |
| static *(control)* | 22/22 | **22/22** | 1,450 |

Same recall. Ignoring event order also picks up **every decoy** — loops that are
structurally real but temporally impossible, where money could not have flowed in
any order — and flags 16x more accounts. That gap is what "time as a first-class
dimension" is worth, measured rather than argued.

**Read [`reports/CONCLUSION.md`](reports/CONCLUSION.md) for the findings.** In
short: the design's prediction held on four of five typologies; three typologies
are effectively invisible to the hot path (0%, 0%, 13% recall) and well caught by
Raphtory (100%, 96%, 90%), taking mean recall from **41% to 96%**.

**The closed loop works too:** 97 transactions across 23 accounts flip from
`allow` to `review` purely because Raphtory's temporal analytics wrote risk scores
that the hot path then read — with no analytics running on the authorization path.

## Both engines agree

Screening the same transactions through TigerGraph and SQLite gives **20/20
identical decisions and scores**, because the signal formulas live once in
`store/screening.py` and each store only *measures* the graph its own way. On
latency, TigerGraph executes all five checks in **6.7 ms** — 97% of the 229 ms
call is laptop-to-cloud network, not the database.

## Prerequisites

- **Python 3.13** (verified on 3.13.2) — Raphtory runs in-process from a local venv; no Docker, no Java, no server.
- **A free TigerGraph Savanna account** — needed only for Phases 2, 3, 6, 7. Phases 1 and 4–5 run fully offline against the SQLite `LocalStore`.
  Getting its connection details: [`docs/tigergraph-setup.md`](docs/tigergraph-setup.md),
  then `paysentry check` to verify them.

## Verified tooling (2026-09-05)

| Package | Version | Notes |
|---|---|---|
| `raphtory` | 0.17.0 | Layers, `rolling`/`expanding`, Arrow `load_edges`, `temporally_reachable_nodes`, `louvain`, temporal 3-node motifs — all confirmed present |
| `pyTigerGraph` | 2.0.4 | Current release |

Both APIs have drifted between releases before. Versions are pinned, and the Raphtory API surface this project depends on was verified by introspection against the installed package — see [`docs/raphtory-notes.md`](docs/raphtory-notes.md). Any signature that moves gets recorded there.
