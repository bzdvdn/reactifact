# Переход на reactifact 0.23.0

Этот гайд — для приложений, которые уже используют reactifact. Он описывает
новый фасад и авторский слой 0.23.0; runtime, артефакты и провенанс не
изменились.

Коротко:

| Стиль 0.22 | Стиль 0.23 |
| --- | --- |
| `ChatAssistant(..., user_message=Q, agents=[...])` | `Chat(..., seed_type=Q, groups=[...])` |
| `create_message` + `ContextVar` категории | `seed_factory(text, session_id, request)` |
| override `stream()` вокруг хода | `per_turn(text, session_id, request)` |
| `Runtime(ctx, agents=[...])` | `Runtime(ctx, groups=[...])` или `steps=[...]` |
| клиент тула через замыкание/глобал | `Annotated[Client, Inject()]` (+ алиас `Depends`) |
| тул возвращает строку для парсинга | тул возвращает pydantic-модель / `list[Model]` |
| вручную `Tracer(store=...)` | `Pipeline(traces=...)`, `Chat(traces=...)` |
| подклассы рецептов | `Recipe.from_funcs(...).pipeline(...)` |

`ChatAssistant` остаётся низкоуровневым движком под `Chat`; это больше не
рекомендуемая точка входа.

## 1. Chat: `ChatAssistant` → `Chat`

Было:

```python
assistant = ChatAssistant(
    store=store,
    groups=[echo_group],
    user_message=UserMsg,
    reply=reply,
    session_state=session_state,
    resources=lambda: build_resources(),
)
```

Стало:

```python
chat = Chat(
    store=store,
    groups=[echo_group],
    seed_type=UserMsg,
    answer_type=Reply,
    reply=reply,
    session_state=session_state,
    resources=lambda: build_resources(),
)
```

- `seed_type=` заменяет `user_message=`;
- `answer_type=` позволяет `chat.answer()` вернуть типизированный артефакт;
- `invoke/stream/history` работают как раньше;
- `chat.router()` — FastAPI-роутер, `chat.test_client()` — изолированный
  in-memory клиент;
- `chat.traces=`/`trace_sample=`/`lifespan=` повторяют `Pipeline`.

### Категория, тенант или пользователь на ход

Раньше для этого часто использовали `create_message` и `ContextVar`. Теперь
значение приходит в `request` и попадает в `seed_factory`:

```python
def seed(text: str, session_id: str, request: Mapping[str, object]) -> UserQuery:
    return UserQuery(
        text=text,
        session_id=session_id,
        category=str(request.get("category") or ""),
    )

chat = Chat(..., seed_type=UserQuery, seed_factory=seed)
await chat.invoke("...", session_id="s1", request={"category": "helm"})
```

`create_message` по-прежнему принимается для HITL/resume; `seed_factory` —
короткий путь для обычных ходов. Для turn-scoped contextvars (vault,
knowledge namespace) используйте `per_turn`:

```python
@asynccontextmanager
async def per_turn(text, session_id, request):
    token = set_current_vault(SecretVault(...))
    try:
        yield
    finally:
        reset_current_vault(token)

chat = Chat(..., per_turn=per_turn)
```

`quick.chat_agent(...)` теперь возвращает `Chat`; он по-прежнему принимает
`groups=`/`steps=` и deprecated-алиас `agents=`.

## 2. Группы, шаги и алиасы

`Runtime(ctx, agents=[...])` — deprecated-алиас. Используйте ключ, который
описывает то, что вы передаёте:

```python
Runtime(ctx, groups=[MyGroup()])     # StepGroup-инстансы
Runtime(ctx, steps=[MyStep()])       # голые шаги, оборачиваются в группы
```

`Agent`/`create_agent` остаются deprecated-алиасами для `StepGroup`/
`create_step_group`; новый код использует канонические имена. `agents=` нельзя
сочетать со `steps=`/`groups=`.

## 3. Тулы: инъекция, типизированные результаты, политики

Параметр с обычным классом инжектится из `RuntimeResources` и не попадает в
JSON-схему:

```python
@tool
async def get_pod(name: str, k8s: K8sClient) -> Pod:
    return Pod(name=await k8s.get(name))
```

Если тип неоднозначен (dataclass, Protocol, скалярно выглядящий сервис),
помечайте явно:

```python
from typing import Annotated
from reactifact import Inject

@tool
async def get_pod(name: str, k8s: Annotated[K8sClient, Inject()]) -> Pod:
    ...
```

`Depends` — FastAPI-совместимый алиас `Inject`. Поддерживаются обе формы:

- `Annotated[T, Inject()]` — предпочтительно;
- `param: T = Inject()` — короткая запись.

