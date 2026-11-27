# Recipes

`reactifact.recipes` contains reusable building blocks that encode patterns which
recur in every demo. Importing `reactifact.recipes` does not pull in additional
dependencies — everything is built on the core.

## `find` / `find_all` — locate typed artifacts in `inputs`

A step whose agent declares more than one `Consume` type receives a flat
`list[Artifact[Any]]`; picking out "the one Question" or "all the Evidence"
is the same `next((a for a in inputs if isinstance(a.data, X)), None)` in
nearly every step. `find`/`find_all` are a typed one-liner for it:

```python
from reactifact.recipes import find, find_all

question = find(inputs, Question)          # Artifact[Question] | None
evidence = find_all(inputs, Evidence)       # list[Artifact[Evidence]]
```

## `search_sources` / `fan_out_sources` — reactive search

`search_sources` is the pure part: it fans out and returns ranked, scoped refs
without touching the effect slot. Use it when the step wants to create the refs
itself (for example, return a list of `Emit`s):

```python
from reactifact import Emit
from reactifact.recipes import search_sources, source_ref_id

query = question.data.text
refs = await search_sources(context, query, owner_id=query_id, limit=5)
return [
    Emit(ref, id=source_ref_id(ref, query_id)) for ref in refs
]
```

`fan_out_sources` is the effect-slot wrapper: it does the same search and also
writes each ref as a create-or-refresh artifact. Pass `effects=` to target an
explicit slot instead of the current produce's.

```python
from reactifact.recipes import fan_out_sources

refs = await fan_out_sources(
    context,
    query=query,
    owner_id=query_id,           # scope refs to the owning turn
    limit=5,
    query_id=query_id,           # owner key for provenance
    on_start=lambda source: context.announce(
        f"Searching {source}…", kind="status"
    ),
    on_count=lambda source, n: context.announce(
        f"{source}: {n} hits", kind="status"
    ),
)  # each SourceRef is created in the current produce's effects (or effects=)
```

Pass `as_artifacts=True` to get the planned `Handle`s instead of the data — the
shape `materialize_doc` accepts in the same produce.

What it does:

1. Fans out to **every configured source** in `context.resources.sources`.
2. Ranks the combined `SourceRef`s by score (highest first).
3. Creates **idempotent, owner-scoped refs** (effect `Create`) —
   `id=f"ref:{ref.stable_id()}:{owner_id}"`.

Idempotency matters: the caller runs a search once per turn (guarded by its own
marker) but may *re-run* on retries; the same ids are re-created, never
duplicated. `on_start` / `on_count` give you progress announces over SSE.

## `MemoryStore` — namespaced long-term memory (§27)

```python
from reactifact.recipes import MemoryStore

memory = MemoryStore(backend, namespace=f"user:{user_id}")
await memory.remember(
    "prefers concise answers with citations",
    value={"tone": "concise"},
    tags={"kind": "preference"},
    ttl_seconds=90 * 24 * 3600,
)
hits = await memory.recall("how should answers be written?", k=3)
```

Namespaces are mandatory (or set on the store), so a recall cannot read another
user/workspace; `tags` filter, `ttl_seconds` expires on read/list/search, and
`recall` is deterministic keyword ranking (`keyword_score`, stems/plurals) with
a pluggable `searcher(record, query)` for semantic recall. `remember` derives a
stable key from the text unless you pass one. `forget()` clears a namespace.
Records persist through any `KVBackend` (file/SQLite/Postgres); `put`/`get`/
`list`/`search` remain the lower-level engine API.

A store is a **cross-session index, not a copy of the chat**: sessions and
checkpoints stay the source of truth. `remember_artifact(artifact, key=…)`
indexes a curated artifact (a report, a preference) and records its
`source_session` / `source_artifact` / `source_version`, so the entry can be
rebuilt or erased later:

```python
record = await memory.remember_artifact(
    report,
    key=f"incident:{report.id}",
    source_session=session_id,          # explicit: request/UserMsg/app state
    text=report.data.text[:800],
    tags={"kind": "report", "system": "k8s"},
)
assert record.source_artifact == report.id
removed = await memory.forget_source(record.source_session)   # GDPR-style
```

`source_session` is a required explicit argument — provenance is data, not
ambient runtime state. Pass the session from the request/user artifact, or `""`
when the memory is not tied to one; `forget_source("")` is rejected so an empty
key can never mass-delete sessionless records.

