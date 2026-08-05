"""WFS GetCapabilities / DescribeFeatureType / ExceptionReport XML."""

from __future__ import annotations

from xml.sax.saxutils import escape

from .registry import Layer, get_data

NS_URI = "http://sota"  # namespace URI for the "sota" prefix, mirroring GeoServer's workspace URI

_OUTPUT_FORMATS = [
    "application/json",
    "json",
    "application/json; subtype=geojson",
]


def _layer_bbox(layer: Layer) -> tuple[float, float, float, float]:
    try:
        return get_data(layer).bbox
    except Exception:
        return (-180.0, -90.0, 180.0, 90.0)


def capabilities_xml(version: str, layers: list[Layer], base_url: str) -> str:
    if version.startswith("1"):
        return _capabilities_110(layers, base_url)
    return _capabilities_200(layers, base_url)


def _operations_metadata(base_url: str, version: str) -> str:
    href = escape(f"{base_url}/geoserver/wfs", {'"': "&quot;"})
    # OWS 1.0 (WFS 1.1.0) puts ows:Value directly inside ows:Parameter; the
    # ows:AllowedValues wrapper only exists in OWS 1.1 (WFS 2.0.0). CalTopo's
    # auto-configure parses the 1.1.0 document and rejects wrapped values.
    ows10 = version.startswith("1")

    def param(name: str, values: list[str]) -> str:
        vals = "".join(f"<ows:Value>{escape(v)}</ows:Value>" for v in values)
        if not ows10:
            vals = f"<ows:AllowedValues>{vals}</ows:AllowedValues>"
        return f'<ows:Parameter name="{name}">{vals}</ows:Parameter>'

    ops = []
    for op in ("GetCapabilities", "DescribeFeatureType", "GetFeature"):
        params = []
        if op == "GetCapabilities":
            params.append(param("AcceptVersions", ["1.1.0", "2.0.0"]))
        else:
            if op == "GetFeature":
                # CalTopo's auto-configure reads GetFeature's parameters
                # positionally and needs outputFormat second, as GeoServer
                # serves it (resultType first).
                params.append(param("resultType", ["results", "hits"]))
            params.append(param("outputFormat", _OUTPUT_FORMATS))
        ops.append(
            f'<ows:Operation name="{op}">'
            f"<ows:DCP><ows:HTTP>"
            f'<ows:Get xlink:href="{href}"/>'
            f'<ows:Post xlink:href="{href}"/>'
            f"</ows:HTTP></ows:DCP>{''.join(params)}</ows:Operation>"
        )
    return f"<ows:OperationsMetadata>{''.join(ops)}</ows:OperationsMetadata>"


def _feature_type_110(layer: Layer) -> str:
    minx, miny, maxx, maxy = _layer_bbox(layer)
    return (
        "<FeatureType>"
        f"<Name>{escape(layer.qname)}</Name>"
        f"<Title>{escape(layer.title)}</Title>"
        f"<Abstract>{escape(layer.abstract)}</Abstract>"
        "<DefaultSRS>urn:ogc:def:crs:EPSG::4326</DefaultSRS>"
        "<OutputFormats>"
        + "".join(f"<Format>{escape(f)}</Format>" for f in _OUTPUT_FORMATS)
        + "</OutputFormats>"
        "<ows:WGS84BoundingBox>"
        f"<ows:LowerCorner>{minx} {miny}</ows:LowerCorner>"
        f"<ows:UpperCorner>{maxx} {maxy}</ows:UpperCorner>"
        "</ows:WGS84BoundingBox>"
        "</FeatureType>"
    )


def _capabilities_110(layers: list[Layer], base_url: str) -> str:
    fts = "".join(_feature_type_110(l) for l in layers)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<wfs:WFS_Capabilities version="1.1.0"'
        ' xmlns:wfs="http://www.opengis.net/wfs"'
        ' xmlns="http://www.opengis.net/wfs"'
        ' xmlns:ows="http://www.opengis.net/ows"'
        ' xmlns:xlink="http://www.w3.org/1999/xlink"'
        ' xmlns:ogc="http://www.opengis.net/ogc"'
        f' xmlns:sota="{NS_URI}">'
        "<ows:ServiceIdentification>"
        "<ows:Title>SOTA WFS</ows:Title>"
        "<ows:Abstract>Minimal WFS serving SOTA summits and Tesla Superchargers</ows:Abstract>"
        "<ows:ServiceType>WFS</ows:ServiceType>"
        "<ows:ServiceTypeVersion>1.1.0</ows:ServiceTypeVersion>"
        "</ows:ServiceIdentification>"
        + _operations_metadata(base_url, "1.1.0")
        + f"<FeatureTypeList><Operations><Operation>Query</Operation></Operations>{fts}</FeatureTypeList>"
        "<ogc:Filter_Capabilities>"
        "<ogc:Spatial_Capabilities>"
        "<ogc:GeometryOperands><ogc:GeometryOperand>gml:Point</ogc:GeometryOperand><ogc:GeometryOperand>gml:Envelope</ogc:GeometryOperand></ogc:GeometryOperands>"
        "<ogc:SpatialOperators><ogc:SpatialOperator name=\"BBOX\"/></ogc:SpatialOperators>"
        "</ogc:Spatial_Capabilities>"
        "<ogc:Scalar_Capabilities><ogc:LogicalOperators/></ogc:Scalar_Capabilities>"
        "<ogc:Id_Capabilities><ogc:FID/></ogc:Id_Capabilities>"
        "</ogc:Filter_Capabilities>"
        "</wfs:WFS_Capabilities>"
    )


