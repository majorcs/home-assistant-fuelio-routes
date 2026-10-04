"""Tests for setup, synchronization and entities."""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from pathlib import Path
import shutil
from typing import Any
from unittest.mock import Mock, patch

from aiohttp import ClientError, ClientResponseError
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.fuelio_routes.api import DriveAuthError, DriveError
from custom_components.fuelio_routes.application_credentials import (
    async_get_description_placeholders,
)
from custom_components.fuelio_routes.const import (
    CARD_FILENAME,
    DOMAIN,
    LISTING_OVERLAP,
    RETRY_INTERVAL,
    SCAN_INTERVAL,
)
from custom_components.fuelio_routes.coordinator import cache_dir, storage_key

from .conftest import (
    TRIP_A,
    TRIP_B,
    TRIP_C,
    FakeDrive,
    make_csv,
    make_data,
    make_gpx,
    make_route,
    setup_integration,
)

pytestmark = pytest.mark.usefixtures("credentials")


@pytest.fixture(autouse=True)
def cache_in_tmp_path(hass: HomeAssistant, tmp_path: Path) -> None:
    """Keep downloaded files out of the shared test configuration directory."""
    hass.config.config_dir = str(tmp_path)


def _entity_id(
    hass: HomeAssistant, entry: MockConfigEntry, platform: str, key: str
) -> str:
    entity_id = er.async_get(hass).async_get_entity_id(
        platform, DOMAIN, f"{entry.entry_id}_{key}"
    )
    assert entity_id is not None
    return entity_id


