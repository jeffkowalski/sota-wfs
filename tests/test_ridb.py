import io
import textwrap
import zipfile

import pytest

from sota_wfs.ridb import build_campgrounds, title_case

FACILITIES = textwrap.dedent(
    """\
    FacilityID,LegacyFacilityID,OrgFacilityID,ParentOrgID,ParentRecAreaID,FacilityName,FacilityDescription,FacilityTypeDescription,FacilityUseFeeDescription,FacilityDirections,FacilityPhone,FacilityEmail,FacilityReservationURL,FacilityMapURL,FacilityAdaAccess,FacilityAccessibilityText,FacilityLongitude,FacilityLatitude,Keywords,StayLimit,Reservable,Enabled,LastUpdatedDate
    232447,,,,2991,UPPER PINES,"<p>Upper Pines is <b>open year-round</b>.</p>
    <p>Vault toilets and drinking water.</p>",Campground,"<p>$36 per night</p>",Drive in,209-372-0200,,,,,,-119.5650,37.7376,"yosemite, camping",14 days,true,true,2026-01-01
    10000167,,126,,,Tail Race Campground,Three campsites.,Campground,,,307-828-4500,,,,,,0,0,,,false,true,2026-02-25
    0377bfa0-e64c-11ea-b327-2e4b29211486,,,,,Valley Visitor Center,Info.,Visitor Center,,,,,,,,,-119.5,37.7,,,false,true,2026-01-01
    249926,,,,1077,Lake of the Woods,<p>A nice small lake with 15 camping spots.</p>,Facility,,,,,,,,,-120.391158,39.502664,,,false,true,2026-01-01
    300001,,,,1077,Truckee Ranger District,Office.,Facility,,,,,,,,,-120.2,39.3,,,false,true,2026-01-01
    300002,,,,1077,Bear Valley,No camping here.,Facility,,,,,,,,,-120.3,39.4,,,false,true,2026-01-01
    """
)

CAMPSITES = textwrap.dedent(
    """\
    CampsiteID,FacilityID,CampsiteName,CampsiteType,TypeOfUse,Loop,CampsiteAccessible,CampsiteLongitude,CampsiteLatitude,CreatedDate,LastUpdatedDate
    100,232447,044,STANDARD NONELECTRIC,Overnight,Upper Pines,false,-119.56504,37.737625,2014-05-02,2025-12-03
    101,232447,045,STANDARD NONELECTRIC,Overnight,Upper Pines,false,-119.56510,37.737700,2014-05-02,2025-12-03
    102,232447,046,TENT ONLY NONELECTRIC,Overnight,Upper Pines,false,-119.56520,37.737800,2014-05-02,2025-12-03
    200,10000167,001,STANDARD NONELECTRIC,Overnight,,false,0,0,2014-05-02,2025-12-03
    """
)

ATTRIBUTES = textwrap.dedent(
    """\
    AttributeID,AttributeName,AttributeValue,EntityID,EntityType
    1,Pets Allowed,Yes,100,Campsite
    1,Pets Allowed,Yes,101,Campsite
    2,Campfire Allowed,No,100,Campsite
    3,Site Access,Drive-In,100,Campsite
    3,Site Access,Drive In,101,Campsite
    3,Site Access,Walk-In,102,Campsite
    3,Site Access,N/A,101,Campsite
    4,Electricity Hookup,30,100,Campsite
    5,Water Hookup,Yes,100,Campsite
    6,Max Vehicle Length,35,100,Campsite
    6,Max Vehicle Length,40,101,Campsite
    6,Max Vehicle Length,0,102,Campsite
    """
)

ACTIVITIES = textwrap.dedent(
    """\
    ActivityID,ActivityParentID,ActivityName,ActivityLevel
    5,,CAMPING,1
    14,,HIKING,1
    """
)

ENTITY_ACTIVITIES = textwrap.dedent(
    """\
    ActivityID,ActivityDescription,ActivityFeeDescription,EntityID,EntityType
    5,,,232447,Campground
    14,,,232447,Campground
    14,,,2991,Rec Area
    5,,,249926,Facility
    5,,,300001,Facility
    """
)


@pytest.fixture()
def ridb_zip(tmp_path):
    path = tmp_path / "ridb.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Facilities_API_v1.csv", FACILITIES)
        z.writestr("Campsites_API_v1.csv", CAMPSITES)
        z.writestr("CampsiteAttributes_API_v1.csv", ATTRIBUTES)
        z.writestr("Activities_API_v1.csv", ACTIVITIES)
        z.writestr("EntityActivities_API_v1.csv", ENTITY_ACTIVITIES)
    return path


