"""
Lab 8 queries — window(), at(), a manual time-respecting-path check on the
Lab 4 closed loop, and a windowed degree comparison (Lesson 6's "run an
OLAP metric over time" idea, made concrete).

Raphtory's exact method names have moved around release to release more
than Cypher's have - treat this as a starting draft, the same caveat as
Lab 7's GSQL. If something here doesn't run, check your installed
version's API reference for the closest equivalent.

Run with: python queries.py
"""

from setup import build_graph, ts


def print_edges(edges, label):
    print(f"\n{label}")
    for e in edges:
        props = e.properties
        print(
            f"  {e.src().name} -> {e.dst().name}  "
            f"txn={props.get('txnId')}  amount={props.get('amount')}  "
            f"fraud={props.get('isFraud')}  device={props.get('device')}"
        )


def main():
    g = build_graph()

    # 1. Window: only transactions between Aug 3rd and Aug 5th (exclusive).
    # Compare this to Lesson 5: instead of filtering every edge's timestamp
    # property one at a time, the engine is asked for the range directly.
    windowed = g.window(ts("2026-08-03T00:00:00Z"), ts("2026-08-05T00:00:00Z"))
    print_edges(windowed.edges, "Window: 2026-08-03 to 2026-08-05 (exclusive)")
    # Expect exactly txn-3 and txn-4 - everything else falls outside the range.

    # 2. "As of": the graph exactly as it looked at noon on Aug 3rd, before
    # that day's transaction had posted yet.
    snapshot = g.at(ts("2026-08-03T12:00:00Z"))
    print_edges(snapshot.edges, "Snapshot: as of 2026-08-03 12:00")
    # Expect txn-1 and txn-2 only - txn-3 posts two hours later, at 14:15.

    # 3. Is the Lab 4 closed loop actually time-respecting?
    # A -> B -> C -> D -> E -> F -> A, in that transaction order.
    loop = ["ACC-1001", "ACC-1002", "ACC-1003", "ACC-1004", "ACC-1005", "ACC-1006", "ACC-1001"]
    print("\nTime-respecting check on the Lab 4 closed loop:")
    last_time = None
    respects_time = True
    for src, dst in zip(loop, loop[1:]):
        edge = g.edge(src, dst)
        hop_time = edge.earliest_time
        txn_id = edge.properties.get("txnId")
        in_order = last_time is None or hop_time > last_time
        respects_time = respects_time and in_order
        print(f"  {src} -> {dst}  ({txn_id}, t={hop_time})  {'OK' if in_order else 'OUT OF ORDER'}")
        last_time = hop_time
    print(f"  => time-respecting: {respects_time}")

    # 4. Degree per account, early window vs. late window - the same metric,
    # run over two slices of time instead of once over the whole graph.
    early = g.window(ts("2026-08-01T00:00:00Z"), ts("2026-08-04T00:00:00Z"))
    late = g.window(ts("2026-08-04T00:00:00Z"), ts("2026-08-06T00:00:00Z"))
    print("\nOut-degree per account, early window (Aug 1-4) vs. late window (Aug 4-6):")
    all_accounts = sorted(n.name for n in g.nodes)
    for acc in all_accounts:
        early_deg = early.node(acc).out_degree() if early.node(acc) is not None else 0
        late_deg = late.node(acc).out_degree() if late.node(acc) is not None else 0
        print(f"  {acc}: early={early_deg}  late={late_deg}")


if __name__ == "__main__":
    main()