async def test_setup_downloads_and_indexes(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    populated_drive: FakeDrive,
    hass_storage: dict[str, Any],
) -> None:
    """The first sync mirrors the wanted files and builds the trip index."""
    coordinator = await setup_integration(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    assert set(coordinator.trips) == {str(TRIP_A), str(TRIP_B), str(TRIP_C)}
    # The polyline of trip A is redundant and the backup file is not a route.
    assert sorted(populated_drive.downloads) == sorted(
        [
            f"id-route-{TRIP_A}.data",
            f"id-route-{TRIP_A}.gpx",
            f"id-route-{TRIP_B}.gpx",
            f"id-route-{TRIP_C}.route",
        ]
    )
    assert coordinator.trips[str(TRIP_A)]["source"] == "data"
    assert coordinator.trips[str(TRIP_A)]["name"] == "Evening drive"
    assert coordinator.trips[str(TRIP_B)]["start"] == TRIP_B + 9000
    assert coordinator.data == {"new_trips": 3, "downloaded_files": 4}
    assert coordinator.last_sync is not None

    files = {path.name for path in cache_dir(hass, config_entry.entry_id).iterdir()}
    assert files == {
        f"route-{TRIP_A}.data",
        f"route-{TRIP_A}.gpx",
        f"route-{TRIP_B}.gpx",
        f"route-{TRIP_C}.route",
    }
    stored = hass_storage[storage_key(config_entry.entry_id)]["data"]
    assert set(stored["trips"]) == set(coordinator.trips)

    # Days follow the Home Assistant time zone, not UTC.
    assert coordinator.days() == {"2026-10-01": 2, "2026-10-02": 1}
    assert coordinator.trip_ids_on(date(2026, 10, 1)) == [str(TRIP_A), str(TRIP_B)]
    assert coordinator.latest_trip["id"] == str(TRIP_C)


async def test_resync_only_fetches_new_and_changed_files(
    hass: HomeAssistant, config_entry: MockConfigEntry, populated_drive: FakeDrive
) -> None:
    """Unchanged files are not downloaded again."""
    coordinator = await setup_integration(hass, config_entry)
    populated_drive.downloads.clear()

    await coordinator.async_refresh()
    assert populated_drive.downloads == []
    assert coordinator.data == {"new_trips": 0, "downloaded_files": 0}

    populated_drive.add(f"route-{TRIP_C}.gpx", make_gpx(TRIP_C + 7200000))
    populated_drive.add(f"route-{TRIP_B}.gpx", make_gpx(TRIP_B + 7200000))
    await coordinator.async_refresh()

    assert sorted(populated_drive.downloads) == sorted(
        [f"id-route-{TRIP_C}.gpx", f"id-route-{TRIP_B}.gpx"]
    )
    assert coordinator.data == {"new_trips": 0, "downloaded_files": 2}
    assert coordinator.trips[str(TRIP_C)]["source"] == "gpx"
    assert coordinator.trips[str(TRIP_B)]["name"] == "Evening drive"


async def test_csv_only_trips_and_duplicate_uploads(
    hass: HomeAssistant, config_entry: MockConfigEntry, drive: FakeDrive
) -> None:
    """CSV-only trips are imported and of duplicate uploads the newest is used."""
    csv_only = TRIP_A
    drive.add(
        f"route-{csv_only}.csv", make_csv(csv_only + 1000), "2026-01-01T00:00:00.000Z"
    )
    drive.add(
        f"route-{csv_only}.csv",
        make_csv(csv_only + 9000),
        "2026-03-01T00:00:00.000Z",
        copy="#2",
    )
    drive.add(
        f"route-{csv_only}.csv",
        make_csv(csv_only + 5000),
        "2026-02-01T00:00:00.000Z",
        copy="#3",
    )
    # The CSV of a trip that also has a .data file is redundant.
    drive.add(f"route-{TRIP_B}.data", make_data(TRIP_B))
    drive.add(f"route-{TRIP_B}.csv", make_csv(TRIP_B))
    drive.add(f"route-{TRIP_B}.route", make_route())

    coordinator = await setup_integration(hass, config_entry)

    assert sorted(drive.downloads) == sorted(
        [f"id-route-{csv_only}.csv#2", f"id-route-{TRIP_B}.data"]
    )
    assert coordinator.trips[str(csv_only)]["source"] == "csv"
    assert coordinator.trips[str(csv_only)]["start"] == csv_only + 9000
    assert coordinator.trips[str(TRIP_B)]["source"] == "data"

    # A full rescan does not fetch the older copies, nor the same content again.
    drive.downloads.clear()
    await coordinator.async_full_rescan()
    assert drive.downloads == []

    # A later re-upload of the same name replaces the cached file.
    drive.add(f"route-{csv_only}.csv", make_csv(csv_only + 2000), copy="#4")
    # The CSV of a trip whose .data file is cached already stays redundant.
    drive.add(f"route-{TRIP_B}.csv", make_csv(TRIP_B), copy="#2")
    await coordinator.async_refresh()
    assert drive.downloads == [f"id-route-{csv_only}.csv#4"]
    assert coordinator.trips[str(csv_only)]["start"] == csv_only + 2000


async def test_later_syncs_only_list_recent_changes(
    hass: HomeAssistant, config_entry: MockConfigEntry, populated_drive: FakeDrive
) -> None:
    """After a complete sync Drive is only asked for files changed since then."""
    before = dt_util.utcnow()
    coordinator = await setup_integration(hass, config_entry)
    assert populated_drive.list_calls == [None]
    assert before <= coordinator.listed_until <= dt_util.utcnow()
    first_sync = coordinator.listed_until

    late_upload = TRIP_A - 86400000 * 30
    populated_drive.add(f"route-{late_upload}.data", make_data(late_upload))
    populated_drive.add(
        f"route-{TRIP_A - 600000}.data",
        make_data(TRIP_A),
        modified="2020-01-01T00:00:00.000Z",
    )
    # The polyline of a trip that already has richer files is not worth fetching.
    populated_drive.add(f"route-{TRIP_B}.route", make_route())
    populated_drive.downloads.clear()
    await coordinator.async_refresh()

    # The listing overlaps the previous sync by a day to tolerate clock differences.
    assert populated_drive.list_calls[-1] == first_sync - LISTING_OVERLAP
    # A trip from a month ago that Fuelio uploaded only now is picked up;
    # a file that has been sitting unchanged in Drive is not asked for.
    assert populated_drive.downloads == [f"id-route-{late_upload}.data"]
    assert str(late_upload) in coordinator.trips
    assert coordinator.listed_until > first_sync


async def test_full_rescan_button_finds_files_an_incremental_sync_misses(
    hass: HomeAssistant, config_entry: MockConfigEntry, populated_drive: FakeDrive
) -> None:
    """The full rescan lists the whole folder once; later syncs are incremental again."""
    coordinator = await setup_integration(hass, config_entry)
    overlooked = TRIP_A - 600000
    populated_drive.add(
        f"route-{overlooked}.data",
        make_data(overlooked),
        modified="2020-01-01T00:00:00.000Z",
    )
    await coordinator.async_refresh()
    assert str(overlooked) not in coordinator.trips

    await hass.services.async_call(
        "button",
        "press",
        {"entity_id": _entity_id(hass, config_entry, "button", "full_rescan")},
        blocking=True,
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert populated_drive.list_calls[-1] is None
    assert str(overlooked) in coordinator.trips

    await coordinator.async_refresh()
    assert populated_drive.list_calls[-1] is not None


async def test_interrupted_full_rescan_is_repeated_after_restart(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    populated_drive: FakeDrive,
    hass_storage: dict[str, Any],
) -> None:
    """A rescan that did not finish is not forgotten."""
    coordinator = await setup_integration(hass, config_entry)
    overlooked = TRIP_A - 600000
    populated_drive.add(
        f"route-{overlooked}.data",
        make_data(overlooked),
        modified="2020-01-01T00:00:00.000Z",
    )
    populated_drive.error = DriveError("offline")
    await coordinator.async_full_rescan()
    assert coordinator.last_update_success is False
    assert (
        hass_storage[storage_key(config_entry.entry_id)]["data"]["listed_until"] is None
    )

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    populated_drive.error = None
    coordinator = await setup_integration(hass, config_entry)

    assert populated_drive.list_calls[-1] is None
    assert str(overlooked) in coordinator.trips
    assert coordinator.listed_until is not None


async def test_full_rescan_requested_during_a_sync_runs_afterwards(
    hass: HomeAssistant, config_entry: MockConfigEntry, populated_drive: FakeDrive
) -> None:
    """Pressing rescan while a sync is running queues a whole-folder listing."""
    coordinator = await setup_integration(hass, config_entry)
    new_trip = TRIP_C + 3600000
    populated_drive.add(f"route-{new_trip}.data", make_data(new_trip))
    overlooked = TRIP_A - 600000
    populated_drive.add(
        f"route-{overlooked}.data",
        make_data(overlooked),
        modified="2020-01-01T00:00:00.000Z",
    )
    populated_drive.gate = asyncio.Event()
    downloading = asyncio.Event()
    unsubscribe = coordinator.async_add_listener(downloading.set)
    running = hass.async_create_task(coordinator.async_refresh())
    await downloading.wait()
    unsubscribe()
    rescan = hass.async_create_task(coordinator.async_full_rescan())
    await asyncio.sleep(0)
    populated_drive.gate.set()
    await running
    await rescan

    assert populated_drive.list_calls[-1] is None
    assert str(overlooked) in coordinator.trips
    assert coordinator.listed_until is not None


async def test_incomplete_sync_does_not_advance_the_listing(
    hass: HomeAssistant, config_entry: MockConfigEntry, populated_drive: FakeDrive
) -> None:
    """Files that failed are listed again by the next synchronization."""
    failing = f"id-route-{TRIP_B}.gpx"
    populated_drive.download_errors[failing] = DriveError("flaky")
    coordinator = await setup_integration(hass, config_entry)
    assert coordinator.listed_until is None

    populated_drive.download_errors.clear()
    await coordinator.async_refresh()

    assert populated_drive.list_calls == [None, None]
    assert str(TRIP_B) in coordinator.trips
    assert coordinator.listed_until is not None


async def test_daily_refresh(
    hass: HomeAssistant, config_entry: MockConfigEntry, populated_drive: FakeDrive
) -> None:
    """New trips are picked up once a day without user action."""
    coordinator = await setup_integration(hass, config_entry)
    new_trip = TRIP_C + 3600000
    populated_drive.add(f"route-{new_trip}.data", make_data(new_trip))

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(hours=23))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert str(new_trip) not in coordinator.trips

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(days=1, minutes=1))
    await hass.async_block_till_done(wait_background_tasks=True)
    assert str(new_trip) in coordinator.trips


