"""Shared pytest fixtures.

Pytest's per-run system temporary directory is intentionally retained.  A fixed
repository-local ``--basetemp`` made concurrent or interrupted Windows runs contend
for and recursively delete the same path before test setup.
"""

import copy

import pytest

from klayout_mcp.design_contract import TRANSVERSE_WIDTH_LONGITUDINAL_LENGTH
from klayout_mcp.phase1_primitives import plan_metal_resistor_primitive
from klayout_mcp.primitive_verification import geometry_fingerprint, terminal_component_manifest


SYNTHETIC_PROCESS_CAPABILITY = {
    "schema_version": 1,
    "process": {
        "name": "synthetic_test_process",
        "version": "test-v1",
        "evidence_status": "research_only",
    },
    "dbu_um": 0.001,
    "manufacturing_grid_um": 0.001,
    "layers": {
        "well": [1, 0],
        "active": [2, 0],
        "implant_n": [3, 0],
        "implant_p": [4, 0],
        "gate_marker": [5, 0],
        "gate": [6, 0],
        "contact": [7, 0],
        "m1": [8, 0],
        "outline": [9, 0],
    },
    "routing_metals": [
        {
            "name": "first_metal",
            "layer_role": "m1",
            "min_width_um": 0.1,
            "min_space_um": 0.1,
            "profile_max_width_um": 0.4,
            "width_dependent_spacing": True,
            "spacing_table": [
                {
                    "width_over_um": 0.2,
                    "parallel_length_at_least_um": 1.0,
                    "min_space_um": 0.3,
                }
            ],
        }
    ],
    "devices": {
        "example_nmos": {
            "family": "transistor",
            "terminals": ["G", "D", "S", "B"],
            "measurements": ["dc_4t"],
            "doe_axes": ["w_um", "l_um", "sa_um", "sb_um"],
            "required_layers": [
                "well",
                "active",
                "implant_n",
                "implant_p",
                "gate_marker",
                "gate",
                "contact",
                "m1",
            ],
            "geometry_source": "reference_geometry",
        },
        "example_resistor": {
            "family": "resistor",
            "terminals": ["F+", "F-", "S+", "S-"],
            "measurements": ["direct_2t", "kelvin_4t"],
            "doe_axes": ["width_um", "length_um", "orientation_deg"],
            "required_layers": ["m1"],
            "geometry_source": "rule_synthesized",
        },
        "example_capacitor": {
            "family": "capacitor",
            "terminals": ["P", "N"],
            "measurements": ["capacitance_2t"],
            "doe_axes": ["area_um2", "perimeter_um", "aspect_ratio"],
            "required_layers": ["m1"],
            "geometry_source": "rule_synthesized",
        },
    },
    "verification": {
        "drc": "not_available",
        "lvs": "not_available",
        "pex": "not_available",
    },
}


