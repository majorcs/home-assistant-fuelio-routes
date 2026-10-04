"""Status sensors of the route cache."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .coordinator import FuelioRoutesConfigEntry
from .entity import FuelioRoutesEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FuelioRoutesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the status sensors."""
    coordinator = entry.runtime_data
    async_add_entities(
        [
            FuelioRoutesTripsSensor(coordinator, "trips"),
            FuelioRoutesLastSyncSensor(coordinator, "last_sync"),
            FuelioRoutesLastTripSensor(coordinator, "last_trip"),
            FuelioRoutesPendingFilesSensor(coordinator, "pending_files"),
        ]
    )


class FuelioRoutesTripsSensor(FuelioRoutesEntity, SensorEntity):
    """Number of trips in the cache."""

    _attr_icon = "mdi:map-marker-path"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int:
        """Return the number of cached trips."""
        return len(self.coordinator.trips)


class FuelioRoutesLastSyncSensor(FuelioRoutesEntity, SensorEntity):
    """Time of the last successful synchronization with Google Drive."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self) -> datetime | None:
        """Return the time of the last successful synchronization."""
        return self.coordinator.last_sync


class FuelioRoutesLastTripSensor(FuelioRoutesEntity, SensorEntity):
    """Start time of the most recent trip."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:car-clock"

    @property
    def native_value(self) -> datetime | None:
        """Return the start of the most recent trip."""
        if (trip := self.coordinator.latest_trip) is None:
            return None
        return dt_util.utc_from_timestamp(trip["start"] / 1000)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Describe the most recent trip."""
        if (trip := self.coordinator.latest_trip) is None:
            return None
        return {
            "trip_name": trip["name"],
            "start_name": trip["start_name"],
            "end_name": trip["end_name"],
            "distance_km": round(trip["distance"] / 1000, 2),
        }


class FuelioRoutesPendingFilesSensor(FuelioRoutesEntity, SensorEntity):
    """Files the running synchronization still has to download; 0 when idle."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:cloud-download-outline"
    _attr_state_class = SensorStateClass.MEASUREMENT

    @property
    def native_value(self) -> int:
        """Return the number of files left to download."""
        return self.coordinator.pending_files
