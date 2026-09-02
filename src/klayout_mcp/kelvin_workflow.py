"""Kelvin M1 profile adapters for the persistent four-call workflow facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .design_contract import TRANSVERSE_WIDTH_LONGITUDINAL_LENGTH
from .errors import AnalysisError
from .kelvin_service import (
    generate_kelvin_m1_teg_service,
    plan_kelvin_m1_routing_service,
)
from .workflow_manifest import canonical_sha256, immutable_json_copy


EXPECTED_TERMINAL_PAD_ORDER = ("S+", "F+", "F-", "S-")

SLN001_KELVIN_PROCESS_CAPABILITY: dict[str, Any] = {
    "schema_version": 1,
    "process": {
        "name": "sln001_kelvin_reference_demo",
        "version": "golden-v15-2026-08-25",
        "evidence_status": "research_only",
    },
    "dbu_um": 0.00025,
    "manufacturing_grid_um": 0.00025,
    "layers": {
        "m1": [15, 0],
        "outline": [62, 20],
    },
    "routing_metals": [
        {
            "name": "metal1",
            "layer_role": "m1",
            "min_width_um": 0.022,
            "min_space_um": 0.300,
            "profile_max_width_um": 0.300,
            "width_dependent_spacing": False,
            "spacing_table": [],
        }
    ],
    "devices": {
        "metal1_resistor": {
            "family": "resistor",
            "terminals": ["S+", "F+", "F-", "S-"],
            "measurements": ["kelvin_4t"],
            "doe_axes": ["width_um", "length_um"],
            "required_layers": ["m1"],
            "geometry_source": "reference_geometry",
        }
    },
    "verification": {
        "drc": "not_available",
        "lvs": "not_available",
        "pex": "not_available",
    },
}


def _fail(code: str, message: str, *, details: Mapping[str, Any]) -> None:
    raise AnalysisError(
        code=code,
        message=message,
        details={**dict(details), "production_ready": False},
        next_action="Correct the approved Kelvin profile intent and restart intake.",
    )


def _exact_nm(value: Any, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(
            "INVALID_KELVIN_WORKFLOW_DIMENSION",
            f"{field} must be numeric microns.",
            details={"field": field, "value": value},
        )
    scaled = float(value) * 1000.0
    rounded = round(scaled)
    if abs(scaled - rounded) > 1e-9 or rounded <= 0:
        _fail(
            "KELVIN_WORKFLOW_DIMENSION_OFF_NM_GRID",
            f"{field} must map exactly to a positive integer nanometer value.",
            details={"field": field, "value_um": value},
        )
    return int(rounded)


class KelvinM1PlanningEngine:
    """Translate a complete generic intent into the deterministic SLN001 plan."""

    engine_id = "sln001-kelvin-m1-planner-v1"

    def plan(
        self,
        *,
        design_intent: Mapping[str, Any],
        process_capability: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        frame = design_intent["frame"]
        pads = design_intent["pads"]
        if (
            float(frame["width_um"]) != 2000.0
            or float(frame["height_um"]) != 54.0
            or pads["count"] != 25
            or pads["rows"] != 1
            or [float(value) for value in pads["outline_um"]] != [40.0, 40.0]
        ):
            _fail(
                "KELVIN_WORKFLOW_FRAME_MISMATCH",
                "The SLN001 Kelvin adapter requires its approved 2000x54 um, 25-Pad frame.",
                details={"frame": dict(frame), "pads": dict(pads)},
            )
        if (
            process_capability.get("first_metal_role") != "m1"
            or process_capability.get("layers", {}).get("m1") != [15, 0]
        ):
            _fail(
                "KELVIN_WORKFLOW_LAYER_MISMATCH",
                "The Kelvin golden adapter requires approved M1 stream layer (15,0).",
                details={
                    "first_metal_role": process_capability.get("first_metal_role"),
                    "m1": process_capability.get("layers", {}).get("m1"),
                },
            )
        devices = design_intent["devices"]
        if len(devices) != 6:
            _fail(
                "KELVIN_WORKFLOW_SIX_DUTS_REQUIRED",
                "The Kelvin golden adapter requires six approved resistor splits.",
                details={"dut_count": len(devices)},
            )
        mapping_by_dut: dict[str, dict[str, int]] = {}
        for record in design_intent["terminal_net_pad_map"]:
            mapping_by_dut.setdefault(record["dut_id"], {})[record["terminal"]] = record[
                "pad"
            ]
        splits: list[dict[str, int]] = []
        origins: list[list[float]] = []
        for index, device in enumerate(devices):
            dut_id = device["dut_id"]
            if (
                device["family"] != "resistor"
                or device["device_type"] != "metal1_resistor"
                or device["measurement_type"] != "kelvin_4t"
            ):
                _fail(
                    "KELVIN_WORKFLOW_DEVICE_MISMATCH",
                    "Every Kelvin golden DUT must be an M1 Kelvin-4T resistor.",
                    details={"dut_id": dut_id, "device": dict(device)},
                )
            expected_pads = {
                terminal: index * 4 + offset
                for offset, terminal in enumerate(EXPECTED_TERMINAL_PAD_ORDER, start=1)
            }
            if mapping_by_dut.get(dut_id) != expected_pads:
                _fail(
                    "KELVIN_WORKFLOW_PAD_ROLE_MISMATCH",
                    "Each Kelvin site must use S+,F+,F-,S- on four consecutive Pads.",
                    details={
                        "dut_id": dut_id,
                        "expected": expected_pads,
                        "actual": mapping_by_dut.get(dut_id),
                    },
                )
            parameters = device["parameters"]
            splits.append(
                {
                    "width_nm": _exact_nm(
                        parameters.get("width_um"), field=f"{dut_id}.width_um"
                    ),
                    "length_nm": _exact_nm(
                        parameters.get("length_um"), field=f"{dut_id}.length_um"
                    ),
                }
            )
            origin = device["placement_constraints"].get("origin_um")
            if (
                not isinstance(origin, list)
                or len(origin) != 2
                or any(
                    isinstance(value, bool) or not isinstance(value, (int, float))
                    for value in origin
                )
            ):
                _fail(
                    "KELVIN_WORKFLOW_ORIGIN_REQUIRED",
                    "Each Kelvin split needs an explicit approved origin_um.",
                    details={"dut_id": dut_id, "origin_um": origin},
                )
            origins.append([float(origin[0]), float(origin[1])])

        plan = plan_kelvin_m1_routing_service(
            dimension_semantics=TRANSVERSE_WIDTH_LONGITUDINAL_LENGTH,
            confirm_routing_contract=True,
            splits=splits,
            site_origins_um=origins,
        )
        if plan.get("ok") is not True:
            _fail(
                "KELVIN_WORKFLOW_PLAN_FAILED",
                "The deterministic Kelvin profile did not produce a plan.",
                details={"result": dict(plan)},
            )
        plan_snapshot = immutable_json_copy(plan)
        routing_fingerprint = canonical_sha256(
            {
                "m1": plan_snapshot["m1"],
                "mesh": plan_snapshot["mesh"],
                "force": plan_snapshot["force"],
                "sense": plan_snapshot["sense"],
                "splits": plan_snapshot["splits"],
            }
        )
        return {
            "ok": True,
            "plan": plan_snapshot,
            "plan_sha256": canonical_sha256(plan_snapshot),
            "routing_plan_fingerprint_sha256": routing_fingerprint,
        }


class KelvinM1GenerationEngine:
    """Generate and fresh-reload the Kelvin profile with optional golden XOR."""

    engine_id = "sln001-kelvin-m1-generator-v1"

    def __init__(
        self,
        *,
        template_gds_path: str | Path,
        reference_gds_path: str | Path | None = None,
        reference_top_cell: str | None = None,
        top_cell: str | None = None,
        klayout_executable: str | None = None,
        timeout_seconds: float = 180.0,
    ):
        self.template_gds_path = str(Path(template_gds_path).resolve())
        self.reference_gds_path = (
            None
            if reference_gds_path is None
            else str(Path(reference_gds_path).resolve())
        )
        self.reference_top_cell = reference_top_cell
        self.top_cell = top_cell
        self.klayout_executable = klayout_executable
        self.timeout_seconds = timeout_seconds

    def generate(
        self,
        *,
        design_intent: Mapping[str, Any],
        process_capability: Mapping[str, Any],
        plan: Mapping[str, Any],
        output_path: str,
    ) -> Mapping[str, Any]:
        del design_intent, process_capability
        splits = [
            {"width_nm": item["width_nm"], "length_nm": item["length_nm"]}
            for item in plan["splits"]
        ]
        origins = [list(item["origin_um"]) for item in plan["splits"]]
        target = Path(output_path).resolve()
        result = generate_kelvin_m1_teg_service(
            template_gds_path=self.template_gds_path,
            output_gds_path=str(target),
            work_directory_path=str(target.parent),
            dimension_semantics=TRANSVERSE_WIDTH_LONGITUDINAL_LENGTH,
            confirm_routing_contract=True,
            splits=splits,
            site_origins_um=origins,
            reference_gds_path=self.reference_gds_path,
            reference_top_cell=self.reference_top_cell,
            top_cell=self.top_cell,
            require_reference_equivalence=self.reference_gds_path is not None,
            klayout_executable=self.klayout_executable,
            timeout_seconds=self.timeout_seconds,
        )
        if result.get("ok") is not True:
            _fail(
                "KELVIN_WORKFLOW_GENERATION_FAILED",
                "Kelvin generation or fresh-reload verification failed.",
                details={"result": dict(result)},
            )
        comparison = result.get("reference_comparison")
        reference_verified = self.reference_gds_path is None or (
            isinstance(comparison, Mapping)
            and comparison.get("equivalent") is True
            and all(
                layer.get("geometry_xor_clean") is True
                for layer in comparison.get("layers", [])
            )
        )
        connectivity_verified = (
            result.get("orthogonal_box_only_verified") is True
            and result.get("m1_component_count") == 7
            and result.get("kelvin_direct_top_instance_count") == 6
            and reference_verified
        )
        fingerprint_payload = {
            "top_cell": result.get("top_cell"),
            "dbu_um": result.get("dbu_um"),
            "bbox_um": result.get("bbox_um"),
            "generated_box_counts": result.get("generated_box_counts"),
            "m1_component_count": result.get("m1_component_count"),
            "m1_hole_count": result.get("m1_hole_count"),
            "reference_equivalent": reference_verified,
        }
        return {
            **immutable_json_copy(result),
            "drawing_fingerprint_verified": True,
            "drawing_fingerprint_sha256": canonical_sha256(fingerprint_payload),
            "connectivity_projection_verified": connectivity_verified,
            "connectivity_projection_is_lvs": False,
            "reference_equivalence_verified": reference_verified,
        }
