"""Read-only, transform-aware hierarchy inventory for PCellizer H0."""

import os

import pya

from .errors import AnalysisError
from .pcellizer_contract import normalize_occurrence_path
from .worker_common import _optional_box_um, _select_top
from .worker_protocol import worker_error as _error
from .workflow_manifest import canonical_sha256


def _transform_record(transform):
    return {
        "displacement_dbu": [int(transform.disp.x), int(transform.disp.y)],
        "angle_degrees": float(transform.angle),
        "mirror": bool(transform.is_mirror()),
        "magnification": float(transform.mag),
    }


def _member_transform(base, a_vector, b_vector, column, row):
    return pya.ICplxTrans(
        float(base.mag),
        float(base.angle),
        bool(base.is_mirror()),
        int(base.disp.x + column * a_vector.x + row * b_vector.x),
        int(base.disp.y + column * a_vector.y + row * b_vector.y),
    )


def inventory_pcellizer_hierarchy(request):
    """Expand occurrence identity only; do not flatten or modify the source layout."""

    layout_path = os.path.abspath(str(request.get("layout_path", "")))
    if not os.path.isfile(layout_path):
        return _error(
            "LAYOUT_NOT_FOUND",
            "PCellizer source layout does not exist.",
            {"layout_path": layout_path},
            "Provide an existing GDS or OAS path.",
        )
    max_occurrences = request.get("max_occurrences", 100000)
    if (
        isinstance(max_occurrences, bool)
        or not isinstance(max_occurrences, int)
        or max_occurrences <= 0
    ):
        return _error(
            "INVALID_PCELLIZER_OCCURRENCE_LIMIT",
            "max_occurrences must be a positive integer.",
            {"max_occurrences": max_occurrences},
            "Use a positive limit sized for the source hierarchy.",
        )

    layout = pya.Layout()
    try:
        layout.read(layout_path)
    except Exception as exc:
        return _error(
            "LAYOUT_READ_FAILED",
            "KLayout could not read the PCellizer source layout.",
            {"layout_path": layout_path, "error": str(exc)},
            "Check the layout format and file integrity.",
        )
    top, top_error = _select_top(layout, request.get("top_cell"))
    if top_error:
        return top_error

    occurrences = []
    source_arrays = {}
    cell_occurrence_counts = {top.name: 1}

    def walk(parent_cell, path_segments, accumulated_transform, ancestry):
        instances = list(parent_cell.each_inst())
        for ordinal, instance in enumerate(instances):
            cell_inst = instance.cell_inst
            child = layout.cell(cell_inst.cell_index)
            if child.cell_index() in ancestry:
                raise AnalysisError(
                    code="PCELLIZER_HIERARCHY_CYCLE",
                    message="PCellizer source hierarchy contains a recursive cell cycle.",
                    details={"cell": child.name, "parent": parent_cell.name},
                    next_action="Remove the hierarchy cycle before PCell authoring.",
                )

            is_regular = bool(cell_inst.is_regular_array())
            member_count = int(cell_inst.size())
            columns = int(cell_inst.na) if is_regular else 1
            rows = int(cell_inst.nb) if is_regular else 1
            a_vector = cell_inst.a if is_regular else pya.Vector(0, 0)
            b_vector = cell_inst.b if is_regular else pya.Vector(0, 0)
            base = cell_inst.cplx_trans
            array_payload = {
                "parent_path_id": canonical_sha256(
                    {"top_cell": top.name, "segments": path_segments}
                ),
                "parent_cell": parent_cell.name,
                "child_cell": child.name,
                "instance_ordinal": ordinal,
                "columns": columns,
                "rows": rows,
                "a_vector_dbu": [int(a_vector.x), int(a_vector.y)],
                "b_vector_dbu": [int(b_vector.x), int(b_vector.y)],
                "regular": is_regular,
                "member_count": member_count,
            }
            source_array_id = canonical_sha256(array_payload)
            if source_array_id not in source_arrays:
                source_arrays[source_array_id] = {
                    "source_array_id": source_array_id,
                    **array_payload,
                    "authoring_supported": is_regular or member_count == 1,
                    "authoring_blockers": (
                        [] if is_regular or member_count == 1 else ["non_regular_iterated_instance"]
                    ),
                }

            if is_regular:
                member_specs = (
                    (
                        column,
                        row,
                        _member_transform(base, a_vector, b_vector, column, row),
                    )
                    for row in range(rows)
                    for column in range(columns)
                )
            else:
                member_specs = (
                    (index, 0, transform)
                    for index, transform in enumerate(cell_inst.each_cplx_trans())
                )

            for column, row, member_transform in member_specs:
                if len(occurrences) >= max_occurrences:
                    raise AnalysisError(
                        code="PCELLIZER_OCCURRENCE_LIMIT_EXCEEDED",
                        message="Expanded hierarchy exceeds the configured occurrence limit.",
                        details={
                            "max_occurrences": max_occurrences,
                            "parent_cell": parent_cell.name,
                            "child_cell": child.name,
                        },
                        next_action="Increase max_occurrences after checking expected array size.",
                    )
                blockers = (
                    []
                    if is_regular or member_count == 1
                    else ["non_regular_iterated_instance"]
                )
                segment = {
                    "parent_cell": parent_cell.name,
                    "child_cell": child.name,
                    "instance_ordinal": ordinal,
                    "transform": _transform_record(member_transform),
                    "array": {
                        "columns": columns if is_regular else member_count,
                        "rows": rows,
                        "column": column,
                        "row": row,
                        "a_vector_dbu": [int(a_vector.x), int(a_vector.y)],
                        "b_vector_dbu": [int(b_vector.x), int(b_vector.y)],
                        "regular": is_regular or member_count == 1,
                    },
                    "authoring_blockers": blockers,
                }
                occurrence_path = normalize_occurrence_path(
                    top_cell=top.name, segments=path_segments + [segment]
                )
                accumulated = accumulated_transform * member_transform
                occurrence_authoring_supported = bool(
                    occurrence_path["authoring_supported"]
                )
                occurrences.append(
                    {
                        "occurrence_id": occurrence_path["occurrence_id"],
                        "source_array_id": source_array_id,
                        "occurrence_path": occurrence_path,
                        "parent_cell": parent_cell.name,
                        "cell": child.name,
                        "cell_index": int(child.cell_index()),
                        "depth": occurrence_path["depth"],
                        "array_member": {"column": column, "row": row},
                        "local_transform": occurrence_path["segments"][-1][
                            "transform"
                        ],
                        "accumulated_transform": _transform_record(accumulated),
                        "bbox_um": _optional_box_um(accumulated * child.bbox(), layout.dbu),
                        "direct_instance_count": sum(1 for _ in child.each_inst()),
                        "is_pcell_variant": bool(child.is_pcell_variant()),
                        "authoring_supported": occurrence_authoring_supported,
                        "authoring_blockers": sorted(
                            {
                                reason
                                for path_segment in occurrence_path["segments"]
                                for reason in (
                                    path_segment["authoring_blockers"]
                                    + path_segment["transform"]["unsupported_reasons"]
                                )
                            }
                        ),
                    }
                )
                cell_occurrence_counts[child.name] = (
                    cell_occurrence_counts.get(child.name, 0) + 1
                )
                walk(
                    child,
                    occurrence_path["segments"],
                    accumulated,
                    ancestry | {child.cell_index()},
                )

    try:
        walk(top, [], pya.ICplxTrans(), {top.cell_index()})
    except AnalysisError as exc:
        return exc.to_result()
    except Exception as exc:
        return _error(
            "PCELLIZER_HIERARCHY_INVENTORY_FAILED",
            "KLayout could not inventory the PCellizer hierarchy.",
            {"error_type": type(exc).__name__, "error": str(exc)},
            "Inspect the unsupported instance representation and KLayout version.",
        )

    cell_definitions = []
    for cell in layout.each_cell():
        cell_definitions.append(
            {
                "name": cell.name,
                "cell_index": int(cell.cell_index()),
                "bbox_um": _optional_box_um(cell.bbox(), layout.dbu),
                "direct_instance_count": sum(1 for _ in cell.each_inst()),
                "occurrence_count_from_top": cell_occurrence_counts.get(cell.name, 0),
                "is_pcell_variant": bool(cell.is_pcell_variant()),
            }
        )
    cell_definitions.sort(key=lambda item: (item["name"], item["cell_index"]))
    source_array_records = sorted(
        source_arrays.values(), key=lambda item: item["source_array_id"]
    )
    return {
        "ok": True,
        "operation": "inventory_pcellizer_hierarchy",
        "schema_version": 1,
        "layout": {
            "path": layout_path,
            "format": os.path.splitext(layout_path)[1].lower().lstrip("."),
            "dbu_um": float(layout.dbu),
            "klayout_version": pya.Application.instance().version(),
            "top_cell": top.name,
            "top_bbox_um": _optional_box_um(top.bbox(), layout.dbu),
            "cell_count": int(layout.cells()),
        },
        "cell_definitions": cell_definitions,
        "source_arrays": source_array_records,
        "occurrences": occurrences,
        "summary": {
            "occurrence_count": len(occurrences),
            "source_array_count": len(source_array_records),
            "authoring_supported_occurrence_count": sum(
                1 for item in occurrences if item["authoring_supported"]
            ),
            "authoring_blocked_occurrence_count": sum(
                1 for item in occurrences if not item["authoring_supported"]
            ),
            "flattening_performed": False,
            "geometry_modified": False,
            "composite_dut_membership_inferred": False,
        },
        "input_layout_modified": False,
        "production_ready": False,
    }
