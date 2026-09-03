// Lab 5 setup — run this whole block in Neo4j Browser.
// Rebuilds Lab 4's financial network (safe to re-run — MERGE throughout),
// then does two new things:
//   1. Adds real indexes, so you can compare scan-vs-seek in PROFILE output.
//   2. Grows dev-0003 into an actual super-node with a batch of unrelated
//      "noise" transactions, so the anchoring exercises have a real
//      high-degree node to traverse through, not just a hypothetical one.

// --- indexes -----------------------------------------------------------
CREATE INDEX account_id IF NOT EXISTS FOR (a:Account) ON (a.id);
CREATE INDEX device_id IF NOT EXISTS FOR (d:Device) ON (d.id);
CREATE INDEX txn_isFraud IF NOT EXISTS FOR (t:Transaction) ON (t.isFraud);

// --- Lab 4's graph, unchanged ------------------------------------------
MERGE (alice:Person {name: "Alice"})
MERGE (bob:Person   {name: "Bob"})
MERGE (carol:Person {name: "Carol"})
MERGE (dave:Person  {name: "Dave"})
MERGE (eve:Person   {name: "Eve"})
MERGE (frank:Person {name: "Frank"})

MERGE (accA:Account {id: "ACC-1001"}) ON CREATE SET accA.openedDate = "2018-01-15"
MERGE (accB:Account {id: "ACC-1002"}) ON CREATE SET accB.openedDate = "2019-03-22"
MERGE (accC:Account {id: "ACC-1003"}) ON CREATE SET accC.openedDate = "2017-11-05"
MERGE (accD:Account {id: "ACC-1004"}) ON CREATE SET accD.openedDate = "2020-06-30"
MERGE (accE:Account {id: "ACC-1005"}) ON CREATE SET accE.openedDate = "2024-02-01"
MERGE (accF:Account {id: "ACC-1006"}) ON CREATE SET accF.openedDate = "2024-02-03"

MERGE (alice)-[:OWNS]->(accA)
MERGE (bob)-[:OWNS]->(accB)
MERGE (carol)-[:OWNS]->(accC)
MERGE (dave)-[:OWNS]->(accD)
MERGE (eve)-[:OWNS]->(accE)
MERGE (frank)-[:OWNS]->(accF)

MERGE (dev1:Device {id: "dev-0001"})
MERGE (dev2:Device {id: "dev-0002"})
MERGE (dev3:Device {id: "dev-0003"})
MERGE (dev4:Device {id: "dev-0004"})

MERGE (t1:Transaction {id: "txn-1"})
  ON CREATE SET t1.amount = 500, t1.timestamp = "2026-08-01T09:00:00Z", t1.isFraud = false
MERGE (accA)-[:SENT]->(t1)-[:RECEIVED_BY]->(accB)
MERGE (t1)-[:VIA_DEVICE]->(dev1)

MERGE (t2:Transaction {id: "txn-2"})
  ON CREATE SET t2.amount = 200, t2.timestamp = "2026-08-02T11:30:00Z", t2.isFraud = false
MERGE (accB)-[:SENT]->(t2)-[:RECEIVED_BY]->(accC)
MERGE (t2)-[:VIA_DEVICE]->(dev2)

MERGE (t3:Transaction {id: "txn-3"})
  ON CREATE SET t3.amount = 900, t3.timestamp = "2026-08-03T14:15:00Z", t3.isFraud = false
MERGE (accC)-[:SENT]->(t3)-[:RECEIVED_BY]->(accD)
MERGE (t3)-[:VIA_DEVICE]->(dev1)

MERGE (t4:Transaction {id: "txn-4"})
  ON CREATE SET t4.amount = 300, t4.timestamp = "2026-08-04T08:45:00Z", t4.isFraud = false
MERGE (accD)-[:SENT]->(t4)-[:RECEIVED_BY]->(accE)
MERGE (t4)-[:VIA_DEVICE]->(dev4)

MERGE (t5:Transaction {id: "txn-5"})
  ON CREATE SET t5.amount = 5000, t5.timestamp = "2026-08-05T02:10:00Z", t5.isFraud = true
MERGE (accE)-[:SENT]->(t5)-[:RECEIVED_BY]->(accF)
MERGE (t5)-[:VIA_DEVICE]->(dev3)

MERGE (t6:Transaction {id: "txn-6"})
  ON CREATE SET t6.amount = 4800, t6.timestamp = "2026-08-05T02:40:00Z", t6.isFraud = false
MERGE (accF)-[:SENT]->(t6)-[:RECEIVED_BY]->(accA)
MERGE (t6)-[:VIA_DEVICE]->(dev3);

// --- new: noise, all funneled through dev-0003 --------------------------
// 8 unrelated customers paying one shared merchant, all coincidentally from
// dev-0003 — a public kiosk, say. None of this is connected to the fraud
// ring except by sharing a device. This is what turns "a device two
// transactions share" into an actual super-node: after this block
// dev-0003 has 10 VIA_DEVICE edges, not 2.
MERGE (merchant:Person {name: "Kiosk Merchant"})
MERGE (accM:Account {id: "ACC-MERCHANT"}) ON CREATE SET accM.openedDate = "2015-05-01"
MERGE (merchant)-[:OWNS]->(accM)

WITH accM
UNWIND range(1, 8) AS i
MERGE (p:Person {name: "Noise" + i})
MERGE (a:Account {id: "ACC-NOISE-" + i}) ON CREATE SET a.openedDate = "2025-01-0" + (i % 9 + 1)
MERGE (p)-[:OWNS]->(a)
MERGE (t:Transaction {id: "txn-noise-" + i})
  ON CREATE SET t.amount = 10 + i, t.timestamp = "2026-08-06T10:0" + (i % 9) + ":00Z", t.isFraud = false
MERGE (a)-[:SENT]->(t)-[:RECEIVED_BY]->(accM)
MERGE (t)-[:VIA_DEVICE]->(:Device {id: "dev-0003"});
