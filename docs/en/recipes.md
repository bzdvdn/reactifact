# Recipes

`reactifact.recipes` contains reusable building blocks that encode patterns which
recur in every demo. Importing `reactifact.recipes` does not pull in additional
dependencies — everything is built on the core.

## `find` / `find_all` — locate typed artifacts in `inputs`

A produce whose agent declares more than one `Consume` type receives a flat
`list[Artifact[Any]]`; picking out "the one Question" or "all the Evidence"
is the same `next((a for a in inputs if isinstance(a.data, X)), None)` in
nearly every produce. `find`/`find_all` are a typed one-liner for it:

```python
from reactifact.recipes import find, find_all

question = find(inputs, Question)          # Artifact[Question] | None
evidence = find_all(inputs, Evidence)       # list[Artifact[Evidence]]
```

## `fan_out_sources` — reactive search

```python
from reactifact.recipes import fan_out_sources

refs = await fan_out_sources(
    context,
    query=question,
    owner_id=query_id,           # scope refs to the owning turn
    limit=5,
    query_id=query_id,           # owner key for provenance
    on_start=lambda source: context.announce(
        f"Searching {source}…", kind="status"
    ),
    on_count=lambda source, n: context.announce(
        f"{source}: {n} hits", kind="status"
    ),
)  # each SourceRef is created in the current produce's effects (self.effects)
```

What it does:

1. Fans out to **every configured source** in `context.resources.sources`.
2. Ranks the combined `SourceRef`s by score (highest first).
3. Creates **idempotent, owner-scoped refs** (effect `Create`) —
   `id=f"ref:{ref.stable_id()}:{owner_id}"`.

Idempotency matters: the caller runs a search once per turn (guarded by its own
marker) but may *re-run* on retries; the same ids are re-created, never
duplicated. `on_start` / `on_count` give you progress announces over SSE.

## `materialize_doc` — lazy reference resolution

```python
from reactifact.recipes import materialize_doc

async def doc_from_ref(context, ref_artifact, content) -> TypedDoc:
    # build your domain document from the fetched content
    return TypedDoc(query_id=ref_artifact.data.query_id, path=..., text=content)

doc = await materialize_doc(
    context,
    ref_artifact,
    doc_from_ref,
    relation="resolved_from",    # default: materialized_from
)
```

Because sources return *references*, not payloads, documents are fetched
**lazily, only when needed** — the rule in the research demo is "resolve a page
only after the model ranks it relevant". A missing source or a resolve failure
yields `None` (an honest no-op), never a crash, and the failure is surfaced in
the run trace.

The produced document is linked back:
`TypedDoc ──resolved_from──► SourceRef` (§34), so the provenance walk
`Answer → Claim → Evidence → Doc → SourceRef` stays complete.

## `StatusMachine` — deterministic lifecycles

Status machines are the pattern for "an artifact that moves through states"
(a research turn: `researching → answerable → answered`). Instead of a manual
transition graph, a `StatusMachine` is a **pure function of current state**:

```python
from reactifact import Artifact, Context
from reactifact.recipes import StatusMachine


class EvaluateTurn(StatusMachine[ResearchTurn]):
    artifact_type = ResearchTurn                 # what it advances
    terminal = frozenset({"answered", "insufficient"})  # where it stops
    query_id_field = "query_id"                  # owner key field (default)
    status_field = "status"                      # status field (default)

    def next_status(self, context: Context, key: str) -> str | None:
        """Pure function: which status the lifecycle deserves now."""
        if any(a.data.query_id == key for a in context.list_artifacts(Answer)):
            return "answered"
        return None

    def on_transition(self, context, key, old_status, new_status) -> None:
        context.announce(f"Research status: {old → new}",
                         kind="status", query_id=key)
```

Mechanics (all inherited from `produce`):

- Woken by any event; the event maps to a lifecycle via `owner_key` (reads
  `query_id_field` of the artifact data, falling back to its `id`).
- Picks the first matching `artifact_type` artifact; `terminal` statuses stop
  the machine.
- `next_status` decides the new status; `None` or unchanged ⇒ nothing happens.
- Right before applying the change, `on_transition` is called — your hook for
  progress announces.
- The transition is an effect: `self.effects.update(target, **{status_field: new})`.

Put your *verify* logic into another `produce` that react on status changes —
the machine and the verifier are separate, deterministic, and unit-testable.
The knowledge/research demos' `EvaluateTurn` are the canonical instance of
this recipe.

