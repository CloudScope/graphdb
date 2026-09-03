# Lab 7 — Standing Up TigerGraph and Porting the Financial Network to GSQL

Goal: feel Lesson 7's claims directly instead of just reading them — schema-first as a real constraint you have to satisfy before loading any data, and GSQL's bulk-frontier-per-hop execution model as something you write, not just a description of it. This reuses the exact Lab 4 dataset, translated vertex by vertex into GSQL.

## Step 0 — a note before you start

This lab is less turnkey than Labs 3–5. Neo4j AuraDB is a hosted signup; TigerGraph Community Edition is software you run yourself, and GSQL syntax shifts slightly between releases in ways Cypher mostly hasn't. Expect to hit an error, read what it says, and adjust a line — that friction is itself part of what this lesson is teaching: a schema-first, compiled query language doesn't let you iterate the way `MERGE`-first Cypher does. Check the [TigerGraph Community Edition docs](https://docs.tigergraph.com) for the current download/install instructions and exact GSQL syntax for your version if anything here doesn't run as-is.

## Step 1 — get TigerGraph CE running

TigerGraph publishes a Docker image for Community Edition. The general shape, current as of writing but worth confirming against TigerGraph's own docs for your platform:

```bash
docker pull tigergraph/community:latest
docker run -d -p 14022:22 -p 14240:14240 --name tigergraph tigergraph/community:latest
docker exec -it tigergraph gsql
```

That last command drops you into the `gsql` shell inside the container — everything below runs there (or via `gsql <file>` from outside the container, whichever your setup supports). GraphStudio, TigerGraph's visual UI, is usually reachable at `http://localhost:14240` once the container's finished starting up, if you'd rather browse the graph visually after loading it than stay in the shell.

## Step 2 — define the schema

Run [`schema.gsql`](schema.gsql). This is the step Neo4j never made you do: every vertex type (`Person`, `Account`, `Txn`, `Device`) and edge type (`OWNS`, `SENT`, `RECEIVED_BY`, `VIA_DEVICE`) has to exist before you can load a single row. Read through it before running it — it's short, and it's the clearest possible illustration of Lesson 2's "TigerGraph is schema-first" line.

## Step 3 — load the data

Run [`load.gsql`](load.gsql). It's the same six people, six accounts, four devices, and six transactions as Lab 4's `setup.cypher` — deliberately unchanged, so the only new thing you're learning here is the translation, not a new dataset.

## Step 4 — install and run the queries

Run [`queries.gsql`](queries.gsql) to define and install three queries, then run each one:

```
RUN QUERY accountsReached("ACC-1001")
RUN QUERY sharedDeviceAsFraud("txn-5")
RUN QUERY totalSentPerAccount()
```

Before running them, reread each query in `queries.gsql` next to its Cypher equivalent from Labs 4–5 (`labs/04-data-modeling/exercises.cypher`, `labs/05-indexing-performance/exercises.cypher`). Notice which one starts from `{seed}` — a single vertex — and which starts from `{Account.*}` — the whole vertex type. That's Lesson 6's OLTP/OLAP distinction, spelled out as the one thing that changed in the query.

## Step 5 — answer Lesson 7's check-your-understanding questions

Lesson 7 asks why a cross-partition hop is so much more expensive than a pointer hop, and what compiling a query ahead of time (`INSTALL QUERY`) trades away versus Cypher's plan-per-execution model. Form your own answer using what Step 2 and Step 4 actually felt like before reading `answers.md`.
