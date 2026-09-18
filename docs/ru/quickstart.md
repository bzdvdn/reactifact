# Быстрый старт — три реальных приложения

[Пример на главной странице](index.md) доказывает идею (evidence → answer, без
проведённого графа между двумя агентами) в минимально возможном виде. Эта
страница — мост к тому, что вы реально будете строить: три коротких,
рабочих сниппета — tool-calling агент, поиск по своим документам и чат-бот
с сохраняемыми сессиями — каждый со ссылкой на полноценный пример, из
которого он урезан. Каждый сниппет на этой странице был реально запущен —
вывод, который вы видите, не выдуман.

## 1. Tool-calling агент (с human-in-the-loop)

`HITLLMAgent` сам собирает LLM + список `Tool` в реактивный цикл
ask/tool/answer — модель на каждом шаге решает: вызвать инструмент,
задать пользователю уточняющий вопрос или ответить (§60). Никакой ручной
ветки «если не хватает — спросить» в вашем коде нет:

```python
import asyncio
from pydantic import BaseModel

from reactifact import Consume, Context, Runtime, RuntimeResources
from reactifact.llm_agent import HITLLMAgent
from reactifact.providers import from_env
from reactifact.tools import tool


class Ticket(BaseModel):
    text: str


@tool
async def check_status(service: str) -> str:
    """Look up the current status of a service."""
    return f"{service}: healthy, 3 replicas, 12ms p50 latency"


class OpsAgent(HITLLMAgent):
    name = "ops"
    system = "You are an ops assistant. Use check_status to answer questions about services."
    tools = [check_status]
    consumes = [Consume(Ticket)]


async def main():
    ctx = Context(resources=RuntimeResources(llm=from_env()))
    runtime = Runtime(ctx, agents=[OpsAgent()])
    ctx.create(Ticket(text="is checkout-api healthy?"))
    await runtime.arun()

    from reactifact.tool_use import ToolAnswer
    answer = ctx.latest(ToolAnswer)
    print("answer:", answer.data.text if answer else None)


asyncio.run(main())
```

Без ключа API (`from_env()` вернёт `None`) агент честно ответит
`"Could not reach a decision."` вместо угадывания (§59) — это не баг, а тот же
контракт честного фолбэка, которому следует любой генеративный шаг в
reactifact. С ключом в `.env` модель реально решит вызвать `check_status` и
ответит по его результату. Чтобы увидеть весь цикл детерминированно и
офлайн (без ключа, без угадывания), подставьте в `resources.llm` заскриптованный
`LLMProvider` — так же, как это делает `ScriptedLLM` в `tests/test_tools.py`,
или используйте `reactifact.testing.ScenarioLab`, если пишете это как тест.

**Полная версия**: `examples/devops` — три специалиста (k8s/GitLab/Ansible)
за одним роутером, трейс-дашборд, веб-UI. Запуск:
`.venv/bin/python examples/devops/chat.py`.

## 2. Поиск по своим документам

Источники — это capability, а не зашитый RAG-пайплайн: здесь
`FileSystemSource`, но `CSVSource`/`EmbeddingSource`/`WebSource` подключаются
через тот же самый вызов `fan_out_sources` (§8). Provenance — полноценный
эффект (`materialize_doc` связывает выведенный `Doc` обратно с его
`SourceRef`), а не строка цитаты, собранная руками:

```python
import asyncio
from pydantic import BaseModel

from reactifact import Consume, Context, Runtime, RuntimeResources, create_agent
from reactifact.produce import produce
from reactifact.recipes import fan_out_sources, materialize_doc, find, keyword_score
from reactifact.sources import FileSystemSource, SourceRef


class Question(BaseModel):
    text: str


class Doc(BaseModel):
    text: str


class Answer(BaseModel):
    text: str
    sources: list[str] = []


@produce(SourceRef)
async def search(call):
    question = find(call.inputs, Question)
    if question is None:
        return None
    await fan_out_sources(call.context, question.data.text, owner_id=question.id, limit=3)


@produce(Doc)
async def resolve(call):
    ref = find(call.inputs, SourceRef)
    if ref is None:
        return None
    await materialize_doc(call.context, ref, lambda ctx, ref, content: Doc(text=content))


@produce(Answer)
async def answer(call):
    docs = [a for a in call.inputs if isinstance(a.data, Doc)]
    if not docs:
        return None
    d = docs[0]
    sources = call.context.related(d.id, "materialized_from")
    return Answer(text=d.data.text.strip(), sources=[s.data.locator for s in sources])


search_agent = create_agent("search", consumes=[Consume(Question)], produces=[search])
resolve_agent = create_agent("resolve", consumes=[Consume(SourceRef)], produces=[resolve])
answer_agent = create_agent("answer", consumes=[Consume(Doc)], produces=[answer])


async def main():
    resources = RuntimeResources(
        # ключ словаря и source_id= должны совпадать — именно так SourceRef
        # находит обратный путь к источнику, который его создал (resources.get_source)
        sources={"docs": FileSystemSource("./docs", source_id="docs", scorer=keyword_score)},
    )
    ctx = Context(resources=resources)
    runtime = Runtime(ctx, agents=[search_agent, resolve_agent, answer_agent])
    ctx.create(Question(text="what is your refunds policy?"))
    await runtime.arun()
    a = ctx.latest(Answer)
    print("answer:", a.data.text if a else None)
    print("sources:", a.data.sources if a else None)


asyncio.run(main())
```

