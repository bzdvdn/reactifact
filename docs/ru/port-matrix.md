# Матрица портов — канонические паттерны агентов на reactifact

Какие классические примеры LangGraph / LangChain / CrewAI / AutoGen / Haystack
/ DSPy мы выражаем и как. Каждая строка сопоставляет каноническую идею с нашим
идиомом и конкретным примером (`examples/`).

| Каноническая идея | Где показана | Наш идиом | Пример |
| --- | --- | --- | --- |
| Цикл инструментов (ReAct) | LangGraph, LangChain | `LLMAgent`/`HITLLMAgent` + `ToolUse`/`ToolUseHITL`, `FunctionTool` | `devops` |
| HITL-одобрение инструмента | LangGraph, CrewAI | `effects.ask` → `effects.resume` (§60) | `devops`, `supervisor` |
| Reflection (генерируй→критикуй→перегенерируй) | LangGraph | `recipes.ReflectionLoop` — рецепт владеет капом раундов/порогом принятия/детекцией завершения, домен даёт `draft`/`critique`/`rewrite`/`finish` | `reflection` (`main.py` вручную, `main_recipe.py` на рецепте) |
| Map-reduce (фан-паут, затем агрегат) | LangChain/LangGraph, Haystack | чанки-артефакты → produce по чанку → гард combine | `map_reduce` |
| Роутер/супервизор/ролевые агенты | CrewAI, AutoGen | `recipes.Router` (классификация + детерминированный fallback) + `recipes.ApprovalGate` (HITL-подтверждение, `kind="approve"`) + свои produce для специалиста | `supervisor` (`main.py` вручную, `main_recipe.py` на рецепте) |
| Суммаризация памяти разговора | LangChain, LangGraph | артефакты `Msg` + `context.view` + summarizer-produce | `summarize` |
| Time-travel / ветвление по чекпойнтам | LangGraph | `Context.branch()`, параллельные рантаймы, трёхсторонний `merge()` | `time_travel` |
| RAG (retrieve→augment→generate) | LangChain, Haystack, LlamaIndex | sources + `fan_out_sources` + `materialize_doc` + evidence→claims | `knowledge`, `research` |
| Структурный вывод / extraction / роутер | LangChain | `StructuredLLM` / `PromptTemplate` / `llm_reply` | везде |
| Стадийный пайплайн с перепланированием (state machine на `stage`; план строится одним LLM-вызовом целиком, не пошаговым циклом ниже — это не `PlanExecute`) | LangGraph | produce, гейтящиеся на `Project.stage` + `changed_fields`/`earliest_stage`/`downstream_fields` (изменить→пересобрать) | `repair` |
| Plan-and-execute (канонический порт) | LangChain/AutoGPT | `recipes.PlanExecute` — рецепт владеет порядком/гейтингом/идемпотентным re-entry/детекцией завершения (поддерживает несколько целей одновременно), домен даёт `plan`/`execute_step`/`finish` | `plan_execute` (`main.py` вручную, `main_recipe.py` на рецепте) |
| Eval-driven разработка (DSPy) | DSPy | многоуровневые метрики `reactifact.eval` (§56) | `examples` + тесты |
| Бюджет инструментов / честность сбоя | — | `Budget` + детерминированные фолбэки, пути `None` (§59) | `devops`, `repair` |

Всё выше работает **офлайн** (детерминированные фолбэки) и, с моделью через
`.env`, использует настоящий LLM — см. `docs/ru/effects.md` о ментальной модели
и `docs/ru/recipes.md` о строительных блоках.