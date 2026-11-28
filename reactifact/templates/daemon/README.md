# Reactifact daemon template

A scheduled agent loop — not a web server. Every tick creates a fresh
`Context`, runs one reactive agent, writes a `Report`, and sleeps. LLM provider
and budget are settings; `APP_LLM_PROVIDER=none` runs offline with a
deterministic report.

```bash
uv sync --extra dev
cp .env.example .env
uv run python -m app.main
```

One tick and exit (CI/smoke):

```bash
APP_RUN_ONCE=true uv run python -m app.main
```

## What is in the box

```text
app/
  main.py       # signal handling + interval loop
  runner.py     # one tick: PipelineRunner + RunConfig -> Report
  monitor.py    # monitor @step + monitor_agent
  models.py     # Tick, Report
  prompts.py    # prompt text outside the step
  resources.py  # per-tick RuntimeResources (LLM + service principal)
  config.py     # interval, LLM, budgets, tracer
  llm.py        # build_llm from Settings
  llm_presets.py# OpenAI-compatible presets + custom gateway
```

## Production notes

- Graceful shutdown: `SIGINT`/`SIGTERM` set an `asyncio.Event`; the loop finishes
  the current tick and exits.
- One process owns one tick at a time; scale by running separate daemons with a
  queue/lease, not by adding threads to this loop.
- `RuntimeResources.aclose()` closes the per-tick LLM after each tick. Shared
  DB/Qdrant clients, if you add them, belong in process startup/shutdown.
- `APP_RUN_ONCE=true` is the smoke-test/one-shot mode; no infinite loop.
- Traces go to `APP_TRACE_PATH` when tracing is enabled.

Replace `monitor` with your real domain step; keep the loop, settings,
`RunConfig` and graceful shutdown.
