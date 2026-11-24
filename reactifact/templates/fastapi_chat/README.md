# FastAPI chat template

A production-shaped starting point for a **web chat app** on reactifact:
FastAPI, authenticated sessions, SSE, traces, settings-driven budgets and an
OpenAI-compatible LLM gateway.

> reactifact core stays headless. This project opts into FastAPI itself via
> `reactifact[web]`; installing reactifact alone does **not** install FastAPI.

## Quick start

From a generated project (or this directory copied out of the repo):

```bash
uv sync --extra dev
cp .env.example .env
# set APP_AUTH_TOKEN and (optionally) an LLM provider block in .env
uv run uvicorn app.main:create_app --factory --reload
```

No LLM configured (`APP_LLM_PROVIDER=none`) is a valid offline mode: the app
starts, the SSE contract works, and the assistant returns the honest fallback
instead of guessing.

## What is in the box

```text
app/
  main.py              # create_app + lifespan
  config.py            # Settings: budgets, LLM, stores, auth, chat
  llm_presets.py       # provider presets (OpenAI-compatible + Anthropic)
  llm.py               # build_llm from Settings
  security.py          # demo bearer auth + session key scoping
  bootstrap.py         # stores, Budget, ChatMemory, assistant
  api/
    deps.py            # get_assistant
    routes/chat.py     # POST /api/chat/stream, GET /api/chat/history, DELETE
  agent/
    models.py          # UserQuery, Answer
    prompts.py         # prompts live outside steps
    steps/answer.py     # one structured answer per turn
    resources.py       # per-turn RuntimeResources + principal/authz
    pipeline.py        # Chat facade hooks + AnswerAgent
tests/
  conftest.py          # TestClient, settings, auth headers
  test_chat_sse.py     # SSE + history + 401
  test_auth_sessions.py# one user cannot read/clear another's session
  test_settings.py     # presets, model required, budget mapping, prod checks
```

## Settings first

Every budget, provider and knob comes from `app/config.py`; there is no
hardcoded `Budget(...)` or vendor model in the wiring.

Key groups:

- **LLM**: `APP_LLM_PROVIDER`, `APP_LLM_MODEL`, `APP_LLM_BASE_URL`,
  `APP_LLM_API_KEY_ENV`, temperature/timeout/retries, auth header/scheme,
  `APP_LLM_EXTRA_HEADERS` / `APP_LLM_EXTRA_BODY`.
- **Budget** (per turn): `APP_BUDGET_MAX_RUNS`, `..._MAX_SECONDS`,
  `..._MAX_TOOL_CALLS`, `..._MAX_TOKENS`, `..._MAX_COST`.
- **Chat**: concurrency, `ChatMemory` window/commit compaction, save policy.
- **Stores**: `APP_SESSION_BACKEND` (`memory|file|sqlite|postgres`),
  `APP_TRACE_BACKEND=sqlite`.
- **Auth placeholder**: `APP_AUTH_TOKEN`, `APP_AUTH_USER_ID`.

`Settings.validate_runtime()` fails fast in production for an in-memory session
store, a missing auth token, or a missing provider key.

### Providers

`APP_LLM_PROVIDER` is a preset name or `custom`:

| Preset | Protocol | Base URL | Key env |
| --- | --- | --- | --- |
| `openai` | OpenAI-compatible | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| `openrouter` | OpenAI-compatible | `https://openrouter.ai/api/v1` | `OPENROUTER_API_KEY` |
| `deepseek` | OpenAI-compatible | `https://api.deepseek.com/v1` | `DEEPSEEK_API_KEY` |
| `groq` | OpenAI-compatible | `https://api.groq.com/openai/v1` | `GROQ_API_KEY` |
| `mistral`, `xai`, `together`, `fireworks`, `cerebras` | OpenAI-compatible | provider default | see `llm_presets.py` |
| `ollama`, `vllm`, `lmstudio`, `litellm` | OpenAI-compatible | local default | optional |
| `anthropic` | native Messages API | `https://api.anthropic.com/v1` | `ANTHROPIC_API_KEY` |
| `custom` | OpenAI-compatible | **`APP_LLM_BASE_URL` required** | `APP_LLM_API_KEY_ENV` optional |

The model is **never defaulted**: set `APP_LLM_MODEL` explicitly. If a vendor
is missing from the table, use `custom` + its OpenAI-compatible base URL.

## Auth and session ownership

`security.py` is a **demo bearer token**, not real auth. Replace
`get_current_user` with JWT/OIDC and keep `scope_session`:

```python
ref = scope_session(user, body.session_id)
# ref.client_id  → returned to the UI in the SSE session frame
# ref.storage_id → assistant.stream/history/delete
```

This prevents a logged-in user from reading or deleting another user's session
by reusing its client id. The template's tests cover exactly that case.

## Resource ownership

`build_resources` creates per-turn `RuntimeResources` (a fresh LLM per turn).
`Chat` closes `llm`/`embedder` after the turn. Everything else
(DB/Qdrant/custom clients) is app-owned and belongs in `lifespan`, not in
`build_resources`.

The template is single-process by design: if you add an in-process refresh or
outbox worker, keep it in `lifespan` and document the single-replica
constraint. For heavy ingestion, use an external worker/queue.

## Tests

```bash
uv run pytest -q
uv run ruff check app tests
uv run mypy
```
