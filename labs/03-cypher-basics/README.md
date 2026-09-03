# Lab 3 — Cypher Basics on Neo4j AuraDB Free

Goal: get a real graph database running (free, no local install) and work through the Cypher patterns from [Lesson 3](../../course/03-cypher-fundamentals.md) against actual data.

## Step 1 — Create a free AuraDB instance

1. Go to https://neo4j.com/cloud/aura-free/ and sign up (or log in).
2. Create a new **free** instance (AuraDB Free — no credit card required, one instance, capped size, plenty for this course).
3. **Important:** when the instance is created, Aura shows you a username/password and a connection URI **once**. Download the credentials file it offers, or copy them somewhere safe — you can't retrieve the password again later (you'd have to reset it).
4. Wait for the instance status to show "Running," then click it to open the **Neo4j Browser** (or "Query" tab in the Aura console) — that's a browser-based UI for running Cypher directly.

## Step 2 — Load the sample data

Open [`setup.cypher`](setup.cypher) in this folder, copy its contents, and paste/run it in the Neo4j Browser query box. It builds:

- The social graph from Lessons 1–2 (Alice, Bob, Carol, Acme Corp — friendships and employment)
- A small movie dataset (Person/Movie/`ACTED_IN` with a `roles` property) — this directly answers Lesson 2's check-your-understanding question about where "character name" belongs

You should see a confirmation of nodes/relationships created. Run `MATCH (n) RETURN n` afterward to see the whole graph rendered visually — Neo4j Browser draws the graph, which is worth doing at least once to build intuition.

## Step 3 — Work through the exercises

Open [`exercises.cypher`](exercises.cypher) and run each query one at a time against your instance. Try to predict the result before running each one. If you get stuck, [`answers.md`](answers.md) has worked explanations — but try first.

## Step 4 — Answering Lesson 2's check questions, for real

Query 8 in the exercises file directly demonstrates why "character name" is a **relationship property** (on `ACTED_IN`), not a property of `Person` or `Movie` — a person can act in multiple movies as different characters, and a movie has multiple actors with different characters, so the fact only makes sense pinned to the specific Person–Movie pairing. Run it and look at the result shape.

## When you're done

Keep the AuraDB instance running (free tier has no time limit, just a size cap) — we'll reuse it for Lesson 4 (data modeling exercises) and Lesson 5 (indexing).
