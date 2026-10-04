"""Synchronize Fuelio route files from Google Drive into a local cache."""

from __future__ import annotations

import asyncio
from datetime import date, datetime
import logging
from pathlib import Path
import shutil
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import DriveAuthError, DriveClient, DriveError, DriveFile
from .const import (
    CONF_FOLDER_ID,
    DOMAIN,
    DOWNLOAD_CONCURRENCY,
    LISTING_OVERLAP,
    RETRY_INTERVAL,
    SAVE_EVERY_N_DOWNLOADS,
    SCAN_INTERVAL,
)
from .parser import (
    EXT_CSV,
    EXT_DATA,
    EXT_ROUTE,
    PARSER_VERSION,
    ParseError,
    Trip,
    build_trip,
    parse_file_name,
)

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1

type FuelioRoutesConfigEntry = ConfigEntry[FuelioRoutesCoordinator]


def storage_key(entry_id: str) -> str:
    """Return the key of the index store of a config entry."""
    return f"{DOMAIN}.{entry_id}"


def cache_dir(hass: HomeAssistant, entry_id: str) -> Path:
    """Return the directory holding the downloaded route files of a config entry.

    It lives in the cache directory, which backups skip: the files can always be
    downloaded again.
    """
    return Path(hass.config.cache_path(DOMAIN, entry_id))


def _read_trip(directory: Path, trip_id: str) -> Trip:
    files: dict[str, bytes] = {}
    for path in directory.glob(f"route-{trip_id}.*"):
        if (parsed := parse_file_name(path.name)) and parsed[0] == trip_id:
            files[parsed[1]] = path.read_bytes()
    return build_trip(trip_id, files)


def _summarize(
    directory: Path, trip_ids: list[str]
) -> dict[str, dict[str, Any] | None]:
    result: dict[str, dict[str, Any] | None] = {}
    for trip_id in trip_ids:
        try:
            trip = _read_trip(directory, trip_id)
        except (ParseError, OSError) as err:
            _LOGGER.warning("Skipping unreadable Fuelio trip %s: %s", trip_id, err)
            result[trip_id] = None
            continue
        for error in trip.errors:
            _LOGGER.debug("Fuelio trip %s: ignored file (%s)", trip_id, error)
        result[trip_id] = trip.summary()
    return result


def _read_trips(directory: Path, trip_ids: list[str]) -> dict[str, Trip]:
    trips: dict[str, Trip] = {}
    for trip_id in trip_ids:
        try:
            trips[trip_id] = _read_trip(directory, trip_id)
        except (ParseError, OSError) as err:
            _LOGGER.warning("Cannot read Fuelio trip %s: %s", trip_id, err)
    return trips


def _write_file(directory: Path, name: str, content: bytes) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / f".{name}.tmp"
    temporary.write_bytes(content)
    temporary.replace(directory / name)


def _local_trips(directory: Path) -> dict[str, str]:
    """Return the name -> trip id of every route file in the cache."""
    if not directory.is_dir():
        return {}
    return {
        path.name: parsed[0]
        for path in directory.iterdir()
        if (parsed := parse_file_name(path.name))
    }


def _select_wanted(
    listing: list[DriveFile], cached: dict[str, set[str]]
) -> list[DriveFile]:
    """Pick the files worth downloading from a Drive listing.

    Drive may hold several files with the same name (Fuelio uploads the same trip
    more than once): the most recently modified one is used. A file is skipped
    when a richer one of the same trip is listed or already cached: the plain CSV
    when there is a ``.data`` file, the polyline when there is anything else.
    """
    by_trip: dict[str, dict[str, DriveFile]] = {}
    for file in listing:
        if not (parsed := parse_file_name(file.name)):
            continue
        files = by_trip.setdefault(parsed[0], {})
        current = files.get(parsed[1])
        if current is None or (file.modified, file.id) > (current.modified, current.id):
            files[parsed[1]] = file
    wanted: list[DriveFile] = []
    for trip_id, files in by_trip.items():
        extensions = set(files) | cached.get(trip_id, set())
        for extension, file in files.items():
            if extension == EXT_ROUTE and extensions - {EXT_ROUTE}:
                continue
            if extension == EXT_CSV and EXT_DATA in extensions:
                continue
            wanted.append(file)
    return wanted


def _trip_start(file: DriveFile) -> int:
    parsed = parse_file_name(file.name)
    return int(parsed[0]) if parsed else 0


class FuelioRoutesCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Keep a local mirror of the route files and an index of the trips in them."""

    config_entry: FuelioRoutesConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: FuelioRoutesConfigEntry,
        client: DriveClient,
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.title}",
            update_interval=SCAN_INTERVAL,
        )
        self.client = client
        self.folder_id: str = entry.data[CONF_FOLDER_ID]
        self.directory = cache_dir(hass, entry.entry_id)
        self._store: Store[dict[str, Any]] = Store(
            hass, STORAGE_VERSION, storage_key(entry.entry_id)
        )
        # Drive file id -> {"name", "modified"} of every downloaded file.
        self.files: dict[str, dict[str, str]] = {}
        # Trip id -> summary as produced by Trip.summary().
        self.trips: dict[str, dict[str, Any]] = {}
        self.last_sync: datetime | None = None
        # Start of the last complete synchronization: later ones only ask Drive
        # for files changed since then. None forces a listing of the whole folder.
        self.listed_until: datetime | None = None
        self._sync_lock = asyncio.Lock()
        self._full_listing_requested = False
        self.sync_total = 0
        self.sync_done = 0

    @property
    def syncing(self) -> bool:
        """Return True while a synchronization with Google Drive is running."""
        return self._sync_lock.locked()

    @property
    def pending_files(self) -> int:
        """Return the number of files the running synchronization still has to fetch."""
        return self.sync_total - self.sync_done

    async def async_load(self) -> None:
        """Restore the index and bring it in line with the cached files.

        The index is rebuilt when the parser has changed, and files that are gone
        from the cache (it is not part of backups) are forgotten so that the next
        synchronization downloads them again.
        """
        stored = await self._store.async_load() or {}
        self.files = stored.get("files", {})
        self.trips = stored.get("trips", {})
        if last_sync := stored.get("last_sync"):
            self.last_sync = dt_util.parse_datetime(last_sync)
        if listed_until := stored.get("listed_until"):
            self.listed_until = dt_util.parse_datetime(listed_until)
        if not stored:
            return

        local = await self.hass.async_add_executor_job(_local_trips, self.directory)
        missing = [
            file_id for file_id, file in self.files.items() if file["name"] not in local
        ]
        for file_id in missing:
            del self.files[file_id]
        if missing:
            self.listed_until = None
        if missing or stored.get("parser_version") != PARSER_VERSION:
            self.trips = {}
            await self._async_index(sorted(set(local.values())))
            await self._async_save()

    async def _async_save(self) -> None:
        await self._store.async_save(
            {
                "parser_version": PARSER_VERSION,
                "files": self.files,
                "trips": self.trips,
                "last_sync": self.last_sync.isoformat() if self.last_sync else None,
                "listed_until": (
                    self.listed_until.isoformat() if self.listed_until else None
                ),
            }
        )

    async def _async_index(self, trip_ids: list[str]) -> None:
        summaries = await self.hass.async_add_executor_job(
            _summarize, self.directory, trip_ids
        )
        for trip_id, summary in summaries.items():
            if summary is None:
                self.trips.pop(trip_id, None)
            else:
                self.trips[trip_id] = summary

    async def _async_update_data(self) -> dict[str, Any]:
        async with self._sync_lock:
            return await self._async_sync()

    async def _async_sync(self) -> dict[str, Any]:
        started = dt_util.utcnow()
        modified_after = (
            None if self.listed_until is None else self.listed_until - LISTING_OVERLAP
        )
        self._full_listing_requested = False
        try:
            listing = await self.client.list_files(self.folder_id, modified_after)
            _LOGGER.debug(
                "Google Drive listed %d files (%s)",
                len(listing),
                "whole folder"
                if modified_after is None
                else f"changed since {modified_after}",
            )
            cached: dict[str, set[str]] = {}
            # Newest known upload per file name: Drive can hold the same name
            # several times, under different ids.
            cached_modified: dict[str, str] = {}
            for file in self.files.values():
                if parsed := parse_file_name(file["name"]):
                    cached.setdefault(parsed[0], set()).add(parsed[1])
                if file["modified"] > cached_modified.get(file["name"], ""):
                    cached_modified[file["name"]] = file["modified"]
            # Newest trips first, so recent days are usable early in a long import.
            pending = sorted(
                (
                    file
                    for file in _select_wanted(listing, cached)
                    if file.modified > cached_modified.get(file.name, "")
                ),
                key=_trip_start,
                reverse=True,
            )
            self.sync_total = len(pending)
            self.async_update_listeners()
            new_trips, failures = await self._async_download(pending)
        except DriveAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except DriveError as err:
            self.update_interval = RETRY_INTERVAL
            raise UpdateFailed(str(err)) from err
        finally:
            self.sync_total = self.sync_done = 0
            self.async_update_listeners()

        if failures:
            self.update_interval = RETRY_INTERVAL
            raise UpdateFailed(
                f"{len(failures)} of {len(pending)} files could not be downloaded"
                f" and will be retried (first error: {failures[0]})"
            )
        self.update_interval = SCAN_INTERVAL
        # A full rescan asked for while this run was under way must still happen.
        self.listed_until = None if self._full_listing_requested else started
        self.last_sync = dt_util.utcnow()
        await self._async_save()
        return {"new_trips": new_trips, "downloaded_files": len(pending)}

    async def _async_download(
        self, pending: list[DriveFile]
    ) -> tuple[int, list[BaseException]]:
        """Download files in batches so an interrupted import keeps its progress.

        A file that fails is skipped and reported; the run only stops early when
        Google rejects the token or a whole batch fails, which means Drive is down.
        """
        known = set(self.trips)
        added: set[str] = set()
        failures: list[BaseException] = []
        semaphore = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)

        async def fetch(file: DriveFile) -> None:
            async with semaphore:
                content = await self.client.download(file.id)
            await self.hass.async_add_executor_job(
                _write_file, self.directory, file.name, content
            )

        for offset in range(0, len(pending), SAVE_EVERY_N_DOWNLOADS):
            batch = pending[offset : offset + SAVE_EVERY_N_DOWNLOADS]
            results = await asyncio.gather(
                *(fetch(file) for file in batch), return_exceptions=True
            )
            batch_failures: list[BaseException] = []
            changed: set[str] = set()
            for file, result in zip(batch, results, strict=True):
                if isinstance(result, BaseException):
                    _LOGGER.debug("Could not download %s: %s", file.name, result)
                    batch_failures.append(result)
                    continue
                self.files[file.id] = {"name": file.name, "modified": file.modified}
                if parsed := parse_file_name(file.name):
                    changed.add(parsed[0])
            await self._async_index(sorted(changed))
            added |= changed & set(self.trips)
            self.sync_done += len(batch)
            await self._async_save()
            self.async_update_listeners()

            failures.extend(batch_failures)
            for failure in batch_failures:
                if isinstance(failure, DriveAuthError):
                    raise failure
            if len(batch_failures) == len(batch):
                raise DriveError(
                    f"Every download failed (first error: {batch_failures[0]})"
                )
        return len(added - known), failures

    async def async_full_rescan(self) -> None:
        """Compare the whole Drive folder with the cache instead of recent changes only.

        The request is stored, so a rescan interrupted by a restart starts over.
        """
        self._full_listing_requested = True
        self.listed_until = None
        await self._async_save()
        await self.async_refresh()

    def _local_date(self, summary: dict[str, Any]) -> date:
        return dt_util.as_local(
            dt_util.utc_from_timestamp(summary["start"] / 1000)
        ).date()

    def days(self, min_distance: float = 0) -> dict[str, int]:
        """Return the number of trips per local calendar day (ISO date).

        Trips shorter than ``min_distance`` meters are not counted.
        """
        counts: dict[str, int] = {}
        for summary in self.trips.values():
            if summary["distance"] < min_distance:
                continue
            day = self._local_date(summary).isoformat()
            counts[day] = counts.get(day, 0) + 1
        return counts

    def trip_ids_on(self, day: date) -> list[str]:
        """Return the ids of the trips starting on a local calendar day, oldest first."""
        return sorted(
            (
                trip_id
                for trip_id, summary in self.trips.items()
                if self._local_date(summary) == day
            ),
            key=lambda trip_id: self.trips[trip_id]["start"],
        )

    async def async_get_trips(self, trip_ids: list[str]) -> dict[str, Trip]:
        """Load trips including their points from the local cache."""
        return await self.hass.async_add_executor_job(
            _read_trips, self.directory, trip_ids
        )

    @property
    def latest_trip(self) -> dict[str, Any] | None:
        """Return the summary of the most recent trip."""
        if not self.trips:
            return None
        return max(self.trips.values(), key=lambda summary: summary["start"])


async def async_remove_cache(hass: HomeAssistant, entry_id: str) -> None:
    """Delete everything stored for a config entry."""
    await Store[dict[str, Any]](
        hass, STORAGE_VERSION, storage_key(entry_id)
    ).async_remove()
    await hass.async_add_executor_job(
        lambda: shutil.rmtree(cache_dir(hass, entry_id), ignore_errors=True)
    )
