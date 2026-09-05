"""Verify Raphtory — the engine, its temporal semantics, and its central claim.

There is no connection to test: Raphtory runs in this process. So "verify" means
something more useful than reachability, in four escalating steps:

1. **The engine** is installed at the pinned version and exposes the API this
   project uses. Raphtory's API has moved between releases (see
   ``docs/raphtory-notes.md``), so this fails loudly rather than at the first
   detector that touches a renamed method.
2. **Reproducibility** is configured. Raphtory's parallel algorithms give
   different answers run to run unless rayon is pinned to one thread
   (``docs/decisions/002-nondeterministic-algorithms.md``).
3. **The graph reconciles** with the event log it was built from — the same
   check anyone should run after any ingest.
4. **The temporal views are exact.** ``window``, ``at``, ``snapshot_at``,
   ``before`` and ``after`` are recomputed independently from the raw
   timestamps in the log and compared. This is the part worth having: every
   temporal result in this project rests on those five being right, and
   "the library is probably correct" is not a verification.

The final check is the strongest one available: it re-runs the **decoy control**
end to end. If time-respecting cycle search stops distinguishing planted rings
from temporally-impossible ones, every conclusion in the report is void — and
this says so in one command.
"""

from __future__ import annotations

import json
import os

from ..config import Config
from ..store.connectivity import CheckResult

# What the detectors actually call. Verified against the installed package rather
# than assumed from documentation.
REQUIRED_GRAPH_API = ("add_edge", "add_node", "load_edges", "window", "at",
                      "snapshot_at", "before", "after", "rolling", "expanding",
                      "layer", "edges", "nodes", "count_nodes", "count_edges",
                      "count_temporal_edges", "earliest_time", "latest_time")
REQUIRED_ALGORITHMS = ("pagerank", "label_propagation", "louvain",
                       "temporally_reachable_nodes", "weakly_connected_components")


