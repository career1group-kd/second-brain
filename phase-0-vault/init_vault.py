"""Initialize an Obsidian vault skeleton for the Second Brain.

Idempotent generator: skips files that already exist unless --force is passed.
Creates folder structure, templates, convention docs, and seed living docs.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from textwrap import dedent

VAULT_DIRS: tuple[str, ...] = (
    "00_Inbox",
    "10_Projects",
    "20_Areas",
    "30_Resources",
    "40_Archive",
    "50_Daily",
    "50_Daily/meetings",
    "60_MOCs",
    "70_People",
    "99_Meta",
    "99_Meta/Templates",
)


def write_file(path: Path, content: str, force: bool) -> bool:
    """Write content to path. Returns True if written, False if skipped."""
    if path.exists() and not force:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return True


def make_dirs(root: Path) -> None:
    for d in VAULT_DIRS:
        (root / d).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

TEMPLATE_LIVING_DOC = dedent("""\
    ---
    type: living
    project: "<% tp.file.title %>"
    status: active
    created: <% tp.date.now("YYYY-MM-DD") %>
    updated: <% tp.date.now("YYYY-MM-DD") %>
    google_tasks_list_id: ""
    tags: []
    ---

    # <% tp.file.title %>

    ## Status & Kontext

    ## Architektur & Entscheidungen

    ## Offene Fragen

    ## Recent Insights

    ## TODOs

    ## Conversation Log
    """)

TEMPLATE_MEETING = dedent("""\
    ---
    type: meeting
    date: <% tp.date.now("YYYY-MM-DD") %>
    project: null
    attendees: []
    unrecognized_attendees: []
    meeting_type: "sync"
    duration_minutes: 0
    fireflies_id: ""
    language: "de"
    created: <% tp.date.now("YYYY-MM-DD") %>
    updated: <% tp.date.now("YYYY-MM-DD") %>
    ---

    # <% tp.file.title %>

    ## Summary

    ## Action Items

    ## Transcript

    <details>
    <summary>Click to expand</summary>

    </details>
    """)

TEMPLATE_PERSON = dedent("""\
    ---
    type: person
    role: ""
    company: ""
    email: ""
    relationship: ""
    tags: []
    hubspot_contact_id: ""
    linkedin: ""
    last_interaction: <% tp.date.now("YYYY-MM-DD") %>
    created: <% tp.date.now("YYYY-MM-DD") %>
    updated: <% tp.date.now("YYYY-MM-DD") %>
    ---

    # <% tp.file.title %>

    ## Kontext

    ## Themen & Interessen

    ## Communication Style

    ## Open Threads

    ## History
    """)

TEMPLATE_DAILY = dedent("""\
    ---
    type: daily
    date: <% tp.date.now("YYYY-MM-DD") %>
    ---

    # <% tp.date.now("YYYY-MM-DD") %>

    ## Highlights

    ## Notes

    ## Tasks Done
    """)

TEMPLATE_RESOURCE = dedent("""\
    ---
    type: resource
    tags: []
    created: <% tp.date.now("YYYY-MM-DD") %>
    updated: <% tp.date.now("YYYY-MM-DD") %>
    ---

    # <% tp.file.title %>

    ## Content
    """)


TEMPLATES: dict[str, str] = {
    "living-doc.md": TEMPLATE_LIVING_DOC,
    "meeting.md": TEMPLATE_MEETING,
    "person.md": TEMPLATE_PERSON,
    "daily.md": TEMPLATE_DAILY,
    "resource.md": TEMPLATE_RESOURCE,
}


# ---------------------------------------------------------------------------
# Convention documents
# ---------------------------------------------------------------------------

VAULT_CONVENTION = dedent("""\
    ---
    type: meta
    title: Vault Convention
    ---

    # Vault Convention

    Reference for the folder structure, frontmatter conventions, and trigger
    phrases used across the Second Brain.

    ## Folder structure

    | Folder | Purpose |
    |---|---|
    | `00_Inbox/` | Quick capture; triage into the right folder later |
    | `10_Projects/` | Active projects, one Living Doc per project |
    | `20_Areas/` | Ongoing responsibility areas (no end date) |
    | `30_Resources/` | Topical knowledge, references, snippets |
    | `40_Archive/` | Completed projects and archived people |
    | `50_Daily/` | Daily logs; `50_Daily/meetings/` for meeting notes |
    | `60_MOCs/` | Maps of Content (index notes) |
    | `70_People/` | Person Living Docs |
    | `99_Meta/` | Templates and conventions |

    ## Frontmatter conventions

    ### `type: living` (Project Living Doc)

    ```yaml
    type: living
    project: "ChapterNext"
    status: active            # active | paused | done
    created: 2026-04-25
    updated: 2026-04-25
    google_tasks_list_id: ""
    tags: []
    ```

    ### `type: meeting`

    ```yaml
    type: meeting
    date: 2026-04-25
    project: "ChapterNext"    # may be null
    attendees: ["[[70_People/Anna Schmidt]]"]
    unrecognized_attendees: ["John Doe"]
    meeting_type: "sync"
    duration_minutes: 30
    fireflies_id: "abc123"
    language: "de"
    ```

    ### `type: person`

    ```yaml
    type: person
    role: "Head of Marketing"
    company: "Klarna"
    email: "anna.schmidt@klarna.com"
    relationship: client     # client | colleague | partner | other
    tags: []
    hubspot_contact_id: ""
    linkedin: ""
    last_interaction: 2026-04-25
    created: 2026-04-25
    updated: 2026-04-25
    ```

    ### `type: daily`

    ```yaml
    type: daily
    date: 2026-04-25
    ```

    ### `type: resource`

    ```yaml
    type: resource
    tags: []
    created: 2026-04-25
    updated: 2026-04-25
    ```

    ## Trigger phrases

    Claude reacts to natural-language triggers in the conversation. The exact
    list lives in the system prompt, but the most common patterns are:

    - **"merk dir das" / "speicher das"** — append the current insight to the
      active project's Living Doc under `## Recent Insights`. If no project
      context is loaded, append to today's daily note.
    - **"Lass uns über X reden"** — call `get_living_doc(project=X)` and
      surface its contents at the start of the conversation.
    - **"füg das als Aufgabe hinzu"** — create a Google Task in the project's
      list and append a checkbox to the Living Doc's `## TODOs` section.
    - **"Anna ist jetzt VP"** — call `append_to_person` with the new fact and
      `update_person_meta` for the role field.

    ## Living Doc principles

    1. One Living Doc per active project lives in `10_Projects/`.
    2. Sections are stable: rename them and Claude's appends will silently
       drift.
    3. Inserts happen at the *end* of the matching section.
    4. The `updated:` timestamp must be refreshed on every write.
    5. On project completion, move the file to `40_Archive/` and flip
       `status: done`.

    ## People principles

    See `DSGVO-People-Convention.md`.
    """)

DSGVO_CONVENTION = dedent("""\
    ---
    type: meta
    title: DSGVO People Convention
    ---

    # DSGVO People Convention

    Privacy convention for `70_People/` notes. Everything stored here is
    personal data under the GDPR. The conventions below keep the vault
    legitimate-interest-compatible and free of special-category data.

    ## Allowed

    - **Business context**: role, company, projects worked on together,
      reporting line.
    - **Freely shared information**: hobbies, languages, communication
      preferences explicitly mentioned by the person.
    - **Interaction history**: meetings attended, topics discussed, decisions
      taken. Wikilinks back to the source meeting note.
    - **Public professional data**: LinkedIn URL, public email, conference
      talks, published work.

    ## Forbidden — GDPR Art. 9 special categories

    Never store, even if shared:

    - Health data (illness, medication, mental health, disability)
    - Political opinions or party membership
    - Religious or philosophical beliefs
    - Sexual orientation or sex life
    - Racial or ethnic origin
    - Trade-union membership
    - Genetic or biometric data
    - Criminal convictions or proceedings

    If a meeting transcript contains any of the above, redact it before
    appending to the person note. The raw transcript may stay in the meeting
    file, but the person summary must not.

    ## DSGVO right of access (Art. 15)

    The vault is fully searchable. To produce a data export for a person:

    1. Search for the person's name and email across the vault.
    2. Bundle person note + all linked meeting notes.
    3. Hand over as a zipped markdown bundle.

    ## Retention & deletion

    - **Active relationship**: keep indefinitely.
    - **Relationship ended** (project closed, person left the company,
      mutual disengagement): move person note to
      `40_Archive/people/<name>.md`. The file stays searchable but is not
      surfaced as an active contact.
    - **On request (Art. 17 right to erasure)**: delete the person note,
      then walk linked meeting notes and remove the person from the
      `attendees` frontmatter; the meeting itself stays.

    ## Recording consent

    The Fireflies bot informs participants automatically that the meeting
    is being recorded and transcribed. Hosting the vault privately is
    legitimate-interest-compatible as long as participants are informed.
    Document any opt-outs in the meeting note's `unrecognized_attendees:`
    field with a note `# opted out` and skip person creation for them.
    """)


# ---------------------------------------------------------------------------
# Static files
# ---------------------------------------------------------------------------

GITIGNORE = dedent("""\
    .obsidian/workspace.json
    .obsidian/workspace-mobile.json
    .obsidian/cache
    .DS_Store
    *.swp
    """)


def vault_readme(projects: list[str]) -> str:
    project_lines = "\n".join(f"- [[10_Projects/{p}]]" for p in projects) or "_(none yet)_"
    return dedent(f"""\
        # Second Brain Vault

        Generated by `init_vault.py` on {date.today().isoformat()}.

        See `99_Meta/Vault-Convention.md` for the folder structure and
        frontmatter conventions, and `99_Meta/DSGVO-People-Convention.md`
        for the privacy rules covering `70_People/`.

        ## Active projects

        {project_lines}

        ## Templates

        Point the Templater plugin's template folder at `99_Meta/Templates/`.
        Available templates:

        - `living-doc.md` — project Living Doc skeleton
        - `meeting.md` — meeting note skeleton
        - `person.md` — person Living Doc skeleton
        - `daily.md` — daily note skeleton
        - `resource.md` — generic knowledge resource
        """)


def living_doc_seed(project: str, today: str) -> str:
    return dedent(f"""\
        ---
        type: living
        project: "{project}"
        status: active
        created: {today}
        updated: {today}
        google_tasks_list_id: ""
        tags: []
        ---

        # {project}

        ## Status & Kontext

        ## Architektur & Entscheidungen

        ## Offene Fragen

        ## Recent Insights

        ## TODOs

        ## Conversation Log
        """)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Initialize an Obsidian vault skeleton for the Second Brain.",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Target directory for the vault.",
    )
    parser.add_argument(
        "--projects",
        default="",
        help="Comma-separated list of project names to seed in 10_Projects/.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing files (default: skip).",
    )
    return parser.parse_args(argv)


def run(output: Path, projects: list[str], force: bool) -> dict[str, int]:
    output.mkdir(parents=True, exist_ok=True)
    make_dirs(output)

    written = 0
    skipped = 0

    def w(path: Path, content: str) -> None:
        nonlocal written, skipped
        if write_file(path, content, force):
            written += 1
        else:
            skipped += 1

    for filename, body in TEMPLATES.items():
        w(output / "99_Meta" / "Templates" / filename, body)

    w(output / "99_Meta" / "Vault-Convention.md", VAULT_CONVENTION)
    w(output / "99_Meta" / "DSGVO-People-Convention.md", DSGVO_CONVENTION)
    w(output / ".gitignore", GITIGNORE)
    w(output / "README.md", vault_readme(projects))

    today = date.today().isoformat()
    for project in projects:
        if not project.strip():
            continue
        w(output / "10_Projects" / f"{project}.md", living_doc_seed(project, today))

    return {"written": written, "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    projects = [p.strip() for p in args.projects.split(",") if p.strip()]
    stats = run(args.output, projects, args.force)
    print(
        f"Vault initialized at {args.output}: "
        f"{stats['written']} written, {stats['skipped']} skipped.",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
