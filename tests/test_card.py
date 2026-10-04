"""Tests for making the card available to dashboards."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import DATA_EXTRA_MODULE_URL
from homeassistant.components.lovelace.const import LOVELACE_DATA
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fuelio_routes.card import CARD_URL
from custom_components.fuelio_routes.const import DOMAIN

from .conftest import FakeDrive, setup_integration

pytestmark = pytest.mark.usefixtures("credentials")


@pytest.fixture(autouse=True)
def cache_in_tmp_path(hass: HomeAssistant, tmp_path: Path) -> None:
    """Keep downloaded files out of the shared test configuration directory."""
    hass.config.config_dir = str(tmp_path)


def _card_resources(hass: HomeAssistant) -> list[dict]:
    return [
        item
        for item in hass.data[LOVELACE_DATA].resources.async_items()
        if item["url"].startswith(CARD_URL)
    ]


async def test_card_is_a_dashboard_resource(
    hass: HomeAssistant, config_entry: MockConfigEntry, drive: FakeDrive
) -> None:
    """The card is loaded by dashboards, not only by the page shell."""
    await setup_integration(hass, config_entry)

    resources = _card_resources(hass)
    assert len(resources) == 1
    assert resources[0]["type"] == "module"
    assert resources[0]["url"].startswith(f"{CARD_URL}?v=")
    assert not hass.data[DATA_EXTRA_MODULE_URL]


async def test_existing_resource_is_updated_and_duplicates_removed(
    hass: HomeAssistant, config_entry: MockConfigEntry, drive: FakeDrive
) -> None:
    """An outdated or duplicated resource for the card is cleaned up."""
    assert await async_setup_component(hass, "lovelace", {})
    resources = hass.data[LOVELACE_DATA].resources
    await resources.async_create_item({"res_type": "js", "url": f"{CARD_URL}?v=old"})
    await resources.async_create_item(
        {"res_type": "module", "url": f"{CARD_URL}?v=older"}
    )
    await resources.async_create_item(
        {"res_type": "module", "url": "/local/other-card.js"}
    )

    await setup_integration(hass, config_entry)

    ours = _card_resources(hass)
    assert len(ours) == 1
    assert ours[0]["type"] == "module"
    assert "v=old" not in ours[0]["url"]
    assert len(resources.async_items()) == 2


async def test_current_resource_is_left_alone(
    hass: HomeAssistant, config_entry: MockConfigEntry, drive: FakeDrive
) -> None:
    """Restarting does not rewrite a resource that is already current."""
    await setup_integration(hass, config_entry)
    before = _card_resources(hass)
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _card_resources(hass) == before


async def test_yaml_resources_fall_back_to_the_page(
    hass: HomeAssistant, config_entry: MockConfigEntry, drive: FakeDrive
) -> None:
    """When resources are managed in YAML the card is added to the page instead."""
    assert await async_setup_component(
        hass, "lovelace", {"lovelace": {"resource_mode": "yaml"}}
    )
    await setup_integration(hass, config_entry)

    assert any(
        url.startswith(f"{CARD_URL}?v=") for url in hass.data[DATA_EXTRA_MODULE_URL]
    )


async def test_resource_removed_with_the_last_entry(
    hass: HomeAssistant, config_entry: MockConfigEntry, drive: FakeDrive
) -> None:
    """The resource stays while another folder is configured and goes with the last one."""
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Second",
        unique_id="folder456",
        data={**config_entry.data, "folder_id": "folder456"},
    )
    second.add_to_hass(hass)
    # Setting up the integration loads both entries.
    await setup_integration(hass, config_entry)
    assert second.runtime_data is not None

    assert await hass.config_entries.async_remove(second.entry_id)
    await hass.async_block_till_done()
    assert len(_card_resources(hass)) == 1

    assert await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert _card_resources(hass) == []


async def test_removal_with_yaml_resources_leaves_them_alone(
    hass: HomeAssistant, config_entry: MockConfigEntry, drive: FakeDrive
) -> None:
    """YAML-managed resources are never edited."""
    assert await async_setup_component(
        hass, "lovelace", {"lovelace": {"resource_mode": "yaml"}}
    )
    await setup_integration(hass, config_entry)
    assert await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
