"""KLayout worker handler for deterministic Kelvin profile generation."""

import os
import tempfile

import pya

from .errors import AnalysisError
from .kelvin_routing import (
    build_kelvin_geometry_dbu,
    build_kelvin_routing_spec,
    geometry_box_counts,
)
from .worker_common import _box_um, _find_layer, _select_top, _shape_kind
from .worker_compare import (
    _compare_layout_objects,
    _layout_layer_pairs,
    _load_layout_and_top,
)
from .worker_protocol import worker_error as _error

def generate_kelvin_m1_teg(request):
    template_path = os.path.abspath(str(request["template_gds_path"]))
    output_path = os.path.abspath(str(request["output_gds_path"]))
    work_directory = os.path.abspath(str(request["work_directory_path"]))
    if not os.path.isfile(template_path):
        return _error(
            "KELVIN_TEMPLATE_NOT_FOUND",
            "Kelvin routing template GDS/OAS does not exist.",
            {"template_gds_path": template_path},
            (
                "Provide an existing SLN001 padset or the golden reference as a "
                "strip-and-rebuild template."
            ),
        )
    if os.path.exists(output_path):
        return _error(
            "OUTPUT_ALREADY_EXISTS",
            "Kelvin output already exists and was not overwritten.",
            {"output_gds_path": output_path},
            "Choose a new output path under the output work directory.",
        )
    try:
        os.makedirs(work_directory, exist_ok=True)
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
    except Exception as exc:
        return _error(
            "OUTPUT_DIRECTORY_FAILED",
            "Kelvin output/work directory could not be prepared.",
            {"error": str(exc), "work_directory_path": work_directory},
            "Provide writable output and work directories.",
        )

    try:
        layout, top, top_error = _load_layout_and_top(
            template_path, request.get("top_cell")
        )
        if top_error:
            return top_error
    except Exception as exc:
        return _error(
            "KELVIN_TEMPLATE_READ_FAILED",
            "KLayout could not read the Kelvin routing template.",
            {"template_gds_path": template_path, "error": str(exc)},
            "Check the template layout format and integrity.",
        )

    spec_request = request["routing_spec"]
    try:
        spec = build_kelvin_routing_spec(
            dimension_semantics=spec_request.get("dimension_semantics"),
            confirm_routing_contract=bool(spec_request.get("confirm_routing_contract")),
            splits=spec_request.get("splits"),
            site_origins_um=spec_request.get("site_origins_um"),
        )
        geometry = build_kelvin_geometry_dbu(spec, dbu_um=layout.dbu)
    except AnalysisError as exc:
        return exc.to_result()

    m1 = geometry["m1"]
    m1_index = _find_layer(layout, int(m1["layer"]), int(m1["datatype"]))
    if m1_index is None:
        return _error(
            "M1_LAYER_NOT_FOUND",
            "The confirmed M1 layer is absent from the Kelvin template.",
            {"m1": m1, "available_layers": _layout_layer_pairs(layout)},
            "Use an SLN001 template with M1 (15,0), or add an explicitly confirmed profile.",
        )

    removed_instances = []
    for instance in list(top.each_inst()):
        child = layout.cell(instance.cell_index)
        if child is not None and child.name.startswith("KELVIN_"):
            removed_instances.append(child.name)
            instance.delete()

    planned_kelvin_cells = set(geometry["cells"])
    obsolete_kelvin_cells = [
        cell
        for cell in layout.each_cell()
        if cell.name.startswith("KELVIN_") and cell.name not in planned_kelvin_cells
    ]
    removed_obsolete_cells = sorted(cell.name for cell in obsolete_kelvin_cells)
    for cell in obsolete_kelvin_cells:
        layout.delete_cell(cell.cell_index())

    pad_instance_count = sum(
        1
        for instance in top.each_inst()
        if layout.cell(instance.cell_index).name == "FRAMED_MESH_PAD_40UM"
    )
    expected_bbox = pya.Box(
        0,
        0,
        int(round(2000.0 / layout.dbu)),
        int(round(54.0 / layout.dbu)),
    )
    if pad_instance_count != 25 or top.bbox() != expected_bbox:
        return _error(
            "SLN001_TEMPLATE_CONTRACT_MISMATCH",
            "Template does not match the confirmed 25-Pad 2000 x 54 um SLN001 profile.",
            {
                "framed_mesh_pad_instance_count": pad_instance_count,
                "bbox_um": _box_um(top.bbox(), layout.dbu),
            },
            "Use the confirmed SLN001 framed-mesh padset or add a separate routing profile.",
        )

    created_cells = []
    for cell_name, cell_plan in geometry["cells"].items():
        cell = layout.cell(cell_name)
        if cell is None:
            cell = layout.create_cell(cell_name)
        else:
            cell.clear()
        created_cells.append(cell_name)
        shapes = cell.shapes(m1_index)
        for box in cell_plan["boxes_dbu"]:
            shapes.insert(pya.Box(*box))

    for cell_name, cell_plan in geometry["cells"].items():
        cell = layout.cell(cell_name)
        for instance_plan in cell_plan["instances"]:
            child = layout.cell(instance_plan["cell_name"])
            cell.insert(
                pya.CellInstArray(
                    child.cell_index(),
                    pya.Trans(
                        int(instance_plan["dx_dbu"]),
                        int(instance_plan["dy_dbu"]),
                    ),
                )
            )
    for instance_plan in geometry["top_instances"]:
        child = layout.cell(instance_plan["cell_name"])
        top.insert(
            pya.CellInstArray(
                child.cell_index(),
                pya.Trans(
                    int(instance_plan["dx_dbu"]),
                    int(instance_plan["dy_dbu"]),
                ),
            )
        )

    temporary_output = os.path.join(
        work_directory,
        os.path.basename(output_path) + ".unverified.gds",
    )
    if os.path.exists(temporary_output):
        os.unlink(temporary_output)
    try:
        layout.write(temporary_output)
        verify_layout, verify_top, verify_error = _load_layout_and_top(
            temporary_output, top.name
        )
        if verify_error:
            os.unlink(temporary_output)
            return verify_error
    except Exception as exc:
        if os.path.exists(temporary_output):
            os.unlink(temporary_output)
        return _error(
            "KELVIN_ROUNDTRIP_FAILED",
            "Generated Kelvin layout could not be written and freshly reloaded.",
            {"error": str(exc)},
            "Inspect DBU conversion and GDS serialization.",
        )

    verify_top_cells = sorted(cell.name for cell in verify_layout.top_cells())
    if verify_top_cells != [verify_top.name]:
        os.unlink(temporary_output)
        return _error(
            "KELVIN_UNEXPECTED_TOP_CELLS",
            "Fresh-reloaded Kelvin output contains unexpected unreferenced top cells.",
            {"expected_top_cell": verify_top.name, "top_cells": verify_top_cells},
            "Remove obsolete Kelvin cells and regenerate into a new output path.",
        )

    verify_kelvin_m1_index = _find_layer(
        verify_layout, int(m1["layer"]), int(m1["datatype"])
    )
    cell_roundtrip = []
    for cell_name, cell_plan in geometry["cells"].items():
        verify_cell = verify_layout.cell(cell_name)
        if verify_cell is None:
            os.unlink(temporary_output)
            return _error(
                "KELVIN_CELL_ROUNDTRIP_FAILED",
                "Fresh-reloaded output is missing a generated Kelvin cell.",
                {"missing_cell": cell_name},
                "Inspect GDS hierarchy serialization and regenerate.",
            )
        actual_region = pya.Region()
        actual_non_box = []
        for shape in verify_cell.each_shape(verify_kelvin_m1_index):
            if shape.is_box():
                actual_region.insert(shape.box)
            else:
                actual_non_box.append(_shape_kind(shape))
        expected_region = pya.Region()
        for box in cell_plan["boxes_dbu"]:
            expected_region.insert(pya.Box(*box))
        actual_children = sorted(
            verify_layout.cell(instance.cell_index).name
            for instance in verify_cell.each_inst()
        )
        expected_children = sorted(
            instance["cell_name"] for instance in cell_plan["instances"]
        )
        xor_clean = (actual_region ^ expected_region).is_empty()
        if actual_non_box or not xor_clean or actual_children != expected_children:
            os.unlink(temporary_output)
            return _error(
                "KELVIN_CELL_ROUNDTRIP_FAILED",
                "Fresh-reloaded Kelvin cell differs from its integer-DBU routing plan.",
                {
                    "cell": cell_name,
                    "geometry_xor_clean": xor_clean,
                    "non_box_shapes": actual_non_box,
                    "actual_children": actual_children,
                    "expected_children": expected_children,
                },
                "Inspect generated box coordinates, child instances, and GDS serialization.",
            )
        cell_roundtrip.append(
            {
                "cell": cell_name,
                "direct_m1_geometry_xor_clean": True,
                "child_instances_match": True,
            }
        )

    actual_top_kelvin_instances = sorted(
        (
            verify_layout.cell(instance.cell_index).name,
            int(instance.trans.disp.x),
            int(instance.trans.disp.y),
        )
        for instance in verify_top.each_inst()
        if verify_layout.cell(instance.cell_index).name.startswith("KELVIN_")
    )
    expected_top_kelvin_instances = sorted(
        (
            instance["cell_name"],
            int(instance["dx_dbu"]),
            int(instance["dy_dbu"]),
        )
        for instance in geometry["top_instances"]
    )
    if actual_top_kelvin_instances != expected_top_kelvin_instances:
        os.unlink(temporary_output)
        return _error(
            "KELVIN_TOP_INSTANCE_ROUNDTRIP_FAILED",
            "Fresh-reloaded Kelvin top instances differ from the routing plan.",
            {
                "actual": actual_top_kelvin_instances,
                "expected": expected_top_kelvin_instances,
            },
            "Inspect Kelvin cell placement and GDS transform serialization.",
        )

    kelvin_cells = [
        cell for cell in verify_layout.each_cell() if cell.name.startswith("KELVIN_")
    ]
    non_box_shapes = []
    for cell in kelvin_cells:
        for shape in cell.each_shape(verify_kelvin_m1_index):
            if not shape.is_box():
                non_box_shapes.append({"cell": cell.name, "kind": _shape_kind(shape)})
    if non_box_shapes:
        os.unlink(temporary_output)
        return _error(
            "NON_ORTHOGONAL_ROUTING_FORBIDDEN",
            "Fresh-reloaded Kelvin routing contains a non-box M1 shape.",
            {"shapes": non_box_shapes[:20]},
            "Generate Kelvin routing only from axis-aligned boxes.",
        )

    comparison = None
    reference_path = request.get("reference_gds_path")
    if reference_path:
        reference_path = os.path.abspath(str(reference_path))
        if not os.path.isfile(reference_path):
            os.unlink(temporary_output)
            return _error(
                "KELVIN_REFERENCE_NOT_FOUND",
                "Golden Kelvin reference does not exist.",
                {"reference_gds_path": reference_path},
                "Provide an existing golden reference or omit comparison.",
            )
        try:
            reference_layout, reference_top, reference_error = _load_layout_and_top(
                reference_path, request.get("reference_top_cell")
            )
        except Exception as exc:
            os.unlink(temporary_output)
            return _error(
                "KELVIN_REFERENCE_READ_FAILED",
                "KLayout could not read the golden Kelvin reference.",
                {"reference_gds_path": reference_path, "error": str(exc)},
                "Check the golden reference format and integrity.",
            )
        if reference_error:
            os.unlink(temporary_output)
            return reference_error
        comparison = _compare_layout_objects(
            verify_layout, verify_top, reference_layout, reference_top, m1
        )
        if request.get("require_reference_equivalence", True) and not comparison["equivalent"]:
            os.unlink(temporary_output)
            return _error(
                "KELVIN_REFERENCE_MISMATCH",
                "Generated Kelvin layout is not semantically equivalent to the reference.",
                {"comparison": comparison},
                "Adjust the routing profile and regenerate into a new output path.",
            )

    verify_m1_index = _find_layer(
        verify_layout, int(m1["layer"]), int(m1["datatype"])
    )
    verify_m1 = pya.Region(verify_top.begin_shapes_rec(verify_m1_index)).merged()
    m1_component_count = sum(1 for _ in verify_m1.each())
    if m1_component_count != 7:
        os.unlink(temporary_output)
        return _error(
            "KELVIN_CONNECTIVITY_CONTRACT_FAILED",
            "Fresh-reloaded SLN001 Kelvin M1 does not have six groups plus isolated Pad 25.",
            {"expected_component_count": 7, "actual_component_count": m1_component_count},
            "Inspect Pad-group shorts/opens and regenerate into a new output path.",
        )

    try:
        reservation = os.open(output_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.close(reservation)
        os.replace(temporary_output, output_path)
    except Exception as exc:
        if os.path.exists(temporary_output):
            os.unlink(temporary_output)
        if os.path.isfile(output_path) and os.path.getsize(output_path) == 0:
            os.unlink(output_path)
        return _error(
            "KELVIN_OUTPUT_PROMOTION_FAILED",
            "Verified Kelvin output could not be promoted to its final path.",
            {"output_gds_path": output_path, "error": str(exc)},
            "Check output permissions and choose a new path.",
        )

    return {
        "ok": True,
        "production_ready": False,
        "optimization_status": "minimize_with_available_constraints_not_rc_proven",
        "output_gds_path": output_path,
        "template_gds_path": template_path,
        "reference_gds_path": reference_path,
        "top_cell": verify_top.name,
        "dbu_um": verify_layout.dbu,
        "bbox_um": _box_um(verify_top.bbox(), verify_layout.dbu),
        "removed_existing_kelvin_instance_count": len(removed_instances),
        "removed_obsolete_kelvin_cells": removed_obsolete_cells,
        "created_kelvin_cells": sorted(created_cells),
        "top_cell_count": len(verify_top_cells),
        "generated_cell_roundtrip": cell_roundtrip,
        "top_instance_roundtrip_verified": True,
        "kelvin_direct_top_instance_count": sum(
            1
            for instance in verify_top.each_inst()
            if verify_layout.cell(instance.cell_index).name.startswith("KELVIN_")
        ),
        "generated_box_counts": geometry_box_counts(geometry),
        "orthogonal_box_only_verified": True,
        "fresh_reload_verified": True,
        "m1_component_count": m1_component_count,
        "m1_hole_count": sum(polygon.holes() for polygon in verify_m1.each()),
        "reference_comparison": comparison,
        "input_layout_modified": False,
    }