When the write must happen **after** the artifact commits, return the
instruction from a step; the recipe owns the action payload and the dispatcher:

```python
from reactifact import Artifact, Ensure, PendingAction, step

@step(produces=PendingAction, reads=Report)
async def remember(report: Artifact[Report]) -> Ensure:
    return memory.remember_later(
        report,
        key=f"incident:{report.id}",
        source_session=session_id,
    )

# app: Runtime(..., dispatcher=MemoryStore.dispatch)
```

`remember_later` returns `Ensure(PendingAction)` — the runtime writes it like
any other return-style step result, with no `Effects`/`Outbox` at the call
site. `MemoryStore.dispatch` resolves the registered store from
`context.resources` and performs the write after the commit. Unknown action
kinds are ignored, so it can be composed with other dispatchers.
`remember_after_commit(effects, …)` remains the compatibility spelling.

## `map_reduce` — fan out typed tasks, then reduce (§24)

```python
from reactifact.recipes import map_reduce

result = await map_reduce(
    context,
    items=docs,
    map_fn=lambda payload, index: summarize(payload),
    reduce_fn=lambda rows: merge(rows),
    key="summaries",
    concurrency=8,
    on_error="collect",
)
```

Each item becomes a stable `MapTask` (`map-task:{key}:{i}`); a worker turns it
into a `MapResult`, and a debounced `ReduceStep` runs once all results for the
key exist (`map-reduced:{key}`). `on_error="collect"` turns a failing item into
a `MapResult(error=...)` and reduces the rest; `"fail_fast"` propagates. The
child run has its own commit DAG (`result.context`), reuses the parent
tracer/budget, and stamps `trace_metadata.parent_run_id`.

## `swarm` — peer-to-peer handoffs with a guard (§24)

```python
from reactifact.recipes import Handoff, swarm

result = await swarm(
    context,
    agents={"triage": triage, "billing": billing},
    start="triage",
    input=Question(text="refund please"),
    final_type=Answer,
    max_hops=6,
)
```

An agent creates a `Handoff(to=..., reason=..., payload=...)`; the helper runs
that agent from the handoff, records the hop as a `Handoff` artifact linked to
the previous step (`handoff_from`), and stops at the first non-handoff output
(linked `derived_from`). `max_hops` is a hard cap; `allow_repeat=False` also
rejects revisiting an agent (`SwarmCycle`/`SwarmLimit`). Each hop is a normal
subflow, so trace/budget/provenance carry over.

## `materialize_doc` — lazy reference resolution

```python
from reactifact.recipes import fan_out_sources, materialize_doc

# `as_artifacts=True` returns planned handles; `materialize_doc` accepts them
refs = await fan_out_sources(
    context, "gpu costs", owner_id="q1", as_artifacts=True
)
doc = await materialize_doc(
    context,
    refs[0],
    doc_type=TypedDoc,
    text_field="content",
    field_map={"path": "locator"},
)
```

The short form builds the document from `doc_type=`, filling `text_field` with
the resolved content and matching fields from the ref (`locator`, `source_id`,
`title`, `query_id`, `score`, `metadata`). For anything custom, keep the
factory form:

```python
async def doc_from_ref(context, ref, content) -> TypedDoc:
    return TypedDoc(query_id=ref.data.query_id, path=..., content=content)

doc = await materialize_doc(
    context, refs[0], doc_from_ref, relation="resolved_from"
)
```

`materialize_doc` accepts an `Artifact[SourceRef]`, a plain `SourceRef`, or a
`Handle` from `as_artifacts=True`. An already-resolved `resolved:{ref_id}` doc
is reused with `refresh=False` (the default re-fetches, preserving the old
behaviour); a missing source or resolve failure
yields `None` and calls `on_error(ref, exc)`, never a crash.

Because sources return *references*, not payloads, documents are fetched
**lazily, only when needed** — the rule in the research demo is "resolve a page
only after the model ranks it relevant".

The produced document is linked back:
`TypedDoc ──resolved_from──► SourceRef` (§34), so the provenance walk
`Answer → Claim → Evidence → Doc → SourceRef` stays complete.

## `StatusMachine` — deterministic lifecycles

