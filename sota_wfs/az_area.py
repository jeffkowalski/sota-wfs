"""Rank cached activation zones by area.

Usage: python -m sota_wfs.az_area W --top 3

Scans the data/az/ cache for ok entries whose SummitCode matches the given
association prefixes and reports the largest AZ rings. Rings are at most a
few km across, so a local equirectangular projection plus the shoelace
formula is accurate to well under 0.1%.
"""

from __future__ import annotations

import argparse
import json
import math
import sys

from . import az

M_PER_DEG_LAT = 111132.0


def ring_area_m2(ring: list) -> float:
    lat0 = math.radians(sum(y for _, y in ring) / len(ring))
    m_per_deg_lon = M_PER_DEG_LAT * math.cos(lat0)
    area = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        area += (x1 * m_per_deg_lon) * (y2 * M_PER_DEG_LAT) - (
            x2 * m_per_deg_lon
        ) * (y1 * M_PER_DEG_LAT)
    return abs(area) / 2.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("prefixes", nargs="+", help="association prefixes, e.g. W or W6")
    ap.add_argument("--top", type=int, default=3)
    args = ap.parse_args()

    results = []
    skipped = 0
    for path in sorted(az._cache_dir().glob("*.json")):
        ref = path.stem.replace("_", "/", 1)
        if not ref.split("/")[0].startswith(tuple(args.prefixes)):
            continue
        try:
            with open(path) as f:
                entry = json.load(f)
        except (OSError, ValueError):
            skipped += 1  # e.g. entry being written by a concurrent bulk run
            continue
        if entry.get("ok") and entry.get("ring"):
            results.append((ring_area_m2(entry["ring"]), ref))
        else:
            skipped += 1

    results.sort(reverse=True)
    print(f"az_area: {len(results)} rings compared ({skipped} skipped)")
    for area, ref in results[: args.top]:
        print(f"{ref}: {area / 1e6:.3f} km^2")
    return 0


if __name__ == "__main__":
    sys.exit(main())
