import json
import textwrap

import pytest

from sota_wfs.loaders import SOTA_POINT_COLORS

FIXTURE_CSV = textwrap.dedent(
    """\
    SOTA Summits List (Date=01/08/2026)
    SummitCode,AssociationName,RegionName,SummitName,AltM,AltFt,GridRef1,GridRef2,Longitude,Latitude,Points,BonusPoints,ValidFrom,ValidTo,ActivationCount,ActivationDate,ActivationCall
    4O/IC-001,Montenegro,Istok Crne Gore,Maja Rosit,2524,8280,19.8505,42.4795,19.85050,42.47950,10,3,01/03/2019,31/12/2099,1,27/07/2022,4O/SQ9MDF/P
    W6/NC-001,USA,California Nevada County,Mount Lola,2774,9101,-120.5217,39.4337,-120.52170,39.43370,10,3,01/07/2010,31/12/2099,42,01/01/2024,N0CALL
    W6/NC-002,USA,California Nevada County,Castle Peak,2775,9103,-120.3517,39.3657,-120.35170,39.36570,10,3,01/07/2010,31/12/2099,0,,
    3Y/BV-001,Bouvet Island,Bouvetoya,Olavtoppen,780,2559,3.3565,-54.4104,3.35650,-54.41040,10,3,01/03/2018,31/12/2099,0,,
    W6/CC-067,USA - California,Coastal Ranges,Oyster Point,642,2106,-121.8777,37.8305,-121.87770,37.83050,1,0,01/07/2009,31/07/2012,0,,
    W6/XX-999,USA - California,Nowhere Yet,Future Peak,1000,3281,-120.0000,38.0000,-120.00000,38.00000,1,0,01/01/2099,31/12/2099,0,,
    """
)


FIXTURE_SUPERCHARGERS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-120.1853, 39.3274]},
            "properties": {
                "id": 150544,
                "station_name": "Truckee, CA - Supercharger",
                "street_address": "11290 Donner Pass Rd",
                "city": "Truckee",
                "state": "CA",
                "zip": "96161",
                "ev_dc_fast_num": 12,
                "ev_connector_types": ["TESLA"],
                "ev_pricing": "$0.36/kWh",
                "access_days_time": "24 hours daily",
                "station_phone": "877-798-3752",
                "ev_charging_units": [{"connectors": {"tesla": {"power_kw": 250}}}],
            },
        }
    ],
}


FIXTURE_CAMPGROUNDS = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-119.565, 37.7376]},
            "properties": {
                "title": "UPPER PINES",
                "id": 232447,
                "kind": "Campground",
                "rec_gov": "https://www.recreation.gov/camping/campgrounds/232447",
                "reservable": "Yes",
                "sites": 3,
                "site_types": "Standard nonelectric 2, Tent only nonelectric 1",
                "access": "Drive-in, Walk-in",
                "hookups": "",
                "allows_pets": "Yes",
                "allows_fires": "No",
                "max_vehicle_len": 40,
                "activities": "Camping, Hiking",
                "stay_limit": "14 days",
                "fees": "$36 per night",
                "overview": "Upper Pines is open year-round.",
                "facilities": "Vault toilets and drinking water.",
                "phone": "209-372-0200",
            },
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-120.391158, 39.502664]},
            "properties": {
                "title": "Lake of the Woods",
                "id": 249926,
                "kind": "Camping area",
                "rec_gov": "https://www.recreation.gov/gateways/1077",
                "reservable": "No",
                "sites": 0,
                "overview": "A nice small lake.",
            },
        },
    ],
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "summitslist.csv").write_text(FIXTURE_CSV)

    import sota_wfs.az as az
    import sota_wfs.registry as registry

    monkeypatch.setattr(az, "COMPUTE_ENABLED", False)

    monkeypatch.setattr(registry, "DATA_DIR", data_dir)
    layers = {}
    for key, layer in registry.LAYERS.items():
        layers[key] = registry.Layer(
            name=layer.name,
            ns=layer.ns,
            title=layer.title,
            abstract=layer.abstract,
            source=data_dir / layer.source.name,
            loader=layer.loader,
        )
    monkeypatch.setattr(registry, "LAYERS", layers)
    monkeypatch.setattr(registry, "_cache", {})

    from sota_wfs.app import create_app

    return create_app().test_client()


def get_json(client, url):
    resp = client.get(url)
    assert resp.status_code == 200, resp.data
    return json.loads(resp.data)


CALTOPO_TEMPLATE = (
    "/geoserver/wfs?SERVICE=WFS&VERSION=1.1.0&REQUEST=GetFeature"
    "&BBOX={bbox}&OUTPUTFORMAT=application/json&TYPENAMES=sota:SOTA_Summits"
)