Status machines are the pattern for "an artifact that moves through states"
(a research turn: `researching → answerable → answered`). Instead of a manual
transition graph, a `StatusMachine` is a **pure function of current state**:

```python
from reactifact.recipes import StatusMachine, StatusState


class EvaluateTurn(StatusMachine[ResearchTurn]):
    terminal = frozenset({"answered", "insufficient"})  # where it stops
    query_id_field = "query_id"                  # owner key field (default)
    status_field = "status"                      # status field (default)

    def next_status(self, state: StatusState[ResearchTurn]) -> str | None:
        """Pure function: which status the lifecycle deserves now."""
        if state.any(Answer):
            return "answered"
        return None

    def on_transition(self, context, key, old_status, new_status) -> None:
        context.announce(f"Research status: {old_status} → {new_status}",
                         kind="status", query_id=key)
```

For a one-method machine, `StatusMachine.from_funcs(...)` avoids the subclass:

```python
machine = StatusMachine.from_funcs(
    artifact_type=ResearchTurn,
    terminal={"answered", "insufficient"},
    next_status=lambda state: "answered" if state.any(Answer) else None,
)
```

The constructor form takes the same options:
`StatusMachine(artifact_type=..., next_status=..., terminal=..., reads=...)`.

Mechanics (all inherited from `Step`):

- The generic `StatusMachine[ResearchTurn]` supplies `artifact_type`; an
  explicit `artifact_type` still works.
- Runs when its `reads` exist; pass `reads=` to narrow them. The
  event maps to a lifecycle via `owner_key(artifact)` (by default it reads
  `query_id_field` of the artifact data, falling back to its `id`). Override
  `owner_key` when a domain uses a different key; `StatusState.inputs` is
  filtered with the same hook.
- Picks the **latest** matching `artifact_type` artifact (by `updated_at`);
  `terminal` statuses stop the machine.
- `next_status` receives a typed `StatusState[StatusT]`: the target plus this
  step's inputs already restricted to `key`. `state.status`, `state.all(T)`,
  `state.any(T)` and `state.latest(T)` replace the repeated
  `list_artifacts(T)` + `query_id == key` filtering.
- `next_status` decides the new status; `None` or unchanged ⇒ nothing happens.
- Right before applying the change, `on_transition(context, key, old_status,
  new_status)` is called — your hook for progress announces.
- The transition is a return-style `Edit(target, **{status_field: new})`, which
  compiles to the same update effect; the machine declares `produces=()`
  because it updates its target rather than creating a new artifact.

Put your *verify* logic into another step that reacts on status changes —
the machine and the verifier are separate, deterministic, and unit-testable.
The knowledge demo's `EvaluateTurn` is the canonical instance of
this recipe.

## `TurnContextStep` — one conversation context per turn

`Transcript` renders a window but still takes `Context`; `TurnContextStep`
turns the common chat shape into one artifact: current query, previous query,
bounded history, rolling digest and a follow-up flag. Downstream steps consume
`TurnContext` and never reconstruct the conversation themselves.

```python
from reactifact.recipes.conversation import TurnFacts, TurnContextStep

def build_dialogue(facts: TurnFacts) -> DialogueContext:
    return DialogueContext(
        query_id=facts.trigger.id,
        history="\n".join(m.text for m in facts.history),
        prior_question=facts.previous_query or "",
        prior_answer=(
            facts.previous_answer.data.text
            if facts.previous_answer is not None
            else ""
        ),
    )

turn_context = TurnContextStep(
    UserQuery,
    assistant_type=Answer,
    digest_type=Digest,         # optional; reads digest_id="digest"
    artifact_type=DialogueContext,  # custom build target
    window=12,
    read=OnCreate(UserQuery),    # or a plain Consume
    ensure=True,                    # idempotent per turn
    follow_up=my_follow_up,         # optional; deterministic default
    build=build_dialogue,           # turns TurnFacts into your artifact
)

# agents=[turn_context, *answer_steps]
```

The step emits `TurnContext` (or `artifact_type=`) with id `turn:{trigger.id}`;
`history` is bounded to the current trigger (later turns are not included) and
keeps the `turn`/`session_id` fields. `TurnFacts` includes the trigger,
current/previous query, previous artifact/answer, history, digest and follow-up
flag — so a domain context can carry `prior_domain`/`routing_text` without
reading `Context`. `read=OnCreate` makes it one artifact per turn;
`ensure=True` makes a re-run a no-op. Combine with
`Annotated[..., Unscoped()]` when another step also needs full history.

