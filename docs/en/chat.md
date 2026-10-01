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
| `graph.invoke(input, config)` | `ChatAssistant.stream()/invoke()`, or `run_message()` for a custom loop |
| `interrupt()` / `Command(resume=…)` | `effects.ask(...)` → `PendingQuestion` → `effects.resume(...)` |
| `get_state_history` / resume a checkpoint | `context.history()` / `context.checkout(v)` / `context.branch()` / `reactifact replay` |
| bound the transcript / summarize | `ChatMemory` → `WindowPruner` / `RollingDigestSummarizer`; `Context.compact` for history |
| `store` (long-term, cross-thread memory) | not built in — see [Sources](sources.md) / an app-owned `SessionStore` |

The difference in kind: a reducer merges opaque messages, whereas reactifact
keeps **typed, versioned artifacts** — so a turn's provenance, the ability to
`diff` two turns, and rewind are properties of the state itself.

## The canonical chat

`ChatAssistant` owns sessions, the turn loop and history; `create_chat_router`
mounts it as SSE on your FastAPI app. Configure it with hooks:

```python
from reactifact import Consume, RuntimeResources, create_agent
from reactifact.chat import ChatAssistant
from reactifact.checkpoints import SQLiteKVBackend
from reactifact.recipes.conversation import (
    ConversationMessage, conversation_state, create_turn,
)
from reactifact.session import SessionStore

assistant = ChatAssistant(
    store=SessionStore(SQLiteKVBackend("./sessions.db")),
    agents=[create_agent("answer", consumes=[Consume(ConversationMessage)], produces=[ANSWER])],
    user_message=ConversationMessage,
    create_message=create_turn,          # record the user message as a ConversationMessage
    reply=my_reply,                      # (ctx, msg_id) -> {"reply": ...}
    session_state=conversation_state,    # how history() reconstructs the thread
    resources=lambda: RuntimeResources(llm=my_llm),
)

await assistant.invoke("what's the refund policy?", session_id="user-1")
await assistant.history(session_id="user-1")   # the thread so far
```

A ready-made `quick.chat_agent(agents, store=…)` does the same with defaults.

**Outbound side effects.** If an agent in the chat records an intent with
`effects.act(...)`, pass `dispatcher=` (and optionally `on_dispatch_error=`) —
they go straight to the `Runtime` the assistant builds per turn, so committed
actions are delivered once, after each generation's commit, exactly as in a
non-chat run:

```python
async def dispatch(context, action):
    await send_notification(action.data.payload)

ChatAssistant(..., dispatcher=dispatch)
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
keeps the last N); `conversation_state` is the `session_state=` history payload;
`turn` numbers come from the max recorded turn, so pruning never restarts them.

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
# ChatAssistant(..., session_state=view.state)
```

## Bounded memory

A conversation grows forever unless you bound it. `ChatMemory` on
`ChatAssistant` wires the [memory recipes](recipes.md) for you:

```python
from reactifact.chat import ChatAssistant, ChatMemory

assistant = ChatAssistant(
    ...,
    memory=ChatMemory(
        message_type=ConversationMessage,
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

## Human-in-the-loop turns

A turn can pause for a human without any special machinery — asking is just an
effect, and the answer is just another patch:

```python
# inside a produce
self.effects.ask("Approve this estimate?", kind="approval")
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
