"""Temporal detectors — the half of the problem a system of record cannot reach.

Every signal here is defined by the *order or spacing* of events, not by the
state they leave behind. That is the dividing line this whole project exists to
measure, and it is why these run in Raphtory rather than in GSQL.

A note on the cycle detector, because it did not survive first contact with the
data. ``algorithms.temporally_reachable_nodes`` looked like the obvious tool, and
it is not: over a 30-day window with six hops, **59 of 60 random accounts reach
themselves**. It answers "can I get there from here", which in a connected
payments graph is almost always yes. What a layering ring actually is, is a
*tight* cycle — closed, short, and completed inside a bounded window. So the
search below walks Raphtory's windowed, exploded edge events directly. Raphtory
supplies the temporal index and the window; the bounded walk is ours. See
``docs/decisions/001-cycle-detection.md``.

The static control is the same walk with the time constraint switched off, over
the same window, hop bound, and seeds. That is what makes the precision
comparison in DESIGN.md §4.3.2 a fair one rather than two different experiments.
"""

from __future__ import annotations

import collections
import statistics
import time
from dataclasses import dataclass, field
from typing import Iterator

from raphtory import algorithms as A

from ..config import Config
from ..models import Engine, RiskSignal, SignalKind
from ..timeutil import MS_PER_DAY, MS_PER_HOUR, days, hours, parse_duration
from .build import DEVICE_LAYER, TRANSFER_LAYER, TemporalGraph

# One exploded transfer event.
Hop = tuple[int, str, float, str]      # (ts, dst, amount, txn_id)
Adjacency = dict[str, list[Hop]]

# Fixed RNG seed for community detection. ``louvain`` accepts no seed and returns
# different communities on every run; an evaluation harness cannot be built on
# that, so seeded label propagation is used instead.
COMMUNITY_SEED = bytes(32)


@dataclass(slots=True)
class TemporalContext:
    """Everything the detectors share: the graph, the window, and the config."""

    cfg: Config
    tg: TemporalGraph
    since: int
    until: int
    timings: dict[str, float] = field(default_factory=dict)
    # Merchant settlement and payroll accounts legitimately show large fan-in and
    # fan-out. Excluding them is not tuning to the generator: a real system knows
    # which of its accounts are businesses.
    business_accounts: frozenset[str] = frozenset()

    @property
    def transfers(self):
        return self.tg.transfer_view()


def _scalar(value) -> float:
    """Unwrap a Raphtory node-state value to a float.

    In 0.17 the algorithm results return a per-node mapping (``{'pagerank': 0.1}``,
    ``{'community_id': 5}``) rather than a bare number. Handling both keeps this
    working if a future release flattens them again.
    """
    if isinstance(value, dict):
        if len(value) != 1:
            raise ValueError(f"expected a single-valued node state, got {value!r}")
        value = next(iter(value.values()))
    return float(value)


def _adjacency(view, reverse: bool = False) -> Adjacency:
    """Materialize a view's exploded events as a time-sorted adjacency list.

    Raphtory windows and explodes; the walk is done here. Sorting by time once
    per window lets every seed reuse the same structure.
    """
    adj: Adjacency = collections.defaultdict(list)
    for edge in view.edges.explode():
        props = edge.properties
        ts = edge.time.t
        if reverse:
            adj[edge.dst.name].append((ts, edge.src.name, props["amount"], props["txn_id"]))
        else:
            adj[edge.src.name].append((ts, edge.dst.name, props["amount"], props["txn_id"]))
    for hops in adj.values():
        hops.sort()
    return adj


