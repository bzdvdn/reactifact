# Agent skills

reactifact поставляет **Agent Skills** — файлы `SKILL.md`, которые кодирующие
агенты (Claude Code, Codex, Cursor, Gemini CLI, GitHub Copilot, …) подгружают
по требованию, чтобы писать код на reactifact так, как задумано. Они лежат
внутри пакета (`reactifact/skills/`), поэтому установленные скиллы всегда
совпадают с версией reactifact.

Для этой библиотеки это важнее, чем для типичной: парадигма reactifact
(артефакты, produce/consume, эффекты, провенанс) не похожа на
mutation-ориентированные агентные фреймворки, и модель, обученная на них, иначе
напишет императивный код с состоянием, воюющий с рантаймом. Скиллы кодируют
ментальную модель и типовые ошибки — а каждый исполняемый блок кода в них
прогоняется в CI, поэтому они не гниют незаметно.

## Установка

```bash
python -m reactifact skills list                      # что доступно
python -m reactifact skills install                    # -> .agents/skills/
python -m reactifact skills install --target both      # ещё и .claude/skills/
python -m reactifact skills install reactifact-agents  # один скилл
python -m reactifact skills show reactifact            # показать SKILL.md
```

`.agents/skills/` — формат, который читают Codex, Cursor, Gemini CLI и Copilot;
Claude Code читает ещё и `.claude/skills/`. Закоммитьте установленную папку,
чтобы вся команда получала одинаковые подсказки.

## Скиллы

| Скилл | Для чего |
| --- | --- |
| `reactifact` | Точка входа: ментальная модель, три инварианта, рабочая петля (design → implement → wire → test → evaluate), таблица маршрутизации и топ-ошибки. Читать первым. |
| `reactifact-agents` | Глубокий авторинг агентов и produce: варианты `Consume` (`by_status`, `by_field`, `JoinConsume`, `AbsentConsume`, `CorrelatedConsume`), все эффекты, id/идемпотентность, провенанс, планирование. |
| `reactifact-llm` | LLM-шаги: провайдеры, структурный вывод в типизированные артефакты, `StructuredGenerateAgent`/`LLMAgent`/HITL, `@tool`, промпты, бюджеты токенов/стоимости. |
| `reactifact-rag` | Ретрив: источники (filesystem, CSV, web, embeddings), `fan_out_sources`, `materialize_doc` и готовые рецепты (router, plan-execute, reflection, memory). |
| `reactifact-testing` | Детерминированные тесты через `ScenarioLab`: ассерты, инъекция сбоев, record/replay, golden-снимки, pytest-плагин и CLI `scenario`. |
| `reactifact-eval` | Оценка качества по датасету: многоуровневые метрики, сопоставление типизированных траекторий, LLM-as-judge и CI-гейт. |
| `reactifact-observability` | Трейсы и `TraceStore`, метрики Prometheus, сессии/устойчивость, детерминированный replay и ветвление, CLI graph/trace. |
| `reactifact-from-langchain` | Порт кода LangChain/LangGraph: карта понятий и пошаговый рецепт миграции. |

## Структура

Каждый скилл — `reactifact/skills/<name>/SKILL.md` с YAML-frontmatter
(`name`, `description`) и телом-workflow; объёмный справочный материал лежит в
папке `references/` скилла и читается только при необходимости. Блок
```` ```python ```` исполняется в CI, если не начинается с `# not-run`
(иллюстративные фрагменты), поэтому примеры всегда валидны для текущего API.
