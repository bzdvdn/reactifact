# Справочник API

Символы верхнего уровня, экспортируемые `reactifact` (см. `reactifact/__init__.py`).
Формат по группам: имя — роль в одну строку. Детали — в док-строках модулей —
см. [автосгенерированный референс](../reference.md) (только на английском,
докстринги в коде не переведены): сигнатуры, типы и полный текст докстринга
рендерятся прямо из исходников на сайте документации, а не копируются сюда
вручную.

## Стабильность

Начиная с `0.4.0` reactifact всё ещё pre-1.0, но уже не `rc` — поверхность ниже
это стабильный контракт, а не движущаяся цель.

- **Публичный API = каждое имя в `reactifact.__all__`** (и в `__all__` каждого
  подмодуля — `reactifact.recipes`, `reactifact.providers`, `reactifact.viz`,
  `reactifact.eval`, `reactifact.quick`, `reactifact.redaction`, `reactifact.audit`, …) — это ровно тот набор символов, что задокументирован на
  этой странице. Если что-то импортируется из `reactifact`, но не входит в
  `__all__` — это внутренняя деталь без гарантий совместимости. Например,
  `reactifact.relations.RelationGraph` и `reactifact.commit_log.CommitLog`
  существуют потому, что `Context` разбили на модули поменьше ради
  читаемости, но ни один из них не экспортируется: поддерживаемая
  поверхность — это `Context`, а не они.
- **SemVer в pre-1.0-стиле**: минорный бамп (`0.4.0` → `0.5.0`) может добавить
  символы или, в редких случаях, изменить поведение так, что `CHANGELOG.md`
  явно пометит это `Breaking` — минорные релизы до 1.0 всё ещё позволяют
  reactifact исправить архитектурную ошибку. Патч (`0.4.0` → `0.4.1`) никогда не
  убирает и не переименовывает публичный символ и никогда не меняет
  задокументированное поведение — только чинит баги относительно него.
- **Любое ломающее изменение помечено в `CHANGELOG.md` заголовком
  `### Breaking`**, даже в pre-1.0 релизе — см. [release.md](release.md). Если
  перед апгрейдом читать только один раздел — читайте этот.
- Всё, что находится под `reactifact.cli.*` за пределами задокументированных
  подкоманд `python -m reactifact …`, а также любые тест-хелперы модулей —
  деталь реализации, даже если формально импортируется.

## Он-рамп (`reactifact.quick`)

Тонкий сахар над примитивами ниже для четырёх частых первых задач — у каждого
объекта есть настоящий `.agent`/`.agents` и `.context` запуска, поэтому он
выпускается в `Consume`/`Produce`/`Effects` без переписывания. См.
[Быстрый старт §0](quickstart.md).

| Символ | Роль |
| --- | --- |
| `agent(system, schema)` | один структурированный вызов LLM → один типизированный артефакт |
| `rag(sources)` | поиск → материализация → ответ, с провенансом `supported_by` |
| `tools_agent(system, tools, human=False)` | LLM + тулы (`human=True` → HITL-вопросы) |
| `chat_agent(agents)` | настроенный `ChatAssistant` (по умолчанию store в памяти) |
| `Question` / `Doc` / `Answer` | обобщённые модели артефактов, которые использует фасад |

## Context и состояние

| Символ | Роль |
| --- | --- |
| `Context` | версионируемое рабочее состояние; ресурсы; запросы; `latest(Model)`; announce; diff/rollback |
| `View` | результат типа-запроса (`context.view(...)`) |
| `RuntimeResources` | провайдеры + источники + произвольные ресурсы приложения; `redactor=` вычищает текст трейсов (см. `reactifact.redaction`); `await resources.aclose()` закрывает HTTP-клиенты llm/embedder (duck-typed) — вызывайте сами при реальном завершении работы, автоматически это делает только `ChatAssistant` для callable `resources=` на каждый ход |
| `Commit`, `Read`, `Write` | учёт версий и записанные операции провенанса |

