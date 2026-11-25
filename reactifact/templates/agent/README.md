# my-agent

A minimal reactifact agent: one typed `@step`, one `Pipeline`, no web layer and
no API key.

```bash
uv sync
uv run reactifact run main:pipeline "hello"
uv run reactifact trace
```

`reactifact run` writes `.reactifact/traces.db`; `reactifact trace` renders the
latest run as Mermaid. Add `await pipeline.arun(...)` or `pipeline.astream(...)`
for async/streaming, and `pipeline.http()` when you want HTTP.
