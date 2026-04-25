"""Pydantic models for the ingestion pipeline."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Section(BaseModel):
    """A markdown section split off by heading."""

    heading_path: list[str] = Field(default_factory=list)
    body: str = ""


class Note(BaseModel):
    """A parsed markdown file."""

    relative_path: str
    title: str
    frontmatter: dict[str, Any] = Field(default_factory=dict)
    sections: list[Section] = Field(default_factory=list)


class Chunk(BaseModel):
    """A unit of text destined for embedding + storage."""

    note_path: str
    chunk_idx: int
    heading_path: list[str]
    content: str
    embed_text: str

    model_config = ConfigDict(frozen=False)


class ChunkPayload(BaseModel):
    """The payload stored alongside the vector in Qdrant."""

    path: str
    title: str
    type: str | None = None
    project: str | None = None
    status: str | None = None
    tags: list[str] = Field(default_factory=list)
    headings: list[str] = Field(default_factory=list)
    attendees: list[str] | None = None
    chunk_idx: int = 0
    content: str
    hash: str
    updated: str

    @classmethod
    def from_chunk(
        cls,
        chunk: Chunk,
        note: Note,
        chunk_hash: str,
        updated: datetime,
    ) -> ChunkPayload:
        fm = note.frontmatter
        tags = fm.get("tags") or []
        if isinstance(tags, str):
            tags = [tags]
        attendees = fm.get("attendees")
        if isinstance(attendees, str):
            attendees = [attendees]
        return cls(
            path=note.relative_path,
            title=note.title,
            type=fm.get("type"),
            project=fm.get("project"),
            status=fm.get("status"),
            tags=list(tags),
            headings=list(chunk.heading_path),
            attendees=list(attendees) if attendees else None,
            chunk_idx=chunk.chunk_idx,
            content=chunk.content,
            hash=chunk_hash,
            updated=updated.isoformat(),
        )