Две ловушки, в которые легко попасть и которых этот сниппет уже избегает:
передавайте `scorer=keyword_score` — у дефолтного скорера нет фильтрации
стоп-слов, поэтому запрос вроде "what **is** your refund**s** policy?" может
поднять не тот документ просто из-за слова "is"; и давайте `FileSystemSource`
`source_id=`, совпадающий с ключом словаря, под которым вы регистрируете его в
`resources.sources` — это единственное, что позволяет `materialize_doc`
позже найти обратный путь от `SourceRef` к источнику, который его создал.

**Полная версия**: `examples/knowledge` — файловые и CSV-источники,
извлечение evidence, верификация claim'ов, детерминированные вычисления,
keyword-триггерный skill. Запуск: `.venv/bin/python examples/knowledge/chat.py`.

## 3. Чат-бот с сохраняемыми сессиями

`ChatAssistant` сам владеет сессиями, циклом хода и восстановлением
истории — ваше приложение поставляет только доменные хуки. Тот же объект
работает и как обычный async-вызов (`invoke`), и, будучи смонтированным через
`reactifact.web.create_chat_router`, как SSE-эндпоинт на вашем FastAPI:

```python
import asyncio
from pydantic import BaseModel

from reactifact import Consume, RuntimeResources, SessionStore, create_agent
from reactifact.checkpoints import FileKVBackend
from reactifact.chat import ChatAssistant
from reactifact.produce import produce


class UserMsg(BaseModel):
    text: str
    session_id: str = ""


class Reply(BaseModel):
    query_id: str
    text: str


@produce(Reply)
async def echo(call):
    msg = call.trigger
    if msg is None:
        return None
    return Reply(query_id=msg.id, text=f"you said: {msg.data.text}")


echo_agent = create_agent("echo", consumes=[Consume(UserMsg)], produces=[echo])


def reply(ctx, msg_id):
    latest = ctx.latest(Reply)
    return {"reply": latest.data.text if latest else ""}


async def main():
    store = SessionStore(FileKVBackend("./sessions"))
    assistant = ChatAssistant(
        store=store,
        agents=[echo_agent],
        user_message=UserMsg,
        reply=reply,
        resources=RuntimeResources,  # свежий RuntimeResources на каждый ход
    )
    result = await assistant.invoke("hello there", session_id="demo")
    print(result)                                    # {'reply': 'you said: hello there'}
    print(await assistant.history(session_id="demo"))  # оба хода, восстановленные


asyncio.run(main())
```

Перезапустите процесс и снова вызовите `assistant.invoke(..., session_id="demo")`
— разговор продолжится ровно с того места, где остановился, потому что
`FileKVBackend` сохранил всю цепочку коммитов, а не только последний ответ.
Чтобы отдавать это по HTTP вместо прямого вызова `invoke`:

```python
from fastapi import FastAPI
from reactifact.web import create_chat_router

app = FastAPI()
app.include_router(create_chat_router(assistant))
# POST /api/chat/stream, GET/DELETE /api/runs/{session_id} — см. docs/en/api.md
```

**Полная версия**: `web.py` у `examples/devops` или `examples/knowledge` —
настоящие агенты за теми же двумя вызовами, плюс трейс-дашборд. `resources=`
там — вызываемый объект (`lambda: build_resources()`), точно как в этом
сниппете, и по той же причине: `ChatAssistant` сам закрывает построенный
через callable `RuntimeResources` после каждого хода, так что HTTP-клиент
настоящего провайдера никогда не течёт.

## Куда дальше отсюда

Каждый сниппет выше — минимально возможная *реактивная* версия своего
паттерна: ни один шаг пайплайна не вызывает следующий напрямую, каждый шаг
только объявляет, что он `consumes`/`produces`, а рантайм сам выводит
исполнение из изменений состояния. Это то же самое правило, на котором
работают полные примеры — просто с большим числом агентов, источников,
путей отказа. Дальше:

- [Patterns](patterns.md) — больше форм (reflection, map-reduce, supervisor, …).
- [Recipes](recipes.md) — строительные блоки, использованные на этой странице
  (`fan_out_sources`, `materialize_doc`, `find`), и те, что не использованы
  (`StatusMachine`, `WindowSummarizer`, `Skill`).
- [Examples](examples.md) — все четырнадцать, с тем, чему конкретно учит каждый.
- [Port matrix](port-matrix.md) — если вы знаете LangGraph/CrewAI, какой
  пример соответствует какому знакомому паттерну.
