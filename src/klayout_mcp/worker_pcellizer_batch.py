"""KLayout worker for non-flattening PCellizer split-batch generation."""

import hashlib
import os
import shutil

import pya

from .errors import AnalysisError
from .pcellizer_klayout_capture import serialize_klayout_shape
from .worker_common import _find_layer, _select_top
from .worker_compare import _compare_layout_objects
from .worker_protocol import worker_error as _error


def _fail(code, message, **details):
    raise AnalysisError(
        code=code,
        message=message,
        details={**details, "production_ready": False},
        next_action="Inspect the batch recipe hierarchy gate and regenerate from the immutable snapshot.",
    )


def _clone_direct_cell(layout, source, name):
    if layout.cell(name) is not None:
        _fail("PCELLIZER_BATCH_CLONE_NAME_COLLISION", "Deterministic clone cell name already exists.", cell=name)
    clone = layout.create_cell(name)
    clone.copy_shapes(source)
    clone.copy_instances(source)
    return clone


def _target_shape(cell, layer_index, target_identity):
    ordinal = int(target_identity["shape_ordinal"])
    shapes = list(cell.each_shape(layer_index))
    if ordinal < 0 or ordinal >= len(shapes):
        _fail("PCELLIZER_BATCH_TARGET_SHAPE_MISSING", "Captured direct-shape ordinal is no longer present.")
    shape = shapes[ordinal]
    geometry = serialize_klayout_shape(shape)
    if geometry != target_identity["geometry"]:
        _fail(
            "PCELLIZER_BATCH_TARGET_SHAPE_STALE",
            "Captured direct box geometry differs after fresh source reload.",
            expected_geometry=target_identity["geometry"],
            actual_geometry=geometry,
        )
    if not shape.is_box():
        _fail("PCELLIZER_BATCH_BOX_REQUIRED", "MVP batch writer supports a direct box only.")
    return shape


def _resized_box(original, operation, span_dbu):
    box = original.box
    axis = operation["local_axis"]
    edge_indices = operation["endpoint_edge_indices"]
    anchor = operation["anchor_policy"]
    if axis == "x":
        low, high = box.left, box.right
        low_edge, high_edge = 3, 1
    elif axis == "y":
        low, high = box.bottom, box.top
        low_edge, high_edge = 0, 2
    else:
        _fail("INVALID_PCELLIZER_BATCH_AXIS", "Recipe local axis must be x or y.")
    if set(edge_indices) != {low_edge, high_edge}:
        _fail("INVALID_PCELLIZER_BATCH_EDGES", "Recipe edges do not match the local box axis.")
    if anchor == "center_fixed":
        coordinate_sum = low + high
        if (coordinate_sum - span_dbu) % 2:
            _fail("PCELLIZER_BATCH_CENTER_HALF_DBU", "Requested center-fixed span cannot preserve the exact center.")
        new_low = (coordinate_sum - span_dbu) // 2
        new_high = new_low + span_dbu
    else:
        endpoint_index = 0 if anchor == "p1_fixed" else 1
        fixed_edge = edge_indices[endpoint_index]
        if fixed_edge == low_edge:
            new_low, new_high = low, low + span_dbu
        elif fixed_edge == high_edge:
            new_low, new_high = high - span_dbu, high
        else:
            _fail("INVALID_PCELLIZER_BATCH_ANCHOR", "Anchor endpoint is not a box boundary.")
    if new_high <= new_low:
        _fail("INVALID_PCELLIZER_BATCH_SPAN", "Requested box span must remain positive.")
    if axis == "x":
        return pya.Box(new_low, box.bottom, new_high, box.top)
    return pya.Box(box.left, new_low, box.right, new_high)


