"""Tests for the websocket commands used by the card."""

from __future__ import annotations

import asyncio
from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.typing import WebSocketGenerator

from custom_components.fuelio_routes.api import DriveError
from custom_components.fuelio_routes.coordinator import cache_dir

from .conftest import (
    FOLDER_ID,
    TRIP_A,
    TRIP_B,
    TRIP_C,
    FakeDrive,
    make_data,
    setup_integration,
)

pytestmark = pytest.mark.usefixtures("credentials")


@pytest.fixture(autouse=True)
def cache_in_tmp_path(hass: HomeAssistant, tmp_path: Path) -> None:
    """Keep downloaded files out of the shared test configuration directory."""
    hass.config.config_dir = str(tmp_path)


async def test_days(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    config_entry: MockConfigEntry,
    populated_drive: FakeDrive,
) -> None:
    """Days are listed in order with their trip counts."""
    await setup_integration(hass, config_entry)
    client = await hass_ws_client(hass)

    await client.send_json_auto_id({"type": "fuelio_routes/days"})
    response = await client.receive_json()
    assert response["success"]
    assert response["result"] == {
        "days": [
            {"date": "2026-10-01", "trips": 2},
            {"date": "2026-10-02", "trips": 1},
        ],
        "sync": {"running": False, "done": 0, "total": 0},
    }

    await client.send_json_auto_id({"type": "fuelio_routes/days", "entry_id": "other"})
    assert (await client.receive_json())["result"]["days"] == []

    await client.send_json_auto_id(
        {"type": "fuelio_routes/days", "entry_id": config_entry.entry_id}
    )
    assert len((await client.receive_json())["result"]["days"]) == 2


async def test_day(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    config_entry: MockConfigEntry,
    populated_drive: FakeDrive,
) -> None:
    """A day returns its trips, oldest first, with their points."""
    await setup_integration(hass, config_entry)
    client = await hass_ws_client(hass)

    await client.send_json_auto_id({"type": "fuelio_routes/day", "date": "2026-10-01"})
    result = (await client.receive_json())["result"]
    assert result["date"] == "2026-10-01"
    assert [trip["id"] for trip in result["trips"]] == [str(TRIP_A), str(TRIP_B)]
    first, second = result["trips"]
    assert first["entry_id"] == config_entry.entry_id
    assert first["name"] == "Evening drive"
    assert first["points"][0] == [47.0, 19.0, TRIP_A + 5000, 5.0]
    assert len(first["points"]) == first["point_count"] == 4
    assert second["points"][0] == [47.0, 19.0, TRIP_B + 9000, None]

    await client.send_json_auto_id({"type": "fuelio_routes/day", "date": "2026-10-02"})
    trips = (await client.receive_json())["result"]["trips"]
    assert [trip["id"] for trip in trips] == [str(TRIP_C)]
    assert trips[0]["has_timestamps"] is False
    assert trips[0]["points"][0] == [47.0, 19.0, None, None]

    await client.send_json_auto_id({"type": "fuelio_routes/day", "date": "2020-01-01"})
    assert (await client.receive_json())["result"] == {
        "date": "2020-01-01",
        "trips": [],
        "hidden": 0,
    }

    await client.send_json_auto_id({"type": "fuelio_routes/day", "date": "yesterday"})
    assert not (await client.receive_json())["success"]


async def test_short_trips_can_be_hidden(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    config_entry: MockConfigEntry,
    drive: FakeDrive,
) -> None:
    """Trips below the minimum distance are not counted or returned."""
    drive.add(f"route-{TRIP_A}.data", make_data(TRIP_A))  # about 558 m
    short = [(47.0, 19.0), (47.0001, 19.0001)]  # about 13 m
    drive.add(f"route-{TRIP_B}.data", make_data(TRIP_B, short))
    drive.add(f"route-{TRIP_C}.data", make_data(TRIP_C, short))
    await setup_integration(hass, config_entry)
    client = await hass_ws_client(hass)

    await client.send_json_auto_id({"type": "fuelio_routes/days", "min_distance": 100})
    assert (await client.receive_json())["result"]["days"] == [
        {"date": "2026-10-01", "trips": 1}
    ]

    await client.send_json_auto_id(
        {"type": "fuelio_routes/day", "date": "2026-10-01", "min_distance": 100}
    )
    result = (await client.receive_json())["result"]
    assert [trip["id"] for trip in result["trips"]] == [str(TRIP_A)]
    assert result["hidden"] == 1

    await client.send_json_auto_id(
        {"type": "fuelio_routes/day", "date": "2026-10-02", "min_distance": 100}
    )
    result = (await client.receive_json())["result"]
    assert result["trips"] == []
    assert result["hidden"] == 1

    await client.send_json_auto_id({"type": "fuelio_routes/days", "min_distance": -1})
    assert not (await client.receive_json())["success"]


