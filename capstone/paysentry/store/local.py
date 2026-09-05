"""SQLite implementation of ``GraphStore``.

The schema mirrors the TigerGraph one vertex-for-table and edge-for-table, so the
two stores answer the same questions from the same shape of data and any
difference in results is a difference in *engine*, not in modelling.

``USED_DEVICE`` is materialized here exactly as it is in GSQL — derivable by
walking ``Account -> SENT -> Txn -> VIA_DEVICE -> Device``, but maintained on
write because device-sharing is the hottest screening query and re-deriving it
per authorization is the wrong trade (DESIGN.md §3.1).

Where this store is honestly weaker: multi-hop traversal is recursive SQL over
join tables, and it degrades with hop count in a way a native graph engine does
not. That is the intended contrast, not an accident.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Iterator

import pyarrow as pa

from ..config import Config
from ..models import (Account, LoadStats, RiskScore, ScreenResult, Txn)
from .base import TRANSFER_SCHEMA, EntitySet, GraphStore

SCHEMA = """
CREATE TABLE IF NOT EXISTS customer (
    customer_id  TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    country      TEXT NOT NULL,
    kyc_level    INTEGER NOT NULL,
    onboarded_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS account (
    account_id   TEXT PRIMARY KEY,
    customer_id  TEXT NOT NULL REFERENCES customer(customer_id),
    opened_at    INTEGER NOT NULL,
    account_type TEXT NOT NULL,
    status       TEXT NOT NULL,
    -- write-back targets: the only columns the warm path ever changes
    risk_score   REAL NOT NULL DEFAULT 0.0,
    risk_reasons TEXT NOT NULL DEFAULT '[]',
    ring_id      TEXT,
    scored_at    INTEGER
);

CREATE TABLE IF NOT EXISTS device (
    device_id    TEXT PRIMARY KEY,
    fingerprint  TEXT NOT NULL,
    os           TEXT NOT NULL,
    first_seen   INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS merchant (
    merchant_id  TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    mcc          TEXT NOT NULL,
    country      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS txn (
    txn_id       TEXT PRIMARY KEY,
    src_account  TEXT NOT NULL,
    dst_account  TEXT NOT NULL,
    amount       REAL NOT NULL,
    ts           INTEGER NOT NULL,
    channel      TEXT NOT NULL,
    device_id    TEXT NOT NULL,
    merchant_id  TEXT,
    currency     TEXT NOT NULL,
    status       TEXT NOT NULL
);

-- Denormalized Account->Device edge. Derivable, maintained on write.
CREATE TABLE IF NOT EXISTS used_device (
    account_id   TEXT NOT NULL,
    device_id    TEXT NOT NULL,
    first_seen   INTEGER NOT NULL,
    last_seen    INTEGER NOT NULL,
    txn_count    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (account_id, device_id)
);

CREATE INDEX IF NOT EXISTS ix_txn_src_ts   ON txn(src_account, ts);
CREATE INDEX IF NOT EXISTS ix_txn_dst_ts   ON txn(dst_account, ts);
CREATE INDEX IF NOT EXISTS ix_txn_device   ON txn(device_id);
CREATE INDEX IF NOT EXISTS ix_txn_ts       ON txn(ts);
CREATE INDEX IF NOT EXISTS ix_used_device  ON used_device(device_id);
CREATE INDEX IF NOT EXISTS ix_account_risk ON account(risk_score);
-- Covers the counterparty walk's second hop without touching the table.
CREATE INDEX IF NOT EXISTS ix_txn_src_dst   ON txn(src_account, dst_account, ts);
"""

TABLES = ("customer", "account", "device", "merchant", "txn", "used_device")


class LocalStore(GraphStore):
    """SQLite-backed store. Free, offline, and deliberately not a graph engine."""

    name = "local"

    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        cfg.paths.ensure()
        self.path = cfg.paths.local_db
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        # WAL plus a relaxed sync: this is a rebuildable derived store, not the
        # system of record. The event log is (DESIGN.md §2.2).
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

    # -- lifecycle -------------------------------------------------------
    def provision(self, drop: bool = False, reinstall: bool = False) -> None:
        if drop:
            for table in reversed(TABLES):
                self._conn.execute(f"DROP TABLE IF EXISTS {table}")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- bulk load -------------------------------------------------------
    def bulk_load(self, entities: EntitySet, events: Iterator[Txn]) -> LoadStats:
        started = time.perf_counter()
        conn = self._conn

        conn.executemany(
            "INSERT OR REPLACE INTO customer VALUES (?,?,?,?,?)",
            [(c.customer_id, c.name, c.country, c.kyc_level, c.onboarded_at)
             for c in entities.customers])
        conn.executemany(
            "INSERT OR REPLACE INTO account "
            "(account_id, customer_id, opened_at, account_type, status) VALUES (?,?,?,?,?)",
            [(a.account_id, a.customer_id, a.opened_at, str(a.account_type), str(a.status))
             for a in entities.accounts])
        conn.executemany(
            "INSERT OR REPLACE INTO device VALUES (?,?,?,?)",
            [(d.device_id, d.fingerprint, d.os, d.first_seen) for d in entities.devices])
        conn.executemany(
            "INSERT OR REPLACE INTO merchant VALUES (?,?,?,?)",
            [(m.merchant_id, m.name, m.mcc, m.country) for m in entities.merchants])
        conn.commit()

        # Streamed in batches: the ``large`` profile is ~2M events and holding
        # them all before the first insert would defeat the point of a log.
        n_txns = 0
        batch: list[tuple] = []
        device_use: dict[tuple[str, str], list[int]] = {}
        for txn in events:
            batch.append((txn.txn_id, txn.src_account, txn.dst_account, txn.amount,
                          txn.ts, str(txn.channel), txn.device_id, txn.merchant_id,
                          txn.currency, txn.status))
            key = (txn.src_account, txn.device_id)
            seen = device_use.get(key)
            if seen is None:
                device_use[key] = [txn.ts, txn.ts, 1]
            else:
                seen[0] = min(seen[0], txn.ts)
                seen[1] = max(seen[1], txn.ts)
                seen[2] += 1
            n_txns += 1
            if len(batch) >= 50_000:
                conn.executemany("INSERT OR REPLACE INTO txn VALUES (?,?,?,?,?,?,?,?,?,?)", batch)
                batch.clear()
        if batch:
            conn.executemany("INSERT OR REPLACE INTO txn VALUES (?,?,?,?,?,?,?,?,?,?)", batch)

        conn.executemany(
            "INSERT OR REPLACE INTO used_device VALUES (?,?,?,?,?)",
            [(account, device, first, last, count)
             for (account, device), (first, last, count) in device_use.items()])
        conn.commit()

        return LoadStats(
            vertices={"Customer": len(entities.customers),
                      "Account": len(entities.accounts),
                      "Device": len(entities.devices),
                      "Merchant": len(entities.merchants),
                      "Txn": n_txns},
            edges={"OWNS": len(entities.accounts),
                   "SENT": n_txns,
                   "RECEIVED_BY": n_txns,
                   "VIA_DEVICE": n_txns,
                   "USED_DEVICE": len(device_use)},
            elapsed_s=round(time.perf_counter() - started, 3),
        )

    # -- hot path --------------------------------------------------------
    def upsert_txn(self, txn: Txn) -> None:
        conn = self._conn
        conn.execute("INSERT OR REPLACE INTO txn VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (txn.txn_id, txn.src_account, txn.dst_account, txn.amount,
                      txn.ts, str(txn.channel), txn.device_id, txn.merchant_id,
                      txn.currency, txn.status))
        conn.execute(
            "INSERT INTO used_device VALUES (?,?,?,?,1) "
            "ON CONFLICT(account_id, device_id) DO UPDATE SET "
            "  first_seen = MIN(first_seen, excluded.first_seen), "
            "  last_seen  = MAX(last_seen,  excluded.last_seen), "
            "  txn_count  = txn_count + 1",
            (txn.src_account, txn.device_id, txn.ts, txn.ts))

    def screen(self, txn: Txn) -> ScreenResult:
        from .screening import screen_local
        return screen_local(self, txn)

    # -- warm path handoff -----------------------------------------------
    def export_transfers(self, since: int, until: int) -> pa.Table:
        rows = self._conn.execute(
            "SELECT txn_id, src_account, dst_account, ts, amount, device_id, channel "
            "FROM txn WHERE ts >= ? AND ts < ? ORDER BY ts", (since, until)).fetchall()
        columns = list(zip(*rows)) if rows else [()] * len(TRANSFER_SCHEMA)
        return pa.table(
            {field.name: pa.array(list(column), type=field.type)
             for field, column in zip(TRANSFER_SCHEMA, columns)},
            schema=TRANSFER_SCHEMA)

    # -- feedback loop ---------------------------------------------------
    def write_risk(self, scores: list[RiskScore]) -> int:
        import json as _json
        self._conn.executemany(
            "UPDATE account SET risk_score = ?, risk_reasons = ?, ring_id = ?, "
            "scored_at = ? WHERE account_id = ?",
            [(s.score, _json.dumps(s.reasons), s.ring_id, s.scored_at, s.account_id)
             for s in scores])
        self._conn.commit()
        return len(scores)

    def account_risk(self, account_ids: list[str]) -> dict[str, RiskScore]:
        import json as _json
        if not account_ids:
            return {}
        marks = ",".join("?" * len(account_ids))
        rows = self._conn.execute(
            f"SELECT account_id, risk_score, risk_reasons, ring_id, scored_at "
            f"FROM account WHERE account_id IN ({marks})", account_ids).fetchall()
        return {r["account_id"]: RiskScore(
            account_id=r["account_id"], score=r["risk_score"],
            reasons=_json.loads(r["risk_reasons"]), ring_id=r["ring_id"],
            scored_at=r["scored_at"]) for r in rows}

    # -- introspection ---------------------------------------------------
    def counts(self) -> dict[str, int]:
        return {table: self._conn.execute(
            f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in TABLES}

    # -- helpers used by screening ---------------------------------------
    def query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        return self._conn.execute(sql, params).fetchall()

    def commit(self) -> None:
        self._conn.commit()
