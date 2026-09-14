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
above. Two changes worth knowing if you're crossing them:

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
excludes `examples`/`tests`) plus the trace dashboard templates
(`reactifact/tracing/templates/*.html`). Examples, tests and docs stay in the
repository and are the documentation-by-example.

## Rollback

A broken `rc` is fixed in the next `rc`/release — never rewrite history of a
tagged version. Keep patch releases strictly backwards-compatible (§61: the
framework is stable at the stated surface).