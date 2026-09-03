# Lesson 4 — Graph Data Modeling: Thinking in Relationships, Not Tables

You now know the building blocks (Lesson 2) and how to query them (Lesson 3). This lesson is about the actual design skill: given a real domain, how do you decide what's a node, what's a relationship, and what's a property? Get this wrong and every query downstream is either impossible to write cleanly or slow — this is where "graph database" stops being a syntax lesson and becomes an architecture decision.

## The core mindset shift: model the questions, not the data

Relational modeling (if you'd learned it first) teaches you to normalize based on the *data's own structure* — functional dependencies, avoiding update anomalies — independent of what you'll query. Graph modeling works backwards from that: **you model based on the traversals you actually need to be fast.** The same real-world domain can legitimately be modeled two different ways depending on what questions the application asks.

Concrete process:

1. **Write down the actual questions** the system needs to answer, in plain English. Not "what are the entities" — "what will someone ask this database."
2. **Underline the nouns** in those questions — these are your node candidates.
3. **Underline the verbs** connecting nouns — these are your relationship candidates.
4. **Draw it**, then mentally walk each question as a pattern over your drawing. If a question requires jumping between things with no direct or short path, your model is wrong, not your query.

This sounds soft, but it's the single biggest differentiator between someone who's used Cypher and someone who can actually design a graph schema for production. TigerGraph's own modeling guidance (Lesson 7) and Raphtory's ingestion model (Lesson 8) both assume you've done this step first — neither tool rescues a bad model.

## Deeper heuristics: node vs. property vs. relationship

Lesson 2 gave you the basic rule (would I query "give me all the X"?). Two refinements that matter once domains get real:

**Promote a property to a node when it needs its own identity or connects to more than one thing.** Classic anti-pattern:

```
(alice:Person {name: "Alice", company: "Acme Corp", companyFounded: 1998})
```

This buries `Acme Corp` as a string. You can't ask "who else works at Acme," can't attach `Acme Corp`'s own relationships (suppliers, investors), and if two people work at the same company, you're duplicating `companyFounded` on every one of their nodes with no way to enforce it stays consistent. Once you promote it:

```
(alice:Person)-[:WORKS_AT]->(acme:Company {founded: 1998})
```

...`acme` is a real node other people and things can connect to, and the fact lives in exactly one place.

**The reverse also happens: don't over-model.** If "favorite color" will never be queried as its own entity, never connects to anything else, and doesn't need `count(all colors)`-style analysis, it's just a property. Making it a node (`(:Person)-[:HAS_FAVORITE_COLOR]->(:Color {name: "Blue"})`) adds traversal overhead for zero query benefit. Judgment call, driven by step 1's question list.

## The limitation nobody mentions early enough: relationships are strictly binary

A property graph relationship connects **exactly two nodes.** No exceptions. Real-world facts are often *not* binary — a bank transaction has a sender, a receiver, an amount, a timestamp, and maybe a device it was made from. That's not "two things related," it's one event with several participants and attributes.

The fix is a well-known pattern: **model the event itself as a node**, with binary relationships out to each participant.

```
(sender:Account)-[:SENT]->(txn:Transaction {amount: 500, timestamp: "2026-08-14T10:03:00Z"})-[:RECEIVED_BY]->(receiver:Account)
(txn)-[:VIA_DEVICE]->(device:Device {id: "dev-8823"})
```

Now `txn` is a first-class node — you can query "all transactions over $10,000," "all transactions from this device," or connect a transaction to more participants later (a co-signer, a merchant category) without redesigning anything. This pattern — sometimes called modeling an **n-ary relationship as a node** — comes up constantly in fraud detection, supply chain, and healthcare domains, and it's exactly the shape TigerGraph markets its fraud-ring use case around. We'll build on this exact example for the rest of the course.

## The anti-pattern in the other direction: super-nodes

A node with an extremely high number of relationships — a `Country` node connected to 50 million `Person` nodes, say — creates a **traversal hot-spot**. Any query that has to pass *through* that node (not just land on it) suddenly has to consider a huge fan-out at that one step, even if the rest of the path is cheap. This is a real, common production problem, not a theoretical one — it's the graph-modeling equivalent of a "hot partition" in a distributed system. We'll go deeper on why this hurts performance specifically in Lesson 5 (traversal execution internals); for now, just know that **degree (number of relationships) is a modeling concern, not just an implementation detail** — if your model has an inevitable super-node, ask in step 1 whether you actually need to traverse *through* it, or just query *from* it.

## Normalization instinct, inverted

In relational design, duplicating data is usually a smell — you normalize it away. In graph modeling, **mild, deliberate denormalization is a legitimate and common optimization**, applied surgically to your hottest query paths. Example: if "how many transactions has this account sent, total" is asked constantly, you might maintain a `txnCount` property directly on `Account`, updated as transactions are added, rather than counting the relationship every time. This trades a small write-time cost for a large read-time win — a judgment call you make explicitly, driven by your question list from step 1, not a rule you apply everywhere.

## Worked example — the financial network model

We're going to keep using this domain for the rest of the course (it reappears in Lesson 6 as an OLTP-vs-OLAP example, Lesson 7 in TigerGraph/GSQL, and Lesson 8 for temporal analytics in Raphtory — transactions have natural timestamps).

**Questions driving the model:**
- Which accounts has this account sent money to, directly or within N hops?
- Which transactions came from the same device as a known-fraudulent transaction?
- What's the shortest path connecting two accounts (are they part of the same ring)?
- Total transaction volume per account.

**Model:**

```
(:Person)-[:OWNS]->(:Account {id, openedDate})
(:Account)-[:SENT]->(:Transaction {amount, timestamp})-[:RECEIVED_BY]->(:Account)
(:Transaction)-[:VIA_DEVICE]->(:Device {id, fingerprint})
```

Walk each question against this drawing:
- "Accounts this account sent to, within N hops" → `(a:Account)-[:SENT]->(:Transaction)-[:RECEIVED_BY]->(b:Account)` chained `*1..N` — direct pattern match.
- "Same device as a known-fraudulent transaction" → `(fraud:Transaction)-[:VIA_DEVICE]->(d:Device)<-[:VIA_DEVICE]-(other:Transaction)` — one shared node, two edges in.
- "Shortest path between two accounts" → `shortestPath()` over the `SENT`/`RECEIVED_BY` chain, exactly like Lesson 3's friend-path example.
- "Total volume per account" → aggregate `sum(txn.amount)` over outgoing `SENT` edges — or, per the denormalization note above, a maintained `totalSent` property if this is queried constantly.

Every one of these was hard or ugly in a relational schema (self-joins on a transactions table, recursive CTEs for the N-hop and shortest-path questions) and is a direct pattern here — this is Lesson 1's promise, now applied to a domain that looks like real work instead of a toy.

## Check your understanding

1. You're modeling a hospital system: `Patient`, `Doctor`, `Prescription` (a doctor prescribes a specific drug, at a specific dose, to a patient, on a specific date). Is `Prescription` a relationship or a node? Why?
2. In the financial network model above, `Device` could theoretically become a super-node (thousands of transactions funneled through one shared device in a fraud ring). Is that a modeling mistake to fix, or is it actually the point? Explain.

## Hands-on lab

[`../labs/04-data-modeling/README.md`](../labs/04-data-modeling/README.md) — build the financial network model in your AuraDB instance from Lab 3, load a small transaction dataset including a deliberate device-sharing pattern, and write the queries above yourself before checking the answer key.
