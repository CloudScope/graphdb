# Lesson 10 — TigerGraph vs. Raphtory: The Evaluation

The course's introduction made a claim on day one, before either product had been discussed in any depth: TigerGraph and Raphtory are **not apples-to-apples**. Nine lessons have been quietly building the evidence for that claim, one architectural difference at a time, rather than asking you to take it on faith. This lesson collects that evidence into an explicit comparison, and — because "which one is better" is the wrong question for two products built to do different jobs — into a decision framework for figuring out which one (or both, or neither) actually fits a given need.

## What each product actually is, restated precisely

- **TigerGraph** — a distributed, disk-backed, transactional-*and*-analytical graph **database**, queried in GSQL, built to be a persistent system of record you run both live application queries and deep analytics against, at scale.
- **Raphtory** — an embeddable, in-memory, Rust-based **temporal graph analytics engine**, driven from a Python-first API, built around ingesting a graph (often from somewhere else's durable storage) and running time-aware analysis over it. It is *usually* embedded in your process rather than run as a service — though it does ship a server, which the correction below covers, because "it isn't a server" is too strong and someone will call you on it.

Everything below is that one-sentence distinction, unpacked axis by axis.

## The full comparison

| Axis | TigerGraph | Raphtory | Built up in |
|---|---|---|---|
| What it is | A database — persistent system of record | An analytics engine, usually embedded in your process; optionally served over GraphQL | Course intro, Lesson 9 |
| Schema | Schema-first: vertex/edge types declared before any data loads | Inferred from whatever you ingest | Lesson 2, Lesson 7 |
| Query / programming model | GSQL — compiled, procedural, bulk-parallel over vertex sets | Python API — imperative, notebook-native | Lesson 3, Lesson 7 |
| Execution model | Compiled ahead of time (`INSTALL QUERY`), distributed across a cluster | Runs in-process, single address space (parallelizable via more cores/processes) | Lesson 5, Lesson 7 |
| Workload shape | OLTP *and* OLAP, natively, one engine | OLAP — writes exist (including remote ones) but there is no transactional write path to build an application on | Lesson 6 |
| Time | A property you filter, same as any other engine here | First-class: windows, "as of" snapshots, time-respecting paths | Lesson 8 |
| Scale model | Horizontal — add machines, repartition | Vertical, per-process — more memory/cores, or parallelize the job yourself | Lesson 7, Lesson 9 |
| Consistency & durability | Distributed transactional guarantees; owns its own backup story | Graphs can be saved and reloaded, but there are no transactional guarantees and durability really lives upstream in whatever fed it | Lesson 9 |
| Who it's built for | An application (and its users) making live queries | A person doing exploratory or scheduled analysis | Lesson 6, Lesson 8, Lesson 9 |

Reading down that table, notice that almost nothing on it is actually a **strength/weakness** pair — most rows are a consequence of the same root choice (system of record vs. analytics engine) showing up in a different place. That's the concrete meaning of "not apples-to-apples": the differences aren't independent features you could mix and match, they're one architectural decision, refracted.

## A correction: Raphtory does have a server

Everything above is written as though Raphtory is purely a library you import.
That was the honest reading when this course was drafted, and it is no longer
quite right. Raphtory 0.17 ships `raphtory.graphql`, and it is a real server:

```python
from raphtory.graphql import GraphServer, RaphtoryClient

GraphServer(work_dir="./graphs").run(port=1736)     # blocks, serves a browsable UI
client = RaphtoryClient("http://localhost:1736")
client.query('{ graph(path: "payments") { nodes { list { name } } } }')
```

Verified running: it binds a port, loads saved graphs from a working directory,
keeps a graph cache with a TTL, answers GraphQL, and exposes `RemoteGraph` /
`RemoteNode` / `RemoteUpdate` for **mutating graphs over the wire**. Alongside
`save_to_file`, `to_parquet` and friends, that is more persistence and more of a
service than "an analytics library" suggests.

**So does the course's central claim survive?** Yes — but the claim has to be
stated as a difference of *degree*, not of kind, and the degree is the whole
argument:

| | Raphtory's `GraphServer` | A system of record |
|---|---|---|
| Distribution | single process | sharded across a cluster |
| Write guarantees | updates applied to a graph | transactional, with isolation and durability |
| Failure story | server dies with its process | replication, failover, backup/restore |
| Latency contract | none stated | an authorization-path budget you design against |

The sharpest way to see it: **a Raphtory server lives inside the Python process
that started it and dies with it.** Stop the script and the endpoint is gone —
there is no daemon, nothing registered with the OS, nothing that survives the
interpreter exiting. A database is a thing you operate; this is a thing you run.

That is still a genuine architectural difference, and it is still the reason you
would not point a payments application's writes at it. But "Raphtory is not a
server" is a claim someone can disprove in one command, and an argument you can
be embarrassed out of is worth replacing with one you cannot.

## The right question isn't "which is better" — it's "what am I actually building"

A decision framework, in the same "write down the actual questions" spirit Lesson 4 taught for data modeling:

1. **Does something need to query this graph live, from an application, with continuous writes?** If yes, you need a system of record — TigerGraph if the scale and OLTP-plus-OLAP-in-one-engine argument (Lesson 6, Lesson 7) actually justifies a distributed cluster; plain Neo4j if it doesn't yet (worth saying honestly: most projects never reach the scale where TigerGraph's distributed architecture pays for itself, and a single well-indexed Neo4j instance, Lessons 3–5, is the better call more often than the marketing for any distributed system will admit). Raphtory is not a candidate here — not because it cannot accept writes (it can — see the correction above) but because it offers none of the guarantees an application's write path needs (Lesson 9).
2. **Is the actual question about the graph's structure or evolution as a whole** — centrality, communities, time-respecting paths, how something changed over months — **rather than about answering individual live queries?** If yes, and especially if time is a real dimension of the question (Lesson 8), Raphtory is built specifically for this in a way neither Neo4j's bolted-on GDS projection nor TigerGraph's general-purpose OLAP support was.
3. **Do you need both — live serving *and* deep temporal analytics?** This is the case where "not apples-to-apples" stops being a caveat and becomes the answer: these two products aren't mutually exclusive choices for the same slot, because they were never competing for the same slot. The realistic architecture is TigerGraph (or Neo4j) as the system of record, with Raphtory pulling a snapshot or stream from it periodically to run the analysis neither of those engines does natively — Lesson 9's "workload isolation" pattern, extended across two different tools instead of one projection inside one tool.
4. **Is the underlying shape of the problem even deep, relationship-heavy traversal in the first place?** Lesson 1's original question, still the first one worth asking: if most real queries are one or two hops, none of this course's later machinery — partitioning, temporal windows, compiled execution — is buying you anything a relational database with a couple of join tables wouldn't already give you more simply.

