"""Structural checks on a generated dataset.

Every planted typology makes a claim that later phases depend on. If circular
layering is not actually time-respecting, or the decoys accidentally are, or a
dormancy gap still has traffic in it, then the evaluation in Phase 7 measures
nothing — and it would measure nothing *quietly*, reporting plausible numbers
built on a broken control.

So the claims are checked against the written files rather than trusted from the
code that wrote them. This runs on ``generate --verify`` and is cheap enough to
leave on.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field

from ..config import Config
from ..models import Typology
from ..timeutil import MS_PER_DAY, MS_PER_HOUR


@dataclass(slots=True)
class Check:
    name: str
    passed: bool
    detail: str


@dataclass(slots=True)
class VerifyReport:
    checks: list[Check] = field(default_factory=list)

    def add(self, name: str, passed: bool, detail: str) -> None:
        self.checks.append(Check(name, passed, detail))

    @property
    def ok(self) -> bool:
        return all(c.passed for c in self.checks)

    def render(self) -> str:
        lines = []
        for check in self.checks:
            lines.append(f"  [{'PASS' if check.passed else 'FAIL'}] {check.name}: {check.detail}")
        return "\n".join(lines)


def _has_increasing_rotation(times: list[int]) -> bool:
    """True if some rotation of this cycle's hop times is strictly increasing.

    Equivalent to the cycle having exactly one cyclic descent — the same
    criterion the decoy generator inverts. Computed independently here on
    purpose: a bug shared between generator and checker would hide itself.
    """
    n = len(times)
    descents = sum(1 for i in range(n) if times[i] >= times[(i + 1) % n])
    return descents == 1


def verify(cfg: Config) -> VerifyReport:
    paths = cfg.paths
    report = VerifyReport()

    labels = [json.loads(line) for line in paths.labels.open()]
    entities = json.loads(paths.entities.read_text())
    owner = {a["account_id"]: a["customer_id"] for a in entities["accounts"]}

    ring_typology: dict[str, str] = {}
    ring_accounts: dict[str, set[str]] = defaultdict(set)
    txn_ring: dict[str, str] = {}
    for label in labels:
        ring_typology[label["ring_id"]] = label["typology"]
        if label.get("txn_id"):
            txn_ring[label["txn_id"]] = label["ring_id"]
        if label.get("account_id"):
            ring_accounts[label["ring_id"]].add(label["account_id"])

    # Accounts whose full timeline is needed for the dormancy check. Everything
    # else is derived from planted transactions alone, so the event log is
    # streamed once and only the relevant rows are retained — the ``large``
    # profile is two million events and materializing them all here would cost
    # more memory than the analytics these checks exist to protect.
    dormant_accounts: set[str] = set()
    for ring, typology in ring_typology.items():
        if typology == Typology.DORMANT_BURST:
            dormant_accounts |= ring_accounts[ring]

    ring_txns: dict[str, list[dict]] = defaultdict(list)
    account_times: dict[str, list[int]] = defaultdict(list)
    n_events = 0
    ordered = True
    dense = True
    previous_ts = None
    for line in paths.events.open():
        event = json.loads(line)
        if previous_ts is not None and event["ts"] < previous_ts:
            ordered = False
        previous_ts = event["ts"]
        if event["txn_id"] != f"TXN-{n_events:09d}":
            dense = False
        n_events += 1

        ring = txn_ring.get(event["txn_id"])
        if ring is not None:
            ring_txns[ring].append(event)
        for side in ("src_account", "dst_account"):
            if event[side] in dormant_accounts:
                account_times[event[side]].append(event["ts"])

    # -- 1. every typology is present ------------------------------------
    present = set(ring_typology.values())
    expected = {t.value for t in Typology}
    missing = expected - present
    report.add("all typologies present",
               not missing,
               f"{len(present)}/{len(expected)} present"
               + (f"; missing {sorted(missing)}" if missing else ""))

    # -- 2. events are ordered and ids are dense --------------------------
    report.add("event log ordered, ids dense", ordered and dense,
               f"{n_events} events, ascending={ordered}, ids sequential={dense}")

    # -- 3. real cycles time-respecting, decoys not -----------------------
    # The measurement Phase 5 turns into a precision comparison. If this check
    # fails, that comparison is meaningless.
    real_ok = real_n = decoy_bad = decoy_n = 0
    for ring, typology in ring_typology.items():
        if typology not in (Typology.CIRCULAR_LAYERING, Typology.DECOY_CYCLE):
            continue
        times = [t["ts"] for t in sorted(ring_txns[ring], key=lambda x: x["txn_id"])]
        # Rebuild hop order by following the chain, not by id order.
        edges = {t["src_account"]: t for t in ring_txns[ring]}
        start = next(iter(edges))
        walk, node = [], start
        for _ in range(len(edges)):
            hop = edges.get(node)
            if hop is None:
                break
            walk.append(hop["ts"])
            node = hop["dst_account"]
        if len(walk) != len(edges):
            continue
        if typology == Typology.CIRCULAR_LAYERING:
            real_n += 1
            real_ok += _has_increasing_rotation(walk)
        else:
            decoy_n += 1
            decoy_bad += _has_increasing_rotation(walk)

    report.add("circular_layering rings are time-respecting",
               real_n > 0 and real_ok == real_n,
               f"{real_ok}/{real_n} rings have an increasing rotation")
    report.add("decoy cycles are NOT time-respecting",
               decoy_n > 0 and decoy_bad == 0,
               f"{decoy_bad}/{decoy_n} decoys have an increasing rotation (must be 0)")

    # -- 4. dormancy gaps are actually empty ------------------------------
    gap_days = cfg.profile.scaled(
        cfg.generation.typologies.dormant_burst.dormancy_days_min.as_dict())
    by_account = account_times

    dormant_rings = [r for r, t in ring_typology.items() if t == Typology.DORMANT_BURST]
    gaps_ok = 0
    observed_gaps = []
    for ring in dormant_rings:
        account = next(iter(ring_accounts[ring]))
        times = sorted(by_account[account])
        biggest = max((b - a for a, b in zip(times, times[1:])), default=0)
        observed_gaps.append(biggest / MS_PER_DAY)
        gaps_ok += biggest >= gap_days * MS_PER_DAY
    report.add("dormant accounts have a real gap",
               bool(dormant_rings) and gaps_ok == len(dormant_rings),
               f"{gaps_ok}/{len(dormant_rings)} accounts show a >={gap_days}d silence "
               f"(observed min {min(observed_gaps, default=0):.1f}d)")

    # -- 5. structuring sits under the reporting threshold ----------------
    threshold = cfg.generation.reporting_threshold
    struct = [t for r, t_list in ring_txns.items()
              if ring_typology[r] == Typology.STRUCTURING for t in t_list]
    under = [t for t in struct if t["amount"] < threshold]
    band = cfg.generation.typologies.structuring.under_threshold_pct
    close = [t for t in struct if threshold * (1 - band["max"]) <= t["amount"] < threshold]
    report.add("structuring amounts sit below the threshold",
               len(under) == len(struct) and len(close) > 0,
               f"{len(under)}/{len(struct)} under {threshold}; "
               f"{len(close)} within {band['max']:.0%} of the line")

    # -- 6. device rings share devices across distinct customers ----------
    dev_rings = [r for r, t in ring_typology.items() if t == Typology.DEVICE_SHARING_RING]
    shared_ok = distinct_ok = 0
    for ring in dev_rings:
        devices = {t["device_id"] for t in ring_txns[ring]}
        customers = {owner[a] for a in ring_accounts[ring]}
        shared_ok += len(devices) <= cfg.generation.typologies.device_sharing_ring.devices_per_ring["max"]
        distinct_ok += len(customers) == len(ring_accounts[ring])
    report.add("device rings: few devices, distinct customers",
               bool(dev_rings) and shared_ok == len(dev_rings) == distinct_ok,
               f"{shared_ok}/{len(dev_rings)} within device cap, "
               f"{distinct_ok}/{len(dev_rings)} all-distinct customers")

    # -- 7. mule holding time inside the window ---------------------------
    hold_h = cfg.generation.typologies.mule_fan_in_out.max_holding_hours
    mule_rings = [r for r, t in ring_typology.items() if t == Typology.MULE_FAN_IN_OUT]
    holds_ok, holds = 0, []
    for ring in mule_rings:
        txns = ring_txns[ring]
        if not txns:
            continue
        counts = defaultdict(int)
        for t in txns:
            counts[t["dst_account"]] += 1
        mule = max(counts, key=counts.get)
        ins = [t["ts"] for t in txns if t["dst_account"] == mule]
        outs = [t["ts"] for t in txns if t["src_account"] == mule]
        if not ins or not outs:
            continue
        hold = (max(outs) - max(ins)) / MS_PER_HOUR
        holds.append(hold)
        holds_ok += hold <= hold_h
    report.add("mule holding time within the window",
               bool(mule_rings) and holds_ok == len(holds),
               f"{holds_ok}/{len(holds)} rings forward within {hold_h}h "
               f"(max observed {max(holds, default=0):.1f}h)")

    # -- 8. no account carries contradictory labels -----------------------
    # An account in both a fraud ring and an organic near-pattern would have two
    # ground-truth typologies, silently corrupting every recall figure computed
    # from this file.
    account_typologies: dict[str, set[str]] = defaultdict(set)
    for label in labels:
        if label.get("account_id"):
            account_typologies[label["account_id"]].add(label["typology"])
    conflicted = {a: sorted(ts) for a, ts in account_typologies.items() if len(ts) > 1}
    report.add("no account has contradictory labels",
               not conflicted,
               f"{len(account_typologies)} labelled accounts, {len(conflicted)} conflicting"
               + (f"; e.g. {list(conflicted.items())[:2]}" if conflicted else ""))

    # -- 9. every planted ring survived into the log ----------------------
    empty = [r for r, ty in ring_typology.items()
             if ty != Typology.LEGITIMATE and not ring_txns[r]]
    report.add("every planted ring has transactions",
               not empty,
               f"{len(ring_typology) - len(empty)}/{len(ring_typology)} rings have "
               f"transactions" + (f"; empty: {empty[:3]}" if empty else ""))

    # -- 10. organic households stay below fraud ring size ----------------
    fam_sizes = [len(ring_accounts[r]) for r, t in ring_typology.items()
                 if t == Typology.LEGITIMATE and r.startswith("FAMILY-")]
    ring_sizes = [len(ring_accounts[r]) for r in dev_rings]
    separated = bool(fam_sizes) and bool(ring_sizes) and max(fam_sizes) < min(ring_sizes)
    report.add("organic households smaller than fraud rings",
               separated,
               f"households {min(fam_sizes, default=0)}-{max(fam_sizes, default=0)} accounts, "
               f"device rings {min(ring_sizes, default=0)}-{max(ring_sizes, default=0)}")

    return report
