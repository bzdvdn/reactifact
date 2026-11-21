# Templates

The canonical template lives inside the package so an installed
`reactifact new` can copy it without network access:

- [`reactifact/templates/fastapi_chat`](../reactifact/templates/fastapi_chat) —
  production-shaped FastAPI chat: `Depends` auth, scoped sessions, SSE, traces,
  settings-driven budgets and an OpenAI-compatible/custom LLM gateway. Opts into
  `reactifact[web]` itself; the core package stays FastAPI-optional.
- [`reactifact/templates/daemon`](../reactifact/templates/daemon) — scheduled
  agent loop with settings-driven LLM/budget, traces and graceful shutdown.
  No FastAPI; `APP_RUN_ONCE=true` is a one-shot smoke mode.

Generate either one:

```bash
reactifact new app my-app
reactifact new daemon my-worker
```
