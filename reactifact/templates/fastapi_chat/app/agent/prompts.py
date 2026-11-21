"""Prompt text lives here, not inside steps.

Keeping prompts as plain module constants makes them reviewable, diffable and
reusable; a step imports the prompt it needs.
"""

from __future__ import annotations

ANSWER_SYSTEM = (
    "You are a concise technical assistant. Answer the user's question in plain "
    "language. If you do not know, say so instead of guessing."
)

__all__ = ["ANSWER_SYSTEM"]
