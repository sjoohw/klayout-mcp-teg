"""KLayout-side labeled DUT corpus geometry extraction."""

from __future__ import annotations

import hashlib
import json
import os

import pya

from .geometry_fingerprint import canonical_ring
from .worker_protocol import worker_error


def _polygon_payload(polygon):
    return {
        "hull_dbu": canonical_ring(polygon.each_point_hull()),
        "holes_dbu": sorted(
            canonical_ring(polygon.each_point_hole(index))
            for index in range(polygon.holes())
        ),
    }


def _digest(payload):
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _terminal_observations(terminals, components_by_role, dbu):
    observations = {}
    touched_by_terminal = {}
    for terminal_name, definition in sorted(terminals.items()):
        role = definition.get("layer_role")
        landing = definition.get("landing_bbox_um")
        observation = {
            "layer_role": role,
            "landing_declared": isinstance(landing, list) and len(landing) == 4,
            "connectivity_scope": "same_layer_merged_polygon_component_not_lvs",
        }
        touched = set()
        if observation["landing_declared"]:
            landing_dbu = [int(round(float(value) / dbu)) for value in landing]
            landing_box = pya.Box(*landing_dbu)
            landing_region = pya.Region(landing_box)
            overlap_area = 0
            touched_payloads = []
            touched_area = 0
            for component_index, (polygon, payload) in enumerate(
                components_by_role.get(role, [])
            ):
                intersection_area = (pya.Region(polygon) & landing_region).area()
                if intersection_area <= 0:
                    continue
                touched.add(component_index)
                overlap_area += intersection_area
                touched_area += polygon.area()
                touched_payloads.append(payload)
            observation.update(
                {
                    "landing_bbox_um": [float(value) for value in landing],
                    "quantized_landing_bbox_um": [value * dbu for value in landing_dbu],
                    "landing_present": bool(touched),
                    "landing_overlap_area_um2": overlap_area * dbu * dbu,
                    "touched_component_count": len(touched),
                    "touched_component_area_um2": touched_area * dbu * dbu,
                    "touched_component_fingerprint_sha256": (
                        _digest(sorted(touched_payloads, key=lambda item: json.dumps(item, sort_keys=True)))
                        if touched_payloads
                        else None
                    ),
                }
            )
        observations[terminal_name] = observation
        touched_by_terminal[terminal_name] = (role, touched)

    pair_observations = {}
    names = sorted(observations)
    for left_index, left in enumerate(names):
        left_role, left_components = touched_by_terminal[left]
        for right in names[left_index + 1 :]:
            right_role, right_components = touched_by_terminal[right]
            pair_id = f"{left}__{right}"
            if left_role != right_role:
                pair_observations[pair_id] = {
                    "status": "unverified_cross_layer",
                    "same_component": None,
                }
                continue
            observable = bool(left_components) and bool(right_components)
            pair_observations[pair_id] = {
                "status": "observed" if observable else "unresolved_landing",
                "same_component": (
                    bool(left_components.intersection(right_components))
                    if observable
                    else None
                ),
            }
    return observations, pair_observations


def _fingerprint_and_metrics(cell, layers, terminals, dbu):
    payload = []
    metrics = {}
    components_by_role = {}
    for role, layer in sorted(layers.items()):
        index = cell.layout().find_layer(int(layer["layer"]), int(layer["datatype"]))
        if index is None:
            metrics[role] = {"present": False}
            continue
        region = pya.Region(cell.begin_shapes_rec(index)).merged()
        polygons = []
        components = []
        area = 0
        hole_count = 0
        for polygon in region.each():
            polygon_record = _polygon_payload(polygon)
            polygons.append(polygon_record)
            components.append((polygon, polygon_record))
            area += polygon.area()
            hole_count += polygon.holes()
        if not polygons:
            metrics[role] = {"present": False}
            continue
        polygons.sort(key=lambda item: json.dumps(item, sort_keys=True))
        components_by_role[role] = components
        bbox = region.bbox()
        layer_fingerprint = _digest(polygons)
        metrics[role] = {
            "present": True,
            "polygon_count": len(polygons),
            "hole_count": hole_count,
            "bbox_um": [
                bbox.left * dbu,
                bbox.bottom * dbu,
                bbox.right * dbu,
                bbox.top * dbu,
            ],
            "width_um": bbox.width() * dbu,
            "height_um": bbox.height() * dbu,
            "area_um2": area * dbu * dbu,
            "geometry_fingerprint_sha256": layer_fingerprint,
        }
        payload.append(
            {
                "role": role,
                "layer": int(layer["layer"]),
                "datatype": int(layer["datatype"]),
                "polygons_dbu": polygons,
            }
        )
    terminal_metrics, terminal_pair_metrics = _terminal_observations(
        terminals, components_by_role, dbu
    )
    return _digest(payload), metrics, terminal_metrics, terminal_pair_metrics


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
        fingerprint, metrics, terminal_metrics, terminal_pair_metrics = (
            _fingerprint_and_metrics(
                cell,
                layers,
                record.get("terminals", {}),
                float(layout.dbu),
            )
        )
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
                "terminal_metrics": terminal_metrics,
                "terminal_pair_metrics": terminal_pair_metrics,
                "terminal_connectivity_verified": False,
                "terminal_connectivity_scope": "same_layer_merged_polygon_component_only_not_lvs",
            }
        )
    return {
        "ok": True,
        "dbu_um": float(layout.dbu),
        "observations": observations,
        "layout_cell_count": sum(1 for _ in layout.each_cell()),
        "geometry_source": "labeled_multi_dut_reference_layout",
    }
