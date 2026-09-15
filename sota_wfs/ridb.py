"""Roll the RIDB (recreation.gov) bulk CSV export up into one GeoJSON point
per place to camp: every "Campground" facility, plus "Facility"-typed records
that list camping (dispersed / informal sites), with per-site attributes
summarized at campground level."""

from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path

import pandas as pd

CAMPGROUND_URL = "https://www.recreation.gov/camping/campgrounds/"
RECAREA_URL = "https://www.recreation.gov/gateways/"

# "Facility"-typed RIDB records that list CAMPING are a grab bag: dispersed
# sites and small recreation sites worth a marker, but also ranger districts,
# offices, marinas, resorts, whole parks/forests, highway corridors and
# town-named records. Drop the administrative ones by name.
_ADMIN_NAME_RE = re.compile(
    r"ranger district|\bdistrict\b|\bzone\b|corridor|greenway|\boffice\b|headquarters"
    r"|visitor center|national forest\b|state park\b|\bunit\b|management area"
    r"|marina|resort|lodge|\bhotel\b|,\s*[A-Z]{2}\s*$",
    re.I,
)


def is_admin_facility(name: str) -> bool:
    return bool(_ADMIN_NAME_RE.search(name.strip()))
SECTION_MAX = 600  # chars per served text section (overview, facilities)

