# Migrating to reactifact 0.24.0

0.24 is additive except for one deliberate cleanup: **LLM helpers take an
`LLMProvider`, not a `Context`.**

## LLM helpers

Before:

```python
from reactifact.structured import structured_llm

body = await structured_llm(context, schema=Answer, user=text)
```

After — use the injected provider (the usual case inside a step):

```python
@step(consumes=Question)
async def answer(question: Question, llm: LLMProvider) -> Answer:
    return await llm.structured(Answer, user=question.text)
```

Or pass the run's provider explicitly when you only have a `Context`:

```python
body = await structured_llm(context.resources.llm, schema=Answer, user=text)
```

The same change applies to:

- `json_schema_llm` / `llm.structured_json(...)`;
- `llm_reply` / `llm.reply(...)`;
- `chat_complete` / `chat_complete_full` / `llm.chat(...)`;
- `StructuredLLM.call(llm, user)`.

`llm` may be `None`; the helpers keep their honest `None` /
`on_error("no_provider")` fallback. The old `context=` keyword is removed — pass
the provider directly (a clear `TypeError` is better than silently accepting a
`Context` in a provider slot).

## Why

`Context` in those helpers was only a provider locator. Passing it obscured the
actual dependency and mixed per-run state with transport. Provider methods call
`self.complete`, so tracing and budgets still apply.
