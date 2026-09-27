# Providers

Providers are *capabilities* — LLMs, embedders, image/speech/video models.
They live in `reactifact.providers`, never in the core package. The app wires the
ones it needs into `RuntimeResources`; the core communicates with them only
through narrow contracts (`LLMProvider`, `EmbeddingProvider`, …).

## Contracts

The core itself knows four contracts:

| Contract | Role |
| --- | --- |
| `LLMProvider` | text generation (`generate`, `stream`) |
| `EmbeddingProvider` | vector embeddings |
| `ImageProvider` | image generation |
| `SpeechProvider` / `TranscriberProvider` | TTS / STT |
| `VideoProvider` | text → video generation |

`LLMRequest` / `LLMResponse` / `LLMResponseChunk` and `Message` are the wire
types. `FakeLLM` and `FakeEmbedder` ship in `providers.fake` — deterministic
stand-ins so demos and tests run with no API keys.

## The env-based switchers

The simplest wiring is `*_from_env` factory functions that read `.env`:

```python
from reactifact.providers import (
    embedder_from_env,
    image_from_env,
    llm_from_env,
    openrouter_llm,
)

resources = RuntimeResources(llm=llm_from_env() or openrouter_llm())
emb = embedder_from_env()
if emb is not None:
    resources.embedder = emb
img = image_from_env()
if img is not None:
    resources.set("images", img)
```

Pattern: **`llm_from_env() or <vendor default>`**. If no key is set the factory
returns `None`, and the demo falls back to deterministic behavior (this is why
every example keeps working with no API key at all).

`from_env(**overrides)` bundles the exact two-branch version of that pattern
every example's local `build_llm()` hand-rolls (`OPENROUTER_API_KEY` first,
else `OPENAI_BASE_URL`, else `None`), for apps that want the common default
without copying that block:

```python
from reactifact.providers import from_env

llm = from_env(max_tokens=2048)  # openrouter_llm(...) or llm_from_env(...) or None
```

The in-repo examples prefer the **explicit factories** (`openai_llm(...)`,
`openrouter_llm(...)`) with an explicit `max_tokens` — each demo defines a small
`build_llm()` that reads `.env` and picks the provider, so you always see which
provider runs and it stays offline-capable when no key is present.

## Chat providers

`OpenAICompatProvider` is the generic OpenAI-compatible client and is the
backing for the vendor factories. It supports:

- `api_base` / custom `proxy` (base URL) and `auth_scheme`/`auth_header`.
- `_network_knobs` — proxy/auth header/auth scheme resolved from
  `<PREFIX>_PROXY`/`<PREFIX>_AUTH_HEADER`/`<PREFIX>_AUTH_SCHEME` or explicit
  overrides.