def test_build_campgrounds_basic_feature(ridb_zip):
    fc = build_campgrounds(ridb_zip)
    assert fc["type"] == "FeatureCollection"
    feat = fc["features"][0]
    assert feat["geometry"] == {"type": "Point", "coordinates": [-119.565, 37.7376]}
    props = feat["properties"]
    assert props["title"] == "Upper Pines"
    assert props["id"] == 232447
    assert props["rec_gov"] == "https://www.recreation.gov/camping/campgrounds/232447"


def test_build_campgrounds_skips_non_campgrounds_and_missing_coords(ridb_zip):
    fc = build_campgrounds(ridb_zip)
    assert [f["properties"]["title"] for f in fc["features"]] == ["Upper Pines", "Lake of the Woods"]


def test_build_campgrounds_includes_facilities_that_list_camping(ridb_zip):
    # "Facility"-typed records with the CAMPING activity are dispersed / informal
    # sites: no campground page, so link to the parent rec area instead.
    by_id = {f["properties"]["id"]: f["properties"] for f in build_campgrounds(ridb_zip)["features"]}
    assert by_id[232447]["kind"] == "Campground"
    lake = by_id[249926]
    assert lake["kind"] == "Camping area"
    assert lake["reservable"] == "No"
    assert lake["rec_gov"] == "https://www.recreation.gov/gateways/1077"
    assert lake["overview"] == "A nice small lake with 15 camping spots."
    assert lake["sites"] == 0
    assert lake["activities"] == "Camping"  # activity rows for these carry EntityType=Facility
    assert 300001 not in by_id  # ranger district: administrative, not a place to camp
    assert 300002 not in by_id  # no camping activity


@pytest.mark.parametrize(
    "name",
    [
        "Coyote Ranger District", "Carson Ranger District Office", "Elk Creek Marina",
        "Atlanta State Park", "Echo Canyon Resort and Marina", "Antonito, CO",
        "Interstate 90 Mountains to Sound Greenway", "Highway 108 Corridor",
        "Lakewood - Laona District", "Black Hills National Forest",
    ],
)
def test_is_admin_facility_name(name):
    from sota_wfs.ridb import is_admin_facility
    assert is_admin_facility(name)


@pytest.mark.parametrize(
    "name",
    ["Lake of the Woods", "Packsaddle Recreation Site", "Peninsula Dispersed Campground and Recreation Area",
     "North Table Rock Camping Area", "Garden Point Boat-in Campground", "Ford's Well"],
)
def test_is_not_admin_facility_name(name):
    from sota_wfs.ridb import is_admin_facility
    assert not is_admin_facility(name)


def test_build_campgrounds_site_rollup(ridb_zip):
    props = build_campgrounds(ridb_zip)["features"][0]["properties"]
    assert props["reservable"] == "Yes"
    assert props["sites"] == 3
    assert props["site_types"] == "Standard nonelectric 2, Tent only nonelectric 1"
    assert props["access"] == "Drive-in, Walk-in"


def test_build_campgrounds_amenity_rollup(ridb_zip):
    props = build_campgrounds(ridb_zip)["features"][0]["properties"]
    assert props["hookups"] == "Electric 30A, Water"
    assert props["allows_pets"] == "Yes"
    assert props["allows_fires"] == "No"
    assert props["max_vehicle_len"] == 40
    assert isinstance(props["max_vehicle_len"], int)


def test_build_campgrounds_max_vehicle_len_stays_int_when_others_missing(tmp_path):
    # A second campground with sites but no vehicle-length attribute must not
    # turn the whole column into floats (pandas NaN promotion).
    facilities = FACILITIES.replace(
        "Three campsites.,Campground,,,307-828-4500,,,,,,0,0", "Three campsites.,Campground,,,307-828-4500,,,,,,-110.06,42.02"
    )
    campsites = CAMPSITES.replace("200,10000167,001,STANDARD NONELECTRIC,Overnight,,false,0,0", "200,10000167,001,STANDARD NONELECTRIC,Overnight,,false,-110.06,42.02")
    path = tmp_path / "ridb.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Facilities_API_v1.csv", facilities)
        z.writestr("Campsites_API_v1.csv", campsites)
        z.writestr("CampsiteAttributes_API_v1.csv", ATTRIBUTES)
        z.writestr("Activities_API_v1.csv", ACTIVITIES)
        z.writestr("EntityActivities_API_v1.csv", ENTITY_ACTIVITIES)
    by_title = {f["properties"]["title"]: f["properties"] for f in build_campgrounds(path)["features"]}
    assert by_title["Upper Pines"]["max_vehicle_len"] == 40
    assert isinstance(by_title["Upper Pines"]["max_vehicle_len"], int)
    assert by_title["Tail Race Campground"]["max_vehicle_len"] is None


