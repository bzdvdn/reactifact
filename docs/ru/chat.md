# Multi-turn и чат

Разговор — это просто состояние, живущее между ходами: приложение вносит
сообщение пользователя, агенты реагируют, ответ читается обратно, а весь
`Context` хранится под `session_id`, так что следующий ход — и следующий
процесс — его видит. Эта страница сводит воедино элементы multi-turn (и их
аналоги в LangGraph).

## Если вы знаете LangGraph

| LangGraph | reactifact |
| --- | --- |
| `thread_id` + checkpointer | `session_id` + `SessionStore` (`FileKVBackend` / `SQLiteKVBackend` / `PostgreSQLKVBackend`) |
| `MessagesState` + reducer `add_messages` | `Conversation` / `ConversationMessage` (`recipes.conversation`); состояние — типизированные артефакты, версионируемые на каждое изменение |
| `graph.invoke(input, config)` | `ChatAssistant.stream()/invoke()` или `run_message()` для своего цикла |
| `interrupt()` / `Command(resume=…)` | `effects.ask(...)` → `PendingQuestion` → `effects.resume(...)` |
| `get_state_history` / resume с checkpoint | `context.history()` / `context.checkout(v)` / `context.branch()` / `reactifact replay` |
| ограничить транскрипт / суммаризовать | `ChatMemory` → `WindowPruner` / `RollingDigestSummarizer`; `Context.compact` для истории |
| `store` (долговременная память между потоками) | не встроена — см. [Sources](sources.md) / `SessionStore` приложения |

Отличие в сути: reducer сливает непрозрачные сообщения, а reactifact хранит
**типизированные версионируемые артефакты** — поэтому провенанс хода,
возможность `diff` двух ходов и откат суть свойства самого состояния.

## Канонический чат

`ChatAssistant` владеет сессиями, циклом хода и историей; `create_chat_router`
монтирует его как SSE на вашем FastAPI-приложении. Настройка через хуки:

```python
from reactifact import Consume, RuntimeResources, create_agent
from reactifact.chat import ChatAssistant
from reactifact.checkpoints import SQLiteKVBackend
from reactifact.recipes.conversation import (
    ConversationMessage, conversation_state, create_turn,
)
from reactifact.session import SessionStore

assistant = ChatAssistant(
    store=SessionStore(SQLiteKVBackend("./sessions.db")),
    agents=[create_agent("answer", consumes=[Consume(ConversationMessage)], produces=[ANSWER])],
    user_message=ConversationMessage,
    create_message=create_turn,          # записать сообщение пользователя как ConversationMessage
    reply=my_reply,                      # (ctx, msg_id) -> {"reply": ...}
    session_state=conversation_state,    # как history() восстанавливает поток
    resources=lambda: RuntimeResources(llm=my_llm),
)

await assistant.invoke("what's the refund policy?", session_id="user-1")
await assistant.history(session_id="user-1")   # поток на данный момент
```

Готовый `quick.chat_agent(agents, store=…)` делает то же с дефолтами.

**Внешние side effects.** Если агент в чате записывает намерение через
`effects.act(...)`, передайте `dispatcher=` (и, при желании,
`on_dispatch_error=`) — они уходят прямо в `Runtime`, который ассистент собирает
на каждый ход, так что закоммиченные действия доставляются один раз, после
коммита генерации, ровно как в обычном прогоне:

```python
async def dispatch(context, action):
    await send_notification(action.data.payload)

ChatAssistant(..., dispatcher=dispatch)
```

Без него записанный `PendingAction` остаётся `pending` (runtime предупреждает),
пока что-нибудь его не дренирует — см. раздел про outbox в
[устойчивости](durability.md).

## Рецепт разговора

`ConversationMessage` — канонический артефакт `role`/`text`/`turn`/`session_id`,
так что вы перестаёте лепить вручную `Question`/`Answer` плюс сборщик промпта.
`Conversation` обобщён над **вашей** моделью сообщения — подойдёт любая с
`role`/`text`:

