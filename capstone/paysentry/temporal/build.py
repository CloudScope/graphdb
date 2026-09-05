"""Build the Raphtory temporal graph.

The modelling shift from the store is the whole point (DESIGN.md §3.2): there is
no ``Txn`` node here. Each transfer is **one timestamped edge** between two
accounts, and the timestamp gives the event its identity. Promotion to a node
buys nothing when time is the index.

Two layers, because the questions differ:

* ``transfer`` — money movement. Cycle and flow analysis runs here alone, since a
  device co-use edge is not a path money can travel along.
* ``device``   — account-pair co-use. Used for ring cohesion, where structural
  closeness matters and the two kinds of tie reinforce each other.

Ingest goes through ``Graph.load_edges``, which consumes the Arrow C stream
protocol directly — one vectorized call instead of a per-edge Python loop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import pyarrow as pa
from raphtory import Graph

TRANSFER_LAYER = "transfer"
DEVICE_LAYER = "device"


@dataclass(slots=True)
class TemporalGraph:
    """A built graph plus what it cost and what it contains."""

    graph: Graph
    transfers: int
    device_pairs: int
    build_s: float

    @property
    def nodes(self) -> int:
        return self.graph.count_nodes()

    @property
    def edges(self) -> int:
        return self.graph.count_edges()

    def transfer_view(self):
        """Money-movement edges only — the view every flow question uses."""
        return self.graph.layer(TRANSFER_LAYER)

    def summary(self) -> str:
        return (f"  nodes {self.nodes:,}  edges {self.edges:,}  "
                f"(transfers {self.transfers:,}, device pairs {self.device_pairs:,})  "
                f"built in {self.build_s:.2f}s")


def build_graph(transfers: pa.Table, device_pairs: pa.Table) -> TemporalGraph:
    started = time.perf_counter()
    graph = Graph()

    if transfers.num_rows:
        graph.load_edges(
            transfers,
            time="ts", src="src_account", dst="dst_account",
            properties=["amount", "device_id", "channel", "txn_id"],
            layer=TRANSFER_LAYER,
        )
    if device_pairs.num_rows:
        graph.load_edges(
            device_pairs,
            time="first_co_use", src="account_a", dst="account_b",
            properties=["device_id"],
            layer=DEVICE_LAYER,
        )

    return TemporalGraph(
        graph=graph,
        transfers=transfers.num_rows,
        device_pairs=device_pairs.num_rows,
        build_s=time.perf_counter() - started,
    )
