"""Flask app: /geoserver WFS routes, param normalization, request dispatch."""

from __future__ import annotations

import json

from flask import Flask, Response, request

from . import az
from .capabilities import capabilities_xml, describe_feature_type_xml, exception_xml
from .getfeature import WfsError, parse_bbox, resolve_properties, select
from .registry import available_layers, get_data, resolve_typename

XML = "text/xml"


def _base_url() -> str:
    proto = request.headers.get("X-Forwarded-Proto", request.scheme)
    host = request.headers.get("X-Forwarded-Host", request.host)
    return f"{proto}://{host}"


def _xml(body: str, status: int = 200) -> Response:
    return Response(body, status=status, content_type=XML)


def _error(exc: WfsError, status: int = 400) -> Response:
    return _xml(exception_xml(exc.code, str(exc), exc.locator), status)


def create_app() -> Flask:
    app = Flask(__name__)

    @app.after_request
    def cors(resp: Response) -> Response:
        resp.headers["Access-Control-Allow-Origin"] = "*"
        return resp

    @app.route("/wfs")
    @app.route("/geoserver/wfs")
    @app.route("/geoserver/<ns>/wfs")
    def wfs(ns: str | None = None) -> Response:
        params = {k.lower(): v for k, v in request.args.items()}
        req = params.get("request", "").lower()
        try:
            if req == "getcapabilities":
                return _get_capabilities(params)
            if req == "getfeature":
                return _get_feature(params)
            if req == "describefeaturetype":
                return _describe_feature_type(params)
            raise WfsError(
                "OperationNotSupported" if req else "MissingParameterValue",
                f"Unsupported request: {params.get('request', '(missing)')}",
                "request",
            )
        except WfsError as exc:
            return _error(exc)

    def _get_capabilities(params: dict[str, str]) -> Response:
        version = params.get("version") or params.get("acceptversions", "").split(",")[0] or "2.0.0"
        return _xml(capabilities_xml(version.strip(), available_layers(), _base_url()))

    def _typename_param(params: dict[str, str]) -> str:
        raw = params.get("typenames") or params.get("typename")
        if not raw:
            raise WfsError("MissingParameterValue", "typeNames parameter is required", "typeNames")
        return raw

    def _resolve_layer(raw: str):
        layer = resolve_typename(raw)
        if layer is None:
            raise WfsError("InvalidParameterValue", f"Unknown type name: {raw}", "typeNames")
        return layer

    def _get_feature(params: dict[str, str]) -> Response:
        layer = _resolve_layer(_typename_param(params))
        out_fmt = params.get("outputformat", "application/json")
        if "json" not in out_fmt.lower():
            raise WfsError(
                "InvalidParameterValue",
                f"Only GeoJSON output is supported, got {out_fmt!r}",
                "outputFormat",
            )
        try:
            data = get_data(layer)
        except FileNotFoundError:
            raise WfsError("NoApplicableCode", f"Data for {layer.qname} not yet fetched")
        bbox = parse_bbox(params["bbox"]) if params.get("bbox") else None
        props = resolve_properties(data, params.get("propertyname") or params.get("propertynames"))
        count_raw = params.get("count") or params.get("maxfeatures")
        try:
            count = int(count_raw) if count_raw else None
        except ValueError:
            raise WfsError("InvalidParameterValue", f"Malformed count: {count_raw!r}", "count")
        fc = select(layer, data, bbox, props, count, _base_url())
        return Response(json.dumps(fc, separators=(",", ":")), content_type="application/json")

    @app.route("/az/<ref>.geojson")
    def az_download(ref: str) -> Response:
        code = ref.replace("_", "/")  # SOTA refs hold exactly one slash, no underscores
        layer = resolve_typename("SOTA_Summits")
        try:
            data = get_data(layer)
        except FileNotFoundError:
            return Response("Summit data not yet fetched", status=503, content_type="text/plain")
        rows = data.props[data.props["SummitCode"] == code]
        fc = az.download_geojson(code, rows.iloc[0], _base_url()) if len(rows) else None
        if fc is None:
            return Response(
                f"No cached activation zone for {code}", status=404, content_type="text/plain"
            )
        resp = Response(
            json.dumps(fc, separators=(",", ":")), content_type="application/geo+json"
        )
        resp.headers["Content-Disposition"] = f'attachment; filename="{ref}_az.geojson"'
        return resp

    def _describe_feature_type(params: dict[str, str]) -> Response:
        raw = params.get("typenames") or params.get("typename")
        layers = [_resolve_layer(raw)] if raw else available_layers()
        layer = layers[0]
        try:
            columns = list(get_data(layer).props.columns)
        except FileNotFoundError:
            columns = []
        return _xml(describe_feature_type_xml(layer, columns))

    return app
