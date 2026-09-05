"""Dataset assembly: population + planted typologies + background, written out.

Order matters and is not arbitrary. Typologies are planted **first**, because
they reserve accounts exclusively and declare the quiet windows that background
generation has to respect. Background then fills the remaining transaction budget
around them. Generating background first would either overwrite the reserved
accounts' behaviour or fill in the dormancy gaps that §4.3.5 depends on.

Output is three files plus a manifest:

* ``events.jsonl``   — the append-only log, ascending by timestamp. The system of
  record (DESIGN.md §2.2); both engines are downstream consumers of this file.
* ``entities.json``  — the static population.
* ``labels.jsonl``   — ground truth. **Read only by the evaluation harness.**
* ``manifest.json``  — counts and per-file SHA-256, so determinism is checkable
  rather than asserted.

The manifest deliberately carries no wall-clock timestamp: two runs with the same
seed must be byte-identical, and a generation time would break that for no gain.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..config import Config
from ..models import Label, Typology
from ..timeutil import days, iso
from .background import CHANNELS, Traffic, generate_background, quiet_window_mask
from .population import Population, build_population
from .typologies import Planted, plant_all


@dataclass(slots=True)
class GenerationResult:
    profile: str
    seed: int
    counts: dict[str, int]
    typology_counts: dict[str, int]
    files: dict[str, str]
    hashes: dict[str, str]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _organic_labels(pop: Population) -> list[Label]:
    """Label the legitimate lookalikes.

    These are **negatives**. They are recorded so the report can separate "a false
    positive" from "a false positive we deliberately planted to be hard" — which
    is the difference between a detector that is noisy and one that is being
    tested properly. Any harness reading ``labels.jsonl`` must treat
    ``typology == "legitimate"`` as not-fraud.
    """
    labels: list[Label] = []
    for i, group in enumerate(pop.family_groups):
        for account in group:
            labels.append(Label(ring_id=f"FAMILY-{i:04d}", typology=Typology.LEGITIMATE,
                                account_id=pop.account_id(account)))
    for i, group in enumerate(pop.settle_up_groups):
        for account in group:
            labels.append(Label(ring_id=f"SETTLEUP-{i:04d}", typology=Typology.LEGITIMATE,
                                account_id=pop.account_id(account)))
    for i, account in enumerate(pop.seasonal_dormant):
        labels.append(Label(ring_id=f"SEASONAL-{i:04d}", typology=Typology.LEGITIMATE,
                            account_id=pop.account_id(account)))
    return labels


def generate(cfg: Config, seed: int, force: bool = False,
             progress: bool = True) -> GenerationResult:
    """Generate and write the full dataset for ``cfg``'s profile."""
    paths = cfg.paths
    paths.ensure()
    if paths.events.exists() and not force:
        raise FileExistsError(
            f"{paths.events} already exists — pass --force to overwrite"
        )

    rng = np.random.default_rng(seed)
    say = print if progress else (lambda *a, **k: None)

    say(f"Generating profile '{cfg.profile.name}' (seed {seed})")
    pop = build_population(cfg, rng)
    say(f"  population : {len(pop.customers)} customers, {len(pop.accounts)} accounts, "
        f"{len(pop.devices)} devices, {len(pop.merchants)} merchants")

    planted = plant_all(cfg, rng, pop)
    say(f"  typologies : {len(planted)} transactions across "
        f"{len(set(planted.ring_id))} rings")

    budget = cfg.profile.transactions - len(planted)
    background, organic_quiet = generate_background(cfg, rng, pop, budget)
    say(f"  background : {len(background)} transactions")

    planted_traffic = planted.to_traffic()
    traffic = Traffic.concat([background, planted_traffic])
    # -1 marks a background row; anything else indexes into ``planted``'s lists,
    # so 2M legitimate rows never materialize a ring-id string.
    origin = np.concatenate([
        np.full(len(background), -1, dtype=np.int64),
        np.arange(len(planted_traffic), dtype=np.int64),
    ])

    # Enforced once, over the merged log. Doing this inside background generation
    # would leave planted traffic from other rings free to fill a dormancy gap —
    # which is exactly the bug the structural checker caught.
    before = len(traffic)
    windows = planted.quiet_windows + organic_quiet
    keep = quiet_window_mask(traffic, windows, len(pop.accounts))
    traffic, origin = traffic.take(keep), origin[keep]
    say(f"  quiet gaps : dropped {before - len(traffic)} transactions inside "
        f"{len(windows)} dormancy windows")

    order = np.argsort(traffic.ts, kind="stable")
    say(f"  merged     : {len(order)} transactions, "
        f"{iso(int(traffic.ts[order[0]]))} .. {iso(int(traffic.ts[order[-1]]))}")

    txn_labels = _write_events(cfg, pop, traffic, origin, order, planted)
    _write_entities(cfg, pop)

    labels = planted.account_labels + txn_labels + _organic_labels(pop)
    _write_labels(paths.labels, labels)

    typology_counts: dict[str, int] = {}
    for name in planted.typology:
        typology_counts[str(name)] = typology_counts.get(str(name), 0) + 1

    counts = {
        "customers": len(pop.customers),
        "accounts": len(pop.accounts),
        "devices": len(pop.devices),
        "merchants": len(pop.merchants),
        "transactions": len(order),
        "planted_transactions": len(planted),
        "background_transactions": len(background),
        "rings": len(set(planted.ring_id)),
        "labels": len(labels),
    }
    hashes = {name: _sha256(path) for name, path in (
        ("events.jsonl", paths.events),
        ("entities.json", paths.entities),
        ("labels.jsonl", paths.labels),
    )}
    manifest = {
        "profile": cfg.profile.name,
        "seed": seed,
        "counts": counts,
        "typology_counts": typology_counts,
        "sha256": hashes,
    }
    (paths.profile_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )

    say(f"  labels     : {len(labels)}")
    say(f"  written    : {paths.profile_dir}")
    return GenerationResult(
        profile=cfg.profile.name, seed=seed, counts=counts,
        typology_counts=typology_counts,
        files={"events": str(paths.events), "entities": str(paths.entities),
               "labels": str(paths.labels)},
        hashes=hashes,
    )


