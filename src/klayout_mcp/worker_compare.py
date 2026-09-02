"""Domain-neutral and Kelvin semantic layout comparison worker handlers."""

import os

import pya

from .worker_common import _box_um, _find_layer, _select_top
from .worker_protocol import worker_error as _error

def _layout_layer_pairs(layout):
    return sorted(
        (layout.get_info(index).layer, layout.get_info(index).datatype)
        for index in layout.layer_indices()
    )


def _recursive_text_records(layout, top):
    records = []
    for layer_index in layout.layer_indices():
        info = layout.get_info(layer_index)
        iterator = layout.begin_shapes(top.cell_index(), layer_index)
        while not iterator.at_end():
            shape = iterator.shape()
            if shape.is_text():
                text = shape.text.transformed(iterator.itrans())
                records.append(
                    {
                        "layer": [info.layer, info.datatype],
                        "string": text.string,
                        "trans": str(text.trans),
                    }
                )
            iterator.next()
    records.sort(key=lambda item: (item["layer"], item["string"], item["trans"]))
    return records


def _compare_layout_objects(
    candidate_layout, candidate_top, reference_layout, reference_top, m1=None
):
    if abs(candidate_layout.dbu - reference_layout.dbu) > 1e-15:
        return {
            "equivalent": False,
            "dbu_match": False,
            "candidate_dbu_um": candidate_layout.dbu,
            "reference_dbu_um": reference_layout.dbu,
            "layers": [],
        }

    candidate_pairs = _layout_layer_pairs(candidate_layout)
    reference_pairs = _layout_layer_pairs(reference_layout)
    layer_pairs = sorted(set(candidate_pairs) | set(reference_pairs))
    layer_reports = []
    all_xor_clean = True
    for layer, datatype in layer_pairs:
        candidate_index = _find_layer(candidate_layout, layer, datatype)
        reference_index = _find_layer(reference_layout, layer, datatype)
        candidate_region = pya.Region()
        reference_region = pya.Region()
        if candidate_index is not None:
            candidate_region = pya.Region(
                candidate_top.begin_shapes_rec(candidate_index)
            ).merged()
        if reference_index is not None:
            reference_region = pya.Region(
                reference_top.begin_shapes_rec(reference_index)
            ).merged()
        xor_region = (candidate_region ^ reference_region).merged()
        xor_clean = xor_region.is_empty()
        all_xor_clean = all_xor_clean and xor_clean
        layer_reports.append(
            {
                "layer": layer,
                "datatype": datatype,
                "geometry_xor_clean": xor_clean,
                "xor_area_um2": xor_region.area() * candidate_layout.dbu * candidate_layout.dbu,
                "candidate_component_count": sum(1 for _ in candidate_region.each()),
                "reference_component_count": sum(1 for _ in reference_region.each()),
            }
        )

    candidate_text = _recursive_text_records(candidate_layout, candidate_top)
    reference_text = _recursive_text_records(reference_layout, reference_top)
    text_match = candidate_text == reference_text
    candidate_m1 = pya.Region()
    reference_m1 = pya.Region()
    if m1 is not None:
        candidate_m1_index = _find_layer(
            candidate_layout, int(m1["layer"]), int(m1["datatype"])
        )
        reference_m1_index = _find_layer(
            reference_layout, int(m1["layer"]), int(m1["datatype"])
        )
        if candidate_m1_index is not None:
            candidate_m1 = pya.Region(
                candidate_top.begin_shapes_rec(candidate_m1_index)
            ).merged()
        if reference_m1_index is not None:
            reference_m1 = pya.Region(
                reference_top.begin_shapes_rec(reference_m1_index)
            ).merged()

    def topology(region):
        polygons = list(region.each())
        return {
            "component_count": len(polygons),
            "hole_count": sum(polygon.holes() for polygon in polygons),
            "area_um2": region.area() * candidate_layout.dbu * candidate_layout.dbu,
        }

    bbox_match = candidate_top.bbox() == reference_top.bbox()
    result = {
        "equivalent": bool(
            all_xor_clean
            and text_match
            and bbox_match
            and candidate_pairs == reference_pairs
        ),
        "dbu_match": True,
        "top_bbox_match": bbox_match,
        "layer_set_match": candidate_pairs == reference_pairs,
        "recursive_text_match": text_match,
        "layers": layer_reports,
    }
    if m1 is not None:
        result["m1"] = {
            "candidate": topology(candidate_m1),
            "reference": topology(reference_m1),
        }
    return result


def _load_layout_and_top(path, top_cell):
    layout = pya.Layout()
    layout.read(path)
    top, error = _select_top(layout, top_cell)
    return layout, top, error


def compare_kelvin_layouts(request):
    candidate_path = os.path.abspath(str(request["candidate_gds_path"]))
    reference_path = os.path.abspath(str(request["reference_gds_path"]))
    for label, path in (("candidate", candidate_path), ("reference", reference_path)):
        if not os.path.isfile(path):
            return _error(
                "LAYOUT_NOT_FOUND",
                "A layout required for semantic comparison does not exist.",
                {"role": label, "path": path},
                "Provide existing candidate and reference GDS/OAS files.",
            )
    try:
        candidate_layout, candidate_top, candidate_error = _load_layout_and_top(
            candidate_path, request.get("candidate_top_cell")
        )
        if candidate_error:
            return candidate_error
        reference_layout, reference_top, reference_error = _load_layout_and_top(
            reference_path, request.get("reference_top_cell")
        )
        if reference_error:
            return reference_error
    except Exception as exc:
        return _error(
            "LAYOUT_COMPARE_READ_FAILED",
            "KLayout could not read one of the layouts for semantic comparison.",
            {"error": str(exc)},
            "Check both layout formats and file integrity.",
        )
    comparison = _compare_layout_objects(
        candidate_layout,
        candidate_top,
        reference_layout,
        reference_top,
        request["m1"],
    )
    return {
        "ok": True,
        "candidate_gds_path": candidate_path,
        "reference_gds_path": reference_path,
        "candidate_top_cell": candidate_top.name,
        "reference_top_cell": reference_top.name,
        "comparison": comparison,
    }


def compare_layouts(request):
    candidate_path = os.path.abspath(str(request["candidate_layout_path"]))
    reference_path = os.path.abspath(str(request["reference_layout_path"]))
    for label, path in (("candidate", candidate_path), ("reference", reference_path)):
        if not os.path.isfile(path):
            return _error(
                "LAYOUT_NOT_FOUND",
                "A layout required for semantic comparison does not exist.",
                {"role": label, "path": path},
                "Provide existing candidate and reference GDS/OAS files.",
            )
    try:
        candidate_layout, candidate_top, candidate_error = _load_layout_and_top(
            candidate_path, request.get("candidate_top_cell")
        )
        if candidate_error:
            return candidate_error
        reference_layout, reference_top, reference_error = _load_layout_and_top(
            reference_path, request.get("reference_top_cell")
        )
        if reference_error:
            return reference_error
    except Exception as exc:
        return _error(
            "LAYOUT_COMPARE_READ_FAILED",
            "KLayout could not read one of the layouts for semantic comparison.",
            {"error": str(exc)},
            "Check both layout formats and file integrity.",
        )
    comparison = _compare_layout_objects(
        candidate_layout,
        candidate_top,
        reference_layout,
        reference_top,
    )
    return {
        "ok": True,
        "candidate_layout_path": candidate_path,
        "reference_layout_path": reference_path,
        "candidate_top_cell": candidate_top.name,
        "reference_top_cell": reference_top.name,
        "comparison": comparison,
    }
