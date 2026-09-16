# Contributing

## Setup

```bash
uv sync --extra dev --extra web
```

## Before opening a PR

Run the same gates CI and the release process run:

```bash
.venv/bin/python -m pytest
.venv/bin/mypy
.venv/bin/ruff check
.venv/bin/ruff format --check
```

- Add tests for new behavior; extend an existing `tests/test_*.py` file when
  one already covers the area.
- If your change is user-visible, add a `CHANGELOG.md` entry (see
  `docs/en/release.md`'s "Changelog rule" and "Upgrading" section) — mark it
  breaking explicitly if it changes existing behavior without raising an
  error.
- Docs live in `docs/en/` and `docs/ru/` as a 1:1 mirror; update both if you
  touch a documented symbol or behavior. `docs/en/api.md` / `docs/ru/api.md`
  should stay in sync with `reactifact/__init__.py`'s public exports.
- The docs site (<https://bzdvdn.github.io/reactifact/>, `mkdocs.yml`,
  EN-only for now — `docs/ru/` isn't wired into the nav yet) deploys
  automatically from `master` (`.github/workflows/docs.yml`) on any
  `docs/**`/`mkdocs.yml` change. Preview locally before relying on the
  nav placement of a new page:
  ```bash
  uv sync --extra docs
  uv run mkdocs serve
  ```

## Design principles

New code should be consistent with [docs/constitution.md](docs/constitution.md) —
the project's design rationale and invariants (state over execution, artifacts
over strings, provenance, determinism where possible). If a change conflicts
with a stated principle, say so in the PR description rather than silently
working around it.

## Scope

Domain-specific connectors (GitLab, Confluence, S3, etc.) belong in
`examples/`, not in `reactifact/` core — see docs/constitution.md §61.