## Артефакты и изменения

| Символ | Роль |
| --- | --- |
| `Artifact` | пара `(id, data)`; `data` — модель pydantic |
| `Patch` | скомпилированный набор изменений рантайма (транспорт); produces пишут `self.effects`, `Patch` собирает рантайм |
| `reactifact.operations` (`Create`/`Update`/`Delete`/`Link`/`Unlink`/`Relation`) | скомпилированные операции, которые несёт патч (§12) |
| `Create`, `Update`, `Delete`, `Link`, `Unlink`, `Relation` | записи операций, из которых строятся патчи |

## Агенты и produce

| Символ | Роль |
| --- | --- |
| `Agent` | тонкий контейнер: `name`, `consumes`, `produces`, `concurrency_limit` |
| `create_agent` | конструктор-фабрика агента — без подкласса для обычных контейнеров |
| `Consume` / `consume` | декларативная (или декоратор) завязка реакции; `Consume.by_field` для скоуп-событий; `wakes=False` — читать как вход, не будя агента; `debounce=True` схлопывает несколько событий одного поколения в один запуск |
| `reactifact.consume.CorrelatedConsume` | срабатывает (и питает входы) только для ключа корреляции, где присутствуют все типы из `require` и отсутствуют все из `forbid` — механизм за `JoinConsume`/`AbsentConsume` |
| `reactifact.consume.JoinConsume(*parts, key=…)` | фабрика над `CorrelatedConsume`: срабатывает, когда для одного ключа существуют все перечисленные типы |
| `reactifact.consume.AbsentConsume(type, absent_type=…, key=…)` | фабрика над `CorrelatedConsume`: срабатывает для `type`, только если для того же ключа ещё нет `absent_type` |
| `Produce` / `produce` | производитель: пишет `self.effects` (или слот `effects` в функции-декораторе) → `None`; возврат модели/Patch тоже компилируется. Два канонических стиля — подкласс и функция `@produce` (см. [effects](effects.md)); `reacts_to=(Type, …)` ограничивает produce конкретными триггерящими событиями, когда несколько produce одного агента реагируют не на одно и то же; produce с необязательным параметром `trigger` получает уже резолвленный триггерящий артефакт вместо сырого `event` — гарантированно не `None` для CREATED/UPDATED/STALE события, если также задан `reacts_to` |
| `Trigger` | вторичное (не артефактное) условие входа produce; `context_condition(artifact, context)` — для условий, которым нужны другие артефакты (join/корреляция); флаг `debounce`, который читает `Runtime` |
| `StructuredGenerateAgent` | декларативный агент LLM→схема→артефакт (`schema`, `build_prompt`, `fallback`) |
| `LLMAgent` | блокирующий цикл LLM+инструменты (`system`, `tools`, `max_steps`, `deferred_tool_groups`) |
| `HITLLMAgent` | цикл LLM+инструменты с паузами на ответ человека (`max_asks`, отчёт о возобновлении) |
| `ToolUse`, `ToolUseHITL` | produce цикла инструментов; HITL-вариант ждёт одобрения перед исполнением |
| `DeferredToolGroup` (`reactifact.tool_use`) | группа инструментов, чьи схемы не попадают в промпт, пока встроенный инструмент `load_tools` в `ToolUse` их не запросит (только `LLMAgent`/`ToolUse`, не HITL-вариант) |
| `Tool`, `FunctionTool`, `tool`, `ToolOutput` | абстракция и регистрация инструментов |
| `ToolAnswer`, `Observation` | результаты инструментов и наблюдения модели (протокол цикла) |
| `PendingQuestion` | HITL-примитив: приостановленный вопрос, ждущий ответа человека, возобновляется через `self.effects.resume(...)` |

## Runtime

