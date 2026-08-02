# sota-wfs — SOTA & Tesla Superchargers → CalTopo

A minimal Python WFS server that publishes SOTA (Summits on the Air) summit
locations and Tesla Supercharger locations for consumption in CalTopo, via an
ngrok https tunnel.

CalTopo only ever issues two request shapes — `GetCapabilities` and
`GetFeature` with a BBOX, GeoJSON output — so the server (`sota_wfs/`, ~400
lines of Flask) implements exactly that and nothing more. Data is loaded
straight from the downloaded artifacts into memory (pandas/numpy); there is no
GeoPackage, no GDAL, no Java, no docker.

## Components

- `serve.py` — WFS server on port 8080 (waitress; trusts ngrok's
  `X-Forwarded-*` headers so capabilities URLs come out as https).
- `fetch/fetch_sota.py` — downloads <https://storage.sota.org.uk/summitslist.csv>,
  validates it, atomically swaps it into `data/`. Daily systemd timer.
- `fetch/fetch_superchargers.py` — downloads Tesla Supercharger locations
  (all US states) from the alt-fuel-stations API at `developer.nlr.gov`.
  Weekly systemd timer. Set `NREL_API_KEY` in `.env` for a personal key
  (default: rate-limited `DEMO_KEY`).
- `systemd/` — user units for the server, ngrok, and both fetch timers.
- Hot reload: the server stats the data files at most every 15 s and reloads
  when the mtime changes, so fetches need no server restart.

## Layers served (namespace `sota`)

| Typename | Fields (popup subset in bold) |
|---|---|
| `sota:SOTA_Summits` | All 17 summit-list columns plus **`SOTLAS`** (link to <https://sotl.as>) and **`Activations`** (activation count as a string, so "0" still displays) |
| `sota:Tesla_Superchargers` | **`name`**, **`address`**, **`stalls`**, **`power_kw`**, **`access`**, plus `street`, `city`, `state`, `zip`, `connectors`, `pricing`, `phone` |

Supercharger field notes: `stalls` = DC fast-charge stall count, `power_kw` =
highest connector power at the site, `access` = hours plus NACS notes. The
extra columns are served but omitted from the CalTopo templates below — any
future template can re-include them via `PROPERTYNAME` without code changes.

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
systemctl --user list-timers fetch-sota.timer fetch-superchargers.timer
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

Use "Save To Account" in the WFS Source dialog to make a layer available on
every map (it appears under Your Data → Layers and in the "Your Overlays"
list). Note each "Save To Account" click creates a new copy — prune old ones
in Your Data → Layers (row → ⓘ → DELETE).

Example map: <https://caltopo.com/m/0N832Q6>

## Verification

```sh
# capabilities list both layers and advertise application/json
curl -fsS 'http://localhost:8080/geoserver/sota/wfs?service=WFS&version=2.0.0&request=GetCapabilities' | grep -c 'sota:'

# exact CalTopo request shape (Maja Rosit test summit)
curl -fsS 'http://localhost:8080/geoserver/wfs?SERVICE=WFS&VERSION=1.1.0&REQUEST=GetFeature&BBOX=42.47,19.84,42.50,19.86&OUTPUTFORMAT=application/json&TYPENAMES=sota:SOTA_Summits&PROPERTYNAME=SummitCode,SummitName,Points,BonusPoints,Activations,SOTLAS,the_geom' | jq -M '.features[0]'

# superchargers through the tunnel
curl -fsS 'https://noneligible-unlithographic-robbie.ngrok-free.dev/geoserver/wfs?SERVICE=WFS&VERSION=1.1.0&REQUEST=GetFeature&BBOX=37.3,-122.1,37.5,-121.9&OUTPUTFORMAT=application/json&TYPENAMES=sota:Tesla_Superchargers' | jq -M '.totalFeatures'
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
