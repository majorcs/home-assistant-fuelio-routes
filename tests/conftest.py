"""Shared fixtures: synthetic Fuelio files and a mocked Google Drive."""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from datetime import datetime, timedelta
import io
import time
from typing import Any
from unittest.mock import patch
import zipfile

from homeassistant.components.application_credentials import (
    ClientCredential,
    async_import_client_credential,
)
from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fuelio_routes.api import DriveFile
from custom_components.fuelio_routes.const import (
    CONF_FOLDER_ID,
    CONF_FOLDER_NAME,
    DOMAIN,
)

CLIENT_ID = "client-id"
CLIENT_SECRET = "client-secret"
FOLDER_ID = "folder123"

# 2026-10-01 10:00:00 UTC, i.e. 12:00 local time in the Europe/Budapest test zone.
TRIP_A = 1790848800000
# 2026-10-01 21:30:00 UTC, still 1 October in Budapest (23:30).
TRIP_B = 1790890200000
# 2026-10-01 22:30:00 UTC, already 2 October in Budapest (00:30).
TRIP_C = 1790893800000

TRACK = [(47.0, 19.0), (47.001, 19.001), (47.002, 19.003), (47.004, 19.004)]


def make_csv(start_ms: int, track: list[tuple[float, float]] = TRACK) -> bytes:
    """Build a .csv file: GPS fixes two seconds apart."""
    rows = [
        f"{start_ms + 2000 * i},{lat},{lon},12.5,{5.0 + i},180.0,10.0"
        for i, (lat, lon) in enumerate(track)
    ]
    return ("\n".join(rows) + "\n").encode()


def make_data(start_ms: int, track: list[tuple[float, float]] = TRACK) -> bytes:
    """Build a .data file: a zip holding the CSV."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("route.csv", make_csv(start_ms, track))
    return buffer.getvalue()


def make_gpx(
    start_ms: int,
    track: list[tuple[float, float]] = TRACK,
    *,
    name: str | None = "Fuelio: Evening drive",
    waypoints: tuple[str, ...] = ("Start street", "End street"),
    with_time: bool = True,
) -> bytes:
    """Build a .gpx file as Fuelio writes it."""
    lines = ['<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">']
    if name is not None:
        lines.append(f"<metadata><name>{name}</name></metadata>")
    lines.extend(
        f'<wpt lat="47.0" lon="19.0"><name>{wpt}</name></wpt>' for wpt in waypoints
    )
    lines.append("<trk><trkseg>")
    for i, (lat, lon) in enumerate(track):
        stamp = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(start_ms / 1000 + 2 * i)
        )
        body = f"<time>{stamp}</time>" if with_time else ""
        lines.append(f'<trkpt lat="{lat}" lon="{lon}">{body}</trkpt>')
    lines.append("</trkseg></trk></gpx>")
    return "\n".join(lines).encode()


def encode_polyline(track: list[tuple[float, float]]) -> str:
    """Encode coordinates as a Google polyline."""
    result = []
    previous = (0, 0)
    for lat, lon in track:
        current = (round(lat * 1e5), round(lon * 1e5))
        for delta in (current[0] - previous[0], current[1] - previous[1]):
            value = ~(delta << 1) if delta < 0 else delta << 1
            while value >= 0x20:
                result.append(chr((0x20 | (value & 0x1F)) + 63))
                value >>= 5
            result.append(chr(value + 63))
        previous = current
    return "".join(result)


def make_route(track: list[tuple[float, float]] = TRACK) -> bytes:
    """Build a .route file."""
    return encode_polyline(track).encode()


class FakeDrive:
    """In-memory stand-in for DriveClient."""

    def __init__(self) -> None:
        """Initialize an empty drive."""
        self.files: dict[str, tuple[DriveFile, bytes]] = {}
        self.folders: dict[str, list[DriveFile]] = {}
        self.error: Exception | None = None
        self.download_errors: dict[str, Exception] = {}
        self.downloads: list[str] = []
        self.list_calls: list[datetime | None] = []
        self._uploads = 0
        # Set to an asyncio.Event to make downloads wait until it is set.
        self.gate: asyncio.Event | None = None

    def add(
        self, name: str, content: bytes, modified: str | None = None, copy: str = ""
    ) -> str:
        """Add or replace a file, by default uploaded just now, and return its id.

        ``copy`` adds another Drive file with the same name, as Fuelio does.
        """
        if modified is None:
            self._uploads += 1
            modified = (dt_util.utcnow() + timedelta(seconds=self._uploads)).strftime(
                "%Y-%m-%dT%H:%M:%S.000Z"
            )
        file_id = f"id-{name}{copy}"
        self.files[file_id] = (
            DriveFile(id=file_id, name=name, modified=modified),
            content,
        )
        return file_id

    async def list_files(
        self, folder_id: str, modified_after: datetime | None = None
    ) -> list[DriveFile]:
        """Return all files, or those modified after a point in time."""
        if self.error:
            raise self.error
        self.list_calls.append(modified_after)
        return [
            file
            for file, _ in self.files.values()
            if modified_after is None
            or dt_util.parse_datetime(file.modified) > modified_after
        ]

    async def list_folders(self, parent_id: str) -> list[DriveFile]:
        """Return the sub-folders of a folder."""
        if self.error:
            raise self.error
        return self.folders.get(parent_id, [])

    async def get_folder(self, folder_id: str) -> DriveFile:
        """Return a folder by id."""
        if self.error:
            raise self.error
        return DriveFile(id=folder_id, name=f"Folder {folder_id}")

    async def download(self, file_id: str) -> bytes:
        """Return the content of a file."""
        if self.gate is not None:
            await self.gate.wait()
        if file_id in self.download_errors:
            raise self.download_errors[file_id]
        self.downloads.append(file_id)
        return self.files[file_id][1]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable loading the integration from custom_components."""


