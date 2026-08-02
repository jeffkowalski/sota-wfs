#!/usr/bin/env python3
"""Download Tesla Supercharger locations (California) from the alternative
fuel stations API; validate; atomically swap into place.

Run weekly (systemd timer). Set NREL_API_KEY for a personal key; DEMO_KEY
works but is heavily rate-limited.
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

API_KEY = os.environ.get("NREL_API_KEY", "DEMO_KEY")
URL = (
    "https://developer.nlr.gov/api/alt-fuel-stations/v1.geojson"
    f"?api_key={API_KEY}&fuel_type=ELEC&ev_network=Tesla&state=CA&status=E&limit=all"
)
DATA_DIR = Path(os.environ.get("SOTA_WFS_ROOT", Path(__file__).resolve().parent.parent)) / "data"
DEST = DATA_DIR / "superchargers.geojson"

MIN_FEATURES = 50


def validate(path: Path) -> None:
    with open(path) as f:
        fc = json.load(f)
    if fc.get("type") != "FeatureCollection":
        raise ValueError(f"Not a FeatureCollection: type={fc.get('type')!r}")
    n = len(fc.get("features", []))
    if n < MIN_FEATURES:
        raise ValueError(f"Only {n} features (expected > {MIN_FEATURES})")


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DEST.with_suffix(".geojson.tmp")
    try:
        with urllib.request.urlopen(URL, timeout=120) as resp, open(tmp, "wb") as out:
            while chunk := resp.read(1 << 20):
                out.write(chunk)
        validate(tmp)
        os.replace(tmp, DEST)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        print(f"fetch_superchargers failed: {exc}", file=sys.stderr)
        return 1
    print(f"fetch_superchargers: updated {DEST} ({DEST.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
