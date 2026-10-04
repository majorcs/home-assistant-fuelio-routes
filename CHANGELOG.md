# Changelog

## 2026.10.4.3

### Fixed
- The card showed "Configuration error" in the companion app: the app can keep showing a page cached before the integration was installed, which never loads the card. The card is now registered as a dashboard resource, which dashboards load every time.

### Changed
- The integration adds one entry for the card under Settings → Dashboards → Resources, keeps it up to date and removes it when the integration is removed. With resources managed in YAML the card is loaded as before; add `/fuelio_routes/fuelio-routes-card.js` as a module resource there.

## 2026.10.4.2

### Fixed
- Signing in through the My Home Assistant redirect could end with "Invalid flow specified": the sign-in step ran twice at once and the second, rejected use of Google's one-time code aborted the setup.

## 2026.10.4.1

### Added
- Google Drive sign-in with a folder browser.
- Daily and on-demand synchronization of Fuelio route files into a local cache.
- Parsers for Fuelio's `.data`, `.gpx` and `.route` trip files.
- `fuelio-routes-card` with day and trip selection on a map.
- Refresh button and status sensors.
- Full rescan button.
- Card option to hide trips shorter than a minimum distance (default 100 m).
- Calendar in the card that highlights the days with trips.
- Plain `route-*.csv` trip files; newest copy of duplicate uploads.
- The card's trip list scrolls on its own (`list_height`), keeping the map in view.
- Incremental synchronization: after the first import only files changed since the last sync are listed.
- Routes are cached outside Home Assistant backups and re-downloaded when the cache is lost.
- Background import of long histories: newest trips first, resumable, with retries and visible progress.
