# Lesson 6 — OLTP vs. OLAP Graph Workloads

Every query in Lessons 3–5 had the same shape: start from one or a few known nodes, expand outward a bounded number of hops, stay cheap because you never touch more of the graph than you need to. That shape has a name — it's **OLTP**. This lesson introduces its counterpart, **OLAP**, where the question isn't "what's near this specific node" but "what does the whole graph look like" — and where the anchor-and-stay-bounded trick from Lesson 5 stops applying entirely.

## Two different kinds of question

**OLTP (online transaction processing)** — many small, low-latency queries against a live, mutating dataset. "Did Alice send this transaction." "Add a new transaction." "What did this account send in the last hour." Point lookups and bounded traversals, mixed with writes, usually with strong consistency guarantees (ACID) because the data is live and other queries are reading and writing it concurrently. Every query from Labs 3–5 — the shortest path between two accounts, the accounts reached within N hops, the transactions sharing a device with a known-fraud case — is OLTP-shaped: anchored, bounded, fast because it's targeted.

**OLAP (online analytical processing)** — fewer, larger, longer-running queries that scan most or all of the graph, usually read-only, usually against a batch or snapshot rather than the live store, optimized for throughput over latency. The answer isn't a specific row, it's a structural or aggregate property of the graph itself: *which accounts are structurally central to the money-flow network? Which clusters of accounts and devices are unusually densely connected? What does the whole transaction graph decompose into, if you group everything by who's reachable from whom?*

## Why the OLTP trick stops working

Lesson 5's entire argument was: anchor on a selective node, expand outward, and the cost stays proportional to the degree of the nodes you actually touch, not the size of the graph. That only works because you know in advance which node to start from — the known fraud transaction, the specific account.

OLAP algorithms don't have that luxury, by design. **PageRank** — "how structurally important is each node" — has to consider every node and repeatedly propagate scores across every edge until the numbers stop changing; there's no smaller starting set, because the whole point is you don't yet know which nodes matter. **Connected components** — "group every node into the cluster of things reachable from it" — has to visit every node and edge at least once to assign it to a component; skipping part of the graph means silently missing a cluster. The cost model flips: instead of "proportional to the degree of the nodes I chose to touch," it becomes **proportional to the size of the whole graph** (`O(V + E)`, often repeated across many iterations until the computation converges). There is no anchor to make this cheap, because being exhaustive is the requirement, not a side effect of a bad query.

## What OLAP graph algorithms actually look like

A short, recurring vocabulary, worth having now because it resurfaces in Lessons 7 and 8:

| Algorithm | Answers |
|---|---|
| Degree / betweenness / closeness centrality | Which nodes are structurally important, and in what way |
| PageRank / eigenvector centrality | Which nodes are important because important things point to them |
| Weakly / strongly connected components | Which nodes fall into the same reachable cluster |
| Community detection (Louvain, label propagation) | Which nodes form a densely-connected group, without a pre-defined cluster count |
| Triangle counting / similarity | Which neighborhoods are unusually tightly interconnected |

Notice the reframe: Lab 4's fraud query found transactions sharing a device *because you already had a known-fraud transaction to anchor from*. Connected components or community detection over the whole financial graph could surface an equally suspicious cluster **with no prior fraud flag at all** — discovering the ring structurally, instead of confirming a ring you already suspected. That's the actual value OLAP analytics adds on top of OLTP traversal: it finds what you didn't know to look for.

## Why this is an architecture decision, not just a query-type distinction

An engine tuned for OLTP — fast index-free adjacency, pointer-chasing storage, ACID transactions under concurrent writes — is not naturally good at bulk, parallel, whole-graph computation; touching every node with transactional guarantees intact is exactly the expensive case that architecture wasn't built for. An engine tuned for OLAP — bulk-loaded, often columnar or partitioned in-memory representations, built for parallel iteration over the entire dataset — is not naturally good at low-latency single-item lookups with concurrent writes; that's not what it was built for either. Serving both well from one storage engine is a genuine, hard architectural problem, not a checkbox.

This is where the three engines in this course actually diverge, and it's worth naming plainly now:

- **Neo4j** ships a transactional OLTP core (everything through Lesson 5) plus a separate **Graph Data Science (GDS)** library for OLAP algorithms — which typically runs against an **in-memory graph projection**, a separate read-optimized copy pulled out of the live transactional store, not the live store itself. That separation isn't an implementation detail; it's the direct consequence of the cost-model mismatch above.
- **TigerGraph** is built to do both from one system — real-time transactional updates *and* large-scale parallel analytics, natively, which is the specific architectural claim behind calling it "transactional + analytical" in this course's framing (Lesson 7 unpacks how).
- **Raphtory** is analytics-first, full stop — an embeddable, in-memory engine built around ingesting a graph and running whole-graph (and temporal) analytics over it, not a live transactional store you'd point a production application's writes at (Lesson 8).

That's the vocabulary this course's final comparison needs: not "which one is faster," but "which combination of these two workload shapes does each product actually commit to."

## Optional: seeing the shift firsthand

If you still have the Lab 4/5 financial-network graph loaded, Neo4j's GDS library makes the contrast concrete without a full new lab. GDS algorithms run in two steps — project a read-only in-memory graph, then run an algorithm over that projection:

```cypher
CALL gds.graph.project(
  'financialNetwork',
  ['Account', 'Transaction', 'Device'],
  ['SENT', 'RECEIVED_BY', 'VIA_DEVICE']
);

CALL gds.wcc.stream('financialNetwork')
YIELD nodeId, componentId
RETURN gds.util.asNode(nodeId).id AS node, componentId
ORDER BY componentId;
```

`wcc` is weakly connected components — every node in the graph gets grouped into a cluster, in one pass over the whole projection, with no anchor at all. Compare that call shape to every `PROFILE`'d query from Lab 5: there's no `NodeIndexSeek`, no selective starting property — the entire projected graph is the input.

## Check your understanding

1. Why doesn't Lesson 5's "anchor on the selective side, stay bounded" advice apply to PageRank or connected components? Answer in terms of what each approach actually needs to guarantee about the graph.
2. Pick one OLTP-shaped and one OLAP-shaped question the financial network model could answer. For each, describe what a `PROFILE`'d plan would roughly have to touch — a bounded set of nodes proportional to degree, or the whole graph — and why.

## Coming up

Lesson 7 takes this OLTP/OLAP split and grounds it in TigerGraph specifically: how GSQL and its distributed, compiled execution model are built to serve both workload shapes from one system, and what that costs architecturally. Lesson 8 does the same for Raphtory, adding time as a first-class dimension on top of the OLAP-first analytics model introduced here.
