"""Activation-zone polygons: cached computation, bbox-gated serving.

AZ rings are computed with the azgen-style approach ported from
sota-template/build_caltopo.py: a coarse SRTM AZ from api.activation.zone
bounds a ~10 m DEM grid fetched from opentopodata, and marching squares at
summit_alt - 25 m yields the ring containing the summit.

The elevation API is slow and rate-limited (~1 req/s, ~1000 calls/day; one
summit costs ~30-60 calls), so rings are never computed during a request.
GetFeature serves whatever is cached under data/az/ and queues the rest for
a single background worker; the polygon appears on a later pan/refresh.
Polygons are only served when the request bbox is small (zoomed well in).
"""

from __future__ import annotations

import json
import math
import queue
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import registry

AZAPI = "https://api.activation.zone/"
OPENTOPO = "https://api.opentopodata.org/v1/"

AZ_COLOR = "#FFAA00"
AZ_THRES_M = 25            # SOTA activation-zone vertical drop
MAX_SPAN_DEG = 0.08        # serve AZs only when bbox lat span is below this (~Z14+)
DAILY_CALL_BUDGET = 900    # opentopodata public limit is 1000 calls/day
RETRY_FAILED_SECONDS = 7 * 86400

COMPUTE_ENABLED = True     # tests disable this to keep the worker offline

_pending: set[str] = set()
_queue: queue.Queue = queue.Queue()
_lock = threading.Lock()
_worker_started = False


def _cache_dir():
    return registry.DATA_DIR / "az"


def _cache_path(ref: str):
    return _cache_dir() / (ref.replace("/", "_") + ".json")


# ---------- serving ----------

def features_for_bbox(data, idx, bbox) -> list[dict]:
    """Styled AZ Polygon features for the summits at data.props.iloc[idx],
    when the view is zoomed in enough. Queues computation for cache misses."""
    minx, miny, maxx, maxy = bbox
    if maxy - miny > MAX_SPAN_DEG:
        return []
    feats = []
    cols = data.props.iloc[idx]
    for _, row in cols.iterrows():
        ref = row["SummitCode"]
        entry = _cached(ref)
        if entry is None:
            _enqueue(ref, row)
        elif entry.get("ok") and entry.get("ring"):
            feats.append(_feature(ref, row, entry["ring"]))
    return feats


def _cached(ref: str) -> dict | None:
    path = _cache_path(ref)
    try:
        with open(path) as f:
            entry = json.load(f)
    except (OSError, ValueError):
        return None
    if not entry.get("ok") and time.time() - path.stat().st_mtime > RETRY_FAILED_SECONDS:
        return None  # stale failure: recompute
    return entry


def _feature(ref: str, row, ring: list) -> dict:
    lons = [p[0] for p in ring]
    lats = [p[1] for p in ring]
    return {
        "type": "Feature",
        "id": f"SOTA_Summits.az-{ref.replace('/', '_')}",
        "geometry": {"type": "Polygon", "coordinates": [ring]},
        "geometry_name": "the_geom",
        "properties": {
            "SummitCode": ref,
            "SummitName": f"{row['SummitName']} AZ",
            "stroke": AZ_COLOR,
            "stroke-width": 1,
            "stroke-opacity": 1,
            "fill": AZ_COLOR,
            "fill-opacity": 0.1,
        },
        "bbox": [min(lons), min(lats), max(lons), max(lats)],
    }


def _enqueue(ref: str, row) -> None:
    global _worker_started
    if not COMPUTE_ENABLED:
        return
    try:
        lat, lon, alt = float(row["Latitude"]), float(row["Longitude"]), float(row["AltM"])
    except (TypeError, ValueError):
        return
    with _lock:
        if ref in _pending:
            return
        _pending.add(ref)
        _queue.put((ref, lat, lon, alt))
        if not _worker_started:
            threading.Thread(target=_worker, daemon=True, name="az-worker").start()
            _worker_started = True


# ---------- background worker ----------

def _worker():
    while True:
        ref, lat, lon, alt = _queue.get()
        while not _budget_ok():
            print("az: daily elevation budget spent; worker sleeping", flush=True)
            time.sleep(1800)
        try:
            ring = _compute_az(ref, lat, lon, alt)
            entry = {"ok": ring is not None, "ring": ring}
        except Exception as exc:  # noqa: BLE001 - cache the failure, keep working
            print(f"az: {ref} failed: {exc}", flush=True)
            entry = {"ok": False, "error": str(exc)}
        write_cache(ref, entry)
        with _lock:
            _pending.discard(ref)
        if entry["ok"]:
            print(f"az: {ref} cached ({len(entry['ring'])} pts)", flush=True)


