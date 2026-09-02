"""Deterministic, hierarchy-preserving recipe compilation for PCellizer P1."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from .errors import AnalysisError
from .pcellizer_intent import validate_pcellizer_parameter_intent
from .pcellizer_fingerprint import canonical_edge
from .pcellizer_snapshot import inspect_pcellizer_snapshot_package
from .workflow_manifest import SHA256_PATTERN, canonical_sha256, immutable_json_copy


RECIPE_SCHEMA_VERSION = 1
RECIPE_KIND = "PCellizerSingleShapeRecipe"


def _fail(code: str, message: str, **details: Any) -> None:
    raise AnalysisError(
        code=code,
        message=message,
        details={**details, "production_ready": False},
        next_action=(
            "Recapture one direct box with a single ruler across two opposite edges, "
            "then confirm the parameter intent explicitly."
        ),
    )


def _um_to_dbu(value: Any, dbu_um: Any, *, field: str) -> int:
    try:
        ratio = Decimal(str(value)) / Decimal(str(dbu_um))
    except (InvalidOperation, ValueError, ZeroDivisionError) as exc:
        _fail("INVALID_PCELLIZER_RECIPE_QUANTIZATION", f"{field} cannot be converted to DBU.")
        raise AssertionError from exc
    integral = ratio.to_integral_value()
    if not ratio.is_finite() or ratio != integral:
        _fail(
            "PCELLIZER_RECIPE_VALUE_OFF_DBU",
            f"{field} must be an exact integer number of DBU.",
            field=field,
            value=value,
            dbu_um=dbu_um,
        )
    return int(integral)


def _same_decimal(left: Any, right: Any) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, ValueError):
        return False


def _point_on_edge(point: list[int], edge: list[list[int]]) -> bool:
    (x, y), ((x1, y1), (x2, y2)) = point, edge
    if x1 == x2:
        return x == x1 and min(y1, y2) <= y <= max(y1, y2)
    if y1 == y2:
        return y == y1 and min(x1, x2) <= x <= max(x1, x2)
    return False


def _validated_ruler(ruler: Mapping[str, Any]) -> tuple[list[list[int]], list[dict[str, Any]]]:
    raw_points = ruler.get("ruler_dbu")
    canonical_points = canonical_edge(raw_points, field="ruler_dbu")
    points = (
        canonical_points
        if list(raw_points[0]) == canonical_points[0]
        else [canonical_points[1], canonical_points[0]]
    )
    if points[0][0] != points[1][0] and points[0][1] != points[1][1]:
        _fail("NON_MANHATTAN_PCELLIZER_RECIPE_RULER", "Recipe ruler must be Manhattan.")
    derived_orientation = "vertical" if points[0][0] == points[1][0] else "horizontal"
    derived_length = abs(points[1][0] - points[0][0]) + abs(points[1][1] - points[0][1])
    if ruler.get("orientation") != derived_orientation or ruler.get("length_dbu") != derived_length:
        _fail(
            "PCELLIZER_RECIPE_RULER_METADATA_MISMATCH",
            "Ruler orientation or length does not match its exact endpoints.",
            derived_orientation=derived_orientation,
            derived_length_dbu=derived_length,
        )
    bindings = ruler.get("endpoint_bindings")
    if not isinstance(bindings, list) or len(bindings) != 2:
        _fail(
            "PCELLIZER_RECIPE_TWO_BINDINGS_REQUIRED",
            "A single-shape recipe requires exactly two exact edge bindings.",
        )
    normalized_bindings: list[dict[str, Any]] = []
    for endpoint_index, (point, raw_binding) in enumerate(zip(points, bindings)):
        if not isinstance(raw_binding, Mapping) or raw_binding.get("endpoint_dbu") != point:
            _fail(
                "PCELLIZER_RECIPE_ENDPOINT_MISMATCH",
                "Ruler endpoint and edge binding coordinates differ.",
                endpoint_index=endpoint_index,
            )
        normalized_bindings.append(immutable_json_copy(raw_binding))
    return points, normalized_bindings


def _selected_direct_box(capture: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ruler = capture.get("ruler")
    selected = capture.get("selected_shapes")
    endpoint_manifests = capture.get("endpoint_manifests")
    if not isinstance(ruler, Mapping) or not isinstance(selected, list):
        _fail("INVALID_PCELLIZER_RECIPE_CAPTURE", "Capture is missing ruler or selected-shape data.")
    if not isinstance(endpoint_manifests, list) or len(endpoint_manifests) != 2:
        _fail(
            "PCELLIZER_RECIPE_TWO_ENDPOINTS_REQUIRED",
            "A single-shape recipe requires exactly two endpoint manifests.",
        )
    if len(selected) != 1:
        _fail(
            "PCELLIZER_RECIPE_EXACTLY_ONE_SHAPE_REQUIRED",
            "The first authoring slice requires exactly one selected direct shape.",
            selected_shape_count=len(selected),
        )
    points, bindings = _validated_ruler(ruler)
    selection_indices = [binding.get("selection_index") for binding in bindings]
    if selection_indices != [0, 0]:
        _fail(
            "PCELLIZER_RECIPE_SINGLE_SHAPE_REQUIRED",
            "Both ruler endpoints must bind opposite edges of the same selected shape.",
            selection_indices=selection_indices,
        )
    selection_index = selection_indices[0]
    if selection_index < 0 or selection_index >= len(selected):
        _fail("INVALID_PCELLIZER_RECIPE_SELECTION", "Ruler binding references a missing selected shape.")
    shape = selected[selection_index]
    if not isinstance(shape, Mapping) or not isinstance(shape.get("shape_identity"), Mapping):
        _fail("INVALID_PCELLIZER_RECIPE_SELECTION", "Selected shape has no exact shape identity.")
    identity = shape["shape_identity"]
    geometry = identity.get("geometry")
    if not isinstance(geometry, Mapping) or geometry.get("kind") != "box":
        _fail(
            "UNSUPPORTED_PCELLIZER_RECIPE_GEOMETRY",
            "The first authoring slice supports one direct box only.",
            geometry_kind=geometry.get("kind") if isinstance(geometry, Mapping) else None,
        )
    top_edges = shape.get("top_edges_dbu")
    if not isinstance(top_edges, list) or len(top_edges) != 4:
        _fail(
            "INVALID_PCELLIZER_RECIPE_SELECTION",
            "Selected box must expose exactly four ordered top-coordinate edges.",
        )
    for endpoint_index, (binding, endpoint_record) in enumerate(zip(bindings, endpoint_manifests)):
        edge_index = binding.get("edge_index")
        if not isinstance(edge_index, int) or edge_index < 0 or edge_index >= len(top_edges):
            _fail(
                "INVALID_PCELLIZER_RECIPE_EDGE_INDEX",
                "Ruler binding references a missing selected-shape edge.",
                endpoint_index=endpoint_index,
            )
        captured_edge = canonical_edge(top_edges[edge_index], field="top_edges_dbu[]")
        bound_edge = canonical_edge(binding.get("edge_dbu"), field="edge_dbu")
        if captured_edge != bound_edge or not _point_on_edge(points[endpoint_index], bound_edge):
            _fail(
                "PCELLIZER_RECIPE_EDGE_IDENTITY_MISMATCH",
                "Bound edge is not the selected shape edge touched by the ruler endpoint.",
                endpoint_index=endpoint_index,
            )
        if (
            not isinstance(endpoint_record, Mapping)
            or endpoint_record.get("endpoint_index") != endpoint_index
            or endpoint_record.get("selection_index") != selection_index
        ):
            _fail(
                "PCELLIZER_RECIPE_ENDPOINT_MISMATCH",
                "Endpoint manifest and ruler binding select different geometry.",
                endpoint_index=endpoint_index,
            )
        manifest = endpoint_record.get("manifest")
        if not isinstance(manifest, Mapping):
            _fail("INVALID_PCELLIZER_RECIPE_ENDPOINT", "Endpoint manifest is missing.")
        if manifest.get("shape_fingerprint_sha256") != identity.get("shape_fingerprint_sha256"):
            _fail(
                "PCELLIZER_RECIPE_SHAPE_IDENTITY_MISMATCH",
                "Endpoint and selected-shape fingerprints differ.",
                endpoint_index=endpoint_index,
            )
        if manifest.get("edge_dbu") != bound_edge:
            _fail(
                "PCELLIZER_RECIPE_EDGE_IDENTITY_MISMATCH",
                "Endpoint and ruler edge identities differ.",
                endpoint_index=endpoint_index,
            )
        if (
            manifest.get("layer") != shape.get("layer")
            or manifest.get("datatype") != shape.get("datatype")
            or manifest.get("shape_ordinal") != identity.get("shape_ordinal")
            or manifest.get("duplicate_geometry_count")
            != identity.get("duplicate_geometry_count")
            or manifest.get("occurrence_path") != shape.get("occurrence_path")
        ):
            _fail(
                "PCELLIZER_RECIPE_SELECTION_MANIFEST_MISMATCH",
                "Selection manifest does not identify the captured direct shape and occurrence.",
                endpoint_index=endpoint_index,
            )
    return immutable_json_copy(shape), [immutable_json_copy(item) for item in bindings]


def _box_axis(shape: Mapping[str, Any], bindings: list[dict[str, Any]], ruler: Mapping[str, Any]) -> tuple[str, int]:
    edge_indices = {binding.get("edge_index") for binding in bindings}
    if edge_indices == {1, 3}:
        local_axis = "x"
        dimension_index = (0, 2)
    elif edge_indices == {0, 2}:
        local_axis = "y"
        dimension_index = (1, 3)
    else:
        _fail(
            "PCELLIZER_RECIPE_OPPOSITE_BOX_EDGES_REQUIRED",
            "The ruler must bind the two opposite edges of one box.",
            edge_indices=sorted(item for item in edge_indices if isinstance(item, int)),
        )
    bbox = shape["shape_identity"]["geometry"].get("bbox_dbu")
    if not isinstance(bbox, list) or len(bbox) != 4:
        _fail("INVALID_PCELLIZER_RECIPE_BOX", "Selected box has no canonical bbox_dbu.")
    nominal_span = int(bbox[dimension_index[1]]) - int(bbox[dimension_index[0]])
    if nominal_span <= 0 or ruler.get("length_dbu") != nominal_span:
        _fail(
            "PCELLIZER_RECIPE_RULER_SPAN_MISMATCH",
            "The ruler must span the selected box dimension exactly.",
            box_span_dbu=nominal_span,
            ruler_length_dbu=ruler.get("length_dbu"),
        )
    return local_axis, nominal_span


def compile_pcellizer_single_shape_recipe(
    *, package_dir: str, parameter_intent: Mapping[str, Any]
) -> dict[str, Any]:
    """Compile a fail-closed P1 recipe without editing or flattening geometry."""

    package = inspect_pcellizer_snapshot_package(package_dir=package_dir)
    intent = validate_pcellizer_parameter_intent(parameter_intent)
    manifest = package["manifest"]
    capture = package["capture"]
    package_hash = manifest["snapshot_package_sha256"]
    if intent["snapshot_package_sha256"] != package_hash:
        _fail(
            "PCELLIZER_RECIPE_SNAPSHOT_MISMATCH",
            "Parameter intent is bound to a different snapshot package.",
            expected_snapshot_package_sha256=package_hash,
            actual_snapshot_package_sha256=intent["snapshot_package_sha256"],
        )
    source_dbu = capture["source"]["dbu_um"]
    if not _same_decimal(intent["dbu_um"], source_dbu):
        _fail(
            "PCELLIZER_RECIPE_DBU_MISMATCH",
            "Parameter intent DBU differs from the captured source DBU.",
            intent_dbu_um=intent["dbu_um"],
            source_dbu_um=source_dbu,
        )
    if capture.get("scope") != "current_occurrence" or capture.get("flattening_performed") is not False:
        _fail(
            "UNSUPPORTED_PCELLIZER_RECIPE_SCOPE",
            "The first authoring slice requires a non-flattened current-occurrence capture.",
            scope=capture.get("scope"),
        )
    shape, bindings = _selected_direct_box(capture)
    local_axis, nominal_span_dbu = _box_axis(shape, bindings, capture["ruler"])
    nominal_intent_dbu = _um_to_dbu(intent["nominal_um"], source_dbu, field="nominal_um")
    if nominal_intent_dbu != nominal_span_dbu:
        _fail(
            "PCELLIZER_RECIPE_NOMINAL_MISMATCH",
            "The confirmed nominal parameter must equal the captured ruler span.",
            captured_nominal_dbu=nominal_span_dbu,
            intent_nominal_dbu=nominal_intent_dbu,
        )
    quantized = {
        key.replace("_um", "_dbu"): _um_to_dbu(intent[key], source_dbu, field=key)
        for key in ("min_um", "nominal_um", "max_um", "step_um", "manufacturing_grid_um")
    }
    if intent["anchor_policy"] == "center_fixed":
        spans = [quantized["min_dbu"], quantized["nominal_dbu"], quantized["max_dbu"]]
        if quantized["step_dbu"] % 2 or len({span % 2 for span in spans}) != 1:
            _fail(
                "PCELLIZER_CENTER_ANCHOR_HALF_DBU",
                "center_fixed requires every reachable box span to preserve one exact integer/half-DBU center.",
                spans_dbu=spans,
                step_dbu=quantized["step_dbu"],
            )
    occurrence_path = shape.get("occurrence_path")
    if not isinstance(occurrence_path, Mapping) or occurrence_path.get("authoring_supported") is not True:
        _fail(
            "UNSUPPORTED_PCELLIZER_RECIPE_OCCURRENCE",
            "The selected occurrence contains an unsupported hierarchy transform or representation.",
        )
    if occurrence_path.get("leaf_cell") != shape.get("cell"):
        _fail(
            "PCELLIZER_RECIPE_LEAF_CELL_MISMATCH",
            "Selected direct-shape cell differs from the occurrence-path leaf cell.",
            selected_cell=shape.get("cell"),
            leaf_cell=occurrence_path.get("leaf_cell"),
        )
    operation = {
        "operator": "resize_direct_box_between_captured_edges",
        "local_axis": local_axis,
        "endpoint_edge_indices": [binding["edge_index"] for binding in bindings],
        "endpoint_edges_top_dbu": [binding["edge_dbu"] for binding in bindings],
        "anchor_policy": intent["anchor_policy"],
        "dependency_policy": "fixed_unselected_geometry",
    }
    core = {
        "schema_version": RECIPE_SCHEMA_VERSION,
        "kind": RECIPE_KIND,
        "snapshot_package_sha256": package_hash,
        "parameter_capture_sha256": capture["parameter_capture_sha256"],
        "parameter_intent_sha256": intent["parameter_intent_sha256"],
        "target": {
            "cell": shape.get("cell"),
            "occurrence_path": occurrence_path,
            "layer": shape.get("layer"),
            "datatype": shape.get("datatype"),
            "shape_identity": shape["shape_identity"],
            "scope": "current_occurrence",
        },
        "parameter": {**intent, **quantized},
        "operations": [operation],
        "hierarchy_strategy": {
            "flattening_performed": False,
            "preserve_unselected_occurrences": True,
            "occurrence_specific_identity_wrapper_required": bool(occurrence_path.get("depth", 0)),
        },
        "verification_required": [
            "nominal_fresh_reload_xor_zero",
            "min_max_target_dimension",
            "unselected_geometry_unchanged",
            "hierarchy_preserved",
        ],
        "source_geometry_modified": False,
        "production_ready": False,
    }
    return {**core, "pcellizer_recipe_sha256": canonical_sha256(core)}


def validate_pcellizer_single_shape_recipe(recipe: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a compiled recipe before it crosses into batch generation."""

    if not isinstance(recipe, Mapping):
        _fail("INVALID_PCELLIZER_RECIPE", "PCellizer recipe must be an object.")
    document = immutable_json_copy(recipe)
    recorded_hash = document.pop("pcellizer_recipe_sha256", None)
    if not isinstance(recorded_hash, str) or not SHA256_PATTERN.fullmatch(recorded_hash):
        _fail("INVALID_PCELLIZER_RECIPE_HASH", "Recipe requires a lowercase SHA-256 identity.")
    expected_hash = canonical_sha256(document)
    if recorded_hash != expected_hash:
        _fail(
            "PCELLIZER_RECIPE_HASH_MISMATCH",
            "Recipe content changed after compilation.",
            expected_sha256=expected_hash,
            actual_sha256=recorded_hash,
        )
    required = {
        "schema_version",
        "kind",
        "snapshot_package_sha256",
        "parameter_capture_sha256",
        "parameter_intent_sha256",
        "target",
        "parameter",
        "operations",
        "hierarchy_strategy",
        "verification_required",
        "source_geometry_modified",
        "production_ready",
    }
    if set(document) != required:
        _fail(
            "INVALID_PCELLIZER_RECIPE_SCHEMA",
            "Recipe keys do not match the single-shape schema.",
            missing_keys=sorted(required.difference(document)),
            unexpected_keys=sorted(set(document).difference(required)),
        )
    if (
        document.get("schema_version") != RECIPE_SCHEMA_VERSION
        or document.get("kind") != RECIPE_KIND
        or document.get("source_geometry_modified") is not False
        or document.get("production_ready") is not False
        or not isinstance(document.get("operations"), list)
        or len(document["operations"]) != 1
        or document["operations"][0].get("operator")
        != "resize_direct_box_between_captured_edges"
        or document["operations"][0].get("dependency_policy")
        != "fixed_unselected_geometry"
    ):
        _fail(
            "INVALID_PCELLIZER_RECIPE_SCHEMA",
            "Recipe is not the supported non-destructive single-box draft.",
        )
    parameter = document.get("parameter")
    target = document.get("target")
    operation = document["operations"][0]
    required_parameter_keys = {
        "parameter_name",
        "dbu_um",
        "min_dbu",
        "nominal_dbu",
        "max_dbu",
        "step_dbu",
        "manufacturing_grid_dbu",
    }
    required_target_keys = {
        "cell",
        "occurrence_path",
        "layer",
        "datatype",
        "shape_identity",
        "scope",
    }
    required_operation_keys = {
        "operator",
        "local_axis",
        "endpoint_edge_indices",
        "endpoint_edges_top_dbu",
        "anchor_policy",
        "dependency_policy",
    }
    if (
        not isinstance(parameter, Mapping)
        or not required_parameter_keys.issubset(parameter)
        or not isinstance(target, Mapping)
        or not required_target_keys.issubset(target)
        or not isinstance(target.get("occurrence_path"), Mapping)
        or not isinstance(target.get("shape_identity"), Mapping)
        or not required_operation_keys.issubset(operation)
        or operation.get("local_axis") not in {"x", "y"}
        or operation.get("anchor_policy") not in {"p1_fixed", "p2_fixed", "center_fixed"}
    ):
        _fail(
            "INVALID_PCELLIZER_RECIPE_SCHEMA",
            "Recipe parameter, target, or operation structure is incomplete.",
        )
    integer_fields = (
        "min_dbu",
        "nominal_dbu",
        "max_dbu",
        "step_dbu",
        "manufacturing_grid_dbu",
    )
    if any(
        isinstance(parameter.get(field), bool)
        or not isinstance(parameter.get(field), int)
        or parameter[field] <= 0
        for field in integer_fields
    ) or not (
        parameter["min_dbu"] <= parameter["nominal_dbu"] <= parameter["max_dbu"]
    ):
        _fail(
            "INVALID_PCELLIZER_RECIPE_SCHEMA",
            "Recipe DBU bounds, step, and grid must be positive ordered integers.",
        )
    document["pcellizer_recipe_sha256"] = recorded_hash
    return immutable_json_copy(document)
