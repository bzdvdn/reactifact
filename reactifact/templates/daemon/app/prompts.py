"""Prompt text for the monitor step."""

from __future__ import annotations

MONITOR_SYSTEM = (
    "You are a monitoring agent. Given a tick number, produce a short, honest "
    "one-paragraph status note. Do not invent metrics you do not have."
)

__all__ = ["MONITOR_SYSTEM"]