| Символ | Роль |
| --- | --- |
| `Runtime` | будит агентов по событиям; `run` / `arun` / `astream`; бюджет и параллельность; `isolate_errors=True` + `on_agent_error(agent, event, exc)`, чтобы исключение одного агента не обрывало весь запуск (по умолчанию — пробрасывается, §69) |
| `Budget`, `RunOutcome`, `RunStats` | лимиты запуска и итог/статистика |
| `Event`, `EventType` | проводной формат «что-то изменилось» — `ARTIFACT_CREATED`/`UPDATED`/`DELETED`/`STALE` |
| `EventHub`, `ProgressEvent` | канал прогресса/announce, который потребляют web-UI |
| `Scheduler` | политика выбора агента filter → rank → LLM tie-break, вызывается рантаймом на каждой итерации (см. [design notes](../en/design-notes/adaptive.md), пока только на английском) |
| `uncertainty_policy(...)` | собирает встроенную гибридную политику `Scheduler` (filter → rank → LLM tie-break → top-k) |

## Чат-слой (reactifact.chat + reactifact.web)

| Символ | Роль |
| --- | --- |
| `ChatAssistant` | сессии + цикл хода + история в одном handle (`stream`/`invoke`/`history`); хуки: `agents`, `user_message`, `reply`, `session_state` |
| `ChatEvent` | один транспорт-нейтральный фрейм (`session`/`status`/`message`) |
| `run_message(runtime, text, *, user_message, reply)` | строительный блок хода: создать вход → стримить статусы → терминальный ответ |
| `default_session_state(ctx, user_message)` | универсальный читатель истории (любой артефакт с `.text`) |
| `create_chat_router(assistant)` | FastAPI `APIRouter` канонического SSE-контракта (`/api/chat/stream`, `/api/runs/{id}`) — нужен extra `web` |
| `reactifact.web.sse(event, data)` | один SSE-фрейм |

## Визуализация (reactifact.viz + python -m reactifact)

| Символ | Роль |
| --- | --- |
| `blueprint(agents)` | статическая карта consumes/produces как Mermaid `flowchart` |
| `context_to_mermaid(context)` | живой граф провенанса контекста (артефакты + связи) |
| `trace_to_mermaid(trace)` | один запуск как Mermaid `sequenceDiagram` |
| `python -m reactifact graph\|context\|trace` | CLI, печатающий диаграммы в stdout |
| `trace_provenance_to_mermaid(trace)` | граф доказательств запуска (записанные артефакты + рёбра `patch.link`) |

## Replay (reactifact.replay, §55)

| Символ | Роль |
| --- | --- |
| `ReplayLLM(recording, mode="record"\|"replay", inner=…)` | записывает каждый LLM-вызов в JSONL или воспроизводит их точно; `ReplayMiss` при расхождении |
| `ReplayMiss` | воспроизводимый вызов не совпал с записью |
| `replay_context(store, session_id, version=None)` | восстанавливает состояние сохранённой сессии на коммите |
| `replay_summary(context)` | компактная сводка состояния для CLI `replay` |

## Ветвление (reactifact.context + reactifact.branching, §39-§40)

| Символ | Роль |
| --- | --- |
| `Context.branch(name="")` | форкает изолированную копию; фиксирует снимок базы для трёхстороннего слияния |
| `Context.merge(other, message=…)` | атомарное трёхстороннее слияние; `MergeConflict` при разошедшихся артефактах |
| `MergeConflict` | бросается, когда обе стороны изменили артефакт по-разному после форка |
| `BranchStore(KVBackend)` | хранит ветки как `branch:<session>:<name>` поверх KV-бэкенда |
| `python -m reactifact branch …` | CLI: `list` / `save` / `merge` |

## Оценка (reactifact.eval, §56)

| Символ | Роль |
| --- | --- |
| `run_suite(cases, metrics)` / `run_case(case, metrics)` | выполнить кейсы и скорить итоговые контексты |
| `EvalCase` / `EvalResult` / `EvalReport` / `Metric` | структуры кейс/скор/отчёт (`overall()`, `render()`, `to_dict()`) |
| `core_metrics` | четыре не генеративные метрики (answer/provenance/evidence/claim) |
| `answer_coverage()` · `calculation_correctness(values=…)` · `source_coverage()` · `confidence_calibration()` | фабрики с грёд-трусом (skip при отсутствии `expected`) |