_BLOCK_TAG_RE = re.compile(r"</?(?:p|br|div|li|ul|ol|h\d|tr|td|th|table)\b[^>]*>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _plain_text(raw: str) -> str:
    """Strip HTML: block tags become a space, inline tags vanish, entities
    are decoded, whitespace collapses."""
    text = _TAG_RE.sub("", _BLOCK_TAG_RE.sub(" ", raw))
    return _WS_RE.sub(" ", html.unescape(text)).strip()


_H2_RE = re.compile(r"<h[23][^>]*>(.*?)</h[23]>", re.I | re.S)


def _sections(raw_html: str) -> dict[str, str]:
    """Split a facility description on its h2/h3 headings. Most RIDB
    descriptions are 'Overview / Facilities / Recreation / Natural Features /
    Nearby Attractions' sections; a description with no headings comes back
    entirely under 'overview'."""
    parts = _H2_RE.split(raw_html)
    out: dict[str, str] = {}
    # A few descriptions label the lead with a bold "Overview:" instead of a heading.
    lead = re.sub(r"^Overview:?\s*", "", _plain_text(parts[0]))
    if lead:
        out["overview"] = lead
    for heading, body in zip(parts[1::2], parts[2::2]):
        key = _plain_text(heading).rstrip(":").lower()
        text = _plain_text(body)
        if key and text and key not in out:
            out[key] = text
    return out


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut + "…"


_SMALL_WORDS = {"a", "an", "and", "at", "by", "de", "for", "in", "la", "of", "on", "or", "the", "to"}
_KEEP_UPPER = {"RV", "OHV", "ATV", "NF", "NRA", "BLM", "USFS", "SP", "HQ", "TH"}
_WORD_START_RE = re.compile(r"(^|[\s\-/(])([a-z])")


def title_case(name: str) -> str:
    """RIDB facility names are a mix of ALL CAPS and proper case. Title-case
    the all-caps ones ('LAKE OF THE WOODS RV PARK' -> 'Lake of the Woods RV
    Park'); leave anything with lowercase letters alone so 'McKinney' and
    'NF' survive."""
    name = name.strip()
    if re.search(r"[a-z]", name):
        return name
    words = []
    for i, word in enumerate(name.split(" ")):
        if word in _KEEP_UPPER or re.fullmatch(r"\([A-Z]{2}\)", word):
            words.append(word)
            continue
        lower = word.lower()
        if i > 0 and lower in _SMALL_WORDS:
            words.append(lower)
            continue
        words.append(_WORD_START_RE.sub(lambda m: m.group(1) + m.group(2).upper(), lower))
    return " ".join(words)


def _camping_facility_ids(entity_acts: pd.DataFrame, acts: pd.DataFrame) -> set[str]:
    """FacilityIDs of 'Facility'-typed records whose activities include CAMPING."""
    camping = acts.loc[acts["ActivityName"].str.strip().str.upper() == "CAMPING", "ActivityID"]
    ea = entity_acts[(entity_acts["EntityType"] == "Facility") & entity_acts["ActivityID"].isin(camping)]
    return set(ea["EntityID"])


def _activities(entity_acts: pd.DataFrame, acts: pd.DataFrame) -> pd.Series:
    """Alphabetical activity list per facility, keyed by facility id.
    Campground records carry EntityType=Campground, camping areas Facility."""
    ea = entity_acts[entity_acts["EntityType"].isin(["Campground", "Facility"])].copy()
    ea["fid"] = pd.to_numeric(ea["EntityID"], errors="coerce")
    ea = ea.merge(acts[["ActivityID", "ActivityName"]], on="ActivityID")
    ea["name"] = ea["ActivityName"].str.strip().str.capitalize()
    return ea.groupby("fid")["name"].agg(lambda v: ", ".join(sorted(set(v))))


def _yes_no(raw: str) -> str:
    return "Yes" if raw.strip().lower() == "true" else "No"


def _counted(series: pd.Series) -> str:
    """'Value N' pairs, most frequent first, ties alphabetical."""
    counts = series.value_counts()
    counts = counts.sort_index().sort_values(ascending=False, kind="stable")
    return ", ".join(f"{name} {n}" for name, n in counts.items())


def _ordered(series: pd.Series) -> str:
    """Distinct values, most frequent first, ties alphabetical."""
    counts = series[series != ""].value_counts()
    counts = counts.sort_index().sort_values(ascending=False, kind="stable")
    return ", ".join(counts.index)


def _site_type(raw: str) -> str:
    return raw.strip().capitalize()


def _access(raw: str) -> str:
    """Normalize 'Drive In' / 'Drive-In' / 'drive in' to 'Drive-in'; N/A -> ''."""
    words = raw.strip().replace("-", " ").split()
    if not words or words[0].lower() in ("n/a", "na", "none"):
        return ""
    return "-".join(w.lower() for w in words).capitalize()


def _truthy(raw: str) -> bool:
    """RIDB yes/no attributes are free text: Yes, yes, Y, No, NO, 'Pets
    Allowed', 'Domestic,Horse' ... anything not starting with 'n' is a yes."""
    return not raw.strip().lower().startswith("n")


def _any_yes(values: pd.Series) -> str:
    flags = values.map(_truthy)
    if flags.empty:
        return ""
    return "Yes" if flags.any() else "No"


def _amps(values: pd.Series) -> str:
    """Electricity Hookup values like '50', '30/50', '20/30/50' -> '30/50'."""
    amps: set[int] = set()
    for raw in values:
        for part in raw.split("/"):
            part = part.strip()
            if part.isdigit() and int(part) > 0:
                amps.add(int(part))
    return "/".join(str(a) for a in sorted(amps))


def _int_or_none(v):
    return None if v is None or pd.isna(v) else int(v)


def _max_len(values: pd.Series):
    nums = pd.to_numeric(values, errors="coerce")
    nums = nums[nums > 0]
    return None if nums.empty else int(nums.max())


def _site_rollup(sites: pd.DataFrame, attrs: pd.DataFrame) -> dict[int, dict]:
    """Per-campground summary of its campsites, keyed by facility id."""
    attrs = attrs[attrs["EntityType"] == "Campsite"].merge(
        sites[["CampsiteID", "fid"]], left_on="EntityID", right_on="CampsiteID"
    )

    def per_fid(name: str, agg):
        sub = attrs[attrs["AttributeName"] == name]
        return sub.groupby("fid")["AttributeValue"].agg(agg)

    access = per_fid("Site Access", lambda v: _ordered(v.map(_access)))
    pets = per_fid("Pets Allowed", _any_yes)
    fires = per_fid("Campfire Allowed", _any_yes)
    amps = per_fid("Electricity Hookup", _amps)
    water = per_fid("Water Hookup", _any_yes)
    sewer = per_fid("Sewer Hookup", _any_yes)
    max_len = per_fid("Max Vehicle Length", _max_len)

    out: dict[int, dict] = {}
    for fid, group in sites.groupby("fid"):
        hookups = []
        if amps.get(fid):
            hookups.append(f"Electric {amps[fid]}A")
        if water.get(fid) == "Yes":
            hookups.append("Water")
        if sewer.get(fid) == "Yes":
            hookups.append("Sewer")
        out[fid] = {
            "sites": int(len(group)),
            "site_types": _counted(group["CampsiteType"].map(_site_type)),
            "access": access.get(fid, ""),
            "hookups": ", ".join(hookups),
            "max_vehicle_len": _int_or_none(max_len.get(fid)),
            # Named so CalTopo's proxy (a Java HashMap, capacity 32) iterates
            # them last: popup order follows that hash order, not ours.
            "allows_pets": pets.get(fid, ""),
            "allows_fires": fires.get(fid, ""),
        }
    return out


def _read_csv(z: zipfile.ZipFile, name: str) -> pd.DataFrame:
    with z.open(name) as f:
        return pd.read_csv(f, dtype=str, keep_default_na=False, low_memory=False)


def build_campgrounds(zip_path: Path) -> dict:
    with zipfile.ZipFile(zip_path) as z:
        fac = _read_csv(z, "Facilities_API_v1.csv")
        sites = _read_csv(z, "Campsites_API_v1.csv")
        attrs = _read_csv(z, "CampsiteAttributes_API_v1.csv")
        acts = _read_csv(z, "Activities_API_v1.csv")
        entity_acts = _read_csv(z, "EntityActivities_API_v1.csv")
    camping_ids = _camping_facility_ids(entity_acts, acts)
    is_cg = fac["FacilityTypeDescription"] == "Campground"
    is_area = (
        (fac["FacilityTypeDescription"] == "Facility")
        & fac["FacilityID"].isin(camping_ids)
        & ~fac["FacilityName"].map(is_admin_facility)
    )
    fac = fac[is_cg | is_area].copy()
    fac["kind"] = is_cg.loc[fac.index].map({True: "Campground", False: "Camping area"})
    fac["id"] = pd.to_numeric(fac["FacilityID"], errors="coerce")
    fac["lon"] = pd.to_numeric(fac["FacilityLongitude"], errors="coerce")
    fac["lat"] = pd.to_numeric(fac["FacilityLatitude"], errors="coerce")
    fac = fac.dropna(subset=["id", "lon", "lat"])
    fac = fac[(fac["lon"] != 0) & (fac["lat"] != 0)]
    fac["title"] = fac["FacilityName"].map(title_case)
    fac = fac[fac["title"] != ""]  # a handful of blank-named, siteless stubs
    sites["fid"] = pd.to_numeric(sites["FacilityID"], errors="coerce")
    rollup = _site_rollup(sites, attrs)
    activities = _activities(entity_acts, acts)
    empty = {
        "sites": 0, "site_types": "", "access": "", "hookups": "",
        "max_vehicle_len": None, "allows_pets": "", "allows_fires": "",
    }
    features = []
    for rec in fac.to_dict("records"):
        fid = int(rec["id"])
        if rec["kind"] == "Campground":
            url = f"{CAMPGROUND_URL}{fid}"
        else:  # no campground page; link the parent rec area (forest/park) page
            parent = rec["ParentRecAreaID"].strip()
            url = f"{RECAREA_URL}{parent}" if parent else ""
        props = {
            "title": rec["title"],
            "id": fid,
            "kind": rec["kind"],
            # Named for CalTopo's hash-ordered popup: "rec_gov" lands in the
            # first bucket, ahead of the server-added "GeoJSON" download link.
            "rec_gov": url,
            "reservable": _yes_no(rec["Reservable"]),
        }
        roll = rollup.get(fid, empty)
        sections = _sections(rec["FacilityDescription"])
        # Popup order (CalTopo lists properties as served): amenities first,
        # long text next, then the yes/no flags and admin details.
        for key in ("sites", "site_types", "access", "hookups", "max_vehicle_len"):
            props[key] = roll[key]
        props["stay_limit"] = _plain_text(rec["StayLimit"])
        props["activities"] = activities.get(fid, "")
        props["overview"] = _truncate(sections.get("overview", ""), SECTION_MAX)
        props["facilities"] = _truncate(sections.get("facilities", ""), SECTION_MAX)
        props["allows_pets"] = roll["allows_pets"]
        props["allows_fires"] = roll["allows_fires"]
        props["fees"] = _plain_text(rec["FacilityUseFeeDescription"])
        props["phone"] = rec["FacilityPhone"].strip()
        features.append(
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [rec["lon"], rec["lat"]]},
                "properties": props,
            }
        )
    return {"type": "FeatureCollection", "features": features}
