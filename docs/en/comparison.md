# reactifact vs. LangGraph / CrewAI / plain function calls

This page is a comparison, not a pitch. reactifact is pre-1.0 (`0.12.0`), the
ecosystem is one maintainer, and there is no hosted platform, no managed
tracing SaaS, no marketplace of pre-built agents. If any of those are what you
need today, the honest answer is: use LangGraph or CrewAI, they're mature and
well-supported. Read on if the comparison below still tips your way.

![Left: a hand-wired fetch → verify → answer pipeline. Right: reactifact — search_agent and answer_agent each declare only what they consume and produce, wired together by Context, never each other.](../img/wiring.svg)

## TL;DR

| | LangGraph | CrewAI | reactifact |
| --- | --- | --- | --- |
| Primary abstraction | explicit state graph (nodes + edges) | role-based crew of agents | typed artifacts + reactive agents |
| Control flow | you draw it | mostly fixed (sequential/hierarchical) | derived from state changes |
| State | a shared, loosely-typed dict/`TypedDict` | task outputs passed along | versioned, typed, immutable-per-version artifacts |
| "Why did it say that?" | requires manual logging/checkpoint inspection | not tracked by default | provenance graph (`supported_by`/`derived_from`/…) built in |
| Numbers/calculations | the LLM computes unless you write a tool | same | recipes push calculation into deterministic code, not the model |
| Rollback / branching | checkpointer + manual replay logic | not built in | `context.branch()`, three-way `merge()`, deterministic replay |
| Maturity / ecosystem | high — used in production widely | high — large community | pre-1.0, one maintainer, small examples set |
| Managed hosting | LangGraph Platform | CrewAI Enterprise | none |
| MCP | via LangChain adapters | via CrewAI adapters | client + server built in (`reactifact.mcp`, `mcp` extra) |

## Where reactifact is *not* the right choice

Being upfront about this matters more than the feature table:

- **You have a genuinely fixed pipeline.** If the steps are always A → B → C
  with no branching by data, a graph framework (or a plain function) is less
  ceremony than modeling artifacts and consumes/produces.
- **You need a managed platform today** — hosted execution, a UI for
  non-engineers, an enterprise support contract. reactifact is a library; there is
  no SaaS behind it.
- **You need a large pre-built agent/tool ecosystem.** LangGraph and CrewAI
  both have more third-party integrations, more Stack Overflow answers, more
  production war stories. reactifact's `Source` abstraction is intentionally
  small (filesystem, CSV, embeddings, web) — you write the rest. `reactifact.mcp`
  ([docs](mcp.md)) reaches any MCP server as a `Tool`, which narrows this for
  tool-calling specifically, but doesn't touch `Source`/retrieval integrations.
- **Your team already has deep LangGraph investment.** Rewriting a working
  system for architectural purity is rarely worth it. reactifact is a better fit
  for a *new* agent, not necessarily a migration target for an old one.

## Where the difference actually matters

### 1. The state model: a dict vs. typed, versioned artifacts

LangGraph's state is a shared dict (or `TypedDict`) that every node can read
and mutate. That's flexible, but it means "what shape does the state have
right now" is a runtime fact, not something the type checker can verify, and
"who last touched this field" is not tracked unless you add it yourself.

reactifact artifacts are pydantic models. Every artifact has a type, an id, a
version, and a `created_at`. Nothing is deleted-and-overwritten in place — an
`Update` produces a *new* version, so `context.diff(v1, v2)` is a real,
inspectable operation, not something you have to reconstruct from logs.

### 2. Control flow: drawn graph vs. reactive dispatch

A LangGraph graph is the orchestration: you write `add_edge`, `add_conditional_edges`,
you decide by hand which node can follow which. That's the right model when
there genuinely is one path. It stops being the right model once the paths
multiply — a real knowledge-agent question ("why did infra costs jump in Q2?")
might need Confluence *and* GitLab *and* a CSV calculation *and* human
confirmation, and the *next* question needs a different subset. Encoding every
combination as graph edges turns into a combinatorial wiring exercise.

reactifact agents declare `consumes`/`produces` — what artifact types they react
to and what they can create. The runtime derives execution from **which
artifacts actually exist**, not from a pre-declared path. Two agents that have
never heard of each other compose correctly as long as one produces what the
other consumes. This is also why there's no `viz.blueprint()` node-and-edge
picture to keep in sync by hand — see [Why reactifact](why-reactifact.md) for the
full argument.

### 3. Provenance: bolt-on vs. built-in

"Why did the agent answer this?" in LangGraph/CrewAI usually means reading
logs, message history, or a custom trace you added yourself. There's no
first-class notion of "this Answer artifact is *derived from* this Evidence,
which was *extracted from* this Doc."

In reactifact, `effects.create(...).link("supported_by", evidence)` is a normal
part of writing a produce, and the resulting graph is queryable:
`context.related(answer.id, "supported_by")`, or rendered as a Mermaid graph
via `context_to_mermaid()`. This isn't a logging add-on — it's the same
mechanism the runtime uses to decide what to re-run after a rollback.

### 4. Determinism: the model computes vs. the model reasons

Ask an LLM to compute `sum(gpu_cost) / sum(total_cost)` inline in a chat
completion and it will confidently produce a number that is sometimes wrong —
this is not a prompting problem, it's what generative token-by-token math
does. Both LangGraph and CrewAI leave this entirely up to you (write a tool,
remember to call it, remember to trust its output over the model's).

reactifact's design bias — not a hard rule — is that a `Produce` doing arithmetic
over structured data (a CSV, a query result) should compute it in plain Python
and hand the LLM the *result* to explain, not the raw numbers to guess from.
The [`knowledge` example](examples.md) does exactly this: the LLM writes the
prose, `calc.py` does the arithmetic.

### 5. Rollback and branching: checkpointer vs. git-like context

LangGraph has checkpointers for persistence and time-travel through
checkpoint history. reactifact's `Context` is versioned more literally: you can
`context.branch()` to fork a parallel exploration, run different agents on
each fork, and three-way `merge()` them back — see
[Branching](branching.md) and the `forklab` example, which runs two
strategies on their own forks and merges the result.

## If you're evaluating both

A reasonable trial: take one agent from your LangGraph/CrewAI project that
does non-trivial branching (not a fixed 3-step pipeline) and port just that
one to reactifact. If the artifact model and reactive dispatch make that agent's
logic *shorter and more honest about failure* (no silent wrong-number
hallucination, real "why" for its answer), the rest of the system is probably
worth porting too. If it mostly adds ceremony for a genuinely linear flow,
that agent was never the graph-heavy case reactifact is built for.

## Starting from scratch (nothing to port)

The trial above assumes an existing graph to compare against. If you don't
have one, the equivalent test doesn't need a pre-existing project — it needs
a *third* thing to wire in after the fact:

1. Pick a question that genuinely needs two independent things combined (a
   CSV number and a doc lookup, say — not a fixed A→B pipeline).
2. Write it as two `Produce`s that each declare only what they `consume`/
   `produce` — the [front-page example](index.md)'s two-agent snippet
   (`find_evidence`/answer) is the shape. Neither should import or reference
   the other by name.
3. Now add a *third* agent for a follow-up question that needs a different
   subset of the same data — without touching the first two agents' code.

If step 3 composes cleanly, that's the actual claim on this page, not a
marketing line — [open a discussion](https://github.com/bzdvdn/reactifact/discussions)
with what you built (or where it didn't compose the way this page claims);
that's a faster way to get a straight answer than reading more docs, and it's
exactly the kind of report that makes this comparison more honest over time.
