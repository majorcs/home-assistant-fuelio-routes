"""Tests for the Google sign-in and folder browser."""

from __future__ import annotations

from pathlib import Path

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from custom_components.fuelio_routes.api import DriveError, DriveFile
from custom_components.fuelio_routes.config_flow import (
    CHOICE_UP,
    CHOICE_USE_CURRENT,
    extract_folder_id,
)
from custom_components.fuelio_routes.const import (
    CONF_FOLDER_ID,
    CONF_FOLDER_NAME,
    DOMAIN,
    OAUTH2_SCOPES,
)

from .conftest import CLIENT_ID, FOLDER_ID, TRIP_A, FakeDrive, make_data

TOKEN_URL = "https://oauth2.googleapis.com/token"
REDIRECT_URI = "https://example.com/auth/external/callback"

pytestmark = pytest.mark.usefixtures("credentials", "current_request_with_host")


@pytest.fixture(autouse=True)
async def prepare(hass: HomeAssistant, tmp_path: Path) -> None:
    """Keep downloads in a temporary directory and register the static path early.

    Unlike a running Home Assistant, the test HTTP client freezes the router
    as soon as it is created, so the integration has to be set up before that.
    """
    hass.config.config_dir = str(tmp_path)
    assert await async_setup_component(hass, DOMAIN, {})


async def _sign_in(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    result: dict,
) -> dict:
    """Complete the external Google step of a flow."""
    state = config_entry_oauth2_flow._encode_jwt(
        hass, {"flow_id": result["flow_id"], "redirect_uri": REDIRECT_URI}
    )
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    assert result["url"].startswith("https://accounts.google.com/o/oauth2/v2/auth")
    assert f"client_id={CLIENT_ID}" in result["url"]
    assert f"scope={OAUTH2_SCOPES[0]}" in result["url"]
    assert "access_type=offline" in result["url"]
    assert "prompt=consent" in result["url"]

    client = await hass_client_no_auth()
    response = await client.get(f"/auth/external/callback?code=abcd&state={state}")
    assert response.status == 200
    aioclient_mock.post(
        TOKEN_URL,
        json={
            "refresh_token": "new-refresh",
            "access_token": "new-access",
            "token_type": "Bearer",
            "expires_in": 3600,
        },
    )
    return await hass.config_entries.flow.async_configure(result["flow_id"])


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1AbC_d-9", "1AbC_d-9"),
        ("  1AbC_d-9  ", "1AbC_d-9"),
        ("https://drive.google.com/drive/folders/1AbC_d-9?usp=sharing", "1AbC_d-9"),
        ("https://drive.google.com/drive/u/0/folders/1AbC_d-9", "1AbC_d-9"),
        ("https://drive.google.com/open?id=1AbC_d-9", "1AbC_d-9"),
        ("my folder", None),
        ("", None),
    ],
)
def test_extract_folder_id(value: str, expected: str | None) -> None:
    """Folder ids are accepted bare or inside a Drive link."""
    assert extract_folder_id(value) == expected


async def test_browse_to_folder(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    drive: FakeDrive,
) -> None:
    """Walk down, back up and down again before choosing a folder."""
    drive.folders = {
        "root": [
            DriveFile(id="android", name="Android"),
            DriveFile(id="docs", name="Docs"),
        ],
        "android": [DriveFile(id="fuelio", name="Fuelio")],
    }
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await _sign_in(hass, hass_client_no_auth, aioclient_mock, result)

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "folder"
    assert result["description_placeholders"]["path"] == "My Drive"
    assert result["description_placeholders"]["route_files"] == "0"
    options = result["data_schema"].schema["folder"].config["options"]
    assert [option["value"] for option in options] == [
        CHOICE_USE_CURRENT,
        "android",
        "docs",
    ]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"folder": "docs"}
    )
    assert result["description_placeholders"]["path"] == "My Drive / Docs"
    options = result["data_schema"].schema["folder"].config["options"]
    assert [option["value"] for option in options] == [CHOICE_USE_CURRENT, CHOICE_UP]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"folder": CHOICE_UP}
    )
    assert result["description_placeholders"]["path"] == "My Drive"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"folder": "android"}
    )
    drive.add(f"route-{TRIP_A}.data", make_data(TRIP_A))
    drive.add("notes.txt", b"")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"folder": "fuelio"}
    )
    assert result["description_placeholders"]["path"] == "My Drive / Android / Fuelio"
    assert result["description_placeholders"]["route_files"] == "1"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"folder": CHOICE_USE_CURRENT}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Fuelio"
    assert result["data"][CONF_FOLDER_ID] == "fuelio"
    assert result["data"][CONF_FOLDER_NAME] == "Fuelio"
    assert result["data"]["token"]["refresh_token"] == "new-refresh"
    assert result["result"].unique_id == "fuelio"
    await hass.async_block_till_done(wait_background_tasks=True)
    assert len(result["result"].runtime_data.trips) == 1


async def test_paste_folder(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    drive: FakeDrive,
) -> None:
    """A pasted link wins over the selection and bad input is explained."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await _sign_in(hass, hass_client_no_auth, aioclient_mock, result)

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["errors"] == {"base": "no_folder"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"manual_folder": "not a folder!"}
    )
    assert result["errors"] == {"manual_folder": "invalid_folder"}

    drive.error = DriveError("File not found")
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"manual_folder": "missing"}
    )
    assert result["errors"] == {
        "manual_folder": "invalid_folder",
        "base": "cannot_connect",
    }
    assert result["description_placeholders"]["detail"] == "File not found"

    drive.error = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "folder": CHOICE_USE_CURRENT,
            "manual_folder": "https://drive.google.com/drive/folders/shared42",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_FOLDER_ID] == "shared42"
    assert result["title"] == "Folder shared42"
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_drive_unreachable_while_browsing(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    drive: FakeDrive,
) -> None:
    """A Drive failure is shown with Google's message and can be retried."""
    drive.error = DriveError("Google Drive API has not been used in project 1")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await _sign_in(hass, hass_client_no_auth, aioclient_mock, result)

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert "has not been used" in result["description_placeholders"]["detail"]

    drive.error = None
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"folder": CHOICE_USE_CURRENT}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "My Drive"
    assert result["data"][CONF_FOLDER_ID] == "root"
    await hass.async_block_till_done(wait_background_tasks=True)


async def test_folder_already_configured(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    drive: FakeDrive,
    config_entry: MockConfigEntry,
) -> None:
    """The same folder cannot be added twice."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await _sign_in(hass, hass_client_no_auth, aioclient_mock, result)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"manual_folder": FOLDER_ID}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth(
    hass: HomeAssistant,
    hass_client_no_auth: ClientSessionGenerator,
    aioclient_mock: AiohttpClientMocker,
    drive: FakeDrive,
    config_entry: MockConfigEntry,
) -> None:
    """Signing in again replaces the token and keeps the folder."""
    result = await config_entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _sign_in(hass, hass_client_no_auth, aioclient_mock, result)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert config_entry.data["token"]["refresh_token"] == "new-refresh"
    assert config_entry.data[CONF_FOLDER_ID] == FOLDER_ID
    await hass.async_block_till_done(wait_background_tasks=True)
