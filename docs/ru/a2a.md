# A2A (Agent2Agent)

[Agent2Agent](https://a2a-protocol.org) — открытый протокол меж-агентной
интероперабельности: **Agent Card** для обнаружения, **Task** с жизненным
циклом и **Message** с частями text/file/data поверх JSON-RPC + SSE.
`reactifact.a2a` говорит на нём в обе стороны — **вызвать** удалённого агента
или **выставить** своего, — без дополнительной зависимости (клиент использует
`httpx`, сервер — FastAPI из уже имеющегося `web`-extra, импортируется лениво).

Реализована JSON-RPC-привязка в форме A2A `0.3` (camelCase-поля, `kind`-
дискриминированные части).

## Вызвать удалённого агента

`A2AClient` — сам протокол; `A2AAgentTool` оборачивает его в reactifact-`Tool`,
поэтому `LLMAgent` может делегировать удалённому агенту как обычному
инструменту:

```python
from reactifact.a2a import A2AClient, A2AAgentTool

client = A2AClient("https://agent.example.com/a2a", headers={"x-api-key": "…"})
card = await client.fetch_agent_card()          # AgentCard
task = await client.send("Summarise the incident")
print(task.status.state, task.artifacts[0].parts[0].text)

researcher = A2AAgentTool(client, name="researcher")
# agent = create_agent("lead", consumes=[Consume(Question)], produces=[...researcher...])
```

Ещё у `A2AClient` есть `stream(message)` (асинхронный итератор событий
task/message по SSE), `get_task(id)` и `cancel_task(id)`.

## Запустить удалённого агента как узел

`A2AAgentTool` — для LLM-цикла. Чтобы удалённого агента **планировал рантайм**
рядом с локальными (аналог `A2ARemoteGraph` в LangGraph), используйте
`remote_agent(...)`: полноценный узел, который потребляет локальный артефакт,
зовёт `message/send` и производит локальный артефакт — так что провенанс,
бюджет, guardrails и eval применяются как обычно:

```python
from reactifact import Consume, Runtime
from reactifact.a2a import remote_agent

translator = remote_agent(
    "translator",
    "https://agent.example.com/a2a",
    consumes=[Consume(Question)],
    output_type=Answer,          # строится как Answer(text=reply)
)
Runtime(ctx, agents=[translator, …]).arun()
```

Удалённый таск в состоянии `input-required` становится локальным
`PendingQuestion`; последующий `context.resume(...)` продолжает *тот же*
удалённый таск и кладёт финальный артефакт. Передайте `message_of=` (вход → текст)
и `build_output=` (ответ, вход → артефакт), если у типов нет поля `text`. Ниже
уровнем лежит `A2ARemoteProduce` — тот самый `Produce`.

## Выставить reactifact-агентов

`create_a2a_router(agents, ...)` возвращает FastAPI-роутер: Agent Card и
JSON-RPC-эндпоинт, где **Task — это одна беседа**:

```python
from fastapi import FastAPI
from reactifact.a2a import create_a2a_router

app = FastAPI()
app.include_router(create_a2a_router([RouteAgent(), …], name="ops-agent"))
```

Отдаёт `GET /.well-known/agent-card.json` (и устаревший `/.well-known/agent.json`)
и `POST /` с `message/send`, `message/stream`, `tasks/get`, `tasks/cancel`.

| A2A | reactifact |
| --- | --- |
| Agent Card (`skills`) | строится из `produces` каждого агента |
| Task | одна беседа над `Context` |
| `message/send` | сид через `create_message(ctx, text)`, прогон агентов до фикспоинта, ответ через `reply(ctx, seed_id)` |
| `message/stream` (SSE) | рабочий Task, status-update на каждый `context.announce()`, затем финальный Task |
| `input-required` | прогон встал на `PendingQuestion`; следующий `message/send` с тем же `taskId` возобновляет его |
| Artifact | текст ответа как `Part` |

`create_message` и `reply` — доменные хуки (ровно как в `reactifact.chat`): без
них сервер сидит `UserMessage` и отвечает последним артефактом с полем `text`.
Передайте свои, чтобы использовать реальные типы вход/выход приложения:

```python
def create_message(ctx, text):
    return ctx.create(Question(text=text)).id

def reply(ctx, seed_id):
    answer = ctx.latest(Answer)
    return answer.data.text if answer else ""

app.include_router(create_a2a_router(AGENTS, create_message=create_message, reply=reply))
```

## Интероп

Сервер проверен против **официального клиента `a2a-sdk`** (тест из `a2a`-группы
зависимостей, не рантайм): эталонный клиент резолвит нашу Agent Card и зовёт
`message/send`, получая Task со статусом `completed` и артефактом-ответом — см.
`tests/test_a2a_interop.py`. `a2a-sdk` — только тест-зависимость (своя группа
`a2a`); рантайм остаётся на `httpx` + FastAPI и ни на чём больше.

## Область

Реализовано: Agent Card, `message/send`, `message/stream` (SSE), `tasks/get`,
`tasks/cancel`, маппинг `input-required` ↔ `PendingQuestion` и JSON-RPC-привязка
`0.3`. Вне области: конфиг push-уведомлений, `tasks/resubscribe`, аутентифицированная
extended-карта, привязки gRPC и HTTP+JSON (REST), а также `securitySchemes`
(аутентификацию ставьте перед роутером). Задачи хранятся **в процессе** (как в
`reactifact.chat`) — для устойчивости сохраняйте `Context` через `Session`.
