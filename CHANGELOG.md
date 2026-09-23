# Changelog

All notable changes to **reactifact** are documented here as releases are cut.
Format follows [Keep a Changelog](https://keepachangelog.com/); versioning is
[SemVer](https://semver.org/) with `rc` marks for pre-releases.

## [Unreleased]

## [0.11.1] — 2026-09-24

### Fixed

- The trace router's `/api/traces/export` handler broke OpenAPI schema
  generation: its `-> JSONResponse` return annotation is an unresolvable forward
  ref (the module uses `from __future__ import annotations` and imports fastapi
  lazily inside `create_trace_router`), so `/openapi.json` and `/docs` raised
  `PydanticUserError`. The route now declares `response_model=None`.

## [0.11.0] — 2026-09-23

### Fixed

- `RunTrace.duration_ms` was carrying seconds (`time.monotonic()`), so the
  dashboard showed every run as `0 ms` and the Langfuse run span ended ~1000×
  too early. It is now milliseconds, the same unit as `AgentSpan.latency_ms`
  (`RunStats.duration` stays seconds and is documented as such).
- The Mermaid viewer failed with `mermaid.initialize is not a function`:
  recent `mermaid@11` dists no longer expose the `window.mermaid` global. The
  loader now uses a pinned UMD build (`11.6.0`) with the jsDelivr ESM bundle as
  a fallback.
- The Mermaid diagram now fits the full block width (small diagrams are scaled
  up, wide ones down, and centred) and stays crisp: fit/zoom resize the SVG
  itself instead of CSS-scaling a rasterised layer, so text is no longer blurry.

### Docs

- README and the docs landing (`docs/en/index.md` + RU) now lead with a
  **Celery-shaped, event-driven framing** for Python developers: a task wakes on
  a typed artifact (not a message you push or an edge you draw), with a
  task/queue/chord/retry/result-backend → reactifact mapping table and the honest
  caveat that it's single-process today. Reproducibility, provenance and audit
  are presented as what you get *on top* of that model. `why-reactifact.md`
  (EN+RU) opens with the same "if you know Celery" model before the design
  argument.
- The `fintech_audit` hero (pain → proof) with real figures and a
  `context_sha256`, a trimmed deterministic-produce snippet, and two recorded
  GIFs (`vhs`, from committed `.tape` scripts) live under "On top: a provable
  answer": a short top-of-funnel one (`docs/img/fintech-audit-short.gif`, ~88 KB,
  from `fintech-audit-short.tape`) on the README/landing, and the full
  `docs/img/fintech-audit-demo.gif` (from `fintech-audit-demo.tape`) in
  `examples.md` (EN+RU) and the example's own README. `examples/fintech_audit`
  gained a `--brief` flag for the compact recording.

### Added

- **Dashboard redesign (app shell).** The trace UI now shares one design system
  (`templates/app.css`, served at `/traces/assets/app.css`): a collapsible left
  sidebar (Traces / Sessions, persisted, drawer on mobile), a sticky topbar with
  breadcrumbs, a unified card/section style, stat tiles on the run page, a
  filter bar with removable active-filter chips, a floating bulk-action bar (no
  layout shift), sticky table headers with horizontal scroll, tag "+N" overflow,
  skeletons/empty states, toasts, and a system-aware theme with a smooth
  transition. All three pages were rebuilt on it.
- **Trace review annotations.** The trace dashboard is now a review surface:
  open a run, tag it (`bad-prompt`, `hallucination`, `needs-review`, …) with an
  optional note describing what to fix, and filter runs by tag later. Tags are
  reviewer annotations stored by the sink — never produced by the runtime — so
  the immutable `RunTrace` is not rewritten. Both `TraceStore` (SQLite) and
  `PostgresStore` gain `tags`/`run_tags` tables (auto-created + migrated),
  `list_tags`/`tag_runs`/`untag_runs`/`rename_tag`/`set_tag_color`/`delete_tag`,
  tag filters (`any`/`all`) and free-text search over the run list. New models
  `Tag`/`TagAssignment` (+ `RunTrace.annotations`), a managed tag vocabulary
  (rename/recolour/delete across all runs), bulk tagging, per-run token/latency
  rollups, sorting and JSON export. New API under `create_trace_router`:
  `GET/POST /api/tags`, `PATCH/DELETE /api/tags/{name}`,
  `POST/DELETE /api/traces/{id}/tags`, `POST /api/traces/tags[/remove]`,
  `GET /api/traces/export`, plus `tag`/`tag_mode`/`q`/`sort`/`order` on
  `/api/traces`. The dashboard UI (list + run pages) was reworked for the full
  review workflow, with a unified button system and a proper Mermaid viewer:
  one shared loader (previously two), palette-matched light/dark theming that
  re-renders on toggle, zoom / pan / reset / fullscreen controls, a fit-to-width
  default, a loading spinner and an offline fallback that keeps the source
  readable. The list gained URL-synced state (every filter/tag/sort/page is a
  shareable link, with local **saved views**), sortable column headers and a
  sessions view; the run page gained a **Tree ↔ Timeline (Gantt)** toggle that
  shows span start offsets and parallelism (spans now persist `started_at` —
  SQLite + Postgres migration), in-trace search, a raw-JSON view and
  copy-link/copy-JSON actions. New `TraceStore.sessions()` / `GET /api/sessions`
  / `/sessions` page aggregate runs per session. The UI also got production
  polish: loading and fetch-error states with retry on all pages, relative
  timestamps (absolute on hover), correct outcome filters (all four
  `RunOutcome`s), background-tab polling pause, and aria-labels on icon
  buttons.
- Deterministic runs and a one-call check. `RuntimeResources(id_factory=…)` (with
  `reactifact.replay.counter_ids()`) gives artifacts created without an explicit
  id stable `Model:0000`-style ids instead of `uuid4`, so an otherwise-unmodified
  app's `context_hash` is reproducible run to run. `reactifact.replay.verify_run(
  build, *, recording=…)` runs a pipeline `repeat` times under a recorded model
  and strict ids and returns a `ReproReport` — `ok=False` (with every diverging
  hash) when something is still nondeterministic (time, randomness, unstable
  ids, order).
- `ScenarioLab.fail_resource(...)` accepts a **typed key** — a class or a
  `ResourceKey` registered via `resources.register(...)` — as well as the
  existing string names (`"llm"`, a source id, `resources.set(name, ...)`), so
  a resource stored typedly can be faulted and restored like any other.

### Fixed

- `reactifact.testing` fault injection now finds tool lists registered as
  **typed resources**. `_iter_tool_lists` scanned only `resources.additional`,
  so after moving to `resources.register(...)` a dynamically-resolved
  `list[Tool]` was invisible to `FaultInstaller` — `result.tools.called(...)` /
  `never_called(...)` silently missed those calls and `lab.fail(tool, ...)`
  had nothing to wrap. It now scans both `additional` and the typed store
  (`RuntimeResources.typed_values()`), de-duping by identity so a list present
  in both is wrapped once.

## [0.10.0] — 2026-09-20

The on-ramp release: a thin `reactifact.quick` facade over the primitives,
first-class audit + reproducible-run tooling, per-run request context and typed
resources, loop-safe provider clients, validated/repairable structured output,
opt-in trace redaction, a migration guide, and four new examples
(`fintech_audit`, `support_copilot`, `repo_agent`, `starter_app`) plus a Colab
quickstart. No breaking changes.

### Docs

- `docs/en/scheduler-semantics.md` (+ `docs/ru/` mirror) — the execution
  contract of `Runtime`: the generation loop step by step (drain → match →
  debounce → order → schedule → budget slice → dispatch → collect → validate →
  commit), termination/quiescence, ordering and concurrency guarantees, budget
  and failure semantics, and an explicit list of what is/isn't deterministic and
  what is deliberately *not* guaranteed. `tests/test_scheduler_semantics.py`
  pins the load-bearing claims so the doc cannot silently drift from the code.
  Added to the docs nav as "Execution model".
- `docs/en/migrating.md` (+ `docs/ru/` mirror) — a migration guide from
  LangGraph / CrewAI / LlamaIndex / plain Python: concept map, a "port one node"
  before/after, state-dict→artifacts, checkpointing/interrupts/RAG/tool loops,
  interop ("don't go all-or-nothing": call reactifact from a node, expose it over
  MCP), an ops mapping and a migration checklist. Added to the nav as
  "Migrating".
- `examples/quickstart.ipynb` — a Colab notebook walking the four `quick` cases
  (offline by default, optional provider cell); "Open in Colab" badges in
  `quickstart.md` and the README.

### Added

- `reactifact.quick` — a thin on-ramp for the common first tasks, as *sugar
  over the same primitives* (not a second framework): `agent(system, schema)`
  (one structured call), `rag(sources)` (retrieval → materialize → answer, with
  `supported_by`/`materialized_from` provenance), `tools_agent(system, tools)`
  (blocking `LLMAgent` or, with `human=True`, reactive `HITLLMAgent`), and
  `chat_agent(agents)` (a configured `ChatAssistant`). Every object exposes the
  real `.agent`/`.agents` and the run's `.context`, so you graduate to
  hand-written produces without rewriting anything. Split into a package
  (`quick.agent`/`quick.rag`/`quick.tools_agent`/`quick.chat` + shared
  `models`/`_shared`), all re-exported from `reactifact.quick`. Every entry
  point takes `llm=<LLMProvider>` (default `from_env()`): `agent`/`rag`/
  `tools_agent` from the start, `chat_agent` now too (it previously only
  accepted a full `resources=`).
- `reactifact.quick` accepts your own artifact models. `rag(...)` takes
  `question_type`/`doc_type`/`answer_type` (defaults `Question`/`Doc`/`Answer`),
  with `doc_factory(context, ref, content)` / `answer_factory(text, docs)` for
  models that don't follow the default fields and `doc_text`/`doc_locator` so
  the prompt and citations know how to read a custom document. `agent(...)` and
  `tools_agent(...)` take `question_type` too. `QuickRAG` is generic in the
  answer type, so `await r.ask(...)` returns `TAnswer | None` (e.g.
  `QuickRAG[MyAnswer]`) with the concrete model preserved statically.
  Provenance (`supported_by`/`materialized_from`) is unaffected by the model
  shape.
- `Effects.create_once_from(source, data)` — idempotent create whose id is
  derived from another artifact (`f"answer:{question.id}"`), folding the
  guard+create ritual into one call; `prefix` overrides the default
  (lowercased model name).
- `reactifact.checkpoints.InMemoryKVBackend` — dict-backed `KVBackend`, the
  zero-config session store (tests/notebooks/short-lived scripts).
- `keyword_score(fold_plurals=True)` — folds English plurals (`refunds` →
  `refund`, `policies` → `policy`), so a singular query term matches a plural
  in the text. `reactifact.quick.rag` uses it for plain-path sources by default.
- `reactifact.redaction` — an opt-in `Redactor` hook (`RuntimeResources(
  redactor=…)`) that scrubs trace text before it reaches a sink: artifact
  `data`, LLM `messages`/`response`, and span/LLM `error`. It never touches the
  live `Context` or persisted sessions (masking the working copy would break
  resume). Ships `RegexRedactor` with conservative defaults (email, US SSN,
  IBAN, `Bearer` tokens, `sk-…` API keys) plus custom `patterns=`; any object
  with `redact(text) -> text` satisfies the protocol. `None` (default) is a
  no-op, so this is non-breaking. A raising redactor is swallowed like a failing
  sink — it can never abort the run.
- `reactifact.audit` — `build_report(context, answer)` walks an answer's
  provenance chain (`supported_by`/`derived_from`/`materialized_from`/…) into a
  verifiable `AuditReport`: content sha256 per artifact, producing author and
  version, the relation edges, and the source locators it rests on;
  `report_to_json`/`report_to_markdown` render it. `context_hash(context)` is a
  sha256 over the run's canonical state (artifacts + relations + version,
  timestamps excluded) — reproducible run to run. `reactifact replay … --hash`
  prints it and `--verify <hash>` exits non-zero on mismatch, so a saved
  session can be checked against a recorded fingerprint offline.
- `examples/fintech_audit` — the audit story end to end: a transactions CSV +
  budget CSV + policy document, figures computed in plain Python (never the
  model), an answer linked to its evidence, and an `AuditReport` +
  reproducibility check. Runs offline, no key.
- `examples/support_copilot` — a grounded support reply with citations, or a
  real escalation: when no document matches, the runtime raises a
  `PendingQuestion` (via `effects.ask`) instead of inventing an answer, and the
  human's answer becomes the reply. Offline.
- `examples/repo_agent` — a coding agent where `git_commit` is
  `@tool(destructive=True)`: the model may decide to call it, but the runtime
  gates execution behind a human approval (`ToolUseHITL`), while safe tools
  (`read_file`, `run_tests`) run freely. Offline (scripted provider, local
  tools).
- `examples/starter_app` — a copy-pasteable FastAPI app covering the four
  `quick` cases (one structured call, RAG with citations, LLM+tools, chat with
  sessions) plus a trace dashboard, with a tiny no-build UI. Provider is
  `from_env()`: offline when no key, OpenRouter (`OPENROUTER_API_KEY`), or any
  OpenAI-compatible endpoint (`OPENAI_BASE_URL`); offline RAG still answers from
  `knowledge/` with citations. Ships `.env.example` and a README.
- `@tool(...)` now has proper overloads, so decorated functions stay typed under
  a strict type checker (`@tool(destructive=True)` no longer reads as an untyped
  decorator).
- Typed, configurable chat SSE contract. `reactifact.chat.ChatEvent.kind` is now
  a closed `Literal["session","status","message"]` (the frame schema is
  introspectable via `ChatEvent.model_json_schema()`), and
  `create_chat_router` accepts `event_names` (rename kinds on the wire),
  `forward_kinds` (emit a subset), `payload_shaper` (reshape a frame) and
  `done_event` (one terminal frame) — so a client with its own vocabulary
  (`status`/`content`/`done`) is configuration, not a forked router. The event
  schema and effective names are published in the route's OpenAPI `responses`;
  defaults are unchanged.
- `PromptTemplate` now only substitutes **identifier-shaped** `{field}`
  placeholders and leaves any other brace run verbatim, so a literal JSON
  example in a prompt (`'Reply with {"name": "..."} for {question}.'`) needs no
  `{{`/`}}` escaping; typos (`{questoin}`) are still missing-variable
  `KeyError`s. `PromptTemplate.hash` / `MessagesPrompt.hash` expose a stable
  sha256 of the template, and `LLMRequest.prompt_hash` is copied onto
  `LLMCall.prompt_hash` by the tracing `RecordingLLM` (set it via
  `structured_llm`/`llm_reply`/`chat_complete*`'s `prompt_hash=`), so prompt
  drift is visible in a trace instead of surfacing as a flaky run.
- Documented and pinned the chat **cancellation contract**: closing the
  `ChatAssistant.stream()` generator (a dropped SSE client) cancels the
  in-flight turn — the `CancelledError` unwinds to `Runtime.astream`'s `finally`
  and stops the runner task, so abandoned turns don't keep burning tokens. A
  regression test now covers it.
- Typed resources: `RuntimeResources.register(Type, instance)` /
  `get(Type)` / `require(Type)` / `has(Type)` read a collaborator back by its
  type instead of a string key + `or None` + duck-typing; `ResourceKey[T]` keys
  two instances of one type (primary/replica, per-tenant), and `require` raises
  a loud early `LookupError` instead of a `None` that fails later. String
  `get`/`set` stay as the `additional` escape hatch.
- Per-run request context: `Runtime.arun`/`arun_once`/`astream` (and
  `ChatAssistant.stream`/`invoke`) take `request=Mapping`, installed for the
  turn and read in produces as `call.request` (or `reactifact.current_request()`)
  — the first-class replacement for a hand-rolled `ContextVar` or a
  `resources`-side dict. It propagates to the generation's child tasks and is
  isolated between concurrent turns.
- `ResourceScope` / `RuntimeResources.scope(factory)` — an `async with` builder
  that creates resources on the current event loop and closes them on exit, so
  loop-bound provider clients (httpx, vector DBs) don't survive into the next
  loop a CLI or pytest uses (`RuntimeError: Event loop is closed`).
- `structured_llm(..., validate=fn, repair=fn, prompt_hash=…)` — a domain-rule
  check on top of the JSON schema: a `validate(model) -> bool` rejection is
  retried like a parse failure, with `repair(invalid_or_None, last_reply) -> str`
  supplying the retry instruction (defaults provided); exhaustion returns the
  same honest `None` with `on_error("validation_error")`. The same two hooks are
  on `json_schema_llm` (for `dict`/JSON-Schema output), and `StructuredLLM`
  carries them too.
- `recipes.run_tool_loop(context, *, system, user, tools, max_rounds=…,
  parallel=True, mandatory=…)` — the opt-in native tool-calling loop over
  `native_tool_use`: bounded rounds, parallel tool execution, a nudge until a
  mandatory tool runs, and a forced final answer when rounds run out; returns a
  `ToolLoopResult` (final text, OpenAI-format transcript, `ToolObservation`s).
  A recipe, not a core primitive — ignore it and compose `native_tool_use`
  yourself if the shape doesn't fit.
- `reactifact.testing` golden-run helpers — `capture(context, trace=…)` freezes
  a `GoldenRun` (`audit.context_hash` + every `LLMCall.prompt_hash`),
  `assert_golden(...)` fails on drift, and `replay_resources(recording)` gives a
  `RuntimeResources` whose llm replays a recording, so a real run becomes an
  offline, deterministic regression.

### Fixed

- Providers, `WebSource` and the OTLP/Langfuse sinks no longer cache one
  `httpx.AsyncClient` for the whole process. A client is bound to the event loop
  it was created on, so reusing a long-lived provider across loops (pytest's
  per-test loops, repeated `asyncio.run`, uvicorn reload) raised
  `RuntimeError: Event loop is closed` on the second use. The client is now
  created lazily **per running loop** (a new `reactifact._httpx.LoopBoundClient`,
  used by `OpenAICompatProvider`/`...Embedder`, `AnthropicProvider`,
  `GeminiProvider`, the image/speech/video providers, `WebSource`,
  `OTLPTracer`, `LangfuseTracer`); `aclose()` closes the current loop's client.
  `RuntimeResources.scope(...)` remains the recommended explicit lifecycle.
- `reactifact.quick`'s `.context` is a real `Context` from construction, not
  `Context | None`. Reading it after `ask()` (`r.context.list_artifacts(...)`)
  no longer gets flagged by a type checker (the run still replaces it).
- `examples/repair` no longer loops on «Уточните, пожалуйста: площадь» forever.
  Fact extraction was LLM-only, so with no provider configured (the offline
  demo) `room_type`/`area`/`budget` were never filled and the collect stage
  re-asked forever; and with a model, a plainly-stated «10 метров» sometimes
  came back with `area` null. Added a deterministic `parse_facts` (Russian
  units: «10 метров»/«10 м»/«10 м²» → area, «300 тысяч»/«50к» → budget,
  «потолок 2.7 м» ≠ area), used when no model is configured and to fill fields
  a configured model left unset — without masking a genuine model failure.

## [0.9.1] — 2026-09-18

### Fixed

- Tracing is now best-effort by contract: a sink that is unreachable (Langfuse
  down, Postgres refusing connections), a custom `Tracer` callback that raises,
  or a malformed trace payload no longer propagates out of `Runtime.arun()` /
  `astream()`. Every tracer call site (`on_turn_begin` / `on_span` /
  `on_turn_end`, the `RecordingLLM` recorder, and the span ref builders) catches
  and logs the failure, then continues — the business run completes normally.
  `CompositeTracer` and `Tracer` isolate per member/sink, so one failing
  observer never blocks the others from receiving the trace.

## [0.9.0] — 2026-09-18

### Added

- `reactifact.native_tool_use` — OpenAI-style native tool-calling
  (`message.tool_calls`), as three composable functions, not a parallel
  loop class: `tools_payload()` (builds the OpenAI `tools=[...]` array
  from `Tool`s), `parse_tool_calls()` (extracts `message.tool_calls` from
  a raw provider response), `native_complete()` (one native turn — sends
  raw OpenAI-format messages via `LLMRequest.extra`, the existing
  escape hatch for provider-specific wire shapes, _and_ as a best-effort
  typed rendering via `LLMRequest.messages` — `tool_calls`/`tool_call_id`
  dropped, since `Message` has no field for either — so a provider that
  doesn't know this module's `extra` convention still sees a real
  conversation instead of an empty list; honest `None` on no
  provider/a failed call, matching `structured_llm`). A reactive,
  `ToolUseHITL`-shaped loop around these (persisted round history, a
  destructive-tool approval gate) was built and then deliberately cut —
  see the module docstring for why: no concrete use case has needed it
  yet, only the plain request/response step these functions cover.
- `reactifact.structured.json_schema_llm` — structured output against your
  own JSON Schema (`dict` or JSON string) instead of one derived from a
  pydantic model, for a schema that must match specific text verbatim or
  express something pydantic can't. Sends the stricter native
  `{"type": "json_schema", ...}` response-format mode (vs.
  `structured_llm`'s weaker `{"type": "json_object"}`); same
  retry/backoff/honest-`None` contract. Returns a plain `dict`, not a
  validated model.
- `reactifact.structured.chat_complete` — a raw multi-turn chat completion:
  `messages` (your own history, `Message` or plain role/content dicts) in,
  `response.text` exactly as returned out, or `None` on an honest failure.
  No envelope, no schema, no retry — for callers that already build their
  own message arrays from domain artifacts and just want the wire call.
- `reactifact.recipes.memory.RollingDigestSummarizer` (+
  `llm_digest_summarizer`) — the other bounded-memory shape: found missing
  while porting a langgraph app whose `SummarizeNode` kept one growing
  digest of everything older than a raw tail, rather than `WindowSummarizer`'s
  per-round checkpoints. Once the conversation passes `trigger` messages, it
  folds everything older than `window` into a single digest artifact —
  rewriting it from the previous digest text plus the newly stale messages —
  and deletes the folded messages itself; `WindowPruner` alone can't stand
  in for this, since it deletes without folding first and loses the content
  instead of condensing it. Two things that same porting turned up before
  release, both fixed here rather than shipped and patched later:
  `message_type` accepts a type _or_ a sequence of types (a conversation
  built from more than one artifact model, e.g. `Question`/`FinalResponse`,
  not just one `Msg`); and `summarize`/`fallback` receive the stale
  artifacts as a raw list, never a string the recipe rendered for you — a
  caller with its own role/content prompt builder writes its own rendering
  instead of round-tripping through a format the recipe would otherwise
  impose. `llm_digest_summarizer` is the opt-in text-flattening default for
  callers who don't need that.
- `reactifact.recipes.cleanup.EphemeralCleanup` (+ `PrefixedEphemeralCleanup`)
  — deletes a turn's/thread's "scratch" artifacts once a terminal artifact
  exists for their shared correlation key, replacing the same hand-rolled
  `Produce[Terminal]` subclass every multi-stage app had been writing to
  keep `Context` (and anything that checkpoints it, e.g. `SessionStore`)
  from growing unbounded. `PrefixedEphemeralCleanup` pre-builds the common
  `f"{prefix}:{correlation_id}"` id shape.
- `reactifact.recipes.identity.SeedIdentity` — writes per-request data (an
  authenticated user, a tenant id, ...) from a `contextvars.ContextVar` set
  right before `ChatAssistant.stream()`/`.invoke()` into an ordinary,
  create-or-refresh artifact each turn, instead of a `resources`-side dict
  keyed by `session_id` that never gets checkpointed. `ChatAssistant`'s
  `resources=`/`create_message=` now optionally accept the turn's
  `session_id` (`resources=lambda session_id: ...`,
  `create_message=lambda ctx, text, session_id: ...`), detected from each
  callable's own arity so the pre-existing zero/two-arg shapes keep working
  unchanged.
- `Produce.also_creates` — declares extra artifact types a single produce's
  own body writes (`self.effects.create(Bar(...))` inside an
  `artifact_type = Foo` produce), so `Runtime._validate_patch_types` allows
  it without an inert second `Produce(Bar)` placeholder added to the
  agent's `produces` list purely to widen that set.
- `Runtime`/`ChatAssistant` `session_save_policy="per_turn"` — one
  `session.save()` after the whole turn completes instead of the default
  one-per-commit, for pipelines that routinely produce several commits per
  turn and don't need crash-resilience at every single one.
- `reactifact.structured.chat_complete_full` — like `chat_complete`, but
  returns the whole `LLMResponse` instead of just `.text`, for a caller that
  needs `.finish_reason` (e.g. to retry only a token-cap truncation) or
  `.usage`; same honest-`None` failure contract. `chat_complete` is now
  built on top of it.
- `reactifact.testing.EventAssertions` (`result.events`/`scenario.events`)
  — asserts on `context.announce()` progress events captured for the
  duration of a scenario run via `context.subscribe()`. Closes the
  "node status" assertion gap the testing harness's own docstring had
  noted as dropped from v1 for lack of a faithful reactifact analog.
  `FaultInstaller` also now wraps dynamically-resolved `list[Tool]` values
  in `RuntimeResources.additional` (a produce's own tool-calling loop, e.g.
  via `reactifact.native_tool_use`), not just the static per-`Produce`
  tool dicts it already covered.
- `Consume(wakes=bool)` (default `True`) — a `Consume` with `wakes=False`
  still feeds `Agent._collect_inputs()` but never contributes to
  `Agent.triggers`, i.e. "read this as input, don't wake up on it" (an
  agent that should run when a `Question` arrives but also wants
  `ConversationHistory` as input, without re-running once per history
  artifact). Found while reviewing the framework's own core design end to
  end: this is the one real, recurring reason a declarative
  (`consumes`/`produces`) agent used to reach for `Agent`'s separate
  `triggers=` override, which otherwise has to be kept in sync with
  `consumes` by hand. `triggers=` itself is unchanged and not deprecated —
  the imperative style (an `Agent` subclass overriding `run()` directly,
  with no `consumes` at all) still needs it, since there's no `Consume` to
  attach a condition to there; the framework's own `tests/test_runtime.py`
  is exactly that style, and finding it is what stopped an earlier, wider
  draft of this change that removed `triggers=` outright.
- `TokenBudgetContextBuilder(min_keep={type: count})` — reserves a costed
  type's top-`count`-ranked instances a slice of the budget _before_ the
  shared greedy fill runs over everything else, so a high-volume type
  sharing the same budget (many `Evidence`) can't crowd out a low-volume
  one that still has real content (the single triggering `Question`,
  older than a pile of freshly-ranked `Evidence`). Complements
  `exempt_types` (for a type with _no_ real content, e.g. a pure
  wake-up marker) rather than replacing it — `min_keep` items still count
  toward `max_tokens`, they're just guaranteed a reserved slot instead of
  being left to rank order. Closes the one specific case the module's own
  "known limitation" docstring had flagged as unsolved.
- `reactifact.consume.CorrelatedConsume` — fires (and feeds inputs) only for
  a correlation key where every type in `require` is present and every type
  in `forbid` is absent, e.g. an approval gate waiting on a `Report` and its
  answered `PendingQuestion`, correlated by `thread_id`. Without it, that
  condition had nowhere declarative to live: `Consume.condition` only ever
  sees the single artifact matched by its own type, never a _different_
  type's instance to correlate against — the real cases seen so far (that
  approval gate; a plan step waiting on its prerequisite `StepResult`; not
  re-filing a `HelpdeskTicket` a thread already has) all hand-rolled the
  correlation _inside_ `produce()`, exactly the logic `consumes` exists to
  keep out of there. `JoinConsume(*parts, key=...)` and
  `AbsentConsume(artifact_type, absent_type=..., key=...)` are thin
  factories over it for the two common single-purpose shapes (`require`-only
  and one-`require`-one-`forbid`) — started as two separate classes, unified
  into one mechanism once both turned out to be the same "group by key,
  check who's in the group" scan with a different pass/fail rule; unifying
  also unlocked combining both at once ("required present _and_ forbidden
  absent" together), which neither original class could express without
  nesting one inside the other. Listens to every `require` type's own
  CREATED/UPDATED events (not `forbid` types'), so the agent wakes up
  regardless of which required type completes the group last — takes any
  number of `require` types (`>= 1`), not just two, verified live against a
  three-type join. Required two small, backward-compatible core extensions
  to get there: `Trigger.context_condition(artifact, context)`, alongside
  the existing per-artifact-only `condition`, since a correlation condition
  fundamentally needs to look at _other_ artifacts, not just the one the
  event is about — and `Consume.collect(context)`, replacing the per-type
  loop that used to be inlined in `Agent._collect_inputs()`, so
  `CorrelatedConsume` (and any future non-single-type `Consume`) can
  override how it contributes to an agent's inputs, not just how it
  triggers.
- `Consume(debounce=bool)` (default `False`) — when several events in the
  same generation would each independently wake an agent via this
  `Consume` (a fan-out step creating five `Evidence` artifacts in one
  commit, five separate `ARTIFACT_CREATED` events), collapses them into a
  single run instead of five. Unlike `JoinConsume`/`AbsentConsume`, this
  couldn't be built as a `Consume` subclass alone: collapsing needs to
  compare _other_ events in the same drained batch, which is state only
  `Runtime` has — `Consume`/`Trigger` (new `Trigger.debounce` flag) just
  carry the setting, `Runtime._arun_once_impl` does the actual collapsing
  (last matching event in the batch wins) before the `max_runs` budget
  check, so a debounced run correctly costs one against the budget, not
  five. An agent with a mix of debounced and non-debounced matching
  triggers for the same event is treated as non-debounced for that event —
  debouncing only kicks in when nothing about the match demands immediacy.
  New `Agent.matching_triggers()` (`matches()` is now `bool(...)` of it)
  is what lets `Runtime` see _which_ triggers matched, not just whether
  any did, to check the flag.

- `Produce(reacts_to=...)` — the input-side mirror of `also_creates`: an
  agent with several `consumes` and several `produces` runs _every_ produce
  on _every_ matching event by default, since `Agent.execute()` has no idea
  which of an agent's several `Consume`s a given produce actually cares
  about — every produce ends up guarding itself by hand at the top of its
  own body (`if event is None or not isinstance(context.get(event.
artifact_id).data, TheOneTypeICareAbout): return None`). Declaring
  `reacts_to = (TheType,)` moves that guard to the class declaration
  instead; `Agent.execute()` now skips calling `produce()` at all for an
  event none of `reacts_to` matches (exact type equality against
  `event.artifact_type`, same convention as `Trigger.artifact_type` — not
  `issubclass`, that's a `Context.list_artifacts()` convention for a
  different purpose). `None` (the default) is unrestricted — today's
  behavior, unchanged, so this is purely additive. Considered
  `Produce[ReactType, CreateType]` (a second generic parameter) instead —
  rejected: `Generic[A, B]` enforces exactly two type arguments at class
  _definition_ time, so every existing single-argument `Produce[Foo]`
  subclass across this repo's own examples/tests, and any downstream
  product, would fail to import, not just fail a type check. Two produces
  sharing one `artifact_type` (the same output) but different `reacts_to`
  (different triggers) is also why this can't just reuse `artifact_type`
  for both directions — the case that motivated this in the first place was
  exactly that shape, a `FinalizeWithDocuments`/`DirectFinalize`-like pair
  both producing the same result type from two different upstream events.

### Breaking

- `produce()` now takes exactly one argument, `call: ProduceCall`, instead
  of the individually-recognized `(context, inputs, event=None)` parameters
  (class-style) or by-name-sniffed `(context, inputs, event, effects)`
  (`@produce`-decorated functions). `ProduceCall` carries `.context`/
  `.inputs`/`.event`/`.effects` (identical to the old parameters/
  `self.effects`) plus a new `.trigger` field: the already-resolved artifact
  behind `.event` (what `context.get(event.artifact_id) if event is not
