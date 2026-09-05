"""The five planted fraud typologies, plus the decoy cycles.

These are chosen so the detection burden **splits across the two engines** rather
than pooling in one (DESIGN.md §4.3). Two are shallow and point-in-time, and a
graph database answers them on the authorization path. Three are defined by the
*order and spacing* of events, and no amount of current-state querying recovers
them.

The decoy cycles are the sharpest instrument here. A decoy is a genuine closed
loop in the transfer graph whose timestamps are arranged so that **no rotation of
it is increasing** — money could not have flowed around it in any order. A static
cycle query cannot tell a decoy from a real ring; a time-respecting one must.
The precision gap between those two numbers is the cleanest measurement this
project makes, and it exists only because the decoys are here.

Accounts are reserved exclusively: no account participates in two typologies, so
a detection attributes to exactly one ring and the ground truth stays unambiguous.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..config import Config
from ..models import Channel, Label, Typology
from ..timeutil import MS_PER_DAY, MS_PER_HOUR, days, hours
from .background import CH_INDEX, Traffic
from .population import Population


@dataclass(slots=True)
class Planted:
    """Everything a typology contributes to the dataset."""

    ts: list[int] = field(default_factory=list)
    src: list[int] = field(default_factory=list)
    dst: list[int] = field(default_factory=list)
    amount: list[float] = field(default_factory=list)
    channel: list[int] = field(default_factory=list)
    device: list[int] = field(default_factory=list)
    merchant: list[int] = field(default_factory=list)
    ring_id: list[str] = field(default_factory=list)
    typology: list[str] = field(default_factory=list)

    account_labels: list[Label] = field(default_factory=list)
    quiet_windows: list[tuple[int, int, int]] = field(default_factory=list)

    def add(self, ts: int, src: int, dst: int, amount: float, channel: str,
            device: int, ring_id: str, typology: str, merchant: int = -1) -> None:
        self.ts.append(int(ts))
        self.src.append(int(src))
        self.dst.append(int(dst))
        self.amount.append(round(float(amount), 2))
        self.channel.append(CH_INDEX[channel])
        self.device.append(int(device))
        self.merchant.append(int(merchant))
        self.ring_id.append(ring_id)
        self.typology.append(typology)

    def label_accounts(self, accounts: list[int], pop: Population,
                       ring_id: str, typology: str) -> None:
        for idx in accounts:
            self.account_labels.append(
                Label(ring_id=ring_id, typology=typology,
                      account_id=pop.account_id(idx))
            )

    def extend(self, other: Planted) -> None:
        for attr in ("ts", "src", "dst", "amount", "channel", "device",
                     "merchant", "ring_id", "typology", "account_labels",
                     "quiet_windows"):
            getattr(self, attr).extend(getattr(other, attr))

    def __len__(self) -> int:
        return len(self.ts)

    def to_traffic(self) -> Traffic:
        if not self.ts:
            return Traffic.empty()
        return Traffic(
            ts=np.array(self.ts, dtype=np.int64),
            src=np.array(self.src, dtype=np.int64),
            dst=np.array(self.dst, dtype=np.int64),
            amount=np.array(self.amount, dtype=np.float64),
            channel=np.array(self.channel, dtype=np.int8),
            device=np.array(self.device, dtype=np.int64),
            merchant=np.array(self.merchant, dtype=np.int64),
        )


class AccountPool:
    """Hands out accounts exclusively, so ground truth stays unambiguous.

    Accounts already used by an organic near-pattern are excluded outright.
    They are labelled ``legitimate``, and letting one also join a fraud ring
    would give a single account two contradictory ground-truth labels — which
    would not crash anything, it would quietly corrupt every recall number
    computed downstream. It would also let an organic dormancy window delete a
    fraud ring's transactions.
    """

    def __init__(self, pop: Population, rng: np.random.Generator) -> None:
        self._pop = pop
        reserved: set[int] = set()
        for group in pop.family_groups:
            reserved.update(group)
        for group in pop.settle_up_groups:
            reserved.update(group)
        reserved.update(pop.seasonal_dormant)
        available = np.array([i for i in pop.personal_idx if int(i) not in reserved],
                             dtype=np.int64)
        if not len(available):
            raise RuntimeError("no accounts left after excluding organic near-patterns")
        self._order = list(rng.permutation(available))
        self._cursor = 0

    def take(self, n: int, distinct_customers: bool = False) -> list[int]:
        """Take ``n`` unused accounts, optionally all owned by different customers."""
        chosen: list[int] = []
        seen: set[str] = set()
        while len(chosen) < n and self._cursor < len(self._order):
            idx = int(self._order[self._cursor])
            self._cursor += 1
            if distinct_customers:
                owner = self._pop.accounts[idx].customer_id
                if owner in seen:
                    continue
                seen.add(owner)
            chosen.append(idx)
        if len(chosen) < n:
            raise RuntimeError(
                "account pool exhausted while planting typologies — reduce ring "
                "counts in config.yaml or raise the profile's account count"
            )
        return chosen

    @property
    def remaining(self) -> int:
        return len(self._order) - self._cursor


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _span(cfg: Config) -> tuple[int, int]:
    start = cfg.end_time_ms - days(cfg.profile.span_days)
    return start, cfg.end_time_ms


def _own_device(pop: Population, account: int, rng: np.random.Generator) -> int:
    devices = pop.account_devices[account]
    return int(devices[rng.integers(0, len(devices))])


def _cyclic_descents(seq: np.ndarray) -> int:
    """Number of positions where the sequence drops, read around the cycle."""
    return int((seq > np.roll(seq, -1)).sum())


def _non_time_respecting_order(rng: np.random.Generator, k: int) -> np.ndarray:
    """A hop ordering for which **no rotation** is increasing.

    A cyclic sequence of distinct values has an increasing rotation if and only
    if it has exactly one cyclic descent. Requiring two or more therefore makes
    the loop time-respecting from no starting point at all — which is precisely
    what a decoy has to guarantee to be a fair control.
    """
    if k < 4:
        raise ValueError("a decoy cycle needs at least 4 hops")
    for _ in range(200):
        order = rng.permutation(k)
        if _cyclic_descents(order) >= 2:
            return order
    raise RuntimeError(f"could not construct a non-time-respecting order for k={k}")


# --------------------------------------------------------------------------
# 4.3.1 Structuring / smurfing  — detectable by BOTH engines
# --------------------------------------------------------------------------

def plant_structuring(cfg: Config, rng: np.random.Generator, pop: Population,
                      pool: AccountPool) -> Planted:
    """One source disperses just under the reporting threshold, mules collect.

    Amounts sit a few percent below the threshold — never on it, never at a round
    number — so this is found by the *shape of the dispersal*, not by the amounts
    looking odd on their own.
    """
    spec = cfg.generation.typologies.structuring
    threshold = cfg.generation.reporting_threshold
    window = hours(spec.window_hours)
    start, end = _span(cfg)
    out = Planted()

    for ring in range(cfg.profile.scaled(spec.rings.as_dict())):
        ring_id = f"STRUCT-{ring:04d}"
        n_mules = int(rng.integers(spec.mules_per_ring["min"],
                                   spec.mules_per_ring["max"] + 1))
        members = pool.take(n_mules + 2)
        source, collector, mules = members[0], members[1], members[2:]
        base = int(rng.integers(start, max(start + 1, end - window - hours(6))))

        for mule in mules:
            shave = rng.uniform(spec.under_threshold_pct["min"],
                                spec.under_threshold_pct["max"])
            amount = threshold * (1.0 - shave)
            t_in = base + int(rng.integers(0, window // 2))
            out.add(t_in, source, mule, amount, Channel.P2P_TRANSFER,
                    _own_device(pop, source, rng), ring_id, Typology.STRUCTURING)

            t_out = t_in + int(rng.integers(hours(1), hours(12)))
            out.add(min(t_out, end - 1), mule, collector,
                    amount * rng.uniform(0.90, 0.99), Channel.P2P_TRANSFER,
                    _own_device(pop, mule, rng), ring_id, Typology.STRUCTURING)

        out.label_accounts(members, pop, ring_id, Typology.STRUCTURING)
    return out


# --------------------------------------------------------------------------
# 4.3.2 Circular layering  — RAPHTORY, plus the decoy control
# --------------------------------------------------------------------------

def plant_circular_layering(cfg: Config, rng: np.random.Generator,
                            pop: Population, pool: AccountPool) -> Planted:
    """Closed cycles that return funds to the origin, minus a fee at each hop.

    Real rings get strictly increasing timestamps around the loop. Decoys get an
    ordering with two or more cyclic descents, so they are structurally identical
    and temporally impossible.
    """
    spec = cfg.generation.typologies.circular_layering
    window = hours(spec.window_hours)
    start, end = _span(cfg)
    n_rings = cfg.profile.scaled(spec.rings.as_dict())
    n_decoys = int(n_rings * spec.decoy_ratio)
    out = Planted()

    def build(ring_id: str, typology: str, time_respecting: bool) -> None:
        k = int(rng.integers(spec.hops["min"], spec.hops["max"] + 1))
        members = pool.take(k)
        base = int(rng.integers(start, max(start + 1, end - window - hours(6))))
        offsets = np.sort(rng.integers(0, window, k))
        if not time_respecting:
            offsets = offsets[_non_time_respecting_order(rng, k)]

        amount = float(rng.uniform(4_000, 60_000))
        for hop, (a, b) in enumerate(zip(members, members[1:] + members[:1])):
            out.add(min(base + int(offsets[hop]), end - 1), a, b, amount,
                    Channel.P2P_TRANSFER, _own_device(pop, a, rng),
                    ring_id, typology)
            amount *= rng.uniform(spec.retention["min"], spec.retention["max"])

        out.label_accounts(members, pop, ring_id, typology)

    for ring in range(n_rings):
        build(f"CIRC-{ring:04d}", Typology.CIRCULAR_LAYERING, time_respecting=True)
    for ring in range(n_decoys):
        build(f"DECOY-{ring:04d}", Typology.DECOY_CYCLE, time_respecting=False)
    return out


# --------------------------------------------------------------------------
# 4.3.3 Mule fan-in / fan-out  — RAPHTORY ONLY
# --------------------------------------------------------------------------

def plant_mule_fan_in_out(cfg: Config, rng: np.random.Generator,
                          pop: Population, pool: AccountPool) -> Planted:
    """Money in from many, straight back out, inside a short holding period.

    The defining feature is the *elapsed time between the last inflow and the
    first outflow*. Current state contains the inflows and the outflows but not
    the interval between them, which is why this is unreachable from the hot path.
    """
    spec = cfg.generation.typologies.mule_fan_in_out
    hold = hours(spec.max_holding_hours)
    start, end = _span(cfg)
    out = Planted()

    for ring in range(cfg.profile.scaled(spec.rings.as_dict())):
        ring_id = f"MULE-{ring:04d}"
        n_sources = int(rng.integers(spec.sources["min"], spec.sources["max"] + 1))
        n_dests = int(rng.integers(1, 4))
        members = pool.take(n_sources + n_dests + 1)
        mule, sources, dests = members[0], members[1:1 + n_sources], members[1 + n_sources:]

        base = int(rng.integers(start, max(start + 1, end - hold - hours(18))))
        inflow_window = hours(12)
        total = 0.0
        last_in = base
        for source in sources:
            amount = float(rng.uniform(800, 9_000))
            t_in = base + int(rng.integers(0, inflow_window))
            last_in = max(last_in, t_in)
            total += amount
            out.add(t_in, source, mule, amount, Channel.P2P_TRANSFER,
                    _own_device(pop, source, rng), ring_id, Typology.MULE_FAN_IN_OUT)

        forwarded = total * rng.uniform(spec.forward_fraction["min"],
                                        spec.forward_fraction["max"])
        for i, dest in enumerate(dests):
            share = forwarded / len(dests)
            t_out = last_in + int(rng.integers(hours(1), hold))
            out.add(min(t_out, end - 1), mule, dest, share, Channel.P2P_TRANSFER,
                    _own_device(pop, mule, rng), ring_id, Typology.MULE_FAN_IN_OUT)

        out.label_accounts(members, pop, ring_id, Typology.MULE_FAN_IN_OUT)
    return out


# --------------------------------------------------------------------------
# 4.3.4 Device-sharing ring  — TIGERGRAPH ONLY
# --------------------------------------------------------------------------

def plant_device_sharing_ring(cfg: Config, rng: np.random.Generator,
                              pop: Population, pool: AccountPool) -> Planted:
    """Unrelated customers transacting from the same handful of devices.

    Pure current state, one or two hops, no time reasoning at all — the case
    where reaching for a temporal engine would be over-engineering. Ring size
    (5-8) sits deliberately above the organic household size (2-3), so the
    detector has a real threshold to find rather than a trivial separation.
    """
    spec = cfg.generation.typologies.device_sharing_ring
    start, end = _span(cfg)
    out = Planted()

    for ring in range(cfg.profile.scaled(spec.rings.as_dict())):
        ring_id = f"DEVRING-{ring:04d}"
        n_accounts = int(rng.integers(spec.accounts_per_ring["min"],
                                      spec.accounts_per_ring["max"] + 1))
        n_devices = int(rng.integers(spec.devices_per_ring["min"],
                                     spec.devices_per_ring["max"] + 1))
        members = pool.take(n_accounts, distinct_customers=True)
        shared = [int(d) for d in rng.choice(len(pop.devices), n_devices, replace=False)]

        for account in members:
            for _ in range(int(rng.integers(4, 9))):
                device = shared[int(rng.integers(0, len(shared)))]
                t = int(rng.integers(start, end))
                if rng.random() < 0.4:
                    merchant = int(rng.integers(0, len(pop.merchants)))
                    out.add(t, account, int(pop.merchant_account[merchant]),
                            float(rng.uniform(10, 400)), Channel.RETAIL_PURCHASE,
                            device, ring_id, Typology.DEVICE_SHARING_RING,
                            merchant=merchant)
                else:
                    peer = members[int(rng.integers(0, len(members)))]
                    if peer == account:
                        continue
                    out.add(t, account, peer, float(rng.uniform(50, 2_500)),
                            Channel.P2P_TRANSFER, device, ring_id,
                            Typology.DEVICE_SHARING_RING)

        out.label_accounts(members, pop, ring_id, Typology.DEVICE_SHARING_RING)
    return out


# --------------------------------------------------------------------------
# 4.3.5 Dormant reactivation burst  — RAPHTORY ONLY
# --------------------------------------------------------------------------

def plant_dormant_burst(cfg: Config, rng: np.random.Generator, pop: Population,
                        pool: AccountPool) -> Planted:
    """A long silence, then a dense burst of activity.

    Requires comparing two widely separated windows, so it emits a **quiet
    window** that background generation must respect. Without that suppression
    the "dormant" account keeps buying coffee through its own dormancy and the
    pattern never exists.
    """
    spec = cfg.generation.typologies.dormant_burst
    gap = days(cfg.profile.scaled(spec.dormancy_days_min.as_dict()))
    burst = hours(spec.burst_hours)
    start, end = _span(cfg)
    # Leave room before the gap for the prior activity that makes it a *gap*.
    prior = max(days(2), int((end - start) * 0.1))
    out = Planted()

    latest_gap_start = end - gap - burst - hours(2)
    if latest_gap_start <= start + prior:
        raise ValueError(
            f"profile {cfg.profile.name!r}: dormancy gap of {gap // MS_PER_DAY}d does "
            f"not fit in a {cfg.profile.span_days}d span with prior activity — "
            f"lower generation.typologies.dormant_burst.dormancy_days_min"
        )

    for ring in range(cfg.profile.scaled(spec.rings.as_dict())):
        ring_id = f"DORMANT-{ring:04d}"
        account = pool.take(1)[0]
        gap_start = int(rng.integers(start + prior, latest_gap_start))
        gap_end = gap_start + gap
        out.quiet_windows.append((account, gap_start, gap_end))

        n_txns = int(rng.integers(spec.burst_txn_count["min"],
                                  spec.burst_txn_count["max"] + 1))
        for _ in range(n_txns):
            t = gap_end + int(rng.integers(0, burst))
            if pop.contact_count[account]:
                peer = int(pop.contact_matrix[account,
                                              rng.integers(0, pop.contact_count[account])])
            else:
                peer = int(rng.choice(pop.personal_idx))
            out.add(min(t, end - 1), account, peer, float(rng.uniform(200, 7_500)),
                    Channel.P2P_TRANSFER, _own_device(pop, account, rng),
                    ring_id, Typology.DORMANT_BURST)

        out.label_accounts([account], pop, ring_id, Typology.DORMANT_BURST)
    return out


# --------------------------------------------------------------------------

PLANTERS = (
    plant_structuring,
    plant_circular_layering,
    plant_mule_fan_in_out,
    plant_device_sharing_ring,
    plant_dormant_burst,
)


def plant_all(cfg: Config, rng: np.random.Generator, pop: Population) -> Planted:
    """Run every planter against one exclusive account pool."""
    pool = AccountPool(pop, rng)
    combined = Planted()
    for planter in PLANTERS:
        combined.extend(planter(cfg, rng, pop, pool))
    return combined
