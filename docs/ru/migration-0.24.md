# Переход на reactifact 0.24.0

0.24 в основном аддитивна, кроме одного намеренного упрощения: **LLM-хелперы
принимают `LLMProvider`, а не `Context`.**

## LLM-хелперы

Было:

```python
from reactifact.structured import structured_llm

body = await structured_llm(context, schema=Answer, user=text)
```

Стало — используйте инжектированный провайдер (обычный случай в шаге):

```python
@step(consumes=Question)
async def answer(question: Question, llm: LLMProvider) -> Answer:
    return await llm.structured(Answer, user=question.text)
```

Или передайте провайдера явно, если есть только `Context`:

```python
body = await structured_llm(context.resources.llm, schema=Answer, user=text)
```

То же касается:

- `json_schema_llm` / `llm.structured_json(...)`;
- `llm_reply` / `llm.reply(...)`;
- `chat_complete` / `chat_complete_full` / `llm.chat(...)`;
- `StructuredLLM.call(llm, user)`.

`llm` может быть `None`; хелперы сохраняют честный `None` /
`on_error("no_provider")`. Старый keyword `context=` удалён — передавайте
провайдера напрямую.

## Почему

`Context` в этих хелперах был только locator'ом провайдера. Он скрывал реальную
зависимость и смешивал per-run состояние с транспортом. Методы провайдера
вызывают `self.complete`, поэтому tracing и бюджеты продолжают работать.
