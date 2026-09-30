# Release management

How a `reactifact` version is cut, built, verified, and published.

## Versioning

- [SemVer](https://semver.org/); pre-releases carry an `rc` mark (e.g.
  `0.5.0rc1`), dropped for the stable cut (`0.5.0`).
- The version lives in **two places** and must stay in sync:
  - `pyproject.toml` → `[project] version`;
  - `reactifact/__init__.py` → `__version__`.

## Changelog rule

Every user-visible change lands in `CHANGELOG.md` (Keep a Changelog). Cut the
entry when the version is bumped:

1. move unreleased items under a new `## [X.Y.Z] — <date>` heading;
2. group them as `Added` / `Changed` / `Removed` (deprecations too);
3. mark breaking changes explicitly, even in `rc`s.

## Upgrading

There's no separate migration doc — `CHANGELOG.md` is the source of truth for
what changed between versions, and breaking entries are marked per the rule
above. A few changes worth knowing if you're crossing them (see also
[migrating.md](migrating.md) for porting from other frameworks):

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
    a prompt needs no `{{`/`}}` escaping. Templates that relied on `str.format`
    treating a stray `{...}` as an error/format-spec now pass it through.
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
- **0.5.0** — `reactifact/__init__.py` re-exports only the core surface
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
  `self.effects.create/update/link/ask/resume` and returns `None` (see
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
- [ ] `pyproject.toml` and `reactifact/__init__.py` versions match.

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

# 2) version + changelog (see above)

# 3) build artifacts
uv build                     # dist/reactifact-0.5.0-py3-none-any.whl + sdist

# 4) verify the wheel in a scratch venv (not the workspace, so no PYTHONPATH)
uv venv /tmp/reactifact-rc
/tmp/reactifact-rc/bin/python -m pip install --quiet dist/reactifact-0.5.0-py3-none-any.whl
/tmp/reactifact-rc/bin/python -c "import reactifact; print(reactifact.__version__)"
/tmp/reactifact-rc/bin/python -m reactifact graph examples.knowledge.agents 2>/dev/null \
    || /tmp/reactifact-rc/bin/reactifact --help >/dev/null   # console script present
# confirm the wheel contains reactifact + tracing templates and NOT examples/tests:
unzip -l dist/reactifact-0.5.0-py3-none-any.whl | grep -E "examples/|tests/|tracing/templates" 

# 5) tag
git tag v0.5.0
git push origin v0.5.0

# 6) publish (PyPI token in env)
uv publish --publish-url https://upload.pypi.org/legacy/
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