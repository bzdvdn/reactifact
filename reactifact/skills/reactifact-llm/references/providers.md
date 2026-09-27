# Providers reference

An `LLMProvider` is a small interface — `async complete(request) -> response`
and `stream(request)`. All providers live in `reactifact.providers`; the core
depends only on the contract, so no transport is imported until you build one.

## Selecting a provider

```python
# not-run: illustrative
from reactifact.providers import from_env, openai_llm, anthropic_llm

llm = from_env()                      # OPENROUTER_API_KEY first, else a local one
llm = openai_llm(model="gpt-4o-mini")  # explicit
llm = anthropic_llm(model="claude-sonnet-4-6")
```

Factory families (same shape, `model=`/key kwargs):

| Kind | Factories |
| --- | --- |
| Hosted | `openai_llm`, `anthropic_llm`, `gemini_llm`, `openrouter_llm`, `azure_llm`, `groq_llm`, `mistral_llm`, `deepseek_llm`, `xai_llm`, `cerebras_llm`, `github_models_llm`, `nvidia_nim_llm`, `qwen_llm`, `perplexity_llm`, `fireworks_llm`, `together_llm`, `zai_llm` |
| Local | `ollama_llm` |
| Embeddings | `openai_embedder`, `mistral_embedder`, `qwen_embedder`, `nvidia_embedder`, `fireworks_embedder`, `together_embedder` |

`RuntimeResources(llm=..., embedder=...)` wires them; `embedder` is what
`EmbeddingSource` uses for RAG (see the `reactifact-rag` skill).

## Fakes for tests

```python
# not-run: illustrative
from reactifact import FakeLLM, FakeEmbedder

ctx = Context(resources=RuntimeResources(llm=FakeLLM('{"label": "ok"}'),
                                          embedder=FakeEmbedder(dim=8)))
```

`FakeLLM` returns a fixed string (deterministic); `FakeEmbedder` hashes text
into a fixed-dim vector. With these, an LLM-in-the-loop pipeline runs with no
network.

## Wrappers (compose like decorators)

| Wrapper | What it adds |
| --- | --- |
| `CachingLLM(inner, cache=...)` | exact-key response cache (`InMemoryCache`, or `KVCache(backend)` to persist). Not semantic. A hit is marked so `BudgetLLM` does not charge it. |
| `RouterLLM(inner, ..., fallback=...)` | provider failover on retryable errors (`retryable_only` predicate; `on_fallback` hook). |
| `ReplayLLM(recording, mode="record"|"replay", inner=...)` | record/replay calls to JSONL; replay never hits the network and raises `ReplayMiss` on divergence. |
| `BudgetLLM(tracker, inner)` | counts tokens/cost per turn and enforces the budget. |

```python
# not-run: illustrative
from reactifact.cache import CachingLLM, InMemoryCache
from reactifact.routing import RouterLLM

llm = RouterLLM(
    CachingLLM(openai_llm(model="gpt-4o-mini"), cache=InMemoryCache()),
    fallback=anthropic_llm(model="claude-sonnet-4-6"),
)
```

## Budget and pricing

`Budget(max_runs, max_iterations, max_seconds, max_tool_calls, max_tokens,
max_cost)`. Token/cost limits need a `RuntimeResources.pricer` (a `Pricer`); no
price table ships with the library, so pass your own. After a run,
`runtime.last_stats: RunStats` holds `runs`, `iterations`, `outcome`,
`duration`, `prompt_tokens`, `completion_tokens`, `cost`, `errors`.

## Messages and requests

`Message.system/user/assistant/tool(text)`, `LLMRequest(messages,
temperature, max_tokens, stop, response_format, headers, extra, prompt_hash)`,
`LLMResponse(text, raw, finish_reason, usage)`. Providers ignore `prompt_hash`;
the tracing `RecordingLLM` copies it onto the `LLMCall` so prompt drift is
visible in a trace.
