"""Base entity for Fuelio Routes."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_FOLDER_ID, DOMAIN, DRIVE_FOLDER_URL_PREFIX
from .coordinator import FuelioRoutesCoordinator


class FuelioRoutesEntity(CoordinatorEntity[FuelioRoutesCoordinator]):
    """Entity describing the route cache of one Google Drive folder."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FuelioRoutesCoordinator, key: str) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Fuelio Routes {entry.title}",
            manufacturer="Fuelio",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=f"{DRIVE_FOLDER_URL_PREFIX}{entry.data[CONF_FOLDER_ID]}",
        )

    @property
    def available(self) -> bool:
        """Stay available while Google Drive is unreachable: the cache still is."""
        return True
