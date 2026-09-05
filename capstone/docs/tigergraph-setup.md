# Getting TigerGraph Savanna connection details

What PaySentry needs, where each value comes from, and how to check it works.

You need exactly three values: **`TG_HOST`**, **`TG_GRAPH`**, **`TG_SECRET`**.

> Savanna's console changes between releases. The names below are what the
> current docs use; if a menu has been renamed, the thing to look for is
> described alongside each step rather than just its label.

## The one distinction that trips people up

Savanna has two different credentials with similar names:

| | What it authenticates | Works with pyTigerGraph? |
|---|---|---|
| **API key** | Savanna's *management* API — creating workspaces, billing, admin | **No** |
| **Database secret** | The *graph itself* — queries, loading, schema | **Yes** — this is what you need |

pyTigerGraph takes the database secret as its `gsqlSecret` argument. An API key
pasted into `TG_SECRET` will fail authentication, and the error will not say why.

## Step 1 — make sure the workspace is running

Signing up auto-provisions a workgroup and a workspace. In the Savanna console,
check the workspace shows **Active**.

Two things to confirm while you are there:

- It is a **Read/Write** workspace. Read-only workspaces cannot load data or
  install queries, and the free plan allows one read-write plus two read-only.
- Note whether **auto-suspend** is on (Workspace → Settings). Free workspaces
  suspend when idle, and the first connection afterwards fails or hangs while it
  wakes up — which looks exactly like a bad credential. `paysentry check` calls
  this out specifically.

## Step 2 — `TG_HOST`

**A workspace only has a hostname once it exists and is Active.** If the console
shows no workspace, or one still provisioning, there is nothing to copy yet —
that is the usual reason this value cannot be found. Create/resume it first.

The workspace endpoint always has the shape `https://<HOST_ID>.i.tgcloud.io`.
Two ways to see yours:

**a. Connect from API (the documented route).** Open the workspace, then the
**Connect** menu → **Connect from API**. That lists the workspace's REST
endpoints — built-in ones plus any query you have installed. Click any entry and
its **endpoint URL** is shown; the origin of that URL is `TG_HOST`.

> **Take only the host from this screen, not the authentication.** TigerGraph's
> own docs note that the code Savanna generates here *does not use the database
> secret* — the snippets authenticate with an API key/token instead. The secret
> comes from Step 4. Copying the whole snippet leads straight into the API-key
> vs. database-secret confusion described at the top of this page.

**b. The address bar.** Open **GraphStudio** or the **Admin Portal** for the
workspace and read the browser URL:

```
https://abcd1234.i.tgcloud.io/studio/...
        ^^^^^^^^^^^^^^^^^^^^^^^ this part is TG_HOST
```

Either way you can paste the whole URL into `TG_HOST` — scheme, path and port are
normalized automatically.

Cloud workspaces serve both REST++ and GSQL over 443, already the default in
`.env.example`.

> Do not confuse this with **`api.tgcloud.io`**, which appears in Savanna's REST
> docs. That is the *management* API — workgroups, workspaces, billing — and it
> is the one that uses API keys. Your graph lives on `<HOST_ID>.i.tgcloud.io`.

> On **Cloud Classic** (the older Solutions console) the equivalent is
> My Solutions → your solution → its **Domain**, giving the same
> `<subdomain>.i.tgcloud.io` shape.

## Step 3 — `TG_GRAPH`, and why it has to exist first

**A database secret is scoped to a graph, so the graph must exist before you can
create a usable secret.** That is a genuine chicken-and-egg with provisioning,
and the way through it is to create an empty graph by hand once.

In the workspace's **GSQL editor** (or GraphStudio):

```gsql
CREATE GRAPH PaySentry ()
```

The empty parentheses are deliberate — no vertex or edge types yet. PaySentry's
own `provision` command adds the schema into this graph later.

Set `TG_GRAPH=PaySentry`.

## Step 4 — `TG_SECRET`

Either route works:

**From the Admin Portal** — open the workspace's Admin Portal, go to **My
Profile**, and click the **`+`** beside Secrets. Choose the `PaySentry` graph.

**From the GSQL editor** — with the graph selected:

```gsql
USE GRAPH PaySentry
CREATE SECRET s1
```

Copy the value immediately. **It is displayed once** and cannot be retrieved
afterwards; if you lose it, drop it and create another.

Set `TG_SECRET=<the secret>`.

Leave `TG_USER` and `TG_PASSWORD` blank. Cloud workspaces do not use
username/password authentication — those exist in `.env` only for a self-hosted
TigerGraph.

## Step 5 — check it

```bash
cd capstone
cp .env.example .env      # then fill in the three values
.venv/bin/paysentry check
```

A good run:

```
  [PASS] credentials present: all set
  [PASS] hostname resolves: abcd1234.i.tgcloud.io
  [PASS] port 443 reachable: 84ms
  [PASS] authenticated: token acquired
  [PASS] query round-trip: TigerGraph 4.x
```

`check` stops at the first break and tells you which of the causes it was —
missing variable, unresolvable host, suspended workspace, wrong credential type,
or missing graph.

## Cost

`check`, `provision` and `load` all consume workspace uptime, which draws on free
credits. Keep using `--store local` for development; reach for `--store savanna`
only when you actually want the TigerGraph comparison. Leaving auto-suspend on is
the single most effective thing for making free credits last.

## Free-plan limits

From TigerGraph's published quota policy: max workspace size **TG-4**, one
read-write workspace, two read-only, one manual backup per workspace, no auto
backups. The **credit amount and expiry are not documented publicly** — read them
off the console and record them here for future reference:

```
Free credits observed at signup:  ____________   (fill in)
Expiry:                           ____________
Auto-suspend after idle:          ____________
```