def _feature_type_200(layer: Layer) -> str:
    minx, miny, maxx, maxy = _layer_bbox(layer)
    return (
        "<FeatureType>"
        f"<Name>{escape(layer.qname)}</Name>"
        f"<Title>{escape(layer.title)}</Title>"
        f"<Abstract>{escape(layer.abstract)}</Abstract>"
        "<DefaultCRS>urn:ogc:def:crs:EPSG::4326</DefaultCRS>"
        "<OutputFormats>"
        + "".join(f"<Format>{escape(f)}</Format>" for f in _OUTPUT_FORMATS)
        + "</OutputFormats>"
        "<ows:WGS84BoundingBox>"
        f"<ows:LowerCorner>{minx} {miny}</ows:LowerCorner>"
        f"<ows:UpperCorner>{maxx} {maxy}</ows:UpperCorner>"
        "</ows:WGS84BoundingBox>"
        "</FeatureType>"
    )


def _capabilities_200(layers: list[Layer], base_url: str) -> str:
    fts = "".join(_feature_type_200(l) for l in layers)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<wfs:WFS_Capabilities version="2.0.0"'
        ' xmlns:wfs="http://www.opengis.net/wfs/2.0"'
        ' xmlns="http://www.opengis.net/wfs/2.0"'
        ' xmlns:ows="http://www.opengis.net/ows/1.1"'
        ' xmlns:xlink="http://www.w3.org/1999/xlink"'
        ' xmlns:fes="http://www.opengis.net/fes/2.0"'
        f' xmlns:sota="{NS_URI}">'
        "<ows:ServiceIdentification>"
        "<ows:Title>SOTA WFS</ows:Title>"
        "<ows:Abstract>Minimal WFS serving SOTA summits and Tesla Superchargers</ows:Abstract>"
        "<ows:ServiceType>WFS</ows:ServiceType>"
        "<ows:ServiceTypeVersion>2.0.0</ows:ServiceTypeVersion>"
        "<ows:ServiceTypeVersion>1.1.0</ows:ServiceTypeVersion>"
        "</ows:ServiceIdentification>"
        + _operations_metadata(base_url, "2.0.0")
        + f"<FeatureTypeList>{fts}</FeatureTypeList>"
        "<fes:Filter_Capabilities>"
        "<fes:Spatial_Capabilities>"
        "<fes:GeometryOperands>"
        '<fes:GeometryOperand name="gml:Point"/><fes:GeometryOperand name="gml:Envelope"/>'
        "</fes:GeometryOperands>"
        '<fes:SpatialOperators><fes:SpatialOperator name="BBOX"/></fes:SpatialOperators>'
        "</fes:Spatial_Capabilities>"
        "</fes:Filter_Capabilities>"
        "</wfs:WFS_Capabilities>"
    )


def describe_feature_type_xml(layer: Layer, columns: list[str]) -> str:
    elements = "".join(
        f'<xsd:element maxOccurs="1" minOccurs="0" name="{escape(c)}" nillable="true" type="xsd:string"/>'
        for c in columns
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema"'
        ' xmlns:gml="http://www.opengis.net/gml"'
        f' xmlns:sota="{NS_URI}"'
        ' elementFormDefault="qualified"'
        f' targetNamespace="{NS_URI}">'
        '<xsd:import namespace="http://www.opengis.net/gml"'
        ' schemaLocation="http://schemas.opengis.net/gml/3.1.1/base/gml.xsd"/>'
        f'<xsd:complexType name="{escape(layer.name)}Type">'
        "<xsd:complexContent>"
        '<xsd:extension base="gml:AbstractFeatureType">'
        "<xsd:sequence>"
        + elements
        + '<xsd:element maxOccurs="1" minOccurs="0" name="the_geom" nillable="true" type="gml:PointPropertyType"/>'
        "</xsd:sequence>"
        "</xsd:extension>"
        "</xsd:complexContent>"
        "</xsd:complexType>"
        f'<xsd:element name="{escape(layer.name)}" substitutionGroup="gml:_Feature" type="sota:{escape(layer.name)}Type"/>'
        "</xsd:schema>"
    )


def exception_xml(code: str, text: str, locator: str | None = None) -> str:
    loc = f' locator="{escape(locator, {chr(34): "&quot;"})}"' if locator else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<ows:ExceptionReport xmlns:ows="http://www.opengis.net/ows/1.1" version="2.0.0">'
        f'<ows:Exception exceptionCode="{escape(code)}"{loc}>'
        f"<ows:ExceptionText>{escape(text)}</ows:ExceptionText>"
        "</ows:Exception>"
        "</ows:ExceptionReport>"
    )
