# Changelog

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
