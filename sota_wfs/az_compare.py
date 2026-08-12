"""Compare cached activation zones against SOTL.as (AD8IS) AZ loops.

Usage: python -m sota_wfs.az_compare W --workers 8
       python -m sota_wfs.az_compare W --report

SOTL.as serves per-summit AZ polygons computed from 1 m USGS 3DEP imagery at
https://az.sotl.as/{ASSOC}/{REGION}/{NUM}.geojson (missing summits 403). For
every cached entry we fetch theirs, rasterize both shapes on a shared metric
grid and record IoU, area ratio and the mean boundary offset in metres
(symmetric-difference area / mean perimeter — a scale-free epsilon; IoU alone
punishes small summits for boundary jitter that 1 m vs 10 m DEMs guarantee).

Results append to DATA_DIR/az_compare/results.jsonl, one JSON line per
summit; reruns skip summits already present, so recategorizing or rebuilding
the report never refetches. --report renders report.md next to the results.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from affine import Affine
from rasterio import features

from . import az, registry
from .az_area import ring_area_m2

SOTLAS = "https://az.sotl.as/{}.geojson"
MAX_GRID = 3000          # cells along the longer bbox axis
MIN_CELL_M = 2.0

# category rules, tried in order (offset metres, min IoU)
CATEGORIES = [
    ("EQUIVALENT", 10.0, 0.5),
    ("CLOSE", 25.0, 0.3),
    ("DISCREPANT", 100.0, 0.0),
]


def _out_dir():
    d = registry.DATA_DIR / "az_compare"
    d.mkdir(parents=True, exist_ok=True)
    return d


def fetch_theirs(ref: str):
    """Return (polygons, properties) from SOTL.as, or (None, None) if absent."""
    url = SOTLAS.format(ref.replace("-", "/"))
    for attempt in (0, 1):
        try:
            with urllib.request.urlopen(url, timeout=90) as r:
                d = json.load(r)
            break
        except urllib.error.HTTPError as exc:
            if exc.code in (403, 404):
                return None, None
            if attempt:
                raise
        except Exception:
            if attempt:
                raise
            time.sleep(2)
    feat = d["features"][0]
    geom = feat["geometry"]
    polys = ([geom["coordinates"]] if geom["type"] == "Polygon"
             else list(geom["coordinates"]))
    return polys, feat.get("properties", {})


def overlap(mine: list, theirs: list) -> dict:
    """Rasterize both polygon lists on a shared metric grid; overlap metrics."""
    pts = [p for poly in (mine + theirs) for ring in poly for p in ring]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    lat0 = (min(ys) + max(ys)) / 2
    mlat = 111320.0
    mlon = 111320.0 * math.cos(math.radians(lat0))
    span = max((max(xs) - min(xs)) * mlon, (max(ys) - min(ys)) * mlat)
    cell = max(MIN_CELL_M, span / MAX_GRID)
    dx, dy, pad = cell / mlon, cell / mlat, 3
    w = int((max(xs) - min(xs)) / dx) + 2 * pad
    h = int((max(ys) - min(ys)) / dy) + 2 * pad
    tr = Affine(dx, 0, min(xs) - pad * dx, 0, -dy, max(ys) + pad * dy)

    def mask(polys):
        shapes = [{"type": "Polygon", "coordinates": p} for p in polys]
        return features.rasterize(
            shapes, out_shape=(h, w), transform=tr).astype(bool)

    a, b = mask(mine), mask(theirs)
    inter = int((a & b).sum())
    union = int((a | b).sum())
    sym = union - inter
    perim = sum(_perim_m(r, mlat, mlon) for poly in (mine + theirs)
                for r in poly) / 2
    return {
        "iou": inter / union if union else 0.0,
        "our_km2": float(a.sum()) * cell * cell / 1e6,
        "their_km2": float(b.sum()) * cell * cell / 1e6,
        "ratio": float(a.sum()) / max(float(b.sum()), 1.0),
        "offset_m": sym * cell * cell / perim if perim else math.inf,
        "cell_m": cell,
    }


def _perim_m(ring, mlat, mlon) -> float:
    return sum(math.hypot((x2 - x1) * mlon, (y2 - y1) * mlat)
               for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]))


def categorize(row: dict) -> str:
    if row["status"] != "compared":
        return row["status"].upper()
    if row["iou"] <= 0 or not (1 / 3 <= row["ratio"] <= 3):
        return "MAJOR"
    for name, max_off, min_iou in CATEGORIES:
        if row["offset_m"] <= max_off and row["iou"] >= min_iou:
            return name
    return "MAJOR"


def compare_one(ref: str, entry: dict) -> dict:
    row = {"code": ref}
    try:
        theirs, props = fetch_theirs(ref)
    except Exception as exc:
        row.update(status="error", error=repr(exc))
        row["category"] = categorize(row)
        return row
    if props is not None:
        row.update(
            their_reported_km2=props.get("areaM2", 0) / 1e6,
            elev_diff=props.get("elevationDifferenceM"),
            seed_dist=props.get("seedDistanceM"),
            edge=bool(props.get("touchesRasterEdge")),
        )
    if not entry.get("ok"):
        row["status"] = "ours_failed" if theirs is not None else "both_failed"
    elif theirs is None:
        row["status"] = "theirs_missing"
        row["our_km2"] = ring_area_m2(entry["ring"]) / 1e6
    else:
        row["status"] = "compared"
        row["holes"] = sum(len(p) - 1 for p in theirs)
        row.update(overlap([[entry["ring"]]], theirs))
    row["category"] = categorize(row)
    return row


def cached_entries(prefixes) -> list[tuple[str, dict]]:
    out = []
    for path in sorted(az._cache_dir().glob("*.json")):
        ref = path.stem.replace("_", "/", 1)
        parts = ref.split("/")
        if len(parts) != 2 or "-" not in parts[1]:
            continue                       # e.g. opentopo_budget.json
        if not parts[0].startswith(tuple(prefixes)):
            continue
        try:
            with open(path) as f:
                out.append((ref, json.load(f)))
        except (OSError, ValueError):
            continue
    return out


def run(prefixes, workers: int, limit: int | None) -> int:
    results_path = _out_dir() / "results.jsonl"
    done_codes = set()
    if results_path.exists():
        with open(results_path) as f:
            done_codes = {json.loads(line)["code"] for line in f if line.strip()}
    todo = [(r, e) for r, e in cached_entries(prefixes) if r not in done_codes]
    if limit:
        todo = todo[:limit]
    total = len(todo)
    print(f"az_compare: {total} summits to compare "
          f"({len(done_codes)} already done)", flush=True)

    lock = threading.Lock()
    out = open(results_path, "a")
    done = 0
    t0 = time.time()

    def work(item):
        nonlocal done
        ref, entry = item
        try:
            row = compare_one(ref, entry)
        except Exception as exc:                 # keep the run alive
            row = {"code": ref, "status": "error", "error": repr(exc),
                   "category": "ERROR"}
        with lock:
            out.write(json.dumps(row) + "\n")
            out.flush()
            done += 1
            n = done
        if n % 500 == 0 or n == total:
            rate = n / (time.time() - t0)
            eta = (total - n) / rate if rate else 0
            print(f"az_compare: {n}/{total} {rate:.1f}/s "
                  f"eta {eta / 60:.0f}m", flush=True)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(work, todo))
    out.close()
    print(f"az_compare: finished {done} in {(time.time() - t0) / 60:.1f}m",
          flush=True)
    return 0


def _load_results(prefixes) -> list[dict]:
    rows, seen = [], set()
    with open(_out_dir() / "results.jsonl") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            if row["code"] in seen:
                continue
            seen.add(row["code"])
            if row["code"].split("/")[0].startswith(tuple(prefixes)):
                rows.append(row)
    return rows


def _hist(values, edges) -> list[str]:
    lines = []
    for lo, hi in zip(edges, edges[1:] + [math.inf]):
        n = sum(1 for v in values if lo <= v < hi)
        label = f"{lo:g}-{hi:g} m" if hi != math.inf else f">{lo:g} m"
        lines.append(f"  {label:>10}  {n:6d}  {'#' * min(60, n * 60 // max(1, len(values)))}")
    return lines


def _fmt_row(r: dict) -> str:
    link = f"[{r['code']}](https://sotl.as/summits/{r['code']})"
    flags = []
    if r.get("edge"):
        flags.append("edge")
    if r.get("elev_diff") is not None and abs(r["elev_diff"]) > 10:
        flags.append(f"elevΔ{r['elev_diff']:+.0f}m")
    if r.get("seed_dist"):
        flags.append(f"seed{r['seed_dist']:.0f}m")
    return (f"| {link} | {r['offset_m']:.0f} | {r['iou']:.2f} "
            f"| {r['our_km2']:.4f} | {r['their_km2']:.4f} "
            f"| {r['ratio']:.2f} | {' '.join(flags)} |")


def report(prefixes) -> int:
    rows = _load_results(prefixes)
    cats = {}
    for r in rows:
        cats.setdefault(r["category"], []).append(r)
    compared = [r for r in rows if r["status"] == "compared"]

    md = ["# AZ comparison: sota-wfs vs SOTL.as (az.sotl.as)", ""]
    md.append(f"{len(rows)} summits examined; {len(compared)} compared "
              "shape-to-shape.")
    md.append("")
    md.append("Metric: mean boundary offset = symmetric-difference area / "
              "mean perimeter (metres); IoU = intersection over union. "
              "Ours: 10 m 3DEP 1/3\"; theirs: 1 m 3DEP ImageServer.")
    md.append("")

    md.append("## Categories")
    md.append("")
    md.append("| Category | Count | Share |")
    md.append("|---|---|---|")
    order = ["EQUIVALENT", "CLOSE", "DISCREPANT", "MAJOR", "OURS_FAILED",
             "BOTH_FAILED", "THEIRS_MISSING", "ERROR"]
    for c in order + sorted(set(cats) - set(order)):
        if c in cats:
            md.append(f"| {c} | {len(cats[c])} | "
                      f"{len(cats[c]) / len(rows):.1%} |")
    md.append("")

    if compared:
        offs = sorted(r["offset_m"] for r in compared)
        pct = {p: offs[min(len(offs) - 1, int(p / 100 * len(offs)))]
               for p in (50, 90, 99)}
        md.append("## Boundary offset distribution (compared summits)")
        md.append("")
        md.append(f"median {pct[50]:.1f} m, p90 {pct[90]:.1f} m, "
                  f"p99 {pct[99]:.1f} m")
        md.append("")
        md.append("```")
        md.extend(_hist(offs, [0, 2, 5, 10, 25, 50, 100, 250]))
        md.append("```")
        md.append("")

    md.append("## Per-association breakdown")
    md.append("")
    md.append("| Assoc | Total | EQUIV | CLOSE | DISCR | MAJOR | other |")
    md.append("|---|---|---|---|---|---|---|")
    by_assoc = {}
    for r in rows:
        by_assoc.setdefault(r["code"].split("/")[0], []).append(r)
    for a in sorted(by_assoc):
        rs = by_assoc[a]
        n = {c: sum(1 for r in rs if r["category"] == c) for c in order[:4]}
        other = len(rs) - sum(n.values())
        md.append(f"| {a} | {len(rs)} | {n['EQUIVALENT']} | {n['CLOSE']} | "
                  f"{n['DISCREPANT']} | {n['MAJOR']} | {other} |")
    md.append("")

    hdr = ("| Summit | offset m | IoU | ours km² | theirs km² | ratio "
           "| flags |")
    sep = "|---|---|---|---|---|---|---|"
    for title, sel in (
        ("## MAJOR discrepancies",
         sorted(cats.get("MAJOR", []), key=lambda r: -r["offset_m"])),
        ("## Worst 50 DISCREPANT",
         sorted(cats.get("DISCREPANT", []),
                key=lambda r: -r["offset_m"])[:50]),
    ):
        md.append(title)
        md.append("")
        if sel:
            md.extend([hdr, sep])
            md.extend(_fmt_row(r) for r in sel)
        else:
            md.append("(none)")
        md.append("")

    for title, key in (("## We failed, SOTL.as has a zone", "OURS_FAILED"),
                       ("## Both failed", "BOTH_FAILED"),
                       ("## SOTL.as missing (coverage note)",
                        "THEIRS_MISSING"),
                       ("## Errors", "ERROR")):
        rs = cats.get(key, [])
        md.append(title)
        md.append("")
        if key == "THEIRS_MISSING":
            md.append(f"{len(rs)} summits we computed that az.sotl.as "
                      "does not serve.")
            by_a = {}
            for r in rs:
                by_a.setdefault(r["code"].split("/")[0], []).append(r)
            for a in sorted(by_a):
                codes = [r["code"] for r in by_a[a]]
                shown = ", ".join(codes[:12])
                more = f" … +{len(codes) - 12}" if len(codes) > 12 else ""
                md.append(f"- {a} ({len(codes)}): {shown}{more}")
        elif rs:
            for r in rs:
                extra = (f" — their area {r['their_reported_km2']:.4f} km², "
                         f"elevΔ {r.get('elev_diff')} m"
                         if r.get("their_reported_km2") is not None else "")
                md.append(f"- {r['code']}{extra}{' — ' + r['error'] if r.get('error') else ''}")
        else:
            md.append("(none)")
        md.append("")

    bad = [r for r in compared if r.get("their_reported_km2")
           and abs(r["their_km2"] / r["their_reported_km2"] - 1) > 0.05]
    md.append("## Internal consistency (my rasterization of their shape "
              "vs their reported areaM2, >5% off)")
    md.append("")
    if bad:
        for r in sorted(bad, key=lambda r: -abs(
                r["their_km2"] / r["their_reported_km2"] - 1))[:20]:
            md.append(f"- {r['code']}: rasterized {r['their_km2']:.4f} vs "
                      f"reported {r['their_reported_km2']:.4f} km²")
    else:
        md.append("(none — rasterization agrees with their own areas)")
    md.append("")

    path = _out_dir() / "report.md"
    path.write_text("\n".join(md))
    print(f"az_compare: report written to {path}")
    for c in order:
        if c in cats:
            print(f"  {c}: {len(cats[c])}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("prefixes", nargs="*", default=["W"],
                    help="association prefixes (default: W)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--report", action="store_true",
                    help="render report.md from existing results")
    args = ap.parse_args()
    prefixes = tuple(args.prefixes or ["W"])
    if args.report:
        return report(prefixes)
    return run(prefixes, args.workers, args.limit)


if __name__ == "__main__":
    sys.exit(main())
