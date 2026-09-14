# Релиз-менеджмент

Как режется, собирается, проверяется и публикуется версия `reactifact`.

## Версионирование

- [SemVer](https://semver.org/); пре-релизы помечаются `rc` (например,
  `0.5.0rc1`), для стабильного релиза `rc` убирается (`0.5.0`).
- Версия живёт в **двух местах** и должна совпадать:
  - `pyproject.toml` → `[project] version`;
  - `reactifact/__init__.py` → `__version__`.

## Правило чейджлога

Каждое видимое пользователю изменение попадает в `CHANGELOG.md` (Keep a
Changelog). При бампе версии:

1. перенесите незакрытые пункты под новый заголовок `## [X.Y.Z] — <дата>`;
2. сгруппируйте `Added` / `Changed` / `Removed` (в т.ч. устаревшее);
3. явно помечайте ломающие изменения даже в `rc`.

## Обновление между версиями

Отдельного migration-гайда нет — источник истины о том, что изменилось,
`CHANGELOG.md`, ломающие изменения помечены по правилу выше. Два изменения,
о которых стоит знать при переходе через них:

- **0.7.0** — `Context.merge_from` теперь сохраняет `id` артефакта,
  существующего в `other`, но отсутствующего в `target` (раньше генерировал
  новый). Если вы полагались на старое поведение с генерацией id — вряд ли,
  т.к. оно молча отвязывало слитый артефакт от любой relation, указывающей
  на его исходный id — передайте артефакт через `create(data, id=new_id())`
  сами перед merge, чтобы сохранить старый эффект.
- **0.5.0** — `reactifact/__init__.py` реэкспортирует только core-поверхность
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
  `self.effects.create/update/link/ask/resume` и возвращает `None` (см.
  [effects](effects.md)). `InterruptPatch`, `Patch.merge_existing_patch` и
  `Patch.to_dict` удалены.

## Цикл релиза

```bash
# 1) проверки
.venv/bin/python -m pytest && .venv/bin/python -m mypy \
  && .venv/bin/python -m ruff check && .venv/bin/python -m ruff format --check

# 2) версия и чейджлог

# 3) сборка
uv build                         # dist/reactifact-0.5.0-py3-none-any.whl + sdist

# 4) проверка wheel в чистом venv (не workspace — чтобы не цеплял PYTHONPATH)
uv venv /tmp/reactifact-rc
/tmp/reactifact-rc/bin/python -m pip install dist/reactifact-0.5.0-py3-none-any.whl
/tmp/reactifact-rc/bin/python -c "import reactifact; print(reactifact.__version__)"
/tmp/reactifact-rc/bin/reactifact --help          # console-скрипт на месте
unzip -l dist/reactifact-0.5.0-py3-none-any.whl | grep -E "examples/|tests/|tracing/templates"

# 5) тег
git tag v0.5.0 && git push origin v0.5.0

# 6) публикация (токен PyPI в env)
uv publish --publish-url https://upload.pypi.org/legacy/
```

## Что входит в дистрибутив

`uv build` пакует только пакет `reactifact` (setuptools `packages.find` исключает
`examples`/`tests`) плюс шаблоны трейс-дашборда
(`reactifact/tracing/templates/*.html`). Примеры, тесты и docs остаются в
репозитории и служат документацией-примером.

## Откат

Сломанный `rc` чинится в следующем `rc`/релизе — историю тега не переписываем.
Патч-релизы строго обратно совместимы (§61).