# starter_app — a copy-pasteable app over the four `quick` cases

One FastAPI process covering the shapes most apps start with, so you can clone
it, run it, and then swap the domain:

| endpoint | case | quick entry point |
| --- | --- | --- |
| `POST /api/ask {mode:"agent"}` | one structured LLM call | `quick.agent` |
| `POST /api/ask {mode:"rag"}` | retrieval over `knowledge/`, with citations | `quick.rag` |
| `POST /api/ask {mode:"tools"}` | an LLM that can call `add()` | `quick.tools_agent` |
| `POST /api/chat` | session-persisted assistant | `quick.chat_agent` |
| `GET /traces` | trace dashboard for every run above | `reactifact.tracing` |

There's a tiny UI at `/` (plain HTML, no build step).

## Run

```bash
uv sync --extra web
.venv/bin/python -m examples.starter_app.app
# open http://127.0.0.1:8000
```

Works with **no key** — offline/deterministic honest fallbacks; `rag` still
answers from `knowledge/` and cites the file. For real LLM calls, copy
`.env.example` to `.env` and set one provider:

```bash
cp examples/starter_app/.env.example examples/starter_app/.env
# OpenRouter:            OPENROUTER_API_KEY=sk-or-...
# or OpenAI-compatible:  OPENAI_BASE_URL=https://api.openai.com/v1 + OPENAI_API_KEY
```

`from_env()` tries OpenRouter first, then `OPENAI_BASE_URL` (OpenAI, vLLM,
Ollama, …); with neither set it returns `None`, which every helper treats as an
honest fallback rather than a crash. `GET /api/provider` tells you which mode
you're in.

## What to keep when you copy it

- `app.py` — the wiring: provider → `Tracer(TraceStore(...))` → the quick
  objects → the FastAPI routes. Replace the domain (prompts, `knowledge/`,
  tools), keep the edges.
- `models.py` — your wire/artifact models.
- `.env` handling and the `/traces` mount are the two things people forget.

## curl it

```bash
curl -s localhost:8000/api/provider
curl -s localhost:8000/api/ask -H 'content-type: application/json' \
  -d '{"mode":"rag","question":"how do refunds work?"}'
curl -s localhost:8000/api/chat -H 'content-type: application/json' \
  -d '{"message":"hello","session_id":"demo"}'
```

## Structure

```
starter_app/
├── knowledge/         # your docs (RAG corpus)
├── web/index.html     # tiny no-build UI
├── models.py          # AskRequest/AskResponse/Chat… + AnswerBody/ChatReply
├── app.py             # create_app(...) + the 4 endpoints + traces
├── .env.example
└── README.md
```

## See also

- `docs/en/quickstart.md` §0 — the `quick` facade these endpoints wrap.
- `examples/devops`, `examples/knowledge` — fuller apps on the same chat/trace
  contract, with HITL and richer agents.
- `reactifact.quick` — `.agent`/`.context` on every object is the graduation
  path from this app to hand-written produces.
