"""Profile a CSV before loading it into Raphtory.

Two different questions get conflated when people ask "is my data valid for a
graph engine", and they deserve separate answers:

1. **Can Raphtory load it?** A narrow, mechanical question. Raphtory needs three
   things — a source id, a destination id, and a timestamp. Everything else is
   optional. Rows missing any of the three are simply not loadable.

2. **Will a temporal graph tell you anything?** A judgement question, and the
   more important one. A table with ten distinct entities is not a graph problem.
   A table where every row shares one timestamp has no temporal dimension to
   analyse, however many rows it has. Loading either wastes the engine.

This reports on both, and says plainly when the answer to the second is no.
Nothing here is specific to payments — it profiles any edge-list export.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# Column names that usually mean what they say. Only used to rank candidates —
# never to decide, because an export from someone else's schema will not match.
SRC_HINTS = ("src", "source", "from", "sender", "origin", "payer", "debit", "caller")
DST_HINTS = ("dst", "dest", "target", "to", "receiver", "payee", "credit", "callee")
TIME_HINTS = ("time", "ts", "timestamp", "date", "created", "occurred", "event", "when")
ID_HINTS = ("id", "uuid", "key", "ref", "no", "number", "account", "user", "customer")


@dataclass
class ColumnProfile:
    name: str
    dtype: str
    nulls: int
    distinct: int
    samples: list[str] = field(default_factory=list)
    numeric: bool = False
    time_like: bool = False
    min_v: object = None
    max_v: object = None

    def null_rate(self, rows: int) -> float:
        return self.nulls / rows if rows else 0.0

    def cardinality(self, rows: int) -> float:
        return self.distinct / rows if rows else 0.0


@dataclass
class Finding:
    level: str            # "pass" | "warn" | "fail"
    title: str
    detail: str


def _looks_temporal(values: list[str]) -> bool:
    """Does this column plausibly hold timestamps?"""
    hits = 0
    for v in values[:40]:
        s = str(v)
        if re.match(r"^\d{4}-\d{2}-\d{2}", s):              # ISO date
            hits += 1
        elif re.match(r"^\d{9,13}$", s):                     # epoch s / ms
            hits += 1
        elif re.match(r"^\d{2}[/-]\d{2}[/-]\d{4}", s):       # dd/mm/yyyy
            hits += 1
    return hits >= max(3, len(values[:40]) // 2)


def _score(name: str, hints: tuple[str, ...]) -> int:
    low = name.lower()
    return max((len(h) for h in hints if h in low), default=0)


def profile_csv(path: Path, limit: int | None = 200_000,
                src: str | None = None, dst: str | None = None,
                time_col: str | None = None) -> tuple[str, bool]:
    """Return (report, ok_to_load)."""
    import pyarrow as pa
    import pyarrow.csv as pv

    table = pv.read_csv(str(path))
    rows = table.num_rows
    if limit and rows > limit:
        table = table.slice(0, limit)

    # ---- per-column profile -------------------------------------------
    cols: list[ColumnProfile] = []
    for name in table.column_names:
        col = table.column(name)
        values = col.to_pylist()
        non_null = [v for v in values if v is not None and v != ""]
        distinct = len(set(non_null))
        numeric = pa.types.is_integer(col.type) or pa.types.is_floating(col.type)
        cp = ColumnProfile(
            name=name, dtype=str(col.type), nulls=len(values) - len(non_null),
            distinct=distinct, samples=[str(v) for v in non_null[:3]],
            numeric=numeric,
            time_like=_looks_temporal([str(v) for v in non_null]) or
                      (numeric and non_null and 1e8 < abs(float(non_null[0])) < 1e14),
        )
        if non_null:
            try:
                cp.min_v, cp.max_v = min(non_null), max(non_null)
            except TypeError:
                pass
        cols.append(cp)

    by_name = {c.name: c for c in cols}
    n = table.num_rows

    # ---- pick the three required roles ---------------------------------
    def best(hints, predicate=lambda c: True):
        ranked = sorted((c for c in cols if predicate(c)),
                        key=lambda c: (-_score(c.name, hints), -c.cardinality(n)))
        return ranked[0] if ranked else None

    id_like = lambda c: c.cardinality(n) > 0.0005 and c.distinct > 1 and not c.time_like
    src_c = by_name.get(src) or best(SRC_HINTS, id_like)
    dst_c = by_name.get(dst) or best(DST_HINTS, id_like)
    time_c = by_name.get(time_col) or best(TIME_HINTS, lambda c: c.time_like)

    out: list[str] = []
    w = out.append
    w(f"  file            {path}")
    w(f"  rows            {rows:,}" + (f"  (profiled first {n:,})" if n < rows else ""))
    w(f"  columns         {len(cols)}")
    w("")
    w("  COLUMNS")
    w(f"    {'name':22s} {'type':10s} {'nulls':>7s} {'distinct':>10s}  sample")
    for c in cols:
        w(f"    {c.name[:22]:22s} {c.dtype[:10]:10s} "
          f"{c.null_rate(n):>6.1%} {c.distinct:>10,}  {', '.join(c.samples)[:38]}")
    w("")

    # ---- role assignment ------------------------------------------------
    w("  ROLES RAPHTORY NEEDS")
    for role, c, why in (("source", src_c, "high-cardinality id column"),
                         ("destination", dst_c, "high-cardinality id column"),
                         ("time", time_c, "parses as a timestamp")):
        w(f"    {role:12s} {'-> ' + c.name if c else '** NONE FOUND **':28s} "
          f"{'(' + why + ')' if c else ''}")
    props = [c for c in cols if c not in (src_c, dst_c, time_c) and c.numeric]
    layers = [c for c in cols
              if c not in (src_c, dst_c, time_c) and 1 < c.distinct <= 12]
    w(f"    properties   -> {', '.join(c.name for c in props) or '(none numeric)'}")
    w(f"    layer        -> {', '.join(c.name for c in layers) or '(no low-cardinality column)'}"
      f"{'  <- candidates for layer=' if layers else ''}")
    w("")

    findings: list[Finding] = []
    add = lambda lv, t, d: findings.append(Finding(lv, t, d))

    if not (src_c and dst_c and time_c):
        add("fail", "required roles missing",
            "Raphtory needs a source, a destination and a timestamp. "
            "Name them explicitly with --src/--dst/--time if the guess is wrong.")
        w("  VALIDITY")
        for f in findings:
            w(f"    [{f.level.upper():4s}] {f.title}: {f.detail}")
        return "\n".join(out), False

    src_v = table.column(src_c.name).to_pylist()
    dst_v = table.column(dst_c.name).to_pylist()
    t_v = table.column(time_c.name).to_pylist()

    # ---- validity: can these rows be loaded at all? ---------------------
    bad = sum(1 for i in range(n)
              if src_v[i] in (None, "") or dst_v[i] in (None, "") or t_v[i] in (None, ""))
    add("fail" if bad > n * 0.05 else ("warn" if bad else "pass"),
        "rows loadable",
        f"{n - bad:,} of {n:,} have all three required fields"
        + (f"; {bad:,} would be dropped" if bad else ""))

    pairs = [(src_v[i], dst_v[i]) for i in range(n)
             if src_v[i] not in (None, "") and dst_v[i] not in (None, "")]
    self_loops = sum(1 for a, b in pairs if a == b)
    add("warn" if self_loops > n * 0.02 else "pass", "self-loops",
        f"{self_loops:,} rows where source == destination"
        + (" — legal, but they carry no path information" if self_loops else ""))

    exact = Counter((src_v[i], dst_v[i], t_v[i]) for i in range(n))
    dupes = sum(v - 1 for v in exact.values() if v > 1)
    add("warn" if dupes > n * 0.05 else "pass", "exact duplicates",
        f"{dupes:,} rows repeat an existing (source, destination, timestamp)"
        + (" — Raphtory keeps them as separate events" if dupes else ""))

    # ---- is there actually a temporal dimension? ------------------------
    times = [t for t in t_v if t not in (None, "")]
    distinct_t = len(set(times))
    add("fail" if distinct_t <= 1 else ("warn" if distinct_t < n * 0.01 else "pass"),
        "temporal variance",
        f"{distinct_t:,} distinct timestamps across {len(times):,} rows"
        + (" — with one timestamp there is no time dimension to analyse"
           if distinct_t <= 1 else ""))

    if distinct_t > 1:
        try:
            lo, hi = min(times), max(times)
            span = f"{lo}  ..  {hi}"
        except TypeError:
            span = "unorderable"
        add("pass", "time span", span)

    # ---- is it graph-shaped? --------------------------------------------
    nodes = set(src_v) | set(dst_v)
    nodes.discard(None); nodes.discard("")
    density = len(pairs) / max(len(nodes), 1)
    add("fail" if len(nodes) < 20 else ("warn" if len(nodes) < 200 else "pass"),
        "entity count",
        f"{len(nodes):,} distinct nodes from {len(pairs):,} edges "
        f"({density:.1f} edges per node)"
        + (" — too few entities to be a graph problem" if len(nodes) < 20 else ""))

    deg = Counter()
    for a, b in pairs:
        deg[a] += 1; deg[b] += 1
    if deg:
        top, top_n = deg.most_common(1)[0]
        share = top_n / (2 * len(pairs))
        add("warn" if share > 0.15 else "pass", "super-nodes",
            f"busiest node {str(top)[:24]} touches {share:.1%} of all edge endpoints"
            + (" — it will dominate traversals; consider excluding or layering it"
               if share > 0.15 else ""))

    reciprocal = sum(1 for a, b in set(pairs) if (b, a) in set(pairs))
    add("pass" if reciprocal else "warn", "bidirectional structure",
        f"{reciprocal:,} node pairs transact in both directions"
        + (" — no reciprocity means no cycles to find" if not reciprocal else ""))

    w("  VALIDITY  — can Raphtory load it?")
    for f in findings[:4]:
        w(f"    [{f.level.upper():4s}] {f.title:24s} {f.detail}")
    w("")
    w("  SUITABILITY  — will a temporal graph tell you anything?")
    for f in findings[4:]:
        w(f"    [{f.level.upper():4s}] {f.title:24s} {f.detail}")
    w("")

    fails = [f for f in findings if f.level == "fail"]
    warns = [f for f in findings if f.level == "warn"]
    if fails:
        w("  VERDICT   NOT READY — " + "; ".join(f.title for f in fails))
    elif warns:
        w(f"  VERDICT   loadable, with {len(warns)} thing(s) to look at above")
    else:
        w("  VERDICT   ready to load")
    w("")
    w("  SUGGESTED CALL")
    w(f"    graph.load_edges(table,")
    w(f"        time=\"{time_c.name}\", src=\"{src_c.name}\", dst=\"{dst_c.name}\",")
    if props:
        w(f"        properties={[c.name for c in props]},")
    if layers:
        w(f"        layer=\"...\")   # or one graph layer per {layers[0].name} value")
    else:
        w(f"        layer=\"default\")")
    return "\n".join(out), not fails
