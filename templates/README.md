# Templates

The canonical template lives inside the package so an installed
`reactifact new` can copy it without network access:

- [`reactifact/templates/fastapi_chat`](../reactifact/templates/fastapi_chat) —
  production-shaped FastAPI chat: `Depends` auth, scoped sessions, SSE, traces,
  settings-driven budgets and an OpenAI-compatible/custom LLM gateway. Opts into
  `reactifact[web]` itself; the core package stays FastAPI-optional.

Run it directly from a checkout, or generate a project with:

```bash
reactifact new app my-app
```

Working inside the reactifact repo (before the next PyPI release):

```bash
cd reactifact/templates/fastapi_chat
uv venv
uv pip install -e ../../.. 'pydantic-settings>=2.0' 'pytest>=8' httpx
PYTHONPATH=. .venv/bin/python -m pytest -q
```
