# Release management

How a `reactifact` version is cut, built, verified, and published.

## Versioning

- [SemVer](https://semver.org/); pre-releases carry an `rc` mark (e.g.
  `0.20.0rc1`), dropped for the stable cut (`0.20.0`).
- The version lives in **one place**: `pyproject.toml` → `[project] version`.
  `reactifact.__version__` reads the installed distribution metadata;
  `tests/test_version.py` guards the match.

## Changelog rule

Every user-visible change lands in `CHANGELOG.md` (Keep a Changelog). Cut the
entry when the version is bumped:

1. move unreleased items under a new `## [X.Y.Z] — <date>` heading;
2. group them as `Added` / `Changed` / `Removed` (deprecations too);
3. mark breaking changes explicitly, even in `rc`s.

## Upgrading

0.24.0 has a [migration guide](migration-0.24.md); 0.23.0 has [its own](migration-0.23.md); 0.20.0 has [its own](migration-0.20.md). `CHANGELOG.md`
remains the source of truth for what changed between versions, and breaking
entries are marked per the rule above. A few changes worth knowing if you're
crossing them (see also [migrating.md](migrating.md) for porting from other
frameworks):

- **0.25.0** — additive ergonomics: `scope=` on `Step`/`@step`, cross-turn
  `Unscoped()`, the `TurnContext` recipe, side-effect-only steps, typed
  `CurrentRequest`, `Step.progress`, `[tool.reactifact]` config, and
  `skills install --target opencode|all|auto`. No migration guide: no breaks.
- **0.24.0** — CLI/daemon/MCP/HTTP adapter work, `check`, `new agent`,
  `demo --web`, pipeline tools and HITL/approval recipes. The LLM helpers
  (`structured_llm`, `json_schema_llm`, `llm_reply`, `chat_complete*`,
  `StructuredLLM.call`) now take an `LLMProvider` (or `None`), not a `Context`.
  `Agent`, `create_agent`, `validate_agents`, `agents=` and the
  `reactifact.agents` module are removed; use `StepGroup`, `create_step_group`,
  `validate_contracts(groups=...)` and `groups=`.
- **0.23.0** — the typed `Chat` facade (`answer/traces/lifespan/test_client`),
  `Inject`/`Depends` tool DI, typed tool results and structured tool errors,
  function-first `PlanExecute`/`ReflectionLoop`, tail trace sampling and
  retention. `Produce`/`@produce`/`ProduceCall`, `also_creates=`/`reacts_to=`
  and `Effects.ask/resume/act` are removed (the last has no prior deprecation
  release; use `Human`/`Outbox`); the remaining deprecated bridges are
  documented in the 0.23 migration guide.
- **0.22.0** — CLI scaffolding (`new app|daemon`, `doctor`) and bundled
  FastAPI/daemon templates (FastAPI remains an optional core extra);
  provenance-aware `MemoryStore` with post-commit indexing; per-request chat
  resources; typed run events, checkpoints/forks, retries/timeouts, tool
  record/replay, swarm/map_reduce, optimistic concurrency/merge policies, typed
  HITL answers and trace scopes. `Produce`/`ProduceCall`/`@produce` leave the
  top-level `__all__` and are removed in 0.23.0.
- **0.21.0** — the optional, code-first `reactifact.knowledge` layer:
  `Connector → RawDocument → Document → Chunk → Index`, incremental `DocStore`
  state, `FilesystemConnector`, hybrid BM25 + RRF retrieval, `MemoryIndex` /
  `SqliteIndex` / `QdrantIndex` / `ChromaIndex` / `PgVectorIndex`, a domain
  `rerank=` hook plus `llm_reranker`, and an optional knowledge status router.
  `IndexSource` / `IngestStep` are the explicit bridges into `Source` / `Step`;
  core has no new runtime dependency.