None else None` used to compute by hand in nearly every produce body).
  One object, not a growing list of individually-recognized parameter
  names, was chosen deliberately over extending the by-name signature
  sniffing further (which already covered `event`/`effects`, and would next
  have needed to cover `trigger` too): every field is now visible from an
  editor's autocomplete on `call.` without cross-referencing which
  parameter-name combination does what, and the shape no longer changes
  every time a new capability is added — a real design cost, weighed
  against breaking every existing `Produce` subclass and `@produce`
  function in this repo (updated across `reactifact/`, all of `examples/`,
  and all of `tests/` in this same release) and any downstream user's code.
  `self.effects` is unchanged on class-style `Produce` (kept for produces
  that don't want to thread `call` through helper methods) — only the
  `produce()` signature itself changed.
- `.trigger` is a real guarantee, not best-effort convenience, whenever the
  produce also declares `reacts_to`: for a CREATED/UPDATED/STALE event,
  `Agent.execute()` now skips calling `produce()` entirely if
  `context.get(event.artifact_id)` no longer resolves (the artifact was
  deleted by another agent earlier in the same generation — the same race
  `Trigger.matches()` already documented), so `call.trigger` is never `None`
  when such a produce actually runs, and the body needs no guard at all. A
  DELETED event on the produce's own `reacts_to` type is exempt from that
  liveness requirement — there, `context.get(...)` correctly returning
  `None` _is_ the event, not a race, so the produce still runs with
  `call.trigger` set to `None`, and deletion-reacting code is expected to
  handle that itself (`call.event` still carries `artifact_id`/
  `artifact_type` there, which `call.trigger` necessarily can't once the
  data is gone). Without `reacts_to` (`None`, unrestricted), `call.trigger`
  is still resolved and passed whenever `call.event` is not `None`, purely
  as a convenience — there's no per-type contract for the framework to
  enforce, so it's never a reason to skip the call.
  Considered making `reacts_to` mandatory (so every produce reads as
  explicit about what it reacts to, and `call.event` could be dropped in
  favor of an always-non-`None` `call.trigger`) — rejected: several of this
  repo's own examples (`Combine`/`Finisher`/`Supervisor`-shaped aggregators)
  genuinely react uniformly to more than one type by design, and `call.event`
  carries information after a DELETED event that `call.trigger` structurally
  cannot — dropping it would lose real information for deletion-reacting
  produces, not just remove ceremony.

### Fixed

- `Consume(wakes=False, debounce=True)` now raises at construction instead
  of silently doing nothing — `debounce` only collapses repeat wake-ups, and
  `wakes=False` means this `Consume` never wakes the agent in the first
  place, so the combination had no meaning to begin with.

## [0.8.0] — 2026-09-16

Harness building blocks (approval gate, context budget, inline
verification, sub-agent delegation), three new recipes, a full worked
example composing all of them, Python 3.11–3.14 support, a real docs site,
and a round of concurrency/persistence correctness fixes. No breaking
changes — see [docs/roadmap.md](docs/roadmap.md#next) for why this isn't
`1.0` yet: the new surface (`context_builder`/`verify`/`agent_tool`) needs
a hardening period before it's worth a semver promise, and 1.0's own
stated bar (MCP hardening, more `Source` integrations) isn't met yet.

### Docs

- `docs/roadmap.md` now spells out three explicit API-stability tiers for
  1.0 (Core / "In the box" / Recipes) instead of one flat "the public
  surface" — clarifies where `context_builder`/`verify` (core, since they
  extend `RuntimeResources`), `agent_tool.AgentAsTool` and the
  destructive-tool approval gate ("in the box", same tier as `Tool`/MCP),
  and this release's three new recipes (recipes tier, already covered by
  the existing "freeze the recipes" commitment) each sit.
- A real docs site (`mkdocs.yml`, Material theme) built from the existing
  `docs/en/` tree, deployed to GitHub Pages on every push to `master` that
  touches `docs/**`/`mkdocs.yml` (`.github/workflows/docs.yml`,
  `mkdocs gh-deploy`). EN-only for now; `docs/ru/` already mirrors the
  structure 1:1 and is a locale-switcher follow-up (`mkdocs-static-i18n`),
  not wired into the nav yet. New `docs` optional-dependency group
  (`mkdocs`, `mkdocs-material`); `project.urls.Documentation` now points
  at the site instead of the GitHub file tree.
- `docs/reference.md` — auto-generated API reference (`mkdocstrings[python]`):
  signatures, type hints, and full docstring text rendered straight from
  source for every Core/"in the box" primitive, instead of hand-copied.
  `docs/en/api.md` and `docs/ru/api.md` now link to it.

### Added

- `examples/incident_commander` — a full harness composed in one scenario:
  deterministic keyword classification (`classify_targets`) decides which
  places (k8s, the database) an incident actually touches — not "always
  fork everything" — then each relevant place is investigated
  independently on its own fork and merged back (`Context.branch()`/
  `merge()`, §39-§40), sub-agent delegation (`agent_tool.AgentAsTool`, a
  DBA specialist on the database fork), a token-bounded rolling synthesis
  (`context_builder.TokenBudgetContextBuilder`), a destructive-tool
  approval gate (`tool_use.ToolUseHITL`), and inline verification
  (`verify.Verify`, `provenance_grounded` required) before an incident is
  considered resolved. Orchestration lives in `pipeline.py` (same split as
  `examples/forklab`); runs fully offline via a scripted provider
  (`ToolUseHITL`'s own decision loop has no offline fallback).
- **Destructive-tool approval gate** (`ToolUseHITL`, `tool_use.py`):
  destructive tools are now offered to the LLM instead of excluded outright
  — a `tool_call` targeting one creates a `PendingQuestion(kind="approve")`
  instead of running immediately, and only executes once a human approves.
  New `max_approvals` (also on `HITLLMAgent`) bounds how many times one
  conversation can ask. `ToolUse` (the non-HITL loop) is unchanged —
  destructive tools stay excluded there, there being no pause to gate on.
- `reactifact.context_builder`: `ContextBuilder`/`DefaultContextBuilder`/
  `TokenBudgetContextBuilder` + `TokenCounter`/`HeuristicTokenCounter` —
  Runtime-level policy for ranking/truncating an agent's consumed artifacts
  under a token budget, set via `RuntimeResources(context_builder=...)`.
  Applied inside `Agent._collect_inputs`, so both the runtime's provenance
  (`Runtime._collect_reads`) and an agent's actual produce inputs go through
  the same builder call and stay in sync.
- `reactifact.verify`: `Verify`/`VerificationResult`/`VerificationFailed` —
  inline verification of a live `Answer` using `eval.py`'s ground-truth-free
  `core_metrics`, instead of only after a pipeline finishes. On failure,
  escalates via the same HITL approve flow (`on_fail="ask"`, default) or a
  `VerificationFailed` marker for a regenerating agent (`on_fail="retry"`).
  The pass/fail threshold is a framework-wide default
  (`RuntimeResources.verification_threshold`) any `Verify` can still
  override for itself.
- `reactifact.agent_tool.AgentAsTool` — wraps a sub-agent as a `Tool`:
  delegation runs an isolated, fresh nested `Context`/`Runtime` to
  completion and returns the final answer as the tool result. HITL
  sub-agents are rejected with an explanatory error (an unanswered
  `PendingQuestion` inside the isolated context has no one to answer it),
  not silently returned as empty text.
- Three new `reactifact.recipes`, generalizing the corresponding
  `examples/` demo (side-by-side `main_recipe.py` added to each, alongside
  the original hand-rolled `main.py`):
  - `PlanExecute` — sequential plan → execute → finish; the recipe owns
    ordering, gating each step on its predecessor, idempotent re-entry, and
    completion detection (now also supports several concurrent goals in one
    `Context`, which the ported example did not).
  - `Router` / `ApprovalGate` — classify a request into a route with a
    deterministic fallback, and ask a human to sign off on a report before
    finalizing it; uses `kind="approve"`, the same vocabulary the
    destructive-tool gate and `Verify` use.
  - `ReflectionLoop` — generate → critique → regenerate; the recipe owns
    round-capping, the accept threshold, and completion detection.
- CI now also runs the full suite (tests + mypy) on Python 3.13 and 3.14,
  alongside the existing 3.11/3.12 — `requires-python` was already
  unbounded above 3.11, this just confirms and enforces it.

### Fixed

- CI/release/docs workflows were pinned to `setup-uv`'s `version: "0.5.x"` —
  stale enough that its bundled Python-version index still resolved
  "3.14" to an unstable `3.14.0a5` alpha build instead of the real 3.14
  stable release, which segfaulted importing `pydantic` (an alpha
  interpreter's ABI is not what compiled extensions like `pydantic-core`
  were built against). Bumped to `"0.10.x"` in all four workflow steps
  (`ci.yml` ×2, `docs.yml`, `release.yml`) — not a reactifact bug, a stale
  CI tool pin.
- `Context.list_artifacts(T)` no longer calls `isinstance()` per artifact
  in the `Context` — an incrementally maintained `type(data) -> ids` index
  turns it into a union over the (usually small) set of distinct types ever
  created, still walked in insertion order so ties in a caller's own sort
  key (e.g. `updated_at`) break the same way as before. Was the hottest
  path in the framework: `Runtime._collect_reads`/`Agent._collect_inputs`
  call it for every agent on every matching event.
- `checkpoints.FileBackend.save()` now writes atomically (tmp file +
  rename), matching the neighboring `FileKVBackend`. Previously a process
  killed mid-write (OOM, deploy, `kill -9`) left a truncated file that
  `load()` would fail to parse — the exact failure mode the neighbor
  already guarded against.
- `Runtime.arun()`/`arun_once()` are no longer silently reentrant: a second
  concurrent call on the same `Runtime` instance (e.g.
  `asyncio.gather(runtime.arun(), runtime.arun())`) now raises `RuntimeError`
  instead of racing on shared turn state (`budget`, `outcome`, the deadline
  written into `context.resources`). Use a separate `Runtime` per concurrent
  request (they can share `Context.resources`).
- `Runtime._dispatch`'s parallel fan-out (`max_concurrency`/
  `concurrency_limit`) no longer leaves sibling agent tasks running
  unawaited in the background when one of them raises
  (`isolate_errors=False`, the default): `gather(..., return_exceptions=True)`
  now waits for every sibling to actually finish before the first exception
  is re-raised (unwrapped, same type as before).

## [0.7.0] — 2026-09-14

Pre-1.0 API-freeze cleanup.

### Breaking

- **`Produce(Model, factory=fn)` (deprecated with a `DeprecationWarning`
  since 0.5.0) is removed.** Port any remaining usage to the `@produce(Type)`
  decorator (same return-style signature, plus optional `effects`/`event`
  params) — see [docs/en/effects.md](docs/en/effects.md).

### Fixed

- `Context.merge_from` (and the underlying `merge_context_from`) now keeps
  the original id of an artifact that exists in `other` but not in `target`
  — it previously minted a fresh id for it, silently detaching the merged
  artifact from any relation or reference that pointed at its original id.
- `Trigger.matches`'s second parameter is renamed `workspace` → `context`
  (pre-1.0 API-consistency pass — `Workspace` was the project's old name,
  every other call site already says `context`). Positional-call code is
  unaffected; nothing in the codebase called it by keyword.
- `RuntimeResources.budget`/`.budget_deadline` are now typed fields, set by
  `Runtime` each turn — internally replacing the untyped
  `resources.set("budget", ...)`/`resources.get("budget_deadline")` pair
  `ToolUse`'s inner loop used to read the active `Budget` through. The
  general-purpose `resources.get`/`.set` bag (for your own app resources —
  a catalog, a skills list, ...) is unaffected.

### Added

- `reactifact.tool_use.DeferredToolGroup` + `ToolUse(deferred_tool_groups=)`
  (also `LLMAgent.deferred_tool_groups`): a named group of tools whose
  schemas stay out of the system prompt — only a compact catalog (name,
  description, bare tool names) — until the LLM asks for the group by name
  via a built-in `load_tools` tool. `loader` (an async, zero-arg callable,
  e.g. wrapping `mcp_stdio_tools(...)`) runs at most once per group per
  run. Fixes real context bloat when several MCP servers/tool sources are
  connected at once and most of their tools go unused in any given run.
  `ToolUseHITL`/`HITLLMAgent` don't support it yet — its reactive, resumable
  loop would need "which groups are loaded" as persisted state across
  `produce()` calls, not a local variable; see the class docstring. See
  [docs/en/patterns.md](docs/en/patterns.md#deferred-tool-groups-many-tools-without-the-context-cost).
- `reactifact.tracing.OTLPTracer(endpoint=, headers=, service_name=)`: a
  vendor-neutral tracer sink for any OTLP/HTTP collector (Jaeger, Tempo,
  Honeycomb, Datadog Agent, a local `otel-collector`, ...) — GenAI
  semantic-convention attributes (`gen_ai.*`) plus a `reactifact.*`
  namespace for reads/writes/provenance, no `langfuse.*` keys. Built on the
  same hand-rolled OTLP/HTTP JSON payload `LangfuseTracer` already posts
  over plain `httpx` (factored into `reactifact.tracing._otlp`, shared by
  both sinks) — no `opentelemetry-sdk` dependency, no new extra. See
  [docs/en/observability.md](docs/en/observability.md#otlptracer-any-otlphttp-collector).
- `reactifact.mcp.oauth_client_credentials(server_url, client_id=, client_secret=,
issuer=)`: builds an `auth=` value for `mcp_http_tools` using OAuth's
  `client_credentials` grant (machine-to-machine, no browser/human consent) —
  a thin wrapper over the MCP SDK's own `ClientCredentialsOAuthProvider`,
  defaulting to a new `InMemoryTokenStorage` (pass your own `TokenStorage` to
  persist tokens across restarts). `mcp_http_tools` gained the `auth=` kwarg
  to carry it (alongside the existing `headers=`, for static credentials).
  Scoped to `client_credentials` only — the authorization-code flow needs a
  host-specific redirect/callback handler outside this library's scope; use
  `mcp.client.auth.OAuthClientProvider` directly for that. See
  [docs/en/mcp.md](docs/en/mcp.md#oauth-client_credentials).
- `mcp_http_tools(url, headers=...)`: an optional `headers` kwarg for
  connecting to MCP servers that require auth (e.g. `Authorization: Bearer
...`). Previously there was no way to reach such a server at all over
  streamable HTTP.
- `reactifact.scheduler.relation_balance_metric`: the built-in
  uncertainty-driven `Metric` for `uncertainty_policy`/`Scheduler` (§26).
  Ranks a candidate by how lopsided the `supports`/`contradicts` relations
  are on the artifact its event is about — deterministic, provider-agnostic,
  the same structural signal `examples/medic_lab` computed by hand for its
  report only (`produce/evaluate.py`). `examples/medic_lab` now wires it into
  its own `Runtime(scheduler=medic_lab_scheduler())`, so the most-contradicted
  open hypothesis is actually investigated first, not just ranked highest in
  the final report.

### Docs

- `docs/{en,ru}/concepts.md` §4 (Agent): explicit paragraph on `Agent.run()`
  as the low-level escape hatch — it was only documented in the class's own
  docstring before, easy to miss from the newcomer-facing docs.
- `reactifact/branching.py`'s module docstring now says explicitly that
  application code should call the `Context.clone/branch/merge/merge_from`
  methods, not the module-level `clone_context`/`fork_context`/
  `merge_contexts`/`merge_context_from` functions they delegate to — the
  functions stay exported for building on a `Context` you don't have in
  hand yet (e.g. `BranchStore`'s own load/merge path), not as an equally
  first-class alternative entry point.
- New [docs/en/troubleshooting.md](docs/en/troubleshooting.md) /
  [docs/ru/troubleshooting.md](docs/ru/troubleshooting.md): symptom-first
  ("my agent didn't run" / "ran twice" / "the run stopped early" / "the
  scheduler picked the wrong one") diagnosis guide pointing at the concrete
  tool for each (`reactifact graph`/`reactifact trace`, `RunOutcome`/
  `RunStats`, `context.related`/`context_to_mermaid`, `reactifact.testing`)
  — distinct from `observability.md`, which is a feature list of the tracing
  tooling, not a "you're stuck, here's the fastest path" guide.
- `docs/en/design-notes/adaptive.md` / ru: mentions `relation_balance_metric`
  and its `examples/medic_lab` wiring (was written before that metric
  existed).
- `docs/{en,ru}/comparison.md`: new "Starting from scratch" section — the
  existing "If you're evaluating both" trial assumes a LangGraph/CrewAI
  project to port from; this is the equivalent falsifiable exercise for a
  greenfield reader with nothing to port, ending with an explicit invite to
  report the result on
  [GitHub Discussions](https://github.com/bzdvdn/reactifact/discussions).
- README/`docs/*/sources.md` no longer claim direct-API/keyword/SQL sources
  are shipped ("equally first-class") — only filesystem/CSV/web are today;
  the others are linked to the [roadmap](docs/roadmap.md#next) instead of
  overstated.
- `docs/constitution.md`'s implementation-status appendix refreshed (test
  count, MCP/testing-harness/adaptive-scheduler rows, the `plan_execute`
  canonical port) — it had drifted since `ver 0.2`.
- New "Security model" section in [docs/en/mcp.md](docs/en/mcp.md) /
  [docs/ru/mcp.md](docs/ru/mcp.md): reactifact has no built-in
  authorization primitive (§57 is "planned", not "implemented") — spelled
  out concretely for `create_mcp_server` (read-only context resources with
  no per-field redaction; tools are not sandboxed by MCP exposure).

### Tests

- `tests/test_canonical_ports.py`: a smoke test per canonical port example
  (`reflection`, `map_reduce`, `supervisor`, `summarize`, `time_travel`,
  `plan_execute`) — these were previously only eyeballed by hand, so a core
  API change could silently break the very code the docs point newcomers at
  as reference, without CI ever noticing.

### CI

- `pyproject.toml` now sets `[tool.coverage.report] fail_under = 85` —
  `pytest --cov` (already run in CI) enforces it automatically. Current
  coverage is ~90%; the floor leaves headroom for
  `reactifact/tracing/postgres.py` (intentionally under-covered — its own
  test only checks the graceful-`ImportError` path, `pg` being an opt-in
  extra with no external service in CI) without a per-file carve-out.
- PyPI classifier bumped `3 - Alpha` → `4 - Beta` (`5 - Production/Stable`
  is reserved for the actual `1.0.0` tag, not before).

## [0.6.1] — 2026-09-11

Added `reactifact.mcp` (`mcp` extra, optional — the core stays dependency-free):
`mcp_stdio_tools`/`mcp_http_tools` connect to an MCP server and hand back its
tools as ordinary `Tool`s for `ToolUse`/`LLMAgent`; `create_mcp_server` exposes
reactifact `Tool`s — and, with `context=`, a running `Context`'s artifacts as
two read-only resources — as an MCP server for Claude Desktop, Claude Code, or
another agent. Verified end-to-end over the real MCP protocol (in-memory
transport, not mocked): tool schemas, destructive annotations, tool errors
(`ToolOutput.error` → MCP `is_error`), and context resources all round-trip
correctly. See [docs/en/mcp.md](docs/en/mcp.md).

`LangfuseTracer` now ships spans via OTLP/HTTP (`POST /api/public/otel/v1/traces`)
instead of the legacy `POST /api/public/traces` + `POST /api/public/observations`
REST ingestion, which Langfuse already rejects on v4 self-hosted and sunsets on
Langfuse Cloud 2026-11-16. `LangfuseTracer(...)`'s constructor signature is
unchanged; verified against a real local Langfuse v4 instance (Docker) — spans
land with the correct type (`SPAN`/`GENERATION`), parent/child hierarchy,
token usage, and session id.

PyPI metadata: SPDX `license`, `keywords`, and `classifiers` (Python versions,
Development Status, Topic) now ship in the package, so `reactifact` actually
shows up in PyPI's own filters instead of just full-text search.

Docs: a new [MCP guide](docs/en/mcp.md), a scope note on Skills (instructions
only, deliberately no bundled-script execution — see
[docs/en/recipes.md#skills](docs/en/recipes.md)), a wiring diagram in the
README and [comparison](docs/en/comparison.md), flow diagrams for the
`knowledge`/`devops`/`repair`/`forklab` examples, a CLI demo GIF, and trace
dashboard screenshots in [observability](docs/en/observability.md). Fixed a
hardcoded Russian string in the trace dashboard's own UI (`tracing/templates/ui.html`).

## [0.6.0] — 2026-09-10

Project renamed from `ctxloom` to `reactifact` — the old name collided with
an unrelated, actively developed GitHub project. Package, imports, CLI env
var (`CTXLOOM_SCENARIO_MODE` → `REACTIFACT_SCENARIO_MODE`), docs, and
examples all updated; no functional changes.

## [0.5.0] — 2026-09-08

Performance and correctness pass across the core runtime (staleness
tracking, session persistence, event-loop blocking, a budget-deadline gap in
`ToolUse`, a `ChatAssistant` concurrency race), a breaking trim of the
public API surface down to core primitives, an internal split of `Runtime`'s
tracing and `Context`'s fork/merge logic into their own modules, and the new
`reactifact.testing` scenario harness.

### Breaking

- **`reactifact/__init__.py` now exports only the core surface** — the
  primitives from the README's "Core primitives" section plus tool calling,
  sessions, and the LLM provider protocol (~40 names, down from ~150).
  Eval, tracing, checkpoint/branch backends beyond the in-memory default,
  the chat/web layer, the adaptive scheduler, replay, structured-LLM
  helpers, viz, and prompt templates are no longer re-exported at the top
  level — import them from their own submodule instead, e.g.:

  ```python
  from reactifact.structured import structured_llm
  from reactifact.tracing import TraceStore
  from reactifact.chat import ChatAssistant
  from reactifact.checkpoints import FileKVBackend, SQLiteKVBackend
  from reactifact.eval import EvalCase, run_suite
  from reactifact.llm_agent import HITLLMAgent
  from reactifact.replay import ReplayLLM
  from reactifact.scheduler import Scheduler
  from reactifact.tool_use import ToolUse
  from reactifact.viz import blueprint
  from reactifact.branching import BranchStore
  from reactifact.prompts import PromptTemplate
  from reactifact.streaming import ProgressEvent
  ```

  Nothing moved or was renamed — every symbol still lives in the same
  module it always did; only the top-level re-export was removed. Grep your
  codebase for the names above under `from reactifact import` and repoint them
  at their submodule.

### Performance

- `Context.stale_artifacts()`/`has_stale()`/the internal dependents lookup
  behind `update()` no longer rescan every artifact in the context on every
  call. `CommitLog` now maintains a reverse index (source artifact →
  dependents) and a `producing_commit` lookup incrementally as commits are
  appended, and `Context` keeps an incrementally-updated `_stale` set.
  `producing_commit()` is O(1) instead of an O(commits) reverse scan;
  `stale_artifacts()`/`has_stale()` are O(stale count) instead of O(context
  size). Matters most for long-lived sessions/knowledge bases where a
  context accumulates thousands of artifacts but any one update only
  invalidates a handful of them.

- **`Session.save()` (called after every commit — see `Runtime._commit_patches_to_apply`)
  no longer re-serializes the whole context from scratch each time.**
  `Artifact.to_dict()` is now memoized per `version` (an artifact's full
  history only needs re-encoding once, not on every unrelated save), and
  `CommitLog.to_dict()` is memoized per commit id (a commit is immutable
  once appended). Separately, `FileKVBackend._set_sync` now builds the JSON
  via `json.dumps()` + one `write()` instead of `json.dump(obj, f)` —
  `json.dump()`'s streaming encoder never takes CPython's C-accelerated
  path (only `.encode()`, what `dumps()` uses, does), so for a large
  payload it was walking the entire object graph in pure Python on every
  save. Measured on a session with 3000 accumulated artifacts/commits:
  `Context.to_dict()` 11ms → 1.3ms (cache warm), full `session.save()`
  ~68ms → ~19ms. Both caches are keyed so they can never go stale (version
  number; commit id) and need no manual invalidation.

### Fixed

- **`ToolUse`'s blocking tool-call loop now respects `Budget.max_seconds`
  between its own internal steps.** `Runtime._budget_exhausted()` only
  checks the deadline _between_ agent runs; `ToolUse._loop` makes up to
  `max_steps` sequential LLM/tool round-trips inside one `produce()`, and
  previously only checked `Budget.max_tool_calls` there — a slow provider
  could blow well past `max_seconds` before the runtime ever got a chance
  to see it. `Runtime._begin_turn` now also publishes the computed deadline
  as `resources.get("budget_deadline")`; the loop checks it between steps
  and falls through to the existing forced-answer path, same as hitting
  `max_steps`. `ToolUseHITL` didn't need this — it's already one step per
  reactive turn, bounded by the runtime's own per-generation check.
- **`FileSystemSource`/`CSVSource`/`EmbeddingSource` no longer block the
  event loop.** `asearch()` (what `fan_out_sources` actually calls) used to
  default to calling the synchronous `search()` directly, which walks and
  reads every matching file under `root` — for a large corpus, that stalls
  the whole runtime (every concurrently-running agent) for as long as the
  scan takes. Each now offloads via `asyncio.to_thread`, the same pattern
  `reactifact/checkpoints.py`'s backends already use. `resolve()` on all three
  is offloaded too. `search()` itself is unchanged (still synchronous,
  still directly usable/testable without an event loop).
- **`EmbeddingSource.invalidate()`**: the vector index was built once,
  lazily, and cached forever with no way to pick up files that changed
  under `root` afterward. Now documented as a known limitation, with an
  explicit method to drop the cache and force a rebuild on the next search.
- **`ChatAssistant.stream()`/`.invoke()` now serialize concurrent turns on
  the same `session_id`.** Two overlapping calls for the same session (a
  double submit, a client retry) both used to load the same starting state
  and run independently, and whichever `session.save()` landed last won —
  silently dropping the other turn (§59: no silent data loss). A per-session
  `asyncio.Lock`, created lazily and dropped once uncontended (bounded by
  concurrently-active sessions, not every session_id ever seen), now makes
  the second call wait for the first instead of racing it. Different
  `session_id`s are unaffected — this doesn't serialize the whole assistant,
  only same-session turns. Verified with a concurrent same-session test:
  both turns' messages and derived artifacts persist (previously the second
  `save()` could overwrite the first's). Scoped to `ChatAssistant` — the
  lower-level `run_message` building block (for custom transports/loops)
  has no such guarantee, by design; its own caller owns that story.

### Known limitation (documented, not fixed)

- `ToolUseHITL`'s history/duplicate-question lookups
  (`context.list_artifacts(Observation | PendingQuestion)` filtered by
  `query_id` in Python) scale with the total count of that artifact type
  across _every_ conversation sharing one `Context`, not just the current
  one — `RelationGraph` isn't indexed by `source_id` either, so routing
  through `context.related()` instead wouldn't actually help without also
  indexing that. Only matters if you share one long-lived `Context` across
  many concurrent tool-use conversations instead of the standard
  one-`Context`-per-session pattern (`SessionStore`, every example here) —
  left as a documented trade-off (see the `ToolUseHITL` class docstring)
  rather than done as a drive-by alongside the fixes above.
- `chat.default_session_state` sorts _every_ artifact in the context
  (`ctx.list_artifacts()`, no type filter) on each call — a view-endpoint
  cost (`ChatAssistant.history()` / `GET /api/runs/{id}`), not a per-commit
  one, so left as-is rather than optimized alongside the session-save fixes.

### Changed

- **`Runtime`'s tracing plumbing moved to `reactifact.tracing.RunTracer`.**
  Span/trace building (`ArtifactRef`/`RelationRef`/`AgentSpan`/`RunTrace`
  construction), the `RecordingLLM` wrap of `resources.llm`, and the
  task→agent attribution that wrap needs were previously interleaved with
  `Runtime`'s dispatch loop (`_artifact_ref`, `_write_refs`,
  `_relation_refs`, `_read_refs`, `_record_llm`, `_agent_by_task`, ...).
  They're now one collaborator (`RunTracer`, a no-op when no tracer is
  configured) that `Runtime` calls into at a few points
  (`begin_turn`/`record_span`/`write_refs`/`relation_refs`/`end_turn`).
  `Runtime` drops from 640 to ~490 lines and is now just the dispatch loop;
  no public behavior changes (`Runtime.tracer` stays a public attribute,
  `runtime.arun()`/`astream()`/`Tracer`/`CompositeTracer` are unaffected).
  Internal-only rename, not covered by the API-surface breaking change
  above: nothing here was ever part of the public API.

- **`Context`'s fork/merge algorithm moved to `reactifact.branching`.**
  `clone()`/`branch()`/`merge_from()`/`merge()` used to carry the full
  three-way-merge implementation (~180 lines) inline in `context.py`,
  reaching into `_artifacts`/`_relations` directly. That logic is now
  `clone_context`/`fork_context`/`merge_context_from`/`merge_contexts` in
  `reactifact/branching.py`, next to `BranchStore` (which already persisted
  forks, just didn't own their semantics). `Context.clone()/.branch()/
