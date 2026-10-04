"""Button to synchronize the routes on demand."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import FuelioRoutesConfigEntry
from .entity import FuelioRoutesEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FuelioRoutesConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the buttons."""
    async_add_entities(
        [
            FuelioRoutesRefreshButton(entry.runtime_data, "refresh"),
            FuelioRoutesFullRescanButton(entry.runtime_data, "full_rescan"),
        ]
    )


class FuelioRoutesRefreshButton(FuelioRoutesEntity, ButtonEntity):
    """Fetch new routes from Google Drive now."""

    _attr_icon = "mdi:cloud-refresh-variant"

    async def async_press(self) -> None:
        """Synchronize with Google Drive."""
        await self.coordinator.async_request_refresh()


class FuelioRoutesFullRescanButton(FuelioRoutesEntity, ButtonEntity):
    """Check every file in the Drive folder and download whatever is missing."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:cloud-search-outline"

    async def async_press(self) -> None:
        """Start a full rescan in the background; it can take minutes."""
        self.coordinator.config_entry.async_create_background_task(
            self.hass, self.coordinator.async_full_rescan(), "fuelio_routes full rescan"
        )
