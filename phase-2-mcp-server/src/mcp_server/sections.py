"""H2-section detection for write tools.

Operates on raw byte content so writes can splice exact ranges without
re-rendering the markdown.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

H2_RE = re.compile(rb"^##\s+(.+?)\s*#*\s*$", re.MULTILINE)


@dataclass(frozen=True)
class Section:
    name: str
    heading_start: int  # offset of the "##" character
    body_start: int  # offset just after the heading line's newline
    body_end: int  # exclusive; offset of the next H2 or end of file


def find_sections(content: bytes) -> list[Section]:
    matches = list(H2_RE.finditer(content))
    sections: list[Section] = []
    for i, m in enumerate(matches):
        name = m.group(1).decode("utf-8").strip()
        heading_start = m.start()
        line_end = content.find(b"\n", m.end())
        body_start = line_end + 1 if line_end != -1 else len(content)
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        sections.append(
            Section(
                name=name,
                heading_start=heading_start,
                body_start=body_start,
                body_end=body_end,
            )
        )
    return sections


def find_section(content: bytes, name: str) -> Section | None:
    target = name.strip().lower()
    sections = find_sections(content)
    # Exact case-sensitive match first.
    for s in sections:
        if s.name == name.strip():
            return s
    for s in sections:
        if s.name.lower() == target:
            return s
    for s in sections:
        if s.name.strip().lower() == target:
            return s
    return None


def append_to_section(content: bytes, name: str, addition: str) -> bytes:
    """Return new content with `addition` appended at the end of section `name`.

    If the section does not exist, append it at the end of the document.
    The addition is added on its own line, preserving an empty line before
    the next section.
    """
    addition_bytes = addition.encode("utf-8") if isinstance(addition, str) else addition
    if not addition_bytes.endswith(b"\n"):
        addition_bytes = addition_bytes + b"\n"

    section = find_section(content, name)
    if section is None:
        prefix = content
        if not prefix.endswith(b"\n"):
            prefix = prefix + b"\n"
        if prefix and not prefix.endswith(b"\n\n"):
            prefix = prefix + b"\n"
        return prefix + f"## {name}\n\n".encode("utf-8") + addition_bytes

    body_end = section.body_end
    # Trim trailing whitespace within the section, then add a single newline.
    head = content[:body_end].rstrip(b" \t\n") + b"\n"
    tail = content[body_end:]
    if tail and not tail.startswith(b"\n"):
        tail = b"\n" + tail
    if not head.endswith(b"\n\n") and (head.endswith(b"\n")):
        # ensure exactly one blank line of breathing room before insertion
        pass
    return head + addition_bytes + tail
