# knowledge — multi-source chat with evidence-backed answers

An English demo that answers questions over local docs (guide/pricing) and a
CSV cost table: search → evidence → claim verification → answer with source and
provenance (§17, §34-§36), plus deterministic calculation over structured data
(§29, §67) and a keyword-triggered skill for reporting that calculation
(§67, `reactifact.recipes.skills`).

The text corpus is built and queried through **`reactifact.knowledge`**
(`knowledge/index.py`): a `KnowledgePipeline` over `docs/guide` + `docs/pricing`
with an incremental SQLite index and a deterministic embedder by default. The
CSV cost table stays a structured `CSVSource`, because arithmetic must remain
deterministic — not text.

## Structure

```
knowledge/
├── chat.py           # CLI + build_resources (knowledge + CSV + skills + env LLM)
├── index.py          # reactifact.knowledge backend (pipeline, DocStore, IndexSource)
├── eval.py            # offline retrieval metrics (no API key)
├── web.py            # FastAPI/SSE app + traces + /knowledge status
├── models.py         # artifact types (Question, Evidence, Claim, Answer, …)
├── agents.py    # Agent containers + the shared PIPELINE
├── steps/            # staged pipeline (each Step is its concern):
│   ├── common.py     #   routing regexes, chat memory, claim/verdict helpers
│   ├── router.py     #   greeting / direct reply vs research
│   ├── search.py     #   fan-out over sources + text/table materialization
│   ├── evidence.py   #   extraction + deterministic claim verification (§35-§36)
│   ├── calc.py       #   CSV aggregation → Calculation (§29, §67)
│   └── lifecycle.py  #   turn overseer + answer builder (loads matched skills)
├── web/index.html    # UI templates
├── docs/             # English documentation fixtures (guide / pricing / costs)
├── skills/           # cost-reporting.md — a Claude-Skills-shaped instruction
├── scenarios/        # ScenarioLab scenarios (see below; fixtures in scenarios/data/)
└── .env.example      # keys for a real model
```

## Scenarios

`scenarios/` holds `reactifact.testing.ScenarioLab` scenarios — a separate track
from the unit tests in `tests/`, run through the `reactifact scenario` CLI so a
plain `pytest` run never needs a model key or a network connection. One of
them locks down exactly the flagship question below — the GPU total (3580)
is asserted straight from `Calculation.value`, no model involved. A
multi-turn scenario (`lab.scenario()`/`.turn()`) also proves chat memory
still holds the first question and its answer by the time a follow-up is
asked, all without a model:

```bash
.venv/bin/python -m reactifact scenario examples.knowledge.scenarios
.venv/bin/python -m reactifact scenario examples.knowledge.scenarios --mode record   # real model call
.venv/bin/python -m reactifact scenario examples.knowledge.scenarios --mode replay   # offline, from the fixture
```

## Run

```bash
.venv/bin/python examples/knowledge/chat.py
.venv/bin/python examples/knowledge/web.py     # SSE UI on :8000
.venv/bin/python -m examples.knowledge.eval    # offline retrieval metrics
# http://127.0.0.1:8000/           — chat UI
# http://127.0.0.1:8000/traces     — agent traces (question/answer columns)
# http://127.0.0.1:8000/knowledge/ — ingestion status (sources, counts, errors)
```

Without an LLM key the demo runs on deterministic fallbacks (§68); with a key
(in `.env`) generation goes through the configured provider.

Try: `how much does gpu cost in total?` — it exercises every stage (text
search, structured-data search, deterministic calculation, verification) in
one turn.

## How it flows — no graph to draw

There is no orchestration to wire up: each agent declares what it
`consumes`/`produces`, and the runtime derives execution from state changes.

![Evidence and Calculation are produced by two agent groups that never reference each other; answer_builder only fires once both exist, and links Answer supported_by each.](../../docs/img/knowledge-flow.svg)

The picture above simplifies the flagship question (`how much does gpu cost
in total?`) down to its two independent branches. This is the actual static
map of all 9 agents in this demo
(`python -m reactifact graph examples.knowledge.agents`):

