# Graph Databases — From Basics to a TigerGraph vs. Raphtory Evaluation

A course for going from "never touched a database" to being able to competently evaluate **TigerGraph vs. Pometry/Raphtory**. Written as a series of lessons; foundational hands-on labs use Neo4j (Cypher) as the friendly on-ramp before the course specializes toward GSQL (TigerGraph) and Raphtory's Python/temporal API.

## Who this is for

Complete beginner to databases in general. No SQL background assumed — each lesson builds the relational comparison first, then shows where/why graphs diverge. There's no real project driving this — the concrete deliverable is a well-grounded comparison of the two target products, not architectural advice for a specific system.

## Important framing, up front

TigerGraph and Raphtory are **not apples-to-apples**:

- **TigerGraph** — a distributed, disk-backed, transactional + analytical graph *database* with its own query language (GSQL). Built to be a persistent system of record you run queries against, at scale.
- **Raphtory (by Pometry)** — an embeddable, in-memory, Rust-based **temporal graph analytics engine** with a Python-first API. Not a persistence-first server database — built around ingesting a graph and running time-aware analytics on it (time-travel through history, temporal motifs, evolving-graph algorithms).

Part of the point of this course is to get you to a place where you can articulate *that* distinction precisely, not just benchmark features side by side. The final lesson makes the comparison explicit; every lesson before it is building the vocabulary and mental models needed to make that comparison mean something.

## Curriculum

| # | Lesson | Status |
|---|--------|--------|
| 1 | [Why graph databases exist](01-why-graph-databases.md) | Done |
| 2 | [Nodes, relationships, properties — the property graph model](02-property-graph-model.md) | Done |
| 3 | [Cypher fundamentals](03-cypher-fundamentals.md) (hands-on, [Lab 3](../labs/03-cypher-basics/README.md)) | Done — lab pending user setup |
| 4 | [Graph data modeling](04-graph-data-modeling.md) — thinking in relationships, not tables ([Lab 4](../labs/04-data-modeling/README.md)) | Done — lab pending user setup |
| 5 | [Indexing, traversal performance, and how graph engines actually execute queries](05-indexing-and-traversal-performance.md) (hands-on, [Lab 5](../labs/05-indexing-performance/README.md)) | Done — lab pending user setup |
| 6 | [OLTP vs. OLAP graph workloads](06-oltp-vs-olap-graph-workloads.md) — transactional queries vs. whole-graph analytics | Done |
| 7 | [Distributed / scale-out graph architecture, and GSQL](07-distributed-architecture-and-gsql.md) (TigerGraph specifics, hands-on, [Lab 7](../labs/07-tigergraph-gsql/README.md)) | Done — lab pending user setup |
| 8 | [Temporal graphs](08-temporal-graphs-and-raphtory.md) — time as a first-class dimension, and Raphtory specifics (hands-on, [Lab 8](../labs/08-raphtory-temporal/README.md)) | Done — lab pending user setup |
| 9 | [Scaling, clustering, and production concerns](09-scaling-and-production-concerns.md) (consistency, backups, operations) | Done |
| 10 | [**TigerGraph vs. Raphtory** — the evaluation](10-tigergraph-vs-raphtory-evaluation.md) | Done |

## Labs

Hands-on exercises live in [`../labs`](../labs), one subfolder per lesson that has an exercise. Foundational labs (Lessons 3–5) use [Neo4j](https://neo4j.com). Later labs (7–8) touch TigerGraph Community Edition and Raphtory directly.
