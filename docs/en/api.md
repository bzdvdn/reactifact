# API reference

Top-level symbols exported by `reactifact` (see `reactifact/__init__.py`). The format
for each group: name — one-line role. Details live in the doc-strings of the
modules — see [the auto-generated reference](../reference.md) for those
rendered directly from source (signatures, types, full docstring text) on
the docs site, rather than copied here by hand.

## Stability

As of `0.4.0`, reactifact is pre-1.0 but no longer `rc` — the surface below is
the stable contract, not a moving target.

- **Public API = every name in `reactifact.__all__`** (and each submodule's own
  `__all__` — `reactifact.recipes`, `reactifact.providers`, `reactifact.viz`, `reactifact.eval`,
  `reactifact.quick`, `reactifact.redaction`, `reactifact.audit`, …), which is exactly the set of symbols documented on this page. If it's
  importable from `reactifact` but not in `__all__`, it's an internal detail with
  no compatibility guarantee — e.g. `reactifact.relations.RelationGraph` and
  `reactifact.commit_log.CommitLog` exist because `Context` was split into
  smaller modules for readability, but neither is exported: `Context` is the
  supported surface, they are not.
- **SemVer, pre-1.0 style**: a minor bump (`0.4.0` → `0.5.0`) may add symbols
  or, rarely, change behavior in a way `CHANGELOG.md` marks `Breaking` — pre-1.0
  minors are where reactifact is still allowed to correct a design mistake. A
  patch bump (`0.4.0` → `0.4.1`) never removes or renames a public symbol and
  never changes documented behavior, only fixes bugs against it.
- **Every breaking change is called out in `CHANGELOG.md` under a `### Breaking`
  heading**, even in a pre-1.0 release — see [release.md](release.md). If you
  only read one section before upgrading, read that one.
- Anything under `reactifact.cli.*` beyond the documented `python -m reactifact …`
  subcommands, and anything in a module's tests-only helpers, is implementation
  detail regardless of whether it happens to be importable.

## Quick on-ramp (`reactifact.quick`)

Thin sugar over the primitives below for the four common first tasks — every
object exposes the real `.agent`/`.agents` and the run's `.context`, so it
graduates to `Consume`/`Produce`/`Effects` with nothing to rewrite. See
[Quickstart §0](quickstart.md).

| Symbol | Role |
| --- | --- |
| `agent(system, schema)` | one structured LLM call → one typed artifact |
| `rag(sources)` | retrieval → materialize → answer, with `supported_by` provenance |
| `tools_agent(system, tools, human=False)` | LLM + tools (`human=True` → HITL asks) |
| `chat_agent(agents)` | a configured `ChatAssistant` (in-memory store by default) |
| `Question` / `Doc` / `Answer` | the generic artifact models the facade uses |

## Context & state

| Symbol | Role |
| --- | --- |
| `Context` | versioned working state; resources; queries; `latest(Model)`; announce; diff/rollback |
| `View` | result of a typed join query (`context.view(...)`) |
| `RuntimeResources` | providers + sources + app resources; `register(Type, instance)` / `get(Type)` / `require(Type)` / `has(Type)` for typed collaborators (`ResourceKey[T]` when two of one type); string `get`/`set` stay as the `additional` escape hatch; `redactor=` scrubs trace text; `await resources.aclose()` closes the llm/embedder HTTP clients (duck-typed) — call it yourself at real shutdown |
| `ResourceKey[T]` | a typed handle for registering two resources of one type (primary/replica, per-tenant) |
| `ResourceScope` / `RuntimeResources.scope(factory)` | `async with` builder that creates resources on the current loop and closes them on exit — the loop-safe way to own providers (see the class docstring) |
| `current_request()` | the active turn's request mapping (same as `ProduceCall.request`) |
| `Commit`, `Read`, `Write` | version bookkeeping and recorded provenance ops |

## Artifacts & changes

| Symbol | Role |
| --- | --- |
| `Artifact` | the `(id, data)` pair; `data` is a pydantic model |
| `Patch` | the runtime's compiled change-set (transport); produces write `self.effects`, `Patch` is assembled by the runtime |
| `reactifact.operations` (`Create`/`Update`/`Delete`/`Link`/`Unlink`/`Relation`) | the compiled operations a patch carries (§12) |
| `Create`, `Update`, `Delete`, `Link`, `Unlink`, `Relation` | op records from which patches are built |

## Agents & produces

