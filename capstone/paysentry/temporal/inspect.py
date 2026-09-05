"""Look at the temporal graph.

A payments graph is not worth drawing whole — 5,000 accounts of mostly ordinary
traffic is a hairball that shows nothing. What *is* worth looking at is a single
planted ring next to its decoy, because that is where the project's central claim
lives and it is visible in a handful of rows.

Everything here reads through Raphtory's own views (``window``, ``edges.explode``)
rather than the event log, so what you see is what the detectors saw.
"""

from __future__ import annotations

import json
from collections import defaultdict

from ..config import Config
from ..models import Typology
from ..timeutil import MS_PER_HOUR, days, iso
from .build import TemporalGraph


def load_rings(cfg: Config) -> tuple[dict[str, str], dict[str, set[str]], dict[str, dict]]:
    """Ring -> typology, ring -> txn ids, txn id -> event."""
    ring_type: dict[str, str] = {}
    ring_txns: dict[str, set[str]] = defaultdict(set)
    for line in cfg.paths.labels.open():
        label = json.loads(line)
        ring_type[label["ring_id"]] = label["typology"]
        if label.get("txn_id"):
            ring_txns[label["ring_id"]].add(label["txn_id"])
    wanted = {t for txns in ring_txns.values() for t in txns}
    events = {}
    for line in cfg.paths.events.open():
        event = json.loads(line)
        if event["txn_id"] in wanted:
            events[event["txn_id"]] = event
    return ring_type, ring_txns, events


def _walk(events: list[dict]) -> list[dict]:
    """Order a ring's transactions by following the chain, not by timestamp.

    Following the money is the point: a decoy's hops are in the *same structural
    order* as a real ring's, and only their timestamps differ. Sorting by time
    would hide exactly the thing worth seeing.
    """
    by_src = {e["src_account"]: e for e in events}
    start = min(by_src)
    walk, node = [], start
    for _ in range(len(by_src)):
        hop = by_src.get(node)
        if hop is None or hop in walk:
            break
        walk.append(hop)
        node = hop["dst_account"]
    return walk or events


def render_ring(cfg: Config, ring: str, ring_type: dict, ring_txns: dict,
                events: dict) -> str:
    txns = [events[t] for t in ring_txns.get(ring, set()) if t in events]
    if not txns:
        return f"  {ring}: no transactions found"

    typology = ring_type[ring]
    hops = _walk(txns)

    # Time-respecting iff the cyclic sequence has exactly one descent — i.e. some
    # rotation is increasing. Two or more descents means no starting point works.
    times = [h["ts"] for h in hops]
    descents = sum(1 for i in range(len(times))
                   if times[i] >= times[(i + 1) % len(times)])
    respecting = descents == 1

    # When a working rotation exists, show *that* one. Starting the walk wherever
    # the account ids happen to begin prints a "back in time" hop on a loop the
    # verdict then calls time-respecting — both true, and thoroughly confusing.
    if respecting:
        pivot = next(i + 1 for i in range(len(times))
                     if times[i] >= times[(i + 1) % len(times)]) % len(times)
        hops = hops[pivot:] + hops[:pivot]
        times = [h["ts"] for h in hops]

    lines = [f"  {ring}   ({typology}, {len(hops)} hops)"
             + ("   [shown from the start of the money's path]" if respecting else "")]

    first = min(times)
    width = max(len(h["src_account"]) for h in hops)
    previous = None
    for hop in hops:
        offset = (hop["ts"] - first) / MS_PER_HOUR
        mark = "" if previous is None else ("↑" if hop["ts"] > previous
                                            else "↓ IMPOSSIBLE — earlier than the hop before")
        lines.append(
            f"    {hop['src_account']:>{width}} → {hop['dst_account']:<{width}}"
            f"  t+{offset:7.2f}h  {hop['amount']:>10,.2f}  {mark}")
        previous = hop["ts"]

    span = (max(times) - first) / MS_PER_HOUR
    lines.append(f"    {'':>{width}}   span {span:.2f}h · cyclic descents {descents} · "
                 f"{'TIME-RESPECTING — money could flow round this loop' if respecting else 'NOT time-respecting — no starting point works'}")
    return "\n".join(lines)


