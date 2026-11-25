# Быстрый старт — три реальных приложения

[Пример на главной странице](index.md) доказывает идею (evidence → answer, без
проведённого графа между двумя агентами) в минимально возможном виде. Эта
страница — мост к тому, что вы реально будете строить: три коротких,
рабочих сниппета — tool-calling агент, поиск по своим документам и чат-бот
с сохраняемыми сессиями — каждый со ссылкой на полноценный пример, из
которого он урезан. Каждый сниппет на этой странице был реально запущен —
вывод, который вы видите, не выдуман.

## Установка и scaffold через uv

```bash
uv init my-agent && cd my-agent
uv add reactifact                 # core + CLI; FastAPI остаётся опциональным
uv run reactifact skills install  # научить кодового агента модели
```

Или начните с one-file шаблона агента (без web и API-ключа):

```bash
uvx reactifact new agent my-agent
cd my-agent && uv sync
uv run reactifact run main:pipeline "hello"
uv run reactifact trace
```

`run` пишет `.reactifact/traces.db`; `trace` рисует последний прогон.
`--no-trace` отключает запись.

Самый быстрый способ увидеть всё целиком — вычисленную цифру, её провенанс и
воспроизводимый хеш контекста, без API-ключа и без файлов — встроенное демо:

```bash
uv run reactifact demo            # ~3 секунды; --plain отключает цвет
uv run reactifact demo --web      # тот же ответ, provenance, contracts + traces
```

