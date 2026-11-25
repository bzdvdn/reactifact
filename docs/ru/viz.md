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
reactifact graph examples.knowledge.step_groups        # все агенты модуля
python -m reactifact graph examples.knowledge.step_groups:Planner # один агент
python -m reactifact graph examples.knowledge.step_groups --check --seed Question --terminal Answer
python -m reactifact context examples/knowledge/sessions/sessions.sqlite3
python -m reactifact trace traces.db [run_id]
python -m reactifact contracts examples.knowledge.step_groups  # контракты в Markdown
python -m reactifact contracts examples.knowledge.step_groups --format json
```

- `graph` инстансирует каждый подкласс `StepGroup`, определённый в модуле (или один
  через `module:Attr`), и печатает blueprint. С `--check` вместо отрисовки
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

Страница запуска (`/traces/<run_id>`) рендерит диаграмму трассы вживую через
Mermaid, с кнопкой `copy` и исходником в раскрывающемся `<pre>`; если браузер
офлайн (нет Mermaid JS) — показывается текст исходника.