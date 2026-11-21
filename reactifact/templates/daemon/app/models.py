"""Daemon artifacts — a tick in, a report out."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class Tick(BaseModel):
    sequence: int
    at: datetime


class Report(BaseModel):
    sequence: int
    text: str
    source: str = "offline"


class ReportBody(BaseModel):
    text: str