Это та же форма из трёх `@step`, которую вы напишете ниже; весь исходник —
[`reactifact/demo.py`](https://github.com/bzdvdn/reactifact/blob/master/reactifact/demo.py).
`--web` поднимает локальную страницу и открывает браузер; `--no-open` — если
не нужно открывать.

Для web-приложения либо добавьте extra сами, либо сгенерируйте шаблон:

```bash
uv add "reactifact[web]"          # + FastAPI/uvicorn
uv run reactifact new app my-app  # или: uvx reactifact new app my-app
cd my-app && uv sync --extra dev
cp .env.example .env
uv run reactifact dev
```

`reactifact dev` запускает приложение с reload и печатает три URL: приложение,
`/contracts` и `/traces` (если смонтированы). Можно передать
`app.main:create_app` явно или открыть браузер через `--open /contracts`.

`reactifact new app` копирует встроенный FastAPI-шаблон (auth, sessions, SSE,
traces, бюджеты/LLM из settings). Сгенерированный проект сам объявляет
`reactifact[web]`; core-пакет FastAPI не требует.

`reactifact new daemon` генерирует scheduled-агент вместо web: LLM/бюджеты из
settings, трейсы, graceful shutdown и `APP_RUN_ONCE=true` для одноразового
smoke-запуска.

Цикл доступен и как библиотека:

```python
from reactifact import Daemon

Daemon(
    pipeline,
    seed_source=next_tick,   # sync/async: индекс тика -> seed | None
    interval=60,
    traces=True,
).serve()
```

`reactifact daemon app.pipeline:pipeline --seed app.seeds:next_tick --interval 60`
запускает то же из CLI.

## Чат в несколько строк

`Chat` — типизированный фасад над `ChatAssistant`: seed и ответ выводятся из
моделей, сессии сохраняются, стриминг и history готовы.

```python
from reactifact import Chat

chat = Chat(
    steps=[search, answer],
    store="sessions.db",
    seed_type=Question,
    answer_type=Answer,
)

reply = await chat.invoke("почему checkout недоступен?", session_id="u1")
async for event in chat.stream("а теперь?", session_id="u1"):
    ...
```

`seed_factory(text, session_id, request)` добавляет метаданные приложения
(категорию, тенанта, пользователя); `per_turn(text, session_id, request)` —
async context manager для turn-scoped contextvars или очистки ресурсов.
`chat.router()` возвращает готовый FastAPI `APIRouter` (SSE + history), а
`chat.test_client()` — копию с in-memory store и traces.

Для HTTP-чата с сессиями `chat.http()` повторяет DEVOPS-стиль путей:

```python
from reactifact.web import ChatRoutes

app = chat.http(
    routes=ChatRoutes(
        invoke="/chat",          # POST /api/chat
        stream="/chat/stream",   # POST /api/chat/stream
        history="/chat/history", # GET  /api/chat/history
        clear="/chat/clear",     # POST /api/chat/clear
        clear_method="POST",
        history_messages=True,
    )
)
```

`/docs`, `/api/contracts` и `/api/traces` тоже монтируются; `reactifact dev`
поднимает приложение.

## Один объект: `Pipeline`

`Pipeline` — короткий путь, когда шаги уже есть: он держит шаги, ресурсы и
дефолты, а на каждый запуск создаёт свежие `Context` + `Runtime`.

```python
from reactifact import Pipeline

pipeline = Pipeline(steps=[compute_spend, compute_variance, answer])
result = pipeline.run(Question(text="какая дисперсия за Q2?"))

print(result.latest(Answer).data.text)
print(result.sources(result.latest(Answer)))
print(result.hash)
```

Async-варианты — `arun(seed)` и `astream(seed)`; низкоуровневый `Context`
остаётся в `result.context` для branch/merge, replay и тестов.

Шаги могут получать ресурсы по типу, а тесты — подменять их без пересборки
пайплайна:

```python
@step(consumes=Question)
def answer(question: Question, llm: LLMProvider) -> Answer: ...

pipeline = Pipeline(steps=[answer], llm=default_llm)
pipeline.override(LLMProvider, FakeLLM())
```

Для долгоживущих ресурсов (HTTP-клиенты, индексы) — lifespan, как в FastAPI:

```python
@asynccontextmanager
async def lifespan():
    yield build_resources()
    await close_resources()

async with Pipeline(steps=[answer], lifespan=lifespan) as pipeline:
    result = await pipeline.arun(Question(text="..."))
```

Тесты получают in-memory trace store и изолированные overrides:

```python
client = pipeline.test_client()
client.override(LLMProvider, FakeLLM())
result = client.run(Question(text="..."))
assert asyncio.run(client.traces.query())["total"] >= 1
client.close()
```

## Одна команда: `check`

`reactifact check` прогоняет цикл качества над `Pipeline`: статические
контракты, зарегистрированные сценарии и оценку на датасете; если хоть один
этап упал, код выхода ненулевой.

```bash
uv run reactifact check app.pipeline:pipeline \
  --scenarios app.scenarios \
  --evals evals/qa.json \
  --overall 0.8
```

Он печатает по строке на этап и финальный `check: PASSED`/`FAILED`; для
метрики используйте `--gate KEY=VALUE`. `reactifact dev` — серверная
пара: запускает приложение и показывает `/contracts` и `/traces`.

## HTTP в одну строку

Web-адаптер опционален; когда нужен HTTP, он получается из того же типизированного
пайплайна:

```python
pipeline = Pipeline(
    steps=[extract, classify, answer],
    seed_type=Question,
    answer_type=Answer,
    traces=True,
)

app = pipeline.http()
```

`reactifact dev main:app` поднимает `POST /run`, `POST /stream`, `/health`,
интерактивный `/docs` (OpenAPI из `Question`/`Answer`), `/contracts` и
`/traces`. Каждый ответ несёт `X-Request-ID`; `/run` добавляет
`X-Reactifact-Run-Id`, а доменные ошибки используют
`{"error": {"code", "message", "request_id", "trace_id"}}`. Для встраивания в
существующее приложение: `app.include_router(pipeline.router(prefix="/agent"))`.

## 0. Он-рамп для 80%: `reactifact.quick`

Большинство первых задач — одна из четырёх. `reactifact.quick` собирает каждую
в несколько строк, но под капотом всё ещё строит **настоящих реактивных
агентов**:

```python
from pydantic import BaseModel
from reactifact.quick import agent, rag, tools_agent, chat_agent


class AnswerBody(BaseModel):
    text: str


# 1) один структурированный вызов LLM
qa = agent(system="Answer in one sentence.", schema=AnswerBody)
body = await qa.ask("What are the three states of water?")   # AnswerBody | None

# 2) поиск по своим файлам, с провенансом
r = rag({"docs": "./docs", "costs": "./costs.csv"})
answer = await r.ask("what's the total gpu cost?")           # answer.text, answer.sources

# 3) LLM с тулами (human=True → задаёт уточняющие вопросы)
t = tools_agent("You are ops. Use the tools.", [check_status])  # @tool (§1)
text = await t.ask("is checkout-api healthy?")

# 4) чат-ассистент с сессиями (provider через llm=; store=None → в памяти)
assistant = chat_agent(groups=[...], llm=provider)
```

Каждая точка входа принимает `llm=` — любой `LLMProvider` (`from_env()`,
`openai_llm(...)`, `openrouter_llm(...)`, …). По умолчанию `from_env()`, так что
настроенный `.env` просто работает; передайте явно, чтобы выбрать модель/эндпоинт.

Это **сахар над теми же примитивами**, а не второй фреймворк. У каждого объекта
есть настоящий `StepGroup` и `Context` последнего запуска:

```python
qa.agent      # реактивный Agent — можно смонтировать в свой Runtime
qa.context    # артефакты/провенанс последнего ask()
```

`rag(...)` связывает каждый `Answer` по `supported_by` с использованными `Doc`,
а каждый `Doc` — по `materialized_from` со своим `SourceRef` (тот же провенанс,
что и в ручном пайплайне ниже). Когда сахара перестаёт хватать — спускайтесь к
`Consume`/`Step`/`Effects`, переписывать нечего. Полная модель — в
[Patterns](patterns.md).

Удобнее ноутбук? [![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/bzdvdn/reactifact/blob/master/examples/quickstart.ipynb)
— [`examples/quickstart.ipynb`](https://github.com/bzdvdn/reactifact/blob/master/examples/quickstart.ipynb) проходит те же четыре кейса, по умолчанию офлайн.

Свои модели артефактов — фасад параметризован, а не захардкожен:

```python
r = rag(
    {"docs": "./docs"},
    doc_type=MyDoc,          # должен принимать text/locator/title…
    answer_type=MyAnswer,
    # …или соберите сами и скажите, как читать тело/метку документа:
    doc_factory=lambda ctx, ref, content: MyDoc(body=content, url=ref.data.locator),
    answer_factory=lambda text, docs: MyAnswer(answer=text, citations=[d.data.url for d in docs]),
    doc_text=lambda d: d.body,
    doc_locator=lambda d: d.url,
)
qa = agent("Answer briefly.", AnswerBody, question_type=MyQuestion)  # нужно поле `text`
```

Провенанс от формы моделей не зависит: ответ всё так же связывается
`supported_by` с использованными документами.

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
    runtime = Runtime(ctx, groups=[OpsAgent()])
    ctx.create(Ticket(text="is checkout-api healthy?"))
    await runtime.arun()

    from reactifact.tools.use import ToolAnswer
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

### Tools: инъекция, политики, типизированный результат

Параметр тула, аннотированный обычным классом, — это collaborator из
`RuntimeResources` (как параметр шага), он не попадает в JSON-схему для
модели. Верните pydantic-модель — она окажется в `ToolOutput.data`;
`retry=`/`timeout=` добавьте для временных сбоев:

```python
from pydantic import BaseModel
from reactifact import RetryPolicy, RuntimeResources, tool
from reactifact.providers import from_env


class Pod(BaseModel):
    name: str


class K8sClient:
    async def get(self, name: str) -> str: ...


@tool(retry=RetryPolicy(max_attempts=3, backoff=0.5), timeout=10)
async def get_pod(name: str, k8s: K8sClient) -> Pod:
    """Прочитать под."""
    return Pod(name=await k8s.get(name))


resources = RuntimeResources(llm=from_env())
resources.register(K8sClient, K8sClient())
```

`execute_tool(tool, args, resources=...)` инжектит вне шага (тесты, веб-джоб);
внутри шага ресурсы берутся из текущего `Context`. `ToolOutput.error_info`
(`ToolError(retryable=True)`) ретраится той же политикой; неретраибельная
ошибка возвращается модели как данные.

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

from reactifact import Context, Runtime, RuntimeResources, create_step_group, step
from reactifact.recipes import fan_out_sources, materialize_doc, find, keyword_score
from reactifact.sources import FileSystemSource, SourceRef


class Question(BaseModel):
    text: str


class Doc(BaseModel):
    text: str


class Answer(BaseModel):
    text: str
    sources: list[str] = []


@step(produces=SourceRef, consumes=Question)
async def search(call):
    question = call.trigger
    await fan_out_sources(call.context, question.data.text, owner_id=question.id, limit=3)


@step(produces=Doc, consumes=SourceRef)
async def resolve(call):
    ref = call.trigger
    await materialize_doc(call.context, ref, lambda ctx, ref, content: Doc(text=content))


@step(produces=Answer, consumes=Doc)
async def answer(call):
    docs = call.inputs
    d = docs[0]
    sources = call.context.related(d.id, "materialized_from")
    return Answer(text=d.data.text.strip(), sources=[s.data.locator for s in sources])


search_group = create_step_group("search", steps=[search])
resolve_group = create_step_group("resolve", steps=[resolve])
answer_group = create_step_group("answer", steps=[answer])


async def main():
    resources = RuntimeResources(
        # ключ словаря и source_id= должны совпадать — именно так SourceRef
        # находит обратный путь к источнику, который его создал (resources.get_source)
        sources={"docs": FileSystemSource("./docs", source_id="docs", scorer=keyword_score)},
    )
    ctx = Context(resources=resources)
    runtime = Runtime(ctx, groups=[search_group, resolve_group, answer_group])
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

`Chat` сам владеет сессиями, циклом хода и восстановлением истории — ваше
приложение поставляет только доменные модели и reply. Тот же объект работает
и как обычный async-вызов (`invoke`), и, будучи смонтированным через
`reactifact.web.create_chat_router`, как SSE-эндпоинт на вашем FastAPI:

```python
import asyncio
from pydantic import BaseModel

from reactifact import Chat, RuntimeResources, SessionStore, create_step_group
from reactifact.checkpoints import FileKVBackend
from reactifact import step


class UserMsg(BaseModel):
    text: str
    session_id: str = ""


class Reply(BaseModel):
    query_id: str
    text: str


@step(produces=Reply, consumes=UserMsg)
async def echo(call):
    msg = call.trigger
    return Reply(query_id=msg.id, text=f"you said: {msg.data.text}")


echo_group = create_step_group("echo", steps=[echo])


def reply(ctx, msg_id):
    latest = ctx.latest(Reply)
    return {"reply": latest.data.text if latest else ""}


async def main():
    store = SessionStore(FileKVBackend("./sessions"))
    assistant = Chat(
        store=store,
        groups=[echo_group],
        seed_type=UserMsg,
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
сниппете, и по той же причине: `Chat` сам закрывает построенный
через callable `RuntimeResources` после каждого хода, так что HTTP-клиент
настоящего провайдера никогда не течёт.

## Production-shape: FastAPI-шаблон

Сниппеты выше держат цикл маленьким. Для настоящего web-приложения скопируйте
[`templates/fastapi_chat`](../../reactifact/templates/fastapi_chat): FastAPI `Depends` auth,
scoped-сессии, SSE, трейсы, бюджеты/LLM из settings, тесты и Docker. Шаблон сам
зависит от `reactifact[web]` — core-пакет по-прежнему не тянет FastAPI.

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
- [Examples](examples.md) — все тринадцать, с тем, чему конкретно учит каждый.
- [Port matrix](port-matrix.md) — если вы знаете LangGraph/CrewAI, какой
  пример соответствует какому знакомому паттерну.
- [Agent skills](skills.md) — официальные Agent Skills, ставятся командой
  `python -m reactifact skills install`, чтобы кодовый агент (Claude Code,
  Cursor, Codex) умел строить *с* reactifact.
