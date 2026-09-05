"""Hot-path screening: five checks, one decision, under a latency budget.

**The one invariant that makes any of this valid: no query may see past
``txn.ts``.** A screening decision is made at authorization time, so every check
is bounded above by the transaction's own timestamp. Letting a query see the
whole table would leak the future into the decision and produce an evaluation
that looks excellent and means nothing. Every SQL statement below carries that
bound, and there is a test for it.

The checks are deliberately shallow — one or two hops, current state only. That
is not a limitation being worked around; it is the half of the problem a system
of record is *for* (DESIGN.md §5). The patterns that need event ordering are
Raphtory's job and are absent here on purpose.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ..config import Config
from ..models import Decision, Engine, RiskSignal, ScreenResult, SignalKind, Txn
from ..timeutil import hours

if TYPE_CHECKING:
    from .local import LocalStore


def _clip(value: float) -> float:
    return max(0.0, min(1.0, value))


# --------------------------------------------------------------------------
# Signal construction — shared by BOTH stores
# --------------------------------------------------------------------------
# Each store measures the graph its own way (SQL joins vs. GSQL accumulators) and
# then hands the raw numbers to these. The strength formulas and thresholds live
# here once, so a difference between the engines can only be a difference in
# *execution*, never in what counts as suspicious. Duplicating this math in the
# GSQL side would make every comparison in the evaluation meaningless.


def device_signal(txn: Txn, hp, engine: str, customers: int, accounts: int,
                  peak_peer_risk: float) -> RiskSignal | None:
    """Distinct customers on one device, lifted by an already-risky peer."""
    if not customers:
        return None
    ok, alarm = hp.device_share_customers_ok, hp.device_share_customers_alarm
    strength = _clip((customers - ok) / (alarm - ok))
    if peak_peer_risk >= hp.shared_device_risk_floor:
        strength = _clip(max(strength, 0.5) + 0.25)
    if strength <= 0.0:
        return None
    return RiskSignal(
        account_id=txn.src_account, kind=SignalKind.SHARED_DEVICE,
        strength=strength, engine=engine,
        evidence={"device_id": txn.device_id, "distinct_customers": customers,
                  "distinct_accounts": accounts, "peak_peer_risk": peak_peer_risk})


def counterparty_signal(txn: Txn, hp, engine: str, peak: float, mean: float,
                        peers: int, ring: str | None,
                        extra: dict | None = None) -> RiskSignal | None:
    """Risk within two transfer hops — where the feedback loop is consumed."""
    if peak <= 0 or not peers:
        return None
    evidence = {"peak_peer_risk": peak, "mean_peer_risk": mean,
                "risky_peers": peers, "peer_ring": ring}
    evidence.update(extra or {})
    return RiskSignal(
        account_id=txn.src_account, kind=SignalKind.COUNTERPARTY_RISK,
        strength=_clip(peak / 100.0), engine=engine, evidence=evidence)


def velocity_signal(txn: Txn, hp, engine: str, n_1h: int, n_24h: int,
                    amount_24h: float) -> RiskSignal | None:
    ratios = ((n_1h or 0) / hp.velocity_1h_count,
              (n_24h or 0) / hp.velocity_24h_count,
              (amount_24h or 0) / hp.velocity_24h_amount)
    worst = max(ratios)
    strength = _clip(worst - 1.0) if worst > 1.0 else 0.0
    if strength <= 0.0:
        return None
    return RiskSignal(
        account_id=txn.src_account, kind=SignalKind.VELOCITY,
        strength=strength, engine=engine,
        evidence={"txns_1h": n_1h, "txns_24h": n_24h, "amount_24h": amount_24h})


def fan_out_signal(txn: Txn, hp, engine: str, recipients: int,
                   near_threshold: int) -> RiskSignal | None:
    worst = max((recipients or 0) / hp.fan_out_distinct_recipients,
                (near_threshold or 0) / hp.fan_out_near_threshold_count)
    strength = _clip(worst - 1.0) if worst > 1.0 else 0.0
    if strength <= 0.0:
        return None
    return RiskSignal(
        account_id=txn.src_account, kind=SignalKind.FAN_OUT_BURST,
        strength=strength, engine=engine,
        evidence={"distinct_recipients": recipients,
                  "near_threshold_txns": near_threshold,
                  "window_hours": hp.fan_out_window_hours})


def near_threshold_signal(txn: Txn, hp, engine: str,
                          threshold: float) -> RiskSignal | None:
    """This payment alone, sitting just under the reporting line."""
    floor = threshold * (1 - hp.near_threshold_band)
    if not floor <= txn.amount < threshold:
        return None
    return RiskSignal(
        account_id=txn.src_account, kind=SignalKind.NEAR_THRESHOLD,
        strength=_clip((txn.amount - floor) / (threshold - floor)),
        engine=engine,
        evidence={"amount": txn.amount, "threshold": threshold})


# --------------------------------------------------------------------------
# The five checks, against SQLite
# --------------------------------------------------------------------------

def _shared_device(store: LocalStore, txn: Txn, hp) -> RiskSignal | None:
    """How many distinct *customers* have used this device, and are any risky?

    Counting customers rather than accounts is the whole trick. One person with
    three accounts on one phone is unremarkable; six unrelated customers on one
    phone is a ring. Households sit at 2-3 and score zero by construction, so the
    check has to find a real threshold rather than a trivial separation.
    """
    rows = store.query(
        "SELECT COUNT(DISTINCT a.customer_id) AS customers, "
        "       COUNT(DISTINCT u.account_id)  AS accounts, "
        "       MAX(a.risk_score)             AS peak_risk "
        "FROM used_device u JOIN account a ON a.account_id = u.account_id "
        "WHERE u.device_id = ? AND u.first_seen <= ?",
        (txn.device_id, txn.ts))
    if not rows or not rows[0]["customers"]:
        return None

    return device_signal(txn, hp, Engine.LOCAL,
                         customers=rows[0]["customers"],
                         accounts=rows[0]["accounts"],
                         peak_peer_risk=rows[0]["peak_risk"] or 0.0)


def _counterparty_risk(store: LocalStore, txn: Txn, hp) -> RiskSignal | None:
    """Risk carried by accounts within two transfer hops.

    **This is where the feedback loop is consumed.** ``account.risk_score`` is
    written only by Raphtory (DESIGN.md §7); a ring found by temporal analytics
    on Monday changes this transaction's decision on Tuesday, with no analytics
    running on the authorization path.

    Written as two bounded lookups rather than one recursive CTE. The CTE was
    correct and unusable: SQLite materialized the whole two-hop expansion for
    every authorization, costing 30ms against 0.08ms for every other check. Both
    hops are capped, because an authorization path has to do bounded work per
    request no matter how much history an account has accumulated — a real
    system draws this line too.
    """
    hop1 = [row[0] for row in store.query(
        "SELECT dst_account FROM txn WHERE src_account = ? AND ts <= ? "
        "UNION SELECT src_account FROM txn WHERE dst_account = ? AND ts <= ? "
        "LIMIT ?",
        (txn.src_account, txn.ts, txn.src_account, txn.ts,
         hp.counterparty_max_fanout))]
    if not hop1:
        return None

    # The second hop is never materialized. Only a handful of accounts carry any
    # risk at all, so the question "is a risky account within two hops" is asked
    # from the risky side: join straight onto `account` and let the risk filter
    # do the selecting, instead of enumerating a few hundred neighbours and then
    # discovering almost none of them matter.
    # Both hops, not just the second. An earlier optimization joined only from
    # hop-1's outbound edges, which silently dropped the *direct* counterparties
    # — the most important case of all, and the one GSQL was checking. The two
    # stores disagreed on 475 of 1,502 scores until this was fixed.
    marks = ",".join("?" * len(hop1))
    row = store.query(
        f"SELECT MAX(a.risk_score) AS peak, AVG(a.risk_score) AS mean, "
        f"COUNT(*) AS peers, MAX(a.ring_id) AS ring "
        f"FROM account a WHERE a.risk_score > 0 AND ("
        f"  a.account_id IN ({marks}) OR a.account_id IN ("
        f"    SELECT t.dst_account FROM txn t "
        f"    WHERE t.src_account IN ({marks}) AND t.ts <= ?))",
        (*hop1, *hop1, txn.ts))[0]

    return counterparty_signal(txn, hp, Engine.LOCAL,
                               peak=row["peak"] or 0.0, mean=row["mean"] or 0.0,
                               peers=row["peers"] or 0, ring=row["ring"],
                               extra={"hop1_size": len(hop1)})


def _velocity(store: LocalStore, txn: Txn, hp) -> RiskSignal | None:
    """Outbound count and value in the trailing hour and day."""
    row = store.query(
        "SELECT "
        "  SUM(CASE WHEN ts > ? THEN 1 ELSE 0 END)      AS n_1h, "
        "  COUNT(*)                                      AS n_24h, "
        "  COALESCE(SUM(amount), 0)                      AS sum_24h "
        "FROM txn WHERE src_account = ? AND ts > ? AND ts <= ?",
        (txn.ts - hours(1), txn.src_account, txn.ts - hours(24), txn.ts))[0]

    return velocity_signal(txn, hp, Engine.LOCAL, n_1h=row["n_1h"] or 0,
                           n_24h=row["n_24h"] or 0, amount_24h=row["sum_24h"] or 0)


def _fan_out_burst(store: LocalStore, txn: Txn, hp) -> RiskSignal | None:
    """Distinct recipients, and how many payments hug the reporting threshold."""
    threshold = store.cfg.generation.reporting_threshold
    floor = threshold * (1 - hp.near_threshold_band)
    row = store.query(
        "SELECT COUNT(DISTINCT dst_account) AS recipients, "
        "       SUM(CASE WHEN amount >= ? AND amount < ? THEN 1 ELSE 0 END) AS near "
        "FROM txn WHERE src_account = ? AND ts > ? AND ts <= ?",
        (floor, threshold, txn.src_account,
         txn.ts - hours(hp.fan_out_window_hours), txn.ts))[0]

    return fan_out_signal(txn, hp, Engine.LOCAL,
                          recipients=row["recipients"] or 0,
                          near_threshold=row["near"] or 0)


def _near_threshold(store: LocalStore, txn: Txn, hp) -> RiskSignal | None:
    """This payment alone, sitting just under the reporting line."""
    return near_threshold_signal(txn, hp, Engine.LOCAL,
                                 store.cfg.generation.reporting_threshold)


CHECKS = (_shared_device, _counterparty_risk, _velocity, _fan_out_burst, _near_threshold)


# --------------------------------------------------------------------------
# Decision
# --------------------------------------------------------------------------

def decide(cfg: Config, txn: Txn, signals: list[RiskSignal],
           engine: str, latency_ms: float) -> ScreenResult:
    """Map signals to a score and a decision. Shared by both stores.

    A transparent weighted sum, capped: the same choice as the warm path, for
    the same reason — attributing detection capability to an engine requires a
    scorer whose output can be traced back to its inputs.
    """
    weights = cfg.detection.hot_path.hot_weights.as_dict()
    score = min(sum(weights.get(str(s.kind), 0.0) * s.strength for s in signals),
                cfg.detection.scoring.max_score)

    bands = cfg.detection.decisions
    if score >= bands.block_at:
        decision = Decision.BLOCK
    elif score >= bands.review_at:
        decision = Decision.REVIEW
    else:
        decision = Decision.ALLOW

    return ScreenResult(
        txn_id=txn.txn_id, account_id=txn.src_account, decision=decision,
        score=round(score, 3), signals=signals,
        latency_ms=round(latency_ms, 3), engine=engine)


def screen_local(store: LocalStore, txn: Txn) -> ScreenResult:
    """Run all five checks against SQLite and decide."""
    started = time.perf_counter()
    hp = store.cfg.detection.hot_path
    signals = [signal for check in CHECKS
               if (signal := check(store, txn, hp)) is not None]
    elapsed_ms = (time.perf_counter() - started) * 1000
    return decide(store.cfg, txn, signals, Engine.LOCAL, elapsed_ms)