def test_caltopo_getfeature_shape(client):
    fc = get_json(
        client,
        CALTOPO_TEMPLATE.format(bbox="42.47,19.84,42.50,19.86")
        + "&PROPERTYNAME=SummitCode,SummitName,Points,BonusPoints,Activations,SOTLAS,the_geom",
    )
    assert fc["totalFeatures"] == 1
    feat = fc["features"][0]
    assert feat["geometry_name"] == "the_geom"
    assert feat["geometry"]["coordinates"] == [19.8505, 42.4795]
    assert feat["properties"] == {
        "SummitCode": "4O/IC-001",
        "SummitName": "Maja Rosit",
        "Points": 10,
        "BonusPoints": 3,
        "Activations": "1",
        "SOTLAS": "https://sotl.as/summits/4O/IC-001",
        "title": "Maja Rosit",  # served even when not in PROPERTYNAME
        "marker-color": SOTA_POINT_COLORS[10],  # served even when not in PROPERTYNAME
        "marker-symbol": "circle-10",
        "GeoJSON": "http://localhost/summit/4O_IC-001.geojson",
    }


def test_all_properties_and_types(client):
    fc = get_json(client, CALTOPO_TEMPLATE.format(bbox="42.47,19.84,42.50,19.86"))
    props = fc["features"][0]["properties"]
    assert len(props) == 23  # 17 CSV + title, SOTLAS, Activations, marker-color, marker-symbol, GeoJSON
    assert props["AltM"] == 2524          # int
    assert props["GridRef1"] == 19.8505   # float
    assert props["Activations"] == "1"    # string (the CAST parity)
    assert props["ActivationCount"] == 1  # int


def test_bbox_lat_first_vs_lon_first(client):
    lat_first = get_json(client, CALTOPO_TEMPLATE.format(bbox="39.0,-121.0,40.0,-120.0"))
    assert lat_first["totalFeatures"] == 2
    crs84 = get_json(
        client,
        CALTOPO_TEMPLATE.format(bbox="-121.0,39.0,-120.0,40.0,urn:ogc:def:crs:OGC:1.3:CRS84"),
    )
    assert crs84["totalFeatures"] == 2
    # heuristic: |lat| > 90 in lat-first slots → reinterpret as lon-first
    fallback = get_json(client, CALTOPO_TEMPLATE.format(bbox="-121.0,39.0,-120.0,40.0"))
    assert fallback["totalFeatures"] == 2


def test_empty_bbox(client):
    fc = get_json(client, CALTOPO_TEMPLATE.format(bbox="0.0,0.0,1.0,1.0"))
    assert fc["features"] == []
    assert fc["totalFeatures"] == 0


def test_no_bbox_returns_all_and_count_limits(client):
    fc = get_json(client, "/geoserver/wfs?service=wfs&request=getfeature&typename=sota:SOTA_Summits")
    assert fc["totalFeatures"] == 4
    fc = get_json(
        client,
        "/geoserver/wfs?service=wfs&request=getfeature&typename=sota:SOTA_Summits&maxFeatures=2",
    )
    assert fc["numberReturned"] == 2
    assert fc["numberMatched"] == 4


def test_invalid_summits_omitted(client):
    fc = get_json(client, "/geoserver/wfs?service=wfs&request=getfeature&typename=sota:SOTA_Summits")
    codes = {f["properties"]["SummitCode"] for f in fc["features"]}
    assert "W6/CC-067" not in codes  # retired 31/07/2012
    assert "W6/XX-999" not in codes  # not valid until 2099


def test_az_polygon_served_only_when_zoomed_in(client, tmp_path):
    ring = [[19.849, 42.478], [19.852, 42.478], [19.852, 42.481], [19.849, 42.478]]
    az_dir = tmp_path / "data" / "az"
    az_dir.mkdir()
    (az_dir / "4O_IC-001.json").write_text(json.dumps({"ok": True, "ring": ring}))

    # zoomed in (lat span 0.03): polygon rides along with the summit point
    fc = get_json(client, CALTOPO_TEMPLATE.format(bbox="42.47,19.84,42.50,19.86"))
    by_id = {f["id"]: f for f in fc["features"]}
    assert fc["totalFeatures"] == 2
    poly = by_id["SOTA_Summits.az-4O_IC-001"]
    assert poly["geometry"]["type"] == "Polygon"
    assert poly["properties"]["SummitName"] == "Maja Rosit AZ"
    assert poly["properties"]["fill"] == "#FFAA00"
    assert poly["properties"]["GeoJSON"] == "http://localhost/az/4O_IC-001.geojson"

    # zoomed out (lat span 1.0): points only
    fc = get_json(client, CALTOPO_TEMPLATE.format(bbox="42.0,19.0,43.0,20.0"))
    assert [f["geometry"]["type"] for f in fc["features"]] == ["Point"]

    # cached failures are not served
    (az_dir / "4O_IC-001.json").write_text(json.dumps({"ok": False, "error": "x"}))
    fc = get_json(client, CALTOPO_TEMPLATE.format(bbox="42.47,19.84,42.50,19.86"))
    assert fc["totalFeatures"] == 1