def find_cycles(adj: Adjacency, seed: str, max_hops: int, window_ms: int,
                respect_time: bool, max_cycles: int = 4) -> list[list[tuple]]:
    """Bounded search for closed loops back to ``seed``.

    With ``respect_time`` the hop timestamps must strictly increase, so the path
    is one money could actually have taken. Without it the same loop is accepted
    in any order — the control that shows what ignoring time costs.

    Several cycles are collected rather than the first one found. Stopping at the
    first would bias attribution: a seed that sits in both a planted ring and an
    ordinary background loop would be credited with whichever the walk reached
    first, which is an artefact of edge ordering rather than a result.
    """
    found: list[list[tuple]] = []

    def walk(node: str, path: list[tuple], t_start: int | None,
             t_last: int, visited: frozenset[str]) -> None:
        if len(found) >= max_cycles or len(path) >= max_hops:
            return
        for ts, dst, amount, txn_id in adj.get(node, ()):
            if respect_time and ts <= t_last:
                continue
            if t_start is not None and ts - t_start > window_ms:
                continue
            hop = (node, dst, ts, amount, txn_id)
            if dst == seed and len(path) >= 2:
                found.append(path + [hop])
                if len(found) >= max_cycles:
                    return
                continue
            if dst in visited or dst == seed:
                continue
            walk(dst, path + [hop], t_start if t_start is not None else ts,
                 ts if respect_time else t_last, visited | {dst})
            if len(found) >= max_cycles:
                return

    walk(seed, [], None, -1, frozenset({seed}))
    return found


def _windows(ctx: TemporalContext, span: int, step: int) -> Iterator[tuple[int, int]]:
    start = ctx.since
    while start < ctx.until:
        yield start, min(start + span, ctx.until)
        start += step


# --------------------------------------------------------------------------
# 1 + 2. Time-respecting cycles, and the static control
# --------------------------------------------------------------------------

def detect_cycles(ctx: TemporalContext) -> list[RiskSignal]:
    """Sweep rolling windows for tight cycles, both time-respecting and static."""
    started = time.perf_counter()
    tcfg = ctx.cfg.detection.temporal
    span = hours(tcfg.cycle_window_hours)
    # The *view* is twice the cycle window and steps by half of it, which
    # guarantees any loop completing within ``span`` is wholly inside some view.
    # With view == span, a cycle that takes the full window straddles every
    # boundary and is never seen at all.
    view_span = 2 * span
    step = span // 2
    band = tcfg.cycle_retention_band
    max_seeds = tcfg.max_cycle_seeds

    signals: list[RiskSignal] = []
    seen: set[tuple[str, str]] = set()

    for w_start, w_end in _windows(ctx, view_span, step):
        view = ctx.transfers.window(w_start, w_end)
        if view.count_edges() == 0:
            continue
        adj = _adjacency(view)
        # Seeded, not exhaustive (DESIGN.md §6.2). Only accounts that both send
        # and receive inside the window can close a loop, which removes most of
        # the population before any search runs. Among those, rank by
        # in-degree x out-degree — a cheap proxy for "could sit on a cycle".
        #
        # Ranking matters more than it looks: taking sorted(...)[:500] searched
        # only the lowest-numbered accounts, so on the medium profile most rings
        # were never seeded at all and cycle recall collapsed to 3/22.
        senders = collections.Counter()
        receivers = collections.Counter()
        for src, hops in adj.items():
            senders[src] += len(hops)
            for _, dst, _, _ in hops:
                receivers[dst] += 1
        both = set(senders) & set(receivers)
        seeds = sorted(both, key=lambda a: (-(senders[a] * receivers[a]), a))[:max_seeds]

        for respect_time, kind in ((True, SignalKind.TIME_RESPECTING_CYCLE),
                                   (False, SignalKind.STATIC_CYCLE)):
            for seed in seeds:
                if (seed, kind) in seen:
                    continue
                cycles = find_cycles(adj, seed, tcfg.cycle_max_hops, span, respect_time)
                if not cycles:
                    continue
                seen.add((seed, kind))

                for cycle in cycles:
                  amounts = [hop[3] for hop in cycle]
                  ratios = [b / a for a, b in zip(amounts, amounts[1:]) if a > 0]
                  consistent = all(band["min"] <= r <= band["max"] for r in ratios)
                  elapsed = cycle[-1][2] - cycle[0][2]
                  # Tighter loops and fee-consistent amounts read as more
                  # deliberate. Amount consistency is a generic layering
                  # signature, not the generator's own retention band.
                  strength = min(1.0, (0.6 if consistent else 0.3)
                                 + 0.4 * (1 - elapsed / span))
                  signals.append(RiskSignal(
                      account_id=seed, kind=kind, strength=strength,
                      engine=Engine.RAPHTORY, window_start=w_start, window_end=w_end,
                      evidence={
                          "hops": len(cycle),
                          "path": [h[0] for h in cycle] + [seed],
                          "txn_ids": [h[4] for h in cycle],
                          "amounts": [round(a, 2) for a in amounts],
                          "elapsed_hours": round(elapsed / MS_PER_HOUR, 2),
                          "amount_consistent": consistent,
                      }))

    ctx.timings["cycles"] = time.perf_counter() - started
    return signals


