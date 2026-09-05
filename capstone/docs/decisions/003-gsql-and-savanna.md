# 003 — What TigerGraph Savanna actually required

**Status:** accepted · **Phase:** 2–3

Everything here was found by running against a live free-tier Savanna workspace
(TigerGraph 4.2.5). Lab 7 predicted this class of friction; this is the specific
list.

## 1. Global type names do not block local ones

The workspace ships sample graphs (`Transaction_Fraud` and friends) that define
**global** vertex types including `Account`, `Device` and `Merchant` — all three
names PaySentry also wants.

This looked like it forced a `PS_`-style prefix across the whole schema. It does
not. A schema change job scoped to a graph creates *local* types, and TigerGraph
says so explicitly:

```
Trying to add local vertex 'Account' to the graph 'PaySentry'.
Local schema change succeeded.
```

**Decision:** keep the design's names. Prefixing would have made the schema
uglier and diverged from DESIGN.md §3.1 for a conflict that does not exist.
Verified by probe before committing to it.

## 2. `DROP ALL` is catalog-wide — never use it

`provision --drop` originally ran `USE GRAPH X` then `DROP ALL`. `DROP ALL` is
**not** scoped by the preceding `USE GRAPH`: it drops every graph, job and query
on the workspace, which would have destroyed the user's sample graphs.

**Decision:** teardown is `DROP GRAPH <name>` followed by `CREATE GRAPH <name> ()`.
Scoped to our own graph, and the event log can rebuild everything anyway
(DESIGN.md §2.2). This was caught before it ran.

## 3. GSQL v2 puts the direction marker inside the parentheses

Multi-hop patterns in one `FROM` need `SYNTAX v2`, but v2 also changes edge
notation, and mixing the dialects fails with *"The query specifies V2 syntax but
uses V1 here"*:

| v1 | v2 |
|---|---|
| `-(SENT:e)->` | `-(SENT>:e)-` |
| `-(SENT:e)<-` | `-(<SENT:e)-` |
| `-(USED_DEVICE:e)-` | unchanged (undirected) |

## 4. `to_vertex()` is not valid in a vertex-set literal

`srcAcct = {to_vertex(src_account, "Account")};` fails to parse. Declare the
parameter as `VERTEX<Account>` and write `srcAcct = {src_account};`. pyTigerGraph
still passes the primary id from Python, so the caller is unchanged.

## 5. `VERTEX<T>` arguments must be 1-tuples

`{"src_account": "ACC-1"}` is deprecated. pyTigerGraph detects the old form,
**fails the POST and silently retries over GET** — a second round trip on every
authorization. Fixing it to `{"src_account": ("ACC-1",)}` halved screening
latency from ~450ms to ~230ms. Correct form:

```python
conn.runInstalledQuery("screenTransaction", {"src_account": (account_id,)})
```

## 6. GSQL reports failure inside a 200 response

Errors arrive as prose in a successful HTTP response, so output has to be read.
Two rules make that reliable:

* **Run `DROP` statements separately and ignore their errors.** On a fresh graph
  `DROP JOB x` legitimately reports *"could not be found anywhere"*, which is
  indistinguishable from a real failure if the whole script's output is scanned
  for the word "fails". This produced a false failure on a schema change that had
  in fact fully succeeded.
* **Judge the rest on an explicit success marker**, not the absence of scary
  words — a schema change that silently did nothing otherwise looks like one that
  worked.

## 7. REST++ counts lag async upserts

Immediately after loading, `getVertexCount("Txn")` returned **18,953** of 19,517
rows that had in fact all been written; USED_DEVICE showed 268 of 726. Re-reading
shortly after gave exact numbers. Reconciliation against another store must allow
the write to settle, or it reports a mismatch that is not there.

## 8. Latency is network, not query

Measured from a laptop against a cloud workspace, screening 19,517 transactions:

| | p50 |
|---|---|
| bare round trip (`echo`) | 222.1 ms |
| full `screenTransaction` | 228.9 ms |
| **query execution** | **6.7 ms (3%)** |
| network / TLS | 222.1 ms (97%) |

DESIGN.md §5.3 predicted the round trip would dominate; it is 97% of the call.
TigerGraph executes all five checks in 6.7ms, comfortably inside the 150ms
budget — the budget is missed by geography, not by the database. A co-located
application server would not pay this.

Compare SQLite at 3.19ms p50 for the same five checks on the same data: the same
order of magnitude, on a dataset small enough that a graph engine's advantages
have nothing to bite on yet.

## 9. Both engines agree

Screening the same transactions through both stores gave **20/20 identical
decisions and scores**. That is by construction — the signal formulas live once,
in `store/screening.py`, and each store only *measures* the graph its own way
(SQL joins vs GSQL accumulators). Had the maths been duplicated into GSQL, every
engine comparison in the evaluation would have been uninterpretable.