```mermaid
flowchart LR
    subgraph SG["knowledge blueprint"]
        direction LR
        A0["answer_builder<br/>BuildAnswer"]
    ART0["ResearchTurn"]
        ART0 -.->|Consume| A0
    ART1["Answer"]
        A0 ==>|creates| ART1
        A1["calculator<br/>CalculateAggregate"]
    ART2["Spreadsheet"]
        ART2 -.->|Consume| A1
    ART3["Calculation"]
        A1 ==>|creates| ART3
        A2["evidence_builder<br/>ExtractEvidence"]
    ART4["TypedDoc"]
        ART4 -.->|Consume| A2
    ART5["Evidence"]
        A2 ==>|creates| ART5
        A3["planner<br/>PlannerReply · PlannerTurn"]
    ART6["UserQuery"]
        ART6 -.->|Consume| A3
    ART7["ChatReply"]
        A3 ==>|creates| ART7
        A3 ==>|creates| ART0
        A4["progress_evaluator<br/>EvaluateTurn"]
        ART0 -.->|Consume| A4
    ART8["SourceRef"]
        ART8 -.->|Consume| A4
        ART4 -.->|Consume| A4
        ART5 -.->|Consume| A4
    ART9["Claim"]
        ART9 -.->|Consume| A4
        ART2 -.->|Consume| A4
        ART3 -.->|Consume| A4
    ART10["SearchDone"]
        ART10 -.->|Consume| A4
        ART1 -.->|Consume| A4
        A4 ==>|lifecycle| ART0
        A5["resolver<br/>ResolveRef"]
        ART8 -.->|Consume| A5
        A5 ==>|creates| ART4
        A6["search_scout<br/>ScoutSources · Step"]
        ART0 -.->|Consume| A6
        A6 ==>|creates| ART8
        A6 ==>|creates| ART10
        A7["table_resolver<br/>ResolveTable"]
        ART8 -.->|Consume| A7
        A7 ==>|creates| ART2
        A8["verifier<br/>VerifyClaims"]
        ART5 -.->|Consume| A8
        A8 ==>|creates| ART9
    end
```

## Provenance for a real question

Ask `how much does gpu cost in total?` and the answer is not a string pulled
from nowhere — every derived artifact links back to what produced it (§34).
Below is the actual relation graph from that run (via
`python -m reactifact context <sessions-db>`; node ids shortened here for
readability — see [docs/en/viz.md](../../docs/en/viz.md) to render your own):

```mermaid
flowchart TD
    subgraph SR["SourceRef"]
        SR1["guide/api.md"]
        SR2["pricing/tiers.md"]
        SR3["costs/gpu_usage.csv"]
    end
    subgraph TD["TypedDoc"]
        TD1["api.md"]
        TD2["tiers.md"]
    end
    subgraph SS["Spreadsheet"]
        SS1["gpu_usage.csv"]
    end
    subgraph EV["Evidence"]
        EV1["from api.md"]
        EV2["from tiers.md"]
    end
    subgraph CA["Calculation"]
        CA1["sum(gpu cost usd) = 3580"]
    end
    subgraph CL["Claim"]
        CL1["api.md #0"]
        CL2["api.md #1"]
        CL3["tiers.md #0"]
        CL4["tiers.md #1"]
        CL5["tiers.md #2"]
    end
    ANS["Answer"]

    TD1 -->|resolved_from| SR1
    TD2 -->|resolved_from| SR2
    SS1 -->|materialized_from| SR3
    EV1 -->|extracted_from| TD1
    EV2 -->|extracted_from| TD2
    CA1 -->|derived_from| SS1
    CL1 -->|derived_from| EV1
    CL2 -->|derived_from| EV1
    CL3 -->|derived_from| EV2
    CL4 -->|derived_from| EV2
    CL5 -->|derived_from| EV2
    ANS -->|supported_by| EV1
    ANS -->|supported_by| EV2
    ANS -->|supported_by| CA1
```

Every arrow is a real `patch.link` written by the pipeline, not a debugging
add-on — `Answer.sources` and this graph come from the same state.

## Skills — instructions loaded by the situation, not the graph

`skills/cost-reporting.md` is a Claude-Skills-shaped file: a `name`/
`description` frontmatter plus a body of procedural instructions.
`chat.py`'s `build_resources()` loads every file in `skills/` once
(`reactifact.recipes.load_skills`) into `resources.get("skills")`; `BuildAnswer`
(`steps/lifecycle.py`) only *matches* a skill against the situation when it
has a `Calculation` to report:

```python
skills = context.resources.get("skills") or []
situation = "reporting an answer backed by a number computed from structured storage (a spreadsheet/CSV table)"
for skill in match_skills(skills, situation):
    prompt += f"\n\nInstruction ({skill.name}): {skill.body}"
```

This is the same reactive shape as everything else here: a skill fires
because of *what state exists* (a `Calculation` artifact), not because the
code branches on the user's phrasing. `match_skills` is deterministic keyword
overlap (`reactifact.recipes.keyword_score`, §67) over the skill's own
description — no embeddings, no new core primitive (§61). See
[docs/en/recipes.md](../../docs/en/recipes.md#skills).