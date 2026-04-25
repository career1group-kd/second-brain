"""Google Tasks MCP tools."""

from __future__ import annotations

from typing import Any

import structlog

from ..gtasks_client import GoogleTasksClient
from ..vault import safe_join
from ._common import ServerContext, fuzzy_match_living_doc

log = structlog.get_logger()


def _wrap_errors(callable_):
    def wrapped(*args, **kwargs):
        try:
            return callable_(*args, **kwargs)
        except FileNotFoundError as e:
            return {"error": str(e), "code": "GTASKS_AUTH"}
        except Exception as e:
            from googleapiclient.errors import HttpError

            if isinstance(e, HttpError):
                status = getattr(e, "status_code", None) or e.resp.status
                if status in (401, 403):
                    return {"error": str(e), "code": "GTASKS_AUTH"}
                if status >= 500:
                    return {"error": str(e), "code": "GTASKS_SERVER_ERROR"}
                return {"error": str(e), "code": "GTASKS_CLIENT_ERROR"}
            log.exception("gtasks_unhandled")
            return {"error": str(e), "code": "GTASKS_UNKNOWN"}

    return wrapped


def list_task_lists(ctx: ServerContext, gtasks: GoogleTasksClient) -> dict[str, Any]:
    @_wrap_errors
    def _do() -> dict[str, Any]:
        return {"results": gtasks.list_task_lists()}

    return _do()


def list_tasks(
    ctx: ServerContext,
    gtasks: GoogleTasksClient,
    *,
    list_id: str,
    status: str = "needsAction",
) -> dict[str, Any]:
    @_wrap_errors
    def _do() -> dict[str, Any]:
        return {"results": gtasks.list_tasks(list_id, status=status)}

    return _do()


def create_task(
    ctx: ServerContext,
    gtasks: GoogleTasksClient,
    *,
    list_id: str,
    title: str,
    notes: str | None = None,
    due: str | None = None,
) -> dict[str, Any]:
    @_wrap_errors
    def _do() -> dict[str, Any]:
        return gtasks.create_task(list_id, title=title, notes=notes, due=due)

    return _do()


def complete_task(
    ctx: ServerContext,
    gtasks: GoogleTasksClient,
    *,
    list_id: str,
    task_id: str,
) -> dict[str, Any]:
    @_wrap_errors
    def _do() -> dict[str, Any]:
        return gtasks.complete_task(list_id, task_id)

    return _do()


def update_task(
    ctx: ServerContext,
    gtasks: GoogleTasksClient,
    *,
    list_id: str,
    task_id: str,
    title: str | None = None,
    notes: str | None = None,
    due: str | None = None,
) -> dict[str, Any]:
    @_wrap_errors
    def _do() -> dict[str, Any]:
        return gtasks.update_task(
            list_id, task_id, title=title, notes=notes, due=due
        )

    return _do()


def resolve_task_list(ctx: ServerContext, *, project: str) -> dict[str, Any]:
    matches = fuzzy_match_living_doc(ctx.settings.vault_path, project)
    if not matches:
        return {"error": "no living doc matches project", "code": "NOT_FOUND"}
    fm = matches[0]["doc"]["frontmatter"]
    list_id = fm.get("google_tasks_list_id") or None
    return {
        "list_id": list_id,
        "project": fm.get("project") or matches[0]["doc"]["title"],
        "path": matches[0]["doc"]["path"],
    }
