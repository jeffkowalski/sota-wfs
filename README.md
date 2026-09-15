# sota-wfs — SOTA, Tesla Superchargers & campgrounds → CalTopo

A minimal Python WFS server that publishes SOTA (Summits on the Air) summit
locations, Tesla Supercharger locations, and recreation.gov campgrounds for
consumption in CalTopo, via an ngrok https tunnel.

CalTopo only ever issues two request shapes — `GetCapabilities` and
`GetFeature` with a BBOX, GeoJSON output — so the server (`sota_wfs/`, ~400
lines of Flask) implements exactly that and nothing more. Data is loaded
straight from the downloaded artifacts into memory (pandas/numpy); there is no
GeoPackage, no GDAL, no Java, no docker.

## Components

- `serve.py` — WFS server on port 8080 (waitress; trusts ngrok's
  `X-Forwarded-*` headers so capabilities URLs come out as https).
- `fetch/fetch_sota.py` — downloads <https://storage.sota.org.uk/summitslist.csv>,
  validates it, atomically swaps it into the data dir. Daily systemd timer.
- Data (fetched datasets, AZ ring cache) lives in `~/.local/share/sota-wfs`
  (`$XDG_DATA_HOME` respected, `$SOTA_WFS_DATA` overrides) — outside the
  repo so workspace syncing doesn't ship 31k cache files to other devices.
- `fetch/fetch_superchargers.py` — downloads Tesla Supercharger locations
  (all US states) from the alt-fuel-stations API at `developer.nlr.gov`.
  Weekly systemd timer. Set `NREL_API_KEY` in `.env` for a personal key
  (default: rate-limited `DEMO_KEY`).
- `fetch/fetch_campgrounds.py` — downloads the recreation.gov RIDB bulk CSV
  export (~250 MB zip, no API key) and rolls it up to one point per campground
  (`sota_wfs/ridb.py`). Weekly systemd timer. Set `RIDB_ZIP=/path/to/export.zip`
  to re-roll a local copy without downloading.
- `systemd/` — user units for the server, ngrok, and the three fetch timers.
- Hot reload: the server stats the data files at most every 15 s and reloads
  when the mtime changes, so fetches need no server restart.

## Layers served (namespace `sota`)