def _write_events(cfg: Config, pop: Population, traffic: Traffic,
                  origin: np.ndarray, order: np.ndarray,
                  planted: Planted) -> list[Label]:
    """Stream the merged log to disk, assigning ids in timestamp order.

    Transaction ids are assigned here rather than at planting time, because they
    have to reflect the final ordering — which is only known after the merge. The
    planted rows' labels are therefore emitted from this pass too.
    """
    account_ids = [a.account_id for a in pop.accounts]
    device_ids = [d.device_id for d in pop.devices]
    merchant_ids = [m.merchant_id for m in pop.merchants]
    currency = cfg.generation.currency

    ts, src, dst = traffic.ts, traffic.src, traffic.dst
    amount, channel, device, merchant = (traffic.amount, traffic.channel,
                                         traffic.device, traffic.merchant)
    txn_labels: list[Label] = []

    with cfg.paths.events.open("w") as handle:
        for position, row in enumerate(order):
            row = int(row)
            m = int(merchant[row])
            record = {
                "txn_id": f"TXN-{position:09d}",
                "src_account": account_ids[int(src[row])],
                "dst_account": account_ids[int(dst[row])],
                "amount": float(amount[row]),
                "ts": int(ts[row]),
                "channel": CHANNELS[int(channel[row])],
                "device_id": device_ids[int(device[row])],
                "merchant_id": merchant_ids[m] if m >= 0 else None,
                "currency": currency,
                "status": "posted",
            }
            handle.write(json.dumps(record) + "\n")

            slot = int(origin[row])
            if slot >= 0:
                txn_labels.append(Label(
                    ring_id=planted.ring_id[slot],
                    typology=str(planted.typology[slot]),
                    account_id=record["src_account"],
                    txn_id=record["txn_id"],
                ))
    return txn_labels


def _write_entities(cfg: Config, pop: Population) -> None:
    payload = {
        "profile": cfg.profile.name,
        "span": {"start": iso(cfg.end_time_ms - days(cfg.profile.span_days)),
                 "end": iso(cfg.end_time_ms)},
        "customers": [c.to_dict() for c in pop.customers],
        "accounts": [a.to_dict() for a in pop.accounts],
        "devices": [d.to_dict() for d in pop.devices],
        "merchants": [m.to_dict() for m in pop.merchants],
    }
    cfg.paths.entities.write_text(json.dumps(payload, indent=None, sort_keys=True))


def _write_labels(path: Path, labels: list[Label]) -> None:
    with path.open("w") as handle:
        for label in labels:
            handle.write(json.dumps(label.to_dict()) + "\n")
