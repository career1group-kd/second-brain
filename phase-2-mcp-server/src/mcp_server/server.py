"""FastMCP server entrypoint.

Registers all tool groups (vault read/write, people, Google Tasks) on a
FastMCP instance, mounts it under /mcp (Streamable HTTP), adds Bearer auth
middleware, and attaches the MeetGeek webhook router under /meetgeek/webhook.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

import structlog
import uvicorn
from fastmcp import FastMCP
from fastmcp.server.middleware import Middleware

from .auth import BearerAuthMiddleware
from .config import Settings, get_settings
from .gtasks_client import GoogleTasksClient
from .logging_setup import setup_logging
from .meetgeek.webhook import make_handler as make_meetgeek_handler
from .oauth import build_oauth_provider
from .qdrant_client import VaultIndex
from .rerank_cache import RerankCache
from .tools import gtasks as gtasks_tools
from .tools import people_read, vault_read, vault_write
from .tools._common import ServerContext
from .voyage import VoyageClient

log = structlog.get_logger()


class _MCPDebugMiddleware(Middleware):
    """Logs every list_tools / call_tool request and its result.

    Temporary diagnostics for debugging why Claude.ai sometimes shows no
    tools even though the server processes ListToolsRequest. Remove once
    the empty-tools issue is understood.
    """

    async def on_list_tools(self, context, call_next):
        result = await call_next(context)
        try:
            names = [getattr(t, "name", "?") for t in result]
        except Exception:
            names = ["<unrepr>"]
        log.info(
            "mcp_list_tools_response",
            count=len(names),
            names=names,
            source=getattr(context, "source", None),
        )
        return result

    async def on_call_tool(self, context, call_next):
        tool_name = getattr(getattr(context, "message", None), "name", None)
        log.info("mcp_call_tool_request", tool=tool_name)
        try:
            result = await call_next(context)
        except Exception:
            log.exception("mcp_call_tool_failed", tool=tool_name)
            raise
        log.info("mcp_call_tool_ok", tool=tool_name)
        return result


def _log_registered_tools(mcp: FastMCP) -> None:
    """Synchronously snapshot the FastMCP tool registry after startup.

    This proves whether @mcp.tool() decorators actually attached tools
    before any client request. If `tools_registered_snapshot` shows
    count=0, the bug is server-side; if count>0 but Claude.ai sees none,
    the bug is in transport / auth / client.
    """
    try:
        tools = asyncio.run(mcp.list_tools())
    except Exception:
        log.exception("tools_snapshot_failed")
        return
    names = sorted(getattr(t, "name", "?") for t in tools)
    log.info("tools_registered_snapshot", count=len(names), names=names)


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


def _log_config_summary(settings: Settings) -> None:
    """Print which required env vars are set, so misconfigured deployments
    are obvious in Railway logs even before any request comes in."""
    log.info(
        "config_summary",
        vault_path=str(settings.vault_path),
        qdrant_url=settings.qdrant_url,
        qdrant_collection=settings.qdrant_collection,
        voyage_api_key_set=bool(settings.voyage_api_key),
        bearer_token_set=bool(settings.bearer_token),
        google_oauth_enabled=settings.google_oauth_enabled,
        allowed_emails_count=len(settings.allowed_emails_set),
        gtasks_token_present=settings.gtasks_token_path.exists(),
        gtasks_key_set=bool(settings.gtasks_token_key),
        public_domain=settings.public_domain,
        host=settings.host,
        port=settings.port,
    )


async def _health(request):
    from starlette.responses import JSONResponse

    return JSONResponse({"ok": True})


# Inline brain favicon. Claude.ai's connector UI fetches /favicon.ico from the
# server's public domain; without this, Railway's edge serves its own default
# and the connector shows a Railway logo next to "second-brain".
_BRAIN_FAVICON_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
    b'<rect width="24" height="24" rx="5" fill="#1e1b4b"/>'
    b'<g fill="none" stroke="#c4b5fd" stroke-width="1.6" '
    b'stroke-linecap="round" stroke-linejoin="round">'
    b'<path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 '
    b'4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/>'
    b'<path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 '
    b'4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"/>'
    b'<path d="M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4"/>'
    b'<path d="M17.599 6.5a3 3 0 0 0 .399-1.375"/>'
    b'<path d="M6.003 5.125A3 3 0 0 0 6.401 6.5"/>'
    b'<path d="M3.477 10.896a4 4 0 0 1 .585-.396"/>'
    b'<path d="M19.938 10.5a4 4 0 0 1 .585.396"/>'
    b'<path d="M6 18a4 4 0 0 1-1.967-.516"/>'
    b'<path d="M19.967 17.484A4 4 0 0 1 18 18"/>'
    b'</g></svg>'
)


async def _favicon(request):
    from starlette.responses import Response

    return Response(
        content=_BRAIN_FAVICON_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


def build_app(settings: Settings | None = None):
    settings = settings or get_settings()
    setup_logging(settings.log_level)
    _log_config_summary(settings)

    # Build OAuth provider first so it can be passed into FastMCP. It
    # registers its own /authorize, /token, /register, /auth/callback
    # routes and a token-verification middleware on the MCP routes.
    oauth_provider = None
    try:
        oauth_provider = build_oauth_provider(settings)
    except Exception:
        log.exception("oauth_provider_init_failed")
        # Don't fall back to open access on misconfiguration. If OAuth
        # was meant to be on, refuse to issue tokens by leaving the
        # provider None and letting Bearer middleware (or no auth) handle
        # the rest. The deploy logs will show the traceback.

    mcp_kwargs: dict[str, Any] = {"name": "second-brain"}
    if oauth_provider is not None:
        mcp_kwargs["auth"] = oauth_provider
    mcp = FastMCP(**mcp_kwargs)

    ctx: ServerContext | None = None
    try:
        ctx = build_context(settings)
        gtasks = _maybe_gtasks(settings)
        register_tools(mcp, ctx, gtasks)
        log.info("tools_registered")
    except Exception:
        # Never crash the HTTP listener. Tools may be partially or fully
        # unavailable, but /health must keep responding so the orchestrator
        # doesn't keep restarting the container.
        log.exception("tool_registration_failed")

    # Snapshot whatever ended up on the registry, even if registration
    # partially failed. Pairs with the mcp_list_tools_response logs from
    # the debug middleware to pinpoint where a "no tools" symptom comes
    # from.
    _log_registered_tools(mcp)
    mcp.add_middleware(_MCPDebugMiddleware())

    # Streamable HTTP is the current MCP transport standard. SSE is deprecated
    # and unreliable in the Claude mobile apps; /mcp keeps the server working
    # there as well as in claude.ai.
    app = mcp.http_app(transport="streamable-http", path="/mcp")

    # /health: register via the modern Starlette API. The decorator form
    # is gone in newer Starlette versions, which would silently 404 here.
    app.router.add_route("/health", _health, methods=["GET"], name="health")

    # Favicon: served as SVG from both /favicon.ico and /favicon.svg so the
    # Claude connector UI shows a brain instead of Railway's default logo.
    app.router.add_route("/favicon.ico", _favicon, methods=["GET"], name="favicon_ico")
    app.router.add_route("/favicon.svg", _favicon, methods=["GET"], name="favicon_svg")

    # MeetGeek webhook only if context exists. Registered as a plain
    # Starlette route — FastAPI APIRouter routes assume their own
    # middleware stack which the MCP top-level app doesn't provide.
    if ctx is not None:
        try:
            meetgeek_handler = make_meetgeek_handler(ctx)
            app.router.add_route(
                "/meetgeek/webhook",
                meetgeek_handler,
                methods=["POST"],
                name="meetgeek_webhook",
            )
        except Exception:
            log.exception("meetgeek_router_failed")

    # Auth middleware selection:
    # - OAuth provider: FastMCP wires its own token verifier on MCP
    #   routes; we leave bearer middleware off entirely.
    # - No OAuth: fall back to legacy bearer for backwards-compat.
    if oauth_provider is not None:
        log.info("auth_mode", mode="google_oauth")
    elif settings.bearer_token:
        log.info("auth_mode", mode="bearer")
        app.add_middleware(
            BearerAuthMiddleware,
            token=settings.bearer_token,
            public_paths=(
                "/health",
                "/meetgeek/webhook",
                "/favicon.ico",
                "/favicon.svg",
            ),
        )
    else:
        log.warning(
            "auth_mode_open; no auth configured. Set GOOGLE_OAUTH_CLIENT_ID "
            "or BEARER_TOKEN to gate /mcp."
        )

    return app


def main() -> None:
    settings = get_settings()
    app = build_app(settings)
    uvicorn.run(
        app,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