## Структурный вывод

| Символ | Роль |
| --- | --- |
| `structured_llm(context, schema, *, system, user, attempts=…, on_error=…)` | один структурный вызов; `None` при честном сбое; `on_error(reason, exc)` (`"no_provider"`\|`"provider_error"`\|`"parse_error"`) — понять *почему*, не меняя контракт `None` |
| `StructuredLLM(schema, *, system=…, attempts=…, on_error=…)` | переиспользуемый экземпляр; `.call(context, user)` |
| `llm_reply(context, *, system, user, attempts=…, on_error=…)` | обычный (неструктурный) вызов → `str` или `None` (под капотом схема с одним полем) |
| `parse_structured` | допускающий JSON→модель парсер, используемый внутри |

## Промпты (reactifact.prompts, §68)

| Символ | Роль |
| --- | --- |
| `PromptTemplate(template, *, defaults=…)` | строгий рендер `{var}`: объявленные `variables`, `KeyError` при нехватке, поля атрибутов модели (`{question.text}`), литералы `{{`/`}}` |
| `MessagesPrompt([(role, template), …])` | рендерит чат-последовательность в `list[Message]` |

## Источники (reactifact.sources)

| Символ | Роль |
| --- | --- |
| `Source` | ABC: `asearch(query, limit)` → list[SourceRef] |
| `SourceRef` | общий атомарный результат поиска (ранжирован, скоуп, стабильный id) |
| `FileSystemSource` | поиск по ключевым словам/эмбедингу по локальным файлам |
| `CSVSource` | детерминированный поиск по каталогу/таблице |
| `EmbeddingSource` | векторный поиск по подготовленному корпусу |
| `WebSource` | открытие ресурсов + ленивое разрешение удалённых документов |

## Провайдеры (reactifact.providers)

| Символ | Роль |
| --- | --- |
| `LLMProvider`, `EmbeddingProvider` | два контракта, с которыми говорит ядро |
| `ImageProvider`, `SpeechProvider`, `TranscriberProvider`, `VideoProvider` | медиа-контракты |
| `OpenAICompatProvider`, `OpenAICompatEmbedder` + вендорные фабрики (`openai_llm`, `anthropic_llm`, `deepseek_llm`, `groq_llm`, `mistral_llm`, `openrouter_llm`, `gemini_llm`, `ollama_llm`, `azure_llm`, …) | 20+ чат/эмбеддинг-бэкендов, у всех `retry_attempts=3` по умолчанию (429/5xx/сетевые ошибки, экспоненциальный backoff — никогда на 4xx) |
| `openrouter_embedder`, `openrouter_speech`, `groq_transcriber`, `together_embedder`, `fireworks_embedder`, `qwen_embedder`, `nvidia_embedder` | эмбеддинги/TTS/STT для вендоров, чьи не-чат эндпоинты подтверждённо OpenAI-совместимы (см. [providers](providers.md)) |
| `Message`, `Role` | одно сообщение чата; `role` — закрытый `Literal` + фабрики `Message.system/user/assistant/tool` |
| `LLMRequest` | одна генерация: `messages` + `temperature`/`max_tokens` — `None` = дефолт провайдера (вызов перекрывает провайдера, провайдер `None` = поле не отправляется) |
| `LLMResponse`, `LLMResponseChunk` | результат одной генерации / один поточный чанк, возвращаемые провайдером |
| `*_from_env(**overrides)` | подключение из `.env`; возвращает `None`, если не настроено |
| `from_env(**overrides)` | выбор в один вызов: сперва `OPENROUTER_API_KEY`, иначе `OPENAI_BASE_URL`, иначе `None` — тот самый двухветочный дефолт, что каждый пример вручную собирает в своём `build_llm()` |
| `FakeLLM`, `FakeEmbedder` | детерминированные заглушки для тестов/демо |

