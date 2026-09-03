# Lab 5 — Indexing and Traversal Performance

Goal: stop reasoning about query cost in the abstract and actually look at the plan. This lab reuses Lab 4's financial network, adds real indexes, and grows `dev-0003` into a genuine super-node — then walks through `PROFILE` output to see the label-scan-vs-index-seek and targeted-vs-broad differences from [Lesson 5](../../course/05-indexing-and-traversal-performance.md) directly, instead of just reading about them.

## Step 1 — Load the model

Run [`setup.cypher`](setup.cypher) in Neo4j Browser. On top of Lab 4's people/accounts/transactions/devices, it adds:

- Three indexes: `Account.id`, `Device.id`, `Transaction.isFraud`
- A shared `ACC-MERCHANT` account and 8 unrelated "noise" customers, each sending one small, ordinary payment to that merchant — all coincidentally from `dev-0003`, the same device the fraud ring's `txn-5`/`txn-6` already used in Lab 4

After this, `dev-0003` has 10 `VIA_DEVICE` edges instead of 2. Nothing about the fraud ring changed — you've just given the device enough innocent traffic to behave like a real super-node.

## Step 2 — Work through the PROFILE exercises

Open [`exercises.cypher`](exercises.cypher) and run each `PROFILE` query one at a time in Neo4j Browser. For each pair, look at the plan Neo4j prints above the results — not just the returned rows — and compare:

1. **Indexed vs. unindexed anchor.** Same label, one property with an index (`id`), one without (`openedDate`). Which operator sits at the bottom of each plan — `NodeIndexSeek` or `NodeByLabelScan`? Which one has a bigger number of rows flowing out of that first operator?
2. **Confirm the super-node.** A plain aggregation — no `PROFILE` needed here, just look at the counts.
3. **Targeted vs. broad.** One query anchors on the single known-fraud transaction; the other anchors on *every* transaction and asks the same "what shares a device with what" question. Compare both the `DB Hits` totals and the row counts returned.
4. **Bounded vs. unbounded variable-length.** Same shortest-path question, one with a bare `*`, one with `*1..6`. Read the note in `answers.md` before concluding too much from this one on a graph this small.
5. **Predict before you check.** `EXPLAIN` only — reason about which side of the pattern is more selective before running it.

## Step 3 — Answer Lesson 5's check-your-understanding questions

Lesson 5 asks what an unindexed anchor lookup costs and why the fraud query in Lab 4 stayed cheap despite `Device` being a super-node. Form your own answer using this lab's actual plan output before reading `answers.md`.
