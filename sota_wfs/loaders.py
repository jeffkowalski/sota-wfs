"""Load fetched artifacts into memory as LayerData."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

SOTA_HEADER = (
    "SummitCode,AssociationName,RegionName,SummitName,AltM,AltFt,GridRef1,GridRef2,"
    "Longitude,Latitude,Points,BonusPoints,ValidFrom,ValidTo,ActivationCount,"
    "ActivationDate,ActivationCall"
)

# Column dtypes matching what GeoServer served from the ogr2ogr-built GeoPackage
# (AUTODETECT_TYPE=YES), so JSON output types stay identical.
_SOTA_INT_COLS = ["AltM", "AltFt", "Points", "BonusPoints", "ActivationCount"]
_SOTA_FLOAT_COLS = ["GridRef1", "GridRef2", "Longitude", "Latitude"]


@dataclass
class LayerData:
    lons: np.ndarray
    lats: np.ndarray
    props: pd.DataFrame
    bbox: tuple[float, float, float, float]  # minx, miny, maxx, maxy
    mtime: float


def _finish(df: pd.DataFrame, lons: np.ndarray, lats: np.ndarray, mtime: float) -> LayerData:
    bbox = (
        float(np.min(lons)),
        float(np.min(lats)),
        float(np.max(lons)),
        float(np.max(lats)),
    )
    return LayerData(lons=lons, lats=lats, props=df, bbox=bbox, mtime=mtime)


def sota_csv_loader(path: Path) -> LayerData:
    mtime = path.stat().st_mtime
    df = pd.read_csv(
        path,
        skiprows=1,  # title line "SOTA Summits List (Date=...)"
        dtype=str,
        keep_default_na=False,
    )
    for col in _SOTA_FLOAT_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in _SOTA_INT_COLS:
        df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    df = df.dropna(subset=["Longitude", "Latitude"]).reset_index(drop=True)
    # Serve only currently-valid summits (ValidFrom/ValidTo are DD/MM/YYYY);
    # a missing bound is treated as unbounded.
    today = pd.Timestamp.today().normalize()
    valid_from = pd.to_datetime(df["ValidFrom"], format="%d/%m/%Y", errors="coerce")
    valid_to = pd.to_datetime(df["ValidTo"], format="%d/%m/%Y", errors="coerce")
    df = df[
        (valid_from.isna() | (valid_from <= today))
        & (valid_to.isna() | (today <= valid_to))
    ].reset_index(drop=True)
    df["SOTLAS"] = "https://sotl.as/summits/" + df["SummitCode"]
    df["Activations"] = df["ActivationCount"].astype(object).map(
        lambda v: "" if pd.isna(v) else str(v)
    )
    lons = df["Longitude"].to_numpy(dtype=np.float64)
    lats = df["Latitude"].to_numpy(dtype=np.float64)
    return _finish(df, lons, lats, mtime)


# Properties surfaced for each Supercharger station, in output order:
# (served name, source key in the API feature; None = derived).
_NREL_PROPS = [
    ("name", "station_name"),
    ("address", None),
    ("street", "street_address"),
    ("city", "city"),
    ("state", "state"),
    ("zip", "zip"),
    ("stalls", "ev_dc_fast_num"),
    ("power_kw", None),
    ("connectors", "ev_connector_types"),
    ("pricing", "ev_pricing"),
    ("access", "access_days_time"),
    ("phone", "station_phone"),
]


def _max_power_kw(props: dict):
    """Highest connector power_kw across the station's charging units."""
    best = None
    for unit in props.get("ev_charging_units") or []:
        for conn in (unit.get("connectors") or {}).values():
            kw = conn.get("power_kw")
            if kw is not None and (best is None or kw > best):
                best = kw
    return None if best is None else int(best)


def nrel_geojson_loader(path: Path) -> LayerData:
    mtime = path.stat().st_mtime
    with open(path) as f:
        fc = json.load(f)
    rows, lons, lats = [], [], []
    for feat in fc.get("features", []):
        geom = feat.get("geometry") or {}
        if geom.get("type") != "Point":
            continue
        lon, lat = geom["coordinates"][:2]
        if lon is None or lat is None:
            continue
        p = feat.get("properties", {})
        row = {}
        for out, src in _NREL_PROPS:
            if out == "power_kw":
                v = _max_power_kw(p)
            elif out == "address":
                v = ", ".join(
                    str(p[part]) for part in ("street_address", "city", "state") if p.get(part)
                )
            else:
                v = p.get(src)
            if isinstance(v, list):
                v = " ".join(str(x) for x in v)
            row[out] = "" if v is None else v
        rows.append(row)
        lons.append(float(lon))
        lats.append(float(lat))
    df = pd.DataFrame(rows, columns=[out for out, _ in _NREL_PROPS])
    return _finish(df, np.asarray(lons), np.asarray(lats), mtime)