| Symbol | Role |
| --- | --- |
| `Agent` | thin container: `name`, `consumes`, `produces`, `concurrency_limit` |
| `create_agent` | constructor-style Agent builder — no subclassing needed for plain containers |
| `Consume` / `consume` | declarative (or decorator) reaction declaration; `Consume.by_field` for scoped events; `wakes=False` reads as input without waking the agent; `debounce=True` collapses several same-generation events into one run |
| `reactifact.consume.CorrelatedConsume` | fires (and feeds inputs) only for a correlation key where every `require` type is present and every `forbid` type is absent — the mechanism behind `JoinConsume`/`AbsentConsume` |
| `reactifact.consume.JoinConsume(*parts, key=…)` | `CorrelatedConsume` factory: fires once every listed type exists for the same key |
| `reactifact.consume.AbsentConsume(type, absent_type=…, key=…)` | `CorrelatedConsume` factory: fires for `type` only where no matching `absent_type` exists yet for the same key |
| `Produce` / `produce` | the work unit: writes `self.effects` (or `effects` slot in a decorated function) → `None`; model/Patch return is compiled too. Two canonical styles — subclass and `@produce` function (see [effects](effects.md)); `reacts_to=(Type, …)` restricts which triggering event a produce runs on, when an agent's several produces don't all care about the same one; a produce declaring an optional `trigger` parameter gets the already-resolved triggering artifact instead of raw `event` — guaranteed non-`None` for a CREATED/UPDATED/STALE event when `reacts_to` is also set |
| `Trigger` | secondary (non-artifact) enter condition for a produce; `context_condition(artifact, context)` for conditions that need other artifacts (joins/correlation); `debounce` hint consumed by `Runtime` |
| `StructuredGenerateAgent` | declarative LLM→schema→artifact agent (`schema`, `build_prompt`, `fallback`) |
| `LLMAgent` | blocking LLM+tools loop (`system`, `tools`, `max_steps`, `deferred_tool_groups`) |
| `HITLLMAgent` | LLM+tools loop that can pause for human answers (`max_asks`, resume reporting) |
| `ToolUse`, `ToolUseHITL` | the tool-loop produce; HITL variant waits for approval on execution |
| `DeferredToolGroup` (`reactifact.tool_use`) | a tool group whose schemas stay out of the prompt until `ToolUse`'s built-in `load_tools` tool requests it (`LLMAgent`/`ToolUse` only, not the HITL variant) |
| `Tool`, `FunctionTool`, `tool`, `ToolOutput` | tool abstraction and registration |
| `ToolAnswer`, `Observation` | tool results and model observations (loop protocol) |
| `PendingQuestion` | HITL primitive: a paused ask waiting for a human answer, resumed via `self.effects.resume(...)` |

## Runtime

| Symbol | Role |
| --- | --- |
| `Runtime` | wakes agents on events; `run` / `arun` / `astream` (each takes `request=Mapping`); budget & concurrency; `isolate_errors=True` + `on_agent_error(agent, event, exc)` to keep one agent's exception from aborting the whole run (default: propagates, §69) |
| `ProduceCall.request` | the turn's request mapping inside a produce (`Runtime.arun(request=…)` / `ChatAssistant.stream(request=…)`); empty when none was set |
| `Budget`, `RunOutcome`, `RunStats` | run limits and the final outcome/stats |
| `Event`, `EventType` | the wire format of "something changed" — `ARTIFACT_CREATED`/`UPDATED`/`DELETED`/`STALE` |
| `EventHub`, `ProgressEvent` | progress/announce channel consumed by web UIs |
| `Scheduler` | filter → rank → LLM tie-break agent-selection policy, callable from the runtime each iteration (see [design notes](design-notes/adaptive.md)) |
| `uncertainty_policy(...)` | builds the built-in hybrid `Scheduler` (filter → rank → LLM tie-break → top-k) |

## Chat layer (reactifact.chat + reactifact.web)

| Symbol | Role |
| --- | --- |
| `ChatAssistant` | sessions + turn loop + history in one handle (`stream`/`invoke`/`history`); hooks: `agents`, `user_message`, `reply`, `session_state` |
| `ChatEvent` | one transport-neutral frame; `kind` is a closed `Literal["session","status","message"]`, so the schema is introspectable |
| `run_message(runtime, text, *, user_message, reply)` | the turn building block: create input → stream statuses → terminal reply |
| `default_session_state(ctx, user_message)` | generic history reader (any artifact with `.text`) |
| `create_chat_router(assistant, *, event_names=…, forward_kinds=…, payload_shaper=…, done_event=…)` | FastAPI `APIRouter` for the SSE contract (`/api/chat/stream`, `/api/runs/{id}`, `/api/health`) — needs the `web` extra. The wire **vocabulary is configurable** (rename kinds, filter, reshape payloads, emit a terminal frame); the event schema is published in the route's OpenAPI `responses` |
| `reactifact.web.sse(event, data)` | one SSE frame |

