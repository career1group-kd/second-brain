# Phase 0 — Vault Skeleton Generator

`init_vault.py` generates a fresh Obsidian vault skeleton for the Second Brain.
Pure stdlib (Python 3.12), idempotent, cross-platform.

## Usage

```bash
python init_vault.py \
  --output ~/Vaults/SecondBrain \
  --projects "ChapterNext,C1G Sidebars,Kay Dollt,HubSpot Revenue Breakdown"
```

Options:

- `--output PATH` (required): target directory for the vault.
- `--projects "A,B,C"`: comma-separated project names. One Living Doc per
  project is seeded under `10_Projects/`.
- `--force`: overwrite files that already exist (default: skip).

The script is idempotent: running it twice without `--force` does not modify
anything that's already there. Re-runs are safe.

## What it produces

```
<output>/
├── 00_Inbox/
├── 10_Projects/
│   ├── ChapterNext.md
│   └── ...
├── 20_Areas/
├── 30_Resources/
├── 40_Archive/
├── 50_Daily/
│   └── meetings/
├── 60_MOCs/
├── 70_People/
├── 99_Meta/
│   ├── DSGVO-People-Convention.md
│   ├── Vault-Convention.md
│   └── Templates/
│       ├── daily.md
│       ├── living-doc.md
│       ├── meeting.md
│       ├── person.md
│       └── resource.md
├── .gitignore
└── README.md
```

## Templater integration

After opening the vault in Obsidian, install the Templater plugin and point
its template folder at `99_Meta/Templates/`. The templates use Templater
syntax (`<% tp.date.now(...) %>`, `<% tp.file.title %>`).

## Running tests

```bash
python -m pytest tests/ -q
```

## Definition of Done

- [x] Folder structure matches `99_Meta/Vault-Convention.md`.
- [x] All five templates present and Templater-compatible.
- [x] Both convention docs are coherent standalone references.
- [x] Idempotent: second run without `--force` is a no-op.
- [x] Code is type-hinted, has docstrings, fits in one file.
