"""Tests for the Google Drive client."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.fuelio_routes.api import (
    DRIVE_API,
    FOLDER_MIME_TYPE,
    DriveAuthError,
    DriveClient,
    DriveError,
)

FILES_URL = f"{DRIVE_API}/files"


@pytest.fixture
def client(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> DriveClient:
    """Return a client with a fixed token."""

    async def token() -> str:
        return "token-1"

    return DriveClient(async_get_clientsession(hass), token)


async def test_list_files_follows_pages(
    client: DriveClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """All pages are fetched and the token is sent."""
    pages = {
        None: {
            "nextPageToken": "page2",
            "files": [
                {
                    "id": "a",
                    "name": "route-1.gpx",
                    "modifiedTime": "t1",
                    "parents": ["p"],
                }
            ],
        },
        "page2": {"files": [{"id": "b"}]},
    }

    async def respond(method, url, data):
        return AiohttpClientMockResponse(
            method, url, json=pages[url.query.get("pageToken")]
        )

    aioclient_mock.get(FILES_URL, side_effect=respond)
    files = await client.list_files("fol'der")

    assert [(f.id, f.name, f.modified, f.parents) for f in files] == [
        ("a", "route-1.gpx", "t1", ("p",)),
        ("b", "", "", ()),
    ]
    assert aioclient_mock.call_count == 2
    _, url, _, headers = aioclient_mock.mock_calls[0]
    assert headers["Authorization"] == "Bearer token-1"
    assert "'fol\\'der' in parents" in url.query["q"]
    # Names are filtered by the caller, not by Drive.
    assert "name contains" not in url.query["q"]


async def test_list_folders(
    client: DriveClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Folders are requested sorted by name."""
    aioclient_mock.get(FILES_URL, json={"files": [{"id": "f1", "name": "Fuelio"}]})
    folders = await client.list_folders("root")
    assert [f.name for f in folders] == ["Fuelio"]
    url = aioclient_mock.mock_calls[0][1]
    assert url.query["orderBy"] == "name_natural"
    assert FOLDER_MIME_TYPE in url.query["q"]


async def test_get_folder(
    client: DriveClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Only folders are accepted."""
    aioclient_mock.get(
        f"{FILES_URL}/f1",
        json={"id": "f1", "name": "Fuelio", "mimeType": FOLDER_MIME_TYPE},
    )
    aioclient_mock.get(f"{FILES_URL}/doc", json={"id": "doc", "mimeType": "text/plain"})
    assert (await client.get_folder("f1")).name == "Fuelio"
    with pytest.raises(DriveError, match="Not a folder"):
        await client.get_folder("doc")


async def test_download(
    client: DriveClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """The raw content is returned."""
    aioclient_mock.get(f"{FILES_URL}/abc", content=b"\x00binary")
    assert await client.download("abc") == b"\x00binary"
    assert aioclient_mock.mock_calls[0][1].query["alt"] == "media"


async def test_download_too_large(
    client: DriveClient,
    aioclient_mock: AiohttpClientMocker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Oversized files are refused by declared and by actual size."""
    monkeypatch.setattr("custom_components.fuelio_routes.api.MAX_FILE_BYTES", 4)
    aioclient_mock.get(
        f"{FILES_URL}/declared", content=b"12", headers={"Content-Length": "99"}
    )
    aioclient_mock.get(f"{FILES_URL}/actual", content=b"123456")
    with pytest.raises(DriveError, match="too large"):
        await client.download("declared")
    with pytest.raises(DriveError, match="too large"):
        await client.download("actual")


async def test_errors(client: DriveClient, aioclient_mock: AiohttpClientMocker) -> None:
    """HTTP and transport failures map to DriveError and DriveAuthError."""
    aioclient_mock.get(f"{FILES_URL}/unauthorized", status=401)
    aioclient_mock.get(f"{FILES_URL}/forbidden", status=403, text="API not enabled")
    aioclient_mock.get(f"{FILES_URL}/offline", exc=ClientError("boom"))
    aioclient_mock.get(f"{FILES_URL}/timeout", exc=TimeoutError())
    aioclient_mock.get(f"{FILES_URL}/html", text="<html>")
    aioclient_mock.get(f"{FILES_URL}/list", json=[1, 2])

    with pytest.raises(DriveAuthError):
        await client.download("unauthorized")
    with pytest.raises(DriveError, match="403: API not enabled"):
        await client.download("forbidden")
    with pytest.raises(DriveError, match="Cannot reach"):
        await client.download("offline")
    with pytest.raises(DriveError, match="Cannot reach"):
        await client.download("timeout")
    with pytest.raises(DriveError, match="Unexpected response"):
        await client.get_folder("html")
    with pytest.raises(DriveError, match="Unexpected response"):
        await client.get_folder("list")


async def test_transient_errors_are_retried(
    client: DriveClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """Throttling, outages and network errors are retried until they pass."""
    responses = iter(
        [
            {"status": 429, "text": "slow down"},
            {"status": 403, "text": '{"reason": "userRateLimitExceeded"}'},
            {"status": 200, "text": "content"},
            {"status": 503},
            {"exc": TimeoutError()},
            {"status": 200, "text": "late"},
        ]
    )

    async def respond(method, url, data):
        response = next(responses)
        if "exc" in response:
            raise response["exc"]
        return AiohttpClientMockResponse(method, url, **response)

    aioclient_mock.get(f"{FILES_URL}/abc", side_effect=respond)

    assert await client.download("abc") == b"content"
    assert aioclient_mock.call_count == 3
    assert await client.download("abc") == b"late"
    assert aioclient_mock.call_count == 6


async def test_retries_are_limited(
    client: DriveClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """A persistent outage fails after three attempts; other errors fail at once."""
    aioclient_mock.get(f"{FILES_URL}/down", status=500, text="backend error")
    aioclient_mock.get(f"{FILES_URL}/gone", status=404, text="not found")
    aioclient_mock.get(f"{FILES_URL}/denied", status=401)

    with pytest.raises(DriveError, match="500: backend error"):
        await client.download("down")
    assert aioclient_mock.call_count == 3

    with pytest.raises(DriveError, match="404"):
        await client.download("gone")
    assert aioclient_mock.call_count == 4

    with pytest.raises(DriveAuthError):
        await client.download("denied")
    assert aioclient_mock.call_count == 5


async def test_list_files_modified_after(
    client: DriveClient, aioclient_mock: AiohttpClientMocker
) -> None:
    """An incremental listing filters on the modification time in UTC."""
    aioclient_mock.get(FILES_URL, json={"files": []})
    await client.list_files(
        "folder", datetime(2026, 10, 2, 9, 30, 15, tzinfo=timezone(timedelta(hours=2)))
    )
    query = aioclient_mock.mock_calls[0][1].query["q"]
    assert query.endswith(" and modifiedTime > '2026-10-02T07:30:15'")
