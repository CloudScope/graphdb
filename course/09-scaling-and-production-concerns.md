# Lesson 9 — Scaling, Clustering, and Production Concerns

Lessons 1–8 built the vocabulary to model a graph, query it, understand what makes a query fast or slow, and place three real engines against the OLTP/OLAP and static/temporal axes. None of that tells you what it takes to keep one of these systems running, correctly, under real usage. This lesson is the operational layer — consistency under concurrent writes, backups, scaling levers, and workload isolation — and it turns out to be the layer where "TigerGraph vs. Raphtory, not apples-to-apples" stops being a framing device and becomes a literal, practical fact: the two products don't just answer the production-concerns question differently, they aren't answering the *same* question.

## Consistency under concurrent, distributed writes

Lesson 7 introduced partitioning and the cost of a cross-partition hop. It left out what happens when a write touches vertices on more than one partition at the same time another client is reading or writing nearby data. A single-machine engine can serialize this with a lock and move on; a distributed one has to coordinate across machines, and that coordination is where the CAP-theorem tradeoff (consistency vs. availability, under a network partition) stops being trivia and becomes a real design decision every distributed graph database has to make and disclose.

- **Neo4j's clustering model** (Causal Clustering) uses a leader for writes and read replicas for reads, with a consensus protocol keeping the core cluster in agreement about what's been committed. Reads can be made **causally consistent** — a client that just wrote something can ask to see a state that includes its own write, even if it's talking to a different machine than the one it wrote to — without paying for full synchronous consistency on every single read.
- **TigerGraph**, as a distributed transactional-and-analytical system of record (Lesson 6, Lesson 7), has to make an equivalent set of guarantees explicit across its partitioned cluster: a transaction spanning multiple partitions needs distributed coordination, not just a local lock, to commit correctly.
- **Raphtory doesn't face this problem in the same form**, and that's worth sitting with rather than skimming past: it's not that Raphtory has a weaker answer, it's that an embeddable, in-memory analytics engine mostly isn't accepting concurrent production writes from many clients the way a live transactional store is — so "how do you keep a distributed write path consistent" is largely not Raphtory's problem to solve, because it isn't attempting the job that creates the problem.

## Backups and durability: who actually owns the source of truth

Durability isn't automatic — a system has to deliberately write data somewhere that survives a crash, and have a process for restoring it. Neo4j and TigerGraph are both **systems of record**: durable, transactional storage is core to what they are, and both ship backup/restore tooling because losing the graph means losing data nobody else has a copy of.

Raphtory is different in a way that's easy to gloss over: as an embeddable, in-memory engine, if the process holding a Raphtory graph dies without you having persisted anything yourself, the graph is gone. That's not a missing feature — Raphtory was never positioned as the durable copy (Lesson 6, Lesson 8). In production, the durability responsibility sits **upstream**: the transaction log, the event stream, the data warehouse the temporal graph gets built from is the actual system of record, and Raphtory's job is to load it and analyze it, potentially rebuilding the in-memory graph from scratch on a schedule or on demand. Getting this backwards — treating a Raphtory process as if it were durable storage — is the single most consequential production mistake this lesson can flag, precisely because nothing about using Raphtory day-to-day makes the mistake obvious until a process restarts.

## Scaling levers

- **TigerGraph** scales out by adding machines and repartitioning (Lesson 7). Repartitioning a live cluster is itself an operationally expensive event — it means moving data between machines and potentially disrupting the locality a good partitioning had already achieved, so it's planned capacity work, not a switch you flip mid-incident.
- **Neo4j** scales reads horizontally via read replicas; write throughput is bounded by the leader, so vertical scaling (a bigger machine) is typically the lever for write-heavy growth rather than adding more machines the way TigerGraph does.
- **Raphtory** scales the way any embeddable analytics library does: give the process more memory and cores, or parallelize the analysis job across more processes/machines yourself at the orchestration layer. There's no cluster to grow because there was never a persistent cluster in the first place.

## Workload isolation: the production version of Lesson 6

Lesson 6 already made the technical case: an OLTP-tuned engine and an OLAP-tuned workload don't naturally coexist, because touching the whole graph is exactly the expensive case OLTP storage wasn't built for. In production this becomes a scheduling and infrastructure decision, not just an execution-model detail: a big analytical job — the kind Lesson 6 and Lesson 8 both described, PageRank over the whole graph, a temporal analysis run per window — has to be kept from starving latency-sensitive transactional queries. This is the literal, practical reason Neo4j's GDS library runs against a separate in-memory projection instead of the live transactional graph (Lesson 6): it isn't just an implementation detail, it's workload isolation, the same pattern as running analytics against a read replica or a data warehouse instead of a production OLTP database in the relational world. TigerGraph's native support for both workloads doesn't remove this concern so much as make it a capacity-planning and query-governance problem — you still don't want an ungoverned whole-graph analytical query competing for the same resources as live fraud-detection lookups, even if the same engine is technically capable of both.

## The financial network, in production

- **Neo4j**: a causal cluster with read replicas serving Labs 4–5's fraud-lookup queries (latency-sensitive, OLTP), GDS analytics (Lesson 6's connected-components ring detection) run periodically against a separate projection so a big analytical pass never competes with live lookups, regular backups of the transactional graph itself.
- **TigerGraph**: a partitioned cluster sized so that `Device`/fraud-ring traversals (Lesson 5's targeted-vs-broad distinction) mostly stay local, and schema changes — adding a `Merchant` vertex type, say — planned deliberately, because schema-first (Lesson 2, Lesson 7) means a production schema migration is a real operation, not an incremental `MERGE`.
- **Raphtory**: no live cluster at all — a scheduled job or streaming pipeline rebuilds the temporal graph from the actual transaction log (which lives in a real system of record elsewhere), runs the Lesson 8 windowed and time-respecting-path analysis, and hands results downstream. The durability question was answered before Raphtory ever got involved.

## Check your understanding

1. Why is "Raphtory doesn't have a strong backup story" a misleading way to frame Raphtory's durability model? What's the more accurate framing, and why does it matter for how you'd actually deploy it?
2. Neo4j runs GDS algorithms against a separate in-memory projection rather than the live transactional graph. Using this lesson's workload-isolation argument, explain why TigerGraph supporting both workloads natively (Lesson 7) doesn't fully eliminate the need for something equivalent to that separation in production.

## Coming up

Lesson 10 is the payoff this whole course has been building toward: taking every architectural difference from Lessons 1–9 — schema strictness, execution model, distribution, OLTP/OLAP support, temporal awareness, and now operational maturity — and using them to make the TigerGraph vs. Raphtory evaluation explicit, instead of asserting it the way the course's introduction did on day one.