def write_cache(ref: str, entry: dict) -> None:
    _cache_dir().mkdir(parents=True, exist_ok=True)
    tmp = _cache_path(ref).with_suffix(".json.tmp")
    with open(tmp, "w") as f:
        json.dump(entry, f)
    tmp.replace(_cache_path(ref))


# ---------- opentopodata call budget ----------

def _budget_path():
    return _cache_dir() / "opentopo_budget.json"

def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _budget_ok() -> bool:
    try:
        with open(_budget_path()) as f:
            b = json.load(f)
    except (OSError, ValueError):
        return True
    return b.get("day") != _today() or b.get("calls", 0) < DAILY_CALL_BUDGET


def _spend(calls: int) -> None:
    try:
        with open(_budget_path()) as f:
            b = json.load(f)
    except (OSError, ValueError):
        b = {}
    if b.get("day") != _today():
        b = {"day": _today(), "calls": 0}
    b["calls"] += calls
    _cache_dir().mkdir(parents=True, exist_ok=True)
    with open(_budget_path(), "w") as f:
        json.dump(b, f)


# ---------- AZ computation (ported from sota-template/build_caltopo.py) ----------

def _http_json(url: str, body: dict | None = None, timeout: int = 90):
    headers = {"User-Agent": "sota-wfs/1.0"}  # api.activation.zone 403s Python-urllib/*
    if body is not None:
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    else:
        req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _dataset(lat: float, lon: float) -> str:
    """Best opentopodata dataset for a location: 10 m NED over the US,
    30 m SRTM where available, ASTER for high latitudes."""
    if 17.0 <= lat <= 72.0 and -180.0 <= lon <= -64.0:
        return "ned10m"
    if -56.0 <= lat <= 60.0:
        return "srtm30m"
    return "aster30m"


def _coarse_bbox(ref: str, lat: float, lon: float, alt: float):
    """Cheap 30 m SRTM AZ from activation.zone, used only to bound the fine
    grid. Falls back to a fixed ~400 m box if the service is unavailable."""
    try:
        body = {"summit_ref": ref, "summit_lat": lat, "summit_long": lon,
                "summit_alt": int(round(alt)), "deg_delta": 0.040,
                "sota_summit_alt_thres": AZ_THRES_M}
        wkt = _http_json(AZAPI, body)["az"]
        ring = wkt[wkt.find("((") + 2: wkt.rfind("))")]
        pts = [tuple(float(x) for x in p.strip().split()) for p in ring.split(",")]
        lons = [p[0] for p in pts]
        lats = [p[1] for p in pts]
        return min(lons), min(lats), max(lons), max(lats)
    except Exception as exc:
        print(f"az: {ref} coarse bbox failed ({exc}); using fixed box", flush=True)
        d = 0.0045
        return lon - d, lat - d, lon + d, lat + d


def _build_grid(lat: float, bbox, margin_m: float = 120.0, step_m: float = 10.0):
    lonmin, latmin, lonmax, latmax = bbox
    mlat = margin_m / 111320.0
    mlon = margin_m / (111320.0 * math.cos(math.radians(lat)))
    lonmin -= mlon; lonmax += mlon; latmin -= mlat; latmax += mlat
    lat_step = step_m / 111320.0
    lon_step = step_m / (111320.0 * math.cos(math.radians(lat)))
    nlat = int(math.ceil((latmax - latmin) / lat_step)) + 1
    nlon = int(math.ceil((lonmax - lonmin) / lon_step)) + 1
    node_lat = [latmax - i * lat_step for i in range(nlat)]   # row 0 = north
    node_lon = [lonmin + j * lon_step for j in range(nlon)]   # col 0 = west
    return node_lat, node_lon


def _fetch_elevations(url: str, node_lat: list, node_lon: list):
    locs = [(la, lo) for la in node_lat for lo in node_lon]
    elev = []
    for k in range(0, len(locs), 100):
        chunk = locs[k:k + 100]
        q = "|".join(f"{la:.6f},{lo:.6f}" for la, lo in chunk)
        for attempt in range(6):
            try:
                d = _http_json(url + "?" + urllib.parse.urlencode({"locations": q}),
                               timeout=60)
                _spend(1)
                if d.get("status") == "OK":
                    elev += [x["elevation"] for x in d["results"]]
                    break
            except urllib.error.HTTPError as e:
                _spend(1)
                if e.code not in (429, 500, 502, 503):
                    raise
            except (urllib.error.URLError, TimeoutError, ValueError):
                pass
            time.sleep(2 + attempt)
        else:
            raise RuntimeError(f"opentopodata failed at chunk {k}")
        time.sleep(1.1)   # public rate limit ~1 req/s
    nlon = len(node_lon)
    return [[(elev[i * nlon + j] if elev[i * nlon + j] is not None else -1e9)
             for j in range(nlon)] for i in range(len(node_lat))]


