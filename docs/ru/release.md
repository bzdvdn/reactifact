# Релиз-менеджмент

Как режется, собирается, проверяется и публикуется версия `reactifact`.

## Версионирование

- [SemVer](https://semver.org/); пре-релизы помечаются `rc` (например,
  `0.20.0rc1`), для стабильного релиза `rc` убирается (`0.20.0`).
- Версия живёт в **одном месте**: `pyproject.toml` → `[project] version`.
  `reactifact.__version__` читает метаданные установленного дистрибутива;
  совпадение проверяет `tests/test_version.py`.

## Правило чейджлога

Каждое видимое пользователю изменение попадает в `CHANGELOG.md` (Keep a
Changelog). При бампе версии:

1. перенесите незакрытые пункты под новый заголовок `## [X.Y.Z] — <дата>`;
2. сгруппируйте `Added` / `Changed` / `Removed` (в т.ч. устаревшее);
3. все ломающие изменения выносите под заголовок `### Breaking`, даже в `rc`.

## Обновление между версиями

Для 0.30.0 есть [migration-гайд](migration-0.30.md); для 0.25.0 — [свой](migration-0.25.md); для 0.24.0 — [свой](migration-0.24.md); для 0.23.0 — [свой](migration-0.23.md); для 0.20.0 — [свой](migration-0.20.md). Источник истины
о том, что изменилось, — `CHANGELOG.md`; ломающие изменения помечены по правилу
выше. Несколько изменений, о которых стоит знать при переходе (см. также
[migrating.md](migrating.md) для переноса с других фреймворков):

- **0.30.0** — разделение `Agent`/`Step` завершено: шаги владеют `reads`,
  агенты — `consumes`, `StepGroup` и все алиасы `groups=` удалены, `Pipeline`
  стал определением (`PipelineRunner` + `RunConfig` исполняют его), а `Chat`
  принимает runtime-настройки через `RunConfig`. См.
  [migration-гайд 0.30](migration-0.30.md).
- **0.25.0** — в основном аддитивная эргономика: `scope=` у `Step`/`@step`,
  `Unscoped()` для истории между ходами, рецепт `TurnContext`, side-effect-only
  шаги, типизированный `CurrentRequest`, `Step.progress`, конфиг
  `[tool.reactifact]`, `skills install --target opencode|all|auto`. Два
  запланированных удаления ломают совместимость: `EmbeddingSource`
  (используйте knowledge-слой + `IndexSource`) и shim-модули инструментов из
  0.20 (используйте `reactifact.tools.*`). См.
  [migration-гайд 0.25](migration-0.25.md).
- **0.24.0** — CLI/daemon/MCP/HTTP-адаптеры, `check`, `new agent`,
  `demo --web`, pipeline tools и HITL/approval-рецепты. LLM-хелперы
  (`structured_llm`, `json_schema_llm`, `llm_reply`, `chat_complete*`,
  `StructuredLLM.call`) теперь принимают `LLMProvider` (или `None`), а не
  `Context`. `Agent`, `create_agent`, `validate_agents`, `agents=` и модуль
  `reactifact.agents` удалены; используйте `StepGroup`, `create_step_group`,
  `validate_contracts(groups=...)` и `groups=`.
- **0.23.0** — типизированный фасад `Chat` (`answer/traces/lifespan/test_client`),
  DI тулов через `Inject`/`Depends`, типизированные результаты и
  структурированные ошибки тулов, function-first `PlanExecute`/`ReflectionLoop`,
  tail-сэмплирование и retention трейсов. `Produce`/`@produce`/`ProduceCall`,
  `also_creates=`/`reacts_to=` и `Effects.ask/resume/act` удалены (у последних
  не было отдельного deprecation-релиза; используйте `Human`/`Outbox`);
  оставшиеся deprecated-мосты описаны в migration guide 0.23.
