# Documentation

`reactifact` is a reactive, artifact-driven agent runtime. Start with the
concepts, then the recipes and patterns; the examples are the best real-world
reference.

## Languages

- **English** — [docs/en/](en/index.md)
- **Русский** — [docs/ru/](ru/index.md)

## English

| Page | What it covers |
| --- | --- |
| [index](en/index.md) | What reactifact is, the mental model, quick start |
| [why-reactifact](en/why-reactifact.md) | the *design argument*: why effects, why no graph, why determinism, why versioned state |
| [concepts](en/concepts.md) | Context, Artifact, Patch, Agent, Produce, provenance, HITL, budget |
| [sources](en/sources.md) | Retrieval: FileSystem, Embedding, CSV, Web; SourceRef |
| [providers](en/providers.md) | LLMs, embedders, images, speech, video — 20+ vendors |
| [recipes](en/recipes.md) | `fan_out_sources`, `materialize_doc`, `StatusMachine` |
| [patterns](en/patterns.md) | HITL tool approvals, structured LLM, fallbacks, sessions |
| [observability](en/observability.md) | Traces, SQLite store, dashboard, Langfuse, Postgres |
| [mcp](en/mcp.md) | MCP client (call remote tools) and server (expose `Tool`s/`Context` as MCP) |
| [viz](en/viz.md) | Mermaid rendering: blueprint, context, trace + `python -m reactifact` CLI |
| [replay](en/replay.md) | Record & replay (§55): ReplayLLM, state replay, `replay` CLI |
| [branching](en/branching.md) | Fork & merge (§39-§40): `branch()`, three-way `merge()`, `BranchStore` |
| [effects](en/effects.md) | The produce contract & mental model (§24): `effects`, Patch = runtime transport |
| [eval](en/eval.md) | Multi-level evaluation (§56): evidence/claim/provenance/calc/answer metrics |
| [examples](en/examples.md) | knowledge, research, medic-lab, devops, repair |
| [port-matrix](en/port-matrix.md) | how canonical patterns (LangGraph/LangChain/CrewAI/…) map to our idioms + `examples/` |
| [release](en/release.md) | versioning, changelog, build/verify/publish |
| [api](en/api.md) | Public API reference (top-level symbols) |
| [design-notes/adaptive](en/design-notes/adaptive.md) | Design notes for the adaptive scheduler (`Scheduler`, `uncertainty_policy`, §24/§26) — English only |
| [design-notes/patches](en/design-notes/patches.md) | Design notes for the effects → Patch compilation model (§12-§13) — English only |

## Русский

| Страница | О чём |
| --- | --- |
| [index](ru/index.md) | Что такое reactifact, ментальная модель, быстрый старт |
| [why-reactifact](ru/why-reactifact.md) | *дизайн-аргумент*: почему effects, почему без графа, почему детерминизм, почему версионируемое состояние |
| [concepts](ru/concepts.md) | Context, Artifact, Patch, Agent, Produce, provenance, HITL, баджеты |
| [sources](ru/sources.md) | Получение данных: FileSystem, Embedding, CSV, Web; SourceRef |
| [providers](ru/providers.md) | LLM, эмбединги, изображения, речь, видео — 20+ вендоров |
| [recipes](ru/recipes.md) | `fan_out_sources`, `materialize_doc`, `StatusMachine` |
| [patterns](ru/patterns.md) | HITL-подтверждение инструментов, structured LLM, фолбэки, сессии |
| [observability](ru/observability.md) | Трейсы, SQLite-хранилище, дашборд, Langfuse, Postgres |
| [mcp](ru/mcp.md) | MCP-клиент (вызов удалённых инструментов) и сервер (отдать `Tool`/`Context` как MCP) |
| [viz](ru/viz.md) | Рендер в Mermaid: blueprint, context, trace + CLI `python -m reactifact` |
| [replay](ru/replay.md) | Запись и воспроизведение (§55): ReplayLLM, реплей состояния, CLI `replay` |
| [branching](ru/branching.md) | Форк и слияние (§39-§40): `branch()`, трёхсторонний `merge()`, `BranchStore` |
| [effects](ru/effects.md) | Контракт produce и ментальная модель (§24): `effects`, Patch = транспорт рантайма |
| [eval](ru/eval.md) | Многоуровневая оценка (§56): метрики evidence/claim/provenance/calc/answer |
| [examples](ru/examples.md) | knowledge, research, medic-lab, devops, repair |
| [port-matrix](ru/port-matrix.md) | как канонические паттерны (LangGraph/LangChain/CrewAI/…) мапятся на наши идиомы + `examples/` |
| [release](ru/release.md) | версионирование, чейджлог, сборка/проверка/публикация |
| [api](ru/api.md) | Справочник публичного API (символы верхнего уровня) |
| [design-notes/adaptive](en/design-notes/adaptive.md) | Design-заметки об адаптивном планировщике (`Scheduler`, `uncertainty_policy`, §24/§26) — только на английском |
| [design-notes/patches](en/design-notes/patches.md) | Design-заметки о компиляции effects → Patch (§12-§13) — только на английском |