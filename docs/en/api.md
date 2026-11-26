# API reference

Top-level symbols exported by `reactifact` (see `reactifact/__init__.py`). The format
for each group: name — one-line role. Details live in the doc-strings of the
modules — see [the auto-generated reference](../reference/index.md) for those
rendered directly from source (signatures, types, full docstring text) on
the docs site, rather than copied here by hand. Per-area pages:
[Knowledge](../reference/knowledge.md), [Evaluation](../reference/eval.md),
[Sources](../reference/sources.md), [Providers](../reference/providers.md),
[Recipes](../reference/recipes.md), [Tracing](../reference/tracing.md).

## Stability

reactifact is pre-1.0 (`0.23.0`) but not `rc` — the surface below is the stable
contract, not a moving target. It is **machine-checked**: `tests/test_public_api.py`
pins every `__all__` listed here, so a new export has to be added deliberately
(and documented on this page), never by accident.

- **Public API = every name in `reactifact.__all__`** (and each submodule's own
  `__all__` — `reactifact.recipes`, `reactifact.providers`, `reactifact.viz`,
  `reactifact.eval`, `reactifact.tracing`, `reactifact.quick`,
  `reactifact.redaction`, `reactifact.audit`, …), which is exactly the set of
  symbols documented on this page and pinned by the freeze test. If it's
  importable from `reactifact` but not in `__all__`, it's an internal detail with
  no compatibility guarantee — e.g. `reactifact.relations.RelationGraph` and
  `reactifact.commit_log.CommitLog` exist because `Context` was split into
  smaller modules for readability, but neither is exported: `Context` is the
  supported surface, they are not.
- **SemVer, pre-1.0 style**: a minor bump (`0.22.0` → `0.23.0`) may add symbols
  or, rarely, change behavior in a way `CHANGELOG.md` marks `Breaking` — pre-1.0
  minors are where reactifact is still allowed to correct a design mistake. A
  patch bump (`0.14.0` → `0.14.1`) never removes or renames a public symbol and
  never changes documented behavior, only fixes bugs against it.