## Applied to the financial network, one last time

The fraud-detection domain that ran through every lab in this course is a genuinely good test of the framework above, because it actually needs both halves:

- **Live serving**: an application needs to look up "does this new transaction share a device with a known-fraud transaction" (Lab 4/5's exact query) in real time, as transactions arrive — a system-of-record job. TigerGraph, if the transaction volume and OLTP-plus-OLAP argument justify a distributed cluster; Neo4j otherwise.
- **Deep temporal analysis**: "which clusters of accounts, over the last quarter, show a time-respecting cycle of transfers through shared devices" (Lesson 8's exact question) is not a query you'd want running against your live OLTP path (Lesson 9) — it's exactly the periodic, whole-graph, time-aware analysis Raphtory is built for, run against a snapshot or stream pulled from the system of record, with any accounts it flags fed back as a signal the live system can act on.

Neither product replaces the other here. The realistic answer to "TigerGraph vs. Raphtory" for this exact domain is **both, doing different jobs** — which is the course's opening claim, now demonstrated against the one running example every lesson used to make it concrete.

## Integrating both in one system

"Both, doing different jobs" isn't just a conclusion — it's a specific, buildable architecture. Neither product merges into the other; there's no single fused engine. Integration means wiring two separate components into the same overall system, each doing the job it was actually built for.

**The mechanics.** Most commonly, data flows **TigerGraph → Raphtory**: TigerGraph stays the system of record, and periodically (batch) or continuously (streaming), a slice of the graph — via a GSQL query's output, TigerGraph's REST++ API, or a scheduled export — gets loaded into Raphtory as a timestamped edge list, the event-based ingestion model from Lesson 8. Raphtory runs the workload TigerGraph either can't do natively (time-respecting paths, "as of" reconstruction) or shouldn't run against its live OLTP path (Lesson 9's workload-isolation argument). **Results flow back, not raw data** — Raphtory's output (risk scores, flagged clusters, community assignments) gets written into TigerGraph as vertex/edge attributes via `INSERT`/`UPSERT` or REST++, so the live application's ordinary OLTP queries can see and act on them, with no analytics logic ever running on the live path itself.

A cleaner variant exists when there's already an event stream upstream — Kafka, say: both TigerGraph and Raphtory consume the same source independently, rather than Raphtory depending on a TigerGraph export. This matches Lesson 9's point that the *real* system of record might be the transaction log itself, with both engines as downstream consumers rather than one feeding the other directly. Note there's no first-party "TigerGraph↔Raphtory connector" to reach for here — this is a custom integration you build, the same caveat this course has given every time it touched either product's exact tooling.

**When it's actually the right call.** Whenever a domain genuinely has both needs at once, not just because both are graph tools:

- **Fraud/AML** (this course's running example) — live transaction screening + periodic time-respecting-cycle / device-sharing-ring detection feeding risk scores back.
- **Recommendations with temporal dynamics** — live "bought X also bought Y" queries + trend/decay analysis over time to retrain scoring.
- **Network/infrastructure monitoring** — live topology queries + temporal anomaly detection (flapping links, traffic pattern shifts) run periodically.
- **Social graphs** — live friend-of-friend lookups + evolving community/influence analysis for periodic reporting.

**When not to bother.** No live-serving need at all → use Raphtory alone. No genuine "how did this change over time" question → Neo4j's GDS or TigerGraph's own OLAP already covers static whole-graph analytics, and adding Raphtory is complexity without payoff. This is Lesson 4's "model the questions, not the data" habit, one level up — applied to system architecture instead of the data model: combining two tools needs the same justification as promoting a property to a node, which is to say, a question that actually demands it.

## Check your understanding — the real one

1. Raphtory ships a GraphQL server that persists graphs and accepts remote writes. Does that make it a system of record? Write down where you would draw the line, and what you would need to see before pointing an application's writes at something. (This is the question the correction section above exists to make you answer for yourself.)
2. Without re-reading the table above, write your own paragraph explaining why TigerGraph and Raphtory aren't apples-to-apples, using this course's vocabulary (system of record, OLTP/OLAP, schema strictness, time as a first-class dimension). If you can't do it without the table, that's a sign to reread Lessons 6–9 before calling this course finished.
3. Think about the real project that prompted this course, back in Lesson 1's first check-your-understanding question. Walk it through the four-question framework above. Which axis actually decides the answer for your case — and did anything in this course change your answer from what you would have guessed before Lesson 1?

## The course, looking back

Ten lessons, one running example. The point was never to memorize GSQL syntax or a Raphtory API surface — both drift, and this course said so honestly every time it used them. The point was to end up able to look at a real system's requirements and know which axis from the table above actually matters for it, instead of reaching for whichever graph database is loudest in the room.
