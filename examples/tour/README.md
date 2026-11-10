# tour — the framework primitives in four small scenes

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
| `reactive` | typed inputs, `Latest`, `Wakes(False)`, `Consume(..., debounce=True)` |
| `durable` | `SQLiteKVBackend` + `SessionStore`, restart, pending-event resume, `replay_context` |
| `model` | an optional LLM step through `llm_reply`, with an offline fallback |

Run one scene:

```bash
.venv/bin/python -m examples.tour.main --scene state
.venv/bin/python -m examples.tour.main --scene reactive
.venv/bin/python -m examples.tour.main --scene durable
.venv/bin/python -m examples.tour.main --scene model
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
- `main.py` — argument parsing and terminal output;
- `../../tests/test_tour.py` — asserts every scene, including the offline and
  `FakeLLM` model paths.

The scenes are intentionally small. For a full application, start with
[`starter_app`](../starter_app), then follow the [examples guide](../../docs/en/examples.md).
