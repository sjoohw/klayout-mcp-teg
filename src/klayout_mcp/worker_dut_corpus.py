"""KLayout-side labeled DUT corpus geometry extraction."""

from __future__ import annotations

import hashlib
import json
import os

import pya

from .worker_protocol import worker_error


def _fingerprint_and_metrics(cell, layers, dbu):
    payload = []
    metrics = {}
    for role, layer in sorted(layers.items()):
        index = cell.layout().find_layer(int(layer["layer"]), int(layer["datatype"]))
        if index is None:
            metrics[role] = {"present": False}
            continue
        region = pya.Region(cell.begin_shapes_rec(index)).merged()
        polygons = []
        area = 0
        for polygon in region.each():
            points = [[point.x, point.y] for point in polygon.each_point_hull()]
            polygons.append(points)
            area += polygon.area()
        if not polygons:
            metrics[role] = {"present": False}
            continue
        bbox = region.bbox()
        metrics[role] = {
            "present": True,
            "polygon_count": len(polygons),
            "bbox_um": [
                bbox.left * dbu,
                bbox.bottom * dbu,
                bbox.right * dbu,
                bbox.top * dbu,
            ],
            "width_um": bbox.width() * dbu,
            "height_um": bbox.height() * dbu,
            "area_um2": area * dbu * dbu,
        }
        payload.append(
            {
                "role": role,
                "layer": int(layer["layer"]),
                "datatype": int(layer["datatype"]),
                "polygons_dbu": polygons,
            }
        )
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return digest, metrics


def inspect_dut_corpus(request):
    layout_path = os.path.abspath(str(request.get("layout_path", "")))
    if not os.path.isfile(layout_path):
        return worker_error(
            "DUT_CORPUS_LAYOUT_NOT_FOUND",
            "Labeled DUT corpus layout does not exist.",
            {"field": "layout_path", "value": layout_path, "stage": "corpus_onboarding"},
            "Provide an existing stable GDS/OAS corpus stream.",
        )
    layout = pya.Layout()
    layout.read(layout_path)
    layers = request.get("layer_roles", {})
    observations = []
    for index, record in enumerate(request.get("dut_records", [])):
        cell_name = str(record.get("cell_name", ""))
        cell = layout.cell(cell_name)
        if cell is None:
            return worker_error(
                "DUT_CORPUS_CELL_NOT_FOUND",
                "A labeled DUT cell is not present in the source layout.",
                {
                    "field": f"dut_records[{index}].cell_name",
                    "value": cell_name,
                    "dut_id": record.get("dut_id"),
                    "stage": "corpus_onboarding",
                },
                "Correct the DUT cell name using the layout inventory.",
            )
        fingerprint, metrics = _fingerprint_and_metrics(cell, layers, float(layout.dbu))
        bbox = cell.bbox()
        observations.append(
            {
                "dut_id": record["dut_id"],
                "cell_name": cell_name,
                "geometry_fingerprint_sha256": fingerprint,
                "bbox_um": [
                    bbox.left * layout.dbu,
                    bbox.bottom * layout.dbu,
                    bbox.right * layout.dbu,
                    bbox.top * layout.dbu,
                ],
                "layer_metrics": metrics,
            }
        )
    return {
        "ok": True,
        "dbu_um": float(layout.dbu),
        "observations": observations,
        "layout_cell_count": sum(1 for _ in layout.each_cell()),
        "geometry_source": "labeled_multi_dut_reference_layout",
    }