```python
from reactifact.recipes.conversation import Conversation, ConversationMessage

conv = Conversation()                 # дефолтная модель
conv = Conversation(MyMessage)        # или своя (role/text/доменные поля)

conv.create_turn(ctx, "refund policy?", session_id="s1")
# ...агенты выполняются и пишут сообщение ассистента...
history = conv.transcript(ctx, window=20)   # list[providers.Message] для промпта
```

`transcript` рендерит поток в провайдерские `Message` (от старых к новым,
`window` оставляет последние N); `conversation_state` — payload истории для
`session_state=`; номера `turn` берутся от максимального записанного, поэтому
pruning их не сбрасывает.

**У большинства реальных приложений *разные* типы на две стороны** (`Question`
+ `FinalResponse`, `UserMsg` + `ChatReply`) с ключом корреляции и нетекстовым
payload ассистента. Для них — read-only `Transcript` + `MessageSpec`, которые
сливают несколько типизированных ходов по ролям и не трогают артефакты (эту
форму используют примеры `repair`/`devops`/`knowledge`, каждый задавая свой
`Transcript([...])` как `session_state=`):

```python
from reactifact.recipes.conversation import MessageSpec, Transcript

view = Transcript([
    MessageSpec(Question, "user"),
    MessageSpec(FinalResponse, "assistant", render=render_answer),  # contract JSON → проза
])
history = view.messages(ctx, window=20)      # list[providers.Message]
# ChatAssistant(..., session_state=view.state)
```

## Ограниченная память

Разговор растёт вечно, если его не ограничить. `ChatMemory` на `ChatAssistant`
подключает [рецепты памяти](recipes.md) за вас:

```python
from reactifact.chat import ChatAssistant, ChatMemory

assistant = ChatAssistant(
    ...,
    memory=ChatMemory(
        message_type=ConversationMessage,
        keep=20,                 # держать последние 20 сырых сообщений (WindowPruner)
        # или, чтобы сжимать, а не отбрасывать:
        # summarize=my_summarize,      # (ctx, previous_digest, stale) -> str | None
        # summary_type=Summary,        # артефакт дайджеста (нужно поле `text`,
        # build_summary=lambda t: Summary(text=t),   # либо свой builder)
        compact_commits=200,     # ещё и ограничить лог коммитов (Context.compact)
    ),
)
```

Используйте одну форму, не обе: pruning держит сырое окно; суммаризация
сворачивает выпавшее в растущий дайджест и удаляет исходные сообщения.
`compact_commits` ортогонален — он ограничивает *историю*
([Устойчивость и resume](durability.md)), а не рабочий набор, и не требует
`message_type`, поэтому `ChatMemory(compact_commits=…)` — самодостаточная
политика (безопасный выбор, когда сообщения коррелированы/типизированы, а не
плоский role/text лог, напр. отдельные типы `Question`/`Answer`).

## Ходы с человеком в цикле

Ход может приостановиться для человека без спецмашинерии — вопрос это просто
эффект, а ответ — просто ещё один патч:

```python
# внутри produce
self.effects.ask("Approve this estimate?", kind="approval")
# позже, из вашего API, когда человек отвечает:
ctx.resume(question_id, "approved")   # эмитит update → агенты реагируют
```

Ожидающий вопрос — это состояние, поэтому он переживает рестарт как всё
остальное: клиент может вернуться с ответом на следующем ходу. См. примеры
`devops` и `repair`.

## Откат и возобновление

- **Продолжить** прерванный прогон: откройте сессию заново и `arun()` —
  отложенные триггеры восстановятся ([Устойчивость и resume](durability.md)).
- **Откатиться** к более ранней точке: `context.checkout(version)` (отказывает
  ниже baseline компакции) или `context.branch()` для альтернативы без
  изменения оригинала.
- **Изучить** ход: `context.diff(v1, v2)` показывает, что именно изменилось,
  `reactifact replay` восстанавливает *почему*.

## См. также

- [Устойчивость и resume](durability.md) — сессии, at-least-once, компакция.
- [Рецепты](recipes.md) — рецепты памяти и рецепт разговора.
- [`examples/chat`](../../examples/chat) — одно-модельный рецепт, запускается
  офлайн.
- [Quickstart](quickstart.md#3-a-session-persisted-chat-bot) — чат-бот в
  несколько строк.