Остальные изменения 0.23:

- pydantic-результат заполняет `ToolOutput.data`; непустой `list[Model]`
  превращается в `{"items": [...]}`, а не `str(list)`;
- `@tool(retry=RetryPolicy(...), timeout=...)` ретраит исключения и
  `ToolError(retryable=True)`, а также ограничивает одну попытку;
- `ToolUse` отдаёт модели `ToolError.code`/`retryable`;
- вне шага ресурсы передаются явно:
  `execute_tool(tool, args, resources=... )`.

## 4. Рецепты: от подклассов к функциям

`PlanExecute` и `ReflectionLoop` всё ещё можно наследовать, но функциональная
форма короче и сама собирает группу/пайплайн:

```python
flow = PlanExecute.from_funcs(
    goal_type=Goal,
    step_type=PlanStep,
    result_type=StepResult,
    final_type=FinalAnswer,
    plan=plan,
    execute_step=execute_step,
    finish=finish,
)
pipeline = flow.pipeline(seed_type=Goal)
```

`ReflectionLoop.from_funcs(...)` устроен так же. `flow.group()` возвращает
`StepGroup` для существующего `Runtime`.

## 5. Трейсы: сэмплирование и retention

Ручная сборка `Tracer(store=...)` больше не нужна:

```python
pipeline = Pipeline(steps=[...], traces=True, trace_sample=SamplingPolicy(rate=0.1))
chat = Chat(steps=[...], seed_type=Q, traces="traces.db")
```

Старые трейсы чистятся из CLI:

```bash
reactifact traces prune traces.db --keep 5000 --days 30 --dry-run
reactifact traces prune traces.db --keep 5000 --days 30 --keep-failed --keep-tag incident
```

Подробности — в [observability](observability.md).

## 6. Паритет `Pipeline`

- `Pipeline.answer(seed)` / `await Pipeline.aanswer(seed)` возвращают
  типизированный `answer_type`, как `Chat.answer()`;
- `Pipeline(trace_sample=...)` учитывается и в `TestPipeline`;
  `TestPipeline.answer()`/`aanswer()` делегируют;
- `TestChat.override(Type, instance)` подменяет типизированный ресурс в
  тестовом клиенте.

## 7. Совместимость и удаления

- `Produce` / `ProduceCall` / `@produce` удалены; используйте `Step`/`@step`/
  `StepCall`.
- `also_creates=` / `reacts_to=` у шагов удалены (deprecated с 0.20): outputs
  объявляются в `produces=(Primary, Extra)`, входы/триггеры — в
  `consumes=(...)` (или `Consume(..., wakes=False)`).
- `Effects.ask` / `Effects.resume` / `Effects.act` удалены; инжектируйте
  `Human`/`Outbox` (`human.ask/resume`, `outbox.act`). Отдельного
  deprecation-релиза у них не было: 0.23 — первый релиз с заменой, поэтому это
  осознанное исключение из правила одного минора.
- `ChatAssistant`, `Agent`, `create_agent`, `agents=`, `Agent.produces` и
  `Artifact.at/history/diff` остаются deprecated-мостами до 0.24.0. Новый
  код использует `Chat`, `StepGroup`, `create_step_group`, `groups=`, `steps`
  и `Context.version_history`/`artifact_version`/`version_diff`.
- Shim-модули тулов (`reactifact.tool_use`, `reactifact.native_tool_use`,
  `reactifact.agent_tool`) импортируются в этом окне совместимости;
  канонические пути — `reactifact.tools.use`/`.native`/`.agent`.

## 8. Чек-лист миграции

1. Замените `ChatAssistant(..., user_message=..., agents=...)` на
   `Chat(..., seed_type=..., groups=...)`.
2. Перенесите метаданные хода в `seed_factory`/`request`, а contextvars — в
   `per_turn`.
3. Замените `Runtime(..., agents=[...])` на `groups=`/`steps=`.
4. Помечайте неоднозначные сервисы тулов через `Inject()`; возвращайте
   pydantic-модели/списки.
5. Замените подклассы рецептов на `from_funcs(...)`, где функциональная форма
   подходит.
6. Переведите трейсы на `traces=`/`trace_sample=` и добавьте retention-политику.
7. Замените `effects.ask/act/resume` на инжектируемые `Human`/`Outbox`, а
   `also_creates=`/`reacts_to=` перенесите в `produces=`/`consumes=`.
8. Прогоните тесты и поищите старые написания:

   ```bash
   rg "ChatAssistant|agents=|user_message=|reactifact\.tool_use|@produce|also_creates|reacts_to|effects\.(ask|act|resume)"
   ```
