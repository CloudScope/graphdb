# Lab 8 — Rebuilding the Financial Network as a Temporal Graph

Goal: feel Lesson 8's claims directly — window and "as of" as native operations instead of hand-rolled property filters, and "is this path time-respecting" as something you can actually check instead of eyeballing a timestamp column. Same financial network as Labs 4, 5, and 7, one more time, in a genuinely different shape.

## Step 0 — install Raphtory

```bash
pip install raphtory
```

Like Lab 7's GSQL, Raphtory's Python API has moved around release to release more than Cypher's has. If a method name in `setup.py` or `queries.py` doesn't match what your installed version exposes, check the [Raphtory Python docs](https://docs.raphtory.com) for the current equivalent — the shape of what each call is doing (build a temporal graph, window it, snapshot it) is the point, not any one exact method signature.

## Step 1 — build the graph

Run [`setup.py`](setup.py) directly (`python setup.py`) or import `build_graph()` from it — `queries.py` does the latter. It rebuilds the same six accounts and six transactions as Lab 4's `setup.cypher`, but read the docstring at the top first: each transaction becomes **one timestamped edge directly between two accounts**, not a promoted `Transaction` node. That's Lesson 8's "What happens to the Transaction node" section, made real instead of just argued.

## Step 2 — run the temporal queries

Run [`queries.py`](queries.py) (`python queries.py`). It walks through four things, in order:

1. **A time window** — only transactions between Aug 3rd and Aug 5th.
2. **An "as of" snapshot** — the graph exactly as it looked at noon on Aug 3rd, one transaction short of what a same-day property filter run later that afternoon would have seen.
3. **A time-respecting check on Lab 4's closed loop** — walks `A → B → C → D → E → F → A` in transaction order and confirms each hop's timestamp is strictly later than the one before it, instead of taking the lesson's word for it.
4. **Degree per account, two windows** — the same metric (out-degree, Lesson 6's simplest centrality measure) computed over an early slice of time and a late slice, showing the "ordinary chain" accounts (A, B, C) are only active early, and the "fraud ring" accounts (D, E, F) only late.

## Step 3 — answer Lesson 8's check-your-understanding questions

Lesson 8 asks what a plain property filter fails to guarantee that a time-respecting query wouldn't, and whether `D → E → F` and a hypothetical `F → D` path need different reasoning about time. Form your own answer using what Step 2's output actually showed before reading `answers.md`.
