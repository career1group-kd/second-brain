"""Heading-aware chunker with sliding window for long sections.

Uses tiktoken when available (preferred) and falls back to a whitespace-based
approximation if the encoding cannot be loaded (e.g. offline test environments).
"""

from __future__ import annotations

from functools import lru_cache

from .models import Chunk, Note, Section


class _TokenizerProtocol:
    def encode(self, text: str) -> list[int]: ...
    def decode(self, tokens: list[int]) -> str: ...


class _WhitespaceTokenizer:
    """Fallback when tiktoken's encoding cannot be loaded."""

    def encode(self, text: str) -> list[int]:
        return list(range(len(text.split())))

    def decode(self, tokens: list[int]) -> str:
        # Not used in the fallback path; window splitting falls back to
        # whitespace slicing in _window().
        raise NotImplementedError


@lru_cache(maxsize=1)
def _encoder():
    try:
        import tiktoken

        return tiktoken.get_encoding("cl100k_base")
    except Exception:  # network unavailable, missing encoding, etc.
        return _WhitespaceTokenizer()


def count_tokens(text: str) -> int:
    return len(_encoder().encode(text))


def _section_prefix(note_title: str, heading_path: list[str]) -> str:
    # Drop a leading H1 that duplicates the note title (common Obsidian pattern).
    if heading_path and heading_path[0].strip().lower() == note_title.strip().lower():
        heading_path = heading_path[1:]
    parts = [note_title, *heading_path]
    parts = [p for p in parts if p]
    return " > ".join(parts)


def _embed_text(note_title: str, heading_path: list[str], content: str) -> str:
    prefix = _section_prefix(note_title, heading_path)
    if prefix:
        return f"{prefix}\n\n{content}"
    return content


def _window(text: str, window_tokens: int, overlap_tokens: int) -> list[str]:
    enc = _encoder()
    if isinstance(enc, _WhitespaceTokenizer):
        words = text.split()
        if len(words) <= window_tokens:
            return [text]
        step = max(1, window_tokens - overlap_tokens)
        chunks: list[str] = []
        for start in range(0, len(words), step):
            end = start + window_tokens
            chunks.append(" ".join(words[start:end]))
            if end >= len(words):
                break
        return chunks

    tokens = enc.encode(text)
    if len(tokens) <= window_tokens:
        return [text]
    chunks_t: list[str] = []
    step = max(1, window_tokens - overlap_tokens)
    for start in range(0, len(tokens), step):
        end = start + window_tokens
        chunk_tokens = tokens[start:end]
        chunks_t.append(enc.decode(chunk_tokens))
        if end >= len(tokens):
            break
    return chunks_t


def chunk_section(
    note_title: str,
    section: Section,
    *,
    max_tokens: int,
    window_tokens: int,
    overlap_tokens: int,
) -> list[tuple[list[str], str]]:
    """Return a list of (heading_path, content) pairs for a section."""
    body = section.body.strip()
    if not body:
        return []
    if count_tokens(body) <= max_tokens:
        return [(section.heading_path, body)]
    return [(section.heading_path, w) for w in _window(body, window_tokens, overlap_tokens)]


def chunk_note(
    note: Note,
    *,
    max_tokens: int = 800,
    window_tokens: int = 500,
    overlap_tokens: int = 80,
) -> list[Chunk]:
    chunks: list[Chunk] = []
    idx = 0
    for section in note.sections:
        for heading_path, content in chunk_section(
            note.title,
            section,
            max_tokens=max_tokens,
            window_tokens=window_tokens,
            overlap_tokens=overlap_tokens,
        ):
            chunks.append(
                Chunk(
                    note_path=note.relative_path,
                    chunk_idx=idx,
                    heading_path=list(heading_path),
                    content=content,
                    embed_text=_embed_text(note.title, heading_path, content),
                )
            )
            idx += 1
    return chunks
