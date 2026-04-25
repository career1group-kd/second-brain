"""Google Tasks API client with Fernet-encrypted token persistence."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import structlog
from cryptography.fernet import Fernet
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

log = structlog.get_logger()

SCOPES = ["https://www.googleapis.com/auth/tasks"]


class GoogleTasksClient:
    def __init__(
        self,
        token_path: Path,
        token_key: str,
    ) -> None:
        if not token_key:
            raise ValueError("GTASKS_TOKEN_KEY is required")
        self.token_path = token_path
        self.fernet = Fernet(token_key.encode("utf-8"))
        self._lock = threading.Lock()
        self._service: Any | None = None
        self._creds: Credentials | None = None

    # --- token I/O ----------------------------------------------------------

    def _load_creds(self) -> Credentials:
        if not self.token_path.exists():
            raise FileNotFoundError(
                f"Google Tasks token not found at {self.token_path}. "
                "Run `gtasks-auth` first."
            )
        encrypted = self.token_path.read_bytes()
        decrypted = self.fernet.decrypt(encrypted)
        info = json.loads(decrypted)
        return Credentials.from_authorized_user_info(info, SCOPES)

    def _save_creds(self, creds: Credentials) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        info = json.loads(creds.to_json())
        encrypted = self.fernet.encrypt(json.dumps(info).encode("utf-8"))
        self.token_path.write_bytes(encrypted)

    # --- service lifecycle --------------------------------------------------

    def _ensure_service(self) -> Any:
        from googleapiclient.discovery import build

        with self._lock:
            if self._creds is None:
                self._creds = self._load_creds()
            if self._creds.expired and self._creds.refresh_token:
                self._creds.refresh(Request())
                self._save_creds(self._creds)
            if self._service is None:
                self._service = build(
                    "tasks", "v1", credentials=self._creds, cache_discovery=False
                )
            return self._service

    # --- Tools --------------------------------------------------------------

    def list_task_lists(self) -> list[dict[str, Any]]:
        svc = self._ensure_service()
        result = svc.tasklists().list(maxResults=100).execute()
        return [
            {"id": item["id"], "title": item["title"]}
            for item in result.get("items", [])
        ]

    def list_tasks(
        self,
        list_id: str,
        *,
        status: str = "needsAction",
    ) -> list[dict[str, Any]]:
        svc = self._ensure_service()
        kwargs = {"tasklist": list_id, "maxResults": 100}
        if status == "completed":
            kwargs["showCompleted"] = True
            kwargs["showHidden"] = True
        result = svc.tasks().list(**kwargs).execute()
        items = result.get("items", [])
        if status == "needsAction":
            items = [t for t in items if t.get("status") == "needsAction"]
        elif status == "completed":
            items = [t for t in items if t.get("status") == "completed"]
        return [
            {
                "id": t["id"],
                "title": t.get("title", ""),
                "notes": t.get("notes"),
                "due": t.get("due"),
                "status": t.get("status"),
            }
            for t in items
        ]

    def create_task(
        self,
        list_id: str,
        *,
        title: str,
        notes: str | None = None,
        due: str | None = None,  # ISO date or RFC3339
    ) -> dict[str, Any]:
        svc = self._ensure_service()
        body: dict[str, Any] = {"title": title}
        if notes:
            body["notes"] = notes
        if due:
            # Google Tasks requires RFC3339; coerce a date.
            if len(due) == 10:
                body["due"] = f"{due}T00:00:00.000Z"
            else:
                body["due"] = due
        result = svc.tasks().insert(tasklist=list_id, body=body).execute()
        return {
            "id": result["id"],
            "title": result.get("title"),
            "notes": result.get("notes"),
            "due": result.get("due"),
            "status": result.get("status"),
        }

    def complete_task(self, list_id: str, task_id: str) -> dict[str, Any]:
        svc = self._ensure_service()
        body = {
            "status": "completed",
            "completed": datetime.now(timezone.utc)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
        }
        result = (
            svc.tasks()
            .patch(tasklist=list_id, task=task_id, body=body)
            .execute()
        )
        return {
            "id": result["id"],
            "title": result.get("title"),
            "status": result.get("status"),
            "completed": result.get("completed"),
        }

    def update_task(
        self,
        list_id: str,
        task_id: str,
        *,
        title: str | None = None,
        notes: str | None = None,
        due: str | None = None,
    ) -> dict[str, Any]:
        svc = self._ensure_service()
        body: dict[str, Any] = {}
        if title is not None:
            body["title"] = title
        if notes is not None:
            body["notes"] = notes
        if due is not None:
            body["due"] = (
                f"{due}T00:00:00.000Z" if len(due) == 10 else due
            )
        result = (
            svc.tasks()
            .patch(tasklist=list_id, task=task_id, body=body)
            .execute()
        )
        return {
            "id": result["id"],
            "title": result.get("title"),
            "notes": result.get("notes"),
            "due": result.get("due"),
            "status": result.get("status"),
        }
