"""Make the bundled Lovelace card available to the frontend."""

from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import LOVELACE_DATA, MODE_STORAGE
from homeassistant.core import HomeAssistant
from homeassistant.loader import async_get_integration

from .const import CARD_FILENAME, DOMAIN, FRONTEND_URL_BASE

_LOGGER = logging.getLogger(__name__)

CARD_URL = f"{FRONTEND_URL_BASE}/{CARD_FILENAME}"


async def async_register_card(hass: HomeAssistant) -> None:
    """Serve the card and make every dashboard load it.

    The card is registered as a dashboard resource: the frontend fetches the
    resource list whenever a dashboard opens, whereas a module added to the page
    itself is missed while the companion app shows a page cached before the
    integration was installed. With resources managed in YAML that is not
    possible, so the card is added to the page instead.
    """
    integration = await async_get_integration(hass, DOMAIN)
    frontend_dir = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(FRONTEND_URL_BASE, str(frontend_dir), cache_headers=False)]
    )
    # The file's modification time is part of the URL so that browsers never keep
    # running a cached copy of the card after it has changed.
    modified = await hass.async_add_executor_job(
        lambda: int((frontend_dir / CARD_FILENAME).stat().st_mtime)
    )
    url = f"{CARD_URL}?v={integration.version}-{modified}"
    if not await _async_set_resource(hass, url):
        _LOGGER.info(
            "Dashboard resources are managed in YAML; add %s as a module resource"
            " for the card to work in the companion app",
            CARD_URL,
        )
        add_extra_js_url(hass, url)


async def _async_set_resource(hass: HomeAssistant, url: str) -> bool:
    """Point the card's dashboard resource at ``url``; False if not possible."""
    lovelace = hass.data.get(LOVELACE_DATA)
    if lovelace is None or lovelace.resource_mode != MODE_STORAGE:
        return False
    resources = lovelace.resources
    await resources.async_get_info()
    ours = [
        item
        for item in resources.async_items()
        if item["url"].split("?")[0] == CARD_URL
    ]
    if not ours:
        await resources.async_create_item({"res_type": "module", "url": url})
        return True
    if ours[0]["url"] != url or ours[0]["type"] != "module":
        await resources.async_update_item(
            ours[0]["id"], {"res_type": "module", "url": url}
        )
    for duplicate in ours[1:]:
        await resources.async_delete_item(duplicate["id"])
    return True


async def async_unregister_card(hass: HomeAssistant) -> None:
    """Remove the card's dashboard resource."""
    lovelace = hass.data.get(LOVELACE_DATA)
    if lovelace is None or lovelace.resource_mode != MODE_STORAGE:
        return
    await lovelace.resources.async_get_info()
    for item in list(lovelace.resources.async_items()):
        if item["url"].split("?")[0] == CARD_URL:
            await lovelace.resources.async_delete_item(item["id"])
