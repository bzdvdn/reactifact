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
reactifact graph examples.knowledge.step_groups        # all agents of a module
python -m reactifact graph examples.knowledge.step_groups:Planner # one agent
python -m reactifact graph examples.knowledge.step_groups --check --seed Question --terminal Answer
python -m reactifact context examples/knowledge/sessions/sessions.sqlite3
python -m reactifact trace traces.db [run_id]
```

- `graph` instantiates every `StepGroup` subclass defined in the module (or a
  single one via `module:Attr`) and prints the blueprint. With `--check` it
  runs `validate_contracts` instead: unknown inputs are errors (exit 1), dead
  outputs/unreachable/cycles are warnings; `--seed TYPE`/`--terminal TYPE` are
  repeatable.
- `context` reads a saved session from a KV backend (file directory or
  SQLite) and prints its provenance graph; `--session` picks a session,
  `--limit` caps the number of artifacts.
- `trace` reads a run from a `TraceStore` db (default: the latest run).

## In the dashboard

The run page (`/traces/<run_id>`) renders the trace diagram live via Mermaid,
with a `copy` button and the source in a collapsible `<pre>`; if the browser is
offline (no Mermaid JS), it degrades to showing the source text.