def check_raphtory(cfg: Config) -> CheckResult:
    result = CheckResult()

    # -- 1. engine ------------------------------------------------------
    try:
        import raphtory
        from raphtory import Graph, algorithms
    except ImportError as exc:
        result.add("raphtory installed", False, str(exc))
        result.hint = "pip install -r requirements.txt"
        return result

    pinned = ""
    requirements = cfg.paths.root / "requirements.txt"
    if requirements.exists():
        for line in requirements.read_text().splitlines():
            if line.strip().startswith("raphtory=="):
                pinned = line.strip().split("==")[1]
    version = raphtory.__version__
    result.add("raphtory installed", True,
               f"{version}" + (f" (pinned {pinned})" if pinned else ""))
    if pinned and version != pinned:
        result.add("version matches the pin", False,
                   f"installed {version}, requirements.txt pins {pinned}")
        result.hint = ("Raphtory's API has moved between releases. Either install "
                       f"the pin (pip install raphtory=={pinned}) or re-verify the "
                       "API surface and update docs/raphtory-notes.md.")
        return result

    missing_api = [name for name in REQUIRED_GRAPH_API if not hasattr(Graph, name)]
    result.add("Graph API surface present", not missing_api,
               f"{len(REQUIRED_GRAPH_API)} methods checked"
               + (f"; MISSING {missing_api}" if missing_api else ""))
    missing_algos = [n for n in REQUIRED_ALGORITHMS if not hasattr(algorithms, n)]
    result.add("algorithms present", not missing_algos,
               f"{len(REQUIRED_ALGORITHMS)} checked"
               + (f"; MISSING {missing_algos}" if missing_algos else ""))
    if missing_api or missing_algos:
        result.hint = "See docs/raphtory-notes.md for the drift log."
        return result

    # -- 2. reproducibility --------------------------------------------
    # Two separate things, and conflating them gave a misleading FAIL: whether
    # THIS process is pinned, and whether the command that actually runs the
    # algorithms pins itself. `analyze` defaults to --threads 1 and sets the
    # variable before importing raphtory, so the second is what matters.
    threads = os.environ.get("RAYON_NUM_THREADS")
    from ..cli import build_parser

    analyze_default = build_parser().parse_args(["analyze"]).threads
    result.add("analyze pins rayon to one thread", analyze_default == 1,
               f"analyze --threads default = {analyze_default}"
               + (f"; this process: RAYON_NUM_THREADS={threads}" if threads else ""))

    # -- 3. dataset ------------------------------------------------------
    if not cfg.paths.events.exists():
        result.add("dataset present", False, f"no {cfg.paths.events}")
        result.hint = f"paysentry generate --profile {cfg.profile.name}"
        return result

    from ..timeutil import days
    from .build import build_graph
    from .extract import extract

    until = cfg.end_time_ms
    since = until - days(min(cfg.detection.temporal.lookback_days,
                             cfg.profile.span_days))
    timestamps = sorted(json.loads(line)["ts"] for line in cfg.paths.events.open())
    in_range = [t for t in timestamps if since <= t < until]

    transfers, device_pairs = extract(cfg, since, until)
    tg = build_graph(transfers, device_pairs)
    graph = tg.transfer_view()

    events = graph.count_temporal_edges()
    result.add("graph reconciles with the log", events == len(in_range),
               f"{events:,} temporal edges vs {len(in_range):,} transfers in range")
    if events != len(in_range):
        result.hint = "Ingest lost or duplicated events; check extract.py."
        return result

    # -- 4. temporal view semantics --------------------------------------
    # Recomputed from raw timestamps, independently of Raphtory.
    mid = in_range[len(in_range) // 2]
    cases = [
        ("window(since, mid)  half-open [start, end)",
         sum(1 for t in in_range if since <= t < mid),
         graph.window(since, mid).count_temporal_edges()),
        ("at(mid)             exactly at the instant",
         sum(1 for t in in_range if t == mid),
         graph.at(mid).count_temporal_edges()),
        ("snapshot_at(mid)    everything up to and including",
         sum(1 for t in in_range if t <= mid),
         graph.snapshot_at(mid).count_temporal_edges()),
        ("before(mid)         exclusive of the bound",
         sum(1 for t in in_range if t < mid),
         graph.before(mid).count_temporal_edges()),
        ("after(mid)          exclusive of the bound",
         sum(1 for t in in_range if t > mid),
         graph.after(mid).count_temporal_edges()),
    ]
    for label, expected, got in cases:
        result.add(f"temporal view: {label}", expected == got,
                   f"expected {expected:,}, got {got:,}")
    if any(expected != got for _, expected, got in cases):
        result.hint = ("A temporal view returned the wrong slice. Every result in "
                       "this project rests on these; do not trust the reports "
                       "until this passes.")
        return result

    # -- 5. the decoy control -------------------------------------------
    if not cfg.paths.labels.exists():
        result.add("decoy control", False, "no labels file; run generate --verify")
        return result

    from ..models import Typology
    from ..timeutil import hours
    from .signals import _adjacency, find_cycles

    # One pass over the log, building everything the loop below needs. It
    # previously re-read the whole file per ring: 360 rings x 1.95M lines on the
    # large profile, which turned a check into a coffee break.
    ring_txns: dict[str, set[str]] = {}
    ring_type: dict[str, str] = {}
    txn_ts: dict[str, int] = {}
    txn_src: dict[str, str] = {}
    for line in cfg.paths.events.open():
        event = json.loads(line)
        txn_ts[event["txn_id"]] = event["ts"]
        txn_src[event["txn_id"]] = event["src_account"]
    for line in cfg.paths.labels.open():
        label = json.loads(line)
        ring_type[label["ring_id"]] = label["typology"]
        if label.get("txn_id"):
            ring_txns.setdefault(label["ring_id"], set()).add(label["txn_id"])

    span = hours(cfg.detection.temporal.cycle_window_hours)
    hops = cfg.detection.temporal.cycle_max_hops
    real_found = decoy_found = real_total = decoy_total = 0

    skipped = 0
    for ring, typology in sorted(ring_type.items()):
        if typology not in (Typology.CIRCULAR_LAYERING, Typology.DECOY_CYCLE):
            continue
        txns = ring_txns.get(ring, set())
        if not txns:
            continue
        # Only rings wholly inside the analysis window can be found in it. The
        # lookback (90d) is shorter than the large profile's span (180d), so
        # roughly half the planted rings predate the window — counting those as
        # misses reported 88/180 and a FAIL for something that was working.
        if not all(since <= txn_ts[t] < until for t in txns):
            skipped += 1
            continue
        start = min(txn_ts[t] for t in txns) - hours(1)
        view = graph.window(start, start + 2 * span)
        adj = _adjacency(view)
        # Seed from the ring's own accounts, read straight off the index.
        members = {txn_src[t] for t in txns if t in txn_src}
        hit = False
        for seed in sorted(members):
            for cycle in find_cycles(adj, seed, hops, span, respect_time=True):
                if {hop[4] for hop in cycle} & txns:
                    hit = True
                    break
            if hit:
                break
        if typology == Typology.CIRCULAR_LAYERING:
            real_total += 1
            real_found += hit
        else:
            decoy_total += 1
            decoy_found += hit

    control_ok = real_total and real_found == real_total and decoy_found == 0
    result.add("decoy control holds", bool(control_ok),
               f"time-respecting search traced {real_found}/{real_total} real "
               f"layering rings and {decoy_found}/{decoy_total} decoys "
               f"(decoys must be 0)"
               + (f"; {skipped} rings outside the {cfg.detection.temporal.lookback_days}d "
                  f"lookback were not evaluated" if skipped else ""))
    if not control_ok:
        result.hint = ("The central control has broken: time-respecting search no "
                       "longer separates real rings from temporally-impossible "
                       "ones. Every conclusion in reports/ depends on this.")
        return result

    return result.finish(
        "Raphtory verified: engine, reproducibility, ingest, temporal view "
        "semantics, and the decoy control all hold.")