## `WindowSummarizer` — periodic conversation checkpoints (§27, §37)

Long-running chat memory is just state: message artifacts accumulate, and a
summarizer condenses the recent window every N messages into a checkpoint
artifact. The recipe owns the window size, cadence, and idempotency; the domain
owns *how* to summarize and *what* the summary artifact looks like:

```python
from reactifact.recipes import WindowSummarizer, llm_summarizer


class Summary(BaseModel):
    round: int
    text: str


def build_summary(round_no: int, text: str) -> Summary:
    return Summary(round=round_no, text=text)


Flow = Agent(
    "chat",
    consumes=[Consume(Msg)],
    steps=[
        WindowSummarizer(
            Msg, Summary,
            summarize=llm_summarizer("Condense the recent conversation into a short memory note."),
            build=build_summary,
            window=8,   # messages fed into one summary
            every=4,    # produce a new checkpoint every N messages
        ),
    ],
)
```

- `summarize(context, history) -> str | None` — your callback (or
  `llm_summarizer(system=...)`, a thin wrapper over `llm_reply`); `None`
  triggers the recipe's `fallback` (default: a truncated-history string, never
  a crash).
- `build(round_no, text) -> Summary` — you own the summary artifact's shape;
  the recipe never guesses field names.
- The summary id is derived from the message count (`summary:{round}` by
  default), so re-running the same generation never duplicates a summary.
  Batched creation is handled: the latest completed round is produced even when
  several messages arrive in one generation.
- `render`/`order_key`/`id_of` are all overridable if the defaults (role/text
  rendering, `created_at` ordering) don't fit your artifact.
- `message_type` also accepts a sequence of types (e.g. `[UserQuery, Answer]`),
  so one checkpoint covers a heterogeneous thread.

**Do not pair it with `WindowPruner`.** The checkpoint cadence is based on the
message count, and pruning stops that count from growing, so the next checkpoint
never arrives. Use `RollingDigestSummarizer` when the raw window must be bounded
(and summarized), or `WindowPruner` alone when you only need a bounded raw
transcript:

```python
from reactifact.recipes import WindowPruner

steps = [WindowPruner(Msg, keep=8)]
```

`WindowPruner` is also standalone-useful and accepts the same single type or
sequence of types.

See `tests/test_recipes_memory.py` for the runnable cases.

## `RollingDigestSummarizer` — one growing memory instead of per-round checkpoints (§27, §37)

`WindowSummarizer` keeps every round's snapshot around; some apps instead
want a single memory note that keeps accumulating, with only a small raw
tail kept verbatim. `RollingDigestSummarizer` is that other shape: once the
conversation passes `trigger` messages, it folds everything older than
`window` into one digest artifact — each fold rewrites the digest from the
*previous* digest text plus the newly stale messages — and deletes the
folded messages itself:

```python
from reactifact.recipes import RollingDigestSummarizer, llm_digest_summarizer


class Digest(BaseModel):
    text: str


def build_digest(text: str) -> Digest:
    return Digest(text=text)


Flow = Agent(
    "chat",
    consumes=[Consume(Msg)],
    steps=[
        RollingDigestSummarizer(
            Msg, Digest,
            summarize=llm_digest_summarizer("Fold the new messages into the running memory note."),
            build=build_digest,
            window=8,    # messages kept raw
            trigger=12,  # fold once the conversation passes this length
        ),
    ],
)
```

- `message_type` takes a single type or a sequence of types — e.g.
  `[Question, FinalResponse]` when a conversation is two artifact types
  interleaved rather than one `Msg` model. They're merged and ordered by
  `order_key` before the window/trigger math runs.
- `summarize(context, previous_digest_text, stale_artifacts) -> str | None`
  gets the stale artifacts as a raw list, *not* a pre-rendered string — the
  recipe never flattens messages into text for you. That matters when you
  already have a role/content prompt builder, or the message types don't
  reduce to one `role`/`text` shape: write your own rendering inside
  `summarize` and there's no round-trip through this recipe's string format
  to worry about. `llm_digest_summarizer(system=...)` is the opt-in default
  for callers who *do* want a plain text-in/text-out prompt — it renders with
  `role: text` lines (override via its own `render=`) and labels the previous
  digest and the new history as two sections of one user turn. `None` from
  either path triggers `fallback`, which defaults to appending a
  truncated-history line to the previous digest rather than dropping it.
