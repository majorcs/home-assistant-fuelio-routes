"""Constants for the Fuelio Routes integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "fuelio_routes"

CONF_FOLDER_ID = "folder_id"
CONF_FOLDER_NAME = "folder_name"

OAUTH2_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]

SCAN_INTERVAL = timedelta(days=1)
RETRY_INTERVAL = timedelta(minutes=15)
# How far an incremental listing reaches back before the previous sync started.
LISTING_OVERLAP = timedelta(days=1)
DOWNLOAD_CONCURRENCY = 4
SAVE_EVERY_N_DOWNLOADS = 50

DRIVE_ROOT_ID = "root"
DRIVE_FOLDER_URL_PREFIX = "https://drive.google.com/drive/folders/"

FRONTEND_URL_BASE = f"/{DOMAIN}"
CARD_FILENAME = "fuelio-routes-card.js"