- **Deprecation**: a symbol on its way out stays importable and keeps working
  for at least one minor release; it is marked deprecated in the docstring and
  `CHANGELOG.md` (with the replacement) before any removal, and removal only
  happens in a minor release under a `### Breaking` heading. If a removal
  cannot wait, the `### Breaking` entry and the migration guide must say so
  explicitly (0.23's `Effects.ask/resume/act` is such an exception).
- **Every breaking change is called out in `CHANGELOG.md` under a `### Breaking`
  heading**, even in a pre-1.0 release — see [release.md](release.md). If you
  only read one section before upgrading, read that one.
- **1.0 means this page stops moving**: the [release checklist](release.md#release-checklist)
  is the bar for leaving pre-1.0; after that, SemVer 1.x applies (no
  breaking change in a minor).
- Anything under `reactifact.cli.*` beyond the documented `python -m reactifact …`
  subcommands, and anything in a module's tests-only helpers, is implementation
  detail regardless of whether it happens to be importable.

## Quick on-ramp (`reactifact.quick`)

Thin sugar over the primitives below for the four common first tasks — every
object exposes the real `.agent`/`.agents` and the run's `.context`, so it
graduates to `Consume`/`Step`/`Effects` with nothing to rewrite. See
[Quickstart §0](quickstart.md).

| Symbol | Role |
| --- | --- |
| `agent(system, schema)` | one structured LLM call → one typed artifact |
| `rag(sources)` | retrieval → materialize → answer, with `supported_by` provenance |
| `tools_agent(system, tools, human=False)` | LLM + tools (`human=True` → HITL asks) |
| `chat_agent(groups)` | a configured `Chat` (in-memory store by default) |
| `Question` / `Doc` / `Answer` | the generic artifact models the facade uses |

## Context & state

| Symbol | Role |
| --- | --- |
| `Context` | versioned working state; resources; `request` (the active turn's per-request mapping); queries; `latest(Model)`; announce; diff/rollback; `compact(keep_commits=, keep_versions=)` to bound history (`compacted_at`); `apply_commit(commit)` replays a commit into the live state (the append-only session load path) |
| `View` | result of a typed join query (`context.view(...)`) |
| `RuntimeResources` | providers + sources + app resources; `register(Type, instance, owned=False)` / `get(Type)` / `require(Type)` / `has(Type)` for typed collaborators (`ResourceKey[T]` when two of one type); an explicit registration wins over artifact resolution for that type; `owned=True` hands the resource to `aclose()`; string `get`/`set` stay as the `additional` escape hatch; `redactor=` scrubs trace text; `trace_truncate=` caps artifact/response JSON kept in a trace (`None` = no clip) — deprecated fallback (emits `DeprecationWarning`), prefer `TraceLimits(max_data_chars=…)`; `await resources.aclose()` closes owned resources (LIFO) and then the llm/embedder HTTP clients (duck-typed, async `aclose()` or sync `close()`) — call it yourself at real shutdown; `record_tool_args=True` puts tool args on `ToolRun` (scrubbed by `redactor=`) |
| `ResourceKey[T]` | a typed handle for registering two resources of one type (primary/replica, per-tenant) |
| `ResourceScope` / `RuntimeResources.scope(factory)` | `async with` builder that creates resources on the current loop and closes them on exit — the loop-safe way to own providers (see the class docstring) |
| `current_request()` | the active turn's request mapping (same as `StepCall.request`) |
| `Commit`, `Read`, `Write` | version bookkeeping and recorded provenance ops |
| `ContextCheckpoint` | lightweight view of one commit (`id`, `version`, `parent_ids`, `created_at`, `author`, `message`, `writes`); `context.checkpoints()`, `context.checkpoint(id/version)`, `context.at(checkpoint)` |

## Artifacts & changes

| Symbol | Role |
| --- | --- |
| `Artifact` | immutable `(id, version, data, content_hash)` handle; history lives in `Context` (`artifact_version`/`version_history`/`find_versions`) |
| `ArtifactConflict` | optimistic-concurrency failure: `effects.update(..., expected_version=…)` / `Context.update` expected a different version; the runtime validates the whole generation before applying any patch |
| `register_type(Model, *, type_id=, aliases=, migrate=)` (`reactifact.types`) | stable persisted type identity: a rename/move of a model no longer breaks loading old sessions/commits/events. `type_id_of`/`resolve`/`migrate_payload`; default id is the qualified name |
| `Patch` | the runtime's compiled change-set (transport); steps write effects (`effects`/`call.effects`), `Patch` is assembled by the runtime |
| `reactifact.operations` (`Create`/`Update`/`Delete`/`Link`/`Unlink`/`Relation`) | the compiled operations a patch carries (§12) |
| `Create`, `Update`, `Delete`, `Link`, `Unlink`, `Relation` | op records from which patches are built |

## Groups & steps

| Symbol | Role |
| --- | --- |
| `StepGroup` | optional thin container: `name`, `steps`, `concurrency_limit`, `retry`, `timeout`; step `consumes` derive triggers. `Agent` is the deprecated alias |
| `create_step_group` | constructor-style `StepGroup` builder — no subclassing needed; accepts `retry=`/`timeout=`. `create_agent` is the deprecated alias |
| `Runtime(steps=..., groups=...)` | dispatches steps and groups together (steps first). Each raw `Step` is wrapped in an implicit group of one; `agents=` is the deprecated alias for `groups=` and cannot be combined with the new keywords. |
| `Chat(steps=..., seed_type=..., answer_type=...)` | typed chat facade: `invoke/stream/history/answer`, `seed_factory`, `per_turn`, `resume_pending=`, `router()`, `http()`, `test_client()`, `traces=`/`lifespan=`, `store=`. A pending `PendingQuestion` makes the default reply `waiting: true` with a structured `question`; `resume_pending=True` makes the next turn its answer. `.validate()` / `.describe()` mirror Pipeline; `.http()` is the session-persisted FastAPI app |
| `Pipeline(steps=..., groups=..., llm=..., traces=...)` | reusable facade: `.run(seed)` / `.arun(seed)` seed one artifact and return a `RunResult`; `.answer(seed)` / `.aanswer(seed)` return the typed `answer_type`; `.astream(seed)` yields progress events. Resources/budget/tracer are constructor defaults; `result.context` / `.last_context` are escape hatches. `.override(Type, instance)` replaces a typed resource (tests/fakes); `lifespan=` + `async with` manage long-lived resources; `.test_client()` adds an in-memory trace store; `traces=True|path` records every run and fills `RunResult.trace_id`/`.trace()`. `.validate()` runs the static contract check; `.describe()` returns `ContractReport` with seed/answer JSON Schemas; `.router()` / `.http()` are the optional FastAPI adapters; `.as_tool()` exposes it as a `Tool`/MCP tool |
| `Daemon(pipeline, *, seed_source=, interval=, run_once=, max_ticks=)` | interval loop over a Pipeline: `seed_source(tick)` may be sync/async and return `None` to skip; graceful SIGINT/SIGTERM, per-tick error isolation. `Daemon(tick=fn)` is the custom-tick form (the bundled daemon template) |
| `RunResult` | `.context`, `.runs`, `.outcome`, `.latest(Type)`, `.sources(artifact)`, `.hash`, `.trace_id`, `.trace()` |
| `RetryPolicy` | per-agent retry config: `max_attempts`, `backoff`, `max_backoff`, `retry_on`; a retry gets a fresh `Effects` slot, so only the successful patch commits |
| `run_subflow(parent, *, groups, input, output_type=, isolate=True, link=, source=, inherit_trace=True, inherit_budget=True, …)` | runs child agents on an isolated Context (or a parent branch), optionally links outputs to the seed, passes the parent tracer and stamps `trace_metadata.parent_run_id`; returns `SubflowResult` |
| `SubflowResult` | `context`, `seed`, `outputs`, `output`, `run_id`, `parent_run_id`, `stats` |
| `describe_contracts(groups)` / `ContractReport` | static consumes/produces/tools contract (Markdown or JSON); CLI `reactifact contracts <module> [--format md\|json]` |
| `validate_contracts(*, steps=, groups=, seeds=, terminal=, budget=)` / `ValidationReport` | static contract check over raw steps and/or groups (steps first, like `Runtime`): unknown inputs (errors), dead outputs/never-scheduled/unreachable/cycle warnings; `raise_for_errors()`; CLI `reactifact validate <module>` (or `graph --check`). `validate_agents` is the deprecated alias |
| `Wakes(False)` / `Scope` / `Consume` / `IdempotentConsume` / `consume` | declarative (or decorator) reaction declaration; `Consume.by_field` for scoped events; `Consume.by_base` / `include_subclasses=True` wakes on a marker base's subclasses; `IdempotentConsume(T, id_of=...)` wakes only while the derived id is absent; `wakes=False` reads as input without waking the agent (needed only when `consumes` is derived from the signature); `debounce=True` collapses several same-generation events into one run |
| `reactifact.consume.CorrelatedConsume` | fires (and feeds inputs) only for a correlation key where every `require` type is present and every `forbid` type is absent — the mechanism behind `JoinConsume`/`AbsentConsume` |
| `reactifact.consume.JoinConsume(*parts, key=…)` | `CorrelatedConsume` factory: fires once every listed type exists for the same key |
| `reactifact.consume.AbsentConsume(type, absent_type=…, key=…)` | `CorrelatedConsume` factory: fires for `type` only where no matching `absent_type` exists yet for the same key |
| `Step` / `@step` | the work unit: class `run(...)` gets typed inputs + `effects: Effects`; function `@step` gets `call: StepCall` and uses `call.effects`. `consumes` declares inputs/triggers, `produces` the allowed create types; when `consumes` is explicit, typed artifact params missing from it are added as read-only inputs (`wakes=False`); return a model for create-sugar. `Annotated[T, When(...)]`/`Consume.by_*` select conditions; put extra creates in the same tuple (`produces=(Primary, Extra)`). `timeout=` is a hard per-step deadline; `retry=`, `priority=`, `concurrency_limit=` and `name=` are copied into the implicit group. Return `Emit(model, id=..., links=(...))` when the id/provenance must be explicit, `Ensure(...)` for an idempotent create, or `Edit`/`Drop`/`Relate` for a single mutation. |
| `Human` / `Outbox` | injected non-data collaborators: `human.ask/resume` (HITL, `PendingQuestion`) and `outbox.act` (outbound intent, `PendingAction`); the old `Effects.ask/resume/act` were removed in 0.23 |
| `Progress` | injected ephemeral progress channel: `progress(message, kind="status", **data)` publishes a `ProgressEvent` immediately (a thin view over `Context.announce`, never committed). A step that only announces and then calls an LLM/tools needs no `Context` |
| `Emit` / `Ensure` / `Edit` / `Drop` / `Relate` | return-style change instructions: create with explicit id/links, idempotent create (by id or source), update fields, delete, link — compiled into the same atomic patch. `Emit` with an existing id refreshes it (`upsert`); `Ensure` skips when it exists |
| `Trigger` | secondary (non-artifact) enter condition for a step; `context_condition(artifact, context)` for conditions that need other artifacts (joins/correlation); `debounce` hint consumed by `Runtime` |
| `StructuredGenerateAgent` | declarative LLM→schema→artifact agent (`schema`, `build_prompt`, `fallback`) |
| `LLMAgent` | blocking LLM+tools loop (`system`, `tools`, `max_steps`, `deferred_tool_groups`) |
| `HITLLMAgent` | LLM+tools loop that can pause for human answers (`max_asks`, resume reporting) |
| `ToolUse`, `ToolUseHITL` | the tool-loop step; HITL variant waits for approval on execution |
| `DeferredToolGroup` (`reactifact.tools.use`) | a tool group whose schemas stay out of the prompt until `ToolUse`'s built-in `load_tools` tool requests it (`LLMAgent`/`ToolUse` only, not the HITL variant) |
| `Inject` | explicit `Annotated[T, Inject()]` marker for an injected tool parameter (useful when a plain class would be ambiguous); `Depends` is a FastAPI-style alias |
| `Tool`, `FunctionTool`, `tool`, `ToolOutput`, `ToolError` | tool abstraction and registration; `Tool.execute` is instrumented once (trace `ToolRun` + hooks + record/replay) for both direct calls and `execute_tool`; `@tool(retry=RetryPolicy(...), timeout=...)` retries one call; a plain-class parameter is injected from `RuntimeResources` (not in the schema); a pydantic return type fills `ToolOutput.data`; `ToolOutput.failed(ToolError(code=, message=, retryable=))` gives a typed failure while `ToolOutput.error` stays a plain string |
| `ToolAnswer`, `Observation` | tool results and model observations (loop protocol) |
| `PendingQuestion` | HITL primitive: a paused ask waiting for a human answer, resumed via `human.resume(...)`; optional `resolution_schema`, typed `resolution_data`, helpers `approved()` / `answer_text()` |
| `Effects.update(artifact, *, expected_version=…, **fields)` | field-bump of an existing artifact; `expected_version=` opts into optimistic concurrency (`ArtifactConflict` instead of last-write-wins) |
| `PendingAction` | the recorded outbound side effect (`kind`, `idempotency_key`, `payload`, `status`, `attempts`); performed once by `Runtime(dispatcher=...)`, then `dispatched` / `failed` (`retry_action` re-arms) |

## Runtime

| Symbol | Role |
| --- | --- |
| `Runtime` | wakes agents on events; `run` / `arun` / `astream` (each takes `request=Mapping`); `astream_events(stream_mode=...)` yields typed `RunEvent`s; budget & concurrency; `isolate_errors=True` + `on_agent_error(agent, event, exc)` to keep one agent's exception from aborting the whole run (default: propagates, §69); `dispatcher=async (context, action)` drains the `PendingAction` outbox after each commit (once per stable id, keeping the turn alive until it is empty); `on_dispatch_error(action, exc)` fires when the dispatcher raises (the action is marked `failed` and the exception propagates); `flush_pending_actions()` drains without a generation (e.g. after `merge()`); `Budget.max_seconds` is a **hard** turn deadline (cancels the generation in flight) |
| `Runtime.request_stop()` / `await ashutdown(timeout=…)` / `in_flight` | graceful shutdown: stop at the next generation boundary (`RunOutcome.STOPPED`; the current generation still commits); `ashutdown` waits, force-cancelling after `timeout` |
| `reactifact.runtime.active_runs()` / `cancel_run(run_id)` | read-only snapshots of the process's in-flight turns (`RunInfo`) and targeted cancel — readiness/ops, in-process only |
| `StepCall.request` | the turn's request mapping inside a step (`Runtime.arun(request=…)` / `Chat.stream(request=…)`); empty when none was set |
| `Budget`, `RunOutcome`, `RunStats`, `BudgetTracker` | run limits (`max_runs`/`max_iterations`/`max_seconds`/`max_tool_calls`/`max_tokens`/`max_cost`) and the final outcome/usage stats (`RunOutcome` includes `budget_time_exceeded` and `stopped`). Token/cost limits count LLM usage without a tracer; cost needs an injected `Pricer` (`reactifact.pricing`) |
| `Event`, `EventType` | the wire format of "something changed" — `ARTIFACT_CREATED`/`UPDATED`/`DELETED`/`STALE` |
| `EventHub`, `ProgressEvent` | progress/announce channel consumed by web UIs |
| `RunEvent` (`reactifact.streaming`) | typed events from `Runtime.astream_events`: `RunStart`/`GenerationStart`/`AgentStart`/`AgentRetry`/`AgentEnd`/`ToolStart`/`ToolEnd`/`ArtifactWrite`/`Values`/`LLMDelta`/`CustomEvent`/`RunEnd`/`RunFailed`; modes `custom`/`updates`/`values`/`debug`/`messages` |
| `Scheduler` | filter → rank → LLM tie-break agent-selection policy, callable from the runtime each iteration (see [design notes](design-notes/adaptive.md)) |
| `uncertainty_policy(...)` | builds the built-in hybrid `Scheduler` (filter → rank → LLM tie-break → top-k) |

## Logging (`reactifact.logging`)

| Symbol | Role |
| --- | --- |
| `configure_logging(level="INFO", *, json=False, stream=None)` | turn on correlated structured logs under `reactifact.*` — silent until called; `json=True` → one JSON object per line |
| `get_logger(name)` | a logger that injects the turn's correlation fields (`run_id`/`session_id`/`generation`/`agent`/`request_id`) into every record |
| `bind(**fields)` | add app correlation fields for a block (`tenant`, `user`, …) |

## Chat layer (reactifact.chat + reactifact.web)

| Symbol | Role |
| --- | --- |
| `ChatAssistant` | the low-level engine behind `Chat`: sessions + turn loop + history in one handle (`stream`/`invoke`/`history`); hooks: `agents`, `user_message`, `reply`, `session_state`; `dispatcher=`/`on_dispatch_error=` pass through to `Runtime` (outbox side effects from a chat turn) |
| `ChatMemory` | optional bounded memory for `Chat`/`ChatAssistant`, each field independent: `keep` (prune the raw thread), `summarize`+`summary_type` (growing digest), `compact_commits` (`Context.compact` — bounds history without touching artifacts); `message_types=[UserQuery, Answer]` (or a marker base) bounds a heterogeneous thread |
| `ChatEvent` | one transport-neutral frame; `kind` is a closed `Literal["session","status","message"]`, so the schema is introspectable |
| `run_message(runtime, text, *, user_message, reply)` | the turn building block: create input → stream statuses → terminal reply |
| `default_session_state(ctx, user_message)` | generic history reader (any artifact with `.text`), plus the latest pending question as a `waiting: true` assistant message |
| `reactifact.contracts.web.create_contracts_router(groups, *, prefix=, title=, username=, password=)` | opt-in FastAPI router: `/contracts` HTML, `/api/contracts` JSON, `/contracts.md` Markdown; `groups` may be a zero-arg factory |
| `install_error_handlers(app, *, extra=…)` | maps `AuthorizationError`/`GuardrailViolation`/`QuotaExceeded`/`MergeConflict` to JSON HTTP responses (403/422/429/409) with `{"error": {"code", "message", "request_id", "trace_id"}, "detail"}`; `extra` overrides/adds mappings. `create_*_app` installs it together with `install_request_id`. |
| `install_request_id(app)` | middleware assigning `X-Request-ID` (and `request.state.request_id`) to every response; error envelopes reuse it and `X-Reactifact-Run-Id` when available |
| `create_chat_router(assistant, *, event_names=…, forward_kinds=…, payload_shaper=…, done_event=…, request_factory=…)` | FastAPI `APIRouter` for the SSE contract (`/api/chat/stream`, `/api/runs/{id}`, `/api/health`) — needs the `web` extra. The wire **vocabulary is configurable** (rename kinds, filter, reshape payloads, emit a terminal frame); `request_factory(request)` builds the per-turn `request=` mapping from the FastAPI `Request` (identity/ACL-aware turns); the event schema is published in the route's OpenAPI `responses` |
| `create_pipeline_router(pipeline, *, prefix=, routes=, request_factory=, max_iterations=, with_health=)` | FastAPI `APIRouter` for a typed `Pipeline`: `POST /run` (body `seed_type`, response `answer_type`), `POST /stream` (SSE `progress`…`done`/`error`), `GET /health`; FastAPI derives validation/OpenAPI/`/docs` from the pipeline's models. `PipelineRoutes` overrides the paths |
| `create_chat_app(chat, *, prefix="/api", routes=, traces=, contracts=True, dependencies=, validate=True)` | ready FastAPI app for a `Chat`: sessions, history, clear, SSE, contracts and traces. `ChatRoutes` can expose the DEVOPS-style `/chat`, `/chat/stream`, `/chat/history`, `/chat/clear` |
| `create_http_app(pipeline, *, title=, version=, prefix=, traces=, contracts=True, dependencies=, request_factory=, validate=True, gzip=True)` | ready FastAPI app: pipeline router + contracts page + trace dashboard + error handlers; `validate=True` fails at startup on a broken contract; `dependencies=` applies to every router |
| `reactifact.web.sse(event, data)` | one SSE frame |

## Visualization (reactifact.viz + python -m reactifact)

| Symbol | Role |
| --- | --- |
| `blueprint(agents)` | static map of consumes/produces as Mermaid `flowchart` |
| `context_to_mermaid(context)` | live provenance graph of a context (artifacts + relations) |
| `trace_to_mermaid(trace)` | one run as a Mermaid `sequenceDiagram` |
| `python -m reactifact graph\|context\|trace` | CLI printing the diagrams to stdout |
| `reactifact new agent` / `new app` / `dev` / `doctor` | CLI: one-file minimal Pipeline / bundled FastAPI chat template / run an ASGI app with reload and contracts+traces URLs / check app settings and boundaries |
| `reactifact run` / `trace` | CLI: run a Pipeline and record `.reactifact/traces.db` by default (`--no-trace` skips), then render the latest trace without arguing the path |
| `reactifact daemon <pipeline> --seed func` | CLI: run a Pipeline on `--interval`; `--run-once`, `--max-ticks`, `--trace`/`--no-trace` |
| `trace_provenance_to_mermaid(trace, *, max_nodes=, max_edges=)` | a run's evidence graph (written artifacts + `patch.link` edges); long ids are shortened and the dashboard caps nodes/edges |

## Replay (reactifact.replay, §55)

| Symbol | Role |
| --- | --- |
| `ReplayLLM(recording, mode="record"\|"replay", inner=…)` | records every LLM call to JSONL, or replays them exactly; `ReplayMiss` on divergence |
| `RecordingToolRecorder` / `ReplayToolRecorder` | JSONL record/replay for tool calls (name + exact args + output/exception), covering direct `tool.execute` and `execute_tool`; `ReplayMiss` on an unrecorded call |
| `ReplayMiss` | a replaying call did not match the recording |
| `Runtime.replay_run(llm_recording=, tool_recording=, model=, save=False)` | temporarily installs the replay wrappers, runs a normal turn, restores resources; `save=False` detaches the session |
| `verify_run(build, *, recording=None, repeat=2)` | runs `build(resources) -> Context` `repeat` times under a recorded model **and** strict ids (`counter_ids`), returns a `ReproReport`; `ok` is False when the `context_hash` differs (real nondeterminism) |
| `counter_ids()` | a deterministic `id_factory` (`Model:0000`, `Model:0001`, …) for `RuntimeResources(id_factory=…)` — makes artifacts created without an explicit id reproducible |
| `replay_context(store, session_id, version=None)` | reconstructs a saved session's state at a commit |
| `replay_summary(context)` | compact state summary for the `replay` CLI — includes `dispatched_actions` / `pending_actions` / `failed_actions` (replay never re-sends them) |

## Branching (reactifact.context + reactifact.branching, §39-§40)

| Symbol | Role |
| --- | --- |
| `Context.branch(name="")` | forks an isolated copy; records the base snapshot for three-way merge |
| `Context.merge(other, message=…, policies=…)` | atomic three-way merge; `MergeConflict` on diverged artifacts unless a per-type `MergePolicy` resolves them |
| `MergeConflict` | raised when both sides changed an artifact differently since the fork |
| `MergePolicy` (`reactifact.branching`) | `(target_data, other_data) -> merged_data` callable passed per model type to `Context.merge`; default stays fail-loud |
| `BranchStore(KVBackend)` | persists branches as `branch:<session>:<name>` over a KV backend |
| `python -m reactifact branch …` | CLI: `list` / `save` / `merge` |

## Evaluation (reactifact.eval, §56)

| Symbol | Role |
| --- | --- |
| `run_suite(cases, metrics)` / `run_case(case, metrics)` | execute cases and score the final contexts |
| `EvalCase` / `EvalResult` / `EvalReport` / `Metric` | case/score/report structures (`overall()`, `aggregate()`, `passed()`, `render()`, `to_dict()`) |
| `core_metrics` | the four non-generative metrics (answer/provenance/evidence/claim) |
| `answer_coverage()` · `calculation_correctness(values=…)` · `source_coverage()` · `confidence_calibration()` | ground-truth factories (skip when `expected` is missing) |
| `Dataset` / `Example` | versioned examples (`from_file`/`from_list`, `.json`/`.jsonl`, content-hash `version`) |
| `evaluate(dataset, target, evaluators, *, extract=…, summary=…, max_concurrency=…, on_result=…)` / `aevaluate` | run a target over every example and score it; `target` returns a `Context`, `ScenarioResult`, outputs mapping or `RunResult`; `on_result(result)` streams each scored `EvalResult` (completion order when concurrent) |
| `ResultCallback` | `Callable[[EvalResult], None]` type alias for the `on_result` progress hook |
| `from_metric(name, metric_fn)` | wrap a scoring metric into an `Evaluator` |
| `trajectory_match(mode, *, steps=…, reference_key=…)` | match a run's path (`strict`/`unordered`/`subset`/`superset`) over `agents`/`events`/`reads`/`writes` |
| `llm_judge(llm, *, instructions, …)` · `judge_correctness` · `judge_relevance` · `judge_faithfulness` | LLM-as-judge evaluators (`continuous`/`choices`/`include_reference`) |
| `summary_pass_rate(threshold)` / `summary_mean(key)` · `assert_eval(report, …)` | suite-level aggregators and the CI gate |
| `OnlineEvaluator(store, config, metrics=, on_report=)` · `online_evaluate(store, config, metrics=)` · `OnlineEvalConfig` | score live runs sampled from a `TraceStore`; `run_once()` / `run_forever(interval)` |
| `trace_source()` · `context_source(run_fn)` · `output_present(key)` · `no_errors()` | what to score from a run: the trace itself, a rehydrated `Context`, its outputs, its spans |
| `create_online_eval_router(evaluator)` | FastAPI router: `POST /api/evals/run`, `GET /api/evals/report`, `GET /api/evals/summary` |
| `python -m reactifact eval <traces.db>` | CLI: online-evaluate runs (tags the results, `--evaluators trace\|judge\|all`) |
| `python -m reactifact check <pipeline>` | CLI gate: contracts + `@scenario` modules + a `Dataset` eval (`--scenarios`, `--evals`, `--gate`, `--overall`) |

## Structured output

| Symbol | Role |
| --- | --- |
| `structured_llm(llm, schema, *, system, user, attempts=…, validate=…, repair=…, prompt_hash=…, on_error=…)` / `llm.structured(...)` | one structured call; `None` on honest failure; `validate(model)->bool` adds a domain-rule check retried like a parse failure, `repair(invalid_or_None, last_reply)->str` supplies the retry instruction; `on_error(reason, exc)` (`"no_provider"`\|`"provider_error"`\|`"parse_error"`\|`"validation_error"`) to distinguish *why*, without changing the `None` contract |
| `json_schema_llm(llm, json_schema, *, user, validate=…, repair=…, …)` / `llm.structured_json(...)` | structured output against your own JSON Schema (dict or JSON string), returns a plain `dict`; same `validate`/`repair` retry hooks as `structured_llm` |
| `StructuredLLM(schema, *, system=…, attempts=…, on_error=…)` | reusable instance; `.call(llm, user)` |
| `llm_reply(llm, *, system, user, attempts=…, on_error=…)` / `llm.reply(...)` | plain-text completion → `str` or `None` (single-text schema under the hood) |
| `parse_structured` | lenient JSON→model parser used internally |

## Prompts (reactifact.prompts, §68)

| Symbol | Role |
| --- | --- |
| `PromptTemplate(template, *, defaults=…)` | strict `{var}` rendering: declared `variables`, `KeyError` on missing vars, model-attribute fields (`{question.text}`); **identifier-shaped placeholders only** — a literal JSON `{"name": …}` / `{}` in the prompt is left verbatim, no escaping; `.hash` is a stable sha256 of the template |
| `MessagesPrompt([(role, template), …])` | renders a chat sequence to `list[Message]`; `.hash` covers all rows |

## Sources (reactifact.sources)

| Symbol | Role |
| --- | --- |
| `Source` | ABC: `asearch(query, limit)` → list[SourceRef] |
| `SourceRef` | the shared atomic search result (ranked, scoped, stable id) |
| `FileSystemSource` | keyword/embedding search over local files |
| `CSVSource` | deterministic catalog/table search |
| `EmbeddingSource` | vector search over a prepared corpus |
| `WebSource` | discovery + lazy remote document resolution |

## Providers (reactifact.providers)

Every provider lazily creates its `httpx.AsyncClient` and **rebinds it to the
current event loop**, so one provider instance stays usable across repeated
`asyncio.run(...)` and per-test loops (no `RuntimeError: Event loop is closed`);
`aclose()` closes the client for the running loop. The same applies to
`WebSource` and the OTLP/Langfuse sinks.

| Symbol | Role |
| --- | --- |
| `LLMProvider`, `EmbeddingProvider` | the two contracts the core talks to; `LLMProvider` adds default `structured`/`structured_json`/`reply`/`chat` methods (they call `complete`, so tracing/budget still apply), and `EmbeddingProvider` adds `embed_one` |
| `ImageProvider`, `SpeechProvider`, `TranscriberProvider`, `VideoProvider` | media contracts |
| `OpenAICompatProvider`, `OpenAICompatEmbedder` + vendor factories (`openai_llm`, `anthropic_llm`, `deepseek_llm`, `groq_llm`, `mistral_llm`, `openrouter_llm`, `gemini_llm`, `ollama_llm`, `azure_llm`, …) | 20+ chat/embedder backends, all `retry_attempts=3` by default (429/5xx/transport errors, exponential backoff — never on 4xx) |
| `openrouter_embedder`, `openrouter_speech`, `groq_transcriber`, `together_embedder`, `fireworks_embedder`, `qwen_embedder`, `nvidia_embedder` | embeddings/TTS/STT for vendors whose non-chat endpoints are confirmed OpenAI-compatible (see [providers](providers.md)) |
| `Message`, `Role` | one chat message; `role` is a closed `Literal` + `Message.system/user/assistant/tool` factories |
| `LLMRequest` | one completion: `messages` + `temperature`/`max_tokens` — `None` = provider default (call overrides provider, provider `None` = field omitted) |
| `LLMResponse`, `LLMResponseChunk` | one completion result / one streamed chunk returned by a provider |
| `*_from_env(**overrides)` | `.env`-driven wiring that returns `None` when unconfigured |
| `CachingLLM`, `KVCache` (`reactifact.cache`) | exact cache for `complete()`: a repeated request is answered without the provider (`cache=` to persist), a hit is not charged to the budget; not semantic, `stream()` uncached |
| `RouterLLM`, `retryable_only` (`reactifact.routing`) | provider failover: try providers in order, fall back on failure (`should_fallback=`/`on_fallback=`); only `complete()` |
| `from_env(**overrides)` | one-call selection: `OPENROUTER_API_KEY` first, else `OPENAI_BASE_URL`, else `None` — the two-branch default every example's local `build_llm()` hand-rolls |
| `FakeLLM`, `FakeEmbedder` | deterministic stand-ins for tests/demos |

## Recipes (reactifact.recipes)

| Symbol | Role |
| --- | --- |
| `find(inputs, Model)` / `find_all(inputs, Model)` / `correlation_id_of(artifact, field, owner=)` | typed lookup in a step's `inputs`; `correlation_id_of` pulls a loop key (or the artifact id for its owner type) |
| `search_sources(context, query, owner_id, …)` / `fan_out_sources(…, as_artifacts=False, effects=)` | `search_sources` is pure (ranked, scoped refs); `fan_out_sources` also writes create-or-refresh refs (`source_ref_id`) into the effect slot (or an explicit `effects=`) |
| `materialize_doc(context, ref, doc_factory=None, *, doc_type=, refresh=, on_error=, relation=, effects=)` | lazy ref → document with provenance; accepts an artifact, `SourceRef` or handle; `effects=` selects an explicit slot |
| `StatusMachine` / `StatusState` | deterministic artifact lifecycle: `next_status(state)` over key-scoped inputs (`all`/`any`/`latest`), `terminal`, `on_transition` |
| `WindowSummarizer(message_type, artifact_type, summarize=…, build=…)` | periodic conversation-window summarization, idempotent by message count; `message_type` may be a sequence of types |
| `WindowPruner(message_type, keep=…)` | deletes messages older than the window; standalone-useful; `message_type` may be a sequence of types |
| `Conversation` / `ConversationMessage` / `create_turn` / `transcript` | multi-turn conversation generic over a message model (yours, or the default `ConversationMessage`): append turns, render the thread to provider `Message`s (`turn_count` / `conversation_state` helpers) |
| `Transcript` / `MessageSpec` | read-only view for apps with *separate* types per side (`MessageSpec(Question, "user")`, `MessageSpec(FinalResponse, "assistant", render=…)`): merges typed turns by role into one `Message` list (`messages` / `state`) |
| `llm_summarizer(system=…)` | builds a `WindowSummarizer(summarize=…)` callback from a system prompt via `llm_reply` |
| `run_tool_loop(context, *, system, user, tools, max_rounds=…, parallel=True, mandatory=…, max_observation_chars=…, redact_tool_outputs=False, retry_tool_errors=False, tool_retry_attempts=2, tool_retry_delay=0.5, parse_calls=…, fallback=…, on_event=…)` | opt-in native tool-calling loop over `reactifact.tools.native`: bounded rounds, parallel tool execution, mandatory-tool nudging, forced final answer; honours the turn's `Budget` (`max_tool_calls`, `max_seconds`, token/cost) between rounds; caps tool observations (`max_observation_chars`), optionally redacts them via `resources.redactor`, retries `ToolError(retryable=True)` when `retry_tool_errors=True`, accepts a `parse_calls` codec (e.g. textual DSML), a `fallback` for markup answers, and an `on_event` streaming hook; returns `ToolLoopResult` (text, transcript, `ToolObservation`s, `stop_reason`/`error`) |
| `looks_like_tool_markup(text)` / `parse_textual_tool_calls(text)` / `parse_tool_calls_with_textual(response)` | opt-in helpers for providers that emit DSML/Hermes/XML tool-call markup instead of native `tool_calls`; pass the last one as `run_tool_loop(parse_calls=…)` |
| `Approval` / `ApprovalIntent` / `ApprovalDecision` | typed yes/no answer; classifier result (`approve`/`reject`/`conditional`/`ambiguous` + confidence); durable decision with `allows(min_confidence=0.8)` and `policy_hash` |
| `ApprovalPolicy` | one object for the prompt and vocabulary: `threshold`, `system`, `instructions`, `examples`, phrase/marker tuples, optional `prompt_builder`; `prompt()` and `policy_hash` |
| `ApprovalFlow` | the ready step list hiding `InterpretApproval` + `ClarifyApproval`; pass `steps=[YourAction()]` and spread it: `Pipeline(steps=[AskApproval(), *ApprovalFlow(steps=[Action()])])` |
| `classify_approval(llm, question, *, policy=…)` | typed answer → deterministic parser → `structured_llm` fallback; low confidence/conditions/errors become ambiguous, never approve |
| `InterpretApproval` / `ClarifyApproval` | steps: answered `PendingQuestion` → linked `ApprovalDecision`; ambiguous/conditional → one typed follow-up `PendingQuestion` (`Approval` schema) |

## Text & rollback helpers (reactifact.recipes)

| Symbol | Role |
| --- | --- |
| `keyword_score(text, query, *, stopwords=EN_STOPWORDS, use_stems=False)` | deterministic query-coverage scoring (English / Russian) |
| `stem_words(text)` / `stem(word)` | Russian-English token stemming without embedders |
| `EN_STOPWORDS` | the default English stop-word set |
| `changed_fields(old, new, *, ignore=())` | which fields actually changed (new `None` = not a change) |
| `earliest_stage(changed, *, field_stages, order)` | the first stage a change affects (change → rebuild) |
| `downstream_fields(target, *, field_stages, order)` | fields to reset when rebuilding from a stage |

## Sessions, checkpoints, tracing

| Symbol | Role |
| --- | --- |
| `Session`, `SessionStore` | durable per-chat working memory across requests; `SessionStore(backend, compact_every=N)` bounds the append-only log (roughly every N saves it rewrites the snapshot); `store.checkpoints(id)` lists history, `store.fork(id, new_id=…, at=…)` starts a new session from a checkpoint |
| `KVBackend`, `FileKVBackend`, `SQLiteKVBackend`, `PostgreSQLKVBackend` | key/value checkpoints backing sessions (`pg` extra for Postgres) — async-native: file I/O runs off-thread, SQLite/Postgres each hold one persistent connection (WAL + busy_timeout on SQLite) serialized by an `asyncio.Lock`; SQLite/Postgres also implement `AppendBackend` (snapshot + append-only commit log, so a save writes only new commits) |
| `CheckpointBackend`, `FileBackend`, `SQLiteBackend` | full-context checkpoints |
| `Tracer`, `CompositeTracer`, `AgentSpan`, `AgentContract`, `RunTrace`, `LLMCall`, `StepRun` (`ProduceRun` alias), `ToolRun`, `TraceStore` | tracing primitives (async sinks: `export`/`query`/`get`/`sessions`; `AgentSpan.started_at` powers the dashboard timeline; `AgentSpan.event_artifact_type`/`event_artifact_id` identify the triggering artifact, `AgentSpan.contract` carries the declared consumes/produces/tools/priority, `AgentSpan.produces` lists the step runs that ran, and `AgentSpan.tools` lists `ToolRun`s; `get(span_limit=…, span_offset=…)` pages a large run and reports `span_total`/`has_more_spans`/`trace_truncated`; both stores expose `delete_run`/`delete_session`) |
| `TraceLimits` | write-time trace caps for `Tracer(..., limits=TraceLimits(max_spans=5000, max_refs_per_span=50, max_llm_calls_per_span=20, max_data_chars=None))`; `max_data_chars=None` falls back to the legacy `RuntimeResources.trace_truncate`; dropped data marks the trace `trace_truncated` |
| `TraceColumn` | a configurable, artifact-derived table column for `create_trace_router(store, columns=[…])`: `label`, `field` (dotted path, list→first), `agent`, `type` (class or name), `direction` read/write/any, `index`, `scope` run/session, `default` |
| `Tag`, `TagAssignment`, `TraceAnnotator`, `TraceStoreProtocol` | reviewer annotations: a tag vocabulary (`Tag`) and per-run tags + notes (`TagAssignment`, `RunTrace.annotations`); stores expose `list_tags`/`tag_runs`/`untag_runs`/`rename_tag`/`set_tag_color`/`delete_tag` |
| `LangfuseTracer`, `OTLPTracer`, `PostgresStore` | external trace sinks — `OTLPTracer` is vendor-neutral (GenAI semconv, any OTLP/HTTP collector), `LangfuseTracer` targets Langfuse specifically, Postgres supports async read+write and the full annotation API; the dashboard (`create_trace_router`) accepts any `TraceStoreProtocol` (SQLite or Postgres) |
| `create_trace_router(store, *, max_detail_spans=500, max_mermaid_spans=200, max_mermaid_nodes=120, max_mermaid_edges=240, max_concurrent_reads=2)` (`reactifact.tracing.web`) | FastAPI dashboard router: list/detail/sessions pages, tag facets + filtering, bulk tagging, tag-vocabulary management, URL-synced + saved views, sortable table, Tree/Timeline run view, in-trace search, raw JSON, JSON export; detail/diagram are bounded and lazy, `DELETE /api/traces/{id}` / `DELETE /api/sessions/{id}` remove runs/sessions; pages are prefix-aware (work under `include_router(prefix=…)` / ASGI `root_path`) |
| `Metrics`, `MetricsTracer`, `create_metrics_router` (`reactifact.metrics`) | dependency-free Prometheus metrics: counters/histograms (`runs_total`, `agent_runs_total`, `llm_calls_total`, `llm_tokens_total`, `llm_cost_total`, `artifacts_written_total`, `relations_total`, latencies…) filled from the tracer hook; `render()` emits the text exposition, `create_metrics_router` serves `GET /metrics`. `RuntimeResources(metrics=…)` exposes the same sink to produces (defaults to a no-op) |

## Trust & safety (reactifact.guardrails / reactifact.authz / reactifact.quota, §57)

| Symbol | Role |
| --- | --- |
| `GuardrailPolicy` · `PIIGuardrail` · `InjectionGuardrail` · `DenyListGuardrail` · `SizeGuardrail` · `PatternGuardrail` · `GuardrailViolation` | artifact-boundary checks on agent-produced data; `on_violation="block"|"flag"`, PII redaction via any `Redactor` |
| `Principal` · `PermissionPolicy` · `AuthorizationError` | capability-based authz on `run` / `create` / `update` / `delete` / `execute` |
| `Quota` · `QuotaTracker` · `QuotaLLM` · `QuotaExceeded` | cross-turn per-principal token/cost/call limits (`RunOutcome.QUOTA_EXCEEDED`) |
| `RuntimeResources(principal=, authorizer=, guardrails=, quota=)` · `resources.authorize`/`require_authorized`/`quota_key` | opt-in wiring; off by default |

## Testing (reactifact.testing, §56)

| Symbol | Role |
| --- | --- |
| `ScenarioLab`, `Scenario` | scenario harness: seed artifacts, run agents, assert (artifacts/tools/path/relations/llm/errors/events), `mode=` live/record/replay. Fault **and stub** injection: `fail`/`stub_tool` (tools) and `fail_resource`/`stub_resource` (any resource by string name or typed `Type`/`ResourceKey`), each with `times`/`when`/`delay`; `stub_resource` supports `returns`/`side_effect`. `run_sync`/`turn_sync` mirror the async API for plain tests |
| `ScenarioResult`, `Scenario` assertion groups | `result.artifacts(Type)` (`.linked(relation, Type)` for provenance), `result.relations` (`has`/`none`/`count`/`outgoing`/`incoming`), `result.tools`, `result.path`, `result.llm`, `result.errors`, `result.events`. `result.explain()` dumps the whole run |
| `ScenarioReport`, `ScenarioResult.report`, `Scenario.report` | token/latency/tool totals for a turn (or aggregated over a multi-turn scenario): `llm_calls`, `prompt_tokens`/`completion_tokens`/`total_tokens`, `tool_calls`, `spans`, `agents`, `errors`, `outcomes`, with `to_dict()` and `render()`. `result.llm` also exposes `prompt_tokens`/`completion_tokens`/`tokens` |
| `current_result()` | the most recent `ScenarioResult` this task produced — so tooling (the CLI) can show the run state when a later assertion fails |
| `capture(context, *, trace=…)`, `assert_golden(context, golden, *, trace=…)`, `assert_golden_file(context, path, *, trace=…, update=…)` | freeze/compare a `GoldenRun`; the file variant **seeds** the snapshot when it is missing (or `update`/`$REACTIFACT_GOLDEN_UPDATE`), and `ScenarioResult.assert_golden(path)`/`Scenario.assert_golden(path)` wrap it |
| `replay_resources(recording, *, base=…)` | a `RuntimeResources` whose llm replays a recording — an offline regression on a real run |
| `reactifact.testing.pytest_plugin` | opt-in pytest plugin (`pytest_plugins = ["reactifact.testing.pytest_plugin"]`): runs `async def` scenarios without pytest-asyncio, maps `ScenarioSkip` to a skip, adds a `scenario_lab` factory fixture |

## MCP (reactifact.mcp, `mcp` extra)

| Symbol | Role |
| --- | --- |
| `mcp_stdio_tools(command, args)`, `mcp_http_tools(url)` | connect to an MCP server, yield its tools as `list[Tool]` |
| `mcp_tools(session)`, `MCPTool` | wrap tools off an existing `mcp.ClientSession` |
| `oauth_client_credentials(server_url, client_id=, client_secret=, issuer=)`, `InMemoryTokenStorage` | `auth=` value for `mcp_http_tools` — OAuth's `client_credentials` grant (machine-to-machine, no browser/human consent) |
| `create_mcp_server(tools, context=...)` | expose `Tool`s (and, with `context=`, a `Context`'s artifacts) as an `mcp.server.mcpserver.MCPServer` |