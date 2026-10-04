"""Minimal read-only Google Drive client."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from http import HTTPStatus
import json
from typing import Any

from aiohttp import ClientError, ClientSession, ClientTimeout

DRIVE_API = "https://www.googleapis.com/drive/v3"
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
MAX_FILE_BYTES = 32 * 1024 * 1024
_TIMEOUT = ClientTimeout(total=60)
_PAGE_SIZE = 1000
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 2.0
_RATE_LIMIT_REASONS = ("rateLimitExceeded", "userRateLimitExceeded")


class DriveError(Exception):
    """Raised when Google Drive cannot be reached or rejects a request."""


class DriveAuthError(DriveError):
    """Raised when the access token is rejected."""


class DriveTransientError(DriveError):
    """Raised for failures that are worth retrying: throttling, outages, network."""


@dataclass(slots=True, frozen=True)
class DriveFile:
    """A file or folder in Google Drive."""

    id: str
    name: str
    modified: str = ""
    parents: tuple[str, ...] = ()


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _to_file(raw: dict[str, Any]) -> DriveFile:
    return DriveFile(
        id=raw["id"],
        name=raw.get("name", ""),
        modified=raw.get("modifiedTime", ""),
        parents=tuple(raw.get("parents", ())),
    )


class DriveClient:
    """Read files and folders from Google Drive."""

    def __init__(
        self,
        session: ClientSession,
        token_provider: Callable[[], Awaitable[str]],
    ) -> None:
        """Initialize the client with a coroutine returning a valid access token."""
        self._session = session
        self._token_provider = token_provider

    async def _get(self, path: str, params: dict[str, str]) -> bytes:
        for attempt in range(1, MAX_ATTEMPTS):
            try:
                return await self._get_once(path, params)
            except DriveTransientError:
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * 2 ** (attempt - 1))
        return await self._get_once(path, params)

    async def _get_once(self, path: str, params: dict[str, str]) -> bytes:
        token = await self._token_provider()
        try:
            async with self._session.get(
                f"{DRIVE_API}/{path}",
                params=params,
                headers={"Authorization": f"Bearer {token}"},
                timeout=_TIMEOUT,
            ) as response:
                if response.status == HTTPStatus.UNAUTHORIZED:
                    raise DriveAuthError("Google rejected the access token")
                if response.status >= HTTPStatus.BAD_REQUEST:
                    detail = (await response.text())[:300]
                    message = f"Google Drive returned {response.status}: {detail}"
                    if (
                        response.status == HTTPStatus.TOO_MANY_REQUESTS
                        or response.status >= HTTPStatus.INTERNAL_SERVER_ERROR
                        or any(reason in detail for reason in _RATE_LIMIT_REASONS)
                    ):
                        raise DriveTransientError(message)
                    raise DriveError(message)
                declared = response.headers.get("Content-Length", "0")
                if declared.isdigit() and int(declared) > MAX_FILE_BYTES:
                    raise DriveError("Google Drive file is too large")
                body = await response.read()
        except (ClientError, TimeoutError) as err:
            raise DriveTransientError(f"Cannot reach Google Drive: {err}") from err
        if len(body) > MAX_FILE_BYTES:
            raise DriveError("Google Drive file is too large")
        return body

    async def _get_json(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        try:
            data = json.loads(await self._get(path, params))
        except ValueError as err:
            raise DriveError(f"Unexpected response from Google Drive: {err}") from err
        if not isinstance(data, dict):
            raise DriveError("Unexpected response from Google Drive")
        return data

    async def _list(self, query: str, order_by: str | None = None) -> list[DriveFile]:
        files: list[DriveFile] = []
        params = {
            "q": query,
            "fields": "nextPageToken,files(id,name,modifiedTime,parents)",
            "pageSize": str(_PAGE_SIZE),
            "spaces": "drive",
        }
        if order_by:
            params["orderBy"] = order_by
        while True:
            data = await self._get_json("files", params)
            files.extend(_to_file(raw) for raw in data.get("files", []))
            if not (token := data.get("nextPageToken")):
                return files
            params["pageToken"] = token

    async def list_folders(self, parent_id: str) -> list[DriveFile]:
        """Return the sub-folders of a folder, sorted by name."""
        return await self._list(
            f"'{_escape(parent_id)}' in parents and mimeType = '{FOLDER_MIME_TYPE}'"
            " and trashed = false",
            order_by="name_natural",
        )

    async def list_files(
        self, folder_id: str, modified_after: datetime | None = None
    ) -> list[DriveFile]:
        """Return the files stored directly in a folder.

        With ``modified_after`` only files uploaded or changed since then are
        listed, which is much faster than walking a folder with years of trips.

        There is no name filter in the query: callers pick the route files by
        name themselves.
        """
        query = (
            f"'{_escape(folder_id)}' in parents"
            f" and mimeType != '{FOLDER_MIME_TYPE}' and trashed = false"
        )
        if modified_after is not None:
            stamp = modified_after.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S")
            query += f" and modifiedTime > '{stamp}'"
        return await self._list(query)

    async def get_folder(self, folder_id: str) -> DriveFile:
        """Return a folder's metadata, raising DriveError if it is not a folder."""
        data = await self._get_json(
            f"files/{folder_id}", {"fields": "id,name,mimeType,parents"}
        )
        if data.get("mimeType") != FOLDER_MIME_TYPE:
            raise DriveError("Not a folder")
        return _to_file(data)

    async def download(self, file_id: str) -> bytes:
        """Return the content of a file."""
        return await self._get(f"files/{file_id}", {"alt": "media"})
