# Fuelio Routes for Home Assistant

Shows the trips recorded by [Fuelio](https://www.fuel.io/)'s Trip Log on a map
in Home Assistant. Fuelio uploads every trip to your Google Drive; this
integration mirrors that folder into Home Assistant and ships a dashboard card
with a day selector and a trip selector.

- Reads the `route-*.data`, `route-*.csv`, `route-*.gpx` and `route-*.route`
  files Fuelio writes, in that order of preference, so every trip is shown no
  matter which of them Fuelio uploaded for it. When Drive holds the same file
  name several times, the most recently uploaded copy is used.
- Downloads only new or changed files: once a day, after a restart, and on
  demand with the refresh button. After the first complete import it only
  asks Google Drive for files uploaded or changed since the previous
  synchronization, so these checks take a second even for a folder with
  years of trips.
- Keeps the routes in its own cache (`<config>/.cache/fuelio_routes/`).
  Nothing is written to the recorder database, and the cache is not part of
  Home Assistant backups: after a restore the routes are downloaded again.
- Read-only access to Google Drive (`drive.readonly`).

## Installation

1. In HACS add `https://github.com/majorcs/home-assistant-fuelio-routes` as a
   custom repository of type *Integration* and install **Fuelio Routes**.
2. Restart Home Assistant.

## Google setup

The integration signs in with your own Google Cloud OAuth client.

1. In the [Google Cloud console](https://console.cloud.google.com/) create a
   project and enable the **Google Drive API** for it.
2. Configure the **OAuth consent screen** (user type *External*). Then either
   publish the app (*In production*) or add your Google account as a test
   user. Prefer publishing: in *Testing* mode Google expires the sign-in
   after 7 days and Home Assistant will ask you to re-authenticate every week.
   An unverified published app only shows a warning screen during sign-in.
3. Under **Credentials** create an **OAuth client ID** of type *Web
   application* and add `https://my.home-assistant.io/redirect/oauth` to the
   *Authorized redirect URIs*.
4. In Home Assistant go to **Settings → Devices & services → Add integration →
   Fuelio Routes**, enter the client ID and secret when asked, and sign in.
5. Pick the folder holding the route files. The folder browser shows how many
   route files each folder contains. Folders outside *My Drive* (for example
   shared ones) can be selected by pasting their ID or link.

Add the integration once more to follow a second folder.

### First synchronization

Importing a history of several years means downloading thousands of small
files. This runs entirely in the background: Home Assistant starts and stays
responsive, and nothing waits for it.

- The newest trips are fetched first, so recent days are on the map within
  seconds while older ones keep arriving.
- Progress is shown in the card and by `sensor.…_files_to_download`, which
  counts down to 0.
- Progress is saved continuously. After a restart the import continues where
  it stopped.
- The whole folder is listed only for this first import (about a minute for
  a folder of 38,000 files, before the downloads start), again if the local
  cache is lost, and when you press **Full rescan**.
- Throttling and network errors are retried. Files that still fail are
  skipped and fetched again 15 minutes later.

## Card

The card is registered automatically as a dashboard resource (visible under
**Settings → Dashboards → Resources**; it is removed again with the
integration). If your resources are managed in YAML, add
`/fuelio_routes/fuelio-routes-card.js` as a `module` resource yourself for the
card to work in the companion app. Add the card from the card picker
("Fuelio Routes") or in YAML:

```yaml
type: custom:fuelio-routes-card
title: Trips            # optional
map_height: 400         # optional, pixels
list_height: 260        # optional, pixels; the trip list scrolls beyond this
min_distance: 100       # optional, meters; shorter trips are hidden, 0 shows all
entry_id: 01J...        # optional, limit to one folder (default: all)
tile_url: https://tile.openstreetmap.org/{z}/{x}/{y}.png   # optional
tile_attribution: "&copy; OpenStreetMap contributors"      # optional
```

- The arrows jump to the previous / next day that has trips. Clicking the
  date opens a calendar in which the days that have trips are highlighted
  (hover a day for the number of trips).
- Click a trip in the list or on the map to highlight it; click it again or
  *All trips* to show the whole day.
- Hovering a route shows the time and speed at that point.
- The refresh icon fetches new routes from Google Drive right away.

Map tiles come from OpenStreetMap by default and are loaded by the browser.

## Entities

| Entity | Description |
|---|---|
| `button.…_refresh` | Synchronize with Google Drive now |
| `button.…_full_rescan` | Compare the whole Drive folder with the cache and fetch whatever is missing (diagnostic) |
| `sensor.…_trips` | Number of cached trips |
| `sensor.…_last_trip` | Start of the most recent trip, with name and distance |
| `sensor.…_last_synchronization` | Last successful synchronization (diagnostic) |
| `sensor.…_files_to_download` | Files the running synchronization still has to fetch, 0 when idle (diagnostic) |

## Notes

- Days follow the Home Assistant time zone.
- Fuelio writes local time into its GPX files but labels it UTC. The
  integration corrects this using the true start time in the file name.
- Trips that only have a `.route` file carry no timestamps: they are shown on
  the day they started, without duration or speed.
- Trips deleted from Google Drive stay in the local cache until that cache is
  lost (for example by restoring a backup). Removing the integration entry
  deletes the cache.

## Development

```bash
pip install -r requirements_test.txt
python -m pytest
```

`./start_e2e_homeassistant.sh` runs a throwaway Home Assistant with this
integration on <http://localhost:8124> (`--restart`, `--stop`). It does not
load `my`, so add `http://localhost:8124/auth/external/callback` to the
Google OAuth client's redirect URIs when testing there.

Leaflet 1.9.4 (BSD-2-Clause) is bundled in
`custom_components/fuelio_routes/frontend/leaflet`.
