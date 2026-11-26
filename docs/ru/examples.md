# Примеры

В `examples/` живут тринадцать запускаемых примеров (в репозитории, не в
пайплайне сборки): двенадцать полноценных приложений и `tour`, который
изолирует примитивы. Это эталонные реализации
[рецептов](recipes.md), [паттернов](patterns.md) и
[port-матрицы](port-matrix.md) — канонические примеры разбиты на пакет `steps/`
(стадии) + тонкие `web.py`/`chat.py`/`main.py`.

Все демо работают **и с ключами API, и без них**: настройте `.env` (скопируйте
`.env.example`) ради LLM/эмбеддера/изображений — или оставьте фолбэк на
детерминированный демо-режим.

Чат-демо с сессиями (`knowledge`, `devops`, `repair`) строят свой web-слой на
каноническом контракте `reactifact.chat` + `reactifact.web` — каждое поставляет
только доменные хуки (агенты, входная модель, терминальный ответ), а
SSE-транспорт и сохранение сессий берёт из фреймворка.

## Чему учит каждый пример

| Пример | Чему учит | Форма |
| --- | --- | --- |
| `starter_app` | минимальное FastAPI-приложение поверх `quick` | он-рамп; без auth/settings |
| `knowledge` | multi-source RAG, evidence и детерминированный расчёт по CSV | доменное web-приложение |
| `knowledge_base` | connector → chunks → index, инкрементальное состояние, hybrid query | ingestion/CLI |
| `medic_lab` | конкурирующие гипотезы, скоринг, HITL steering | доменный сервис |
| `devops` | HITL-инструменты, authz, per-user memory, трейсы | ближе всего к production; demo auth |
| `code_review` | детерминированные правила по diff + опциональный LLM, связанный отчёт | CLI/worker |
| `repair` | stage-driven replanning, бюджет, HITL | доменное web-приложение |
| `chat` | минимальный транскрипт + ограниченный `ChatMemory` | офлайн-чат |
| `forklab` | `branch`/`merge`, конфликты и политики | CLI/исследование состояния |
| `fintech_audit` | вычисленный воспроизводимый ответ с context hash | CLI/аудируемый пайплайн |
| `outbox` | закоммиченные side effects, retry и replay | паттерн worker/daemon |
| `repo_agent` | destructive-инструмент за одобрением | CLI/guardrail |
| `tour` | примитивы фреймворка в маленьких сценах | обучение |

Выбирайте пример, ближайший к вашей задаче. Если читать только три — начните
со `starter_app` (минимальный web), `devops` (tools/authz/memory) и
`fintech_audit` (вычисляемый воспроизводимый ответ). Примеры намеренно узкие: у
них **нет** обязательного auth/settings. Production-shape scaffold — это
`reactifact new app|daemon`, а не ещё один пример.

## `starter_app` — копипастибельное веб-приложение поверх quick-кейсов

**Что показывает:** один FastAPI-процесс, который можно склонировать и
запустить: структурированный вызов, RAG с цитатами, LLM с тулами и чат с
сессиями — плюс дашборд трасс по всем запускам. Это тонкая надстройка над
[`reactifact.quick`](quickstart.md), а провайдер выбирается `from_env()`: без
ключа → офлайн/детерминированные фолбэки (RAG всё равно отвечает из
`knowledge/` с цитатами); `OPENROUTER_API_KEY` → OpenRouter;
`OPENAI_BASE_URL` → любой OpenAI-совместимый эндпоинт. Копируй директорию и
меняй домен.

```bash
uv sync --extra web
uv run python -m examples.starter_app.app      # http://127.0.0.1:8000
```

## `knowledge` — мультиисточниковый чат с доказательствами

**Что показывает:** поиск `fan_out_sources` по индексу `reactifact.knowledge`
и структурированному CSV-источнику → ленивую `materialize_doc` → извлечение
доказательств → проверку утверждений → ответ с провенансом; детерминированный
CSV-расчёт (`Spreadsheet` → `Calculation`). Веб-приложение также монтирует
`/traces` (агентские запуски с колонками Question/Answer), `/contracts`
(статические consumes/produces/tools) и `/knowledge/` (статус индексации).

