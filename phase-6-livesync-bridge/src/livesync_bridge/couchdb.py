"""CouchDB HTTP client wrapper (async)."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator
from urllib.parse import quote

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

log = structlog.get_logger()


class CouchDBError(RuntimeError):
    pass


class CouchDB:
    def __init__(
        self,
        url: str,
        db: str,
        *,
        user: str = "",
        password: str = "",
        timeout: float = 30.0,
    ) -> None:
        self.url = url.rstrip("/")
        self.db = db
        auth = (user, password) if user else None
        self._client = httpx.AsyncClient(
            base_url=f"{self.url}/{quote(db, safe='')}",
            auth=auth,
            timeout=timeout,
        )
        self._stream_client = httpx.AsyncClient(
            base_url=f"{self.url}/{quote(db, safe='')}",
            auth=auth,
            timeout=None,
        )

    async def aclose(self) -> None:
        await self._client.aclose()
        await self._stream_client.aclose()

    # --- DB lifecycle -------------------------------------------------------

    async def ensure_db(self) -> None:
        r = await self._client.head("")
        if r.status_code == 200:
            return
        if r.status_code == 404:
            r = await self._client.put("")
            if r.status_code not in (201, 412):
                raise CouchDBError(f"create db failed: {r.status_code} {r.text}")
            return
        raise CouchDBError(f"unexpected: {r.status_code} {r.text}")

    # --- Document CRUD ------------------------------------------------------

    async def get_doc(self, doc_id: str) -> dict[str, Any] | None:
        r = await self._client.get(f"/{quote(doc_id, safe='')}")
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise CouchDBError(f"get failed: {r.status_code} {r.text}")
        return r.json()

    async def get_docs_bulk(self, ids: list[str]) -> list[dict[str, Any] | None]:
        if not ids:
            return []
        body = {"docs": [{"id": i} for i in ids]}
        r = await self._client.post("/_bulk_get", json=body)
        if r.status_code != 200:
            raise CouchDBError(f"bulk_get failed: {r.status_code} {r.text}")
        results = r.json().get("results", [])
        out: list[dict[str, Any] | None] = []
        for item in results:
            docs = item.get("docs", [])
            if docs and "ok" in docs[0]:
                out.append(docs[0]["ok"])
            else:
                out.append(None)
        return out

    async def put_doc(self, doc: dict[str, Any]) -> dict[str, Any]:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(min=0.5, max=4),
            retry=retry_if_exception_type(httpx.HTTPError),
            reraise=True,
        ):
            with attempt:
                r = await self._client.post("", json=doc)
        if r.status_code not in (201, 202):
            raise CouchDBError(f"put failed: {r.status_code} {r.text}")
        return r.json()

    async def delete_doc(self, doc_id: str, rev: str) -> None:
        r = await self._client.delete(
            f"/{quote(doc_id, safe='')}", params={"rev": rev}
        )
        if r.status_code not in (200, 202, 404):
            raise CouchDBError(f"delete failed: {r.status_code} {r.text}")

    # --- All docs -----------------------------------------------------------

    async def all_doc_ids(self, *, include_design: bool = False) -> list[str]:
        params: dict[str, Any] = {"include_docs": "false"}
        r = await self._client.get("/_all_docs", params=params)
        if r.status_code != 200:
            raise CouchDBError(f"all_docs failed: {r.status_code} {r.text}")
        rows = r.json().get("rows", [])
        ids = [row["id"] for row in rows]
        if not include_design:
            ids = [i for i in ids if not i.startswith("_design/")]
        return ids

    # --- Changes feed -------------------------------------------------------

    async def changes_continuous(
        self,
        *,
        since: str = "now",
        heartbeat: int = 30000,
        include_docs: bool = True,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield change records as they arrive on a continuous feed.

        The feed reconnects on transient errors via tenacity.
        """
        params = {
            "feed": "continuous",
            "since": since,
            "heartbeat": str(heartbeat),
            "include_docs": "true" if include_docs else "false",
        }
        async with self._stream_client.stream(
            "GET", "/_changes", params=params
        ) as resp:
            if resp.status_code != 200:
                text = (await resp.aread()).decode(errors="replace")
                raise CouchDBError(
                    f"changes failed: {resp.status_code} {text}"
                )
            async for raw in resp.aiter_lines():
                line = raw.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    log.warning("changes_unparsed", line=line[:120])
                    continue
