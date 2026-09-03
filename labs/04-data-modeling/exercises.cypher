// Lab 4 exercises — reference queries for the four questions in the README.
// Try writing your own first; these are here to check against, not to copy blind.

// 1. Accounts Alice's account sent to, directly or within 2 account-hops.
// Note: each "account hop" is 2 graph edges (SENT then RECEIVED_BY), so
// 2 account-hops = up to 4 chained edges.
MATCH (accA:Account {id: "ACC-1001"})-[:SENT|RECEIVED_BY*1..4]->(reached)
WHERE reached:Account AND reached <> accA
RETURN DISTINCT reached.id;

// 2. Any transaction sharing a device with a known-fraudulent transaction.
MATCH (fraud:Transaction {isFraud: true})-[:VIA_DEVICE]->(d:Device)<-[:VIA_DEVICE]-(other:Transaction)
WHERE other <> fraud
RETURN other.id, other.amount, d.id AS sharedDevice;

// 3. Shortest (directed, following money flow) path from Dave's account to Alice's.
MATCH path = shortestPath(
  (accD:Account {id: "ACC-1004"})-[:SENT|RECEIVED_BY*]->(accA:Account {id: "ACC-1001"})
)
RETURN [n IN nodes(path) WHERE n:Account | n.id] AS accountChain, length(path) AS edgeHops;

// 4. Total amount sent per account, highest first.
MATCH (a:Account)-[:SENT]->(t:Transaction)
RETURN a.id, sum(t.amount) AS totalSent
ORDER BY totalSent DESC;

// 5. Try it yourself: using query 2's result and query 3's pattern, write a
//    query that finds the shortest path from Alice's account back to whichever
//    account originated the fraudulent transaction (Eve's). What does the
//    fact that this path exists at all suggest about Alice, if you were
//    investigating this as a fraud analyst?