- `build(text) -> Digest` — you own the digest artifact's shape; `digest_text`
  is the matching reader (default: `artifact.data.text`) if your shape
  doesn't use a `text` field.
- The digest lives at one stable id (`digest` by default, via `digest_id`) —
  each fold refreshes it in place rather than creating a new artifact.
- Don't pair this with `WindowPruner`: pruning deletes without folding first,
  which loses the content this recipe means to condense. It manages its own
  raw window and needs nothing else.

### `llm_digest_summarizer` and `ChatMemory.from_transcript`

`llm_digest_summarizer` covers the whole "fold stale turns into a digest"
callback, including the fallback policy:

```python
from reactifact.recipes.conversation import MessageSpec, Transcript
from reactifact.recipes.memory import llm_digest_summarizer

transcript = Transcript([
    MessageSpec(UserQuery, "user"),
    MessageSpec(Answer, "assistant"),
])

summarize = llm_digest_summarizer(
    SUMMARY_SYSTEM,
    user=SUMMARY_USER,             # PromptTemplate with {previous}/{history}
    render=transcript.render,      # typed role lines
    schema=AnswerBody,             # structured call; omit for plain text
    max_fallback_chars=4000,
    attempts=1,
    temperature=0.2,
    max_tokens=800,
)
memory = ChatMemory.from_transcript(
    transcript,
    keep=settings.chat_memory_keep,
    summarize=summarize,
    summary_type=ConversationDigest,
    compact_commits=settings.chat_memory_commits,
)
```

The callback returns the previous digest when there is no new history, skips
the LLM when `context.resources.llm is None`, and falls back to
`previous + "\n" + history` (truncated) when the provider raises, returns
`None`, or returns blank text. `fallback=` overrides that policy.

## `Route` — one case group per artifact value

`Route` is for the common "the turn carries a branch key; only one downstream
group should run" shape. It stays a builder over ordinary `Agent`s — no
nested groups in the runtime. Each case group keeps its own steps and later
triggers; only matches of the route's `on` event are narrowed by
`key(value) == case`.

```python
from reactifact.recipes import Route

route = Route(
    on=ResearchTurn,
    key=lambda turn: turn.domain,
    cases={
        "meta": kb_react_group,
        "rag": rag_group,
    },
    default="rag",       # values outside the map use the rag group
)

Runtime(ctx, agents=[*route.agents(), planner, ...])
```

Generated groups carry a `route_case` marker. `validate_contracts` uses it to
suppress the `ambiguous_producer` warning when the same artifact is produced by
distinct cases of one route; producers outside the route still warn.

## `ScopedCleanup` — drop a turn's scratch by scope

`EphemeralCleanup` removes artifacts whose ids are derived from a terminal
artifact's key. The chat variant has arbitrary ids but a shared scope
(`query_id`): `ScopedCleanup` reacts to `OnCreate(Answer)` and drops every
scratch artifact in that scope, leaving other turns untouched. In the example,
`Answer` is the app's terminal artifact type.

```python
from reactifact import OnCreate
from reactifact.recipes.cleanup import ScopedCleanup

cleanup = ScopedCleanup(
    Answer,
    scratch_types=(DialogueContext, TurnIntent, ResearchTurn, Evidence),
    scope="query_id",          # or a Scope(...)
    read=OnCreate(Answer),  # default
)

# agents=[Agent("cleanup", steps=[cleanup])]
```

It declares `produces=()` and the real `deletes=(...)`, so contracts no longer
claim the cleanup creates an `Answer`. The `related_all(type)` lookup is scoped
by the terminal's key; a scratch type produced outside that scope is left
alone. `scope` is required (pass `None` explicitly for a deliberate global
cleanup).

## `run_tool_loop` — a bounded native tool-calling loop (§46, §47)

`reactifact.tools.native` deliberately ships one-turn helpers, not a loop.
When you *do* want the loop — bounded rounds, parallel calls, a forced final
answer — `run_tool_loop` is the opt-in recipe; it is not a reactive `Agent`:

```python
from reactifact.recipes import parse_tool_calls_with_textual, run_tool_loop

result = await run_tool_loop(
    call.context,
    system="You are a research assistant.",
    user=question,
    tools=[search_knowledge, read_source],
    mandatory="read_source",
    max_rounds=6,
    max_observation_chars=4_000,   # cap what goes back into the prompt
    redact_tool_outputs=True,      # apply resources.redactor to tool output
    parse_calls=parse_tool_calls_with_textual,  # tolerate DSML/XML providers
    on_event=lambda name, payload: ...,         # round/tool/final for SSE
)
call.effects.create(Answer(text=result.text))
```

- The turn's `Budget` is honoured between rounds: `max_tool_calls` caps
  executions (extra calls come back as tool errors), `max_seconds` and
  token/cost limits stop new rounds; the loop then forces an answer.
- `fallback(ctx, messages, text)` runs when no calls were parsed and the text
  looks like tool-call markup (`looks_like_tool_markup`); return a string to
  end the turn (e.g. delegate to a legacy `ToolUse` loop), or `None` to accept
  the text as the answer.
- `on_event` receives `round_start`, `tool_call`, `tool_result`, `final`; the
  same progress is published through `Context.announce(kind="agent")`, so a
  `Chat(status_kinds=("status", "agent"))` streams it without a
  custom loop.
- `ToolLoopResult.stop_reason` is `completed`, `max_rounds`, `budget`,
  `provider_error` or `fallback`; `error` carries the provider failure detail.
  Tool failures are also typed: `ToolOutput.failed(ToolError(code=, message=,
  retryable=))` surfaces as `ToolObservation.error_info`, and
  `retry_tool_errors=True` retries only the `retryable=True` ones
  (`tool_retry_attempts`/`tool_retry_delay` control the backoff; retries count
  as tool executions for `Budget.max_tool_calls`).

## `keyword_score` / `stem_words` — deterministic text scoring (§67)

Where embeddings are optional, keyword coverage is the neutral fallback (the
English `knowledge` chat and the Russian `repair` catalog use it):

```python
from reactifact.recipes import EN_STOPWORDS, keyword_score, stem_words

keyword_score("How to set up authentication", "authentication")          # 1.0
keyword_score("Установка аутентификации", "аутентификацию", use_stems=True)  # 1.0
stem_words("Ремонт комнаты и kitchen")  # {"ремонт", "комнат", "kitchen"}
```

- English stop words are removed from both sides by default (`EN_STOPWORDS`).
- `use_stems=True` applies a small Russian inflectional stemmer, so
  «аутентификацию» matches «аутентификация» without a model.

## Skills — keyword-triggered instruction snippets (§67)

A **skill** is the same shape popularized by Claude's Skills: a markdown file
with a `name`/`description` frontmatter and a body of procedural
instructions. It is not a `Source` — a `Source` is retrieved to answer a
question with facts; a skill is loaded to change *how* an LLM call for the
current turn is made (a rule to follow, a format to use) once its description
matches the situation:

```python
from reactifact.recipes import load_skills, match_skills

# --- once, at startup ---
skills = load_skills("skills/")   # every *.md file, parsed by frontmatter

# --- per turn, only where it applies ---
situation = "reporting an answer backed by a number computed from structured storage"
for skill in match_skills(skills, situation):
    prompt += f"\n\nInstruction ({skill.name}): {skill.body}"
```

A skill file:

```markdown
---
name: cost-reporting
description: How to report a number computed from structured storage. Use when the answer is backed by a deterministic calculation.
---
State the exact computed value explicitly, and say plainly it was computed,
not estimated — name the source and column it came from.
```

- `situation` is a short, **code-written** description of what is currently
  happening, not necessarily the user's raw question — the caller
  characterizes the moment, the same way the skill's own `description`
  characterizes when to use it. This keeps triggering reactive (§8): a skill
  fires because of what state exists (e.g. a `Calculation` artifact), not
  because the code parses the user's phrasing.
- Matching is `keyword_score` (deterministic, no embeddings) over
  `name + description`; only matches at or above `threshold` (default
  `0.34`) are returned, capped at `limit` (default `1`) — a skill should be a
  precise trigger, not a fallback that fires on every turn.