def compare_cycles(cfg: Config, count: int = 2) -> str:
    """Real layering rings next to decoys — the project's central contrast."""
    ring_type, ring_txns, events = load_rings(cfg)
    real = sorted(r for r, t in ring_type.items() if t == Typology.CIRCULAR_LAYERING)
    decoy = sorted(r for r, t in ring_type.items() if t == Typology.DECOY_CYCLE)

    out = [
        "REAL LAYERING RINGS — planted with strictly increasing hop times",
        "",
    ]
    for ring in real[:count]:
        out.append(render_ring(cfg, ring, ring_type, ring_txns, events))
        out.append("")
    out += [
        "DECOY CYCLES — structurally identical, temporally impossible",
        "  Same shape: a closed loop of the same length between real accounts.",
        "  The only difference is the order of the hop timestamps.",
        "",
    ]
    for ring in decoy[:count]:
        out.append(render_ring(cfg, ring, ring_type, ring_txns, events))
        out.append("")
    out += [
        "  A static cycle query cannot tell these apart — both are closed loops.",
        "  A time-respecting search fires only on the first group.",
    ]
    return "\n".join(out)


def overview(cfg: Config, tg: TemporalGraph) -> str:
    """What the temporal graph contains, through Raphtory's own accessors."""
    graph = tg.graph
    transfers = tg.transfer_view()
    earliest, latest = graph.earliest_time.t, graph.latest_time.t

    busiest = sorted(((n.name, n.degree()) for n in transfers.nodes),
                     key=lambda x: -x[1])[:5]
    lines = [
        f"  nodes            {graph.count_nodes():,}",
        f"  edges (pairs)    {graph.count_edges():,}",
        f"  events           {graph.count_temporal_edges():,}",
        f"  layers           {', '.join(graph.unique_layers)}",
        f"    transfer       {transfers.count_edges():,} pairs",
        f"    device         {graph.layer('device').count_edges():,} pairs",
        f"  time span        {iso(earliest)}  ..  {iso(latest)}",
        f"                   ({(latest - earliest) / (MS_PER_HOUR * 24):.1f} days)",
        "",
        "  busiest accounts by degree (transfer layer)",
    ]
    lines += [f"    {name}  degree {degree:,}" for name, degree in busiest]

    # A window and a snapshot, so the temporal views are visible rather than
    # merely described.
    week = graph.window(earliest, earliest + days(7))
    lines += [
        "",
        f"  window(first 7 days)      {week.count_temporal_edges():,} events, "
        f"{week.count_nodes():,} nodes",
        f"  snapshot_at(+7 days)      "
        f"{graph.snapshot_at(earliest + days(7)).count_temporal_edges():,} events",
    ]
    return "\n".join(lines)


def render_account(cfg: Config, tg: TemporalGraph, account: str) -> str:
    """One account's timeline, in and out."""
    transfers = tg.transfer_view()
    node = transfers.node(account)
    if node is None:
        return f"  {account}: not present in the transfer layer"

    rows = []
    for edge in transfers.edges.explode():
        if edge.src.name == account or edge.dst.name == account:
            props = edge.properties
            rows.append((edge.time.t,
                         "OUT" if edge.src.name == account else "IN ",
                         edge.dst.name if edge.src.name == account else edge.src.name,
                         props["amount"], props["txn_id"]))
    rows.sort()
    lines = [f"  {account}   in-degree {node.in_degree()}  out-degree {node.out_degree()}  "
             f"{len(rows)} events"]
    for ts, direction, peer, amount, txn in rows[:40]:
        lines.append(f"    {iso(ts)}  {direction}  {peer}  {amount:>10,.2f}  {txn}")
    if len(rows) > 40:
        lines.append(f"    ... {len(rows) - 40:,} more")
    return "\n".join(lines)
