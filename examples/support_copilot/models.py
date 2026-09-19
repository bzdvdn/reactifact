"""Artifacts for support_copilot."""

from pydantic import BaseModel, Field


class Question(BaseModel):
    text: str


class Doc(BaseModel):
    text: str
    locator: str = ""


class SearchDone(BaseModel):
    """Search outcome marker: `count == 0` means nothing matched → escalate."""

    owner_id: str
    count: int


class Reply(BaseModel):
    """A grounded reply (with citations) or an escalated one (from a human)."""

    text: str
    citations: list[str] = Field(default_factory=list)
    escalated: bool = False
