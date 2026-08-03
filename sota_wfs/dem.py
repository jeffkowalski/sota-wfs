"""Windowed elevation reads from USGS 3DEP 1/3-arcsecond COGs on AWS.

This is the same source data opentopodata's ned10m serves, but read directly
as HTTP range requests against the public prd-tnm bucket: each summit grid
costs a few hundred KB instead of dozens of rate-limited API calls, which is
what makes bulk AZ computation feasible.
"""

from __future__ import annotations

import math
import os

import numpy as np
import rasterio
from rasterio.windows import Window

TILE_URL = (
    "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/"
    "TIFF/current/{name}/USGS_13_{name}.tif"
)
NODATA_SENTINEL = -1e9  # matches az._fetch_elevations' null handling

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
os.environ.setdefault("VSI_CACHE", "TRUE")


class TileMissing(Exception):
    pass


class Dem:
    """Bilinear sampler over 3DEP tiles; keeps the last few tiles open.

    Not thread-safe: use one instance per worker thread.
    """

    def __init__(self, max_open: int = 4):
        self.max_open = max_open
        self._open: dict[str, rasterio.DatasetReader] = {}

    @staticmethod
    def tile_name(lat: float, lon: float) -> str:
        # Tiles are named by their NW corner: n35w120 covers 34..35N, 120..119W.
        return f"n{math.ceil(lat):02d}w{math.ceil(-lon):03d}"

    def _ds(self, name: str):
        ds = self._open.pop(name, None)
        if ds is None:
            try:
                ds = rasterio.open(TILE_URL.format(name=name))
            except rasterio.errors.RasterioIOError as exc:
                raise TileMissing(f"DEM tile {name} unavailable: {exc}") from exc
            while len(self._open) >= self.max_open:
                self._open.pop(next(iter(self._open))).close()
        self._open[name] = ds  # re-insert keeps LRU order
        return ds

    def grid(self, node_lat: list, node_lon: list) -> list[list[float]]:
        """Elevations for the node_lat x node_lon mesh, shaped like
        az._fetch_elevations' return: rows follow node_lat, cols node_lon."""
        lats = np.repeat(node_lat, len(node_lon))
        lons = np.tile(node_lon, len(node_lat))
        out = np.full(lats.size, NODATA_SENTINEL)

        names = np.array([self.tile_name(la, lo) for la, lo in zip(lats, lons)])
        got_any = False
        for name in np.unique(names):
            sel = names == name
            try:
                out[sel] = self._sample(name, lats[sel], lons[sel])
                got_any = True
            except TileMissing:
                pass  # e.g. grid margin crossing into Canada: clip at data edge
        if not got_any:
            raise TileMissing(f"no DEM coverage for any of {np.unique(names)}")
        return out.reshape(len(node_lat), len(node_lon)).tolist()

    def _sample(self, name: str, lats: np.ndarray, lons: np.ndarray) -> np.ndarray:
        ds = self._ds(name)
        t = ds.transform
        # Fractional pixel indices relative to pixel centers.
        cols = (lons - t.c) / t.a - 0.5
        rows = (lats - t.f) / t.e - 0.5
        r0 = np.clip(np.floor(rows).astype(int), 0, ds.height - 2)
        c0 = np.clip(np.floor(cols).astype(int), 0, ds.width - 2)
        fr = np.clip(rows - r0, 0.0, 1.0)
        fc = np.clip(cols - c0, 0.0, 1.0)

        win = Window(
            int(c0.min()), int(r0.min()),
            int(c0.max() - c0.min() + 2), int(r0.max() - r0.min() + 2),
        )
        arr = ds.read(1, window=win).astype(float)
        rr = r0 - int(r0.min())
        cc = c0 - int(c0.min())
        v00 = arr[rr, cc]
        v01 = arr[rr, cc + 1]
        v10 = arr[rr + 1, cc]
        v11 = arr[rr + 1, cc + 1]
        vals = (
            v00 * (1 - fr) * (1 - fc)
            + v01 * (1 - fr) * fc
            + v10 * fr * (1 - fc)
            + v11 * fr * fc
        )
        nodata = ds.nodata if ds.nodata is not None else -999999.0
        bad = (v00 == nodata) | (v01 == nodata) | (v10 == nodata) | (v11 == nodata)
        vals[bad] = NODATA_SENTINEL
        return vals
