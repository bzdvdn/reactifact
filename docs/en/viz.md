# Visualization & CLI

`reactifact.viz` renders the system as **Mermaid** strings. There is no execution
graph to draw — the runtime derives execution from state changes — so the two
honest diagrams are the *static map* and the *dynamic state*:

| Function | What it draws |
| --- | --- |
| `blueprint(agents)` | static map: artifact types = nodes, agents = edges (`Consume` / `creates` / `lifecycle`) |
| `context_to_mermaid(context)` | live provenance graph: artifacts grouped by type, `patch.link` relations |
| `trace_to_mermaid(trace)` | one run as a `sequenceDiagram`: spans over time, writes/reads, LLM calls |
| `trace_provenance_to_mermaid(trace)` | one run's **evidence graph**: written artifacts as nodes, `patch.link` edges (§34, §54) |

All three are pure string functions — no dependencies, paste the output into
GitHub, Notion, or [mermaid.live](https://mermaid.live).

## CLI

```bash
python -m reactifact run app:pipeline "why is checkout down?"
reactifact graph examples.knowledge.agents        # all agents of a module
python -m reactifact graph examples.knowledge.agents:Planner # one agent
python -m reactifact graph examples.knowledge.agents --check --seed UserQuery --terminal Answer
reactifact contracts examples.knowledge.agents          # Markdown contracts
reactifact contracts examples.knowledge.agents --format json
python -m reactifact context examples/knowledge/sessions/sessions.sqlite3
python -m reactifact trace traces.db [run_id]
reactifact check app.pipeline:pipeline --scenarios app.scenarios
reactifact daemon app.pipeline:pipeline --seed app.seeds:next_tick --interval 60
reactifact new agent my-agent
reactifact dev app.main:app
reactifact demo --web
```

- `validate`, `graph --check` and `check` read `[tool.reactifact]` from
  `pyproject.toml` (`agents`, `seeds`, `terminal`, `budget`, `scenarios`,
  `evals`, `overall`, `gates`) when the matching flags are absent; flags win.
- `check` is the quality gate: static contracts, any number of `@scenario`
  modules and an optional `Dataset` eval against a `Pipeline` (`--gate
  KEY=VALUE`, `--overall`); it exits non-zero when a stage fails.
- `daemon` runs a `Pipeline` on an interval (`--seed module:fn` seeds each
  tick); `new agent` writes a minimal one-file pipeline; `dev` boots an ASGI
  app with reload and prints the app/`/contracts`/`/traces` URLs; `demo --web`
  serves the no-key demo in a browser.
- `graph` discovers module-level `Agent` instances (and instantiates legacy
  `Agent` subclasses) or takes a single one via `module:Attr`, then prints the
  blueprint. With `--check` it
  runs `validate_contracts` instead: unknown inputs are errors (exit 1), dead
  outputs/unreachable/cycles are warnings; `--seed TYPE`/`--terminal TYPE` are
  repeatable.
- `contracts` prints the declared surface (steps, reads/produces, tools,
  timeout/retry) as Markdown or JSON; no run and no LLM.
- `context` reads a saved session from a KV backend (file directory or
  SQLite) and prints its provenance graph; `--session` picks a session,
  `--limit` caps the number of artifacts.
- `trace` reads a run from a `TraceStore` db (default: the latest run).

## In the dashboard

Apps can also mount the same contracts as a page with
`reactifact.contracts.web.create_contracts_router(agents)` — `/contracts` (HTML), `/api/contracts`
(JSON) and `/contracts.md` (Markdown). It is opt-in and supports Basic auth.

The run page (`/traces/<run_id>`) renders the trace diagram live via Mermaid,
with a `copy` button and the source in a collapsible `<pre>`; if the browser is
offline (no Mermaid JS), it degrades to showing the source text.