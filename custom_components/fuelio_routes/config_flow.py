"""Config flow for Fuelio Routes: Google sign-in followed by a Drive folder browser."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import re
from typing import Any

from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlowResult
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
import voluptuous as vol

from .api import DriveClient, DriveError, DriveFile
from .const import (
    CONF_FOLDER_ID,
    CONF_FOLDER_NAME,
    DOMAIN,
    DRIVE_ROOT_ID,
    OAUTH2_SCOPES,
)
from .parser import parse_file_name

_LOGGER = logging.getLogger(__name__)

CONF_FOLDER = "folder"
CONF_MANUAL_FOLDER = "manual_folder"
CHOICE_USE_CURRENT = "__use_current__"
CHOICE_UP = "__up__"
ROOT_NAME = "My Drive"

_FOLDER_LINK_RE = re.compile(r"(?:/folders/|[?&]id=)([A-Za-z0-9_-]+)")
_FOLDER_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def extract_folder_id(value: str) -> str | None:
    """Return the Drive folder id from a pasted id or share link."""
    value = value.strip()
    if match := _FOLDER_LINK_RE.search(value):
        return match.group(1)
    if _FOLDER_ID_RE.match(value):
        return value
    return None


class FuelioRoutesFlowHandler(
    config_entry_oauth2_flow.AbstractOAuth2FlowHandler, domain=DOMAIN
):
    """Handle the Google sign-in and the choice of the Drive folder."""

    DOMAIN = DOMAIN
    VERSION = 1

    def __init__(self) -> None:
        """Initialize the flow."""
        super().__init__()
        self._oauth_data: dict[str, Any] = {}
        self._client: DriveClient | None = None
        self._path: list[DriveFile] = [DriveFile(id=DRIVE_ROOT_ID, name=ROOT_NAME)]
        self._folder_names: dict[str, str] = {}

    @property
    def logger(self) -> logging.Logger:
        """Return logger."""
        return _LOGGER

    @property
    def extra_authorize_data(self) -> dict[str, Any]:
        """Request a refresh token along with read-only Drive access."""
        return {
            "scope": " ".join(OAUTH2_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
        }

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Perform reauth upon an API authentication error."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauth dialog."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        return await self.async_step_user()

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Continue to the folder browser, or finish a reauth."""
        if self.source == SOURCE_REAUTH:
            return self.async_update_reload_and_abort(
                self._get_reauth_entry(), data_updates=data
            )
        self._oauth_data = data
        access_token: str = data[CONF_TOKEN][CONF_ACCESS_TOKEN]

        async def async_get_access_token() -> str:
            return access_token

        self._client = DriveClient(
            async_get_clientsession(self.hass), async_get_access_token
        )
        return await self.async_step_folder()

    async def async_step_folder(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Browse Google Drive one level at a time until a folder is chosen."""
        assert self._client is not None
        errors: dict[str, str] = {}
        detail = ""

        if user_input is not None:
            manual = (user_input.get(CONF_MANUAL_FOLDER) or "").strip()
            choice = user_input.get(CONF_FOLDER)
            if manual:
                if (folder_id := extract_folder_id(manual)) is None:
                    errors[CONF_MANUAL_FOLDER] = "invalid_folder"
                else:
                    try:
                        folder = await self._client.get_folder(folder_id)
                    except DriveError as err:
                        errors[CONF_MANUAL_FOLDER] = "invalid_folder"
                        detail = str(err)
                    else:
                        return await self._async_finish(folder)
            elif choice == CHOICE_USE_CURRENT:
                return await self._async_finish(self._path[-1])
            elif choice == CHOICE_UP:
                self._path.pop()
            elif choice:
                self._path.append(
                    DriveFile(id=choice, name=self._folder_names.get(choice, choice))
                )
            else:
                errors["base"] = "no_folder"

        current = self._path[-1]
        folders: list[DriveFile] = []
        route_files = 0
        try:
            folders = await self._client.list_folders(current.id)
            route_files = sum(
                1
                for file in await self._client.list_files(current.id)
                if parse_file_name(file.name)
            )
        except DriveError as err:
            errors.setdefault("base", "cannot_connect")
            detail = detail or str(err)
        self._folder_names = {folder.id: folder.name for folder in folders}

        options = [
            SelectOptionDict(
                value=CHOICE_USE_CURRENT,
                label=f"✔ Use this folder: {current.name} ({route_files} route files)",
            )
        ]
        if len(self._path) > 1:
            options.append(
                SelectOptionDict(value=CHOICE_UP, label="↩ Back to the parent folder")
            )
        options.extend(
            SelectOptionDict(value=folder.id, label=f"\U0001f4c1 {folder.name}")
            for folder in folders
        )

        return self.async_show_form(
            step_id="folder",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_FOLDER): SelectSelector(
                        SelectSelectorConfig(
                            options=options, mode=SelectSelectorMode.LIST
                        )
                    ),
                    vol.Optional(CONF_MANUAL_FOLDER): TextSelector(),
                }
            ),
            errors=errors,
            description_placeholders={
                "path": " / ".join(folder.name for folder in self._path),
                "route_files": str(route_files),
                "detail": detail,
            },
            last_step=False,
        )

    async def _async_finish(self, folder: DriveFile) -> ConfigFlowResult:
        await self.async_set_unique_id(folder.id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=folder.name or ROOT_NAME,
            data={
                **self._oauth_data,
                CONF_FOLDER_ID: folder.id,
                CONF_FOLDER_NAME: folder.name,
            },
        )