def test_az_geojson_download(client, tmp_path):
    ring = [[19.849, 42.478], [19.852, 42.478], [19.852, 42.481], [19.849, 42.478]]
    az_dir = tmp_path / "data" / "az"
    az_dir.mkdir()
    (az_dir / "4O_IC-001.json").write_text(json.dumps({"ok": True, "ring": ring}))

    resp = client.get("/az/4O_IC-001.geojson")
    assert resp.status_code == 200
    assert resp.content_type == "application/geo+json"
    assert 'filename="4O_IC-001_az.geojson"' in resp.headers["Content-Disposition"]
    fc = json.loads(resp.data)
    assert fc["name"] == "Maja Rosit - AZ"
    feat = fc["features"][0]
    assert feat["geometry"]["coordinates"] == [ring]
    assert feat["properties"]["title"] == "Maja Rosit - AZ"
    assert feat["properties"]["name"] == "Maja Rosit - AZ"
    assert feat["properties"]["SummitName"] == "Maja Rosit AZ"

    # unknown summit and uncached AZ both 404
    assert client.get("/az/ZZ_XX-000.geojson").status_code == 404
    assert client.get("/az/W6_NC-001.geojson").status_code == 404


def test_summit_geojson_download(client):
    resp = client.get("/summit/4O_IC-001.geojson")
    assert resp.status_code == 200
    assert resp.content_type == "application/geo+json"
    assert 'filename="4O_IC-001_summit.geojson"' in resp.headers["Content-Disposition"]
    fc = json.loads(resp.data)
    assert fc["name"] == "Maja Rosit"
    feat = fc["features"][0]
    assert feat["geometry"]["coordinates"] == [19.8505, 42.4795]
    assert feat["properties"]["title"] == "Maja Rosit"
    assert feat["properties"]["name"] == "Maja Rosit"
    assert feat["properties"]["SummitCode"] == "4O/IC-001"
    assert feat["properties"]["marker-symbol"] == "circle-10"
    assert feat["properties"]["GeoJSON"] == "http://localhost/summit/4O_IC-001.geojson"

    # unknown summit 404s
    assert client.get("/summit/ZZ_XX-000.geojson").status_code == 404


def test_supercharger_getfeature_has_geojson(client, tmp_path):
    (tmp_path / "data" / "superchargers.geojson").write_text(json.dumps(FIXTURE_SUPERCHARGERS))
    fc = get_json(
        client,
        "/geoserver/wfs?service=WFS&request=GetFeature&typename=sota:Tesla_Superchargers",
    )
    assert fc["totalFeatures"] == 1
    props = fc["features"][0]["properties"]
    assert props["id"] == 150544
    assert props["title"] == "Truckee, CA - Supercharger"
    assert props["power_kw"] == 250
    assert props["GeoJSON"] == "http://localhost/supercharger/150544.geojson"


def test_supercharger_geojson_download(client, tmp_path):
    (tmp_path / "data" / "superchargers.geojson").write_text(json.dumps(FIXTURE_SUPERCHARGERS))
    resp = client.get("/supercharger/150544.geojson")
    assert resp.status_code == 200
    assert resp.content_type == "application/geo+json"
    assert 'filename="150544_supercharger.geojson"' in resp.headers["Content-Disposition"]
    fc = json.loads(resp.data)
    assert fc["name"] == "Truckee, CA - Supercharger"
    feat = fc["features"][0]
    assert feat["geometry"]["coordinates"] == [-120.1853, 39.3274]
    assert feat["properties"]["title"] == "Truckee, CA - Supercharger"
    assert feat["properties"]["name"] == "Truckee, CA - Supercharger"
    assert feat["properties"]["marker-symbol"] == "electric-charging"
    assert feat["properties"]["GeoJSON"] == "http://localhost/supercharger/150544.geojson"

    # unknown station 404s
    assert client.get("/supercharger/999999.geojson").status_code == 404


