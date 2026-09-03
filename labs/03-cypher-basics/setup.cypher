// Lab 3 setup — run this whole block in Neo4j Browser against your AuraDB Free instance.

// --- Social graph (from Lessons 1-2) ---
CREATE (alice:Person {name: "Alice", age: 34})
CREATE (bob:Person   {name: "Bob",   age: 29})
CREATE (carol:Person {name: "Carol", age: 41})
CREATE (dave:Person  {name: "Dave",  age: 37})
CREATE (acme:Company {name: "Acme Corp", founded: 1998})

CREATE (alice)-[:FRIENDS_WITH {since: 2019}]->(bob)
CREATE (bob)-[:FRIENDS_WITH {since: 2020}]->(carol)
CREATE (carol)-[:FRIENDS_WITH {since: 2018}]->(dave)

CREATE (alice)-[:WORKS_AT {role: "Engineer", since: 2021}]->(acme)
CREATE (carol)-[:WORKS_AT {role: "Manager",  since: 2015}]->(acme)

// --- Small movie dataset (answers Lesson 2's "where does character name go" question) ---
CREATE (keanu:Person {name: "Keanu Reeves"})
CREATE (carrie:Person {name: "Carrie-Anne Moss"})
CREATE (matrix:Movie {title: "The Matrix", released: 1999})
CREATE (matrixReloaded:Movie {title: "The Matrix Reloaded", released: 2003})

CREATE (keanu)-[:ACTED_IN {roles: ["Neo"]}]->(matrix)
CREATE (carrie)-[:ACTED_IN {roles: ["Trinity"]}]->(matrix)
CREATE (keanu)-[:ACTED_IN {roles: ["Neo"]}]->(matrixReloaded)
CREATE (carrie)-[:ACTED_IN {roles: ["Trinity"]}]->(matrixReloaded);
