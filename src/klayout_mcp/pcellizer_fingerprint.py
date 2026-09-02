"""Exact DBU geometry fingerprints and ruler-to-edge binding for PCellizer."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
from typing import Any, Mapping, Sequence

from .errors import AnalysisError
from .workflow_manifest import canonical_sha256, immutable_json_copy


SUPPORTED_GEOMETRY_KINDS = ("box", "polygon", "path", "edge")


def _fail(code: str, message: str, **details: Any) -> None:
    raise AnalysisError(
        code=code,
        message=message,
        details=details,
        next_action="Reselect exact Manhattan geometry and a DBU-aligned edge-to-edge ruler.",
    )


def _integer(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(
            "INVALID_PCELLIZER_GEOMETRY",
            f"{field} must be an integer DBU value.",
            field=field,
            value=value,
        )
    return value


def _point(value: Any, *, field: str) -> list[int]:
    if (
        isinstance(value, (str, bytes, bytearray))
        or not isinstance(value, Sequence)
        or len(value) != 2
    ):
        _fail(
            "INVALID_PCELLIZER_GEOMETRY",
            f"{field} must contain two DBU coordinates.",
            field=field,
            value=value,
        )
    return [
        _integer(value[0], field=f"{field}[0]"),
        _integer(value[1], field=f"{field}[1]"),
    ]


def _canonical_ring(points: Any, *, field: str) -> list[list[int]]:
    if (
        isinstance(points, (str, bytes, bytearray))
        or not isinstance(points, Sequence)
    ):
        _fail(
            "INVALID_PCELLIZER_GEOMETRY",
            f"{field} must be an array of DBU points.",
            field=field,
        )
    normalized = [_point(point, field=f"{field}[]") for point in points]
    if len(normalized) >= 2 and normalized[0] == normalized[-1]:
        normalized.pop()
    if len(normalized) < 3 or len({tuple(point) for point in normalized}) < 3:
        _fail(
            "DEGENERATE_PCELLIZER_POLYGON",
            "Polygon contours require at least three distinct DBU points.",
            field=field,
            points=normalized,
        )
    candidates: list[list[list[int]]] = []
    for direction in (normalized, list(reversed(normalized))):
        for offset in range(len(direction)):
            candidates.append(direction[offset:] + direction[:offset])
    return min(candidates)


def canonical_edge(value: Any, *, field: str = "edge_dbu") -> list[list[int]]:
    if (
        isinstance(value, (str, bytes, bytearray))
        or not isinstance(value, Sequence)
        or len(value) != 2
    ):
        _fail(
            "INVALID_PCELLIZER_EDGE",
            f"{field} must contain exactly two endpoints.",
            field=field,
            value=value,
        )
    points = sorted(
        [_point(value[0], field=f"{field}[0]"), _point(value[1], field=f"{field}[1]")]
    )
    if points[0] == points[1]:
        _fail(
            "DEGENERATE_PCELLIZER_EDGE",
            "Edge endpoints must be distinct.",
            field=field,
            edge_dbu=points,
        )
    return points


def normalize_geometry_record(value: Mapping[str, Any]) -> dict[str, Any]:
    """Canonicalize local geometry without using tolerances or floating point."""

    if not isinstance(value, Mapping):
        _fail(
            "INVALID_PCELLIZER_GEOMETRY",
            "Geometry record must be an object.",
            received_type=type(value).__name__,
        )
    kind = value.get("kind")
    if kind == "box":
        bbox = value.get("bbox_dbu")
        if (
            isinstance(bbox, (str, bytes, bytearray))
            or not isinstance(bbox, Sequence)
            or len(bbox) != 4
        ):
            _fail(
                "INVALID_PCELLIZER_GEOMETRY",
                "Box geometry requires bbox_dbu [x1,y1,x2,y2].",
                bbox_dbu=bbox,
            )
        coords = [_integer(item, field="bbox_dbu[]") for item in bbox]
        if coords[0] >= coords[2] or coords[1] >= coords[3]:
            _fail(
                "DEGENERATE_PCELLIZER_BOX",
                "Box geometry must have positive width and height.",
                bbox_dbu=coords,
            )
        return {"kind": "box", "bbox_dbu": coords}
    if kind == "polygon":
        hull = _canonical_ring(value.get("hull_dbu"), field="hull_dbu")
        holes_value = value.get("holes_dbu", [])
        if (
            isinstance(holes_value, (str, bytes, bytearray))
            or not isinstance(holes_value, Sequence)
        ):
            _fail(
                "INVALID_PCELLIZER_GEOMETRY",
                "Polygon holes_dbu must be an array of contours.",
            )
        holes = sorted(
            _canonical_ring(hole, field="holes_dbu[]") for hole in holes_value
        )
        return {"kind": "polygon", "hull_dbu": hull, "holes_dbu": holes}
    if kind == "path":
        raw_points = value.get("points_dbu")
        if (
            isinstance(raw_points, (str, bytes, bytearray))
            or not isinstance(raw_points, Sequence)
        ):
            _fail(
                "INVALID_PCELLIZER_GEOMETRY",
                "Path points_dbu must be an array.",
            )
        points = [_point(point, field="points_dbu[]") for point in raw_points]
        if len(points) < 2 or any(a == b for a, b in zip(points, points[1:])):
            _fail(
                "DEGENERATE_PCELLIZER_PATH",
                "Path requires at least two consecutive distinct points.",
                points_dbu=points,
            )
        width = _integer(value.get("width_dbu"), field="width_dbu")
        if width <= 0:
            _fail(
                "DEGENERATE_PCELLIZER_PATH",
                "Path width must be positive.",
                width_dbu=width,
            )
        forward = {
            "kind": "path",
            "points_dbu": points,
            "width_dbu": width,
            "begin_extension_dbu": _integer(
                value.get("begin_extension_dbu", 0), field="begin_extension_dbu"
            ),
            "end_extension_dbu": _integer(
                value.get("end_extension_dbu", 0), field="end_extension_dbu"
            ),
            "round": bool(value.get("round", False)),
        }
        reverse = {
            **forward,
            "points_dbu": list(reversed(points)),
            "begin_extension_dbu": forward["end_extension_dbu"],
            "end_extension_dbu": forward["begin_extension_dbu"],
        }
        return min((forward, reverse), key=canonical_sha256)
    if kind == "edge":
        return {"kind": "edge", "edge_dbu": canonical_edge(value.get("edge_dbu"))}
    _fail(
        "UNSUPPORTED_PCELLIZER_SHAPE_KIND",
        "Selected shape kind is not authorable in the initial PCellizer slice.",
        kind=kind,
        supported_kinds=list(SUPPORTED_GEOMETRY_KINDS),
    )


def build_shape_identity(
    *,
    geometry: Mapping[str, Any],
    layer: int,
    datatype: int,
    shape_ordinal: int,
    duplicate_geometry_count: int = 1,
) -> dict[str, Any]:
    """Bind canonical geometry to its direct-cell ordinal and exact stream layer."""

    normalized = normalize_geometry_record(geometry)
    layer_value = _integer(layer, field="layer")
    datatype_value = _integer(datatype, field="datatype")
    ordinal = _integer(shape_ordinal, field="shape_ordinal")
    duplicate_count = _integer(
        duplicate_geometry_count, field="duplicate_geometry_count"
    )
    if min(layer_value, datatype_value, ordinal) < 0 or duplicate_count < 1:
        _fail(
            "INVALID_PCELLIZER_SHAPE_IDENTITY",
            "Layer, datatype, ordinal and duplicate count are outside their valid ranges.",
            layer=layer_value,
            datatype=datatype_value,
            shape_ordinal=ordinal,
            duplicate_geometry_count=duplicate_count,
        )
    payload = {
        "layer": layer_value,
        "datatype": datatype_value,
        "shape_ordinal": ordinal,
        "duplicate_geometry_count": duplicate_count,
        "geometry": normalized,
    }
    return {**payload, "shape_fingerprint_sha256": canonical_sha256(payload)}


def build_neighborhood_fingerprint(
    shape_identities: Sequence[Mapping[str, Any]], *, radius_dbu: int
) -> dict[str, Any]:
    """Hash an explicitly bounded, order-independent local geometry neighborhood."""

    radius = _integer(radius_dbu, field="radius_dbu")
    if radius < 0:
        _fail(
            "INVALID_PCELLIZER_NEIGHBORHOOD_RADIUS",
            "Neighborhood radius must be nonnegative.",
            radius_dbu=radius,
        )
    records = [immutable_json_copy(record) for record in shape_identities]
    records.sort(key=canonical_sha256)
    payload = {"radius_dbu": radius, "shape_identities": records}
    return {
        **payload,
        "neighborhood_fingerprint_sha256": canonical_sha256(payload),
    }


def micron_ruler_to_exact_dbu(
    points_um: Sequence[Sequence[float]],
    *,
    dbu_um: float,
    grid_tolerance_dbu: float = 1e-6,
) -> list[list[int]]:
    """Convert one ruler to exact DBU and reject off-grid or non-Manhattan input."""

    if (
        isinstance(points_um, (str, bytes, bytearray))
        or not isinstance(points_um, Sequence)
        or len(points_um) != 2
    ):
        _fail(
            "PCELLIZER_SINGLE_SEGMENT_RULER_REQUIRED",
            "Exactly one two-point ruler is required.",
            points_um=points_um,
        )
    try:
        dbu = Decimal(str(dbu_um))
        tolerance = Decimal(str(grid_tolerance_dbu))
    except (InvalidOperation, ValueError) as exc:
        _fail(
            "INVALID_PCELLIZER_DBU",
            "DBU and grid tolerance must be finite decimal values.",
            dbu_um=dbu_um,
            grid_tolerance_dbu=grid_tolerance_dbu,
        )
        raise AssertionError from exc
    if not dbu.is_finite() or dbu <= 0 or not tolerance.is_finite() or tolerance < 0:
        _fail(
            "INVALID_PCELLIZER_DBU",
            "DBU must be positive and tolerance nonnegative.",
            dbu_um=dbu_um,
            grid_tolerance_dbu=grid_tolerance_dbu,
        )

    result: list[list[int]] = []
    for point_index, raw_point in enumerate(points_um):
        if (
            isinstance(raw_point, (str, bytes, bytearray))
            or not isinstance(raw_point, Sequence)
            or len(raw_point) != 2
        ):
            _fail(
                "INVALID_PCELLIZER_RULER",
                "Each ruler endpoint must contain two micron coordinates.",
                point_index=point_index,
            )
        converted = []
        for axis, raw_coordinate in zip(("x", "y"), raw_point):
            try:
                coordinate = Decimal(str(raw_coordinate)) / dbu
            except (InvalidOperation, ValueError, ZeroDivisionError) as exc:
                _fail(
                    "INVALID_PCELLIZER_RULER",
                    "Ruler coordinates must be finite numeric values.",
                    point_index=point_index,
                    axis=axis,
                    value=raw_coordinate,
                )
                raise AssertionError from exc
            nearest = coordinate.to_integral_value(rounding=ROUND_HALF_EVEN)
            if not coordinate.is_finite() or abs(coordinate - nearest) > tolerance:
                _fail(
                    "OFF_GRID_PCELLIZER_RULER",
                    "Ruler endpoint is not exactly aligned to the layout DBU grid.",
                    point_index=point_index,
                    axis=axis,
                    coordinate_dbu=str(coordinate),
                    nearest_dbu=str(nearest),
                    tolerance_dbu=str(tolerance),
                )
            converted.append(int(nearest))
        result.append(converted)

    if result[0] == result[1]:
        _fail(
            "DEGENERATE_PCELLIZER_RULER",
            "Ruler endpoints must be distinct.",
            ruler_dbu=result,
        )
    if result[0][0] != result[1][0] and result[0][1] != result[1][1]:
        _fail(
            "NON_MANHATTAN_PCELLIZER_RULER",
            "Initial PCellizer rulers must be horizontal or vertical.",
            ruler_dbu=result,
        )
    return result


def _point_on_manhattan_edge(point: Sequence[int], edge: Sequence[Sequence[int]]) -> bool:
    x, y = point
    (x1, y1), (x2, y2) = edge
    if x1 == x2:
        return x == x1 and min(y1, y2) <= y <= max(y1, y2)
    if y1 == y2:
        return y == y1 and min(x1, x2) <= x <= max(x1, x2)
    return False


def bind_ruler_to_selected_edges(
    ruler_dbu: Sequence[Sequence[int]],
    selected_shapes: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Bind each ruler endpoint to one unambiguous selected Manhattan edge."""

    canonical_ruler = canonical_edge(ruler_dbu, field="ruler_dbu")
    first_point = list(ruler_dbu[0])
    ruler = (
        canonical_ruler
        if first_point == canonical_ruler[0]
        else [canonical_ruler[1], canonical_ruler[0]]
    )
    if ruler[0][0] != ruler[1][0] and ruler[0][1] != ruler[1][1]:
        _fail(
            "NON_MANHATTAN_PCELLIZER_RULER",
            "Initial PCellizer rulers must be horizontal or vertical.",
            ruler_dbu=ruler,
        )
    bindings = []
    for endpoint_index, endpoint in enumerate(ruler):
        candidates = []
        for selection_index, shape in enumerate(selected_shapes):
            edges = shape.get("top_edges_dbu")
            if (
                isinstance(edges, (str, bytes, bytearray))
                or not isinstance(edges, Sequence)
            ):
                _fail(
                    "INVALID_PCELLIZER_EDGE_CANDIDATES",
                    "Selected shape must provide top_edges_dbu.",
                    selection_index=selection_index,
                )
            for edge_index, raw_edge in enumerate(edges):
                edge = canonical_edge(raw_edge, field="top_edges_dbu[]")
                if _point_on_manhattan_edge(endpoint, edge):
                    candidates.append(
                        {
                            "selection_index": selection_index,
                            "edge_index": edge_index,
                            "edge_dbu": edge,
                        }
                    )
        if len(candidates) != 1:
            _fail(
                "AMBIGUOUS_PCELLIZER_RULER_ENDPOINT",
                "Each ruler endpoint must lie on exactly one selected Manhattan edge.",
                endpoint_index=endpoint_index,
                endpoint_dbu=endpoint,
                candidate_count=len(candidates),
                candidates=candidates,
            )
        bindings.append({"endpoint_dbu": endpoint, **candidates[0]})
    return {
        "ruler_dbu": ruler,
        "orientation": "vertical" if ruler[0][0] == ruler[1][0] else "horizontal",
        "length_dbu": abs(ruler[1][0] - ruler[0][0])
        + abs(ruler[1][1] - ruler[0][1]),
        "endpoint_bindings": bindings,
        "edge_snap": "exact_dbu",
        "ambiguous": False,
    }
