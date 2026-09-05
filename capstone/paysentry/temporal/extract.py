"""Pull a slice of the graph out for Raphtory.

Two sources, deliberately:

* ``store`` — via ``GraphStore.export_transfers``, the realistic path. This is
  what a scheduled job would do against TigerGraph: run a query, get an edge
  list, hand it to the analytics engine (DESIGN.md §2).
* ``log`` — straight from ``events.jsonl``. Faster, needs no store, and it is the
  architecture Lesson 9 argued was cleaner anyway: both engines as independent
  consumers of the same event stream rather than one feeding the other.

Both produce the same Arrow tables, so the analytics downstream cannot tell which
one it got — which is the point of naming the transport as a choice.
"""

from __future__ import annotations

import json
from collections import defaultdict

import pyarrow as pa

from ..config import Config
from ..store.base import TRANSFER_SCHEMA, GraphStore

# Account-to-account device co-use. Undirected in meaning, stored once per pair.
DEVICE_SCHEMA = pa.schema([
    ("account_a", pa.string()),
    ("account_b", pa.string()),
    ("first_co_use", pa.int64()),
    ("device_id", pa.string()),
])


def transfers_from_log(cfg: Config, since: int, until: int) -> pa.Table:
    """Read the event log directly into the transfer schema."""
    columns: dict[str, list] = {field.name: [] for field in TRANSFER_SCHEMA}
    with cfg.paths.events.open() as handle:
        for line in handle:
            event = json.loads(line)
            if not since <= event["ts"] < until:
                continue
            columns["txn_id"].append(event["txn_id"])
            columns["src_account"].append(event["src_account"])
            columns["dst_account"].append(event["dst_account"])
            columns["ts"].append(event["ts"])
            columns["amount"].append(event["amount"])
            columns["device_id"].append(event["device_id"])
            columns["channel"].append(event["channel"])
    return pa.table(columns, schema=TRANSFER_SCHEMA)


def device_pairs_from_transfers(transfers: pa.Table,
                                max_accounts_per_device: int = 64) -> pa.Table:
    """Derive account-pair device co-use from a transfer table.

    Pairs are emitted at the timestamp of the *second* account's first use of the
    device — the moment the sharing actually began, which is the only timestamp
    that makes the edge meaningful in a temporal window.

    Devices used by more than ``max_accounts_per_device`` accounts are skipped.
    A device on hundreds of accounts is a shared kiosk or an instrumentation
    artefact, not a ring, and pairing it would emit a quadratic blow-up of edges
    that dominates the graph while meaning nothing.
    """
    first_use: dict[str, dict[str, int]] = defaultdict(dict)
    devices = transfers.column("device_id").to_pylist()
    accounts = transfers.column("src_account").to_pylist()
    times = transfers.column("ts").to_pylist()
    for device, account, ts in zip(devices, accounts, times):
        seen = first_use[device]
        if account not in seen or ts < seen[account]:
            seen[account] = ts

    a_col, b_col, t_col, d_col = [], [], [], []
    skipped = 0
    for device, users in first_use.items():
        if len(users) < 2:
            continue
        if len(users) > max_accounts_per_device:
            skipped += 1
            continue
        ordered = sorted(users.items())
        for i, (account_a, ts_a) in enumerate(ordered):
            for account_b, ts_b in ordered[i + 1:]:
                a_col.append(account_a)
                b_col.append(account_b)
                t_col.append(max(ts_a, ts_b))
                d_col.append(device)
    return pa.table({"account_a": a_col, "account_b": b_col,
                     "first_co_use": t_col, "device_id": d_col},
                    schema=DEVICE_SCHEMA)


def extract(cfg: Config, since: int, until: int,
            store: GraphStore | None = None) -> tuple[pa.Table, pa.Table]:
    """Return ``(transfers, device_pairs)`` for the window ``[since, until)``."""
    transfers = (store.export_transfers(since, until) if store is not None
                 else transfers_from_log(cfg, since, until))
    return transfers, device_pairs_from_transfers(transfers)
