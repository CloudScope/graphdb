// Lab 5 exercises — reference queries for the README's PROFILE walkthrough.
// Run each PROFILE query in Neo4j Browser and read the plan it prints, not
// just the returned rows — the plan is the actual point of this lab.

// 1. Indexed anchor vs. unindexed anchor.
// account_id has an index (from setup.cypher); openedDate does not.
// Compare the two plans: look for "NodeIndexSeek" vs. "NodeByLabelScan" as
// the first operator, and compare the "Rows"/"DB Hits" columns.
PROFILE
MATCH (a:Account {id: "ACC-1001"})
RETURN a;

PROFILE
MATCH (a:Account {openedDate: "2018-01-15"})
RETURN a;

// 2. Confirm dev-0003 is actually a super-node now.
MATCH (d:Device)<-[:VIA_DEVICE]-(t:Transaction)
RETURN d.id, count(t) AS degree
ORDER BY degree DESC;

// 3. Targeted (anchor on the one known fraud transaction) vs. broad (anchor
// on every transaction, then look for device-sharing pairs) — same
// question ("what shares a device with what"), two traversal shapes.
// Compare "DB Hits" and, this time, the row counts too: the broad version
// re-derives every pairing dev-0003's 10 transactions produce with each
// other, not just the one pairing you actually care about.
PROFILE
MATCH (fraud:Transaction {isFraud: true})-[:VIA_DEVICE]->(d:Device)<-[:VIA_DEVICE]-(other:Transaction)
WHERE other <> fraud
RETURN other.id, d.id AS sharedDevice;

PROFILE
MATCH (t1:Transaction)-[:VIA_DEVICE]->(d:Device)<-[:VIA_DEVICE]-(t2:Transaction)
WHERE t1 <> t2
RETURN t1.id, t2.id, d.id AS sharedDevice;

// 4. Bounded vs. unbounded variable-length traversal.
// Both should find the same path here (the graph is small) — the point is
// to compare "DB Hits" between an open '*' and a bounded '*1..6' on a graph
// that now has a real super-node sitting off to the side of the real path.
PROFILE
MATCH path = shortestPath(
  (accD:Account {id: "ACC-1004"})-[:SENT|RECEIVED_BY*]->(accA:Account {id: "ACC-1001"})
)
RETURN length(path) AS edgeHops;

PROFILE
MATCH path = shortestPath(
  (accD:Account {id: "ACC-1004"})-[:SENT|RECEIVED_BY*1..6]->(accA:Account {id: "ACC-1001"})
)
RETURN length(path) AS edgeHops;

// 5. Try it yourself: using EXPLAIN (not PROFILE — you don't need to run it),
//    predict which operator Neo4j will choose first for this query, and why,
//    before checking. Hint: which side is more selective?
EXPLAIN
MATCH (t:Transaction {isFraud: true})-[:VIA_DEVICE]->(d:Device)
RETURN d;
