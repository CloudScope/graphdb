# Raphtory API notes

Raphtory's Python API has moved between releases more than most — Lab 8 already
carried that caveat. This file records what was **verified by introspection
against the installed package**, not read from documentation, so that when
something breaks later it is clear whether the API moved or the code was wrong.

## Verified: raphtory 0.17.0 on Python 3.13.2 (2026-09-05)

Installed clean from PyPI with no build step. Pulls in `pandas`, `pyarrow`,
`ipython`/`ipywidgets` (for `to_pyvis`) transitively.

### Graph construction

```python
from raphtory import Graph
g = Graph()
g.add_edge(timestamp, src, dst, properties=None, layer=None, event_id=None)
g.add_node(timestamp, id, properties=None, ...)
```

`layer=` is present and is what DESIGN.md §3.2's two-layer model relies on
(`"transfer"` and `"device"`). Confirmed in `add_edge`'s signature.

### Bulk ingest

`Graph.load_edges()` accepts **any object implementing `__arrow_c_stream__`** —
the Arrow C stream protocol. That is why `GraphStore.export_transfers()` returns
an Arrow table rather than a list of dicts (DESIGN.md §5.4): ingest becomes one
vectorized call instead of a per-edge Python loop.

Also present: `load_nodes`, `load_edge_metadata`, `load_node_metadata`,
`from_parquet`, `to_parquet`, `save_to_file` / `load_from_file`, `serialise` /
`deserialise`.

### Time views

| Call | Semantics |
|---|---|
| `g.window(start, end)` | events in `[start, end)` — **half-open**, which is why `timeutil.window_bounds` returns half-open bounds too |
| `g.at(t)` | events at `t` |
| `g.snapshot_at(t)` | events not explicitly deleted as of `t` |
| `g.before(end)` / `g.after(start)` | exclusive on the bound |
| `g.rolling(window, step=None)` | `WindowSet` of rolling windows |
| `g.expanding(step)` | `WindowSet` of expanding windows |
| `g.latest()` / `g.snapshot_latest()` | most recent state |

Also available: `layer`/`layers`/`exclude_layer`, `subgraph`, `shrink_window`,
`materialize`, `largest_connected_component`.

### Algorithms (`raphtory.algorithms`)

The ones this project depends on, all confirmed present:

- **`temporally_reachable_nodes(graph, max_hops, start_time, seed_nodes, stop_nodes=None)`**
  — returns a `NodeStateReachability`. A *time-respecting path* is defined in its
  own docstring as a sequence of edges `(v_i, v_i+1, t_i)` with `t_i < t_i+1`.
  This is the single most load-bearing call in the project: **a seed node
  appearing in its own reachable set is a genuine time-respecting cycle**, which
  is how DESIGN.md §4.3.2's decoy comparison is measured.
  Note it takes `seed_nodes` — it is inherently seeded, not all-pairs, which is
  what makes §6.2's "seeded, not exhaustive" constraint natural rather than a
  workaround.
- `louvain`, `label_propagation`, `weakly_connected_components`,
  `strongly_connected_components`, `k_core`
- `pagerank`, `hits`, `degree_centrality`, `betweenness_centrality`
- `global_temporal_three_node_motif`, `local_temporal_three_node_motifs`
  (Paranjape et al., *Motifs in Temporal Networks*, 2017 — delta-temporal motifs)
- `balance` (sums edge weights by direction — useful for fan-in/fan-out)
- `temporal_SEIR`, `temporally_reachable_nodes`, `temporal_bipartite_graph_projection`

Full module surface as of 0.17.0 is 44 callables; the above is what §6.2 uses.

## Drift log

### 0.17.0 vs. Lab 8's draft (`labs/08-raphtory-temporal/`)

Lab 8 was written against an earlier API and does not run as-is. What moved:

| Lab 8 wrote | 0.17.0 wants |
|---|---|
| `edge.src().name` | `edge.src.name` — `src`/`dst` are properties, not methods |
| `edge.earliest_time` as an `int` | `OptionalEventTime`; use `.t` for the integer, `.is_some()` to test |
| `graph.earliest_time` as an `int` | same — `.t` |
| exploded event time as an `int` | `EventTime`; use `.time.t` |

### Algorithm return shapes changed

Node-state results now map a node to a **single-key dict**, not a bare number:

```python
A.pagerank(g).items()            # -> (Node, {'pagerank': 0.0123})
A.louvain(g).items()             # -> (Node, {'community_id': 5})
A.weakly_connected_components(g) # -> (Node, {'component_id': 0})
```

`signals._scalar()` unwraps either shape, so a future release that flattens them
again will not break anything.

### `count_edges` vs `count_temporal_edges`

`count_edges()` counts **unique node pairs**; repeated transfers between the same
two accounts collapse into one edge with many updates. `count_temporal_edges()`
counts the events. On the small profile: 13,771 edges, 20,781 temporal edges from
19,506 transfers plus 1,275 device pairs. Reconciling an ingest against a source
row count needs the temporal one.

### Reproducibility

`pagerank`, `louvain` and `label_propagation` are all non-deterministic under
default (multi-threaded) execution, and `label_propagation`'s `seed` argument
does **not** fix it. See `docs/decisions/002-nondeterministic-algorithms.md`.

### `temporally_reachable_nodes` is not a cycle detector

Measured: 59 of 60 random accounts "reach themselves" over a 72h window with six
hops. See `docs/decisions/001-cycle-detection.md`.