- **0.22.0** — CLI-scaffolding (`new app|daemon`, `doctor`) и встроенные
  FastAPI/daemon-шаблоны (FastAPI остаётся опциональным extra ядра);
  provenance-aware `MemoryStore` с индексацией после commit; per-request
  ресурсы чата; типизированные run-события, checkpoints/forks, retries/timeouts,
  tool record/replay, swarm/map_reduce, optimistic concurrency/merge policies,
  типизированные HITL-ответы и trace scopes. `Produce`/`ProduceCall`/`@produce`
  уходят из top-level `__all__` и удаляются в 0.23.0.
- **0.21.0** — опциональный code-first слой `reactifact.knowledge`:
  `Connector → RawDocument → Document → Chunk → Index`, инкрементальное
  состояние `DocStore`, `FilesystemConnector`, hybrid BM25 + RRF, индексы
  `MemoryIndex` / `SqliteIndex` / `QdrantIndex` / `ChromaIndex` /
  `PgVectorIndex`, hook `rerank=` и `llm_reranker`, опциональный knowledge
  status router. `IndexSource` / `IngestStep` — явные мосты в `Source` / `Step`;
  новых runtime-зависимостей у ядра нет.
- **0.20.0** — новая core-линия. `Step`/`@step` заменяют авторский слой
  `Produce`; класс-шаги получают `effects: Effects` (`self.effects` удалён);
  `Artifact` — frozen handle поверх content-addressed version store; сессии
  используют v2-формат `blobs`/`data_ref`; старый набор примеров удалён.
  `Produce`, `ProduceCall`, `@produce`, `StepGroup.produces`,
  `Artifact.at/history/diff` и top-level shims инструментов остаются
  импортируемыми на окно совместимости 0.21; `Produce`/`ProduceCall`/`@produce`
  больше не входят в top-level `__all__`; `Produce`/`ProduceCall`/`@produce` удалены в 0.23.0, старые имена `Agent` удалены в 0.24.0, остальные мосты сохранены до 0.24.x.
- **0.14.0** — поведенчески заметные (не ломающие) изменения:
  - `Budget.max_seconds` теперь **жёсткий** дедлайн хода: runtime отменяет
    генерацию в полёте, когда он истекает (зависший LLM-вызов действительно
    прерывается), и завершает ход `RunOutcome.BUDGET_TIME_EXCEEDED`; батч
    триггеров отменённой генерации остаётся в очереди для resume. Раньше
    проверка шла только между генерациями, и прогон мог её перескочить.
  - Появляется `RunOutcome.STOPPED`, когда `Runtime.request_stop()` /
    `ashutdown()` завершает прогон на границе генерации.
  - Логи молчат по умолчанию — под `reactifact.*` ничего не пишется, пока не
    вызван `configure_logging()`.
- **0.7.0** — `Context.merge_from` теперь сохраняет `id` артефакта,
  существующего в `other`, но отсутствующего в `target` (раньше генерировал
  новый). Если вы полагались на старое поведение с генерацией id — вряд ли,
  т.к. оно молча отвязывало слитый артефакт от любой relation, указывающей
  на его исходный id — передайте артефакт через `create(data, id=new_id())`
  сами перед merge, чтобы сохранить старый эффект.
- **0.2.0** — `reactifact/__init__.py` реэкспортирует только core-поверхность
  (~40 имён вместо ~150); eval, tracing, checkpoint/branch-бэкенды, chat/web
  слой, адаптивный scheduler, replay, structured-LLM хелперы, viz и
  prompt-шаблоны переехали в импорты из своих сабмодулей. Ничего не
  переименовано — полный список before/after в записи `### Breaking`
  `CHANGELOG.md`.
- **0.4.0-rc1** — `LLMRequest.temperature` был захардкожен как `0.7`, стал
  `float | None`; `None` теперь означает «не передавать поле → дефолт
  провайдера», а не «использовать `0.7`». Форма вызова та же, поведение
  генерации — другое, ошибки не будет — если код полагался на старый неявный
  дефолт, передайте `temperature=0.7` явно (на вызов или на провайдер).