def _specialize_occurrence(layout, top, recipe, span_dbu, variant_key):
    target = recipe["target"]
    path = target["occurrence_path"]
    segments = path["segments"]
    for segment in segments:
        if segment["array"]["is_array"]:
            _fail(
                "PCELLIZER_BATCH_ARRAY_PARTITION_APPROVAL_REQUIRED",
                "A single array member cannot be specialized without changing hierarchy representation.",
                occurrence_id=path["occurrence_id"],
            )
    layer_index = _find_layer(layout, int(target["layer"]), int(target["datatype"]))
    if layer_index is None:
        _fail("PCELLIZER_BATCH_TARGET_LAYER_MISSING", "Captured target layer is missing after reload.")
    leaf_source = layout.cell(str(target["cell"]))
    if leaf_source is None or path["leaf_cell"] != leaf_source.name:
        _fail("PCELLIZER_BATCH_LEAF_MISMATCH", "Captured occurrence leaf cell is missing or changed.")
    clone_prefix = "PZ%s" % variant_key[:10].upper()
    if segments:
        leaf_target = _clone_direct_cell(layout, leaf_source, "%sL%02d" % (clone_prefix, len(segments)))
    else:
        leaf_target = leaf_source
    shape = _target_shape(leaf_target, layer_index, target["shape_identity"])
    shape.box = _resized_box(shape, recipe["operations"][0], int(span_dbu))
    specialized_leaf_name = leaf_target.name

    child_target = leaf_target
    for index in range(len(segments) - 1, -1, -1):
        segment = segments[index]
        parent_source = layout.cell(str(segment["parent_cell"]))
        if parent_source is None:
            _fail("PCELLIZER_BATCH_PARENT_MISSING", "Captured hierarchy parent is missing.", segment_index=index)
        parent_target = top if index == 0 else _clone_direct_cell(
            layout, parent_source, "%sP%02d" % (clone_prefix, index)
        )
        instances = list(parent_target.each_inst())
        ordinal = int(segment["instance_ordinal"])
        if ordinal < 0 or ordinal >= len(instances):
            _fail("PCELLIZER_BATCH_INSTANCE_MISSING", "Captured instance ordinal is missing.", segment_index=index)
        instance = instances[ordinal]
        if instance.cell.name != segment["child_cell"]:
            _fail(
                "PCELLIZER_BATCH_INSTANCE_STALE",
                "Captured parent-child hierarchy differs after fresh reload.",
                segment_index=index,
                expected_child=segment["child_cell"],
                actual_child=instance.cell.name,
            )
        instance.cell_index = child_target.cell_index()
        child_target = parent_target
    return specialized_leaf_name, layer_index


def _hash_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _generate_unique(source_path, top_name, recipe, span_dbu, variant_key, output_path):
    layout = pya.Layout()
    layout.read(source_path)
    original_top_names = {cell.name for cell in layout.top_cells()}
    top, top_error = _select_top(layout, top_name)
    if top_error:
        _fail(top_error["code"], top_error["message"], **top_error.get("details", {}))
    source_layout = pya.Layout()
    source_layout.read(source_path)
    source_top, source_error = _select_top(source_layout, top_name)
    if source_error:
        _fail(source_error["code"], source_error["message"], **source_error.get("details", {}))
    specialized_leaf_name, layer_index = _specialize_occurrence(
        layout, top, recipe, span_dbu, variant_key
    )
    while True:
        disposable = [cell for cell in layout.top_cells() if cell.name not in original_top_names]
        if not disposable:
            break
        layout.delete_cell(disposable[0].cell_index())
    temporary = output_path + ".tmp.gds"
    layout.write(temporary)
    os.replace(temporary, output_path)

    fresh = pya.Layout()
    fresh.read(output_path)
    fresh_top, fresh_error = _select_top(fresh, top_name)
    if fresh_error:
        _fail(fresh_error["code"], fresh_error["message"], **fresh_error.get("details", {}))
    fresh_leaf = fresh.cell(specialized_leaf_name)
    if fresh_leaf is None:
        _fail("PCELLIZER_BATCH_SPECIALIZED_LEAF_MISSING", "Specialized leaf is absent after fresh reload.")
    fresh_layer = _find_layer(fresh, int(recipe["target"]["layer"]), int(recipe["target"]["datatype"]))
    if fresh_layer is None:
        _fail("PCELLIZER_BATCH_TARGET_LAYER_MISSING", "Target layer is absent after fresh reload.")
    fresh_shapes = list(fresh_leaf.each_shape(fresh_layer))
    fresh_ordinal = int(recipe["target"]["shape_identity"]["shape_ordinal"])
    if fresh_ordinal < 0 or fresh_ordinal >= len(fresh_shapes):
        _fail("PCELLIZER_BATCH_TARGET_SHAPE_MISSING", "Target shape ordinal is absent after fresh reload.")
    shape = fresh_shapes[fresh_ordinal]
    if not shape.is_box():
        _fail("PCELLIZER_BATCH_BOX_REQUIRED", "Fresh-reloaded target is not a direct box.")
    box = shape.box
    actual_span = box.width() if recipe["operations"][0]["local_axis"] == "x" else box.height()
    if actual_span != int(span_dbu):
        _fail(
            "PCELLIZER_BATCH_DIMENSION_VERIFY_FAILED",
            "Fresh-reloaded target dimension differs from the split value.",
            expected_span_dbu=int(span_dbu),
            actual_span_dbu=int(actual_span),
        )
    nominal = int(recipe["parameter"]["nominal_dbu"])
    nominal_xor_clean = None
    if int(span_dbu) == nominal:
        comparison = _compare_layout_objects(fresh, fresh_top, source_layout, source_top)
        nominal_xor_clean = bool(comparison["equivalent"])
        if not nominal_xor_clean:
            failing_layers = [
                {
                    "layer": item["layer"],
                    "datatype": item["datatype"],
                    "xor_area_um2": item["xor_area_um2"],
                }
                for item in comparison.get("layers", [])
                if not item.get("geometry_xor_clean")
            ]
            _fail(
                "PCELLIZER_BATCH_NOMINAL_XOR_FAILED",
                "Nominal specialized hierarchy is not semantically equivalent to the source.",
                failing_layers=failing_layers,
                comparison=comparison,
            )
    return {
        "layout_sha256": _hash_file(output_path),
        "size_bytes": os.path.getsize(output_path),
        "top_cell": fresh_top.name,
        "dbu_um": float(fresh.dbu),
        "target_span_dbu": int(actual_span),
        "specialized_leaf_cell": specialized_leaf_name,
        "fresh_reload_verified": True,
        "nominal_xor_clean": nominal_xor_clean,
        "flattening_performed": False,
    }