- This is deliberately **not** a new core primitive (§61): a matched skill's
  `body` is just a string you prepend to a `structured_llm`/`llm_reply`
  prompt. The `knowledge` demo's `cost-reporting` skill
  (`examples/knowledge/skills/`) is the canonical instance — see its
  [README](https://github.com/bzdvdn/reactifact/blob/master/examples/knowledge/README.md#skills--instructions-loaded-by-the-situation-not-the-graph).
- **Scope**: this covers the instructions half of Claude's Skills format —
  frontmatter + procedural markdown. It does not cover the other half —
  bundled executable scripts a skill can ship and have the model run. That's
  a deliberate line, not a gap to close: executing a skill's own code is a
  sandboxing and permissions problem, and reactifact's determinism story
  (§67) is about *not* handing the runtime code to execute unreviewed. If you
  need that, wire the script as a `Tool` yourself (§46) — reachable from a
  matched skill's `body` (name it in the instructions) or independent of it.

## `PlanExecute` — sequential plan → execute → finish (§24, §42, §69)

Plan-and-Execute (LangChain/AutoGPT-style): a plan is drafted once, its steps
run **one at a time** — each gated on its predecessor's result — and a final
answer is synthesized once every step has one. Ported from
The `PlanExecute` recipe's `Planner`/`Executor`/`Finisher`
(141 + 14 lines), generalized: the recipe owns ordering, gating, idempotent
re-entry, and completion detection; you only implement the three decisions
that need judgement.

```python
from reactifact.recipes import PlanExecute


class Flow(PlanExecute[Goal, PlanStep, StepResult, FinalAnswer]):
    goal_type = Goal
    step_type = PlanStep
    result_type = StepResult
    final_type = FinalAnswer

    async def plan(self, context, goal) -> list[PlanStep]:
        ...  # ordered steps — index/goal are stamped by the recipe

    async def execute_step(self, context, step, history) -> StepResult:
        ...  # history: every prior step's result, in order

    async def finish(self, context, goal, results) -> FinalAnswer:
        ...  # combine every step's result, in order


FlowAgent = Agent(
    "flow_agent",
    consumes=[Consume(Goal), Consume(PlanStep), Consume(StepResult)],
    steps=Flow().produces(),
)
```

The same flow without a subclass — and with the group/pipeline wiring inferred:

```python
from reactifact.recipes import PlanExecute

flow = PlanExecute.from_funcs(
    goal_type=Goal,
    step_type=PlanStep,
    result_type=StepResult,
    final_type=FinalAnswer,
    plan=plan,                    # sync or async
    execute_step=execute_step,
    finish=finish,
)
from reactifact import PipelineRunner

pipeline = flow.pipeline(seed_type=Goal)   # or flow.agent() in a Runtime
result = PipelineRunner(pipeline).run(Goal(text="ship it"))
```

- `step_type`/`result_type` need `goal_field`/`index_field` (defaults:
  `"goal"`/`"index"`) with defaults on the fields (e.g. `index: int = 0`) —
  the recipe stamps the real values on every create, your hooks don't need
  to fill them in.
- Supports several goals concurrently in one `Context` — steps/results are
  scoped by `goal_field`, not by "whatever's in the Context right now".
- Provenance: the final artifact is linked `supported_by` → every step
  result (§34).

See `tests/test_recipe_plan_execute.py` for the runnable cases.

## `Router` / `ApprovalGate` — classify, then ask before finalizing (§60, §67)

Two independent pieces: a router and an approval gate. The pattern mirrors
`RouteTask`/`Supervisor` produces:

- `Router` — classify a request into one of a fixed set of routes,
  idempotently, with a deterministic fallback when the classifying call is
  unavailable or returns something outside `routes` (§67).
- `ApprovalGate` — ask a human to sign off on a report before finalizing it
  (§60). Tracks a thread's *whole* history of approval questions, not just
  the unanswered ones, so a rejection doesn't spawn a duplicate question
  chain — the exact bookkeeping bug this recipe exists to not let you write
  again. Uses `kind="approve"` — the same vocabulary the destructive-tool
  gate (`reactifact.tools.use`) and `Verify` (`verify.py`) already use, so one
  control-plane UI built for "approve" requests handles all three.

```python
from reactifact.recipes import ApprovalGate, Router


class MyRouter(Router[Request, Task]):
    request_type = Request
    task_type = Task
    routes = ("budget", "timeline", "quality")

    async def classify(self, context, request) -> str | None:
        ...  # structured LLM decision, or None to fall back

    def fallback_route(self, context, request) -> str:
        ...  # deterministic keyword match, etc.


class MyGate(ApprovalGate[SpecialistReport, FinalReply]):
    report_type = SpecialistReport
    final_type = FinalReply

    def approval_question(self, context, report) -> str: ...
    async def on_approve(self, context, report) -> FinalReply: ...
    async def on_reject(self, context, report, answer) -> FinalReply: ...


Flow = Agent(
    "flow",
    consumes=[Consume(Request), Consume(Task), Consume(SpecialistReport),
              Consume(PendingQuestion)],
    steps=[MyRouter().produce(), Specialist(), MyGate().produce(),
           Step(PendingQuestion)],  # Router/ApprovalGate call human.ask at runtime
)
```

`Specialist` — actually doing the routed work — stays fully your own
`Step`; there's nothing generic about "what a route does".

See `tests/test_recipe_supervisor.py` for the runnable cases.

## `ReflectionLoop` — generate → critique → regenerate (§24, §42, §69)

LangGraph's "Reflection" pattern. The recipe mirrors the hand-rolled
hand-rolled `DraftIt`/`Critic`/`Rewrite`/`Finalize` produces. Unlike
`PlanExecute` (immutable, indexed steps), the working state is *one mutable
draft artifact per topic*, updated in place each round — matching the
ported example's own `effects.update(draft, ...)` model.

```python
from reactifact.recipes import ReflectionLoop


class MyLoop(ReflectionLoop[Topic, Draft, Review, Final]):
    topic_type = Topic
    draft_type = Draft
    review_type = Review
    final_type = Final
    accept_at = 0.8
    max_rounds = 2

    async def draft(self, context, topic) -> Draft: ...
    async def critique(self, context, draft) -> tuple[float, str]: ...  # (score, feedback)
    async def rewrite(self, context, draft, feedback) -> Draft: ...
    async def finish(self, context, draft) -> Final: ...


Flow = Agent(
    "flow",
    consumes=[Consume(Topic), Consume(Draft), Consume(Review)],
    steps=MyLoop().produces(),
)
```

Function-first is the same shape:

```python
loop = ReflectionLoop.from_funcs(
    topic_type=Topic,
    draft_type=Draft,
    review_type=Review,
    final_type=Final,
    draft=draft,
    critique=critique,            # (score, feedback), sync or async
    rewrite=rewrite,
    finish=finish,
    accept_at=0.85,
)
pipeline = loop.pipeline(seed_type=Topic)
```

- `draft_type` needs `topic_field`/`round_field`/`status_field` (defaults:
  `"topic"`/`"round"`/`"status"`); `review_type` needs `topic_field` too —
  a `Review` is only ever reached by reacting to *its own* creation, so the
  recipe must read the topic straight off it. Give these fields defaults;
  the recipe stamps the real values on every create/update.
- Round-capping (`max_rounds`) and the accept threshold (`accept_at`) are
  the recipe's — not scattered `if round + 1 > MAX_ROUNDS` checks across
  three produces.
- Supports several topics concurrently in one `Context`.

See `tests/test_recipe_reflection.py` for the runnable cases.

## `changed_fields` / `earliest_stage` / `downstream_fields` — change → rebuild

Long multi-stage flows occasionally have to *go back*: the user edits a fact,
the pipeline rebuilds from the earliest affected stage and clears everything
downstream. The helpers are generic; the workflow is your `field_stages` map and
stage order (the `repair` approval is the canonical usage):

```python
from reactifact.recipes import changed_fields, downstream_fields, earliest_stage

field_stages = {"room": "collect", "style": "design_choice",
                "area": "plan", "budget": "estimate"}
order = ("collect", "design_choice", "plan", "estimate")

changed = changed_fields(old_info, new_info)          # {"style", "budget"}
target = earliest_stage(changed, field_stages=field_stages, order=order)
reset = downstream_fields(target, field_stages=field_stages, order=order)
```

`changed_fields` ignores fields the new state left `None` (the model didn't
know); `earliest_stage` returns `None` when nothing changed; `downstream_fields`
is target-inclusive (everything at that stage or later is reset, upstream kept).