- **0.1.0-rc1** — `Produce` больше не возвращает `Patch`; вместо этого пишет
  `effects.create/update/link/ask/resume` и возвращает `None` (см.
  [effects](effects.md)). `InterruptPatch`, `Patch.merge_existing_patch` и
  `Patch.to_dict` удалены.

## Критерии релиза {: #release-checklist }

Что должно быть верно перед выпуском — и конкретно планка выхода из pre-1.0 и
релиза **1.0.0**:

- [ ] `uv run python -m pytest -q` зелёный, покрытие не ниже порога
      `fail_under` (а `reactifact/quota.py`, `metrics.py`, `eval/` — не в хвосте).
- [ ] `uv run python -m mypy` и `uv run python -m ruff check` /
      `ruff format --check` чисты.
- [ ] `uv run --group docs mkdocs build --strict` чист (это проверяет CI).
- [ ] Публичная поверхность не менялась иначе, чем фиксируют freeze-тест
      (`tests/test_public_api.py`) и §Стабильность `docs/ru/api.md`; любое
      переименование/удаление имеет запись `### Breaking` и окно депрекейшена.
- [ ] Wheel-smoke проходит в чистой venv (`uv build`, импорт, CLI).
- [ ] `CHANGELOG.md` переносит `[Unreleased]` под `## [X.Y.Z] — <дата>`.
- [ ] `tests/test_version.py` проходит (метаданные дистрибутива
      совпадают с `reactifact.__version__`).

**Критерии 1.0** (всё выше, плюс):

- [ ] Два подряд минорных релиза без записи `### Breaking`.
- [ ] Каждый задокументированный `__all__` подмодуля зафиксирован freeze-тестом
      и отражён в `docs/ru/api.md`.
- [ ] У поверхностей safety/eval/observability (guardrails, authz, quota, eval,
      online eval, metrics, tracing) есть документация и тесты.
- [ ] В контракт не протекают гарантии уровня `rc`/`Unreleased`.

## Цикл релиза

```bash
# 0) версия, которую режете (pyproject.toml и CHANGELOG совпадают)
VERSION=0.30.0

# 1) проверки
.venv/bin/python -m pytest && .venv/bin/python -m mypy \
  && .venv/bin/python -m ruff check && .venv/bin/python -m ruff format --check \
  && .venv/bin/lint-imports && uv lock --check

# 2) версия и чейджлог

# 3) сборка (сначала очистите stale build/ + dist/: setuptools переиспользует build/lib)
rm -rf build dist *.egg-info
uv build                         # dist/reactifact-$VERSION-py3-none-any.whl + sdist

# 4) проверка wheel в чистом venv (не workspace — чтобы не цеплял PYTHONPATH)
uv venv /tmp/reactifact-rc
uv pip install --python /tmp/reactifact-rc/bin/python dist/reactifact-$VERSION-py3-none-any.whl
/tmp/reactifact-rc/bin/python -c "import reactifact; print(reactifact.__version__)"
/tmp/reactifact-rc/bin/reactifact --help          # console-скрипт на месте
unzip -l dist/reactifact-$VERSION-py3-none-any.whl | grep -E "examples/|tests/|tracing/templates"

# 5) тег
git tag v$VERSION && git push origin v$VERSION

# 6) публикация только новых файлов, а не старого dist/ (токен PyPI в env)
uv publish --publish-url https://upload.pypi.org/legacy/ dist/reactifact-$VERSION*
```

## Что входит в дистрибутив

Wheel пакует только пакет `reactifact` (setuptools `packages.find` исключает
`examples`/`tests`) плюс шаблоны трейс-дашборда
(`reactifact/tracing/templates/*.html`). Sdist дополнительно несёт тесты для
упаковщиков; примеры и docs остаются в репозитории и служат
документацией-примером.

## Откат

Сломанный `rc` чинится в следующем `rc`/релизе — историю тега не переписываем.
Патч-релизы строго обратно совместимы (§61).