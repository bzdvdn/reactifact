# Multi-turn & chat

A conversation is just state that persists across turns: the app enters a user
message, agents react, the reply is read back, and the whole `Context` is kept
under a `session_id` so the next turn — and the next process — sees it. This
page maps the multi-turn pieces (and the LangGraph equivalents) to reactifact.

## If you know LangGraph

| LangGraph | reactifact |
| --- | --- |
| `thread_id` + checkpointer | `session_id` + `SessionStore` (`FileKVBackend` / `SQLiteKVBackend` / `PostgreSQLKVBackend`) |
| `MessagesState` + `add_messages` reducer | `Conversation` / `ConversationMessage` (`recipes.conversation`); state is typed artifacts, versioned per change |
| `graph.invoke(input, config)` | `Chat.stream()/invoke()`, or `run_message()` for a custom loop |
| `interrupt()` / `Command(resume=…)` | `human.ask(...)` → `PendingQuestion` → `human.resume(...)` |
| `get_state_history` / resume a checkpoint | `context.history()` / `context.checkout(v)` / `context.branch()` / `reactifact replay` |
| bound the transcript / summarize | `ChatMemory` → `WindowPruner` / `RollingDigestSummarizer`; `Context.compact` for history |
| `store` (long-term, cross-thread memory) | `MemoryStore` recipe (namespaced facts, TTL, `forget_source`); `SessionStore` remains conversation history — see [Recipes](recipes.md#memorystore-namespaced-long-term-memory-27) |

The difference in kind: a reducer merges opaque messages, whereas reactifact
keeps **typed, versioned artifacts** — so a turn's provenance, the ability to
`diff` two turns, and rewind are properties of the state itself.

## The canonical chat

`Chat` owns sessions, the turn loop and history; `create_chat_router`
mounts it as SSE on your FastAPI app. Configure it with types and hooks:

```python
from reactifact import Chat, Consume, RuntimeResources, create_step_group
from reactifact.checkpoints import SQLiteKVBackend
from reactifact.recipes.conversation import (
    ConversationMessage, conversation_state, create_turn,
)
from reactifact.session import SessionStore

assistant = Chat(
    store=SessionStore(SQLiteKVBackend("./sessions.db")),
    groups=[create_step_group("answer", steps=[ANSWER])],
    seed_type=ConversationMessage,
    create_message=create_turn,          # record the user message as a ConversationMessage
    reply=my_reply,                      # (ctx, msg_id) -> {"reply": ...}
    session_state=conversation_state,    # how history() reconstructs the thread
    resources=lambda: RuntimeResources(llm=my_llm),
)

await assistant.invoke("what's the refund policy?", session_id="user-1")
await assistant.history(session_id="user-1")   # the thread so far
```

A ready-made `quick.chat_agent(groups, store=…)` does the same with defaults.

The facade mirrors `Pipeline` where it matters: `Chat(traces=True|path)`
opens a `TraceStore`, `trace_sample=` applies the same sampling policy,
`lifespan=` + `async with chat:` owns long-lived resources, and
`await chat.answer(...)` returns the typed `answer_type` instead of the
transport payload.

**Per-request metadata in `create_message`.** The hook may take `(ctx, text)`,
`(ctx, text, session_id)`, or `(ctx, text, session_id, request)` (detected from
its arity). During it `ctx.request` is the same mapping passed to
`stream(..., request=...)`, so a turn's category/tenant/user can be stamped
onto the user artifact without a `ContextVar` of your own:

```python
def create_message(ctx, text, session_id):
    return ctx.create(UserQuery(
        text=text,
        session_id=session_id,
        category=ctx.request.get("category", ""),
    )).id

await assistant.invoke("...", session_id="s1", request={"category": "helm"})
```

`resources=` may also be a plain `RuntimeResources` instance when you want one
long-lived provider shared across sessions. Per-turn budget/deadline/token
tracking and tracing wrappers are installed on a shallow copy, so concurrent
turns never mutate the shared instance.

**Per-request resources.** A `resources=` factory may take `(session_id,
request)` — the same turn mapping — to build user/tenant-scoped resources per
turn instead of sharing one mutable instance:

```python
resources=lambda session_id, request: build_resources(request.get("user"))
```

`request=` is passed through unchanged (no extra keys are injected). If a turn
needs the session id, read it from the request your `request_factory` built or
from the user artifact the chat layer creates (it carries `session_id`).
`history()` is read-only and does not call the factory; a shared
`RuntimeResources` instance is still attached for a custom `session_state`.

**Outbound side effects.** If an agent in the chat records an intent with
`outbox.act(...)`, pass `dispatcher=` (and optionally `on_dispatch_error=`) —
they go straight to the `Runtime` the assistant builds per turn, so committed
actions are delivered once, after each generation's commit, exactly as in a
non-chat run:

```python
async def dispatch(context, action):
    await send_notification(action.data.payload)

Chat(..., dispatcher=dispatch)
```

Without it, a recorded `PendingAction` stays `pending` (the runtime warns) until
something drains it — see [Durability → Outbox](durability.md#outbox-in-production).

## The conversation recipe

`ConversationMessage` is a canonical `role`/`text`/`turn`/`session_id` artifact,
so you stop hand-rolling `Question`/`Answer` plus a prompt builder. `Conversation`
is generic over **your own** message model — any model with `role`/`text`:

```python
from reactifact.recipes.conversation import Conversation, ConversationMessage

conv = Conversation()                 # the default model
conv = Conversation(MyMessage)        # or your own (role/text/vendor fields)

conv.create_turn(ctx, "refund policy?", session_id="s1")
# ...agents run and write the assistant message...
history = conv.transcript(ctx, window=20)   # list[providers.Message] for a prompt
```

`transcript` renders the thread to provider `Message`s (oldest first, `window`
keeps the last N, `0` means empty); `conversation_state` is the
`session_state=` history payload; `turn` numbers come from the max recorded
turn, so pruning never restarts them. `add`/`create_turn` are app-side hooks;
inside a `Step` use `conv.add_effect(effects, ...)` or return an `Emit`.

**Most real apps have *separate* types for the two sides** (`Question` +
`FinalResponse`, `UserMsg` + `ChatReply`) with a correlation key and a non-text
assistant payload. For those, use the read-only `Transcript` + `MessageSpec`,
which merges several typed turns by role and leaves the artifacts untouched —
the shape the `repair`/`devops`/`knowledge` examples use (each defining a
`Transcript([...])` as its `session_state=`):

```python
from reactifact.recipes.conversation import MessageSpec, Transcript

view = Transcript([
    MessageSpec(Question, "user"),
    MessageSpec(FinalResponse, "assistant", render=render_answer),  # contract JSON → prose
])
history = view.messages(ctx, window=20)      # list[providers.Message]
# Chat(..., session_state=view.state)
```

`view.entries(ctx)` is the lower-level form: it returns the ordered
`TranscriptEntry(spec, artifact)` pairs, so a step that needs the artifacts
themselves (ids, versions, metadata) does not have to re-scan `Context`.

## Bounded memory

A conversation grows forever unless you bound it. `ChatMemory` on
`Chat` wires the [memory recipes](recipes.md) for you:

```python
from reactifact import Chat
from reactifact.chat import ChatMemory

assistant = Chat(
    ...,
    memory=ChatMemory(
        message_type=ConversationMessage,
        # message_types=[UserQuery, Answer],  # heterogeneous thread / marker base
        keep=20,                 # keep the last 20 raw messages (WindowPruner)
        # or, to condense instead of dropping:
        # summarize=my_summarize,      # (ctx, previous_digest, stale) -> str | None
        # summary_type=Summary,        # the digest artifact (needs a `text` field,
        # build_summary=lambda t: Summary(text=t),   # or pass a builder)
        compact_commits=200,     # also bound the commit log (Context.compact)
    ),
)
```

Use one shape or the other, not both: pruning keeps the raw window;
summarizing folds what falls out of it into a growing digest and deletes the
source messages. `compact_commits` is orthogonal — it bounds the *history*
(see [Durability & resume](durability.md)), not the working set, and needs no
`message_type`, so `ChatMemory(compact_commits=…)` is a complete policy on its
own (the safe choice when messages are correlated/typed rather than a plain
role/text log, e.g. separate `Question`/`Answer` artifact types).

When the thread really is more than one artifact type, pass
`message_types=[UserQuery, Answer]` — or a marker base (`ChatMessage`) whose
concrete types inherit it. The memory agent wakes on every listed type (and,
for a marker base, on its subclasses), so one window bounds the whole thread;
`message_type` also accepts a sequence for the same effect.

## Three kinds of memory

These are different lifecycles, not alternatives:

| What | Where it lives | Lifetime | Use it for |
| --- | --- | --- | --- |
| Conversation history | `SessionStore` + the session `Context` | per `session_id`, until deleted | continue/replay a thread; `history()` |
| Working window | `ChatMemory` (`WindowPruner` / `RollingDigestSummarizer`), `Context.compact` | the live `Context` | bound the prompt/history without growing forever |
| Long-term facts | `MemoryStore` (`recipes`) | per namespace (`user:<id>`), TTL/`forget_source` | preferences and learned facts across sessions |
| Operational traces | `TraceStore` | retention policy | debugging/quality monitoring, not user memory |

`SessionStore` is *what happened in this conversation*; `MemoryStore` is
*what the agent should remember about this user*; `ChatMemory` is *how much of
the transcript to keep in the prompt*. Keep them separate.

## Human-in-the-loop turns

A turn can pause for a human without any special machinery — asking is just an
effect, and the answer is just another patch:

```python
# inside a step
human.ask("Approve this estimate?", kind="approval")
# later, from your API, when the human answers:
ctx.resume(question_id, "approved")   # emits an update → agents react
```

The pending question is state, so it survives a restart like everything else —
a client can come back with the answer on a later turn. See the `devops` and
`repair` examples.

## Rewind and resume

- **Continue** an interrupted run: reopen the session and `arun()` — pending
  triggers are restored ([Durability & resume](durability.md)).
- **Rewind** to an earlier point: `context.checkout(version)` (refused below a
  compaction baseline) or `context.branch()` to explore an alternative without
  touching the original.
- **Inspect** a turn: `context.diff(v1, v2)` shows exactly what changed,
  `reactifact replay` reconstructs *why*.

## Related

- [Durability & resume](durability.md) — sessions, at-least-once, compaction.
- [Recipes](recipes.md) — the memory recipes and the conversation recipe.
- [`examples/chat`](../../examples/chat) — the single-model recipe, runnable
  offline.
- [Quickstart](quickstart.md#3-a-session-persisted-chat-bot) — the chat bot in
  a few lines.
