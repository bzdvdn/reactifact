# tour — the framework primitives in seventeen small scenes

The domain examples show complete applications. This one is the compact
counterpart: each scene isolates a primitive that is easy to miss when reading a
large app. It runs offline by default and needs no web server.

```bash
.venv/bin/python -m examples.tour.main
```

Scenes:

| Scene | What it shows |
| --- | --- |
| `state` | typed `Step`, artifact versions, `version_history`, `at`, `diff`, `compact` |
| `reactive` | typed inputs, `Latest`, implicit read-only inputs, `Consume(..., debounce=True)` |
| `durable` | `SQLiteKVBackend` + `SessionStore`, restart, pending-event resume, `replay_context` |
| `model` | an optional LLM step through `llm_reply`, with an offline fallback |
| `resilience` | `Agent.retry` with `RetryPolicy`, `Step.timeout` |
| `hitl` | `human.ask(..., schema=Model)` + typed `context.resume(...)` |
| `knowledge` | offline ingestion, hybrid retrieval, diagnostics and retrieval metrics |
| `time_travel` | `context.checkpoints()`, `context.at(checkpoint)`, `SessionStore.fork` |
| `events` | `Runtime.astream_events` with all `stream_mode`s |
| `conflicts` | `effects.update(expected_version=...)`, `ArtifactConflict`, merge policy |
| `subflow` | `run_subflow` — child Context, provenance link, parent run id |
| `map_reduce` | `map_reduce` — stable `MapTask`s, debounced reduce |
| `swarm` | `swarm` — peer handoff chain, hop/cycle guard |
| `replay` | tool record/replay via `Runtime.replay_run` |
| `experiment` | A/B variants over a dataset, eval, winner |
| `validate` | static consumes/produces contract check |
| `memory` | namespaced long-term memory (`MemoryStore`) |

Run one scene:

```bash
.venv/bin/python -m examples.tour.main --scene state
.venv/bin/python -m examples.tour.main --scene reactive
.venv/bin/python -m examples.tour.main --scene durable
.venv/bin/python -m examples.tour.main --scene model
.venv/bin/python -m examples.tour.main --scene resilience
.venv/bin/python -m examples.tour.main --scene hitl
.venv/bin/python -m examples.tour.main --scene knowledge
.venv/bin/python -m examples.tour.main --scene time_travel
.venv/bin/python -m examples.tour.main --scene events
.venv/bin/python -m examples.tour.main --scene conflicts
.venv/bin/python -m examples.tour.main --scene subflow
.venv/bin/python -m examples.tour.main --scene map_reduce
.venv/bin/python -m examples.tour.main --scene swarm
.venv/bin/python -m examples.tour.main --scene replay
.venv/bin/python -m examples.tour.main --scene experiment
.venv/bin/python -m examples.tour.main --scene validate
.venv/bin/python -m examples.tour.main --scene memory
```

Keep the durable session to inspect or replay it yourself:

```bash
.venv/bin/python -m examples.tour.main --scene durable --db /tmp/tour.sqlite3
.venv/bin/python -m reactifact replay /tmp/tour.sqlite3 --session tour --diagram
```

## With an LLM

The tour is deterministic unless you ask for a model. Copy `.env.example` to
`.env`, set one provider, then use `--llm`:

```bash
cp examples/tour/.env.example examples/tour/.env
# edit examples/tour/.env
.venv/bin/python -m examples.tour.main --llm --scene model
```

`from_env()` chooses OpenRouter when `OPENROUTER_API_KEY` is set, otherwise an
OpenAI-compatible endpoint via `OPENAI_BASE_URL`. With no provider, the model
scene prints the offline brief rather than a fabricated answer.

## Layout

- `scenes/state.py` — versions, history, diff, compaction;
- `scenes/reactive.py` — typed inputs, `Latest`, `Wakes`, debounce;
- `scenes/durable.py` — SQLite session, restart, resume, replay;
- `scenes/model.py` — optional LLM step with fallback;
- `scenes/resilience.py` — agent retry and per-step deadline;
- `scenes/hitl.py` — schema-backed question + typed resume;
- `scenes/time_travel.py` — checkpoints, snapshot, fork;
- `scenes/events.py` — typed `astream_events` modes;
- `scenes/conflicts.py` — expected-version conflict + merge policy;
- `scenes/subflow.py` — child Context with trace/provenance;
- `scenes/map_reduce.py` — stable map tasks + debounced reduce;
- `scenes/swarm.py` — peer handoff chain with a hop/cycle guard;
- `scenes/replay.py` — tool call record/replay;
- `scenes/experiment.py` — A/B variants + eval winner;
- `scenes/validate.py` — static contract check;
- `scenes/memory.py` — namespaced long-term memory;
- `main.py` — argument parsing and terminal output;
- `../../tests/test_tour.py` — asserts every scene, including the offline and
  `FakeLLM` model paths.

The scenes are intentionally small. For a full application, start with
[`starter_app`](../starter_app), then follow the [examples guide](../../docs/en/examples.md).
