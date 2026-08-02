"""GetFeature: bbox parsing, filtering, GeoServer-shaped GeoJSON output."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .loaders import LayerData
from .registry import Layer

COORD_DECIMALS = 6


class WfsError(Exception):
    def __init__(self, code: str, text: str, locator: str | None = None):
        super().__init__(text)
        self.code = code
        self.locator = locator


def parse_bbox(raw: str) -> tuple[float, float, float, float]:
    """Return (minx, miny, maxx, maxy) from a WFS BBOX parameter.

    CalTopo sends {bottom},{left},{top},{right} with VERSION=1.1.0 and no CRS
    token, i.e. lat-first (EPSG:4326 axis order) — matching GeoServer's
    interpretation. A trailing CRS84 token means lon-first.
    """
    parts = [p.strip() for p in raw.split(",")]
    if len(parts) not in (4, 5):
        raise WfsError("InvalidParameterValue", f"BBOX must have 4 values, got {len(parts)}", "bbox")
    try:
        a, b, c, d = (float(p) for p in parts[:4])
    except ValueError:
        raise WfsError("InvalidParameterValue", f"Malformed BBOX: {raw!r}", "bbox")
    lat_first = True
    if len(parts) == 5 and "CRS84" in parts[4].upper():
        lat_first = False
    if lat_first and (abs(a) > 90 or abs(c) > 90):
        lat_first = False  # values can't be latitudes; caller sent lon-first
    if lat_first:
        miny, minx, maxy, maxx = a, b, c, d
    else:
        minx, miny, maxx, maxy = a, b, c, d
    return minx, miny, maxx, maxy


# Style-hint columns are served even when PROPERTYNAME omits them, so
# CalTopo's saved layer templates don't have to change to pick them up.
_ALWAYS_SERVED = ("marker-color", "marker-symbol")


def resolve_properties(data: LayerData, raw: str | None) -> list[str]:
    columns = list(data.props.columns)
    if raw is None:
        return columns
    by_lower = {c.lower(): c for c in columns}
    out = []
    for name in raw.split(","):
        name = name.strip()
        if not name or name.lower() == "the_geom":
            continue  # geometry is always emitted
        col = by_lower.get(name.lower())
        if col is not None:
            out.append(col)
    for col in _ALWAYS_SERVED:
        if col in columns and col not in out:
            out.append(col)
    return out


def _json_value(v):
    if v is None or v is pd.NA:
        return None
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        f = float(v)
        return None if np.isnan(f) else f
    return v


def select(
    layer: Layer,
    data: LayerData,
    bbox: tuple[float, float, float, float] | None,
    prop_names: list[str],
    count: int | None,
) -> dict:
    if bbox is None:
        idx = np.arange(len(data.lons))
    else:
        minx, miny, maxx, maxy = bbox
        mask = (
            (data.lons >= minx)
            & (data.lons <= maxx)
            & (data.lats >= miny)
            & (data.lats <= maxy)
        )
        idx = np.flatnonzero(mask)
    matched = int(idx.size)
    if count is not None:
        idx = idx[:count]

    lons = np.round(data.lons[idx], COORD_DECIMALS)
    lats = np.round(data.lats[idx], COORD_DECIMALS)
    records = data.props.iloc[idx].to_dict("records")

    features = []
    for i, (fid, lon, lat, rec) in enumerate(zip(idx, lons, lats, records)):
        lon, lat = float(lon), float(lat)
        features.append(
            {
                "type": "Feature",
                "id": f"{layer.name}.{int(fid) + 1}",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "geometry_name": "the_geom",
                "properties": {k: _json_value(rec[k]) for k in prop_names},
                "bbox": [lon, lat, lon, lat],
            }
        )

    fc = {
        "type": "FeatureCollection",
        "features": features,
        "totalFeatures": matched,
        "numberMatched": matched,
        "numberReturned": len(features),
        "timeStamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
        "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::4326"}},
    }
    if features:
        fc["bbox"] = [
            float(np.min(lons)),
            float(np.min(lats)),
            float(np.max(lons)),
            float(np.max(lats)),
        ]
    return fc
