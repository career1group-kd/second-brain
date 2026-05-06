"""Pydantic schemas for note frontmatter validation (Phase 3 create_*)."""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LivingDocFrontmatter(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["living"] = "living"
    project: str
    status: Literal["active", "paused", "done"] = "active"
    created: date
    updated: date
    google_tasks_list_id: str = ""
    tags: list[str] = Field(default_factory=list)


class MeetingFrontmatter(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["meeting"] = "meeting"
    date: date
    project: str | None = None
    attendees: list[str] = Field(default_factory=list)
    unrecognized_attendees: list[str] = Field(default_factory=list)
    meeting_type: str = "sync"
    duration_minutes: int = 0
    fireflies_id: str = ""
    language: str = "de"
    audio_url: str | None = None
    created: date
    updated: date


class PersonFrontmatter(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["person"] = "person"
    role: str = ""
    company: str = ""
    email: str = ""
    relationship: str = ""
    tags: list[str] = Field(default_factory=list)
    hubspot_contact_id: str = ""
    linkedin: str = ""
    last_interaction: date
    created: date
    updated: date


class DailyFrontmatter(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["daily"] = "daily"
    date: date


class ResourceFrontmatter(BaseModel):
    model_config = ConfigDict(extra="allow")

    type: Literal["resource"] = "resource"
    tags: list[str] = Field(default_factory=list)
    created: date
    updated: date


SCHEMA_BY_TYPE = {
    "living": LivingDocFrontmatter,
    "meeting": MeetingFrontmatter,
    "person": PersonFrontmatter,
    "daily": DailyFrontmatter,
    "resource": ResourceFrontmatter,
}


def validate_frontmatter(type_: str, data: dict) -> dict:
    schema = SCHEMA_BY_TYPE.get(type_)
    if schema is None:
        raise ValueError(f"unknown type: {type_}")
    model = schema(**data)
    return model.model_dump(mode="json", exclude_none=False)
