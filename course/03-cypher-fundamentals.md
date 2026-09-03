# Lesson 3 — Cypher Fundamentals

Cypher is Neo4j's query language. We're learning it for two reasons: it's the gentlest on-ramp to graph thinking, and it directly informs how you'll read GSQL (TigerGraph, Lesson 7) later — GSQL is more verbose and procedural, but the *pattern-matching* mental model you build here carries over directly.

## The one idea Cypher is built around

SQL describes tables and joins. Cypher describes **the shape you're looking for**, using ASCII-art that mirrors the property graph diagrams from Lesson 2:

```
(a)-[:REL]->(b)
```

That's a node `a`, a relationship of type `REL`, pointing to node `b`. You write patterns that look like the thing you're searching for, and Cypher finds every match in the graph. This is the core shift from relational thinking: **you don't specify how to join — you specify the shape, and the engine finds it** (using index-free adjacency under the hood, from Lesson 1).

## Core syntax, piece by piece

### Creating data

```cypher
CREATE (alice:Person {name: "Alice", age: 34})
CREATE (bob:Person {name: "Bob", age: 29})
CREATE (alice)-[:FRIENDS_WITH {since: 2019}]->(bob)
```

Round brackets `()` = node. Square brackets `[]` = relationship. Labels after `:`. Properties in `{}` — same syntax on both nodes and relationships, because both can carry properties (Lesson 2).

### Reading data — MATCH / RETURN

```cypher
MATCH (p:Person)
RETURN p.name, p.age
```

`MATCH` finds every occurrence of the pattern. `RETURN` says what to project out. This is the graph equivalent of `SELECT ... FROM`, except the "from" is a shape, not a table.

### Filtering — WHERE

```cypher
MATCH (p:Person)
WHERE p.age > 30
RETURN p.name
```

### Following relationships — the part tables can't do cleanly

```cypher
MATCH (alice:Person {name: "Alice"})-[:FRIENDS_WITH]->(friend)
RETURN friend.name
```

Multi-hop is just... a longer pattern:

```cypher
MATCH (alice:Person {name: "Alice"})-[:FRIENDS_WITH]->()-[:FRIENDS_WITH]->(fof)
RETURN DISTINCT fof.name
```

Two hops, written as two arrows. No nested joins, no aliasing gymnastics — this is the payoff Lesson 1 promised.

### Variable-length paths

What if you don't know how many hops ahead — you want "anyone reachable within 1 to 3 friend-hops"?

```cypher
MATCH (alice:Person {name: "Alice"})-[:FRIENDS_WITH*1..3]->(person)
RETURN DISTINCT person.name
```

`*1..3` means "1 to 3 relationships of this type, chained." This is the single Cypher feature with no clean relational equivalent — in SQL this requires a recursive CTE, and it gets significantly more awkward the deeper or more variable the traversal.

### Shortest path

```cypher
MATCH path = shortestPath(
  (alice:Person {name: "Alice"})-[:FRIENDS_WITH*]-(carol:Person {name: "Carol"})
)
RETURN path
```

Straight to "how are these two connected, minimally" — a query type that comes up constantly in fraud/investigation use cases (and is a good preview of why TigerGraph markets itself heavily on deep-link analytics).

### Upserting — MERGE

`CREATE` always inserts, even if it's a duplicate. `MERGE` matches an existing pattern if one exists, or creates it if not — the graph equivalent of "insert or update."

```cypher
MERGE (bob:Person {name: "Bob"})
ON CREATE SET bob.age = 29
```

### Aggregation

```cypher
MATCH (p:Person)-[:WORKS_AT]->(c:Company)
RETURN c.name, count(p) AS employees
ORDER BY employees DESC
LIMIT 5
```

`count()`, `collect()` (gather matches into a list), `avg()`, `sum()` — same idea as SQL aggregates, applied over matched patterns instead of grouped rows.

### Indexes and constraints (a preview of Lesson 5)

```cypher
CREATE INDEX person_name IF NOT EXISTS FOR (p:Person) ON (p.name)
CREATE CONSTRAINT person_name_unique IF NOT EXISTS FOR (p:Person) REQUIRE p.name IS UNIQUE
```

Indexes here matter for a specific reason: they speed up the *first* node you find in a pattern (the "anchor"). Once the engine has that anchor node, everything after it is pointer-hops (index-free adjacency), not more index lookups. We'll dig into this properly in Lesson 5.

### Deleting

```cypher
MATCH (p:Person {name: "Bob"})
DETACH DELETE p
```

`DETACH DELETE` removes the node *and* any relationships attached to it — plain `DELETE` on a node with relationships will error, by design, so you don't silently leave dangling edges.

## Cheat sheet

| Want to... | Cypher |
|---|---|
| Insert | `CREATE (n:Label {prop: val})` |
| Insert or reuse | `MERGE (n:Label {prop: val})` |
| Find all of a type | `MATCH (n:Label) RETURN n` |
| Filter | `MATCH (n:Label) WHERE n.prop > x RETURN n` |
| Follow a relationship | `MATCH (a)-[:REL]->(b) RETURN b` |
| N-hop, unknown depth | `MATCH (a)-[:REL*1..3]->(b) RETURN b` |
| Shortest connection | `MATCH p = shortestPath((a)-[:REL*]-(b)) RETURN p` |
| Aggregate | `MATCH (a)-[:REL]->(b) RETURN b.x, count(a)` |
| Delete safely | `MATCH (n) DETACH DELETE n` |

## Hands-on lab

Head to [`../labs/03-cypher-basics/README.md`](../labs/03-cypher-basics/README.md) — it walks through getting a free Neo4j AuraDB instance running and working through these patterns against real data, including a direct answer to the "where does the character name go" question from Lesson 2's check-your-understanding.
