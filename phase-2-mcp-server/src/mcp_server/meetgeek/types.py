"""Pydantic models for MeetGeek webhook payloads."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Attendee(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str
    email: str | None = None


class TranscriptLine(BaseModel):
    model_config = ConfigDict(extra="ignore")
    speaker: str
    timestamp: str | None = None
    text: str


class MeetingPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    meeting_id: str
    title: str
    started_at: datetime
    ended_at: datetime | None = None
    duration_seconds: int = 0
    language: str = "de"
    meeting_type: str = "sync"
    attendees: list[Attendee] = Field(default_factory=list)
    summary: str = ""
    action_items: list[str] = Field(default_factory=list)
    transcript: list[TranscriptLine] = Field(default_factory=list)
    audio_url: str | None = None


class MatchResult(BaseModel):
    matched: list[tuple[str, str]] = Field(default_factory=list)  # (attendee_name, person_path)
    unrecognized: list[str] = Field(default_factory=list)