.merge_from()/.merge()` are now one-line delegations — same signatures,
  same behavior, including the pre-existing quirk where `merge_from()`
  mints a fresh id for an artifact absent from the target (not "fixed" as
  part of this move — a real behavior change belongs in its own change).
  `MergeConflict` moved with the algorithm: `reactifact.branching.MergeConflict`,
  still re-exported as `reactifact.MergeConflict` — code importing it from
  `reactifact.context` directly (nothing in this repo did) would need to
  switch to `reactifact.branching` or the top-level import. `context.py` drops
  by ~150 lines; conflict messages now say `target=`/`other=` instead of
  `self=`/`other=` (cosmetic — no test or example asserted on the old
  wording).

### Added

- **`reactifact.testing`**: a scenario-based behavioral testing harness for agent
  pipelines. `ScenarioLab`/`Scenario` (`reactifact scenario` CLI) seed artifacts
  into a fresh `Context`, run a `Runtime` to completion, and return a
  `ScenarioResult` with chained assertions (`.artifacts(...)`, `.tools`,
  `.path`, `.llm`, `.errors`). `Scenario` (`lab.scenario()`) supports
  multi-turn conversations on one shared `Context`, for flows that need
  several rounds to reach the state under test.
- Tool fault injection (`lab.fail(tool_name, error, times=None)`) and
  generic resource fault injection (`lab.fail_resource(name, error,
method=None, times=None)`) — the latter fails the LLM, the embedder, a
  named source, or any `resources.set(...)` value via a duck-typed
  reflection proxy (`reactifact/testing/mock.py`) that correctly handles sync,
  async, and async-generator methods.
- Record/replay LLM wrapping (`reactifact/testing/record.py`, reusing
  `ReplayLLM`) and a `@scenario` registry (`reactifact/testing/registry.py`)
  for discovering and running scenarios via `reactifact scenario <module>...`.
- Assertion sugar: `ArtifactAssertions.equals/.contains/.field_in`,
  `PathAssertions.any_of/.times`, `ToolAssertions.called_any`.
- Worked examples: `examples/repair/scenarios/`, `examples/knowledge/scenarios/`.

## [0.4.0] — 2026-09-04

Work since 0.4.0-rc1 — internal cleanup, dedupe, and an async-native
checkpoint/session/branch layer. First stable (non-rc) release.

### Breaking

- `Produce(Model, factory=fn)` is deprecated (`DeprecationWarning` on
  construction): it predates `@produce`, only supports `(context, inputs[,
event]) -> Model | list | Patch | None`, and cannot see the effects slot.
  Still works for existing code; the canonical styles going forward are the
  `Produce` subclass and the `@produce` function.
- Sessions, branches and checkpoints are now **async**: `Session.save`/
  `.delete`, `SessionStore.{save_session,load_session,has_session,
list_sessions,delete_session,open}`, `BranchStore.{save_branch,load_branch,
list_branches,delete_branch}`, `Context.{save_checkpoint,load_checkpoint,
to_kv,from_kv}`, and `replay_context` are all `async def` — call them with
  `await`. `KVBackend`/`CheckpointBackend` and every implementation
  (`File*`, `SQLite*`, `PostgreSQLKVBackend`) follow the same interface
  change; a new `aclose()` releases held connections.

### Added

- **Async checkpoint backends**: `FileKVBackend`/`FileBackend` offload
  blocking file I/O via `asyncio.to_thread`; `SQLiteKVBackend`/
  `SQLiteBackend` share one persistent connection (WAL + `busy_timeout=5000`)
  serialized by an `asyncio.Lock` instead of reconnecting on every call;
  `PostgreSQLKVBackend` moves to psycopg's native `AsyncConnection`. Fixes
  the connection-per-operation overhead and the missing lock-wait timeout
  that could raise `sqlite3.OperationalError: database is locked` under
  concurrent writers.
- `reactifact/cli/` package: `graph`/`context`/`trace`/`replay`/`branch` are now
  one module each (`add_parser()` + handler) instead of living in a single
  334-line `__main__.py`, which is now a thin entry point.
- `_openai_compat_llm()`/`_openai_compat_embedder()`/
  `_openai_compat_speech()`/`_openai_compat_transcriber()` factory builders
  (`providers/chat.py`, `providers/speech.py`) — the one implementation
  behind every OpenAI-compatible vendor. New vendor coverage verified
  against each vendor's docs: `openrouter_embedder`, `openrouter_speech`,
  `groq_transcriber`, `together_embedder`, `fireworks_embedder`,
  `qwen_embedder`, `nvidia_embedder`. `mistral_llm`/`mistral_embedder` now
  use the same factory instead of hand-rolled env/auth resolution — all 13
  OpenAI-compatible vendors are consistent.
- Retry/backoff (`providers/_retry.py`, `with_retry()`): 429/5xx and
  transport errors now retry with exponential backoff on every network call
  across the package (chat, embeddings, image generation + its URL-fetch
  fallback, TTS, transcription, all four video providers). 4xx is never
  retried; streaming calls are not retried (can't replay already-yielded
  chunks).
- `Runtime(isolate_errors=True, on_agent_error=...)`: one agent's exception
  can skip that agent's patch instead of aborting the whole `arun()`/
  `astream()` — default stays fail-loud. Isolated errors get a traced
  `AgentSpan(error=...)` and count toward `RunStats.errors`.
- `on_error(reason, exc)` hook on `structured_llm`/`llm_reply`/
  `StructuredLLM`, called right before the honest `None` fallback — lets
  callers distinguish "offline" from "the provider is down" without
  changing the `None`-returning contract.
- `effects.upsert(data, id=...)` and `effects.create_once(data, id=...)` —
  explicit names for create-or-refresh and the "already done" idempotency
  guard every `produce` used to hand-roll. `effects.ask(...)` gained an
  optional `id=`.
- `RuntimeResources.aclose()` (duck-typed, closes `llm`/`embedder` if they
  support it) — fixes a real leak where `ChatAssistant` with a callable
  `resources=` built a fresh provider + HTTP client every turn and never
  closed the previous one.
- `providers.from_env(**overrides)`: the `OPENROUTER_API_KEY` → else
  `OPENAI_BASE_URL` → else `None` selection every example hand-rolled as a
  local `build_llm()`.
- `recipes.find(inputs, Model)`/`recipes.find_all(inputs, Model)` — pick the
  typed artifact(s) out of a produce's `inputs` without repeating
  `next(... isinstance ...)`; adopted across the `llm_ladder` examples.
- `recipes.WindowSummarizer`/`recipes.WindowPruner`/`recipes.llm_summarizer` —
  bounded conversation memory (periodic summarization + pruning) as two
  parametrized `Produce`s, generalized from `examples/summarize/main.py`
  (which now uses them instead of its own hand-rolled Summarize/Prune pair).
- `docs/{en,ru}/comparison.md` — reactifact vs LangGraph/CrewAI, feature by
  feature, and an explicit "where reactifact is not the right choice" section.
- `docs/{en,ru}/api.md`: a **Stability** section spelling out the pre-1.0
  SemVer contract — public API is `reactifact.__all__` (and each submodule's own
  `__all__`), everything importable-but-unexported (e.g.
  `reactifact.relations.RelationGraph`, `reactifact.commit_log.CommitLog`) carries
  no compatibility guarantee, and breaking changes are always called out in
  `CHANGELOG.md` even pre-1.0.
- `tests/test_cli.py`: the `reactifact/cli/` package (extracted this release)
  shipped with 0% test coverage — now covered end to end (parser wiring,
  `graph`/`context`/`replay`/`branch`/`trace`, happy paths and the shared
  "store not found" error path).
- `tests/test_checkpoints_concurrency.py`: a concurrent-writer regression
  test for `SQLiteKVBackend`, the scenario the WAL/`busy_timeout` fix above
  targets.

### Changed

- `Context` split: `RelationGraph` (`reactifact/relations.py`) and `CommitLog`
  (`reactifact/commit_log.py`) extracted out of the 754-line `Context` god
  object — same public API and behavior, verified against the full suite
  and forklab's branch/merge/conflict path byte-for-byte.
- `SessionStore`/`BranchStore` no longer hand-roll their own
  `to_dict()`/`from_dict()` round-trip over a `KVBackend` — both delegate to
  `Context.to_kv`/`from_kv`.
- `tool_use.py`: `ToolUse`/`ToolUseHITL` extracted a shared `_ToolLoopBase`,
  removing a byte-for-byte duplicated `_run_tool` and init boilerplate.
- Examples: explicit `build_llm()` instead of `llm_from_env()`; manual
  `PendingQuestion` reconstruction replaced by `ctx.resume()`;
  `medic_lab`'s `Consume(condition=...)` replaced by
  `Consume.by_status(Hypothesis, "open")` for consistency with sibling
  examples.
- Docs (EN+RU) synced: `Runtime(isolate_errors, on_agent_error)`, `on_error`,
  `RuntimeResources.aclose()`, `effects.create_once`/`upsert`,
  `providers.from_env`, `retry_attempts`, the new vendor factories, the
  async session/branch/checkpoint API, and produce-style guidance.

### Fixed

- `llm_from_env()`/`embedder_from_env()` silently dropped
  `temperature`/`max_tokens`/`timeout`/`transport`/... overrides instead of
  forwarding them to the provider.
- `VideoProvider.poll()` no longer aborts a multi-minute job on a single
  transient `fetch()` failure — it waits for the next interval and returns
  an honest failed `VideoResult` only once the deadline passes.
- `session.save()`/`store.open()`/`assistant.history()`/
  `store.delete_session()` were being called synchronously from inside
  already-`async` chat/web request handlers (`chat.py`, `web.py`, the
  `medic_lab` example router) — silently blocking the event loop on every
  turn. Now real `await` calls against the async session API.
- `reactifact/__init__.py`: 15 public names (eval + replay helpers) were
  importable but missing from `__all__`, so `from reactifact import *` and
  doc/IDE tooling silently dropped them.
- Minor example bugs across `knowledge`, `map_reduce`, `medic_lab`, and
  `repair` produces: a stale re-query re-running the same filter twice
  instead of reusing an already-scoped list, a `Combine` produce that
  cross-joined chunks against summaries instead of a direct id lookup, a
  dead local re-alias, and a duplicated existence check in `repair`'s
  `CollectStage`.
- `SQLiteKVBackend`'s bootstrap (`journal_mode=WAL` + `busy_timeout` pragmas
  on first connect) could itself raise `sqlite3.OperationalError: database is