# --------------------------------------------------------------------------
# 3. Mule fan-in / fan-out
# --------------------------------------------------------------------------

def detect_fan_in_out(ctx: TemporalContext) -> list[RiskSignal]:
    """Money in from many, most of it back out inside a short holding period.

    The discriminating quantity is the interval between the last inflow and the
    outflow that follows it. Current state holds both sides and not the gap.
    """
    started = time.perf_counter()
    tcfg = ctx.cfg.detection.temporal
    span = parse_duration(tcfg.fan_in_out_window)
    floor = tcfg.fan_in_out_forward_floor
    min_sources = tcfg.fan_in_out_min_sources
    max_dests = tcfg.fan_in_out_max_destinations

    signals: list[RiskSignal] = []
    seen: set[str] = set()

    # Same containment rule as the cycle search: the pattern is inflows spread
    # over a period PLUS an outflow up to `span` after the last of them, so it
    # can run to nearly twice the holding window. A view of exactly `span`
    # straddles it and sees only half the pattern — which showed up as 33% recall
    # on a signal that had just been measured at 100% precision.
    view_span = 2 * span
    for w_start, w_end in _windows(ctx, view_span, span // 2):
        view = ctx.transfers.window(w_start, w_end)
        if view.count_edges() == 0:
            continue
        out_adj = _adjacency(view)
        in_adj = _adjacency(view, reverse=True)

        for account, inflows in in_adj.items():
            # A mule collects from many and forwards to few. Merchant settlement
            # and payroll accounts also have large fan-in or fan-out, but not
            # both in that shape inside a day — the source/destination bounds are
            # what separate them.
            if (account in seen or account in ctx.business_accounts
                    or len({hop[1] for hop in inflows}) < min_sources):
                continue
            outflows = out_adj.get(account, [])
            if not outflows:
                continue
            in_total = sum(hop[2] for hop in inflows)
            last_in = max(hop[0] for hop in inflows)
            after = [hop for hop in outflows if hop[0] >= last_in]
            if not after or in_total <= 0:
                continue
            if len({hop[1] for hop in after}) > max_dests:
                continue
            forwarded = sum(hop[2] for hop in after)
            ratio = forwarded / in_total
            if ratio < floor:
                continue
            hold_h = (min(hop[0] for hop in after) - last_in) / MS_PER_HOUR
            # The holding bound is a property of the pattern, not of the view, so
            # it is enforced here now that the view is wider than the window.
            if hold_h > span / MS_PER_HOUR:
                continue
            seen.add(account)
            signals.append(RiskSignal(
                account_id=account, kind=SignalKind.FAN_IN_OUT_HOLDING,
                strength=min(1.0, ratio) * max(0.3, 1 - hold_h / (span / MS_PER_HOUR)),
                engine=Engine.RAPHTORY, window_start=w_start, window_end=w_end,
                evidence={"sources": len(inflows), "destinations": len(after),
                          "in_total": round(in_total, 2),
                          "forwarded": round(forwarded, 2),
                          "forward_ratio": round(ratio, 3),
                          "holding_hours": round(hold_h, 2)}))

    ctx.timings["fan_in_out"] = time.perf_counter() - started
    return signals


# --------------------------------------------------------------------------
# 4. Dormant reactivation
# --------------------------------------------------------------------------

def detect_dormant_burst(ctx: TemporalContext) -> list[RiskSignal]:
    """A long silence followed by a dense burst.

    Needs two widely separated windows compared against each other — the shape of
    question ``expanding``/``rolling`` exist for, and one a point-in-time query
    cannot express at all.
    """
    started = time.perf_counter()
    tcfg = ctx.cfg.detection.temporal
    gap_ms = days(ctx.cfg.profile.scaled(tcfg.dormancy_gap_days.as_dict()))
    burst_ms = parse_duration(tcfg.dormancy_burst_window)
    min_txns = tcfg.dormancy_burst_min_txns

    history: dict[str, list[int]] = collections.defaultdict(list)
    for edge in ctx.transfers.edges.explode():
        ts = edge.time.t
        history[edge.src.name].append(ts)
        history[edge.dst.name].append(ts)

    signals: list[RiskSignal] = []
    for account, times in history.items():
        if len(times) < min_txns:
            continue
        times.sort()
        gaps = [(b - a, i) for i, (a, b) in enumerate(zip(times, times[1:]))]
        if not gaps:
            continue
        biggest, index = max(gaps)
        if biggest < gap_ms:
            continue
        resume = times[index + 1]
        burst = [t for t in times if resume <= t < resume + burst_ms]
        if len(burst) < min_txns:
            continue
        signals.append(RiskSignal(
            account_id=account, kind=SignalKind.DORMANT_BURST,
            strength=min(1.0, len(burst) / (min_txns * 2)),
            engine=Engine.RAPHTORY, window_start=resume, window_end=resume + burst_ms,
            evidence={"dormant_days": round(biggest / MS_PER_DAY, 1),
                      "burst_txns": len(burst),
                      "burst_window_hours": round(burst_ms / MS_PER_HOUR, 1)}))

    ctx.timings["dormant"] = time.perf_counter() - started
    return signals


# --------------------------------------------------------------------------
# 5. Windowed centrality spikes
# --------------------------------------------------------------------------

def detect_pagerank_spikes(ctx: TemporalContext) -> list[RiskSignal]:
    """PageRank per rolling window; flag accounts whose rank jumps.

    The same metric TigerGraph could compute — run once per window instead of
    once over the graph. That repetition is the temporal part, and it is what
    turns a static ranking into "something changed here".
    """
    started = time.perf_counter()
    tcfg = ctx.cfg.detection.temporal
    span = parse_duration(tcfg.rolling_window)
    step = parse_duration(tcfg.rolling_step)
    sigma = tcfg.pagerank_spike_sigma
    min_active = tcfg.pagerank_min_active_windows
    top_k = max(tcfg.pagerank_top_floor,
                int(ctx.cfg.profile.accounts * tcfg.pagerank_top_fraction))

    series: dict[str, list[float]] = collections.defaultdict(list)
    window_bounds: list[tuple[int, int]] = []
    for w_start, w_end in _windows(ctx, span, step):
        view = ctx.transfers.window(w_start, w_end)
        if view.count_edges() == 0:
            continue
        window_bounds.append((w_start, w_end))
        ranks = A.pagerank(view, iter_count=tcfg.pagerank_iterations,
                           **({} if tcfg.pagerank_max_diff is None
                              else {"max_diff": tcfg.pagerank_max_diff}))
        current = {node.name: _scalar(value) for node, value in ranks.items()}
        for account in set(series) | set(current):
            series[account].append(current.get(account, 0.0))

    signals: list[RiskSignal] = []
    candidates: list[tuple] = []
    for account, values in series.items():
        active = [v for v in values if v > 0]
        # Present in at least half the windows: a spike is a change in a
        # sustained level, not the one appearance of a rarely-seen account.
        if len(values) < 4 or len(active) < max(min_active, len(values) // 2):
            continue
        peak = max(values)
        others = sorted(v for v in active if v != peak)
        if len(others) < 3:
            continue
        # Median and MAD rather than mean and standard deviation: a single large
        # window otherwise inflates its own baseline's spread and suppresses the
        # very spike being looked for.
        median = statistics.median(others)
        mad = statistics.median([abs(v - median) for v in others])
        if mad <= 0:
            # No dispersion in the baseline means there is no baseline to be
            # unusual against. Substituting an epsilon here manufactures an
            # unbounded z-score out of nothing, which is precisely how this
            # detector first came to flag 463 of 500 accounts.
            continue
        z = (peak - median) / (1.4826 * mad)
        if z < sigma:
            continue
        mean = median
        index = values.index(peak)
        bounds = window_bounds[min(index, len(window_bounds) - 1)]
        candidates.append((z, account, peak, mean, len(values), bounds))

    # Rank, then take the top K. A threshold cannot work here (see config), so
    # the signal reports the most anomalous accounts rather than every account
    # that clears a bar almost everything clears.
    # Account id breaks exact ties so the top-K boundary cannot depend on
    # dictionary ordering either.
    candidates.sort(key=lambda c: (-c[0], c[1]))
    ceiling = candidates[0][0] if candidates else 1.0
    for z, account, peak, mean, n_windows, bounds in candidates[:top_k]:
        signals.append(RiskSignal(
            account_id=account, kind=SignalKind.PAGERANK_SPIKE,
            strength=min(1.0, 0.4 + 0.6 * z / ceiling),
            engine=Engine.RAPHTORY, window_start=bounds[0], window_end=bounds[1],
            evidence={"z_score": round(z, 2), "peak": round(peak, 6),
                      "baseline_median": round(mean, 6), "windows": n_windows,
                      "rank_of": len(candidates)}))

    ctx.timings["pagerank"] = time.perf_counter() - started
    return signals


# --------------------------------------------------------------------------
# 6. Ring cohesion
# --------------------------------------------------------------------------

def detect_ring_cohesion(ctx: TemporalContext) -> list[RiskSignal]:
    """Small, tight groups in the device layer, weighted by money moving inside them.

    Communities are found on the **device layer alone**. Run over transfers and
    devices together the graph is one connected blob — the largest community
    covers most of the population and nothing small survives. Device co-use is
    naturally sparse and clique-shaped, so the rings are visible there.

    Transfer activity inside a community is then what separates a fraud ring from
    a household: relatives share a phone but do not systematically pay each other.
    This is the one detector that needs both layers.
    """
    started = time.perf_counter()
    tcfg = ctx.cfg.detection.temporal
    max_size = tcfg.louvain_max_community_size
    min_size = tcfg.community_min_size
    lookback = days(tcfg.lookback_days)
    w_start = max(ctx.since, ctx.until - lookback)

    view = ctx.tg.graph.window(w_start, ctx.until)
    device_view = view.layer(DEVICE_LAYER)
    transfer_view = view.layer(TRANSFER_LAYER)

    signals: list[RiskSignal] = []
    if device_view.count_edges() == 0:
        ctx.timings["cohesion"] = time.perf_counter() - started
        return signals

    communities: dict[int, list[str]] = collections.defaultdict(list)
    for node, value in A.label_propagation(
            device_view, iter_count=tcfg.community_iterations,
            seed=COMMUNITY_SEED).items():
        communities[int(_scalar(value))].append(node.name)

    for members in communities.values():
        if not min_size <= len(members) <= max_size:
            continue
        member_set = set(members)
        device_ties = 0
        for account in members:
            node = device_view.node(account)
            if node is not None:
                device_ties += sum(1 for peer in node.neighbours
                                   if peer.name in member_set)
        transfer_ties = 0
        for account in members:
            node = transfer_view.node(account)
            if node is not None:
                transfer_ties += sum(1 for peer in node.out_neighbours
                                     if peer.name in member_set)
        # Device sharing alone is a household. A ring also moves money among
        # itself — at least one internal transfer per member on average.
        # Requiring merely transfer_ties > 0 flagged 509 accounts at 20%
        # precision and lent spurious recall to every typology.
        if device_ties == 0 or transfer_ties < len(members):
            continue

        size = len(members)
        # Density of device sharing, lifted when the same group also moves money
        # among itself — the combination is the claim, not either half.
        density = device_ties / size
        strength = min(1.0, 0.25 * density + 0.15 * (transfer_ties / size))
        if strength <= 0:
            continue
        for account in sorted(members):
            signals.append(RiskSignal(
                account_id=account, kind=SignalKind.RING_COHESION,
                strength=strength, engine=Engine.RAPHTORY,
                window_start=w_start, window_end=ctx.until,
                evidence={"community_size": size, "device_ties": device_ties,
                          "transfer_ties": transfer_ties,
                          "members": sorted(members)[:12]}))

    ctx.timings["cohesion"] = time.perf_counter() - started
    return signals


DETECTORS = (detect_cycles, detect_fan_in_out, detect_dormant_burst,
             detect_pagerank_spikes, detect_ring_cohesion)


def run_all(ctx: TemporalContext) -> list[RiskSignal]:
    signals: list[RiskSignal] = []
    for detector in DETECTORS:
        signals.extend(detector(ctx))
    return signals
