from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class Candidate(BaseModel):
    source: str = "liepin"
    candidate_id: str = ""
    name: str = ""
    title: str = ""
    location: str = ""
    experience: str = ""
    education: str = ""
    profile_url: str = ""
    resume_text: str = ""
    raw: dict[str, Any] = Field(default_factory=dict)


class ScoreResult(BaseModel):
    score: int = Field(ge=0, le=100)
    matched_keywords: list[str] = Field(default_factory=list)
    missing_keywords: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    summary: str = ""


class GreetingDecision(BaseModel):
    candidate: Candidate
    score: ScoreResult
    greeting: str
    should_greet: bool
    sent: bool = False
    dry_run: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)