def generate_pcellizer_batch(request):
    """Generate all unique variants, then duplicate exact bytes for repeated rows."""

    try:
        source_path = os.path.abspath(str(request["layout_path"]))
        output_dir = os.path.abspath(str(request["output_dir"]))
        recipe = request["recipe"]
        rows = request["rows"]
        if not os.path.isfile(source_path) or not os.path.isdir(output_dir):
            _fail("PCELLIZER_BATCH_PATH_MISSING", "Source or staging output directory is missing.")
        outputs = []
        generated = {}
        parameter_name = recipe["parameter"]["parameter_name"]
        nominal_span = int(recipe["parameter"]["nominal_dbu"])
        nominal_in_rows = any(
            int(row["parameters_dbu"][parameter_name]) == nominal_span for row in rows
        )
        nominal_xor_verified = False
        if not nominal_in_rows:
            nominal_path = os.path.join(output_dir, ".nominal_verify.gds")
            try:
                nominal_evidence = _generate_unique(
                    source_path,
                    recipe["target"]["occurrence_path"]["top_cell"],
                    recipe,
                    nominal_span,
                    "0" * 64,
                    nominal_path,
                )
                nominal_xor_verified = nominal_evidence["nominal_xor_clean"] is True
            finally:
                if os.path.exists(nominal_path):
                    os.remove(nominal_path)
        for row in rows:
            filename = str(row["output_filename"])
            if os.path.basename(filename) != filename or not filename.lower().endswith(".gds"):
                _fail("UNSAFE_PCELLIZER_OUTPUT_FILENAME", "Worker received an unsafe output filename.")
            output_path = os.path.abspath(os.path.join(output_dir, filename))
            if os.path.dirname(output_path) != output_dir:
                _fail("UNSAFE_PCELLIZER_OUTPUT_FILENAME", "Output escaped the staging directory.")
            variant_key = row["variant_key"]
            span_dbu = row["parameters_dbu"][recipe["parameter"]["parameter_name"]]
            if variant_key not in generated:
                evidence = _generate_unique(
                    source_path,
                    recipe["target"]["occurrence_path"]["top_cell"],
                    recipe,
                    span_dbu,
                    variant_key,
                    output_path,
                )
                generated[variant_key] = {"path": output_path, "evidence": evidence}
                reused = False
            else:
                shutil.copyfile(generated[variant_key]["path"], output_path)
                evidence = dict(generated[variant_key]["evidence"])
                if _hash_file(output_path) != evidence["layout_sha256"]:
                    _fail("PCELLIZER_BATCH_REUSE_COPY_FAILED", "Repeated variant copy hash differs.")
                reused = True
            outputs.append(
                {
                    "split_id": row["split_id"],
                    "output_filename": filename,
                    "variant_key": variant_key,
                    "reused_identical_variant": reused,
                    **evidence,
                }
            )
            if evidence["nominal_xor_clean"] is True:
                nominal_xor_verified = True
        return {
            "ok": True,
            "operation": "generate_pcellizer_batch",
            "outputs": outputs,
            "summary": {
                "row_count": len(outputs),
                "unique_variant_count": len(generated),
                "all_fresh_reload_verified": all(item["fresh_reload_verified"] for item in outputs),
                "nominal_xor_verified": nominal_xor_verified,
                "flattening_performed": False,
            },
            "klayout_version": pya.Application.instance().version(),
            "production_ready": False,
        }
    except AnalysisError as exc:
        return exc.to_result()
    except Exception as exc:
        return _error(
            "PCELLIZER_BATCH_GENERATION_FAILED",
            "KLayout could not generate the split batch.",
            {"error_type": type(exc).__name__, "error": str(exc)},
            "Inspect the target shape, hierarchy path, and installed KLayout version.",
        )
