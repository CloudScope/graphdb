"""
Lab 8 setup — the same financial network from Labs 4-5-7, rebuilt as a
Raphtory temporal graph.

Notice the modeling shift from Lesson 8's "What happens to the Transaction
node" section: instead of Person -> Account -> Transaction -> Account,
Transaction -> Device, each transaction becomes ONE timestamped edge
directly between two Account nodes, carrying amount/isFraud/device as edge
properties. No node promotion needed - the timestamp itself gives the
event a first-class identity here.

Run with: python setup.py
(pip install raphtory first if you haven't - see README.md)
"""

from datetime import datetime, timezone
from raphtory import Graph


def ts(iso: str) -> int:
    """ISO 8601 string -> epoch milliseconds, the timestamp form Raphtory expects."""
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def build_graph() -> Graph:
    g = Graph()

    accounts = {
        "ACC-1001": ("Alice", "2018-01-15T00:00:00Z"),
        "ACC-1002": ("Bob", "2019-03-22T00:00:00Z"),
        "ACC-1003": ("Carol", "2017-11-05T00:00:00Z"),
        "ACC-1004": ("Dave", "2020-06-30T00:00:00Z"),
        "ACC-1005": ("Eve", "2024-02-01T00:00:00Z"),
        "ACC-1006": ("Frank", "2024-02-03T00:00:00Z"),
    }
    for acc_id, (owner, opened) in accounts.items():
        g.add_node(ts(opened), acc_id, properties={"owner": owner})

    # (src, dst, timestamp, amount, isFraud, device, txnId)
    transactions = [
        ("ACC-1001", "ACC-1002", "2026-08-01T09:00:00Z", 500, False, "dev-0001", "txn-1"),
        ("ACC-1002", "ACC-1003", "2026-08-02T11:30:00Z", 200, False, "dev-0002", "txn-2"),
        ("ACC-1003", "ACC-1004", "2026-08-03T14:15:00Z", 900, False, "dev-0001", "txn-3"),
        ("ACC-1004", "ACC-1005", "2026-08-04T08:45:00Z", 300, False, "dev-0004", "txn-4"),
        ("ACC-1005", "ACC-1006", "2026-08-05T02:10:00Z", 5000, True, "dev-0003", "txn-5"),
        ("ACC-1006", "ACC-1001", "2026-08-05T02:40:00Z", 4800, False, "dev-0003", "txn-6"),
    ]
    for src, dst, when, amount, is_fraud, device, txn_id in transactions:
        g.add_edge(
            ts(when),
            src,
            dst,
            properties={
                "amount": amount,
                "isFraud": is_fraud,
                "device": device,
                "txnId": txn_id,
            },
        )

    return g


if __name__ == "__main__":
    graph = build_graph()
    print(f"Built graph with {graph.count_nodes()} accounts and {graph.count_edges()} transaction edges.")