locked` when several backends opened the same brand-new file at once —
  changing journal mode is an exclusive operation SQLite does not always
  retry through the busy handler. The one-time bootstrap now retries with
  backoff; the hot-path `execute()` was already correctly serialized.

## [0.4.0-rc1] — 2026-09-02

Minor release — provider-level generation defaults + explicit provider wiring.

### Breaking

- `LLMRequest.temperature` no longer defaults to `0.7` — a `None` now means
  "omit the field, let the provider apply its own default" instead of
  "use `0.7`". Same call shape, different generation behavior, no error
  raised. Pass `temperature=0.7` explicitly (per-call or on the provider) if
  your code relied on the old implicit default. See "Upgrading" in
  [docs/en/release.md](docs/en/release.md).

### Added

- **Provider-level `temperature` / `max_tokens`** — the defaults live on the
  provider instance (`openai_llm(..., temperature=0.7, max_tokens=2048)`), any
  per-call value overrides them, and a `None` at both levels omits the field so
  the API applies its own default. `LLMRequest.temperature` is now `float | None`
  (was a hard-coded `0.7`), ending the 0.7-vs-0.0 drift between call sites.
- Applied across the stack: `structured_llm` / `llm_reply` /
  `LLMAgent` / `HITLLMAgent` / `ToolUse` accept `temperature`/`max_tokens`
  (default `None` = provider default). `OpenAICompatProvider`, `AnthropicProvider`
  and `GeminiProvider` follow the same resolution order
  (call → provider → omit); Anthropic always sends `max_tokens` (API requires
  it, default `4096`).
- **Image provider defaults** — `OpenAICompatImageProvider` takes `n`/`size`/
  `quality` in the constructor; `generate(prompt, size=...)` overrides per call,
  unset fields are omitted.

### Changed

- **Explicit providers in examples** — demos no longer use `llm_from_env()`.
  Each defines a local `build_llm()` picking `openrouter_llm(...)` or
  `openai_llm(...)` explicitly (with `max_tokens=2048`) and returning `None`
  offline. `openai_llm`/`openrouter_llm` now tolerate `base_url`/`model` as
  `None` (defaults applied) and return `None` without a key, so demos stay
  offline-capable.

---

## [0.3.2-rc1] — 2026-09-02

Patch release on top of 0.3.1.

### Added

- **Cоnfigurable structured completions** — `structured_llm` / `llm_reply` /
  `StructuredLLM` now accept `temperature` and `max_tokens` (defaults stay
  `0.0` / `2048`, backward compatible) for non-zero-`temperature` models or
  longer generations.

---

## [0.3.1-rc1] — 2026-09-01

Patch release on top of 0.3.0.

### Fixed

- **Async tracing end-to-end** — `Tracer.on_turn_end`, sink `export`/`query`/`get`
  are now async. `TraceStore` (SQLite) bridges its core via `asyncio.to_thread`.
- **`PostgresStore` read support** — was write-only (`export`); now also `query`/`get`,
  and the schema grew the `relations` jsonb column to match SQLite. Uses
  `psycopg.AsyncConnection` per operation.
- **`create_trace_router` accepts any `TraceReader`** — the dashboard now works
  against `PostgresStore(dsn)` directly, not just SQLite.
- **`LangfuseTracer` is async** (`httpx.AsyncClient`); `RecordingLLM` unchanged.
- **CLI `reactifact trace` awaits** the async store.
- Exported `RelationRef` from `reactifact.tracing`.

### Changed

- Docs (`api.md`, `observability.md`, EN/RU) describe the async interface and the
  Postgres-backed dashboard.

---

## [0.3.0-rc1] — 2026-09-01

Third release candidate — "build your agent in minutes" surface: a ready
app-facing chat layer, a zero-subclass agent factory, and a furnished CLI.
The runtime itself is unchanged (still reactive/effect-driven); this release is
about the ergonomics around it.

### Added

- **Chat layer** — `ChatAssistant` (`reactifact.chat`): session-persisted turns
  (`stream`/`invoke`/`history`) driven by hooks (`agents`, `user_message`,
  `reply`, `session_state`, `create_message`, `tracer`); transport-agnostic
  building blocks (`run_message`, `default_session_state`).
- **Web router** — `reactifact.web.create_chat_router(assistant)` mounts the
  canonical SSE chat contract (`/api/chat/stream`, `/api/runs/{id}`, `health`,
  delete) on _your_ FastAPI app. FastAPI is imported lazily with a readable
  `pip install "reactifact[web]"` error when the extra is missing.
- **Error resilience** — the chat layer never leaks a 500: runtime crashes,
  failing reply hooks and session-open errors degrade to a fallback `message`
  (`error: true`) and are logged via the `reactifact.chat` logger.
- **`create_agent`** — constructor-style agent factory: `Agent` is a thin
  container, no subclassing needed for the common case.
- **Function produces with effects** — `@produce(…)` functions may declare an
  `event`/`effects` parameter (recognized by name) and author the same slot as
  `self.effects`; return-based produces still work.
- **`Context.latest(type)`** — the most recent artifact of a type.
- **Zero-run diagnostic** — a run where no agent reacted prints a one-time hint
  (agents present / consumed types) instead of failing silently.
- **CLI friendliness** — `reactifact` with no args prints a welcome + how-to,
  `reactifact --version` reports the release.

### Refactored

- **Examples** — `knowledge`, `research`, `devops`, `repair` web layers rebuilt
  on `reactifact.chat` + `create_chat_router` (~60% less code each; domain hooks
  only). medic-lab/forkLab stay custom by design.

---

## [0.2.0-rc1] — 2026-08-31

Second release candidate — the framework API is stable at the `0.2` surface,
now ready for wider adoption.

### Added

- **PostgreSQL session backend** — `PostgreSQLKVBackend` (behind the `pg`
  extra): sessions stored in the same Postgres as the application.
- **Readable optional-dependency errors** — `reactifact._extras.require_extra`:
  missing `pg`/other extras now say
  `pip install "reactifact[pg]"` / `uv sync --extra pg` instead of a bare
  `ModuleNotFoundError` (applies to the Postgres KV and trace sink).

### Internal

- **CI** — GitHub Actions: checks + wheel smoke (`ci.yml`) and release-on-tag
  (`release.yml`).
- **Release process** — `docs/en|ru/release.md` (versioning, changelog,
  build/verify/publish) and the `reactifact` console script.

---

## [0.1.0-rc1] — 2026-08-31

First release candidate — the framework is ready for early adoption in
real projects. Everything runs offline (deterministic fallbacks) or with an
LLM via `.env`.

### Added — core

- **Effects authoring (§24)**: `Produce` writes `self.effects.create/update/
link/ask/resume` and returns `None`; the runtime compiles the effect set into
  one atomic `Patch` (commit, events, validation, trace). `Patch` is the
  runtime's transport; `Operation` types moved to `reactifact.operations`.
- **HITL (§60)**: `effects.ask(...)` → `PendingQuestion`, answered with
  `effects.resume(...)`; `InterruptPatch` removed.
- **Recipes `reactifact.recipes`**: `fan_out_sources`, `materialize_doc`,
  `StatusMachine`, `keyword_score`/`stem_words` (EN/RU), change→rebuild
  rollback helpers.
- **Branching & merge (§39-§40)**: `Context.branch()`, three-way `merge()`
  with explicit `MergeConflict`, `BranchStore` over KV, CLI.
- **Replay (§55)**: `ReplayLLM` record/replay, deterministic state replay.
- **Evaluation harness (§56)**: `reactifact.eval` — multi-level metrics over the
  final state.
- **Adaptive scheduling (§26, §24)**: `Runtime(scheduler=…)` —
  filter (rules) → deterministic rank → LLM tie-break (app-owned system
  prompt) → optional top-k; HITL-resume is always pinned; `Agent.capabilities`.
- **Structured I/O**: `structured_llm`, `StructuredLLM`, `llm_reply`,
  `PromptTemplate`/`MessagesPrompt`, typed `Message` roles + factories.
- **Auto-derived `artifact_type`** from `Produce[Foo]`.
- **Observability (§54)**: SQLite trace store, dashboard with sequence and
  evidence-graph (Mermaid), Langfuse/Postgres sinks.
- **Viz & CLI**: `blueprint`/`context_to_mermaid`/`trace_to_mermaid` and
  `python -m reactifact {graph,context,trace,replay,branch}`.
- **Providers**: OpenAI-compatible chat/embedder + Anthropic, Gemini, Mistral,
  OpenRouter, Groq, xAI, DeepSeek, Azure, and more; image/speech/video; fakes.
- **`reactifact` console script** (`uv add` → `reactifact graph …`).

### Added — examples (in-repo, not shipped)

`knowledge` · `research` · `medic-lab` · `devops` · `repair` (Russian by
design, with plan/estimate UI + CSV export) · `forklab` (branch/merge) ·
`llm_ladder` (learning path) · canonical-pattern ports: `reflection`,
`map_reduce`, `supervisor`, `summarize`, `time_travel`, `adaptive`.

### Changed

- `Produce` no longer returns `Patch`; effects are the authoring surface.
- `Agent.execute` runs produces; the runtime compiles the effects slot.
- `MergeConflict`, `ReplayLLM`, `reactifact.eval`, scheduler — new core surface.

### Removed

- `InterruptPatch`, `Patch.merge_existing_patch`, `Patch.to_dict`, `examples/plan`.

### Notes

- Requires Python ≥ 3.11; only `pydantic>=2.13` is mandatory at runtime.
- `uv build` ships only the `reactifact` package (examples/tests stay in-repo).