async def test_index_survives_restart_without_drive(
    hass: HomeAssistant, config_entry: MockConfigEntry, populated_drive: FakeDrive
) -> None:
    """After a restart the cached trips are available even if Drive is down."""
    await setup_integration(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)

    populated_drive.error = DriveError("offline")
    coordinator = await setup_integration(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    assert coordinator.last_update_success is False
    assert len(coordinator.trips) == 3
    trips = await coordinator.async_get_trips([str(TRIP_A), "404"])
    assert list(trips) == [str(TRIP_A)]
    assert len(trips[str(TRIP_A)].points) == 4

    state = hass.states.get(_entity_id(hass, config_entry, "sensor", "trips"))
    assert state.state == "3"


async def test_parser_change_rebuilds_index(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    populated_drive: FakeDrive,
    hass_storage: dict[str, Any],
) -> None:
    """A new parser version re-reads the cached files instead of downloading."""
    await setup_integration(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    stored = hass_storage[storage_key(config_entry.entry_id)]["data"]
    stored["parser_version"] = -1
    stored["trips"] = {"1": {"start": 1}}
    populated_drive.downloads.clear()

    coordinator = await setup_integration(hass, config_entry)

    assert set(coordinator.trips) == {str(TRIP_A), str(TRIP_B), str(TRIP_C)}
    assert populated_drive.downloads == []


async def test_cache_is_outside_backups_and_refilled_when_lost(
    hass: HomeAssistant, config_entry: MockConfigEntry, populated_drive: FakeDrive
) -> None:
    """The routes live in the cache directory and are fetched again after a restore."""
    await setup_integration(hass, config_entry)
    directory = cache_dir(hass, config_entry.entry_id)
    assert directory == Path(hass.config.cache_path(DOMAIN, config_entry.entry_id))
    assert await hass.config_entries.async_unload(config_entry.entry_id)

    # A restored backup brings back the index but not the cache.
    shutil.rmtree(directory)
    populated_drive.downloads.clear()
    populated_drive.gate = asyncio.Event()
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = config_entry.runtime_data

    # Nothing pretends to be there before the files are back.
    assert coordinator.trips == {}
    assert coordinator.files == {}

    populated_drive.gate.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    # The whole folder is listed again, not just recent changes.
    assert populated_drive.list_calls[-1] is None
    assert len(populated_drive.downloads) == 4
    assert set(coordinator.trips) == {str(TRIP_A), str(TRIP_B), str(TRIP_C)}


async def test_partially_lost_cache_is_repaired(
    hass: HomeAssistant, config_entry: MockConfigEntry, populated_drive: FakeDrive
) -> None:
    """Only the missing files are downloaded again; the rest is kept."""
    await setup_integration(hass, config_entry)
    directory = cache_dir(hass, config_entry.entry_id)
    assert await hass.config_entries.async_unload(config_entry.entry_id)

    (directory / f"route-{TRIP_A}.data").unlink()
    (directory / f"route-{TRIP_C}.route").unlink()
    populated_drive.downloads.clear()
    populated_drive.gate = asyncio.Event()
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = config_entry.runtime_data

    # Trip A falls back to its GPX file, trip C is gone until it is fetched again.
    assert set(coordinator.trips) == {str(TRIP_A), str(TRIP_B)}
    assert coordinator.trips[str(TRIP_A)]["source"] == "gpx"

    populated_drive.gate.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert sorted(populated_drive.downloads) == sorted(
        [f"id-route-{TRIP_A}.data", f"id-route-{TRIP_C}.route"]
    )
    assert coordinator.trips[str(TRIP_A)]["source"] == "data"
    assert str(TRIP_C) in coordinator.trips


async def test_unparseable_trip_is_skipped(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    drive: FakeDrive,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A corrupt file is logged, skipped and not downloaded again."""
    drive.add(f"route-{TRIP_A}.data", b"corrupt")
    drive.add(f"route-{TRIP_B}.data", b"corrupt")
    drive.add(f"route-{TRIP_B}.route", make_route())
    drive.add(f"route-{TRIP_C}.route", make_route())
    coordinator = await setup_integration(hass, config_entry)

    assert set(coordinator.trips) == {str(TRIP_C)}
    assert f"Skipping unreadable Fuelio trip {TRIP_A}" in caplog.text
    assert await coordinator.async_get_trips([str(TRIP_A)]) == {}

    drive.downloads.clear()
    await coordinator.async_refresh()
    assert drive.downloads == []


async def test_failed_download_does_not_stop_the_import(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    drive: FakeDrive,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing file is skipped, the rest is imported and a retry follows soon."""
    monkeypatch.setattr(
        "custom_components.fuelio_routes.coordinator.SAVE_EVERY_N_DOWNLOADS", 2
    )
    trip_ids = [TRIP_A + index * 600000 for index in range(6)]
    for trip_id in trip_ids:
        drive.add(f"route-{trip_id}.data", make_data(trip_id))
    # Newest first: the failing file is in the first of three batches.
    failing = f"id-route-{trip_ids[-1]}.data"
    drive.download_errors[failing] = DriveError("flaky")

    coordinator = await setup_integration(hass, config_entry)

    assert coordinator.last_update_success is False
    assert "1 of 6 files could not be downloaded" in str(coordinator.last_exception)
    assert sorted(coordinator.trips) == [str(trip_id) for trip_id in trip_ids[:-1]]
    assert drive.downloads == [
        f"id-route-{trip_id}.data" for trip_id in reversed(trip_ids[:-1])
    ]
    assert coordinator.update_interval == RETRY_INTERVAL
    assert coordinator.pending_files == 0

    drive.download_errors.clear()
    drive.downloads.clear()
    async_fire_time_changed(
        hass, dt_util.utcnow() + RETRY_INTERVAL + timedelta(minutes=1)
    )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert drive.downloads == [failing]
    assert coordinator.last_update_success is True
    assert coordinator.data == {"new_trips": 1, "downloaded_files": 1}
    assert coordinator.update_interval == SCAN_INTERVAL


async def test_import_stops_when_drive_goes_down(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    drive: FakeDrive,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a whole batch fails the import gives up instead of trying every file."""
    monkeypatch.setattr(
        "custom_components.fuelio_routes.coordinator.SAVE_EVERY_N_DOWNLOADS", 2
    )
    trip_ids = [TRIP_A + index * 600000 for index in range(6)]
    for trip_id in trip_ids:
        drive.add(f"route-{trip_id}.data", make_data(trip_id))
    for trip_id in trip_ids[:4]:
        drive.download_errors[f"id-route-{trip_id}.data"] = DriveError("offline")

    coordinator = await setup_integration(hass, config_entry)

    assert coordinator.last_update_success is False
    assert "Every download failed" in str(coordinator.last_exception)
    assert sorted(coordinator.trips) == [str(trip_id) for trip_id in trip_ids[4:]]
    assert coordinator.update_interval == RETRY_INTERVAL
    assert coordinator.pending_files == 0


async def test_rejected_token_during_import_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, populated_drive: FakeDrive
) -> None:
    """A token rejected in the middle of the import asks for a new sign-in."""
    populated_drive.download_errors[f"id-route-{TRIP_B}.gpx"] = DriveAuthError(
        "revoked"
    )
    coordinator = await setup_integration(hass, config_entry)

    assert set(coordinator.trips) == {str(TRIP_A), str(TRIP_C)}
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_large_sync_saves_in_batches(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    drive: FakeDrive,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A long first sync is processed in several batches."""
    monkeypatch.setattr(
        "custom_components.fuelio_routes.coordinator.SAVE_EVERY_N_DOWNLOADS", 2
    )
    for index in range(5):
        trip_id = TRIP_A + index * 600000
        drive.add(f"route-{trip_id}.data", make_data(trip_id))
    coordinator = await setup_integration(hass, config_entry)
    assert len(coordinator.trips) == 5
    assert coordinator.data == {"new_trips": 5, "downloaded_files": 5}


async def test_rejected_token_starts_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, populated_drive: FakeDrive
) -> None:
    """An authentication failure asks the user to sign in again."""
    populated_drive.error = DriveAuthError("expired")
    await setup_integration(hass, config_entry)

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (None, "access"),
        (
            ClientResponseError(
                Mock(real_url="https://oauth2.googleapis.com/token"), (), status=400
            ),
            DriveAuthError,
        ),
        (
            ClientResponseError(
                Mock(real_url="https://oauth2.googleapis.com/token"), (), status=503
            ),
            DriveError,
        ),
        (ClientError("offline"), DriveError),
    ],
)
async def test_access_token_provider(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    error: Exception | None,
    expected: Any,
) -> None:
    """Token refresh failures are translated for the Drive client."""
    with (
        patch("custom_components.fuelio_routes.DriveClient") as client_class,
        patch("custom_components.fuelio_routes.FuelioRoutesCoordinator.async_refresh"),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done(wait_background_tasks=True)
    token_provider = client_class.call_args.args[1]

    with patch(
        "homeassistant.helpers.config_entry_oauth2_flow.OAuth2Session.async_ensure_token_valid",
        side_effect=error,
    ):
        if error is None:
            assert await token_provider() == expected
        else:
            with pytest.raises(expected) as raised:
                await token_provider()
            assert type(raised.value) is expected


async def test_missing_credentials_retries_setup(
    hass: HomeAssistant, config_entry: MockConfigEntry, drive: FakeDrive
) -> None:
    """Setup is retried when the OAuth client has been removed."""
    with patch(
        "custom_components.fuelio_routes.async_get_config_entry_implementation",
        side_effect=ValueError("Implementation not available"),
    ):
        assert not await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_entities(
    hass: HomeAssistant, config_entry: MockConfigEntry, populated_drive: FakeDrive
) -> None:
    """Sensors describe the cache and the button synchronizes."""
    coordinator = await setup_integration(hass, config_entry)

    assert (
        hass.states.get(_entity_id(hass, config_entry, "sensor", "trips")).state == "3"
    )
    last_trip = hass.states.get(_entity_id(hass, config_entry, "sensor", "last_trip"))
    assert last_trip.state == "2026-10-01T22:30:00+00:00"
    assert last_trip.attributes["distance_km"] == 0.56
    last_sync = hass.states.get(_entity_id(hass, config_entry, "sensor", "last_sync"))
    assert dt_util.parse_datetime(last_sync.state) == coordinator.last_sync.replace(
        microsecond=0
    )

    new_trip = TRIP_C + 3600000
    populated_drive.add(f"route-{new_trip}.data", make_data(new_trip))
    with patch(
        "homeassistant.helpers.debounce.Debouncer.async_call",
        side_effect=coordinator.async_refresh,
    ):
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": _entity_id(hass, config_entry, "button", "refresh")},
            blocking=True,
        )
    await hass.async_block_till_done(wait_background_tasks=True)

    assert (
        hass.states.get(_entity_id(hass, config_entry, "sensor", "trips")).state == "4"
    )


async def test_sensors_without_trips(
    hass: HomeAssistant, config_entry: MockConfigEntry, drive: FakeDrive
) -> None:
    """An empty folder yields empty sensors, not errors."""
    drive.error = DriveError("offline")
    coordinator = await setup_integration(hass, config_entry)

    assert coordinator.latest_trip is None
    assert (
        hass.states.get(_entity_id(hass, config_entry, "sensor", "trips")).state == "0"
    )
    last_trip = hass.states.get(_entity_id(hass, config_entry, "sensor", "last_trip"))
    assert last_trip.state == "unknown"
    assert "distance_km" not in last_trip.attributes
    assert (
        hass.states.get(_entity_id(hass, config_entry, "sensor", "last_sync")).state
        == "unknown"
    )


async def test_remove_entry_deletes_cache(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    populated_drive: FakeDrive,
    hass_storage: dict[str, Any],
) -> None:
    """Removing the integration removes the downloaded routes."""
    await setup_integration(hass, config_entry)
    directory = cache_dir(hass, config_entry.entry_id)
    assert directory.is_dir()

    assert await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()

    assert not directory.exists()
    assert storage_key(config_entry.entry_id) not in hass_storage


async def test_card_is_served(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    populated_drive: FakeDrive,
    hass_client_no_auth: ClientSessionGenerator,
) -> None:
    """The bundled card and its map library are reachable by the browser."""
    await setup_integration(hass, config_entry)
    client = await hass_client_no_auth()

    for path in (CARD_FILENAME, "leaflet/leaflet-src.esm.js", "leaflet/leaflet.css"):
        response = await client.get(f"/{DOMAIN}/{path}")
        assert response.status == 200, path


async def test_credentials_dialog_placeholders(hass: HomeAssistant) -> None:
    """The credentials dialog shows the redirect URL matching the installation."""
    hass.config.external_url = "https://ha.example.com"
    placeholders = await async_get_description_placeholders(hass)
    assert (
        placeholders["redirect_url"] == "https://ha.example.com/auth/external/callback"
    )

    hass.config.components.add("my")
    placeholders = await async_get_description_placeholders(hass)
    assert placeholders["redirect_url"] == "https://my.home-assistant.io/redirect/oauth"
