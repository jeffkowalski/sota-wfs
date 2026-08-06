#!/usr/bin/env python3
"""Download the SOTA summits list; validate; atomically swap into place.

Run daily (systemd timer). On any failure the previous file is left untouched
and the process exits non-zero so the failure lands in the journal.
"""

import os
import sys
import urllib.request
from pathlib import Path

SOTA_URL = "https://storage.sota.org.uk/summitslist.csv"
# Keep in sync with sota_wfs.registry.DATA_DIR (standalone script, no import).
DATA_DIR = Path(
    os.environ.get("SOTA_WFS_DATA")
    or Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    / "sota-wfs"
)
DEST = DATA_DIR / "summitslist.csv"

EXPECTED_HEADER = (
    "SummitCode,AssociationName,RegionName,SummitName,AltM,AltFt,GridRef1,GridRef2,"
    "Longitude,Latitude,Points,BonusPoints,ValidFrom,ValidTo,ActivationCount,"
    "ActivationDate,ActivationCall"
)
MIN_ROWS = 100_000


def validate(path: Path) -> None:
    with open(path, encoding="utf-8-sig") as f:
        title = f.readline()
        if not title.startswith("SOTA Summits List"):
            raise ValueError(f"Unexpected first line: {title[:80]!r}")
        header = f.readline().strip()
        if header != EXPECTED_HEADER:
            raise ValueError(f"Unexpected header: {header[:120]!r}")
        rows = sum(1 for _ in f)
    if rows < MIN_ROWS:
        raise ValueError(f"Only {rows} data rows (expected > {MIN_ROWS})")


def main() -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DEST.with_suffix(".csv.tmp")
    try:
        with urllib.request.urlopen(SOTA_URL, timeout=120) as resp, open(tmp, "wb") as out:
            while chunk := resp.read(1 << 20):
                out.write(chunk)
        validate(tmp)
        os.replace(tmp, DEST)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        print(f"fetch_sota failed: {exc}", file=sys.stderr)
        return 1
    print(f"fetch_sota: updated {DEST} ({DEST.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
