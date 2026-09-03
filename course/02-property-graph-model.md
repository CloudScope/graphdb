# Lesson 2 — Nodes, Relationships, Properties: The Property Graph Model

Lesson 1 introduced nodes, relationships, and properties at a high level. This lesson makes the model precise, because "graph database" is not one thing — the specific model here (the **property graph**) is what Neo4j, TigerGraph, and Raphtory all use, as opposed to the other major graph model (**RDF triple stores**, e.g. Ontotext GraphDB). Getting this model precise now pays off directly when we hit GSQL and Raphtory's schema later.

## The four building blocks

A property graph is built from exactly four things:

1. **Nodes** (also called vertices) — the entities. `Alice`, `Acme Corp`, `Product #4471`.
2. **Labels** — the *type(s)* of a node. Alice has label `Person`. A node can have more than one label (`Person`, `Employee`).
3. **Relationships** (also called edges) — the connections between two nodes. Always have exactly one **type**, and are always **directed**.
4. **Properties** — key/value pairs attached to *either* a node or a relationship.

That last point is the detail most newcomers underrate: **relationships can carry properties too.** This is what makes property graphs genuinely different from a simple "boxes and lines" diagram.

```
(Alice:Person {name: "Alice", age: 34})
        -[:WORKS_AT {since: 2021, role: "Engineer"}]->
(AcmeCorp:Company {name: "Acme Corp", founded: 1998})
```

Read that as: a node labeled `Person` with properties `name`/`age`, connected by a relationship of type `WORKS_AT` (which itself has properties `since`/`role`) to a node labeled `Company`.

## Why relationship properties matter architecturally

Go back to the relational junction-table example from Lesson 1 (`Friendships(user_id, friend_id)`). If you want to say *when* the friendship started, you add a `since` column to the junction table — workable, but the junction table was a workaround to express a many-to-many relationship in the first place, and now it's doing double duty as "relationship" and "relationship's data."

In a property graph, the relationship `since` property lives directly on the edge, because the edge is a first-class object with its own identity — not a synthetic row. This isn't just cleaner syntax. It means a query like "find every relationship where `since < 2020`" is a direct property filter on an edge, not a join. This becomes important once we get to Cypher and GSQL, both of which let you filter and even index on relationship properties.

## Labels vs. properties — how to decide

A common beginner mistake: putting everything as a property and never using labels, or the reverse. The rule of thumb:

- **Label** = "what kind of thing is this, for the purposes of writing queries and applying constraints/indexes." `Person`, `Company`, `Product`.
- **Property** = "a fact about this specific thing." `name`, `age`, `foundedYear`.

Ask: *would I ever want to query "give me all the X"?* If yes, X is probably a label. `MATCH (p:Person)` is a label-based query — fast, index-friendly, structural. `MATCH (p:Person {age: 34})` filters further with a property.

Labels aren't mutually exclusive — Alice can be `:Person:Employee:BoardMember` simultaneously. That's a different modeling lever than relational, where a row belongs to exactly one table.

## Relationship types are always directed — even when the relationship "feels" mutual

Every relationship has one direction in storage: `(Alice)-[:FRIENDS_WITH]->(Bob)`. Friendship feels symmetric in real life, but the graph engine still stores it as a directed edge. Two ways to handle this, and the choice matters:

- Query it ignoring direction (`MATCH (a)-[:FRIENDS_WITH]-(b)`, no arrow) when the relationship is genuinely symmetric.
- Store it as two directed edges (`Alice->Bob` and `Bob->Alice`) if you need to attach different properties per direction, or if your traversal patterns care about direction.

For relationships that are *not* symmetric — `MANAGES`, `PURCHASED`, `PARENT_OF` — direction is semantically load-bearing, not a storage artifact. Get this wrong in your model and every downstream query has to compensate.

## Multigraphs: more than one relationship between the same two nodes

Property graphs allow multiple relationships — even of the same type — between the same pair of nodes. Alice can `WORKS_AT` Acme Corp *and* `INVESTED_IN` Acme Corp — two separate edges, two separate sets of properties, no conflict. This is routine and expected, unlike a relational junction table where a duplicate `(user_id, friend_id)` row is usually a bug you'd constrain against.

## Schema: optional vs. enforced (foreshadowing Lesson 7)

Here's a detail that will matter a lot when we get to TigerGraph: the property graph *model* doesn't mandate a schema, but individual *engines* differ sharply in how they enforce one:

- **Neo4j**: schema-optional by default. You can create a node with any labels/properties you want, and add constraints/indexes incrementally as needed. Great for exploration — which is exactly why we're using it for hands-on labs.
- **TigerGraph**: schema-first. You define a graph schema (vertex types, edge types, and their property types) up front, and data must conform. This is closer to how you'd think about a relational schema — it's a deliberate design tradeoff TigerGraph makes for performance and validation at scale, and we'll unpack why when we get to Lesson 7.
- **Raphtory**: schema is inferred from whatever you ingest (typically a Python DataFrame or edge list) — closer to Neo4j's flexibility, but the "schema" question there is secondary to the *temporal* question — every node/edge/property in Raphtory can have a timestamp, which is a modeling dimension Neo4j and TigerGraph don't natively have. More in Lesson 8.

You don't need to act on this yet — just notice that "property graph model" is the shared vocabulary, but "how strict is the schema" is one of the first real axes these three products diverge on.

## Worked example — extending Lesson 1's social graph

```
(alice:Person {name:"Alice", age:34})
(bob:Person {name:"Bob", age:29})
(carol:Person {name:"Carol", age:41})
(acme:Company {name:"Acme Corp"})

(alice)-[:FRIENDS_WITH {since: 2019}]->(bob)
(bob)-[:FRIENDS_WITH {since: 2020}]->(carol)
(alice)-[:WORKS_AT {role: "Engineer", since: 2021}]->(acme)
(carol)-[:WORKS_AT {role: "Manager", since: 2015}]->(acme)
```

Notice what this buys you that a table diagram doesn't make obvious: you can already ask questions like *"find companies where a Manager and an Engineer are also friends-of-friends outside of work"* — a pattern that spans multiple relationship types and multiple hops — just by describing the shape. In Lesson 3, we'll write that as an actual Cypher query against a running Neo4j instance.

## Check your understanding

1. You're modeling a movie database: `Person`, `Movie`, and the fact that a person *acted in* a movie *as a specific character*. Where does "character name" belong — property on the `Person` node, property on the `Movie` node, or property on a relationship? (Think about why.)
2. Why might storing a symmetric relationship as two directed edges instead of one undirected-style edge sometimes be the *right* call, not just extra work?

We'll use both answers directly once we're writing Cypher in Lesson 3.