def test_campground_getfeature(client, tmp_path):
    (tmp_path / "data" / "campgrounds.geojson").write_text(json.dumps(FIXTURE_CAMPGROUNDS))
    fc = get_json(
        client,
        "/geoserver/wfs?service=WFS&request=GetFeature&typename=sota:Recreation_Camping",
    )
    assert fc["totalFeatures"] == 2
    feat = fc["features"][0]
    assert feat["geometry"]["coordinates"] == [-119.565, 37.7376]
    props = feat["properties"]
    assert props["title"] == "UPPER PINES"
    assert props["id"] == 232447
    assert props["rec_gov"] == "https://www.recreation.gov/camping/campgrounds/232447"
    assert props["sites"] == 3
    assert props["max_vehicle_len"] == 40
    assert props["marker-color"] == "#2E7D32"
    assert props["marker-symbol"] == "camping"  # CalTopo's "Tent" icon
    assert props["GeoJSON"] == "http://localhost/campground/232447.geojson"
    # Camping areas (dispersed / informal) get the campfire icon in brown.
    area = fc["features"][1]["properties"]
    assert area["kind"] == "Camping area"
    assert area["marker-symbol"] == "campfire"
    assert area["marker-color"] == "#A0522D"
    assert area["GeoJSON"] == "http://localhost/campground/249926.geojson"


def test_campground_geojson_download(client, tmp_path):
    (tmp_path / "data" / "campgrounds.geojson").write_text(json.dumps(FIXTURE_CAMPGROUNDS))
    resp = client.get("/campground/232447.geojson")
    assert resp.status_code == 200
    assert resp.content_type == "application/geo+json"
    assert 'filename="232447_campground.geojson"' in resp.headers["Content-Disposition"]
    fc = json.loads(resp.data)
    assert fc["name"] == "UPPER PINES"
    feat = fc["features"][0]
    assert feat["id"] == "Recreation_Camping.1"
    assert feat["geometry"]["coordinates"] == [-119.565, 37.7376]
    assert feat["properties"]["title"] == "UPPER PINES"
    assert feat["properties"]["name"] == "UPPER PINES"
    assert feat["properties"]["sites"] == 3
    assert feat["properties"]["GeoJSON"] == "http://localhost/campground/232447.geojson"

    # unknown campground 404s
    assert client.get("/campground/1.geojson").status_code == 404


def test_param_case_insensitivity_and_both_routes(client):
    for url in (
        "/geoserver/wfs?SeRvIcE=WFS&ReQuEsT=GetFeature&TyPeNaMe=SOTA_Summits",
        "/geoserver/sota/wfs?service=WFS&request=GetFeature&typenames=foo:sota_summits",
        "/wfs?service=WFS&request=GetFeature&typename=sota:SOTA_Summits",
    ):
        fc = get_json(client, url)
        assert fc["totalFeatures"] == 4


def test_capabilities_both_versions(client):
    for url, marker in (
        ("/geoserver/sota/wfs?service=WFS&version=2.0.0&request=GetCapabilities", 'version="2.0.0"'),
        ("/geoserver/wfs?service=WFS&version=1.1.0&request=GetCapabilities", 'version="1.1.0"'),
    ):
        resp = client.get(url)
        assert resp.status_code == 200
        body = resp.data.decode()
        assert marker in body
        assert "sota:SOTA_Summits" in body
        assert "application/json" in body
        # hrefs must be built from the request host, not hardcoded
        assert "http://localhost/geoserver/wfs" in body


def test_capabilities_forwarded_proto(client):
    resp = client.get(
        "/geoserver/wfs?service=WFS&request=GetCapabilities",
        headers={"X-Forwarded-Proto": "https", "Host": "example.ngrok-free.dev"},
    )
    assert "https://example.ngrok-free.dev/geoserver/wfs" in resp.data.decode()


def test_unknown_typename_is_exception_report(client):
    resp = client.get("/geoserver/wfs?service=WFS&request=GetFeature&typename=sota:Nope")
    assert resp.status_code == 400
    assert b"ExceptionReport" in resp.data
    assert b"InvalidParameterValue" in resp.data


def test_unknown_request_is_exception_report(client):
    resp = client.get("/geoserver/wfs?service=WFS&request=Transaction")
    assert resp.status_code == 400
    assert b"OperationNotSupported" in resp.data


def test_describe_feature_type(client):
    resp = client.get(
        "/geoserver/wfs?service=WFS&request=DescribeFeatureType&typename=sota:SOTA_Summits"
    )
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "SummitCode" in body
    assert "gml:PointPropertyType" in body


def test_missing_data_file_is_wfs_error(client):
    resp = client.get("/geoserver/wfs?service=WFS&request=GetFeature&typename=sota:Tesla_Superchargers")
    assert resp.status_code == 400
    assert b"not yet fetched" in resp.data
