"""KLayout worker handler for conceptual fixed-25-Pad TEG assembly."""

import json
import os
import tempfile

import pya

from .drc_guardrails import analyze_m1_connectivity
from .dut_geometry import DutParameters, build_dut_geometry
from .errors import AnalysisError
from .file_publication import (
    OutputAlreadyExistsError,
    publication_staging_prefix,
    publish_new_file,
)
from .geometry import Box
from .worker_common import _box_um, _find_layer, _select_top
from .worker_padset import analyze_padset
from .worker_protocol import worker_error as _error

def assemble_teg(request):
    padset_path = os.path.abspath(str(request["padset_path"]))
    output_gds_path = os.path.abspath(str(request["output_gds_path"]))
    teg_name = str(request.get("teg_name", "TEG_DUT_ARRAY_V1"))
    export_static = bool(request.get("export_static", True))
    dut_sweep = request.get("dut_sweep", [])
    layermap = request.get("layermap", {})

    if not request.get("conceptual_export_confirmed"):
        return _error(
            "CONCEPTUAL_EXPORT_REQUIRES_OPT_IN",
            "Synthetic DUT assembly requires explicit non-production acknowledgement.",
            {"production_ready": False},
            "Use the host assemble_teg tool with confirm_conceptual_export=true.",
        )
    if os.path.exists(output_gds_path):
        return _error(
            "OUTPUT_ALREADY_EXISTS",
            "Assembly output already exists and will not be overwritten.",
            {"output_gds_path": output_gds_path},
            "Choose a new output path.",
        )
    output_dir = os.path.dirname(output_gds_path) or os.getcwd()
    if not os.path.isdir(output_dir):
        return _error(
            "OUTPUT_DIRECTORY_NOT_FOUND",
            "Assembly output directory does not exist.",
            {"output_directory": output_dir},
            "Create the output directory before retrying.",
        )

    required_roles = {"m1", "active", "poly", "contact", "text"}
    missing_roles = sorted(required_roles.difference(layermap))
    if missing_roles:
        return _error(
            "ASSEMBLY_LAYERMAP_INCOMPLETE",
            "Every generated layer role must be explicit in the layermap.",
            {"missing_layer_roles": missing_roles},
            "Add explicit m1, active, poly, contact, and text layer/datatype pairs.",
        )

    layout = pya.Layout()
    try:
        layout.read(padset_path)
    except Exception as exc:
        return _error(
            "PADSET_READ_FAILED",
            "KLayout could not read the template padset.",
            {"padset_path": padset_path, "error": str(exc)},
        )

    top, top_error = _select_top(layout, request.get("top_cell"))
    if top_error:
        return top_error

    dbu = layout.dbu

    # Resolve layers
    def get_layer_idx(role_key):
        role_info = layermap.get(role_key)
        l = int(role_info["layer"])
        dt = int(role_info["datatype"])
        return layout.layer(l, dt)

    l_m1 = get_layer_idx("m1")
    l_active = get_layer_idx("active")
    l_poly = get_layer_idx("poly")
    l_contact = get_layer_idx("contact")
    l_text = get_layer_idx("text")

    # 1. Inspect padset to get deterministic slot origins
    req_analyze = dict(request)
    req_analyze["layout_path"] = padset_path
    analysis = analyze_padset(req_analyze)
    if not analysis.get("ok"):
        return analysis

    dut_slots = analysis["dut_slots"]
    slots_by_site = {int(slot["site"]): slot for slot in dut_slots}
    requested_sites = [item.get("site") for item in dut_sweep]
    if (
        not dut_sweep
        or len(set(requested_sites)) != len(requested_sites)
        or any(site not in slots_by_site for site in requested_sites)
    ):
        return _error(
            "INVALID_DUT_SITE_SELECTION",
            "Validated DUT sweep entries must select unique available sites.",
            {
                "requested_sites": requested_sites,
                "available_sites": sorted(slots_by_site),
            },
            "Choose one or more unique DUT sites between 1 and 21.",
        )

    # 2. Build canonical geometry once per unique parameter/window variant and
    # instantiate reusable cells at the deterministic slot origins.
    variant_cells = {}
    variant_expectations = {}
    created_cells = []
    variant_records = []
    site_variants = []
    for sweep_item in dut_sweep:
        site_num = int(sweep_item["site"])
        slot = slots_by_site[site_num]
        params_dict = sweep_item.get("parameters", {})
        origin = slot["origin_um"]
        local_device_window = Box(
            float(slot["device_window_um"][0]) - float(origin[0]),
            float(slot["device_window_um"][1]) - float(origin[1]),
            float(slot["device_window_um"][2]) - float(origin[0]),
            float(slot["device_window_um"][3]) - float(origin[1]),
        )
        local_routing_boundary = Box(
            float(slot["routing_boundary_um"][0]) - float(origin[0]),
            float(slot["routing_boundary_um"][1]) - float(origin[1]),
            float(slot["routing_boundary_um"][2]) - float(origin[0]),
            float(slot["routing_boundary_um"][3]) - float(origin[1]),
        )
        canonical_values = {
            name: params_dict[name]
            for name in (
                "w_um",
                "l_um",
                "array_rows",
                "array_cols",
                "pitch_x_um",
                "pitch_y_um",
                "routed_device_count",
                "m1_width_um",
                "m1_overlap_um",
            )
            if name in params_dict
        }
        canonical_values["device_window_um"] = local_device_window
        canonical_values["routing_boundary_um"] = local_routing_boundary
        try:
            params = DutParameters(**canonical_values)
            geometry = build_dut_geometry(params, dbu_um=dbu)
        except AnalysisError as exc:
            return _error(
                "ASSEMBLY_GEOMETRY_INVALID",
                f"Site {site_num} canonical geometry generation failed: {exc.message}",
                {
                    "site": site_num,
                    "cause_code": exc.code,
                    "parameters": params_dict,
                },
                exc.next_action,
            )

        parameter_key = json.dumps(
            params.to_dict(), sort_keys=True, separators=(",", ":")
        )
        if parameter_key not in variant_cells:
            variant_id = f"VARIANT_{len(variant_cells) + 1:03d}"
            cell_name = f"DUT_{variant_id}"
            dut_cell = layout.create_cell(cell_name)
            for box in geometry.active_boxes_um:
                dut_cell.shapes(l_active).insert(pya.DBox(*box))
            for box in geometry.poly_boxes_um:
                dut_cell.shapes(l_poly).insert(pya.DBox(*box))
            for box in geometry.contact_boxes_um:
                dut_cell.shapes(l_contact).insert(pya.DBox(*box))
            for shape in geometry.m1_shapes_um:
                dut_cell.shapes(l_m1).insert(pya.DBox(*shape["bbox_um"]))

            connectivity = analyze_m1_connectivity(geometry.m1_shapes_um)
            variant_cells[parameter_key] = (variant_id, dut_cell)
            variant_expectations[cell_name] = {
                "active": list(geometry.active_boxes_um),
                "poly": list(geometry.poly_boxes_um),
                "contact": list(geometry.contact_boxes_um),
                "m1": [
                    list(shape["bbox_um"]) for shape in geometry.m1_shapes_um
                ],
            }
            created_cells.append(cell_name)
            variant_records.append(
                {
                    "variant_id": variant_id,
                    "cell_name": cell_name,
                    "parameters": params.to_dict(),
                    "routed_indices": list(geometry.routed_indices),
                    "shape_counts": geometry.to_dict()["shape_counts"],
                    "m1_connectivity": connectivity,
                }
            )
        else:
            variant_id, dut_cell = variant_cells[parameter_key]

        # Insert instance into top cell
        trans = pya.DTrans(pya.DPoint(origin[0], origin[1]))
        top.insert(pya.DCellInstArray(dut_cell.cell_index(), trans))
        site_variants.append(
            {
                "site": site_num,
                "variant_id": variant_id,
                "cell_name": dut_cell.name,
                "origin_um": [float(origin[0]), float(origin[1])],
            }
        )

    # 3. Add TEG Name Text (90-degree rotated at left edge)
    pads = analysis.get("pads", [])
    if pads:
        leftmost_pad = min(pads, key=lambda p: p["bbox_um"][0])
        label_x = leftmost_pad["bbox_um"][0] - 20.0
        label_y = leftmost_pad["center_um"][1]
        text_obj = pya.DText(
            teg_name,
            pya.DTrans(pya.DTrans.R90, label_x, label_y),
        )
        top.shapes(l_text).insert(text_obj)

    # 4. Export static layout if requested
    if export_static:
        top.flatten(-1, True)

    # 5. Write to a temporary sibling, verify, then promote without replacing
    # any pre-existing user artifact.
    temp_handle, temporary_output = tempfile.mkstemp(
        prefix=publication_staging_prefix("assembly"),
        suffix=".gds",
        dir=output_dir,
    )
    os.close(temp_handle)
    os.unlink(temporary_output)
    try:
        layout.write(temporary_output)
    except Exception as exc:
        if os.path.exists(temporary_output):
            os.unlink(temporary_output)
        return _error(
            "GDS_WRITE_FAILED",
            f"Failed to write assembled GDS: {exc}",
            {"output_path": output_gds_path, "error": str(exc)},
        )

    # 6. Round-trip verification
    verify_layout = pya.Layout()
    verify_layout.read(temporary_output)
    verify_top = verify_layout.top_cell()
    verify_layers = sorted(
        {
            (verify_layout.get_info(index).layer, verify_layout.get_info(index).datatype)
            for index in verify_layout.layer_indices()
        }
    )
    expected_layers = sorted(
        {(int(layermap[role]["layer"]), int(layermap[role]["datatype"])) for role in required_roles}
    )
    missing_output_layers = sorted(set(expected_layers).difference(verify_layers))
    if missing_output_layers:
        os.unlink(temporary_output)
        return _error(
            "ASSEMBLY_ROUNDTRIP_INVALID",
            "Fresh reload is missing required generated layers.",
            {"missing_layers": missing_output_layers, "output_layers": verify_layers},
            "Inspect layer generation before exporting again.",
        )
    direct_instance_count = sum(1 for _ in verify_top.each_inst())
    if export_static and direct_instance_count != 0:
        os.unlink(temporary_output)
        return _error(
            "ASSEMBLY_ROUNDTRIP_INVALID",
            "Static export still contains top-level instances after fresh reload.",
            {"direct_instance_count": direct_instance_count},
            "Inspect flattening and static export logic.",
        )

    pcell_variant_names = [
        cell.name for cell in verify_layout.each_cell() if cell.is_pcell_variant()
    ]
    if pcell_variant_names:
        os.unlink(temporary_output)
        return _error(
            "ASSEMBLY_ROUNDTRIP_INVALID",
            "Fresh reload unexpectedly depends on PCell variants.",
            {"pcell_variant_names": sorted(pcell_variant_names)},
            "Export only ordinary geometry cells before delivery.",
        )

    variant_roundtrip = []
    if not export_static:
        verify_role_layers = {
            role: _find_layer(
                verify_layout,
                int(layermap[role]["layer"]),
                int(layermap[role]["datatype"]),
            )
            for role in ("active", "poly", "contact", "m1")
        }
        for cell_name, expected_by_role in sorted(variant_expectations.items()):
            verify_cell = verify_layout.cell(cell_name)
            if verify_cell is None:
                os.unlink(temporary_output)
                return _error(
                    "ASSEMBLY_ROUNDTRIP_INVALID",
                    "Fresh reload is missing a reusable DUT variant cell.",
                    {"missing_variant_cell": cell_name},
                    "Inspect hierarchy serialization before export.",
                )
            mismatched_roles = []
            for role, expected_boxes in expected_by_role.items():
                layer_index = verify_role_layers[role]
                actual_region = pya.Region(verify_cell.begin_shapes_rec(layer_index))
                expected_region = pya.Region()
                for box in expected_boxes:
                    expected_region.insert(
                        pya.DBox(*box).to_itype(verify_layout.dbu)
                    )
                if not (actual_region ^ expected_region).is_empty():
                    mismatched_roles.append(role)
            if mismatched_roles:
                os.unlink(temporary_output)
                return _error(
                    "ASSEMBLY_ROUNDTRIP_INVALID",
                    "Fresh reload changed canonical DUT variant geometry.",
                    {
                        "variant_cell": cell_name,
                        "mismatched_layer_roles": mismatched_roles,
                    },
                    "Inspect DBU conversion and GDS serialization before export.",
                )
            variant_roundtrip.append(
                {"cell_name": cell_name, "geometry_xor_clean": True}
            )

    verify_text_layer = _find_layer(
        verify_layout,
        int(layermap["text"]["layer"]),
        int(layermap["text"]["datatype"]),
    )
    verified_labels = []
    if verify_text_layer is not None:
        for shape in verify_top.each_shape(verify_text_layer):
            if shape.is_text() and shape.text.string == teg_name:
                verified_labels.append(shape.text)
    expected_text_rotation = 1
    if (
        len(verified_labels) != 1
        or int(verified_labels[0].trans.rot) != expected_text_rotation
        or bool(verified_labels[0].trans.is_mirror())
    ):
        actual_transforms = [
            {
                "rotation_quadrants": int(label.trans.rot),
                "mirrored": bool(label.trans.is_mirror()),
            }
            for label in verified_labels
        ]
        os.unlink(temporary_output)
        return _error(
            "ASSEMBLY_ROUNDTRIP_INVALID",
            "Fresh reload did not preserve exactly one unmirrored 90-degree TEG label.",
            {
                "teg_name": teg_name,
                "matching_label_count": len(verified_labels),
                "actual_transforms": actual_transforms,
            },
            "Inspect text construction and GDS serialization before exporting again.",
        )

    try:
        publish_new_file(temporary_output, output_gds_path)
    except OutputAlreadyExistsError:
        return _error(
            "OUTPUT_ALREADY_EXISTS",
            "Another writer published the assembly output first; its result was preserved.",
            {"output_gds_path": output_gds_path},
            "Choose a new output path or reuse the existing winning artifact.",
        )
    except Exception as exc:
        return _error(
            "ASSEMBLY_PROMOTION_FAILED",
            "Verified assembly could not be promoted to the requested output path.",
            {
                "output_gds_path": output_gds_path,
                "error_type": type(exc).__name__,
                "error": str(exc),
            },
            "Check output permissions and retry with a new path.",
        )
    finally:
        if os.path.exists(temporary_output):
            os.unlink(temporary_output)

    unresolved_landings = analysis.get("m1_connectivity", {}).get(
        "unresolved_landings", []
    )

    return {
        "ok": True,
        "production_ready": False,
        "geometry_status": "conceptual_scaffold",
        "process_geometry_verified": False,
        "electrical_connectivity_verified": False,
        "known_terminal_state": "canonical_conceptual_geometry_with_reported_internal_opens",
        "output_gds_path": output_gds_path,
        "teg_name": teg_name,
        "export_static": export_static,
        "total_sites": len(dut_slots),
        "assembled_sites": len(dut_sweep),
        "selected_sites": sorted(int(item["site"]) for item in dut_sweep),
        "variant_count": len(variant_cells),
        "top_cell": verify_top.name,
        "dbu_um": verify_layout.dbu,
        "bbox_um": _box_um(verify_top.bbox(), verify_layout.dbu),
        "cell_count": verify_layout.cells(),
        "direct_instance_count": direct_instance_count,
        "layers": [
            {"layer": layer, "datatype": datatype}
            for layer, datatype in verify_layers
        ],
        "teg_label": {
            "string": teg_name,
            "rotation_degrees": 90,
            "mirrored": False,
            "roundtrip_verified": True,
        },
        "roundtrip_verified": True,
        "pcell_dependency_count": 0,
        "variant_roundtrip": variant_roundtrip,
        "input_layout_modified": False,
        "unresolved_padset_landings": unresolved_landings,
        "created_dut_cells": created_cells,
        "site_variants": site_variants,
        "variants": variant_records,
        "warning": (
            "Canonical synthetic geometry and reported internal opens are for visual testing only."
        ),
    }
