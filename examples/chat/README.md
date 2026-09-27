# chat — a minimal single-model multi-turn conversation (offline)

Shows the *simple* multi-turn shape: one message model
(`recipes.conversation.ConversationMessage`) that both sides write.
`Conversation` owns appending turns (`create_turn`) and rendering the thread
(`transcript` / `state`); `ChatMemory(keep=…)` bounds the raw thread with
`WindowPruner` — all wired into `ChatAssistant`, no LLM and no API key.

For the *other* shape — separate question/answer artifact types, which the
`repair`/`devops`/`knowledge` examples have — see `Transcript` +
`MessageSpec` in [docs/en/chat.md](../../docs/en/chat.md).

## Run

```bash
.venv/bin/python -m examples.chat.main
```

```text
You: what's the refund policy?
AI:  [echo] what's the refund policy?
You: and the pricing?
AI:  [echo] and the pricing?
...
thread kept: 4 (ChatMemory keep=4)
transcript (the window that survives):
       user: where is my order?
  assistant: [echo] where is my order?
       user: thanks!
  assistant: [echo] thanks!
```

## What to look at

- `build()` — `Conversation()`, then `ChatAssistant(create_message=conv.create_turn,
  session_state=conv.state, memory=ChatMemory(message_type=ConversationMessage,
  keep=KEEP))`. That is the whole wiring: turns are recorded and rendered by
  the recipe, and the thread stays bounded.
- `Echo` — a plain `Produce`; swap it for an LLM agent and the rest is
  unchanged.
- `run()` — used by `tests/test_chat_example.py`, so the example is covered in
  CI.