```bash
uv run python ./examples/knowledge/web.py    # FastAPI/SSE + трейс-дашборд
uv run python ./examples/knowledge/chat.py   # интерактивный CLI
```

Ключевые файлы: `steps/` (common, router, search, evidence, calc, lifecycle),
`agents.py`, `web.py`.

## `knowledge_base` — code-first индексация и поиск

**Что показывает:** опциональный слой `reactifact.knowledge` на реальном
корпусе: `Connector -> RawDocument -> Document -> Chunk -> EmbeddingProvider ->
Index`, инкрементальное состояние `DocStore` и hybrid-поиск (BM25 + RRF). По
умолчанию — SQLite и детерминированный эмбеддер, поэтому всё работает офлайн;
`--qdrant-url` меняет индекс, не меняя пайплайн.

```bash
uv run python examples/knowledge_base/build.py --docs docs/en --query "sources"
uv run python examples/knowledge_base/build.py --docs docs/en --qdrant-url http://localhost:6333
```

Ключевые файлы: `build.py` (коннектор, сборка пайплайна, запрос). См.
[База знаний](knowledge.md).

## `medic_lab` — лаборатория гипотез

**Что показывает:** один вопрос рождает конкурирующие гипотезы; каждая исследуется
по пулу доказательств; **скорится** по числу подтверждений/опровержений (скор
вычисляется, а не угадывается); HITL-управление через `PendingQuestion`;
честный отчёт при нехватке доказательств; `concurrency_limit` (LLM-агенты = 2,
глобальный лимит = 6).

```bash
uv run python ./examples/medic_lab/main.py    # uvicorn поднимается сам
```

## `devops` — инструментальный агент с HITL

**Что показывает:** `HITLLMAgent` + LLM-**роутер инструментов** (маршрутизация —
отдельный структурный шаг, `StructuredLLM`), мутации Kubernetes/GitLab/Ansible с
подтверждением человеком (каждая — необратимая операция, которую LLM не может
выдумать сам); трейс-дашборд на `create_trace_router` и страница
`/contracts`. Плюс слой trust & safety:
`guardrails.py` определяет **кастомный** guardrail (деструктивное изменение
прода требует тикета `CHG-`) и редактирование секретов, подключаемые к
`RuntimeResources` одной строкой — ops-политика блокирует инъекции и
неподтверждённые прод-изменения, редактирует PII/секреты и проверяет сырой ввод.
А ещё он оценивает собственный трафик: `online_eval.py` сэмплирует обслуженные
прогоны из трейс-стора (`trace_source`, кастомные `routed`/`routed_to`), тегирует
прошедшие `eval` / проваленные `eval:failed`, а `web.py` монтирует
`create_online_eval_router` (`POST /api/evals/run`). Две демо-личности
показывают multi-user слой: `admin1` (админ) и `admin2` (аналитик), выбор в UI
уходит заголовком `X-User` (только демо-аутентификация). Каждый запуск получает
`Principal` + `PermissionPolicy` (`policy.py`), поэтому `admin2` может общаться,
пользоваться k8s и GitLab, но получает отказ до старта Ansible-агента.
Долговременная память — per-user (`MemoryStore(namespace="user:<name>")`):
summary отчёта записывается как outbox-`PendingAction` (`remember_after_commit`)
и индексируется уже после коммита отчёта (`MemoryStore.dispatch`), поэтому
упавший ход не оставит висячую запись; recall подмешивается в routing в
следующих сессиях, не копируя историю чата и не попадая в namespace другого
пользователя. Отдельные демо (без LLM и сети):

```bash
uv run python ./examples/devops/web.py      # SSE UI + трейс-дашборд
uv run python ./examples/devops/safety.py   # кастомные guardrails, end-to-end
uv run python ./examples/devops/online_eval.py   # online-eval по прогонам
```

