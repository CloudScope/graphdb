# Lesson 8 — Temporal Graphs: Time as a First-Class Dimension, and Raphtory Specifics

Every query in this course so far, in Cypher and in GSQL, asked about the graph as it exists **right now** — or, more precisely, as it exists in whatever the database currently holds. Lesson 4's `Transaction` nodes have always had a `timestamp` property, but nothing about Cypher or GSQL understood that property as time — it was just a string or an integer you could filter on, exactly like `amount` or `role`. This lesson introduces the alternative: an engine that treats time itself as a structural part of the graph, not a property you happen to have remembered to store.

## What "temporal graph" actually means

A **static property graph** — everything through Lesson 7 — is one snapshot: nodes and edges exist, full stop, as of whenever you're looking. A **temporal graph** attaches time to the structure itself: an edge doesn't just exist, it exists *from t1 to t2*, or occurred *at t*; a property doesn't just have a value, it has a **history** of values across time. Two ways this shows up in practice, both worth having names for:

- **Event-based (interaction) graphs** — a stream of timestamped edge events: "at time T, account A sent to account B." This is exactly what a transaction log already is, and it's the natural ingestion model for a temporal engine — you don't declare a fixed schema of what the graph looks like, you hand it the event stream and it builds the temporal structure from that.
- **Snapshot-based graphs** — a sequence of full static graphs, one per time step. Conceptually simple, but naively storing every snapshot in full is wasteful; a real temporal engine reconstructs a snapshot on demand from the event history rather than materializing every one of them up front.

## Why a `timestamp` property isn't the same thing

You could always write `WHERE t.timestamp > "2026-08-03" AND t.timestamp < "2026-08-05"` in Cypher. The problem isn't expressiveness, it's cost and what the engine is able to reason about. Lesson 5 established that a property filter costs proportional to how many rows/nodes have to be checked, unless there's an index built for exactly that access pattern — and a plain property index doesn't know that `timestamp` values have an *order* that lets you skip straight to a range the way a time-partitioned engine can. More fundamentally, neither Cypher nor GSQL has any native concept of "the graph as it existed at time T" or "only follow edges in increasing time order" — those have to be hand-rolled as property filters applied after the fact, on every query, by you.

A temporal-native engine treats time as indexed structure: windowing to `[start, end)` or reconstructing "as of time T" are first-class operations the engine is built to make cheap, the same way Lesson 5's property index made an anchor lookup cheap instead of a full scan.

## The idea unique to temporal graphs: time-respecting paths

This is the single most important concept that a static graph model genuinely cannot express. A **time-respecting path** is a path where each edge occurs strictly *after* the one before it — not just structurally connected, but causally ordered. Money can only flow A → B → C if the A→B transfer happened before the B→C transfer; a rumor can only spread along edges in the order people actually told each other; a disease can only transmit forward in time. A plain `shortestPath()` over a static graph (Lesson 3) is blind to this entirely — it only cares whether edges exist, never whether they happened in an order that makes the path physically possible.

This lands directly on our running example. Lab 4's answer to exercise 5 found a closed transaction loop — `A → B → C → D → E → F → A` — and called a closed loop a "classic money-laundering red flag," but that conclusion quietly depended on eyeballing the timestamps to confirm the loop was actually traversable in order:

| Transaction | Timestamp |
|---|---|
| `txn-1` (A→B) | 2026-08-01 09:00 |
| `txn-2` (B→C) | 2026-08-02 11:30 |
| `txn-3` (C→D) | 2026-08-03 14:15 |
| `txn-4` (D→E) | 2026-08-04 08:45 |
| `txn-5` (E→F) | 2026-08-05 02:10 |
| `txn-6` (F→A) | 2026-08-05 02:40 |