## Visualization (reactifact.viz + python -m reactifact)

| Symbol | Role |
| --- | --- |
| `blueprint(agents)` | static map of consumes/produces as Mermaid `flowchart` |
| `context_to_mermaid(context)` | live provenance graph of a context (artifacts + relations) |
| `trace_to_mermaid(trace)` | one run as a Mermaid `sequenceDiagram` |
| `python -m reactifact graph\|context\|trace` | CLI printing the diagrams to stdout |
| `trace_provenance_to_mermaid(trace)` | a run's evidence graph (written artifacts + `patch.link` edges) |

## Replay (reactifact.replay, §55)

| Symbol | Role |
| --- | --- |
| `ReplayLLM(recording, mode="record"\|"replay", inner=…)` | records every LLM call to JSONL, or replays them exactly; `ReplayMiss` on divergence |
| `ReplayMiss` | a replaying call did not match the recording |
| `replay_context(store, session_id, version=None)` | reconstructs a saved session's state at a commit |
| `replay_summary(context)` | compact state summary for the `replay` CLI |

## Branching (reactifact.context + reactifact.branching, §39-§40)

| Symbol | Role |
| --- | --- |
| `Context.branch(name="")` | forks an isolated copy; records the base snapshot for three-way merge |
| `Context.merge(other, message=…)` | atomic three-way merge; `MergeConflict` on diverged artifacts |
| `MergeConflict` | raised when both sides changed an artifact differently since the fork |
| `BranchStore(KVBackend)` | persists branches as `branch:<session>:<name>` over a KV backend |
| `python -m reactifact branch …` | CLI: `list` / `save` / `merge` |

## Evaluation (reactifact.eval, §56)

| Symbol | Role |
| --- | --- |
| `run_suite(cases, metrics)` / `run_case(case, metrics)` | execute cases and score the final contexts |
| `EvalCase` / `EvalResult` / `EvalReport` / `Metric` | case/score/report structures (`overall()`, `render()`, `to_dict()`) |
| `core_metrics` | the four non-generative metrics (answer/provenance/evidence/claim) |
| `answer_coverage()` · `calculation_correctness(values=…)` · `source_coverage()` · `confidence_calibration()` | ground-truth factories (skip when `expected` is missing) |

## Structured output

| Symbol | Role |
| --- | --- |
| `structured_llm(context, schema, *, system, user, attempts=…, validate=…, repair=…, prompt_hash=…, on_error=…)` | one structured call; `None` on honest failure; `validate(model)->bool` adds a domain-rule check retried like a parse failure, `repair(invalid_or_None, last_reply)->str` supplies the retry instruction; `on_error(reason, exc)` (`"no_provider"`\|`"provider_error"`\|`"parse_error"`\|`"validation_error"`) to distinguish *why*, without changing the `None` contract |
| `json_schema_llm(context, json_schema, *, user, validate=…, repair=…, …)` | structured output against your own JSON Schema (dict or JSON string), returns a plain `dict`; same `validate`/`repair` retry hooks as `structured_llm` |
| `StructuredLLM(schema, *, system=…, attempts=…, on_error=…)` | reusable instance; `.call(context, user)` |
| `llm_reply(context, *, system, user, attempts=…, on_error=…)` | plain-text completion → `str` or `None` (single-text schema under the hood) |
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
| `LLMProvider`, `EmbeddingProvider` | the two contracts the core talks to |
| `ImageProvider`, `SpeechProvider`, `TranscriberProvider`, `VideoProvider` | media contracts |
| `OpenAICompatProvider`, `OpenAICompatEmbedder` + vendor factories (`openai_llm`, `anthropic_llm`, `deepseek_llm`, `groq_llm`, `mistral_llm`, `openrouter_llm`, `gemini_llm`, `ollama_llm`, `azure_llm`, …) | 20+ chat/embedder backends, all `retry_attempts=3` by default (429/5xx/transport errors, exponential backoff — never on 4xx) |
| `openrouter_embedder`, `openrouter_speech`, `groq_transcriber`, `together_embedder`, `fireworks_embedder`, `qwen_embedder`, `nvidia_embedder` | embeddings/TTS/STT for vendors whose non-chat endpoints are confirmed OpenAI-compatible (see [providers](providers.md)) |
| `Message`, `Role` | one chat message; `role` is a closed `Literal` + `Message.system/user/assistant/tool` factories |
| `LLMRequest` | one completion: `messages` + `temperature`/`max_tokens` — `None` = provider default (call overrides provider, provider `None` = field omitted) |
| `LLMResponse`, `LLMResponseChunk` | one completion result / one streamed chunk returned by a provider |
| `*_from_env(**overrides)` | `.env`-driven wiring that returns `None` when unconfigured |
| `from_env(**overrides)` | one-call selection: `OPENROUTER_API_KEY` first, else `OPENAI_BASE_URL`, else `None` — the two-branch default every example's local `build_llm()` hand-rolls |
| `FakeLLM`, `FakeEmbedder` | deterministic stand-ins for tests/demos |

