# Визуализация и CLI

`reactifact.viz` рендерит систему в **Mermaid**. Рисовать граф исполнения нечего —
runtime выводит исполнение из изменений состояния, — поэтому две честные
диаграммы: *статическая карта* и *живое состояние*:

| Функция | Что рисует |
| --- | --- |
| `blueprint(agents)` | статическая карта: типы артефактов = ноды, агенты = рёбра (`Consume` / `creates` / `lifecycle`) |
| `context_to_mermaid(context)` | живой граф провенанса: артефакты сгруппированы по типам, связи `patch.link` |
| `trace_to_mermaid(trace)` | один запуск как `sequenceDiagram`: спаны по времени, записи/чтения, LLM-вызовы |
| `trace_provenance_to_mermaid(trace)` | **граф доказательств** запуска: записанные артефакты как ноды, рёбра `patch.link` (§34, §54) |

Все три — чистые функции, возвращающие строку, без зависимостей: вставляйте вывод
в GitHub, Notion или [mermaid.live](https://mermaid.live).

## CLI

```bash
python -m reactifact run app:pipeline "почему checkout недоступен?"
reactifact graph examples.knowledge.agents        # все агенты модуля
python -m reactifact graph examples.knowledge.agents:Planner # один агент
python -m reactifact graph examples.knowledge.agents --check --seed UserQuery --terminal Answer
python -m reactifact context examples/knowledge/sessions/sessions.sqlite3
python -m reactifact trace traces.db [run_id]
python -m reactifact contracts examples.knowledge.agents  # контракты в Markdown
python -m reactifact contracts examples.knowledge.agents --format json
reactifact check app.pipeline:pipeline --scenarios app.scenarios
reactifact daemon app.pipeline:pipeline --seed app.seeds:next_tick --interval 60
reactifact new agent my-agent
reactifact dev app.main:app
reactifact demo --web
```

- `validate`, `graph --check` и `check` читают `[tool.reactifact]` из
  `pyproject.toml` (`agents`, `seeds`, `terminal`, `budget`, `scenarios`,
  `evals`, `overall`, `gates`), если соответствующие флаги не заданы; флаги
  приоритетнее.
- `check` — единый quality-gate: статические контракты, любое число
  `@scenario`-модулей и опциональный eval `Dataset` против `Pipeline`
  (`--gate KEY=VALUE`, `--overall`); при провале стадии выходит с ненулевым
  кодом.
- `daemon` крутит `Pipeline` по интервалу (`--seed module:fn` сеет каждый тик);
  `new agent` создаёт минимальный one-file pipeline; `dev` поднимает ASGI-апп
  с reload и печатает URL приложения/`/contracts`/`/traces`; `demo --web`
  показывает demo без ключей в браузере.
- `graph` находит module-level экземпляры `Agent` (и инстансирует legacy
  подклассы) или берёт один через `module:Attr`, затем печатает blueprint. С `--check` вместо отрисовки
  запускает `validate_contracts`: неизвестные входы — errors (exit 1), dead
  outputs/unreachable/cycles — warnings; `--seed TYPE`/`--terminal TYPE` можно
  повторять.
- `contracts` печатает объявленную поверхность (шаги, consumes/produces, tools,
  timeout/retry) в Markdown или JSON; без запуска и LLM.
- `context` читает сохранённую сессию из KV-бэкенда (директория файлов или
  SQLite) и печатает её граф провенанса; `--session` выбирает сессию,
  `--limit` ограничивает число артефактов.
- `trace` читает запуск из базы `TraceStore` (по умолчанию — последний запуск).

## В дашборде

Приложение может смонтировать те же контракты как страницу через
`reactifact.contracts.web.create_contracts_router(agents)` — `/contracts` (HTML), `/api/contracts`
(JSON) и `/contracts.md` (Markdown). Это opt-in, с поддержкой Basic auth.

Страница запуска (`/traces/<run_id>`) рендерит диаграмму трассы вживую через
Mermaid, с кнопкой `copy` и исходником в раскрывающемся `<pre>`; если браузер
офлайн (нет Mermaid JS) — показывается текст исходника.