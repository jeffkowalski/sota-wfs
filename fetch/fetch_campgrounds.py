#!/usr/bin/env python3
"""Download the recreation.gov RIDB bulk CSV export (~250 MB zip, refreshed
daily, no API key), roll it up to one point per campground, and atomically
swap the resulting GeoJSON into place.

Run weekly (systemd timer). Set RIDB_ZIP to a local copy of the export to
skip the download (handy for a manual re-roll).
"""

import json
import os
import sys
import urllib.request
from pathlib import Path

from sota_wfs.ridb import build_campgrounds

URL = "https://ridb.recreation.gov/downloads/RIDBFullExport_V1_CSV.zip"
# Keep in sync with sota_wfs.registry.DATA_DIR (standalone script).
DATA_DIR = Path(
    os.environ.get("SOTA_WFS_DATA")
    or Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    / "sota-wfs"
)
DEST = DATA_DIR / "campgrounds.geojson"

MIN_FEATURES = 1000


def download(dest: Path) -> None:
    with urllib.request.urlopen(URL, timeout=600) as resp, open(dest, "wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = Path(os.environ.get("RIDB_ZIP") or DATA_DIR / "ridb.zip.tmp")
    tmp = DEST.with_suffix(".geojson.tmp")
    try:
        if not os.environ.get("RIDB_ZIP"):
            download(zip_path)
        fc = build_campgrounds(zip_path)
        n = len(fc["features"])
        if n < MIN_FEATURES:
            raise ValueError(f"Only {n} campgrounds (expected > {MIN_FEATURES})")
        with open(tmp, "w") as f:
            json.dump(fc, f, separators=(",", ":"))
        os.replace(tmp, DEST)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        print(f"fetch_campgrounds failed: {exc}", file=sys.stderr)
        return 1
    finally:
        if not os.environ.get("RIDB_ZIP"):
            zip_path.unlink(missing_ok=True)
    print(f"fetch_campgrounds: updated {DEST} ({n} campgrounds, {DEST.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
