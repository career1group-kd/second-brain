"""Google Calendar (read-only) client with Fernet-encrypted token persistence.

Used by the Fireflies webhook to look up which meeting a transcript belongs
to. Mirrors the GoogleTasksClient pattern: the token is created once via the
`gcal-auth` CLI, persisted encrypted on disk, and refreshed automatically.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import structlog
from cryptography.fernet import Fernet
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

log = structlog.get_logger()

SCOPES = ["https://www.googleapis.com/auth/calendar.events.readonly"]


class GoogleCalendarClient:
    def __init__(
        self,
        token_path: Path,
        token_key: str,
    ) -> None:
        if not token_key:
            raise ValueError("GCAL_TOKEN_KEY is required")
        self.token_path = token_path
        self.fernet = Fernet(token_key.encode("utf-8"))
        self._lock = threading.Lock()
        self._service: Any | None = None
        self._creds: Credentials | None = None

    def _load_creds(self) -> Credentials:
        if not self.token_path.exists():
            raise FileNotFoundError(
                f"Google Calendar token not found at {self.token_path}. "
                "Run `gcal-auth` first."
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
                    "calendar", "v3", credentials=self._creds, cache_discovery=False
                )
            return self._service

    def get_event(
        self,
        event_id: str,
        calendar_id: str = "primary",
    ) -> dict[str, Any] | None:
        """Fetch a single event by ID. Returns None on 404."""
        from googleapiclient.errors import HttpError

        svc = self._ensure_service()
        try:
            return svc.events().get(calendarId=calendar_id, eventId=event_id).execute()
        except HttpError as e:
            if e.resp.status == 404:
                return None
            raise

    def list_events_in_window(
        self,
        *,
        start: datetime,
        end: datetime,
        calendar_id: str = "primary",
    ) -> list[dict[str, Any]]:
        """List events whose time-range intersects [start, end]."""
        svc = self._ensure_service()
        time_min = start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        time_max = end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        result = (
            svc.events()
            .list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                maxResults=20,
            )
            .execute()
        )
        return list(result.get("items", []))

    def find_event_around(
        self,
        *,
        started_at: datetime,
        slack: timedelta = timedelta(minutes=10),
        calendar_id: str = "primary",
    ) -> dict[str, Any] | None:
        """Best-match event whose start time is closest to `started_at`.

        Used as a fallback when Fireflies didn't capture a `cal_id` on the
        transcript (e.g. when recording from the Mac app without the
        calendar plugin connected).
        """
        events = self.list_events_in_window(
            start=started_at - slack,
            end=started_at + slack,
            calendar_id=calendar_id,
        )
        if not events:
            return None

        target_ts = started_at.timestamp()

        def _score(ev: dict[str, Any]) -> float:
            start = ev.get("start", {})
            iso = start.get("dateTime") or start.get("date")
            if not iso:
                return float("inf")
            try:
                t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            except ValueError:
                return float("inf")
            if t.tzinfo is None:
                t = t.replace(tzinfo=timezone.utc)
            return abs(t.timestamp() - target_ts)

        return min(events, key=_score)
