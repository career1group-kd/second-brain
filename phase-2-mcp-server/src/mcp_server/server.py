"""FastMCP server entrypoint.

Registers all tool groups (vault read/write, people, Google Tasks) on a
FastMCP instance, mounts it under /sse, adds Bearer auth middleware, and
attaches the MeetGeek webhook router under /meetgeek/webhook.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import structlog
import uvicorn
from fastmcp import FastMCP

from .auth import BearerAuthMiddleware
from .config import Settings, get_settings
from .gtasks_client import GoogleTasksClient
from .logging_setup import setup_logging
from .meetgeek.webhook import make_router as make_meetgeek_router
from .qdrant_client import VaultIndex
from .rerank_cache import RerankCache
from .tools import gtasks as gtasks_tools
from .tools import people_read, vault_read, vault_write
from .tools._common import ServerContext
from .voyage import VoyageClient

log = structlog.get_logger()


def build_context(settings: Settings | None = None) -> ServerContext:
    settings = settings or get_settings()
    return ServerContext(
        settings=settings,
        index=VaultIndex(
            url=settings.qdrant_url,
            collection=settings.qdrant_collection,
            api_key=settings.qdrant_api_key,
        ),
        voyage=VoyageClient(
            api_key=settings.voyage_api_key,
            query_model=settings.query_model,
            rerank_model=settings.rerank_model,
        ),
        rerank_cache=RerankCache(),
    )


def _maybe_gtasks(settings: Settings) -> GoogleTasksClient | None:
    if not settings.gtasks_token_key or not settings.gtasks_token_path.exists():
        return None
    try:
        return GoogleTasksClient(
            token_path=settings.gtasks_token_path,
            token_key=settings.gtasks_token_key,
        )
    except Exception:
        log.exception("gtasks_init_failed")
        return None


def register_tools(mcp: FastMCP, ctx: ServerContext, gtasks: GoogleTasksClient | None) -> None:
    # --- Vault read tools --------------------------------------------------

    @mcp.tool()
    def search_notes(
        query: str,
        top_k: int = 10,
        type: str | None = None,
        project: str | None = None,
        tags: list[str] | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        attendees: list[str] | None = None,
        status: str | None = "active",
    ) -> dict:
        """Hybrid (dense + BM25) search over the vault, then Voyage rerank."""
        return vault_read.search_notes(
            ctx,
            query=query,
            top_k=top_k,
            type=type,
            project=project,
            tags=tags,
            date_from=date_from,
            date_to=date_to,
            attendees=attendees,
            status=status,
        )

    @mcp.tool()
    def get_note(path: str) -> dict:
        """Read a markdown note by relative path."""
        return vault_read.get_note(ctx, path=path)

    @mcp.tool()
    def get_living_doc(project: str) -> dict:
        """Resolve and return the Living Doc for an active project."""
        return vault_read.get_living_doc(ctx, project=project)

    @mcp.tool()
    def list_recent(n: int = 10, type: str | None = None) -> dict:
        """List the most recently updated notes (optionally filtered by type)."""
        return vault_read.list_recent(ctx, n=n, type=type)

    @mcp.tool()
    def find_related(path: str, top_k: int = 5) -> dict:
        """Vector neighbours of a given note."""
        return vault_read.find_related(ctx, path=path, top_k=top_k)

    @mcp.tool()
    def list_active_projects() -> dict:
        """All living docs with status=active."""
        return vault_read.list_active_projects(ctx)

    # --- People read tools -------------------------------------------------

    @mcp.tool()
    def get_person(name_or_email: str) -> dict:
        """Resolve a person by name or email."""
        return people_read.get_person(ctx, name_or_email=name_or_email)

    @mcp.tool()
    def find_person(query: str) -> dict:
        """Hybrid search restricted to person notes."""
        return people_read.find_person(ctx, query=query)

    @mcp.tool()
    def list_recent_interactions(name: str, n: int = 5) -> dict:
        """Recent meetings a person attended."""
        return people_read.list_recent_interactions(ctx, name=name, n=n)

    @mcp.tool()
    def list_people_by_company(company: str) -> dict:
        """Find person notes whose `company` frontmatter matches (substring, case-insensitive)."""
        return people_read.list_people_by_company(ctx, company=company)

    # --- Vault write tools (Phase 3) --------------------------------------

    @mcp.tool()
    def append_to_living_doc(project: str, section: str, content: str) -> dict:
        """Append a dated bullet (or block) to a Living Doc section."""
        return vault_write.append_to_living_doc(
            ctx, project=project, section=section, content=content
        )

    @mcp.tool()
    def update_section(path: str, section: str, content: str) -> dict:
        """Replace the body of a section, leaving the heading untouched."""
        return vault_write.update_section(
            ctx, path=path, section=section, content=content
        )

    @mcp.tool()
    def create_note(
        path: str,
        frontmatter_data: dict,
        content: str = "",
        force: bool = False,
    ) -> dict:
        """Create a new note under VAULT_PATH with validated frontmatter."""
        return vault_write.create_note(
            ctx,
            path=path,
            frontmatter_data=frontmatter_data,
            content=content,
            force=force,
        )

    @mcp.tool()
    def append_to_person(name: str, section: str, content: str) -> dict:
        """Append to a person note's section and bump last_interaction."""
        return vault_write.append_to_person(
            ctx, name=name, section=section, content=content
        )

    @mcp.tool()
    def update_person_meta(name: str, fields: dict) -> dict:
        """Merge frontmatter fields on a person note."""
        return vault_write.update_person_meta(ctx, name=name, fields=fields)

    @mcp.tool()
    def create_person(
        name: str,
        frontmatter_data: dict | None = None,
        content: str | None = None,
    ) -> dict:
        """Create a new person note in 70_People/."""
        return vault_write.create_person(
            ctx, name=name, frontmatter_data=frontmatter_data, content=content
        )

    # --- Google Tasks (Phase 4) -------------------------------------------

    if gtasks is None:
        log.info("gtasks_disabled", reason="not configured or token missing")
    else:

        @mcp.tool()
        def list_task_lists() -> dict:
            """All Google Tasks lists for the authenticated account."""
            return gtasks_tools.list_task_lists(ctx, gtasks)

        @mcp.tool()
        def list_tasks(list_id: str, status: str = "needsAction") -> dict:
            """Tasks in a list (needsAction or completed)."""
            return gtasks_tools.list_tasks(ctx, gtasks, list_id=list_id, status=status)

        @mcp.tool()
        def create_task(
            list_id: str,
            title: str,
            notes: str | None = None,
            due: str | None = None,
        ) -> dict:
            """Create a task in a list."""
            return gtasks_tools.create_task(
                ctx, gtasks, list_id=list_id, title=title, notes=notes, due=due
            )

        @mcp.tool()
        def complete_task(list_id: str, task_id: str) -> dict:
            """Mark a task completed."""
            return gtasks_tools.complete_task(ctx, gtasks, list_id=list_id, task_id=task_id)

        @mcp.tool()
        def update_task(
            list_id: str,
            task_id: str,
            title: str | None = None,
            notes: str | None = None,
            due: str | None = None,
        ) -> dict:
            """Patch a task's fields."""
            return gtasks_tools.update_task(
                ctx,
                gtasks,
                list_id=list_id,
                task_id=task_id,
                title=title,
                notes=notes,
                due=due,
            )

        @mcp.tool()
        def resolve_task_list(project: str) -> dict:
            """Map a project name to its `google_tasks_list_id`."""
            return gtasks_tools.resolve_task_list(ctx, project=project)


def build_app(settings: Settings | None = None):
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    ctx = build_context(settings)
    gtasks = _maybe_gtasks(settings)

    mcp = FastMCP(name="second-brain")
    register_tools(mcp, ctx, gtasks)

    app = mcp.sse_app()

    # MeetGeek webhook router (auth handled internally).
    meetgeek_router = make_meetgeek_router(ctx)
    app.router.routes.extend(meetgeek_router.routes)

    @app.route("/health")
    async def health(request) -> Any:
        from starlette.responses import JSONResponse

        return JSONResponse({"ok": True})

    app.add_middleware(
        BearerAuthMiddleware,
        token=settings.bearer_token,
        public_paths=("/health", "/meetgeek/webhook"),
    )
    return app


def main() -> None:
    settings = get_settings()
    app = build_app(settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level.lower())


if __name__ == "__main__":
    main()