## Рецепты (reactifact.recipes)

| Символ | Роль |
| --- | --- |
| `find(inputs, Model)` / `find_all(inputs, Model)` | типизированный поиск в `inputs` продьюса без `next(... isinstance ...)` |
| `fan_out_sources(context, query, owner_id, …)` | идемпотентный поиск fan-out → рефы + патч |
| `materialize_doc(context, ref_artifact, doc_factory, relation=…)` | ленивый реф → документ с провенансом |
| `StatusMachine` | детерминированный жизненный цикл артефакта (`next_status`, `terminal`, `on_transition`, `query_id_field`/`status_field`) |
| `WindowSummarizer(message_type, artifact_type, summarize=…, build=…)` | периодическая суммаризация окна диалога, идемпотентна по числу сообщений |
| `WindowPruner(message_type, keep=…)` | удаляет сообщения старше окна; полезен и сам по себе |
| `llm_summarizer(system=…)` | строит колбэк `WindowSummarizer(summarize=…)` из системного промпта через `llm_reply` |

## Хелперы текста и отката (reactifact.recipes)

| Символ | Роль |
| --- | --- |
| `keyword_score(text, query, *, stopwords=EN_STOPWORDS, use_stems=False)` | детерминированный скоринг покрытия запроса (EN/RU) |
| `stem_words(text)` / `stem(word)` | стемминг русско-английских токенов без эмбеддингов |
| `EN_STOPWORDS` | набор английских стоп-слов по умолчанию |
| `changed_fields(old, new, *, ignore=())` | какие поля реально изменились (новый `None` — не изменение) |
| `earliest_stage(changed, *, field_stages, order)` | первая затронутая изменение стадия (change → rebuild) |
| `downstream_fields(target, *, field_stages, order)` | поля, которые сбрасывать при пересборке со стадии |

## Сессии, чекпоинты, трейсинг

| Символ | Роль |
| --- | --- |
| `Session`, `SessionStore` | долгоживущая память чата между запросами |
| `KVBackend`, `FileKVBackend`, `SQLiteKVBackend`, `PostgreSQLKVBackend` | key/value чекпоинты под сессии (`pg` extra для Postgres) — async-native: файловый I/O уходит в отдельный поток, SQLite/Postgres держат одно постоянное соединение (WAL + busy_timeout у SQLite) под `asyncio.Lock` |
| `CheckpointBackend`, `FileBackend`, `SQLiteBackend` | чекпоинты всего контекста |
| `Tracer`, `CompositeTracer`, `AgentSpan`, `RunTrace`, `LLMCall`, `TraceStore` | примитивы трейсинга (async-приёмники: `export`/`query`/`get`) |
| `LangfuseTracer`, `OTLPTracer`, `PostgresStore` | внешние приёмники трейсов — `OTLPTracer` вендор-нейтральный (GenAI semconv, любой OTLP/HTTP-коллектор), `LangfuseTracer` заточен под Langfuse, Postgres поддерживает async чтение+запись; дашборд (`create_trace_router`) принимает любой `TraceReader` |
| `create_trace_router(store)` (`reactifact.tracing.web`) | FastAPI-роутер дашборда |

## MCP (reactifact.mcp, extra `mcp`)

| Символ | Роль |
| --- | --- |
| `mcp_stdio_tools(command, args)`, `mcp_http_tools(url)` | подключение к MCP-серверу, отдаёт его инструменты как `list[Tool]` |
| `mcp_tools(session)`, `MCPTool` | обернуть инструменты существующей `mcp.ClientSession` |
| `oauth_client_credentials(server_url, client_id=, client_secret=, issuer=)`, `InMemoryTokenStorage` | значение `auth=` для `mcp_http_tools` — grant `client_credentials` OAuth (machine-to-machine, без браузера и согласия человека) |
| `create_mcp_server(tools, context=...)` | отдать `Tool` (и, с `context=`, артефакты `Context`) как `mcp.server.mcpserver.MCPServer` |