## `code_review` — детерминированный review-агент

**Что показывает:** unified diff → `ChangedFile` → `FileReview` на файл
(детерминированные правила + опциональный LLM) → `ReviewReport` с Markdown.
Правила ловят `eval`/`exec`, `shell=True`, захардкоженные секреты, bare
`except`, `TODO` и debug `print`; отчёт ссылается на каждый проверенный файл
(`reviewed`). Работает офлайн, без API-ключа.

```bash
python -m examples.code_review.main --demo
git diff | python -m examples.code_review.main
```

## `repair` — перепланирование под бюджет (русский по замыслу)

**Что показывает:** чат и данные каталога намеренно **на русском** — контраст с
другими англоязычными примерами сознателен (локализация — продуктовый вопрос, не
вопрос фреймворка). Поток: сбор фактов → варианты дизайна от LLM → 3 фото-превью
→ план → **детерминированная смета из каталога** → HITL-одобрение → жалоба на
бюджет запускает пересборку с более ранней стадии с откатом через
`_downstream_resets`. Явное детерминировано; LLM — только там, где это
действительно генерация.

Веб-приложение монтирует `/traces` и `/contracts` рядом с чатом.

```bash
uv run python ./examples/repair/web.py
uv run python ./examples/repair/chat.py
```

## `chat` — минимальный одно-модельный многоходовой разговор (офлайн)

**Что показывает:** простую форму multi-turn в ~80 строк — одна модель
сообщения (`ConversationMessage`), которую пишут обе стороны. `Conversation`
записывает ходы (`create_turn`) и рендерит поток (`transcript`/`state`);
`ChatMemory` с `keep=` ограничивает его (`WindowPruner`) — всё через
`Chat`. Без LLM и API-ключа (отвечает детерминированный echo-агент).
Гетерогенная форма (разные типы на вопрос/ответ) — в примерах
`repair`/`devops`/`knowledge`, через `Transcript`/`MessageSpec` — см.
[Multi-turn и чат](chat.md).

```bash
.venv/bin/python -m examples.chat.main
```

## `forklab` — ветвление и слияние (§39-§40)

**Что показывает:** детерминированное исследование альтернативных состояний —
один вопрос, две стратегии исследования на **собственных форках**
(`Context.branch()`); трёхсторонний `merge()`, который либо чисто объединяет,
либо бросает явный `MergeConflict` (§40 — никакого молчаливого выбора);
оценщик слиянного состояния, чей `Answer` связан `supported_by` с находками
**обеих** веток. Полностью офлайн (§67) — дело в семантике состояния, а не в
модели. Флаг `--conflict` демонстрирует цикл «конфликт → политика разрешения».

```bash
uv run python -m examples.forklab.main            # счастливый путь
uv run python -m examples.forklab.main --mermaid  # граф провенанса слияния
uv run python -m examples.forklab.main --conflict # явный MergeConflict + политика
```

Тот же паттерн — естественная основа для переписывания `medic_lab`: гипотезы
становятся настоящими форками вместо tag-маршрутизации каналов.

## `fintech_audit` — аудируемый и воспроизводимый ответ

**Что показывает:** финансовый вопрос (*«какова вариация cloud-расходов за Q2
и требует ли политика одобрения?»*) с ответом из CSV транзакций, CSV бюджета и
документа политики. История аудита целиком: цифры считаются на чистом Python
(модель — не источник истины, §67); каждый производный артефакт ссылается на
то, откуда он взялся; `reactifact.audit.build_report` рендерит ответ с
хешем содержимого каждого артефакта и `context_sha256` всего запуска; а
повторный прогон даёт тот же хеш — это и проверяет
`reactifact replay <store> --session <id> --verify <hash>` на сохранённой
сессии. Офлайн, без ключа.

```bash
uv run python -m examples.fintech_audit.main
```

![fintech_audit demo: вариация и её аудит-отчёт, затем проверка воспроизводимости — повторный прогон печатает тот же context hash.](../img/fintech-audit-demo.gif)

