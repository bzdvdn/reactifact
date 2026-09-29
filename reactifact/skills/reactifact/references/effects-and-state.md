# Effects, state and provenance

Reference material for the `reactifact` skill. Read it when a produce needs to
read more than its trigger, when ids matter, or when wiring multi-turn state.

## Reading state

The `Context` is the queryable artifact graph. All methods are safe to call
inside a produce:

| Call | Returns |
| --- | --- |
| `context.latest(T)` | the most recent artifact of type `T`, or `None` |
| `context.list_artifacts(T)` | every artifact of type `T` |
| `context.get(id)` | one artifact by id |
| `context.related(id, "supported_by")` | artifacts on the other end of a relation |
| `context.relations(source_id=..., relation=...)` | edges of the graph |
| `context.view()` / `context.snapshot()` | a read-only view / a stable snapshot |

```python
# not-run: illustrative
async def produce(self, call):
    query = call.trigger.data.text
    evidence = call.context.list_artifacts(Evidence)
    best = max(evidence, key=lambda a: a.data.score, default=None)
    if best is not None:
        answer = call.effects.create(Answer(text=synthesize(query, best.data)))
        answer.link("supported_by", best)
```

## Ids and idempotency

Every artifact has a version and a content hash; re-running a turn does not
duplicate work when ids are stable. Prefer a deterministic id derived from the
inputs (`f"scouted:{query_id}"`) over a random one, and use `create_once` when a
produce may legitimately re-wake:

```python
# not-run: illustrative
call.effects.create_once(SearchDone(query_id=qid), id=f"search:{qid}")
```

## Provenance is the point

A relation is a first-class artifact edge, not a string in the prose. Link the
answer to what justifies it, and link derived claims to their source:

```python
# not-run: illustrative
answer = call.effects.create(Answer(text=...))
claim = call.effects.create(Claim(text=..., confidence=0.8))
claim.link("derived_from", evidence)
answer.link("supported_by", evidence)
```

Downstream code (and `reactifact.eval`) can then check grounding with
`context.related(answer.id, "supported_by")` instead of trusting the text.

## Multi-turn and sessions

Conversation is a sequence of turns over one evolving context, not a bag of
messages. Keep the same `Context`/`Session` across turns so later turns see
everything earlier ones produced:

```python
# not-run: illustrative
session = Session(store=...)         # in-memory by default
runtime = Runtime(ctx, agents=agents, session=session)
ctx.create(Question(text="first turn"))
asyncio.run(runtime.arun())
ctx.create(Question(text="second turn"))   # same ctx/session
asyncio.run(runtime.arun())
```

See `docs/en/chat.md` for `Conversation`, `Transcript`, `ChatMemory` and
compaction.

## Persisted type identity

A saved artifact's type is a string — by default the model's **qualified name**
(`module.Qualname`), written automatically and resolved by import on load. You
do **not** call anything to register a type; a module-level `BaseModel`
round-trips as-is. Reach for `reactifact.register_type`/a `TYPE_ID` classvar only
to pin a stable id when you **rename or move** a model, to keep old ids loading
via `aliases`, or to add a `migrate` hook for a field rename. A model that is not
importable in a new process (`__main__`, a notebook, a class inside a function,
`pydantic.create_model`) is the one case that *needs* an explicit `type_id`.

## Determinism

Given the same seeds and the same LLM responses, a run is reproducible: the
runtime is a fixpoint over a set of enabled triggers. For tests and evals this
means you can record LLM calls once (`ReplayLLM`) and assert on the *artifacts*
afterwards — see the `reactifact-testing` and `reactifact-eval` skills.