@pytest.fixture(autouse=True)
async def base_setup(hass: HomeAssistant) -> None:
    """Use a fixed time zone and stand in for the frontend, which is not installed."""
    await hass.config.async_set_time_zone("Europe/Budapest")
    hass.config.components.add("frontend")
    hass.data[DATA_EXTRA_MODULE_URL] = set()


@pytest.fixture(autouse=True)
def no_retry_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retry Drive requests without waiting."""
    monkeypatch.setattr("custom_components.fuelio_routes.api.RETRY_BACKOFF_SECONDS", 0)


@pytest.fixture
async def credentials(hass: HomeAssistant) -> None:
    """Register the Google OAuth client."""
    assert await async_setup_component(hass, "application_credentials", {})
    await async_import_client_credential(
        hass, DOMAIN, ClientCredential(CLIENT_ID, CLIENT_SECRET), DOMAIN
    )


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return a config entry with a token that is valid for a long time."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Fuelio",
        unique_id=FOLDER_ID,
        data={
            "auth_implementation": DOMAIN,
            "token": {
                "access_token": "access",
                "refresh_token": "refresh",
                "expires_at": time.time() + 3600,
                "token_type": "Bearer",
            },
            CONF_FOLDER_ID: FOLDER_ID,
            CONF_FOLDER_NAME: "Fuelio",
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def drive() -> Generator[FakeDrive]:
    """Replace the Drive client used by the integration and the config flow."""
    fake = FakeDrive()
    with (
        patch("custom_components.fuelio_routes.DriveClient", return_value=fake),
        patch(
            "custom_components.fuelio_routes.config_flow.DriveClient", return_value=fake
        ),
    ):
        yield fake


@pytest.fixture
def populated_drive(drive: FakeDrive) -> FakeDrive:
    """Drive with three trips covering every combination of file types."""
    drive.add(f"route-{TRIP_A}.data", make_data(TRIP_A + 5000))
    drive.add(f"route-{TRIP_A}.gpx", make_gpx(TRIP_A + 5000 + 7200000))
    drive.add(f"route-{TRIP_A}.route", make_route())
    drive.add(
        f"route-{TRIP_B}.gpx",
        make_gpx(TRIP_B + 9000 + 7200000, name=None, waypoints=()),
    )
    drive.add(f"route-{TRIP_C}.route", make_route())
    drive.add("backup.csv", b"not a route")
    return drive


async def setup_integration(hass: HomeAssistant, entry: MockConfigEntry) -> Any:
    """Set up the config entry and wait for the initial background sync."""
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    return entry.runtime_data
