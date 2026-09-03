# Lab 8 — Answers & Explanations

**1. Window (Aug 3–Aug 5, exclusive).** `txn-3` (`ACC-1003 → ACC-1004`, Aug 3 14:15) and `txn-4` (`ACC-1004 → ACC-1005`, Aug 4 08:45). Everything else falls outside the range — `txn-2` ends Aug 2, `txn-5` starts Aug 5 at 02:10, before the window's Aug 5 00:00 boundary is even reached in wall-clock terms but *after* it in the sense that matters: the window's upper bound is exclusive at the instant Aug 5 00:00, and `txn-5` happens two hours into Aug 5, so it's correctly excluded.

**2. Snapshot as of Aug 3, 12:00.** `txn-1` and `txn-2` only — `txn-3` doesn't post until 14:15 that same day, two hours later. This is the concrete version of Lesson 8's claim: the snapshot isn't a filter you wrote, it's a state the graph genuinely was in at that instant, and the engine reconstructs it directly.

**3. Time-respecting check.** All six hops print `OK`, and the final line reads `time-respecting: True`. Every timestamp in the chain — `txn-1` through `txn-6` — is strictly later than the one before it, so the closed loop from Lab 4 wasn't just structurally a cycle, it was a path the money could actually have taken, in that exact order. This is the answer Lab 4's exercise 5 asserted by eye; Lab 8 makes it a checkable fact.

**4. Degree per window.** `ACC-1001`, `ACC-1002`, `ACC-1003` each show `early=1, late=0`; `ACC-1004`, `ACC-1005`, `ACC-1006` each show `early=0, late=1`. The "ordinary chain" (Alice → Bob → Carol → Dave) and the "fraud ring" (Dave → Eve → Frank → Alice) aren't just structurally different parts of the graph — they're *temporally* separate too, active in different windows. A static engine would have to run this same query twice, against two manually-filtered copies of the graph, and stitch the two result sets together by hand; here it's one metric, two windows, same call.

---

## Lesson 8 check-your-understanding — answers

**1. Why doesn't `WHERE timestamp > X AND timestamp < Y` give the same guarantee as a native time-respecting query?**

The property filter only checks that each *individual* edge falls in a range — it says nothing about the *relationship between hops* in a multi-edge path. `MATCH (a)-[:SENT]->(t1)-[:RECEIVED_BY]->(b)-[:SENT]->(t2)-[:RECEIVED_BY]->(c) WHERE t1.timestamp > X AND t2.timestamp < Y` would happily match a path where `t2` happened *before* `t1` — two edges each individually inside the window, in an order that makes no physical sense as a flow of money. Lab 8's step 3 query specifically checks that each hop's timestamp is later than the *previous hop's*, not just that it falls in some absolute range — that pairwise ordering constraint is exactly what a plain property filter, applied independently to each edge, structurally cannot express without you manually threading the comparison between every consecutive pair yourself.

**2. Is `D → E → F` time-respecting? What about a hypothetical `F → D`?**

`D → E → F` is `txn-4` (Aug 4, 08:45) then `txn-5` (Aug 5, 02:10) — later, so yes, time-respecting; this is a real 2-hop slice of the same chain step 3 verified in full. A hypothetical path from `F` back to `D` needs different reasoning entirely, not just reversed timestamps: the actual edges in this graph run `D → E`, `E → F`, and separately `F → A` — there is no edge from `F` to `D` at all, so any "path" from `F` to `D` would have to route through the *reverse* of an existing directed edge, or through some other connection entirely. Even if such a path existed structurally, being time-respecting would require each of *its* hops to be in increasing time order too — it isn't automatically true, or automatically false, just because the forward direction was time-respecting. Time-respecting is a property of the specific sequence of edges in the specific order you traverse them, not a property of the underlying graph as a whole.