- **0.20.0** — the new core line. `Step`/`@step` replace the `Produce` authoring
  surface; class steps take `effects: Effects` (`self.effects` is gone);
  `Artifact` is a frozen handle over a content-addressed version store; sessions
  use the v2 `blobs`/`data_ref` format; and the legacy example set was removed.
  `Produce`, `ProduceCall`, `@produce`, `StepGroup.produces`, `Artifact.at/history/
  diff` and the top-level tool-module shims remain importable for the 0.21
  compatibility window; `Produce`/`ProduceCall`/`@produce` are no longer part of
  the top-level `__all__`; `Produce`/`ProduceCall`/`@produce` are removed in 0.23.0, while the other bridges are kept through 0.24.0.
- **0.14.0** — behavior-visible (non-breaking) changes to know about:
  - `Budget.max_seconds` is now a **hard** turn deadline: the runtime cancels
    the generation in flight when it passes (a hung LLM call is actually
    stopped) and ends the turn with `RunOutcome.BUDGET_TIME_EXCEEDED`; the
    cancelled generation's trigger batch is left queued for a resume. Before,
    the check only ran between generations, so a run could overshoot it.
  - A new `RunOutcome.STOPPED` is reported when `Runtime.request_stop()` /
    `ashutdown()` ends a run at a generation boundary.
  - Logging is silent by default — nothing is emitted under `reactifact.*`
    until `configure_logging()` is called.
- **0.11.0** — behavior-visible (non-breaking) changes to know about:
  - `RunTrace.duration_ms` is now **milliseconds** (it carried raw
    `time.monotonic()` seconds before, so the dashboard showed `0 ms` and the
    Langfuse run span ended ~1000× too early). `RunStats.duration` stays
    seconds.
  - `RunTrace.started_at` is now the turn **start** (previously the end), and
    `AgentSpan.started_at` is persisted (SQLite/Postgres auto-migrate) to power
    the dashboard timeline.
  - The `TraceReader` protocol grew a `sessions()` read; the dashboard's
    `create_trace_router` now expects a `TraceStoreProtocol` (read + the tag
    annotation methods). The built-in `TraceStore`/`PostgresStore` satisfy it;
    a custom reader implementing only `query`/`get` needs `sessions()` and the
    annotation methods for the new pages to work.
- **0.10.0** — behavior-visible (non-breaking) changes to know about:
  - `PromptTemplate.render` now substitutes only *identifier-shaped* `{field}`
    placeholders and leaves other braces verbatim, so a literal JSON example in
    a prompt needs no `{{`/`}}` escaping. A placeholder *inside* a JSON object
    still needs the object's outer braces doubled (`{{"answer": "{answer}"}}`).
    Templates that relied on `str.format` treating a stray `{...}` as an
    error/format-spec now pass it through.
  - `reactifact.chat.ChatEvent.kind` is a closed
    `Literal["session","status","message"]`; constructing one with an arbitrary
    `kind` now fails validation.
  - Provider/`WebSource`/OTLP/Langfuse HTTP clients are created lazily per event
    loop (no more `RuntimeError: Event loop is closed` across loops); an
    injected `client=` is still used as-is.
  - `Runtime.arun/astream` gained a keyword-only `request=`; `Runtime.run`/
    `run_once` likewise. Existing positional calls are unaffected.
- **0.7.0** — `Context.merge_from` now preserves the `id` of an artifact
  that exists in `other` but not in `target` (it previously minted a fresh
  one). If you relied on the old id-regenerating behavior — unlikely, since
  it silently detached the merged artifact from any relation pointing at
  its original id — pass the artifact through `create(data, id=new_id())`
  yourself before merging to keep the old effect.
- **0.2.0** — `reactifact/__init__.py` re-exports only the core surface
  (~40 names, down from ~150); eval, tracing, checkpoint/branch backends,
  the chat/web layer, the adaptive scheduler, replay, structured-LLM
  helpers, viz, and prompt templates moved to their own submodule imports.
  Nothing renamed — see the `### Breaking` entry in `CHANGELOG.md` for the
  full before/after import list.
