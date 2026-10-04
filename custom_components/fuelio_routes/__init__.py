"""Fuelio Routes: show the trips Fuelio uploads to Google Drive on a map."""

from __future__ import annotations

from aiohttp import ClientError, ClientResponseError
from homeassistant.const import CONF_ACCESS_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import (
    OAuth2Session,
    async_get_config_entry_implementation,
)
from homeassistant.helpers.typing import ConfigType

from .api import DriveAuthError, DriveClient, DriveError
from .card import async_register_card, async_unregister_card
from .const import DOMAIN
from .coordinator import (
    FuelioRoutesConfigEntry,
    FuelioRoutesCoordinator,
    async_remove_cache,
)
from .websocket_api import async_register_commands

PLATFORMS = [Platform.BUTTON, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Serve the bundled Lovelace card and register the websocket commands."""
    await async_register_card(hass)
    async_register_commands(hass)
    return True


async def async_setup_entry(
    hass: HomeAssistant, entry: FuelioRoutesConfigEntry
) -> bool:
    """Set up a Google Drive folder with Fuelio routes."""
    try:
        implementation = await async_get_config_entry_implementation(hass, entry)
    except (ValueError, HomeAssistantError) as err:
        raise ConfigEntryNotReady(
            "The Google application credentials are not available"
        ) from err
    session = OAuth2Session(hass, entry, implementation)

    async def async_get_access_token() -> str:
        try:
            await session.async_ensure_token_valid()
        except ClientResponseError as err:
            if 400 <= err.status < 500:
                raise DriveAuthError(
                    f"Google refused to refresh the token: {err}"
                ) from err
            raise DriveError(f"Cannot refresh the Google token: {err}") from err
        except ClientError as err:
            raise DriveError(f"Cannot refresh the Google token: {err}") from err
        return session.token[CONF_ACCESS_TOKEN]

    coordinator = FuelioRoutesCoordinator(
        hass, entry, DriveClient(async_get_clientsession(hass), async_get_access_token)
    )
    await coordinator.async_load()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # The first sync may download years of trips; never make startup wait for it.
    entry.async_create_background_task(
        hass, coordinator.async_refresh(), f"{DOMAIN} initial sync"
    )
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: FuelioRoutesConfigEntry
) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(
    hass: HomeAssistant, entry: FuelioRoutesConfigEntry
) -> None:
    """Delete the cached routes; drop the card's resource with the last entry."""
    await async_remove_cache(hass, entry.entry_id)
    others = [
        other
        for other in hass.config_entries.async_entries(DOMAIN)
        if other.entry_id != entry.entry_id
    ]
    if not others:
        await async_unregister_card(hass)
