# Lesson 7 — Distributed Architecture, and GSQL: TigerGraph Specifics

Lesson 6 ended by naming TigerGraph as doing OLTP and OLAP "natively," from one system, via "distributed, compiled execution" — and deferred explaining what that actually means. This lesson unpacks both halves: why a graph gets distributed across machines at all, what that costs, and how GSQL's execution model is built specifically to make one query language serve both workload shapes.

## Why distribute a graph at all

Everything through Lesson 6 assumed a graph living in one machine's memory. That has a ceiling — RAM, and the throughput of however many cores one machine has. TigerGraph is built as a **native MPP (massively parallel processing) system**: the graph is partitioned across a cluster of machines, each holding a shard of vertices and edges, with queries distributed and executed in parallel across the cluster rather than serialized through a single process.

## The hard part: partitioning a graph

Relational sharding usually partitions rows by a key — customer ID, region — and cross-shard joins are the exception, something the application mostly avoids by design. A graph doesn't get that luxury: relationships *are* the data, and a traversal routinely needs to hop from a vertex on one machine to a vertex on another.

This matters because of what Lesson 1 quietly assumed. Index-free adjacency's whole promise — a relationship traversal is a pointer dereference, not a search — assumed the pointer and the node it points to live in the same address space. Distribute the graph, and a hop that crosses a partition boundary stops being a pointer dereference and becomes a **network round-trip**: roughly a hundred nanoseconds for a memory access versus tens to hundreds of *microseconds* for a network call within a datacenter — a three-to-four-orders-of-magnitude gap. Lesson 5's cost model (proportional to degree, not graph size) still holds *within* a partition; crossing partitions adds a cost Lesson 5 never had to account for.

So the central design problem in a distributed graph engine isn't "how do we split the data" — it's **how do we split the data so that most traversals stay on one machine**. Minimizing cross-partition edges (the "edge cut") while keeping every partition roughly balanced in size is itself a graph problem — a variant of the community-detection and clustering algorithms Lesson 6 introduced, applied to the graph's own structure before a single query ever runs. TigerGraph's partitioning strategy aims to keep densely-connected neighborhoods together on the same machine, so that a typical bounded traversal (Lesson 5's anchored, degree-proportional shape) stays local; cross-partition hops still happen, but a good partitioning keeps them the exception rather than the norm.

## Replication is a separate concern from partitioning

Worth naming so the two don't blur together: **partitioning** splits the graph across machines for scale — each machine holds a different slice. **Replication** copies a partition onto more than one machine for fault tolerance and read throughput — several machines hold the *same* slice. TigerGraph does both, but they answer different questions: partitioning is "how do we fit a graph bigger than one machine," replication is "how do we keep serving queries if one machine goes down."

## GSQL's execution model: bulk, parallel, per-hop

This is the piece that actually resolves Lesson 6's open question. Cypher plans and executes **per query**, one pattern-match at a time, row by row through an interpreter (Lesson 5). GSQL takes a structurally different approach:

- A GSQL query is written, then explicitly **installed** — `INSTALL QUERY` compiles it ahead of time into native code, distributed out to run across the partitioned cluster. Every later `RUN QUERY` invocation runs that compiled code directly, instead of re-planning. This is the compiled-vs-interpreted distinction Lesson 5 flagged, made concrete.
- The core primitive isn't "match this pattern" — it's a **bulk, parallel traversal over vertex sets**. A query starts with a seed set of vertices, and at each step expands the *entire current frontier*, across *every partition*, in parallel, along one edge type — optionally accumulating data onto vertices as it goes, using **accumulators** (`SumAccum`, `MaxAccum`, `SetAccum`, `ListAccum`, …) that merge correctly across parallel workers. Then the whole frontier moves to the next hop, together.

That last point is the payoff. The same execution primitive — expand the whole current frontier in parallel, accumulate, repeat — doesn't care whether the frontier is one vertex or the entire graph. An OLTP-shaped query just happens to keep its frontier small (start from one known device, expand two hops, done). An OLAP algorithm like PageRank is written as the *identical* loop-and-accumulate shape, just run to convergence over a frontier that starts as, and stays, the whole graph. There's no separate projection step the way Neo4j's GDS library requires (Lesson 6) — one query language, one engine, one execution primitive, because the primitive itself doesn't distinguish "a few nodes" from "all of them."

## GSQL by example: the shared-device query, translated

Lab 4's Cypher version:

```cypher
MATCH (fraud:Transaction {isFraud: true})-[:VIA_DEVICE]->(d:Device)<-[:VIA_DEVICE]-(other:Transaction)
WHERE other <> fraud
RETURN other.id, d.id AS sharedDevice;
```

The same idea in GSQL, in shape — treat this as illustrating the structure, not a copy-paste-ready script; check exact syntax against your installed TigerGraph version, same as you'd sanity-check any query language against current docs:

```gsql
CREATE QUERY sharedDeviceAsFraud(VERTEX<Transaction> seed) FOR GRAPH FinancialNetwork SYNTAX v2 {
  SetAccum<VERTEX<Transaction>> @@others;

  Seed  = {seed};
  Dev   = SELECT d
          FROM Seed:s -(VIA_DEVICE:e)-> Device:d;
  Other = SELECT t
          FROM Dev:d -(reverse_VIA_DEVICE:e)- Transaction:t
          WHERE t != seed
          ACCUM @@others += t;

  PRINT @@others;
}
```

A few things worth noticing even at this sketch level:

- **Schema-first, concretely.** Every vertex type (`Transaction`, `Device`), edge type (`VIA_DEVICE`), and their attributes had to be declared with `CREATE VERTEX` / `CREATE DIRECTED EDGE` before any data could be loaded — Lesson 2's "TigerGraph is schema-first" table entry, now the reason this query can even reference `Transaction` and `VIA_DEVICE` by name.
- **Queries are named, parameterized, reusable objects** — closer to a stored procedure or an API endpoint (`sharedDeviceAsFraud(seed)`, called with a specific transaction) than an ad hoc script. That's a direct consequence of compile-then-run: you compile a *query definition*, not a one-off statement.
- **Accumulators replace implicit row semantics.** Cypher's `RETURN other.id` just projects matched rows. GSQL explicitly collects results into an accumulator (`@@others`) because the underlying execution model is "many parallel workers processing their own slice of the frontier," and accumulators are the mechanism that merges their partial results back together correctly.

## Check your understanding

1. Why does a cross-partition hop cost so much more than an in-memory pointer hop, and what does that imply about how a distributed graph engine should decide which vertices to co-locate on the same machine?
2. GSQL requires `INSTALL QUERY` (compilation) before a query can run, while Cypher plans a query fresh on each execution. What's the tradeoff being made — what do you gain, and what do you give up, by compiling ahead of time?

## Hands-on lab

[`../labs/07-tigergraph-gsql/README.md`](../labs/07-tigergraph-gsql/README.md) — stand up TigerGraph Community Edition, port the financial network's schema and data from Cypher to GSQL by hand (feeling Lesson 2's schema-first distinction directly, not just reading about it), then translate and run two of the OLTP queries from Labs 4–5.
