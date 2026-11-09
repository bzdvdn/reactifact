# Матрица портов — канонические паттерны агентов на reactifact

Какие классические примеры LangGraph / LangChain / CrewAI / AutoGen / Haystack
/ DSPy мы выражаем и как. Каждая строка сопоставляет каноническую идею с нашим
идиомом и конкретным примером (`examples/`).

| Каноническая идея | Где показана | Наш идиом | Пример |
| --- | --- | --- | --- |
| Цикл инструментов (ReAct) | LangGraph, LangChain | `LLMAgent`/`HITLLMAgent` + `ToolUse`/`ToolUseHITL`, `FunctionTool` | `devops` |
| HITL-одобрение инструмента | LangGraph, CrewAI | `effects.ask` → `effects.resume` (§60) | `devops` |
| Time-travel / ветвление по чекпойнтам | LangGraph | `Context.branch()`, параллельные рантаймы, трёхсторонний `merge()` | `forklab` |
| RAG (retrieve→augment→generate) | LangChain, Haystack, LlamaIndex | sources + `fan_out_sources` + `materialize_doc` + evidence→claims | `knowledge` |
| Структурный вывод / extraction / роутер | LangChain | `StructuredLLM` / `PromptTemplate` / `llm_reply` | везде |
| Стадийный пайплайн с перепланированием (state machine на `stage`; план строится одним LLM-вызовом целиком, не пошаговым циклом ниже — это не `PlanExecute`) | LangGraph | produce, гейтящиеся на `Project.stage` + `changed_fields`/`earliest_stage`/`downstream_fields` (изменить→пересобрать) | `repair` |
| Eval-driven разработка (DSPy) | DSPy | многоуровневые метрики `reactifact.eval` (§56) | `examples` + тесты |
| Бюджет инструментов / честность сбоя | — | `Budget` + детерминированные фолбэки, пути `None` (§59) | `devops`, `repair` |

Всё выше работает **офлайн** (детерминированные фолбэки) и, с моделью через
`.env`, использует настоящий LLM — см. `docs/ru/effects.md` о ментальной модели
и `docs/ru/recipes.md` о строительных блоках.
