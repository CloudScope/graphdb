# Lab 4 — Modeling and Querying the Financial Network

Goal: build the financial network model from [Lesson 4](../../course/04-graph-data-modeling.md) in your AuraDB instance from Lab 3, then write the four driving queries yourself before checking the answer key.

This reuses the `Person` nodes (Alice, Bob, Carol, Dave) from Lab 3 and adds `Account`, `Transaction`, and `Device` nodes on top — same instance, richer model. If you never got Lab 3's data loaded, don't worry: [`setup.cypher`](setup.cypher) uses `MERGE` for the people, so it creates them if they're missing and reuses them if they're already there.

## Step 1 — Load the model

Run [`setup.cypher`](setup.cypher) in Neo4j Browser. It builds:

- 6 people (Alice, Bob, Carol, Dave, plus two new ones — Eve and Frank) each owning one `Account`
- 6 `Transaction` nodes connecting accounts (the n-ary-relationship-as-node pattern from Lesson 4), each with an `amount`, a `timestamp`, and a `VIA_DEVICE` link
- One transaction (`Eve → Frank`) flagged `isFraud: true`, sharing its device with another transaction (`Frank → Alice`) — a deliberate device-sharing pattern to query against

Run `MATCH (n) RETURN n` afterward and look at the picture — you should see the financial subgraph hanging off the people you already had.

## Step 2 — Write the four queries yourself

Before opening [`exercises.cypher`](exercises.cypher), try writing these from scratch, straight from Lesson 4's "worked example" section:

1. Accounts Alice's account sent to, directly or within 2 hops.
2. Any transaction sharing a device with the known-fraudulent transaction.
3. Shortest path (in terms of `SENT`→`RECEIVED_BY` hops) between Dave's account and Alice's account.
4. Total amount sent, per account, highest first.

Then open `exercises.cypher` to check your queries against the reference versions, and `answers.md` for why each is shaped the way it is.

## Step 3 — Answer Lesson 4's check-your-understanding questions

Lesson 4 asked whether `Prescription` should be a node or relationship in a hospital model, and whether a super-node `Device` in the fraud pattern is a mistake or the point. `answers.md` covers both directly — but form your own answer first using the reasoning from the lesson before reading it.
