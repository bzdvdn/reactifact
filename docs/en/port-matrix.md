# Port matrix — canonical agent patterns on reactifact

Which classic example from LangGraph / LangChain / CrewAI / AutoGen / Haystack
/ DSPy we express, and how. Each row maps a canonical idea to our idiom and to
a concrete example (`examples/`).

| Canonical idea | Showed by | Our idiom | Example |
| --- | --- | --- | --- |
| ReAct tools loop | LangGraph, LangChain | `LLMAgent`/`HITLLMAgent` + `ToolUse`/`ToolUseHITL`, `FunctionTool` | `devops` |
| HITL tool approval (interrupt/gate) | LangGraph, CrewAI | `effects.ask` → `effects.resume` (§60) | `devops`, `supervisor` |
| Reflection (generate→critique→regenerate) | LangGraph | `recipes.ReflectionLoop` — recipe owns round-capping/accept-threshold/completion, domain supplies `draft`/`critique`/`rewrite`/`finish` | `reflection` (`main.py` hand-rolled, `main_recipe.py` on the recipe) |
| Map-reduce (fan-out then aggregate) | LangChain/LangGraph, Haystack | chunk artifacts → per-chunk produces → combine guard | `map_reduce` |
| Router / supervisor / multi-role agents | CrewAI, AutoGen | `recipes.Router` (classify + deterministic fallback) + `recipes.ApprovalGate` (HITL sign-off, `kind="approve"`) + your own specialist produces | `supervisor` (`main.py` hand-rolled, `main_recipe.py` on the recipe) |
| Conversation memory summarization | LangChain, LangGraph | `Msg` artifacts + `context.view` + summarizer produce | `summarize` |
| Time-travel / checkpoint branching | LangGraph | `Context.branch()`, parallel runtimes, three-way `merge()` | `time_travel` |
| RAG (retrieve→augment→generate) | LangChain, Haystack, LlamaIndex | sources + `fan_out_sources` + `materialize_doc` + evidence→claims | `knowledge`, `research` |
| Structured output / extraction / router | LangChain | `StructuredLLM` / `PromptTemplate` / `llm_reply` | everywhere |
| Staged pipeline w/ replanning (a `stage`-driven state machine — its plan is one single-shot LLM call, not the step-by-step loop below; not `PlanExecute`) | LangGraph | `Project.stage`-guarded produces + `changed_fields`/`earliest_stage`/`downstream_fields` (change→rebuild) | `repair` |
| Plan-and-execute (canonical port) | LangChain/AutoGPT | `recipes.PlanExecute` — recipe owns ordering/gating/idempotent re-entry/completion (supports several concurrent goals), domain supplies `plan`/`execute_step`/`finish` | `plan_execute` (`main.py` hand-rolled, `main_recipe.py` on the recipe) |
| Evaluation-driven dev (DSPy) | DSPy | `reactifact.eval` multi-level metrics (§56) | `examples` + tests |
| Tool budget / honesty on failure | — | `Budget` + deterministic fallbacks, `None` paths (§59) | `devops`, `repair` |

Everything above runs **offline** (deterministic fallbacks) and, with a model
via `.env`, uses the real LLM — see `docs/en/effects.md` for the mental model,
and `docs/en/recipes.md` for the building blocks.