async def test_day_skips_trip_whose_files_vanished(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    config_entry: MockConfigEntry,
    populated_drive: FakeDrive,
) -> None:
    """A trip that can no longer be read is left out instead of failing the day."""
    await setup_integration(hass, config_entry)
    (cache_dir(hass, config_entry.entry_id) / f"route-{TRIP_B}.gpx").unlink()
    client = await hass_ws_client(hass)

    await client.send_json_auto_id({"type": "fuelio_routes/day", "date": "2026-10-01"})
    trips = (await client.receive_json())["result"]["trips"]
    assert [trip["id"] for trip in trips] == [str(TRIP_A)]


async def test_refresh(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    config_entry: MockConfigEntry,
    populated_drive: FakeDrive,
) -> None:
    """Refreshing reports new trips and failures."""
    await setup_integration(hass, config_entry)
    client = await hass_ws_client(hass)
    new_trip = TRIP_C + 3600000
    populated_drive.add(f"route-{new_trip}.data", make_data(new_trip))

    await client.send_json_auto_id({"type": "fuelio_routes/refresh"})
    assert (await client.receive_json())["result"] == {
        "running": False,
        "success": True,
        "new_trips": 1,
    }

    populated_drive.error = DriveError("offline")
    await client.send_json_auto_id({"type": "fuelio_routes/refresh"})
    assert (await client.receive_json())["result"] == {
        "running": False,
        "success": False,
        "new_trips": 0,
    }


async def test_progress_of_running_import(
    hass: HomeAssistant,
    hass_ws_client: WebSocketGenerator,
    config_entry: MockConfigEntry,
    drive: FakeDrive,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A long import is visible while it runs and does not block anything."""
    monkeypatch.setattr(
        "custom_components.fuelio_routes.coordinator.SAVE_EVERY_N_DOWNLOADS", 2
    )
    trip_ids = [TRIP_A + index * 600000 for index in range(5)]
    for trip_id in trip_ids:
        drive.add(f"route-{trip_id}.data", make_data(trip_id))
    drive.gate = asyncio.Event()

    # Setup returns while the import is still waiting for its first download.
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    coordinator = config_entry.runtime_data
    assert coordinator.syncing
    assert coordinator.pending_files == 5

    registry = er.async_get(hass)
    pending_id = registry.async_get_entity_id(
        "sensor", "fuelio_routes", f"{config_entry.entry_id}_pending_files"
    )
    trips_id = registry.async_get_entity_id(
        "sensor", "fuelio_routes", f"{config_entry.entry_id}_trips"
    )
    assert hass.states.get(pending_id).state == "5"
    assert hass.states.get(trips_id).state == "0"

    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "fuelio_routes/days"})
    assert (await client.receive_json())["result"] == {
        "days": [],
        "sync": {"running": True, "done": 0, "total": 5},
    }

    # Asking for a refresh now does not queue behind the import.
    await client.send_json_auto_id({"type": "fuelio_routes/refresh"})
    assert (await client.receive_json())["result"] == {
        "running": True,
        "success": True,
        "new_trips": 0,
    }

    # Let the first batch through and hold the second one.
    first_batch_done = asyncio.Event()

    def hold_second_batch() -> None:
        drive.gate.clear()
        first_batch_done.set()

    unsubscribe = coordinator.async_add_listener(hold_second_batch)
    drive.gate.set()
    await first_batch_done.wait()
    unsubscribe()

    # The newest trips arrive first and are already usable.
    assert sorted(coordinator.trips) == [str(trip_id) for trip_id in trip_ids[-2:]]
    assert hass.states.get(pending_id).state == "3"
    assert hass.states.get(trips_id).state == "2"
    await client.send_json_auto_id({"type": "fuelio_routes/days"})
    result = (await client.receive_json())["result"]
    assert result["days"] == [{"date": "2026-10-01", "trips": 2}]
    assert result["sync"] == {"running": True, "done": 2, "total": 5}

    drive.gate.set()
    await hass.async_block_till_done(wait_background_tasks=True)

    assert not coordinator.syncing
    assert len(coordinator.trips) == 5
    assert hass.states.get(pending_id).state == "0"
    assert hass.states.get(trips_id).state == "5"
    assert config_entry.unique_id == FOLDER_ID