Every timestamp is strictly later than the one before it — the loop isn't just a structural cycle, it's a valid time-respecting path: the money genuinely *could* have moved all the way around, in that order. A temporal graph engine makes "is this path time-respecting" a queryable property instead of something a human checks by eye against a table — which matters enormously the moment the graph is too large to eyeball, which is the normal case, not the exception.

## Raphtory specifics

Raphtory (Pometry) is the second half of this course's target comparison, and Lesson 6 already placed it precisely: **analytics-first**, an embeddable, in-memory, Rust-cored engine with a Python-first API, built around ingesting a graph and running analysis over it — not a persistent store you'd point a production application's live writes at.

- **Ingestion is event-based**, matching the interaction-graph model above: you build a `Graph` and add timestamped edges (and nodes) directly, closer to replaying a transaction log than defining a schema and loading rows.
- **Windowing and "as of" views are native operations** — restrict a query to a time range, or reconstruct the graph exactly as it looked at a specific moment, without hand-rolling a property filter every time.
- **Whole-graph algorithms become temporal.** Lesson 6's OLAP vocabulary — degree, centrality, connected components — doesn't disappear here, it gains a time dimension: instead of running PageRank once against a static snapshot, you can run it per window and watch how structural importance shifts over time. A static engine can only approximate this by manually re-running N separate queries against N separately-filtered snapshots and stitching the results together yourself; a temporal engine treats "how did this metric evolve" as one operation.
- **The Python-first API is a deliberate audience choice**, not an accident — it targets the person doing exploratory analysis in a notebook, not the engineer wiring up a low-latency production query path. That's the same OLAP-first, not-a-system-of-record positioning from Lesson 6, now visible in the tooling itself.

## What happens to the Transaction node

Lesson 4 promoted `Transaction` to a node specifically to give a binary-relationship model (Lesson 4's "relationships are strictly binary" constraint) a place to hang an n-ary event's extra participants and attributes. A temporal, event-based engine doesn't have that constraint in the same way: the event already has a first-class identity — it's the `(sender, receiver, time)` triple itself, carrying `amount`, `isFraud`, and a device reference as edge properties, no promotion required. Raphtory's natural representation of this domain collapses back to a simple timestamped edge between two `Account` nodes. That's not a step backward from Lesson 4's modeling lesson — it's the same underlying tradeoff (node vs. property vs. relationship) landing differently because the engine's primitive changed from "a static graph you traverse" to "a stream of timed events you window and replay."

## The picture so far

| | Neo4j | TigerGraph | Raphtory |
|---|---|---|---|
| Time | A property you filter, nothing more | A property you filter, nothing more | First-class: windows, "as of," time-respecting paths |
| Primary workload | OLTP core + separate OLAP (GDS) | OLTP + OLAP, natively, one engine | OLAP-only, temporal-native |
| Where it runs | Server you connect to | Distributed cluster | Embedded in your own process |

That last row is worth sitting with going into Lesson 10: it's not just "Raphtory adds time." It's that Raphtory was never trying to be the other two things TigerGraph and Neo4j both are — a live, queryable system of record. The course's opening framing — "not apples-to-apples" — has now been built up one architectural difference at a time, not asserted.

## Check your understanding

1. Why can't a plain `WHERE timestamp > X AND timestamp < Y` filter in Cypher give you the same guarantee as a native "time-respecting path" query? What specifically does the filter fail to check that the temporal query wouldn't?
2. Using the financial network's timestamps, is the path `D → E → F` (from Lab 4's shortest-path exercise) time-respecting? What about a hypothetical query for the shortest path from `F` back to `D` — would that path, if one existed, need different reasoning about time than the `D → E → F` direction did?

## Hands-on lab

[`../labs/08-raphtory-temporal/README.md`](../labs/08-raphtory-temporal/README.md) — rebuild the financial network as a Raphtory temporal graph in Python, window it to a specific date range, reconstruct an "as of" snapshot, and verify the Lab 4 closed loop is genuinely time-respecting instead of taking the lesson's word for it.