См. также [`reactifact/audit.py`](../reference/index.md) (`build_report`,
`context_hash`, `report_to_markdown`) и [Наблюдаемость](observability.md) —
как отправить такой запуск в приёмник с `redactor=`.

## `outbox` — внешние эффекты как закоммиченное состояние

**Что показывает:** контракт outbox в семи детерминированных кейсах: шаг
записывает `outbox.act(...)` вместо I/O; `Runtime(dispatcher=...)` отправляет
после коммита; повторная деривация, дубли в одном поколении, слитые ветки,
replay, failure→retry и app-owned retry-wrapper сохраняют одну доставку.
Без LLM и сети; фейковый dispatcher делает счётчик видимым.

```bash
uv run python -m examples.outbox.main
```

См. [Outbox в production](durability.md#outbox-in-production) —
at-least-once-контракт и app-owned drain/retry-политика.

## `repo_agent` — destructive-инструмент за одобрением

**Что показывает:** `HITLLMAgent`-ассистент по крошечному репозиторию.
`read_file` и `run_tests` выполняются сразу; `git_commit` объявлен
`@tool(destructive=True)`, поэтому runtime превращает решение модели в
`PendingQuestion(kind="approve")` и выполняет коммит только после
`context.resume(...)`. Провайдер scripted, поэтому демо офлайн и
детерминировано.

```bash
uv run python -m examples.repo_agent.main
```

## `tour` — примитивы фреймворка в шестнадцати маленьких сценах

**Что показывает:** то, что легко пропустить в большом приложении:
типизированные артефакты с `version_history` / `diff` / `compact`;
типизированные входы с `Latest`, автоматическими read-only входами и
`Consume(..., debounce=True)`; SQLite-сессию, которая переживает перезапуск,
возобновляет pending-событие и реплеит прошлый коммит; опциональный LLM-шаг;
retry агента и per-step дедлайны; schema-backed HITL-вопрос с типизированным
ответом; чекпоинты/форки; типизированные режимы `Runtime.astream_events`;
optimistic-конфликт записи, разрешённый merge-политикой; subflow на изолированном
Context с trace-ссылкой на родительский run; MapReduce fan-out/reduce,
peer-to-peer swarm handoff, tool record/replay через `Runtime.replay_run`, A/B
эксперимент с выбором победителя, статическую проверку контрактов и
namespaced long-term memory. Все сцены по умолчанию офлайн, а `model` идёт через
провайдера только с `--llm`.

```bash
uv run python -m examples.tour.main                 # все девять сцен, офлайн
uv run python -m examples.tour.main --scene state   # версии + компакция
uv run python -m examples.tour.main --scene durable --db /tmp/tour.sqlite3
uv run python -m examples.tour.main --scene resilience
uv run python -m examples.tour.main --scene hitl
uv run python -m examples.tour.main --scene time_travel
uv run python -m examples.tour.main --scene events
uv run python -m examples.tour.main --scene conflicts
uv run python -m examples.tour.main --scene subflow
uv run python -m examples.tour.main --scene map_reduce
uv run python -m examples.tour.main --scene swarm
uv run python -m examples.tour.main --scene replay
uv run python -m examples.tour.main --scene experiment
uv run python -m examples.tour.main --scene validate
uv run python -m examples.tour.main --scene memory
uv run python -m examples.tour.main --llm --scene model
```

С `--llm` `from_env()` читает `examples/tour/.env` (скопируйте `.env.example`);
без провайдера сцена `model` печатает честный детерминированный бриф. См.
[`examples/tour`](../../examples/tour).

## Тесты

```bash
.venv/bin/python -m pytest      # 1111 тестов (6 пропущены без TEST_PG_DSN)
.venv/bin/mypy                  # строгая типизация по всему репозиторию
.venv/bin/ruff check            # линтер
```

`uv sync` ставит группы dev+web из `pyproject.toml`; `uv build` собирает только
wheel `reactifact` (примеры и docs в пакет не входят).