## `WindowSummarizer` / `WindowPruner` — bounded conversation memory (§27, §37)

Long-running chat memory is just state: message artifacts accumulate, and two
plain `Produce`s keep the window bounded — a summarizer condenses the recent
window every N messages, a pruner deletes what falls outside it. The recipe
owns the window size, cadence, and idempotency; the domain owns *how* to
summarize and *what* the summary artifact looks like:

```python
from reactifact.recipes import WindowPruner, WindowSummarizer, llm_summarizer


class Summary(BaseModel):
    round: int
    text: str


def build_summary(round_no: int, text: str) -> Summary:
    return Summary(round=round_no, text=text)


class Flow(Agent):
    name = "chat"
    consumes = [Consume(Msg)]
    produces = [
        WindowSummarizer(
            Msg, Summary,
            summarize=llm_summarizer("Condense the recent conversation into a short memory note."),
            build=build_summary,
            window=8,   # messages fed into one summary
            every=4,    # produce a new summary every N messages
        ),
        WindowPruner(Msg, keep=8),  # standalone-useful without the summarizer too
    ]
```

- `summarize(context, history) -> str | None` — your callback (or
  `llm_summarizer(system=...)`, a thin wrapper over `llm_reply`); `None`
  triggers the recipe's `fallback` (default: a truncated-history string, never
  a crash).
- `build(round_no, text) -> Summary` — you own the summary artifact's shape;
  the recipe never guesses field names.
- The summary id is derived from the message count (`summary:{round}` by
  default), so re-running the same generation never duplicates a summary.
- `render`/`order_key`/`id_of` are all overridable if the defaults (role/text
  rendering, `created_at` ordering) don't fit your artifact.

See `examples/summarize/main.py` for the full runnable demo.

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
  [README](../../examples/knowledge/README.md#skills--instructions-loaded-by-the-situation-not-the-graph).
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
`examples/plan_execute`'s hand-rolled `Planner`/`Executor`/`Finisher`
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


class FlowAgent(Agent):
    consumes = [Consume(Goal), Consume(PlanStep), Consume(StepResult)]
    produces = Flow().produces()
```

- `step_type`/`result_type` need `goal_field`/`index_field` (defaults:
  `"goal"`/`"index"`) with defaults on the fields (e.g. `index: int = 0`) —
  the recipe stamps the real values on every create, your hooks don't need
  to fill them in.
- Supports several goals concurrently in one `Context` — steps/results are
  scoped by `goal_field`, not by "whatever's in the Context right now".
- Provenance: the final artifact is linked `supported_by` → every step
  result (§34).

See `examples/plan_execute/main_recipe.py` for the full runnable demo
(compare it to `produce.py`+`agents.py` in the same directory).

## `Router` / `ApprovalGate` — classify, then ask before finalizing (§60, §67)

Two independent pieces, ported from `examples/supervisor`'s hand-rolled
`RouteTask`/`Supervisor` produces:

- `Router` — classify a request into one of a fixed set of routes,
  idempotently, with a deterministic fallback when the classifying call is
  unavailable or returns something outside `routes` (§67).
- `ApprovalGate` — ask a human to sign off on a report before finalizing it
  (§60). Tracks a thread's *whole* history of approval questions, not just
  the unanswered ones, so a rejection doesn't spawn a duplicate question
  chain — the exact bookkeeping bug this recipe exists to not let you write
  again. Uses `kind="approve"` — the same vocabulary the destructive-tool
  gate (`tool_use.py`) and `Verify` (`verify.py`) already use, so one
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


class Flow(Agent):
    consumes = [Consume(Request), Consume(Task), Consume(SpecialistReport),
                Consume(PendingQuestion)]
    produces = [MyRouter().produce(), Specialist(), MyGate().produce(),
                Produce(PendingQuestion)]  # widens allowed Create types (§ verify.py)
```

`Specialist` — actually doing the routed work — stays fully your own
`Produce`; there's nothing generic about "what a route does".

See `examples/supervisor/main_recipe.py` for the full runnable demo.

## `ReflectionLoop` — generate → critique → regenerate (§24, §42, §69)

LangGraph's "Reflection" pattern, ported from `examples/reflection`'s
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


class Flow(Agent):
    consumes = [Consume(Topic), Consume(Draft), Consume(Review)]
    produces = MyLoop().produces()
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

See `examples/reflection/main_recipe.py` for the full runnable demo.

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