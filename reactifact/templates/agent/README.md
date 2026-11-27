# my-agent

A minimal reactifact agent: one typed `@step`, one `Pipeline`, no web layer and
no API key.

```bash
uv sync
uv run reactifact run main:pipeline "hello"
uv run reactifact trace
```

`reactifact run` writes `.reactifact/traces.db`; `reactifact trace` renders the
latest run as Mermaid. Use `PipelineRunner(pipeline)` for `run`/`arun`/
`astream`/`http`; pass `RunConfig(...)` for resources, tracing and concurrency.