def test_build_campgrounds_text_fields(ridb_zip):
    props = build_campgrounds(ridb_zip)["features"][0]["properties"]
    assert props["activities"] == "Camping, Hiking"
    assert props["stay_limit"] == "14 days"
    assert props["fees"] == "$36 per night"
    # No section headings: the whole text is the overview, facilities is empty.
    assert props["overview"] == "Upper Pines is open year-round. Vault toilets and drinking water."
    assert props["facilities"] == ""
    assert "description" not in props
    assert props["phone"] == "209-372-0200"


SECTIONED = (
    "<h2>Overview</h2>\n<p>High View Park is on a long peninsula.  <br></p>\n"
    "<h2>Recreation</h2>\n<p>Bass fishermen seldom leave empty-handed.</p>\n"
    "<h2>Facilities</h2>\n<p>Campsites with water and 50-amp electric hookups.</p>"
    "<p>Vault toilets.</p>\n<h2>Nearby Attractions</h2>\n<p>Dallas.</p>"
)


def _zip_with_description(tmp_path, html):
    facilities = FACILITIES.replace(
        '"<p>Upper Pines is <b>open year-round</b>.</p>\n<p>Vault toilets and drinking water.</p>"',
        '"' + html + '"',
    )
    path = tmp_path / "ridb.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Facilities_API_v1.csv", facilities)
        z.writestr("Campsites_API_v1.csv", CAMPSITES)
        z.writestr("CampsiteAttributes_API_v1.csv", ATTRIBUTES)
        z.writestr("Activities_API_v1.csv", ACTIVITIES)
        z.writestr("EntityActivities_API_v1.csv", ENTITY_ACTIVITIES)
    return path


def test_build_campgrounds_splits_h2_sections(tmp_path):
    props = build_campgrounds(_zip_with_description(tmp_path, SECTIONED))["features"][0]["properties"]
    assert props["overview"] == "High View Park is on a long peninsula."
    assert props["facilities"] == "Campsites with water and 50-amp electric hookups. Vault toilets."


def test_build_campgrounds_truncates_long_sections(tmp_path):
    long_html = "<h2>Overview</h2><p>" + "word " * 200 + "</p><h2>Facilities</h2><p>" + "site " * 200 + "</p>"
    props = build_campgrounds(_zip_with_description(tmp_path, long_html))["features"][0]["properties"]
    for field in ("overview", "facilities"):
        assert len(props[field]) <= 600
        assert props[field].endswith("…")


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("UPPER PINES", "Upper Pines"),
        ("LOOKOUT CAMPGROUND AND BOAT LAUNCH - WILLAMETTE", "Lookout Campground and Boat Launch - Willamette"),
        ("HIDEOUT CANYON BOAT-IN CAMPGROUND", "Hideout Canyon Boat-In Campground"),
        ("BOUNDARY (ID)", "Boundary (ID)"),
        ("LAKE OF THE WOODS RV PARK", "Lake of the Woods RV Park"),
        ("Tail Race Campground", "Tail Race Campground"),  # mixed case left alone
        ("McKinney Creek NF", "McKinney Creek NF"),
    ],
)
def test_title_case_all_caps_names(raw, expected):
    assert title_case(raw) == expected


def test_build_campgrounds_drops_unnamed_facilities(tmp_path):
    facilities = FACILITIES + "10099999,,,,,   ,,Campground,,,,,,,,,-110.1,42.1,,,false,true,2026-01-01\n"
    path = tmp_path / "ridb.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Facilities_API_v1.csv", facilities)
        z.writestr("Campsites_API_v1.csv", CAMPSITES)
        z.writestr("CampsiteAttributes_API_v1.csv", ATTRIBUTES)
        z.writestr("Activities_API_v1.csv", ACTIVITIES)
        z.writestr("EntityActivities_API_v1.csv", ENTITY_ACTIVITIES)
    ids = [f["properties"]["id"] for f in build_campgrounds(path)["features"]]
    assert ids == [232447, 249926]


def test_build_campgrounds_strips_bold_overview_label(tmp_path):
    html = "<p><strong>Overview:&nbsp; </strong></p><p>Set within the beautiful canyon.</p>"
    props = build_campgrounds(_zip_with_description(tmp_path, html))["features"][0]["properties"]
    assert props["overview"] == "Set within the beautiful canyon."


def test_build_campgrounds_property_order(ridb_zip):
    # Popup order when a template omits PROPERTYNAME: pets/campfires trail the text.
    props = build_campgrounds(ridb_zip)["features"][0]["properties"]
    assert list(props) == [
        "title", "id", "kind", "rec_gov", "reservable", "sites", "site_types", "access", "hookups",
        "max_vehicle_len", "stay_limit", "activities", "overview", "facilities",
        "allows_pets", "allows_fires", "fees", "phone",
    ]