## Recipes (reactifact.recipes)

| Symbol | Role |
| --- | --- |
| `find(inputs, Model)` / `find_all(inputs, Model)` | typed lookup in a produce's `inputs` without `next(... isinstance ...)` |
| `fan_out_sources(context, query, owner_id, …)` | idempotent fan-out search → refs + patch |
| `materialize_doc(context, ref_artifact, doc_factory, relation=…)` | lazy ref → document with provenance |
| `StatusMachine` | deterministic artifact lifecycle (`next_status`, `terminal`, `on_transition`, `query_id_field`/`status_field`) |
| `WindowSummarizer(message_type, artifact_type, summarize=…, build=…)` | periodic conversation-window summarization, idempotent by message count |
| `WindowPruner(message_type, keep=…)` | deletes messages older than the window; standalone-useful |
| `llm_summarizer(system=…)` | builds a `WindowSummarizer(summarize=…)` callback from a system prompt via `llm_reply` |
| `run_tool_loop(context, *, system, user, tools, max_rounds=…, parallel=True, mandatory=…)` | opt-in native tool-calling loop over `native_tool_use`: bounded rounds, parallel tool execution, mandatory-tool nudging, forced final answer; returns `ToolLoopResult` (text, transcript, `ToolObservation`s) |

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
| `Session`, `SessionStore` | durable per-chat working memory across requests |
| `KVBackend`, `FileKVBackend`, `SQLiteKVBackend`, `PostgreSQLKVBackend` | key/value checkpoints backing sessions (`pg` extra for Postgres) — async-native: file I/O runs off-thread, SQLite/Postgres each hold one persistent connection (WAL + busy_timeout on SQLite) serialized by an `asyncio.Lock` |
| `CheckpointBackend`, `FileBackend`, `SQLiteBackend` | full-context checkpoints |
| `Tracer`, `CompositeTracer`, `AgentSpan`, `RunTrace`, `LLMCall`, `TraceStore` | tracing primitives (async sinks: `export`/`query`/`get`) |
| `LangfuseTracer`, `OTLPTracer`, `PostgresStore` | external trace sinks — `OTLPTracer` is vendor-neutral (GenAI semconv, any OTLP/HTTP collector), `LangfuseTracer` targets Langfuse specifically, Postgres supports async read+write; the dashboard (`create_trace_router`) accepts any `TraceReader` |
| `create_trace_router(store)` (`reactifact.tracing.web`) | FastAPI dashboard router |

## Testing (reactifact.testing, §56)

| Symbol | Role |
| --- | --- |
| `ScenarioLab` | scenario harness: seed artifacts, run agents, assert (artifacts/tools/path/errors), `mode=` live/record/replay, fault injection (`fail(tool, …)`, `fail_resource(name_or_key, …)` — a string name or a typed `Type`/`ResourceKey`) |
| `capture(context, *, trace=…)` | freeze a `GoldenRun` — `context_hash` plus the trace's prompt hashes |
| `assert_golden(context, golden, *, trace=…)` | fail loudly when state (or, with `trace`, prompts) drifted |
| `replay_resources(recording, *, base=…)` | a `RuntimeResources` whose llm replays a recording — an offline regression on a real run |

## MCP (reactifact.mcp, `mcp` extra)

| Symbol | Role |
| --- | --- |
| `mcp_stdio_tools(command, args)`, `mcp_http_tools(url)` | connect to an MCP server, yield its tools as `list[Tool]` |
| `mcp_tools(session)`, `MCPTool` | wrap tools off an existing `mcp.ClientSession` |
| `oauth_client_credentials(server_url, client_id=, client_secret=, issuer=)`, `InMemoryTokenStorage` | `auth=` value for `mcp_http_tools` — OAuth's `client_credentials` grant (machine-to-machine, no browser/human consent) |
| `create_mcp_server(tools, context=...)` | expose `Tool`s (and, with `context=`, a `Context`'s artifacts) as an `mcp.server.mcpserver.MCPServer` |