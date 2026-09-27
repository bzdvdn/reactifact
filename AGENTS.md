# AGENTS.md

Guidance for AI coding agents (and humans) working on **reactifact**.

## What this repo is

`reactifact` is a reactive, artifact-driven agent runtime: agents transform
typed, versioned, provenance-aware artifacts in a `Context`. The mental model
and the contribution rules are in `docs/constitution.md`; user-facing docs are
in `docs/en/` (Russian mirrors in `docs/ru/`).

## Working with the framework (writing agent code)

The repo ships **Agent Skills** for building *with* reactifact — a mental model,
the produce/consume/effects paradigm, and the mistakes mutation-style frameworks
lead you into. They live in `reactifact/skills/<name>/SKILL.md` and are
installed into a project with:

```bash
python -m reactifact skills install                 # -> .agents/skills/
python -m reactifact skills install --target both    # also .claude/skills/
reactifact skills list
```

Skills: `reactifact` (entry point + mental model), `reactifact-agents`,
`reactifact-llm`, `reactifact-rag`, `reactifact-testing`, `reactifact-eval`,
`reactifact-observability`, `reactifact-from-langchain`. If you are writing
code that *uses* reactifact, read the matching skill first.

## Working on the library (this repo)

- **Tests**: `.venv/bin/python -m pytest -q`. Tests are synchronous; async code
  is driven with `asyncio.run(...)` inside a sync test (there is no
  `pytest-asyncio`).
- **Types**: `.venv/bin/mypy` (strict; packages `reactifact`, `examples`).
- **Lint/format**: `.venv/bin/ruff check` and `.venv/bin/ruff format --check`
  (line length 88). Run `ruff format` before committing.
- **Docs**: `.venv/bin/mkdocs build -d /tmp/mkdocs_out` must succeed.
- Skill code blocks are executed in CI by `tests/test_skills.py`: a fenced
  ```` ```python ```` block runs unless it starts with a `# not-run` comment.
  Keep at least one runnable block per `SKILL.md`.

## House rules

- Match the surrounding style; prefer the existing helpers over new ones.
- The core stays dependency-light: new runtime dependencies need a strong
  reason. Optional features (FastAPI, Postgres, MCP) are lazy-imported.
- Every behavioural change needs a test; keep the suite green and the type
  checks clean.
- Never commit secrets. Only commit when the user asks.