- **0.4.0-rc1** — `LLMRequest.temperature` changed from a hard-coded `0.7` to
  `float | None`; `None` now means "omit → provider default" instead of "use
  `0.7`". Same call shape, different generation behavior, no error raised —
  pass `temperature=0.7` explicitly (per-call or on the provider) if your code
  relied on the old implicit default.
- **0.1.0-rc1** — `Produce` no longer returns a `Patch`; it writes
  `effects.create/update/link/ask/resume` and returns `None` (see
  [effects](effects.md)). `InterruptPatch`, `Patch.merge_existing_patch` and
  `Patch.to_dict` were removed.

## Release checklist

What must be true before cutting a release — and, specifically, the bar for
leaving pre-1.0 and shipping **1.0.0**:

- [ ] `uv run python -m pytest -q` is green, coverage ≥ the `fail_under` floor
      (and `reactifact/quota.py`, `metrics.py`, `eval/` not the tail).
- [ ] `uv run python -m mypy` and `uv run python -m ruff check` /
      `ruff format --check` are clean.
- [ ] `uv run --group docs mkdocs build --strict` is clean (CI enforces it).
- [ ] The public surface is unchanged except as the freeze test
      (`tests/test_public_api.py`) and `docs/en/api.md` §Stability record; any
      rename/removal has a `### Breaking` changelog entry and a deprecation
      window behind it.
- [ ] The wheel smoke job passes in a clean venv (`uv build`, import, CLI).
- [ ] `CHANGELOG.md` moves `[Unreleased]` under `## [X.Y.Z] — <date>`.
- [ ] `tests/test_version.py` passes (distribution metadata matches
      `reactifact.__version__`).

**1.0 criteria** (all of the above, plus):

- [ ] Two consecutive minor releases with no `### Breaking` entry.
- [ ] Every documented submodule `__all__` is pinned by the freeze test and
      reflected in `docs/en/api.md`.
- [ ] The safety/eval/observability surfaces (guardrails, authz, quota, eval,
      online eval, metrics, tracing) each have usage docs and tests.
- [ ] No `rc`/`Unreleased`-only guarantees leak into the contract.

## The release loop

```bash
# 1) sanity
.venv/bin/python -m pytest
.venv/bin/python -m mypy
.venv/bin/python -m ruff check
.venv/bin/python -m ruff format --check
.venv/bin/lint-imports
uv lock --check

# 2) version + changelog (see above)

# 3) build artifacts (clean stale build/ + dist/ first; setuptools reuses build/lib)
rm -rf build dist *.egg-info
uv build                     # dist/reactifact-0.25.0-py3-none-any.whl + sdist

# 4) verify the wheel in a scratch venv (not the workspace, so no PYTHONPATH)
uv venv /tmp/reactifact-rc
/tmp/reactifact-rc/bin/python -m pip install --quiet dist/reactifact-0.25.0-py3-none-any.whl
/tmp/reactifact-rc/bin/python -c "import reactifact; print(reactifact.__version__)"
/tmp/reactifact-rc/bin/python -m reactifact graph examples.knowledge.step_groups 2>/dev/null \
    || /tmp/reactifact-rc/bin/reactifact --help >/dev/null   # console script present
# confirm the wheel contains reactifact + tracing templates and NOT examples/tests:
unzip -l dist/reactifact-0.25.0-py3-none-any.whl | grep -E "examples/|tests/|tracing/templates"

# 5) tag
git tag v0.25.0
git push origin v0.25.0

# 6) publish (PyPI token in env); publish only the new files, not an old dist/
uv publish --publish-url https://upload.pypi.org/legacy/ dist/reactifact-0.25.0*
```

## What ships

`uv build` packages only the `reactifact` package (setuptools `packages.find`
excludes `examples`/`tests`) plus the trace dashboard assets
(`reactifact/tracing/templates/*.html` and `*.css`). Examples, tests and docs
stay in the repository and are the documentation-by-example.

## Rollback

A broken `rc` is fixed in the next `rc`/release — never rewrite history of a
tagged version. Keep patch releases strictly backwards-compatible (§61: the
framework is stable at the stated surface).