def synthetic_transistor_primitive():
    """Return a process-matched static primitive for process-neutral core tests."""

    operations = [
        {"type": "add_box", "cell": "DUT", "layer": "well", "bbox_um": [-21.0, -6.0, 21.0, 26.0]},
        {"type": "add_box", "cell": "DUT", "layer": "active", "bbox_um": [-0.3, -0.25, 0.3, 0.25]},
        {"type": "add_box", "cell": "DUT", "layer": "implant_n", "bbox_um": [-0.3, -0.25, 0.3, 0.25]},
        {"type": "add_box", "cell": "DUT", "layer": "implant_p", "bbox_um": [-0.1, -5.1, 0.1, -4.9]},
        {"type": "add_box", "cell": "DUT", "layer": "gate_marker", "bbox_um": [-0.2, -0.35, 0.2, 0.35]},
        {"type": "add_box", "cell": "DUT", "layer": "gate", "bbox_um": [-0.05, -0.35, 0.05, 25.2]},
        {"type": "add_box", "cell": "DUT", "layer": "contact", "bbox_um": [-20.05, -0.05, -19.95, 0.05]},
        {"type": "add_box", "cell": "DUT", "layer": "contact", "bbox_um": [19.95, -0.05, 20.05, 0.05]},
        {"type": "add_box", "cell": "DUT", "layer": "contact", "bbox_um": [-0.05, 24.95, 0.05, 25.05]},
        {"type": "add_box", "cell": "DUT", "layer": "contact", "bbox_um": [-0.05, -5.05, 0.05, -4.95]},
        {"type": "add_box", "cell": "DUT", "layer": "m1", "bbox_um": [-20.3, -0.3, -19.7, 0.3]},
        {"type": "add_box", "cell": "DUT", "layer": "m1", "bbox_um": [19.7, -0.3, 20.3, 0.3]},
        {"type": "add_box", "cell": "DUT", "layer": "m1", "bbox_um": [-0.3, 24.7, 0.3, 25.3]},
        {"type": "add_box", "cell": "DUT", "layer": "m1", "bbox_um": [-0.3, -5.3, 0.3, -4.7]},
    ]
    terminals = {"S": [-20.0, 0.0], "D": [20.0, 0.0], "G": [0.0, 25.0], "B": [0.0, -5.0]}
    layer_roles = sorted({item["layer"] for item in operations})
    return {
        "ok": True,
        "geometry_status": "process_gated_primitive_not_routed",
        "production_ready": False,
        "process": dict(SYNTHETIC_PROCESS_CAPABILITY["process"]),
        "device": {"name": "example_nmos", "family": "transistor", "measurement": "dc_4t"},
        "dbu_um": SYNTHETIC_PROCESS_CAPABILITY["dbu_um"],
        "cells": ["DUT"],
        "layers": [
            {
                "name": role,
                "layer": SYNTHETIC_PROCESS_CAPABILITY["layers"][role][0],
                "datatype": SYNTHETIC_PROCESS_CAPABILITY["layers"][role][1],
            }
            for role in layer_roles
        ],
        "operations": operations,
        "terminals_um": terminals,
        "bbox_um": [-21.0, -6.0, 21.0, 26.0],
        "parameters": {"w_um": 1.0, "l_um": 0.1},
        "external_routing_included": False,
        "verification": {
            "box_only": True,
            "required_layers_present": True,
            "terminal_m1_landings": {key: True for key in terminals},
            "geometry_fingerprint_sha256": geometry_fingerprint({"operations": operations}),
            "terminal_components": terminal_component_manifest(
                operations, terminals, layer_role="m1"
            ),
            "drc": "not_available",
            "lvs": "not_available",
            "pex": "not_available",
        },
    }
from klayout_mcp.teg_planning import plan_teg_measurement_request


@pytest.fixture
def ready_phase1_inputs():
    """Return one verified direct-resistor input set without importing another test module."""

    profile = copy.deepcopy(SYNTHETIC_PROCESS_CAPABILITY)
    profile["devices"]["example_resistor"] = {
        "family": "resistor",
        "terminals": ["P", "N"],
        "measurements": ["direct_2t"],
        "doe_axes": ["width_um", "length_um"],
        "required_layers": ["m1"],
        "geometry_source": "rule_synthesized",
    }
    primitive = plan_metal_resistor_primitive(
        process_capability=profile,
        device_name="example_resistor",
        layer_role="m1",
        measurement="direct_2t",
        width_um=0.1,
        length_um=1.0,
        terminal_size_um=0.3,
        dimension_semantics=TRANSVERSE_WIDTH_LONGITUDINAL_LENGTH,
    )
    assignments = [
        {"dut": "R1", "family": "resistor", "terminal": "N", "net": "RN", "pad": 12},
        {"dut": "R1", "family": "resistor", "terminal": "P", "net": "RP", "pad": 13},
    ]
    contracts = [
        {
            "dut": "R1",
            "family": "resistor",
            "measurement": "direct_2t",
            "required_terminals": ["P", "N"],
        }
    ]
    connections = [
        {
            "connection_id": "R1:N",
            "net": "RN",
            "start_um": [959.35, 27.0],
            "end_um": [920.0, 27.0],
            "width_um": 0.3,
            "clear_space_um": 0.3,
        },
        {
            "connection_id": "R1:P",
            "net": "RP",
            "start_um": [960.65, 27.0],
            "end_um": [1000.0, 27.0],
            "width_um": 0.3,
            "clear_space_um": 0.3,
        },
    ]
    request_plan = plan_teg_measurement_request(
        device_families=["resistor"],
        process_profile="synthetic_test_process",
        process_profile_version="test-v1",
        dut_count=1,
        approved_layermap=True,
        approved_design_rules=True,
        terminal_mapping_confirmed=True,
        measurement_bias_confirmed=True,
        routing_obstacles_confirmed=True,
        dimension_semantics=TRANSVERSE_WIDTH_LONGITUDINAL_LENGTH,
        terminal_assignments=assignments,
        dut_terminal_contracts=contracts,
        routing_connections=connections,
    )
    assert request_plan["planning_status"] == "ready_for_geometry"
    return profile, primitive, request_plan
