# Lesson 1 — Why Graph Databases Exist

## What a database does, at the core

A database's job is boring on the surface: store data, let you query it back efficiently, keep it correct under concurrent changes. The interesting part — the part that splits databases into "kinds" — is **how it organizes data on disk/memory to make certain queries fast**, because you cannot make every query fast. Every database design is a bet on which access patterns matter most for you.

## The relational model (quick primer)

The dominant model since the 1970s is **relational**: data lives in tables (rows and columns). Example — a tiny social app:

```
Users            Friendships
+----+-------+   +---------+----------+
| id | name  |   | user_id | friend_id|
+----+-------+   +---------+----------+
| 1  | Alice |   |    1    |    2     |
| 2  | Bob   |   |    2    |    3     |
| 3  | Carol |   |    1    |    3     |
```

To answer "who are Alice's friends," you `JOIN Users` to `Friendships`. Fine. One hop, cheap.

Now ask: **"who are Alice's friends-of-friends-of-friends, excluding people she already knows?"** That's a 3-hop traversal. In SQL, each hop is another self-join on `Friendships`. At 3 hops it's ugly but survivable. At 6 hops (realistic for "how are these two people connected" queries — LinkedIn's "2nd/3rd degree connections," fraud-ring detection), the query becomes a monster of nested joins, and — this is the important part — **it gets slower as the network grows**, because the database has to search for matching rows at each join step, and that search cost is tied to table size (even with indexes, it's `O(log n)` per lookup, repeated at every hop, across a combinatorially expanding set of candidates).

## Where relational actually breaks down

It's not that SQL *can't* do it. It's that:

1. The query gets exponentially harder to write and read as hops increase.
2. Performance degrades as the *dataset* grows, even though the *actual answer* (Alice's local neighborhood) didn't grow.
3. The relationship itself has no identity — in the table above, "Alice is friends with Bob" is just a row. If that relationship needs its own properties (since when? how strong? what type?), you're bolting more columns onto a junction table that was never designed to be a first-class thing.

This specific pattern — **deep, variable-length, relationship-heavy traversal** — is what graph databases are built for.

## The core idea of a graph database

Instead of rows in tables, you store:

- **Nodes** — the "things" (a Person, a Product, an Account)
- **Relationships** — the connections between them, stored as **direct physical pointers**, not looked-up-by-value like a foreign key
- **Properties** — key/value data on either nodes or relationships (Alice's `name`, or a `FRIENDS_SINCE: 2019` property on the relationship itself)

The consequence that matters architecturally: traversing a relationship in a graph DB is a **pointer hop**, not a search. Going from Alice → Bob → Carol → Dave costs roughly the same regardless of whether your database has 10,000 people or 10 billion people, because at each step the engine just follows a stored pointer to Bob's record — it never has to scan or index-search for "who is friends with Bob." This is called **index-free adjacency**, and it's the single most important technical idea that distinguishes a real graph database from "a relational database with a graph-shaped schema."

## The bet, summarized

| | Relational | Graph |
|---|---|---|
| Optimized for | Structured, tabular, aggregate queries ("total sales by region this quarter") | Deep relationship traversal, pattern-finding ("is this transaction part of a fraud ring," "shortest path between these two entities") |
| Cost of an N-hop traversal | Grows with dataset size (join cost) | ~Constant regardless of dataset size (pointer-follow) |
| Relationship identity | Implicit — a row in a join table | First-class — has its own type and properties |

## Check your understanding

Before Lesson 2, think about the real project you have in mind:

- Does the *shape* of the problem look like deep/variable-hop relationship traversal (recommendations, fraud rings, org charts of unknown depth, "how is X connected to Y")?
- Or is it more like "I have connected data but my queries are mostly 1–2 hops" — in which case a relational database with a couple of join tables may genuinely be the better call, and Lesson 7 (the decision framework) will make that case explicit.

There's no wrong answer here — part of thinking like an architect is being willing to *not* reach for the graph database.
