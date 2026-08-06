"""Bulk AZ computation for whole associations from local DEM windows.

Usage: python -m sota_wfs.bulk_az W6 W7 [--workers N] [--limit N]

Prefixes match the association part of SummitCode, so "W7" covers W7A..W7Y.
Elevations come from USGS 3DEP 1/3" COGs (see dem.py) instead of the
rate-limited opentopodata API, so all ~22k W6/W7 summits take hours, not
years. Results land in the same data/az/ cache the WFS server serves from;
summits with an ok cache entry are skipped, cached failures are retried.

Instead of asking api.activation.zone for a coarse bbox per summit (22k
requests to a free service), the grid starts at a 500 m half-width and
doubles whenever the ring touches the grid edge (or no contour closes),
capped at 16 km. Only flat-country summits ever widen past 4 km, so the
big grids stay rare. A summit whose AZ still won't close at the cap is
cached as a failure rather than a clipped polygon.
"""

from __future__ import annotations

import argparse
import math
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import az, registry
from .dem import Dem, TileMissing

START_HALF_M = 500.0
MAX_HALF_M = 16000.0

_tls = threading.local()


def compute_one(ref: str, lat: float, lon: float, alt: float) -> dict:
    dem = getattr(_tls, "dem", None)
    if dem is None:
        dem = _tls.dem = Dem()
    try:
        half = START_HALF_M
        while True:
            dlat = half / 111320.0
            dlon = half / (111320.0 * math.cos(math.radians(lat)))
            node_lat, node_lon = az._build_grid(
                lat, (lon - dlon, lat - dlat, lon + dlon, lat + dlat)
            )
            V = dem.grid(node_lat, node_lon)
            ring = az.ring_from_grid(lat, lon, alt, node_lat, node_lon, V)
            if half >= MAX_HALF_M or (ring and not _touches_edge(ring, node_lat, node_lon)):
                break
            half *= 2  # AZ ran off the grid (or no contour closed): widen
        if ring is None:
            return {"ok": False, "src": "3dep13",
                    "error": f"no closed ring within {MAX_HALF_M / 1000:g} km grid"}
        return {"ok": True, "ring": ring, "src": "3dep13"}
    except Exception as exc:  # noqa: BLE001 - cache the failure and continue
        return {"ok": False, "error": str(exc), "src": "3dep13"}


def _touches_edge(ring, node_lat, node_lon) -> bool:
    step = 1.5 * (node_lat[0] - node_lat[1])
    lon_lo, lon_hi = node_lon[0] + step, node_lon[-1] - step
    lat_lo, lat_hi = node_lat[-1] + step, node_lat[0] - step
    return any(
        x < lon_lo or x > lon_hi or y < lat_lo or y > lat_hi for x, y in ring
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("prefixes", nargs="+", help="association prefixes, e.g. W6 W7")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=None, help="stop after N summits")
    args = ap.parse_args()

    layer = registry.LAYERS["sota_summits"]
    df = layer.loader(layer.source).props  # loader already drops invalid summits
    assoc = df["SummitCode"].str.split("/").str[0]
    df = df[assoc.str.startswith(tuple(args.prefixes))]

    todo = []
    for _, row in df.iterrows():
        entry = az._cached(row["SummitCode"])
        if entry is not None and entry.get("ok"):
            continue
        todo.append((row["SummitCode"], float(row["Latitude"]),
                     float(row["Longitude"]), float(row["AltM"])))
    cached = len(df) - len(todo)
    # Neighbors share DEM tiles/blocks: keep each thread's tile cache warm.
    todo.sort(key=lambda t: (Dem.tile_name(t[1], t[2]), t[2], t[1]))
    if args.limit:
        todo = todo[: args.limit]
    total = len(todo)
    print(f"bulk_az: {total} summits to compute "
          f"({cached} already cached)", flush=True)

    done = failed = 0
    t0 = time.time()
    tally = threading.Lock()

    def work(item):
        nonlocal done, failed
        ref, lat, lon, alt = item
        entry = compute_one(ref, lat, lon, alt)
        az.write_cache(ref, entry)
        if not entry["ok"]:
            print(f"bulk_az: {ref} FAILED: {entry.get('error', 'no ring')}", flush=True)
        with tally:
            done += 1
            if not entry["ok"]:
                failed += 1
            n = done
        if n % 250 == 0 or n == total:
            rate = n / (time.time() - t0)
            eta = (total - n) / rate if rate else 0
            print(f"bulk_az: {n}/{total} ({failed} failed) "
                  f"{rate:.1f}/s eta {eta / 60:.0f}m", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        list(ex.map(work, todo))
    print(f"bulk_az: finished {done} ({failed} failed) "
          f"in {(time.time() - t0) / 60:.1f}m", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
