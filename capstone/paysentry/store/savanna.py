"""TigerGraph Savanna implementation of ``GraphStore``.

The same six questions ``LocalStore`` answers from SQLite, answered by a
distributed graph database over REST++. Everything above this module is
unchanged, which is the point: swap the store, rerun the evaluation, and
whatever differs is what the graph database actually bought (DESIGN.md §5.4).

Three things this has to cope with that the local store does not:

* **Cold starts.** Free-tier workspaces auto-suspend when idle. The first call
  after a suspension fails or hangs while the workspace wakes, so connection is
  retried with backoff rather than treated as an error.
* **Round trips.** Screening is one installed query, not five calls — on a cloud
  workspace the network dominates the query (DESIGN.md §5.3).
* **Schema first.** Nothing loads until the schema exists. That is Lesson 7's
  central constraint, and it is why ``provision`` has to run before ``load``.

Signal strengths are *not* computed here. They come from the shared builders in
``screening.py``, so the only thing that differs between the two stores is how
the graph is measured — never what counts as suspicious.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterator

import pyarrow as pa

from ..config import Config, ConfigError
from ..models import (Decision, Engine, LoadStats, RiskScore, ScreenResult, Txn)
from ..timeutil import hours
from .base import TRANSFER_SCHEMA, EntitySet, GraphStore

PERMANENT_AUTH_MARKERS = (
    "authentication failed", "invalid token", "token expired", "unauthorized",
    "invalid credential", "expired", "forbidden",
)


def is_permanent_auth_error(exc: Exception | None) -> bool:
    """Distinguish a rejected credential from a workspace that is still waking."""
    if exc is None:
        return False
    return any(marker in str(exc).lower() for marker in PERMANENT_AUTH_MARKERS)


def build_connection(creds):
    """Build a pyTigerGraph connection, using the right argument for the credential.

    ``jwtToken`` and ``gsqlSecret`` are separate parameters; a JWT passed as a
    secret authenticates as nobody. Only a database secret can be exchanged for a
    token via ``getToken``, so that call is skipped for JWTs.
    """
    from pyTigerGraph import TigerGraphConnection

    common = dict(host=creds.normalized_host, graphname=creds.graph,
                  restppPort=creds.rest_port, gsPort=creds.gsql_port)
    if creds.is_jwt:
        conn = TigerGraphConnection(jwtToken=creds.secret, **common)
    elif creds.secret:
        conn = TigerGraphConnection(gsqlSecret=creds.secret, **common)
        conn.getToken(creds.secret)
    else:
        conn = TigerGraphConnection(username=creds.username,
                                    password=creds.password, **common)
    conn.echo()
    return conn

GSQL_DIR = Path(__file__).parent / "gsql"
UPSERT_BATCH = 10_000

# File stem -> the query name GSQL registers, which is not derivable from the
# filename.
QUERY_NAMES = {
    "screen_transaction": "screenTransaction",
    "export_transfers": "exportTransfers",
}


class SavannaStore(GraphStore):
    """TigerGraph Savanna over pyTigerGraph."""

    name = "savanna"

    def __init__(self, cfg: Config) -> None:
        super().__init__(cfg)
        self.creds = Config.savanna_credentials()
        missing = self.creds.missing()
        if missing:
            raise ConfigError(
                f"Savanna credentials incomplete: missing {', '.join(missing)}. "
                f"See capstone/docs/tigergraph-setup.md, then run 'paysentry check'."
            )
        self._conn: Any = None

    # -- connection ------------------------------------------------------
    @property
    def conn(self) -> Any:
        """Connect on first use, tolerating a suspended workspace."""
        if self._conn is not None:
            return self._conn

        from pyTigerGraph import TigerGraphConnection

        sv = self.cfg.runtime.savanna
        last: Exception | None = None
        for attempt in range(sv.cold_start_retries):
            try:
                self._conn = build_connection(self.creds)
                return self._conn
            except Exception as exc:                 # pyTigerGraph raises broadly
                last = exc
                # Only a cold start is worth waiting out. A rejected credential
                # will be rejected just as firmly in fifteen seconds, and
                # retrying it turns an instant, clear error into two and a half
                # minutes of misleading "workspace not ready".
                if is_permanent_auth_error(exc) or attempt == sv.cold_start_retries - 1:
                    break
                wait = sv.cold_start_backoff_s * (attempt + 1)
                print(f"  workspace not ready ({type(exc).__name__}); "
                      f"retrying in {wait}s [{attempt + 1}/{sv.cold_start_retries}]")
                time.sleep(wait)

        if is_permanent_auth_error(last):
            raise RuntimeError(
                f"TigerGraph rejected the credential in TG_SECRET "
                f"(detected as: {self.creds.auth_kind}).\n{last}\n"
                f"Run 'paysentry check' for a staged diagnosis."
            ) from last
        raise RuntimeError(
            f"could not reach the Savanna workspace after "
            f"{sv.cold_start_retries} attempts: {last}\n"
            f"Run 'paysentry check' for a staged diagnosis."
        ) from last

    def close(self) -> None:
        self._conn = None

    # -- lifecycle -------------------------------------------------------
    # GSQL reports outcomes in prose inside a 200 response, so success has to be
    # read out of the text. Two rules make that reliable:
    #
    #   * DROP statements run separately and their errors are ignored. On a fresh
    #     graph `DROP JOB x` legitimately says "could not be found anywhere",
    #     which is not a failure — but it is indistinguishable from one if the
    #     whole script's output is scanned for the word "fails".
    #   * The remainder is judged on an explicit success marker, not on the
    #     absence of scary words. A schema change that silently did nothing would
    #     otherwise look identical to one that worked.
    SUCCESS_MARKERS = ("schema change succeeded", "completes in",
                       "query installation finished", "successfully created quer",
                       "is created")
    FAILURE_MARKERS = ("syntax error", "semantic check fails", "failed to",
                       "error:", "cannot be", "does not exist")

    def _run_gsql_file(self, path: Path) -> str:
        script = path.read_text().replace("@graph@", self.creds.graph)

        preamble, body = [], []
        for line in script.splitlines():
            (preamble if line.strip().upper().startswith("DROP ") else body).append(line)

        # Idempotency: dropping something that was never created is fine.
        header = [ln for ln in body if ln.strip().upper().startswith("USE GRAPH")]
        for drop in preamble:
            try:
                self.conn.gsql("\n".join(header + [drop]))
            except Exception:
                pass

        output = self.conn.gsql("\n".join(body))
        text = output if isinstance(output, str) else str(output)
        lowered = text.lower()

        if any(marker in lowered for marker in self.SUCCESS_MARKERS):
            return text
        if any(marker in lowered for marker in self.FAILURE_MARKERS):
            raise RuntimeError(f"GSQL failed in {path.name}:\n{text}")
        raise RuntimeError(
            f"GSQL in {path.name} reported no success marker — treating as failed:\n{text}")

    def _graph_exists(self) -> bool:
        listing = str(self.conn.gsql("SHOW GRAPH *"))
        return f"- Graph {self.creds.graph}(" in listing

    def _ensure_graph(self) -> None:
        """Create the graph if it is absent.

        A database secret is scoped to a graph, so the graph normally has to
        exist before credentials can even be made — but a JWT is workspace-scoped
        and arrives before any graph does. Creating it here removes a manual step
        that otherwise fails with a bare "Graph 'X' does not exist".
        """
        if self._graph_exists():
            return
        print(f"  graph '{self.creds.graph}' does not exist — creating it")
        output = str(self.conn.gsql(f"CREATE GRAPH {self.creds.graph} ()"))
        if "is created" not in output.lower():
            raise RuntimeError(f"could not create graph {self.creds.graph}:\n{output}")

    def provision(self, drop: bool = False, reinstall: bool = False) -> None:
        """Apply the schema and install the queries. Safe to re-run.

        ``reinstall`` recompiles the queries while leaving schema and data alone —
        the case where a .gsql file changed after a load. Without it the only way
        to pick up an edited query was ``--drop``, which also discards a loaded
        dataset and the ~30s reload that follows.
        """
        if drop:
            # DROP GRAPH, never DROP ALL. `DROP ALL` is catalog-wide: it would
            # take out every other graph on the workspace, including the sample
            # graphs Savanna ships with. Scoping the teardown to our own graph is
            # the difference between a reset and an incident.
            if self._graph_exists():
                print(f"  dropping graph '{self.creds.graph}' (this graph only)")
                output = str(self.conn.gsql(f"DROP GRAPH {self.creds.graph}"))
                if "error" in output.lower() and "not exist" not in output.lower():
                    raise RuntimeError(f"could not drop graph:\n{output}")

        self._ensure_graph()

        # Re-applying a schema change job whose types already exist is an error,
        # so provisioning checks first. DESIGN.md §10.2 requires this command to
        # be re-runnable: a free-tier workspace gets torn down and recreated, and
        # a provisioner you can only run once is no use then.
        expected = {"Customer", "Account", "Device", "Merchant", "Txn"}
        try:
            existing = set(self.conn.getVertexTypes())
        except Exception:
            existing = set()
        if expected <= existing:
            print("  schema already present — skipping")
        else:
            print("  applying schema (a schema change takes ~30-60s)")
            self._run_gsql_file(GSQL_DIR / "schema.gsql")

        # Query compilation costs about a minute each and is the slowest part of
        # provisioning, so already-installed queries are skipped. `load` calls
        # provision() to guarantee a schema exists; without this it would pay for
        # a full reinstall on every load.
        try:
            installed = set(self.conn.listQueryNames())
        except Exception:
            installed = set()
        installed = {name.lstrip("/") for name in installed}

        for query in sorted((GSQL_DIR / "queries").glob("*.gsql")):
            name = QUERY_NAMES.get(query.stem, query.stem)
            if name in installed and not drop and not reinstall:
                print(f"  {name} already installed — skipping")
                continue
            print(f"  installing {name} (compilation takes a minute)")
            self._run_gsql_file(query)

    # -- bulk load -------------------------------------------------------
    def bulk_load(self, entities: EntitySet, events: Iterator[Txn]) -> LoadStats:
        import pandas as pd

        started = time.perf_counter()
        conn = self.conn
        vertices: dict[str, int] = {}
        edges: dict[str, int] = {}

        def frame(rows: list[dict]) -> "pd.DataFrame":
            return pd.DataFrame(rows)

        vertices["Customer"] = conn.upsertVertexDataFrame(
            frame([c.to_dict() for c in entities.customers]), "Customer", "customer_id",
            attributes={"name": "name", "country": "country",
                        "kyc_level": "kyc_level", "onboarded_at": "onboarded_at"})

        accounts = [{k: v for k, v in a.to_dict().items()
                     if k not in ("risk_reasons", "ring_id", "scored_at", "risk_score")}
                    for a in entities.accounts]
        vertices["Account"] = conn.upsertVertexDataFrame(
            frame(accounts), "Account", "account_id",
            attributes={"customer_id": "customer_id", "opened_at": "opened_at",
                        "account_type": "account_type", "status": "status"})

        vertices["Device"] = conn.upsertVertexDataFrame(
            frame([d.to_dict() for d in entities.devices]), "Device", "device_id",
            attributes={"fingerprint": "fingerprint", "os": "os",
                        "first_seen": "first_seen"})

        vertices["Merchant"] = conn.upsertVertexDataFrame(
            frame([m.to_dict() for m in entities.merchants]), "Merchant", "merchant_id",
            attributes={"name": "name", "mcc": "mcc", "country": "country"})

        edges["OWNS"] = conn.upsertEdgeDataFrame(
            frame([{"c": a.customer_id, "a": a.account_id} for a in entities.accounts]),
            "Customer", "OWNS", "Account", from_id="c", to_id="a", attributes={})

        # Transactions stream in batches: the large profile is ~2M events and a
        # single payload would neither fit nor retry cleanly.
        n_txns = 0
        device_use: dict[tuple[str, str], list[int]] = {}
        batch: list[Txn] = []

        def flush(rows: list[Txn]) -> None:
            nonlocal n_txns
            if not rows:
                return
            conn.upsertVertexDataFrame(
                frame([{"txn_id": t.txn_id, "amount": t.amount, "ts": t.ts,
                        "channel": str(t.channel), "currency": t.currency,
                        "status": t.status} for t in rows]),
                "Txn", "txn_id",
                attributes={"amount": "amount", "ts": "ts", "channel": "channel",
                            "currency": "currency", "status": "status"})
            pairs = frame([{"a": t.src_account, "t": t.txn_id} for t in rows])
            conn.upsertEdgeDataFrame(pairs, "Account", "SENT", "Txn",
                                     from_id="a", to_id="t", attributes={})
            conn.upsertEdgeDataFrame(
                frame([{"t": t.txn_id, "a": t.dst_account} for t in rows]),
                "Txn", "RECEIVED_BY", "Account", from_id="t", to_id="a", attributes={})
            conn.upsertEdgeDataFrame(
                frame([{"t": t.txn_id, "d": t.device_id} for t in rows]),
                "Txn", "VIA_DEVICE", "Device", from_id="t", to_id="d", attributes={})
            merchant_rows = [{"t": t.txn_id, "m": t.merchant_id}
                             for t in rows if t.merchant_id]
            if merchant_rows:
                conn.upsertEdgeDataFrame(frame(merchant_rows), "Txn", "AT_MERCHANT",
                                         "Merchant", from_id="t", to_id="m",
                                         attributes={})
            n_txns += len(rows)
            print(f"    {n_txns:,} transactions loaded")

        for txn in events:
            batch.append(txn)
            key = (txn.src_account, txn.device_id)
            seen = device_use.get(key)
            if seen is None:
                device_use[key] = [txn.ts, txn.ts, 1]
            else:
                seen[0] = min(seen[0], txn.ts)
                seen[1] = max(seen[1], txn.ts)
                seen[2] += 1
            if len(batch) >= UPSERT_BATCH:
                flush(batch)
                batch = []
        flush(batch)

        edges["USED_DEVICE"] = conn.upsertEdgeDataFrame(
            frame([{"a": account, "d": device, "first_seen": first,
                    "last_seen": last, "txn_count": count}
                   for (account, device), (first, last, count) in device_use.items()]),
            "Account", "USED_DEVICE", "Device", from_id="a", to_id="d",
            attributes={"first_seen": "first_seen", "last_seen": "last_seen",
                        "txn_count": "txn_count"})

        vertices["Txn"] = n_txns
        edges.update({"SENT": n_txns, "RECEIVED_BY": n_txns, "VIA_DEVICE": n_txns})
        return LoadStats(vertices=vertices, edges=edges,
                         elapsed_s=round(time.perf_counter() - started, 3))

    # -- hot path --------------------------------------------------------
    def upsert_txn(self, txn: Txn) -> None:
        conn = self.conn
        conn.upsertVertex("Txn", txn.txn_id, {
            "amount": txn.amount, "ts": txn.ts, "channel": str(txn.channel),
            "currency": txn.currency, "status": txn.status})
        conn.upsertEdge("Account", txn.src_account, "SENT", "Txn", txn.txn_id)
        conn.upsertEdge("Txn", txn.txn_id, "RECEIVED_BY", "Account", txn.dst_account)
        conn.upsertEdge("Txn", txn.txn_id, "VIA_DEVICE", "Device", txn.device_id)
        if txn.merchant_id:
            conn.upsertEdge("Txn", txn.txn_id, "AT_MERCHANT", "Merchant", txn.merchant_id)
        conn.upsertEdge("Account", txn.src_account, "USED_DEVICE", "Device",
                        txn.device_id, {"first_seen": txn.ts, "last_seen": txn.ts,
                                        "txn_count": 1})

    def screen(self, txn: Txn) -> ScreenResult:
        """One installed query, then the shared signal builders."""
        from .screening import (counterparty_signal, decide, device_signal,
                                fan_out_signal, near_threshold_signal,
                                velocity_signal)

        hp = self.cfg.detection.hot_path
        threshold = self.cfg.generation.reporting_threshold
        started = time.perf_counter()

        result = self.conn.runInstalledQuery("screenTransaction", {
            # VERTEX<T> parameters must be 1-tuples. Passing a bare id is
            # deprecated: pyTigerGraph detects the old form, fails the POST, and
            # silently retries over GET — a second round trip on every single
            # authorization, which is exactly the wrong place to pay for one.
            "src_account": (txn.src_account,),
            "device_id": (txn.device_id,),
            "amount": txn.amount,
            "as_of": txn.ts,
            "window_1h": hours(1),
            "window_24h": hours(24),
            "window_fanout": hours(hp.fan_out_window_hours),
            "near_lo": threshold * (1 - hp.near_threshold_band),
            "near_hi": threshold,
            "max_fanout": hp.counterparty_max_fanout,
        })
        row = result[0] if result else {}
        elapsed_ms = (time.perf_counter() - started) * 1000

        signals = [s for s in (
            device_signal(txn, hp, Engine.TIGERGRAPH,
                          customers=row.get("device_customers", 0),
                          accounts=row.get("device_accounts", 0),
                          peak_peer_risk=row.get("device_peer_risk", 0.0)),
            counterparty_signal(txn, hp, Engine.TIGERGRAPH,
                                peak=row.get("counterparty_peak", 0.0),
                                mean=row.get("counterparty_peak", 0.0),
                                peers=row.get("counterparty_peers", 0),
                                ring=row.get("counterparty_ring") or None),
            velocity_signal(txn, hp, Engine.TIGERGRAPH,
                            n_1h=row.get("velocity_1h", 0),
                            n_24h=row.get("velocity_24h", 0),
                            amount_24h=row.get("velocity_amount", 0.0)),
            fan_out_signal(txn, hp, Engine.TIGERGRAPH,
                           recipients=row.get("fanout_recipients", 0),
                           near_threshold=row.get("near_threshold_count", 0)),
            near_threshold_signal(txn, hp, Engine.TIGERGRAPH, threshold),
        ) if s is not None]

        return decide(self.cfg, txn, signals, Engine.TIGERGRAPH, elapsed_ms)

    # -- warm path handoff -----------------------------------------------
    def export_transfers(self, since: int, until: int) -> pa.Table:
        result = self.conn.runInstalledQuery(
            "exportTransfers", {"since": since, "until": until})
        rows = result[0].get("transfers", []) if result else []
        columns = {field.name: [row.get(field.name) for row in rows]
                   for field in TRANSFER_SCHEMA}
        return pa.table(
            {name: pa.array(values, type=TRANSFER_SCHEMA.field(name).type)
             for name, values in columns.items()},
            schema=TRANSFER_SCHEMA)

    # -- feedback loop ---------------------------------------------------
    def write_risk(self, scores: list[RiskScore]) -> int:
        import json

        import pandas as pd

        if not scores:
            return 0
        frame = pd.DataFrame([{
            "account_id": s.account_id, "risk_score": s.score,
            "risk_reasons": json.dumps(s.reasons), "ring_id": s.ring_id or "",
            "scored_at": s.scored_at or 0} for s in scores])
        return self.conn.upsertVertexDataFrame(
            frame, "Account", "account_id",
            attributes={"risk_score": "risk_score", "risk_reasons": "risk_reasons",
                        "ring_id": "ring_id", "scored_at": "scored_at"})

    def account_risk(self, account_ids: list[str]) -> dict[str, RiskScore]:
        import json

        if not account_ids:
            return {}
        out: dict[str, RiskScore] = {}
        for vertex in self.conn.getVerticesById("Account", account_ids):
            attrs = vertex.get("attributes", {})
            reasons = attrs.get("risk_reasons") or "[]"
            out[vertex["v_id"]] = RiskScore(
                account_id=vertex["v_id"],
                score=attrs.get("risk_score", 0.0),
                reasons=json.loads(reasons) if reasons.startswith("[") else [],
                ring_id=attrs.get("ring_id") or None,
                scored_at=attrs.get("scored_at") or None)
        return out

    # -- introspection ---------------------------------------------------
    def counts(self, settle_s: float = 0.0) -> dict[str, int]:  # type: ignore[override]
        """Vertex and edge counts.

        REST++ upserts are acknowledged before they are fully visible to the
        counters, so reading counts straight after a bulk load under-reports —
        observed 18,953 of 19,517 Txn vertices immediately after a load that had
        in fact written all of them. Pass ``settle_s`` to wait before counting
        when reconciling against another store.
        """
        if settle_s:
            time.sleep(settle_s)
        out: dict[str, int] = {}
        for vertex_type in ("Customer", "Account", "Device", "Merchant", "Txn"):
            try:
                out[vertex_type.lower()] = self.conn.getVertexCount(vertex_type)
            except Exception:
                out[vertex_type.lower()] = 0
        for edge_type in ("OWNS", "SENT", "RECEIVED_BY", "VIA_DEVICE",
                          "AT_MERCHANT", "USED_DEVICE"):
            try:
                out[edge_type.lower()] = self.conn.getEdgeCount(edge_type)
            except Exception:
                out[edge_type.lower()] = 0
        return out

    def commit(self) -> None:
        """No-op: REST++ upserts are already durable."""
