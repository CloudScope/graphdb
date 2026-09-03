// Lab 3 exercises — run one at a time. Predict the result before running.
// Answers/explanations: answers.md in this folder.

// 1. Find every Person and their age.
MATCH (p:Person)
RETURN p.name, p.age;

// 2. Find only people older than 30.
MATCH (p:Person)
WHERE p.age > 30
RETURN p.name;

// 3. Who is Alice directly friends with?
MATCH (alice:Person {name: "Alice"})-[:FRIENDS_WITH]->(friend)
RETURN friend.name;

// 4. Who is Alice's friend-of-a-friend (2 hops), excluding Alice herself?
MATCH (alice:Person {name: "Alice"})-[:FRIENDS_WITH]->()-[:FRIENDS_WITH]->(fof)
WHERE fof <> alice
RETURN DISTINCT fof.name;

// 5. Everyone reachable from Alice within 1 to 3 friend-hops.
MATCH (alice:Person {name: "Alice"})-[:FRIENDS_WITH*1..3]->(person)
RETURN DISTINCT person.name;

// 6. Shortest friend-path between Alice and Dave, and how many hops it took.
MATCH path = shortestPath(
  (alice:Person {name: "Alice"})-[:FRIENDS_WITH*]-(dave:Person {name: "Dave"})
)
RETURN [n IN nodes(path) | n.name] AS chain, length(path) AS hops;

// 7. How many people work at each company, most-staffed first.
MATCH (p:Person)-[:WORKS_AT]->(c:Company)
RETURN c.name, count(p) AS employees
ORDER BY employees DESC;

// 8. For each movie, who acted in it and as which character?
// (This is the answer to Lesson 2's check question: `roles` lives on ACTED_IN,
//  not on Person or Movie, because it only makes sense per Person-Movie pairing.)
MATCH (actor:Person)-[r:ACTED_IN]->(movie:Movie)
RETURN movie.title, actor.name, r.roles;

// 9. Try it yourself: find every pair of actors who have appeared together in
//    the same movie (hint: two ACTED_IN relationships into the same Movie node).

// 10. Try it yourself: add a new Person and a new FRIENDS_WITH relationship
//     using MERGE instead of CREATE, then run it twice — confirm it doesn't
//     create a duplicate the second time.