def _marching_squares(node_lat, node_lon, V, level):
    def interp(pa, va, pb, vb):
        t = 0.0 if va == vb else (level - va) / (vb - va)
        return (pa[0] + t * (pb[0] - pa[0]), pa[1] + t * (pb[1] - pa[1]))
    segs = []
    ni, nj = len(node_lat), len(node_lon)
    for i in range(ni - 1):
        for j in range(nj - 1):
            TL = (node_lon[j], node_lat[i]);       vTL = V[i][j]
            TR = (node_lon[j+1], node_lat[i]);     vTR = V[i][j+1]
            BR = (node_lon[j+1], node_lat[i+1]);   vBR = V[i+1][j+1]
            BL = (node_lon[j], node_lat[i+1]);     vBL = V[i+1][j]
            idx = (1 if vTL > level else 0) | (2 if vTR > level else 0) \
                | (4 if vBR > level else 0) | (8 if vBL > level else 0)
            if idx in (0, 15):
                continue
            top    = lambda: interp(TL, vTL, TR, vTR)
            right  = lambda: interp(TR, vTR, BR, vBR)
            bottom = lambda: interp(BL, vBL, BR, vBR)
            left   = lambda: interp(TL, vTL, BL, vBL)
            if idx in (1, 14): segs.append((left(), top()))
            elif idx in (2, 13): segs.append((top(), right()))
            elif idx in (3, 12): segs.append((left(), right()))
            elif idx in (4, 11): segs.append((right(), bottom()))
            elif idx in (6, 9):  segs.append((top(), bottom()))
            elif idx in (7, 8):  segs.append((left(), bottom()))
            elif idx == 5:  segs.append((left(), top())); segs.append((right(), bottom()))
            elif idx == 10: segs.append((top(), right())); segs.append((left(), bottom()))
    from collections import defaultdict
    key = lambda p: (round(p[0], 9), round(p[1], 9))
    adj = defaultdict(list)
    for a, b in segs:
        adj[key(a)].append((b, key(b)))
        adj[key(b)].append((a, key(a)))
    rings, used = [], set()
    for a, b in segs:
        ka = key(a)
        if ka in used:
            continue
        ring = [a]; ck = ka; used.add(ck)
        while True:
            nxts = [(p, pk) for (p, pk) in adj[ck] if pk not in used]
            if not nxts:
                break
            p, pk = nxts[0]
            ring.append(p); used.add(pk); ck = pk
            if pk == ka:
                break
        if len(ring) >= 4:
            rings.append(ring)
    return rings


def _point_in_ring(pt, ring) -> bool:
    x, y = pt; inside = False; n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]; x2, y2 = ring[(i + 1) % n]
        if ((y1 > y) != (y2 > y)) and \
           (x < (x2 - x1) * (y - y1) / (y2 - y1 + 1e-30) + x1):
            inside = not inside
    return inside


def _compute_az(ref: str, lat: float, lon: float, alt: float) -> list | None:
    node_lat, node_lon = _build_grid(lat, _coarse_bbox(ref, lat, lon, alt))
    n = len(node_lat) * len(node_lon)
    print(f"az: {ref} grid {len(node_lat)}x{len(node_lon)} "
          f"(~{-(-n // 100)} elevation calls)", flush=True)
    V = _fetch_elevations(OPENTOPO + _dataset(lat, lon), node_lat, node_lon)
    return ring_from_grid(lat, lon, alt, node_lat, node_lon, V)


def ring_from_grid(lat, lon, alt, node_lat, node_lon, V) -> list | None:
    """Marching squares at the AZ cutoff; returns the closed ring containing
    the summit (largest ring as fallback), rounded to 6 decimals."""
    ci = min(range(len(node_lat)), key=lambda i: abs(node_lat[i] - lat))
    cj = min(range(len(node_lon)), key=lambda j: abs(node_lon[j] - lon))
    dem_center = V[ci][cj]
    cutoff = alt - AZ_THRES_M
    if alt - dem_center > AZ_THRES_M - 1:      # azgen guard: trust the DEM
        cutoff = dem_center - AZ_THRES_M
    rings = _marching_squares(node_lat, node_lon, V, cutoff)
    cont = [r for r in rings if _point_in_ring((lon, lat), r)] or rings
    if not cont:
        return None
    ring = [[round(x, 6), round(y, 6)] for x, y in max(cont, key=len)]
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    return ring
