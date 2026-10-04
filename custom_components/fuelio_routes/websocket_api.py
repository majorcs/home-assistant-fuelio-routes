"""Websocket commands used by the Fuelio Routes card."""

from __future__ import annotations

from datetime import date
from typing import Any

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
import voluptuous as vol

from .const import DOMAIN
from .coordinator import FuelioRoutesCoordinator


@callback
def async_register_commands(hass: HomeAssistant) -> None:
    """Register the websocket commands."""
    websocket_api.async_register_command(hass, ws_days)
    websocket_api.async_register_command(hass, ws_day)
    websocket_api.async_register_command(hass, ws_refresh)


def _coordinators(
    hass: HomeAssistant, entry_id: str | None
) -> list[FuelioRoutesCoordinator]:
    return [
        entry.runtime_data
        for entry in hass.config_entries.async_loaded_entries(DOMAIN)
        if entry_id is None or entry.entry_id == entry_id
    ]


@websocket_api.websocket_command(
    {
        vol.Required("type"): "fuelio_routes/days",
        vol.Optional("entry_id"): str,
        vol.Optional("min_distance", default=0): vol.All(
            vol.Coerce(float), vol.Range(min=0)
        ),
    }
)
@callback
def ws_days(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return every day that has trips, with the number of trips."""
    coordinators = _coordinators(hass, msg.get("entry_id"))
    counts: dict[str, int] = {}
    for coordinator in coordinators:
        for day, count in coordinator.days(msg["min_distance"]).items():
            counts[day] = counts.get(day, 0) + count
    connection.send_result(
        msg["id"],
        {
            "days": [{"date": day, "trips": counts[day]} for day in sorted(counts)],
            "sync": {
                "running": any(c.syncing for c in coordinators),
                "done": sum(c.sync_done for c in coordinators),
                "total": sum(c.sync_total for c in coordinators),
            },
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "fuelio_routes/day",
        vol.Required("date"): cv.date,
        vol.Optional("entry_id"): str,
        vol.Optional("min_distance", default=0): vol.All(
            vol.Coerce(float), vol.Range(min=0)
        ),
    }
)
@websocket_api.async_response
async def ws_day(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Return the trips of one local calendar day including their points.

    Trips shorter than ``min_distance`` meters are left out and only counted.
    """
    day: date = msg["date"]
    trips: list[dict[str, Any]] = []
    hidden = 0
    for coordinator in _coordinators(hass, msg.get("entry_id")):
        trip_ids = []
        for trip_id in coordinator.trip_ids_on(day):
            if coordinator.trips[trip_id]["distance"] < msg["min_distance"]:
                hidden += 1
            else:
                trip_ids.append(trip_id)
        loaded = await coordinator.async_get_trips(trip_ids)
        for trip_id in trip_ids:
            if (trip := loaded.get(trip_id)) is None:
                continue
            trips.append(
                {
                    **trip.summary(),
                    "entry_id": coordinator.config_entry.entry_id,
                    "points": [
                        [round(lat, 6), round(lon, 6), timestamp, speed]
                        for lat, lon, timestamp, speed in trip.points
                    ],
                }
            )
    trips.sort(key=lambda trip: trip["start"])
    connection.send_result(
        msg["id"], {"date": day.isoformat(), "trips": trips, "hidden": hidden}
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): "fuelio_routes/refresh",
        vol.Optional("entry_id"): str,
    }
)
@websocket_api.async_response
async def ws_refresh(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Synchronize with Google Drive now and report whether it worked.

    A synchronization that is already running is left alone and reported as such,
    so the caller does not wait for a long import to finish.
    """
    coordinators = _coordinators(hass, msg.get("entry_id"))
    idle = [c for c in coordinators if not c.syncing]
    for coordinator in idle:
        await coordinator.async_refresh()
    connection.send_result(
        msg["id"],
        {
            "running": len(idle) < len(coordinators),
            "success": all(c.last_update_success for c in idle),
            "new_trips": sum(
                (c.data or {}).get("new_trips", 0)
                for c in idle
                if c.last_update_success
            ),
        },
    )
