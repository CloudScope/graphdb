"""Background (legitimate) traffic.

The job here is not to produce plausible-looking rows — it is to produce a
background a detector cannot beat by accident. Three properties do that work:

* **Diurnal and weekly rhythm.** Volume peaks in the working day and drops
  overnight and at weekends, so "3am is suspicious" is not free money.
* **Recurring counterparties.** Salaries and bills arrive monthly from the same
  business accounts, which puts stable, high-degree, entirely legitimate hubs in
  the graph. Centrality alone therefore cannot separate fraud.
* **Round-number clustering.** A fifth of amounts snap to round values, so
  structuring's just-under-threshold amounts cannot be found by "the number
  looks deliberate".

Everything is generated as parallel numpy arrays rather than objects. The
``large`` profile is two million transactions; materializing that many
dataclasses before sorting would cost more memory than the analytics that follow.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ..config import Config
from ..models import Channel
from ..timeutil import MS_PER_DAY, MS_PER_HOUR, days, hours
from .population import Population

CHANNELS: tuple[str, ...] = (
    Channel.RETAIL_PURCHASE, Channel.P2P_TRANSFER,
    Channel.BILL_PAYMENT, Channel.SALARY_CREDIT,
)
CH_INDEX = {name: i for i, name in enumerate(CHANNELS)}


@dataclass(slots=True)
class Traffic:
    """A batch of transactions as columns. ``merchant`` is -1 where absent."""

    ts: np.ndarray
    src: np.ndarray
    dst: np.ndarray
    amount: np.ndarray
    channel: np.ndarray
    device: np.ndarray
    merchant: np.ndarray

    def __len__(self) -> int:
        return len(self.ts)

    @classmethod
    def empty(cls) -> Traffic:
        z = lambda dt: np.array([], dtype=dt)  # noqa: E731
        return cls(z(np.int64), z(np.int64), z(np.int64), z(np.float64),
                   z(np.int8), z(np.int64), z(np.int64))

    @classmethod
    def concat(cls, parts: list[Traffic]) -> Traffic:
        parts = [p for p in parts if len(p)]
        if not parts:
            return cls.empty()
        cat = lambda attr: np.concatenate([getattr(p, attr) for p in parts])  # noqa: E731
        return cls(cat("ts"), cat("src"), cat("dst"), cat("amount"),
                   cat("channel"), cat("device"), cat("merchant"))

    def take(self, mask: np.ndarray) -> Traffic:
        return Traffic(self.ts[mask], self.src[mask], self.dst[mask],
                       self.amount[mask], self.channel[mask], self.device[mask],
                       self.merchant[mask])


# --------------------------------------------------------------------------
# Timestamp sampling
# --------------------------------------------------------------------------

def _diurnal_bins(cfg: Config) -> tuple[np.ndarray, np.ndarray]:
    """Probability over every (day, hour) bin in the span, and each bin's start.

    Weekday-vs-weekend is decided from the bin's real UTC date, not a modulus, so
    the rhythm lines up with the timestamps that actually get written.
    """
    from ..timeutil import is_weekend

    p = cfg.profile
    bg = cfg.generation.background
    start = cfg.end_time_ms - days(p.span_days)

    hourly = np.asarray(bg.hourly_weights, dtype=np.float64)
    bin_start = start + np.arange(p.span_days * 24, dtype=np.int64) * MS_PER_HOUR
    weights = np.tile(hourly, p.span_days)

    day_start = start + np.arange(p.span_days, dtype=np.int64) * MS_PER_DAY
    weekend = np.array([is_weekend(int(d)) for d in day_start])
    weights = weights * np.repeat(
        np.where(weekend, bg.weekend_multiplier, 1.0), 24
    )
    return weights / weights.sum(), bin_start


def _sample_times(rng: np.random.Generator, probs: np.ndarray,
                  bin_start: np.ndarray, n: int) -> np.ndarray:
    """Draw ``n`` timestamps from the diurnal distribution."""
    if n <= 0:
        return np.array([], dtype=np.int64)
    bins = rng.choice(len(probs), n, p=probs)
    return bin_start[bins] + rng.integers(0, MS_PER_HOUR, n)


# --------------------------------------------------------------------------
# Amounts
# --------------------------------------------------------------------------

def _sample_amounts(cfg: Config, rng: np.random.Generator,
                    channel_idx: np.ndarray) -> np.ndarray:
    """Log-normal amounts, shifted per channel, with round-number clustering."""
    bg = cfg.generation.background
    n = len(channel_idx)
    if n == 0:
        return np.array([], dtype=np.float64)

    shifts = np.array([bg.amount_channel_mu_shift[c] for c in CHANNELS])
    mu = bg.amount_lognormal_mu + shifts[channel_idx]
    amounts = rng.lognormal(mu, bg.amount_lognormal_sigma, n)
    amounts = np.clip(amounts, bg.amount_min, bg.amount_max)

    ladder = np.asarray(bg.round_number_ladder, dtype=np.float64)
    snap = rng.random(n) < bg.round_number_fraction
    if snap.any():
        target = amounts[snap]
        nearest = ladder[np.abs(target[:, None] - ladder[None, :]).argmin(axis=1)]
        # Above the ladder's top rung, round to the nearest thousand instead of
        # collapsing every large amount onto one value.
        big = target > ladder[-1]
        nearest[big] = np.round(target[big] / 1000.0) * 1000.0
        amounts[snap] = nearest
    return np.round(amounts, 2)


# --------------------------------------------------------------------------
# Recurring traffic
# --------------------------------------------------------------------------

def _expand_schedule(src: np.ndarray, dst: np.ndarray, period_days: np.ndarray,
                     phase_ms: np.ndarray, start: int, end: int, hour: int,
                     rng: np.random.Generator) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Expand recurring relationships into individual dated events.

    Each relationship repeats every ``period_days`` with its own phase, so a
    counterparty pair recurs on a stable rhythm rather than at random — which is
    what puts legitimate high-degree hubs in the graph.
    """
    if not len(src):
        return (np.array([], dtype=np.int64),) * 3

    period_ms = period_days * MS_PER_DAY
    occurrences = ((end - start) // period_ms) + 1
    max_occ = int(occurrences.max())

    k = np.arange(max_occ, dtype=np.int64)[None, :]
    ts = start + (phase_ms % period_ms)[:, None] + k * period_ms[:, None] + hours(hour)
    live = k < occurrences[:, None]

    ts = ts[live]
    src_out = np.repeat(src, live.sum(axis=1))
    dst_out = np.repeat(dst, live.sum(axis=1))

    # Jitter: nobody's salary lands at exactly 09:00:00 every period.
    ts = ts + rng.integers(-hours(2), hours(3), len(ts))
    keep = (ts >= start) & (ts < end)
    return ts[keep], src_out[keep], dst_out[keep]


def _recurring(cfg: Config, rng: np.random.Generator, pop: Population,
               kind: str, target: int) -> Traffic:
    """Salary credits or bill payments, on each relationship's own cadence."""
    p = cfg.profile
    start = cfg.end_time_ms - days(p.span_days)
    end = cfg.end_time_ms

    if kind == "salary":
        employed = np.flatnonzero(pop.employer >= 0)
        if not len(employed):
            return Traffic.empty()
        rel_src = pop.employer[employed]
        rel_dst = employed
        period = pop.pay_period[employed]
        phase = (pop.payday[employed] - 1) * MS_PER_DAY
        hour = 9
    else:
        src_parts, dst_parts, per_parts, phase_parts = [], [], [], []
        for account in pop.personal_idx:
            billers = pop.billers[account]
            if not len(billers):
                continue
            src_parts.append(np.full(len(billers), account, dtype=np.int64))
            dst_parts.append(billers)
            per_parts.append(pop.bill_period[account])
            phase_parts.append((pop.bill_day[account] - 1) * MS_PER_DAY)
        if not src_parts:
            return Traffic.empty()
        rel_src = np.concatenate(src_parts)
        rel_dst = np.concatenate(dst_parts)
        period = np.concatenate(per_parts)
        phase = np.concatenate(phase_parts)
        hour = 11

    ts, src, dst = _expand_schedule(rel_src, rel_dst, period, phase,
                                    start, end, hour, rng)
    if target < len(ts):
        pick = np.sort(rng.choice(len(ts), target, replace=False))
        ts, src, dst = ts[pick], src[pick], dst[pick]

    channel_idx = np.full(
        len(ts), CH_INDEX[Channel.SALARY_CREDIT if kind == "salary" else Channel.BILL_PAYMENT],
        dtype=np.int8,
    )
    return Traffic(
        ts=ts, src=src, dst=dst,
        amount=_sample_amounts(cfg, rng, channel_idx),
        channel=channel_idx,
        device=pop.pick_devices(src, rng),
        merchant=np.full(len(ts), -1, dtype=np.int64),
    )


# --------------------------------------------------------------------------
# Sampled traffic
# --------------------------------------------------------------------------

def _retail(cfg: Config, rng: np.random.Generator, pop: Population,
            probs: np.ndarray, bin_start: np.ndarray, n: int) -> Traffic:
    if n <= 0:
        return Traffic.empty()
    src = rng.choice(pop.personal_idx, n)
    merchant = rng.integers(0, len(pop.merchants), n)
    channel_idx = np.full(n, CH_INDEX[Channel.RETAIL_PURCHASE], dtype=np.int8)
    return Traffic(
        ts=_sample_times(rng, probs, bin_start, n),
        src=src,
        dst=pop.merchant_account[merchant],
        amount=_sample_amounts(cfg, rng, channel_idx),
        channel=channel_idx,
        device=pop.pick_devices(src, rng),
        merchant=merchant,
    )


def _p2p(cfg: Config, rng: np.random.Generator, pop: Population,
         probs: np.ndarray, bin_start: np.ndarray, n: int) -> Traffic:
    """Peer transfers, drawn from each account's contact list.

    Uniform counterparties would leave the graph with no community structure,
    making Louvain's discovery of the planted rings a tautology.
    """
    if n <= 0:
        return Traffic.empty()
    eligible = pop.personal_idx[pop.contact_count[pop.personal_idx] > 0]
    src = rng.choice(eligible, n)
    slot = (rng.random(n) * pop.contact_count[src]).astype(np.int64)
    dst = pop.contact_matrix[src, slot]
    channel_idx = np.full(n, CH_INDEX[Channel.P2P_TRANSFER], dtype=np.int8)
    return Traffic(
        ts=_sample_times(rng, probs, bin_start, n),
        src=src,
        dst=dst,
        amount=_sample_amounts(cfg, rng, channel_idx),
        channel=channel_idx,
        device=pop.pick_devices(src, rng),
        merchant=np.full(n, -1, dtype=np.int64),
    )


# --------------------------------------------------------------------------
# Organic near-patterns
# --------------------------------------------------------------------------

def _settle_up_loops(cfg: Config, rng: np.random.Generator,
                     pop: Population) -> Traffic:
    """Housemates settling up: genuine closed 3-cycles, legitimately.

    Half are generated in time order, which makes them **time-respecting cycles
    that are not fraud**. Without these the temporal cycle detector would have no
    organic false positives and its precision would be meaningless.
    """
    if not pop.settle_up_groups:
        return Traffic.empty()
    start = cfg.end_time_ms - days(cfg.profile.span_days)
    span = days(cfg.profile.span_days)

    ts, src, dst = [], [], []
    for group in pop.settle_up_groups:
        base = start + int(rng.integers(0, max(1, span - days(6))))
        offsets = np.sort(rng.integers(0, days(5), 3))
        if rng.random() < 0.5:                    # half are out of time order
            offsets = rng.permutation(offsets)
        for hop, (a, b) in enumerate(zip(group, group[1:] + group[:1])):
            ts.append(base + int(offsets[hop]))
            src.append(a)
            dst.append(b)

    ts_arr = np.array(ts, dtype=np.int64)
    src_arr = np.array(src, dtype=np.int64)
    channel_idx = np.full(len(ts_arr), CH_INDEX[Channel.P2P_TRANSFER], dtype=np.int8)
    return Traffic(
        ts=ts_arr, src=src_arr, dst=np.array(dst, dtype=np.int64),
        amount=np.round(rng.uniform(15, 180, len(ts_arr)), 2),
        channel=channel_idx,
        device=pop.pick_devices(src_arr, rng),
        merchant=np.full(len(ts_arr), -1, dtype=np.int64),
    )


def _seasonal_returns(cfg: Config, rng: np.random.Generator,
                      pop: Population) -> tuple[Traffic, list[tuple[int, int, int]]]:
    """Legitimate dormancy: a quiet stretch, then a modest return.

    The burst is deliberately sized below the fraud threshold (§4.3.5) so the two
    populations overlap near the boundary rather than separating cleanly.
    """
    near = cfg.generation.background.organic_near_patterns
    if not pop.seasonal_dormant:
        return Traffic.empty(), []

    p = cfg.profile
    start = cfg.end_time_ms - days(p.span_days)
    gap = days(max(2, int(p.span_days * 0.4)))
    lo, hi = near.seasonal_burst_txns["min"], near.seasonal_burst_txns["max"]

    # The window must end early enough that the return burst still fits inside
    # the span. Otherwise the burst gets clipped back to the end of the span,
    # which lands it inside its own quiet window and deletes it.
    burst_room = days(3)
    latest_start = (cfg.end_time_ms - gap - burst_room) - start
    if latest_start <= days(1):
        return Traffic.empty(), []

    ts, src, dst, quiet = [], [], [], []
    for account in pop.seasonal_dormant:
        if pop.contact_count[account] == 0:
            continue
        quiet_start = start + int(rng.integers(days(1), latest_start))
        quiet_end = quiet_start + gap
        quiet.append((account, quiet_start, quiet_end))
        for _ in range(int(rng.integers(lo, hi + 1))):
            ts.append(quiet_end + int(rng.integers(0, burst_room)))
            src.append(account)
            dst.append(int(pop.contact_matrix[account,
                                              rng.integers(0, pop.contact_count[account])]))

    if not ts:
        return Traffic.empty(), quiet
    ts_arr = np.clip(np.array(ts, dtype=np.int64), start, cfg.end_time_ms - 1)
    src_arr = np.array(src, dtype=np.int64)
    channel_idx = np.full(len(ts_arr), CH_INDEX[Channel.P2P_TRANSFER], dtype=np.int8)
    return Traffic(
        ts=ts_arr, src=src_arr, dst=np.array(dst, dtype=np.int64),
        amount=_sample_amounts(cfg, rng, channel_idx),
        channel=channel_idx,
        device=pop.pick_devices(src_arr, rng),
        merchant=np.full(len(ts_arr), -1, dtype=np.int64),
    ), quiet


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------

def generate_background(cfg: Config, rng: np.random.Generator, pop: Population,
                        budget: int) -> tuple[Traffic, list[tuple[int, int, int]]]:
    """Generate ``budget`` legitimate transactions.

    Returns the traffic plus the quiet windows its own organic dormancy needs,
    for the caller to enforce over the merged log alongside the planted ones.
    """
    if budget <= 0:
        return Traffic.empty(), []

    mix = cfg.generation.background.mix
    probs, bin_start = _diurnal_bins(cfg)

    organic_loops = _settle_up_loops(cfg, rng, pop)
    organic_returns, organic_quiet = _seasonal_returns(cfg, rng, pop)
    organic = Traffic.concat([organic_loops, organic_returns])

    remaining = max(0, budget - len(organic))
    n_salary = int(remaining * mix[Channel.SALARY_CREDIT])
    n_bill = int(remaining * mix[Channel.BILL_PAYMENT])
    n_retail = int(remaining * mix[Channel.RETAIL_PURCHASE])
    n_p2p = remaining - n_salary - n_bill - n_retail

    salary = _recurring(cfg, rng, pop, "salary", n_salary)
    bills = _recurring(cfg, rng, pop, "bill", n_bill)
    # Recurring traffic is capped by how many account-months actually exist; any
    # shortfall goes to P2P rather than silently shrinking the dataset.
    shortfall = (n_salary - len(salary)) + (n_bill - len(bills))
    retail = _retail(cfg, rng, pop, probs, bin_start, n_retail)
    p2p = _p2p(cfg, rng, pop, probs, bin_start, n_p2p + shortfall)

    traffic = Traffic.concat([salary, bills, retail, p2p, organic])
    # Quiet windows are NOT enforced here. Planted traffic from other rings can
    # also land inside a window, so enforcement has to happen once over the
    # merged log — see generate.apply_quiet_windows.
    return traffic, organic_quiet


def quiet_window_mask(traffic: Traffic, windows: list[tuple[int, int, int]],
                      n_accounts: int) -> np.ndarray:
    """Boolean keep-mask for transactions outside every account's quiet window.

    Returns a mask rather than filtered traffic so the caller can apply the same
    selection to its parallel arrays — the merged log carries an ``origin`` index
    alongside it that must stay aligned.

    One vectorized pass: each account has at most one window, so the bounds go
    into per-account lookup arrays and the test becomes two comparisons over the
    whole batch instead of a loop over windows.

    Bursts are safe by construction — every burst starts at or after its own
    window's end — so no exemption is needed. Applying this to the *merged* log
    rather than to background alone is the point: a dormant account can otherwise
    receive a planted transaction from an unrelated ring and lose its gap.
    """
    if not windows or not len(traffic):
        return np.ones(len(traffic), dtype=bool)

    lo = np.full(n_accounts, np.iinfo(np.int64).max, dtype=np.int64)
    hi = np.full(n_accounts, np.iinfo(np.int64).min, dtype=np.int64)
    for account, start, end in windows:
        lo[account] = min(lo[account], start)
        hi[account] = max(hi[account], end)

    inside = lambda who: (traffic.ts >= lo[who]) & (traffic.ts < hi[who])  # noqa: E731
    return ~(inside(traffic.src) | inside(traffic.dst))
