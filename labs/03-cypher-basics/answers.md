# Lab 3 — Answers & Explanations

**1.** Returns all 6 people with ages (Keanu/Carrie-Anne have no `age` property set — they'll show as `null`, which is expected: property graphs don't require every node of a label to have the same properties).

**2.** Alice (34), Carol (41), Dave (37). Bob (29) filtered out.

**3.** Just `Bob` — Alice only has one outgoing `FRIENDS_WITH` edge. Note this is *directed*: if you queried `(bob)-[:FRIENDS_WITH]->(friend)` you'd get Carol, not Alice, even though "friendship" feels mutual. This is the Lesson 2 directionality point made concrete — worth re-reading that section if this result surprised you.

**4.** `Carol` — Alice → Bob → Carol. The `WHERE fof <> alice` guard matters once chains get longer or cyclic; without it you can end up looping back to your start node and returning it as its own "friend of a friend."

**5.** `Bob`, `Carol`, `Dave` — everyone in the chain, because it's all one direction (Alice→Bob→Carol→Dave) and `*1..3` covers all three hops from Alice.

**6.** `chain: ["Alice", "Bob", "Carol", "Dave"]`, `hops: 3`. Note the undirected pattern (`-[:FRIENDS_WITH*]-`, no arrowhead) — `shortestPath` here ignores direction, which is the right call for "how are these two connected at all," as opposed to exercise 3 where direction was the point.

**7.** `Acme Corp: 2` (Alice and Carol).

**8.** Four rows — Keanu/Carrie-Anne × two movies each, with `r.roles` giving `["Neo"]` or `["Trinity"]` depending on the row. This is the direct payoff: the *same two people* have *different facts* depending on *which relationship* you're looking at (a role only makes sense in the context of one specific Person–Movie pairing) — that's exactly why it's a relationship property, not a node property. If you'd put `role: "Neo"` on the `keanu` node instead, it would be wrong the moment he's in a second movie with a different character, or right only by accident if the character name happened to repeat.

**9.** One way to write it:

```cypher
MATCH (a1:Person)-[:ACTED_IN]->(m:Movie)<-[:ACTED_IN]-(a2:Person)
WHERE a1.name < a2.name
RETURN a1.name, a2.name, collect(m.title) AS movies_together;
```

The `WHERE a1.name < a2.name` trick avoids returning both `(Keanu, Carrie-Anne)` and `(Carrie-Anne, Keanu)` as separate rows — a common pattern for deduplicating symmetric pairs matched via two independent relationship traversals into a shared node.

**10.**

```cypher
MERGE (eve:Person {name: "Eve"})
MERGE (bob:Person {name: "Bob"})
MERGE (bob)-[:FRIENDS_WITH {since: 2022}]->(eve)
```

Run it once — creates Eve and the relationship. Run it again — `MERGE` matches the existing pattern (same labels/properties used in the `MERGE` clause) and does nothing new, so no duplicate node or relationship appears. Contrast with `CREATE`, which would create a second Eve and a second relationship every time you ran it — this is why `MERGE` is the safer default for idempotent setup scripts (like `setup.cypher` in this lab, which deliberately uses `CREATE` since it's meant to run exactly once against an empty database).