- `retry_attempts` (default `3`) — 429/5xx and transport errors (connection
  reset, timeout) retry with exponential backoff; 4xx never retries. Applies
  to `complete()` only, not `stream()` (a stream that fails mid-way can't be
  retried without duplicating already-yielded chunks) — see
  [Reliability](#reliability) below.
- `stream` for token-wise `LLMResponseChunk`s.

11 of the vendor `*_llm` factories below (Cerebras, DeepSeek, Fireworks,
GitHub Models, Groq, NVIDIA NIM, Perplexity, Qwen, Together, xAI, z.ai) are
generated from one `_openai_compat_llm(env_prefix=..., default_model=...,
default_base_url=...)` factory-builder — each vendor module is a 3-line
config, not a hand-copied function.

| Factory | Vendor |
| --- | --- |
| `openai_llm` / `openai_embedder` | OpenAI |
| `anthropic_llm` | Anthropic |
| `mistral_llm` / `mistral_embedder` | Mistral |
| `deepseek_llm` | DeepSeek |
| `openrouter_llm` / `openrouter_embedder` / `openrouter_speech` | OpenRouter (multi-vendor: chat, images, video, embeddings, TTS — not STT, see below) |
| `groq_llm` / `groq_transcriber` | Groq (fast inference; Whisper STT) |
| `xai_llm` | xAI (`grok`) |
| `together_llm` / `together_embedder` | Together AI |
| `fireworks_llm` / `fireworks_embedder` | Fireworks |
| `azure_llm` | Azure OpenAI |
| `cerebras_llm` | Cerebras (fast inference) |
| `github_models_llm` | GitHub Models |
| `qwen_llm` / `qwen_embedder` | Qwen/百炼 (DashScope) |
| `nvidia_nim_llm` / `nvidia_embedder` | NVIDIA NIM |
| `perplexity_llm` | Perplexity |
| `zai_llm` | Z.ai |
| `ollama_llm` | local Ollama |
| `gemini_llm` | Google Gemini (`GeminiProvider`) |

Every embedder/speech/transcriber factory above is verified against that
vendor's own docs, not assumed from "OpenAI-compatible chat" — some
capabilities that look like they should exist don't, in an API-compatible
shape. One concrete example: OpenRouter's `/audio/transcriptions` (STT) takes
a base64-JSON body, not the multipart file upload `OpenAICompatTranscriber`
(and every other transcriber here) expects — a real API difference, so there
is deliberately no `openrouter_transcriber`.

## Image, speech, video

| Capability | Providers |
| --- | --- |
| Image | `OpenAICompatImageProvider` (any image API), `OpenRouterImageProvider`, `GeminiImageProvider`, `gemini_image`, `image_from_env` |
| Embeddings | `OpenAICompatEmbedder`, `openai_embedder`, `mistral_embedder`, `openrouter_embedder`, `together_embedder`, `fireworks_embedder`, `qwen_embedder`, `nvidia_embedder`, `embedder_from_env` |
| Speech (TTS) | `OpenAICompatSpeech`, `openrouter_speech`, `speech_from_env` |
| Speech (STT) | `OpenAICompatTranscriber`, `groq_transcriber`, `transcriber_from_env` |
| Video | `SoraVideoProvider`, `RunwayVideoProvider`, `LumaVideoProvider`, `OpenRouterVideoProvider`, `video_from_env` — `generate()`/`fetch()`/`download()` all retry (same policy as chat); `poll()` itself tolerates a `fetch()` that still fails after its own retry and keeps polling until `timeout`, rather than abandoning a multi-minute job over one bad network moment |

Each `*_from_env` accepts `**overrides` so you can inject config in tests without
setting environment variables.

## .env reference (examples)

Every demo ships an `.env.example`; a typical one for a chat model looks like:

```dotenv
# model selection (the *_from_env factories read these)
LLM_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-...
OPENROUTER_MODEL=deepseek/deepseek-chat

# optional
EMBEDDING_PROVIDER=mistral
MISTRAL_API_KEY=...
IMAGE_PROVIDER=openrouter
IMAGE_MODEL=google/gemini-2.0-flash-exp:free
```

## Reliability

**Retries.** Every provider (`OpenAICompatProvider`/`Embedder`, `AnthropicProvider`,
`GeminiProvider`, `OpenAICompatImageProvider`, `OpenAICompatSpeech`/
`Transcriber`, all four video providers) takes `retry_attempts` (default `3`)
and retries 429/5xx and transport errors with exponential backoff via
`reactifact.providers._retry.with_retry` — 4xx (auth/bad request) never retries,
since retrying a misconfiguration just delays the real error. Pass
`retry_attempts=1` to disable.

**Failover.** `RouterLLM([primary, secondary, …])` calls providers in order and
falls back on failure — a provider outage, a rate limit, or a missing key while
another provider works. Any exception falls back by default; `should_fallback=`
narrows it (e.g. `retryable_only` skips a 4xx, which would repeat everywhere).
Only `complete()` fails over — `stream()` uses the first provider. An
`on_fallback(provider, exc)` callback fires before moving on.

**Exact cache.** `CachingLLM(provider)` keys every `complete()` by the whole
request (model, messages, temperature, …) and answers a repeat without calling
the provider — no tokens, no latency; `cache=KVCache(FileKVBackend(…))` makes it
survive restarts. A hit is marked (`raw={"reactifact_cache": "hit"}`) and
`BudgetLLM` does **not** charge it. Deliberately not semantic (an approximate
match would answer a different question), and `stream()` is not cached.

```python
from reactifact.cache import CachingLLM, KVCache
from reactifact.checkpoints import SQLiteKVBackend
from reactifact.routing import RouterLLM, retryable_only

resources.llm = CachingLLM(                     # cache outermost
    RouterLLM([openrouter_llm(), groq_llm()],   # failover in the middle
              should_fallback=retryable_only),
    cache=KVCache(SQLiteKVBackend("llm-cache.db")),
)
```

**HTTP client lifecycle.** Every provider opens an `httpx.AsyncClient` lazily
and owns it — nothing in the runtime closes it for you. Call
`await resources.aclose()` (`RuntimeResources`) yourself at real shutdown (a
FastAPI `lifespan`, or the end of a script):

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await resources.aclose()

app = FastAPI(lifespan=lifespan)
```

The one exception is `ChatAssistant` with a **callable** `resources=` (e.g.
`resources=lambda: build_resources()`): a fresh `RuntimeResources` — and
typically a fresh provider + HTTP client — is built on every turn, so
`stream()` closes it after that turn itself; nothing else will ever
reference that instance again. A plain (non-callable) `resources=` instance
is left alone — it must outlive the turn.

## Temperature & max_tokens — provider defaults, per-call overrides

`temperature` and `max_tokens` are **provider-level defaults**, overridable per
call. A `None` at both levels means the field is omitted from the request and
the API applies its own default.

```python
from reactifact.providers import openai_llm, openrouter_llm

# provider default for every request made through it
llm = openai_llm(model="gpt-4o-mini", temperature=0.7, max_tokens=2048)
```

Per-call overrides win (e.g. a deterministic extractor that must not wander):

```python
body = await structured_llm(context, schema=AnswerBody, user=text, temperature=0.0)
```

Resolution order: **call → provider → omit the field**. The same applies to
`llm_reply`, `ToolUse`, `LLMAgent`/`HITLLMAgent` (`temperature`/`max_tokens`
class attributes, `None` = provider default), and to the image provider
(`n`/`size`/`quality` defaults in the constructor, per-call override via
`generate(prompt, size=...)`). Anthropic always sends `max_tokens` (the API
requires it) — default `4096`, change it in the constructor.

## Deterministic demo mode

When no LLM is configured (`llm_from_env()` returns `None` and no fallback is
set), demos switch to deterministic behavior: canned options, fallback plans,
rule-based summarizing (§68). The rule is strict: a configured model that
*returns nothing usable* is reported honestly (never silently substituted with
stubs); the deterministic fallback is only for "no model at all" mode.