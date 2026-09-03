# Lesson 5 — Indexing, Traversal Performance, and How Graph Engines Actually Execute Queries

Lesson 1 promised that a pointer hop costs "roughly the same regardless of dataset size." That's true, but it's a simplification, and Lab 4 ended on a live question it couldn't fully answer yet: is a super-node like `Device` a modeling mistake, or the point? The answer depended on *how* you traverse it — this lesson formalizes exactly why, by looking at what a graph engine actually does when it runs a query.

## Index-free adjacency has a blind spot: getting started

Index-free adjacency describes the cost of **traversal** — once you're standing on a node, following one of its relationships is a pointer dereference, not a search. It says nothing about how you got there in the first place.

Every `MATCH` needs at least one **anchor** — a starting node (or set of nodes) the pattern expands out from. Finding that anchor is not a pointer hop. Without an index, `MATCH (a:Account {id: "ACC-1001"})` has to visit every node labeled `Account` and check its `id` property — a **label scan**, cost proportional to how many `Account` nodes exist. That's the same cost class as an unindexed table scan in a relational database. Index-free adjacency buys you cheap traversal *after* the anchor; it was never a claim that graphs need no indexes at all.

This is why Lesson 3's `CREATE INDEX person_name IF NOT EXISTS FOR (p:Person) ON (p.name)` matters architecturally, not just as syntax: it turns the anchor lookup from an `O(n)` scan into an `O(log n)` (or near-`O(1)`) index seek — the one place a graph query still pays a classic database-index cost, doing exactly the job a relational index does.

## What a query planner actually does

Cypher has `EXPLAIN` (show the plan) and `PROFILE` (run the query and annotate the plan with real row counts and **db hits**, the engine's unit of "how much work did this step do"). A query like:

```cypher
PROFILE
MATCH (a:Account {id: "ACC-1001"})-[:SENT]->(t:Transaction)
RETURN t
```

compiles to a small operator tree, roughly:

```
NodeIndexSeek   Account(id) = "ACC-1001"     → 1 row  (the anchor)
  Expand(All)   -[:SENT]->                    → one row per outgoing SENT edge
    ProduceResults
```

Read bottom-up in cost terms: the seek finds the anchor in work proportional to the index, not the graph. The expand step then costs proportional to **how many `SENT` relationships that one account has** — its out-degree. Neither step touches the rest of the graph. That's the mechanism behind "pointer hop, not search," made concrete enough to reason about.

## The real cost model: proportional to degree, not to graph size

The precise version of Lesson 1's claim: each hop costs proportional to the **degree** of the node(s) currently being expanded, not to the size of the whole graph. For an ordinary node with a handful of relationships, that's a small constant — indistinguishable from `O(1)` in practice. At a super-node (Lesson 4), the "constant" is just large: continuing past it means touching every one of its relationships.

This is the formal version of the answer Lab 4 deferred. Anchoring on a specific, known `Transaction {isFraud: true}` and expanding outward through `VIA_DEVICE` stays cheap even though `Device` is a super-node, because you only ever touch *that one* fraud transaction's edges plus *that one* device's edges — a bounded, specific expansion. A query that instead started by scanning **all** `Device` nodes and expanding to find shared transactions would, at the shared device, have to enumerate its entire relationship set as an intermediate waypoint — most of it irrelevant. Same super-node, two different traversal shapes, two very different costs. Degree is a modeling concern *and* a query-shape concern.

## Indexes in a graph engine

| Kind | What it's for |
|---|---|
| Label index (implicit) | Backs `NodeByLabelScan` — used when no better index exists |
| Property index (range/B-tree) | Equality and range lookups on a property — `CREATE INDEX ... FOR (n:Label) ON (n.prop)` |
| Composite index | Same, across multiple properties matched together |
| Uniqueness constraint | Enforces uniqueness *and* doubles as an index |
| Text / point / vector indexes | Full-text search, spatial queries, and (increasingly relevant) nearest-neighbor search over embeddings |

The rule of thumb from Lesson 3 still holds: index the properties you anchor on, not every property you'll ever filter by. An anchor property gets touched on every query that starts from that label; a property you only filter *after* an expand is cheap to check inline and rarely needs its own index.

## Traversal order matters: start from the selective side

When a pattern has more than one plausible starting point, anchor on whichever side matches the fewest nodes, then expand toward the rest. `MATCH (fraud:Transaction {isFraud: true})-[:VIA_DEVICE]->(d)<-[:VIA_DEVICE]-(other)` anchors on `isFraud: true` — rare, a handful of matches — rather than on `Device`, which is comparatively high-degree. The planner will often make this choice for you based on index statistics, but when you hand-write a pattern, put the selective, indexed side first; it's the difference between the bounded expansion and the full-enumeration case above.

## Variable-length paths: where cost can silently explode

`*1..N` traversals compound: each additional hop multiplies the frontier by roughly the average out-degree of the nodes reached so far. For sparse graphs and small `N` this stays small. If a variable-length pattern's path happens to route through a high-degree node partway through, the frontier at that hop balloons — the same mechanism as the section above, just triggered mid-traversal instead of at the anchor. This is why Lesson 4 called degree "a modeling concern, not just an implementation detail," and why an unconstrained `shortestPath()` on a graph with real super-nodes can be surprisingly slow, despite "graphs are fast for path queries" being the usual folk wisdom. Bound your variable-length ranges (`*1..3`, not bare `*`) whenever the domain allows it.

## Relational vs. graph, at execution time

| | Relational | Graph |
|---|---|---|
| Find the starting row(s) | Index seek on a table (B-tree) | Index seek or label scan to find the anchor node(s) |
| Follow a relationship | Join — re-search the joined table, usually via an index | Traversal — follow a stored pointer to the neighbor |
| Cost of one more hop | Another indexed search; cost tied to table size | Proportional to the current node's degree, not graph size |
| Where it gets expensive | Deep join chains against large tables | Traversal *through* a high-degree "super-node" |

## Preview: interpreted vs. compiled execution

Everything above describes how Neo4j's Cypher planner works: it builds an operator plan per query and interprets it (the plan itself is cached, but each row still flows through the interpreter). TigerGraph's GSQL takes a different path — queries are compiled ahead of time into native code that runs directly against the stored graph, which is a large part of why TigerGraph markets itself on raw traversal throughput at scale. Same underlying ideas — anchors, degree-proportional hops, indexes — different execution strategy. Lesson 7 unpacks that difference properly.

## Check your understanding

1. With no index on `Account.id`, what does `MATCH (a:Account {id: "ACC-1001"})` actually cost, and why is that the same cost class as an unindexed table scan in a relational database?
2. Lab 4 left `Device` as a super-node on purpose. Using this lesson's execution model, explain precisely why the fraud-detection query from Lab 4 (anchor on `isFraud: true`, expand through `VIA_DEVICE`) stays cheap, while a query that anchored on *all* `Device` nodes first would not — even though both queries touch the same super-node.

## Hands-on lab

[`../labs/05-indexing-performance/README.md`](../labs/05-indexing-performance/README.md) — add real indexes to the Lab 4 graph, grow `dev-0003` into an actual super-node with a batch of unrelated "noise" transactions, then use `PROFILE` to see the label-scan-vs-index-seek and anchored-vs-unconstrained differences directly in the plan, instead of just reading about them.
