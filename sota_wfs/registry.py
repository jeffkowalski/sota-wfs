"""Layer registry: what we serve and how it hot-reloads."""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .loaders import LayerData, nrel_geojson_loader, sota_csv_loader

ROOT_DIR = Path(os.environ.get("SOTA_WFS_ROOT", Path(__file__).resolve().parent.parent))
DATA_DIR = ROOT_DIR / "data"

RELOAD_CHECK_SECONDS = 15


@dataclass(frozen=True)
class Layer:
    name: str      # local layer name, e.g. "SOTA_Summits"
    ns: str        # namespace prefix, e.g. "sota"
    title: str
    abstract: str
    source: Path
    loader: Callable[[Path], LayerData]

    @property
    def qname(self) -> str:
        return f"{self.ns}:{self.name}"


LAYERS: dict[str, Layer] = {
    layer.name.lower(): layer
    for layer in [
        Layer(
            name="SOTA_Summits",
            ns="sota",
            title="SOTA Summits",
            abstract="Summits on the Air (SOTA) summit locations",
            source=DATA_DIR / "summitslist.csv",
            loader=sota_csv_loader,
        ),
        Layer(
            name="Tesla_Superchargers",
            ns="sota",
            title="Tesla Superchargers (CA)",
            abstract="Tesla Supercharger locations in California, from the alternative fuel stations API",
            source=DATA_DIR / "superchargers.geojson",
            loader=nrel_geojson_loader,
        ),
    ]
}


def resolve_typename(raw: str) -> Layer | None:
    local = raw.split(":", 1)[-1].strip()
    return LAYERS.get(local.lower())


def available_layers() -> list[Layer]:
    return [layer for layer in LAYERS.values() if layer.source.exists()]


_cache: dict[str, tuple[LayerData, float]] = {}
_lock = threading.Lock()


def get_data(layer: Layer) -> LayerData:
    now = time.monotonic()
    entry = _cache.get(layer.name)
    if entry is not None:
        data, last_check = entry
        if now - last_check < RELOAD_CHECK_SECONDS:
            return data
        try:
            mtime = layer.source.stat().st_mtime
        except FileNotFoundError:
            return data  # keep serving stale data if the file vanishes mid-swap
        if mtime == data.mtime:
            _cache[layer.name] = (data, now)
            return data
    with _lock:
        # Re-check under the lock: another thread may have just reloaded.
        entry = _cache.get(layer.name)
        if entry is not None and entry[0].mtime == layer.source.stat().st_mtime:
            _cache[layer.name] = (entry[0], time.monotonic())
            return entry[0]
        data = layer.loader(layer.source)
        _cache[layer.name] = (data, time.monotonic())
        return data
