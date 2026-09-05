# 002 — Raphtory's parallel algorithms are not reproducible by default

**Status:** accepted · **Phase:** 5

## Context

Phase 7 compares two engines by rerunning them and reading the difference. That
requires a run to be reproducible. It is not, by default.

Two separate sources were found:

1. **`pagerank`** returns values differing by roughly 0.2% between runs on the
   same graph. Enough to reshuffle a top-K boundary and change which accounts get
   flagged.
2. **`louvain`** returns materially different communities every run — observed
   sizes `[74, 69, 64, …]`, `[89, 75, 67, …]`, `[93, 85, 78, …]` on three
   consecutive calls over an identical graph. It accepts **no seed parameter**.

`label_propagation` does take a `seed` argument, so it looked like the fix. It is
not: with a fixed 32-byte seed it still produced different communities run to run.
The seed governs the algorithm's own tie-breaking, not the parallel scheduling
above it.

The common cause is parallel iteration order — Raphtory is Rust, and its
algorithms run through rayon, where the order of floating-point accumulation and
of label updates depends on how work happens to be scheduled.

## Decision

1. **Pin rayon to a single thread** for all analysis runs. `paysentry analyze`
   defaults to `--threads 1` and sets `RAYON_NUM_THREADS` before Raphtory is
   first imported. Verified: byte-identical `signals.jsonl` and `scores.jsonl`
   across repeated runs.
2. ~~Converge PageRank tightly.~~ **Reverted.** Tight convergence
   (`iter_count=200, max_diff=1e-14`) does make PageRank reproducible even
   multi-threaded, but it is not needed once rayon is pinned to one thread — and
   it costs 941ms per window against 59ms at the defaults, which was **365s of a
   370s run** on the medium profile. Single-threading alone is deterministic at
   default convergence, verified. Left at the defaults.
3. **Use seeded `label_propagation` rather than `louvain`** for community
   detection. Under a single thread both are reproducible, but only one of them
   documents a seed, and depending on an algorithm with no reproducibility story
   at all is the worse bet if this ever runs multi-threaded again.
4. **Sort all output before writing.** Even with identical content, detectors
   iterate dictionaries whose order is not guaranteed. The evaluation compares
   files, so ordering has to be pinned independently of content.

## Consequences

- Measured cost of single-threading on the `small` profile: 16.28s vs 16.19s —
  nothing. This workload is dominated by Python-side walking and windowing, not
  by the Rust algorithms, so there is currently no reproducibility/speed
  trade-off to make. That may change at the `large` profile, and if it does the
  right answer is to keep determinism and say what it costs.
- `--threads N` is exposed for anyone who wants the parallel behaviour back.
- **This is a reportable property of Raphtory, not a defect in this project.**
  Any pipeline feeding these outputs into decisions that get audited needs to
  know that the default configuration does not reproduce.