| Typename | Fields (popup subset in bold) |
|---|---|
| `sota:SOTA_Summits` | All 17 summit-list columns plus **`SOTLAS`** (link to <https://sotl.as>) and **`Activations`** (activation count as a string, so "0" still displays) |
| `sota:Tesla_Superchargers` | **`name`**, **`address`**, **`stalls`**, **`power_kw`**, **`access`**, plus `street`, `city`, `state`, `zip`, `connectors`, `pricing`, `phone` |
| `sota:Recreation_Camping` | **`title`**, **`kind`** (Campground / Camping area), **`rec_gov`** (link to the recreation.gov campground page, or the parent forest/park page for camping areas), **`reservable`**, **`sites`**, **`site_types`**, **`access`**, **`hookups`**, **`stay_limit`**, **`activities`**, **`overview`**, **`facilities`**, **`allows_pets`**, **`allows_fires`**, plus `id`, `max_vehicle_len`, `fees`, `phone` |

Supercharger field notes: `stalls` = DC fast-charge stall count, `power_kw` =
highest connector power at the site, `access` = hours plus NACS notes. The
extra columns are served but omitted from the CalTopo templates below — any
future template can re-include them via `PROPERTYNAME` without code changes.

Camping field notes: one marker per place to camp (~6k), not per site: every
RIDB "Campground" facility (`kind` = Campground, green tent icon) plus
"Facility"-typed records whose activities include camping (`kind` = Camping
area, brown campfire icon) — dispersed and informal sites such as small lakes
with a few first-come spots. Administrative records among the latter (ranger
districts, offices, marinas, resorts, whole parks/forests, corridors,
town-named entries) are dropped by name (`ridb.is_admin_facility`).
RIDB facility names are a mix of ALL CAPS and proper case; all-caps names are
title-cased at fetch time (`Lake of the Woods RV Park`), mixed-case names are
left alone, and the few blank-named siteless stubs are dropped. The
per-site attributes in RIDB are rolled up: `site_types` counts sites by type,
`access` lists the distinct site access modes (drive-in, hike-in, walk-in,
boat-in), `hookups` summarizes electric amperage / water / sewer, `pets` and
`campfires` are "Yes" if any site allows them (served as `allows_pets` /
`allows_fires`: CalTopo's proxy re-emits properties in Java HashMap order, so
popup order is fixed by the property *names*; these two hash to the last
buckets, after `facilities`). `overview` and `facilities`
are the same-named sections of the facility description (most RIDB
descriptions are h2-sectioned: Overview / Facilities / Recreation / Natural
Features / Nearby Attractions), HTML stripped and each truncated to 600
characters; a description without headings lands whole in `overview`.
Seasons live in `overview`; toilets, showers and drinking water in `facilities`. Live site availability is NOT in
the export — follow `rec_gov` to recreation.gov for that (CalTopo renders
URL-valued properties as links; the name is chosen so CalTopo's hash-ordered
popup lists it first, see the `allows_*` note). `reservable` = "No" marks first-come-first-served
campgrounds. Markers use CalTopo's "Tent" icon (`marker-symbol` = `camping`) for campgrounds
and "Campfire" (`campfire`) for camping areas.

## Installation

Prerequisites: `python3`, `ngrok` (with authtoken:
`ngrok config add-authtoken ...`). Then:

```sh
git clone git@github.com:jeffkowalski/sota-wfs.git ~/Dropbox/workspace/sota-wfs
cd ~/Dropbox/workspace/sota-wfs
./install.sh
```

`install.sh` creates the venv, does the initial data fetch, installs the
systemd user units (rewriting paths to wherever the repo lives), enables and
starts everything, and enables lingering so services run without a login
session.

Note: the ngrok unit pins the tunnel to this account's static domain
`noneligible-unlithographic-robbie.ngrok-free.dev`. A new owner gets their own
static domain at <https://dashboard.ngrok.com> → Domains, edits
`systemd/ngrok.service` accordingly, re-runs `./install.sh`, and updates the
hostname in the CalTopo layer URLs once.

## Operations

```sh
systemctl --user status sota-wfs ngrok
systemctl --user list-timers fetch-sota.timer fetch-superchargers.timer fetch-campgrounds.timer
journalctl --user -u sota-wfs -f      # server logs
journalctl --user -u fetch-sota       # last fetch result
.venv/bin/python fetch/fetch_sota.py  # manual refresh (picked up within ~15 s)
.venv/bin/python -m pytest tests/ -q  # test suite
```

## CalTopo integration

Tunnel URL (stable, static domain):
`https://noneligible-unlithographic-robbie.ngrok-free.dev`

### Auto-configuration (Add → WFS Source → Auto-Configure URL)

```
https://noneligible-unlithographic-robbie.ngrok-free.dev/geoserver/sota/wfs?service=WFS&version=2.0.0&request=GetCapabilities
```

### Manual layer templates (Add → WFS Source → URL Template)

SOTA summits, limited fields (label: `SummitName` or `SummitCode`):

```
https://noneligible-unlithographic-robbie.ngrok-free.dev/geoserver/wfs?SERVICE=WFS&VERSION=1.1.0&REQUEST=GetFeature&BBOX={bottom},{left},{top},{right}&OUTPUTFORMAT=application/json&TYPENAMES=sota:SOTA_Summits&PROPERTYNAME=SummitCode,SummitName,Points,BonusPoints,Activations,SOTLAS,the_geom
```

SOTA summits, all fields: drop the `PROPERTYNAME` parameter.

Tesla Superchargers (label: `name`):

```
https://noneligible-unlithographic-robbie.ngrok-free.dev/geoserver/wfs?SERVICE=WFS&VERSION=1.1.0&REQUEST=GetFeature&BBOX={bottom},{left},{top},{right}&OUTPUTFORMAT=application/json&TYPENAMES=sota:Tesla_Superchargers&PROPERTYNAME=name,address,stalls,power_kw,access,the_geom
```

Recreation.gov camping (label: `title`):

```
https://noneligible-unlithographic-robbie.ngrok-free.dev/geoserver/wfs?SERVICE=WFS&VERSION=1.1.0&REQUEST=GetFeature&BBOX={bottom},{left},{top},{right}&OUTPUTFORMAT=application/json&TYPENAMES=sota:Recreation_Camping&PROPERTYNAME=title,kind,rec_gov,reservable,sites,site_types,access,hookups,stay_limit,activities,overview,facilities,allows_pets,allows_fires,the_geom
```

Use "Save To Account" in the WFS Source dialog to make a layer available on
every map (it appears under Your Data → Layers and in the "Your Overlays"
list). Note each "Save To Account" click creates a new copy — prune old ones
in Your Data → Layers (row → ⓘ → DELETE).

Example map: <https://caltopo.com/m/0N832Q6>

## Verification

```sh
# capabilities list all three layers and advertise application/json
curl -fsS 'http://localhost:8080/geoserver/sota/wfs?service=WFS&version=2.0.0&request=GetCapabilities' | grep -c 'sota:'

# exact CalTopo request shape (Maja Rosit test summit)
curl -fsS 'http://localhost:8080/geoserver/wfs?SERVICE=WFS&VERSION=1.1.0&REQUEST=GetFeature&BBOX=42.47,19.84,42.50,19.86&OUTPUTFORMAT=application/json&TYPENAMES=sota:SOTA_Summits&PROPERTYNAME=SummitCode,SummitName,Points,BonusPoints,Activations,SOTLAS,the_geom' | jq -M '.features[0]'

# superchargers through the tunnel
curl -fsS 'https://noneligible-unlithographic-robbie.ngrok-free.dev/geoserver/wfs?SERVICE=WFS&VERSION=1.1.0&REQUEST=GetFeature&BBOX=37.3,-122.1,37.5,-121.9&OUTPUTFORMAT=application/json&TYPENAMES=sota:Tesla_Superchargers' | jq -M '.totalFeatures'

# campgrounds around Yosemite Valley
curl -fsS 'http://localhost:8080/geoserver/wfs?SERVICE=WFS&VERSION=1.1.0&REQUEST=GetFeature&BBOX=37.7,-119.7,37.8,-119.5&OUTPUTFORMAT=application/json&TYPENAMES=sota:Recreation_Camping' | jq -M '.features[].properties.title'
```

## Troubleshooting

- **CalTopo shows no features** — check `systemctl --user status sota-wfs ngrok`.
  The capabilities response must contain https hrefs with the ngrok hostname
  (not localhost) — if not, ngrok's `X-Forwarded-*` headers aren't reaching
  waitress (see `trusted_proxy` in `serve.py`).
- **Markers labeled with feature IDs** (`Tesla_Superchargers.123`) — the
  layer's "Label Name" doesn't match a property in its `PROPERTYNAME` list;
  edit the layer and set it (e.g. `name`).
- **Stale data** — `journalctl --user -u fetch-sota`; the fetcher refuses to
  replace the file if the download fails validation, which is intentional.
- **Port 8080 busy** — something else holds the port: `ss -tlnp | grep 8080`.

## History

Until 2026-08 this was a GeoServer pipeline: CSV → ogr2ogr → GeoPackage →
kartoza/geoserver docker container configured over REST, ngrok run by hand,
all driven manually from org-babel blocks in an org-roam file. See git history
(`20250925163459-sota_mapserver.org`) for the literate version.
