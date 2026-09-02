from __future__ import annotations

import copy

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.workflow_manifest import (
    build_job_manifest,
    canonical_json_bytes,
    canonical_sha256,
    canonicalization_contract,
    validate_approved_design_intent_reference,
    validate_design_intent_draft,
    validate_measurement_manifest,
    workflow_document_contract,
)


HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64


def _draft(family: str = "resistor") -> dict:
    terminals = {
        "transistor": [("G", "force"), ("D", "force_sense"), ("S", "common"), ("B", "bias")],
        "resistor": [("F+", "force"), ("F-", "force"), ("S+", "sense"), ("S-", "sense")],
        "capacitor": [("HI", "force_sense"), ("LO", "common")],
    }[family]
    return {
        "schema_version": 1,
        "intent_id": f"intent-{family}",
        "units": "um",
        "process": {
            "profile": "example",
            "version": "1",
            "capability_sha256": HASH_A,
        },
        "frame": {
            "width_um": 2000.0,
            "height_um": 54.0,
            "origin_um": [0.0, 0.0],
            "allowed_boundary_um": [0.0, 0.0, 2000.0, 54.0],
        },
        "pads": {
            "count": 25,
            "rows": 1,
            "outline_um": [40.0, 40.0],
            "numbering": "left_to_right",
            "reserved_roles": {"25": "body_or_common"},
            "pitch_um": 80.0,
        },
        "devices": [
            {
                "dut_id": "D1",
                "family": family,
                "device_type": f"example_{family}",
                "measurement_type": "direct",
                "parameters": {"width_um": 0.1, "length_um": 1.0},
                "doe": {},
                "placement_constraints": {},
            }
        ],
        "terminal_contracts": [
            {
                "dut_id": "D1",
                "terminals": [
                    {"name": name, "electrical_role": role} for name, role in terminals
                ],
            }
        ],
        "terminal_net_pad_map": [
            {
                "dut_id": "D1",
                "terminal": name,
                "net": f"D1_{name}",
                "pad": index,
                "shared_net_explicit": False,
            }
            for index, (name, _role) in enumerate(terminals, start=1)
        ],
        "measurement_requirements": {
            "stimuli": [
                {
                    "dut_id": "D1",
                    "terminal": terminals[0][0],
                    "mode": "voltage_sweep",
                    "source_mode": "voltage",
                    "program": {"kind": "dc_value", "value": 0.1, "unit": "V"},
                    "compliance": {"quantity": "current", "limit": 0.001, "unit": "A"},
                    "polarity": "positive",
                    "frequency_hz": None,
                }
            ],
            "observables": [
                {
                    "dut_id": "D1",
                    "terminal": terminals[-1][0],
                    "mode": "measure",
                    "quantity": "response",
                    "unit": "A",
                }
            ],
            "biases": [],
            "timing": {
                "settling_s": 0.1,
                "integration": {"mode": "default"},
                "hold_s": 0.0,
                "delay_s": 0.0,
            },
            "environment": {},
            "safety_envelope": {
                "limits": {"max_voltage_v": 1.0},
                "source_reference": "user:test",
                "em_current_density_evidence": None,
            },
        },
        "routing_policy": {
            "manhattan_only": True,
            "prefer_first_metal": True,
            "allowed_layer_roles": ["m1"],
            "escalation_policy": "user_approval_required",
        },
        "verification_policy": {
            "internal_checks": ["fresh_reload", "projected_connectivity"],
            "external_evidence_required": ["drc", "lvs"],
        },
        "output_policy": {
            "format": "gds",
            "top_cell": "TEG",
            "new_output_required": True,
        },
        "unresolved_questions": [],
    }


def _draft_evidence() -> dict:
    return {"draft_schema_valid": True, "unresolved_questions_zero": True}


def _job_manifest() -> dict:
    return {
        "schema_version": 1,
        "job_id": "job-1",
        "parent_manifest_sha256": None,
        "design_intent_sha256": HASH_A,
        "approved_intent_sha256": None,
        "process_capability_sha256": HASH_B,
        "stage": "intent_draft_complete",
        "evidence": _draft_evidence(),
        "normalized_inputs": {},
        "outputs": [],
        "fingerprints": {},
        "runtime": {},
        "warnings": [],
        "blockers": [],
        "refusal_codes": [],
        "created_at": "2026-08-30T00:00:00+09:00",
        "completed_at": None,
        "atomic_promotion": {"promoted": False},
    }


def _measurement_manifest_for_intent(intent: dict) -> dict:
    intent_result = validate_design_intent_draft(intent)
    role_by_ref = {
        (contract["dut_id"], terminal["name"]): terminal["electrical_role"]
        for contract in intent["terminal_contracts"]
        for terminal in contract["terminals"]
    }
    records = intent["terminal_net_pad_map"]
    stimulus_requirement = intent["measurement_requirements"]["stimuli"][0]
    observable_requirement = intent["measurement_requirements"]["observables"][0]
    return {
        "schema_version": 1,
        "design_intent_sha256": intent_result["canonical_sha256"],
        "generated_layout_sha256": HASH_B,
        "dut_pin_map": [
            {
                "dut_id": record["dut_id"],
                "terminal": record["terminal"],
                "net": record["net"],
                "pad": record["pad"],
                "probe_pin": f"P{index}",
                "instrument_channel": f"CH{index}",
                "electrical_role": role_by_ref[(record["dut_id"], record["terminal"])],
            }
            for index, record in enumerate(records, start=1)
        ],
        "electrical_topology": {"type": "direct", "connections": [], "guards": []},
        "stimuli": [
            {
                "stimulus_id": "s1",
                "requirement_kind": "stimulus",
                "requirement_mode": stimulus_requirement["mode"],
                "target": {
                    "dut_id": stimulus_requirement["dut_id"],
                    "terminal": stimulus_requirement["terminal"],
                },
                "source_mode": stimulus_requirement["source_mode"],
                "program": copy.deepcopy(stimulus_requirement["program"]),
                "compliance": copy.deepcopy(stimulus_requirement["compliance"]),
                "polarity": stimulus_requirement["polarity"],
                "frequency_hz": stimulus_requirement["frequency_hz"],
            }
        ],
        "observables": [
            {
                "label": "response",
                "requirement_mode": observable_requirement["mode"],
                "quantity": observable_requirement["quantity"],
                "unit": observable_requirement["unit"],
                "source": {
                    "dut_id": observable_requirement["dut_id"],
                    "terminal": observable_requirement["terminal"],
                },
            }
        ],
        "timing": copy.deepcopy(intent["measurement_requirements"]["timing"]),
        "environment": copy.deepcopy(intent["measurement_requirements"]["environment"]),
        "safety_envelope": copy.deepcopy(
            intent["measurement_requirements"]["safety_envelope"]
        ),
        "calibration_and_deembedding": {
            "required": False,
            "calibration_plane": "probe_tip",
            "reference_duts": [],
        },
    }


def _shared_pad_intent() -> dict:
    intent = _draft("transistor")
    second_device = copy.deepcopy(intent["devices"][0])
    second_device["dut_id"] = "D2"
    intent["devices"].append(second_device)
    second_contract = copy.deepcopy(intent["terminal_contracts"][0])
    second_contract["dut_id"] = "D2"
    intent["terminal_contracts"].append(second_contract)
    for record in intent["terminal_net_pad_map"]:
        record["shared_net_explicit"] = True
    for record in copy.deepcopy(intent["terminal_net_pad_map"]):
        record["dut_id"] = "D2"
        intent["terminal_net_pad_map"].append(record)
    return intent


def _shared_pad_measurement() -> dict:
    intent = _shared_pad_intent()
    manifest = _measurement_manifest_for_intent(intent)
    for pin in manifest["dut_pin_map"]:
        pin["probe_pin"] = f"P{pin['pad']}"
        pin["instrument_channel"] = f"CH{pin['pad']}"
    manifest["observables"][0]["source"] = {"dut_id": "D1", "terminal": "B"}
    return manifest


def test_canonical_hash_ignores_json_object_key_order() -> None:
    first = {"b": {"y": 2, "x": 1}, "a": [3, 4]}
    second = {"a": [3, 4], "b": {"x": 1, "y": 2}}

    assert canonical_sha256(first) == canonical_sha256(second)


def test_canonical_hash_normalizes_integral_float_and_integer() -> None:
    assert canonical_sha256({"width": 1}) == canonical_sha256({"width": 1.0})


def test_named_canonicalization_profile_has_a_fixed_cross_language_fixture() -> None:
    value = {"z": "μ", "a": 1.0, "nested": {"b": 2, "a": 0.125}}

    assert canonical_json_bytes(value) == (
        b'{"a":1,"nested":{"a":0.125,"b":2},"z":"\xce\xbc"}'
    )
    assert canonical_sha256(value) == (
        "95010f122f1121861e371e96944e12c2d6eb170d32f04ec85c2896d907f40063"
    )
    assert canonicalization_contract()["rfc8785_claimed"] is False
    contract = workflow_document_contract()
    assert contract["schema_frozen"] is True
    assert contract["approval_reference_shape_is_trusted_approval"] is False
    assert (
        contract["schema_discovery"]["DesignIntentDraft"]
        ["canonical_nested_template_tool"]
        == "teg_intake"
    )
    assert contract["schema_discovery"]["ApprovedDesignIntent"][
        "model_may_self_issue"
    ] is False
    assert contract["schema_discovery"]["MeasurementManifest"][
        "means_executable_tester_program"
    ] is False


def test_one_field_change_changes_draft_hash() -> None:
    first = _draft()
    second = copy.deepcopy(first)
    second["frame"]["height_um"] = 60.0

    assert validate_design_intent_draft(first)["canonical_sha256"] != (
        validate_design_intent_draft(second)["canonical_sha256"]
    )


@pytest.mark.parametrize("family", ["transistor", "resistor", "capacitor"])
def test_all_phase1_families_share_one_top_level_schema(family: str) -> None:
    result = validate_design_intent_draft(_draft(family))

    assert result["ok"] is True
    assert result["device_families"] == [family]
    assert result["draft_complete"] is True
    assert result["authorizes_planning"] is False
    assert result["authorizes_generation"] is False


def test_kelvin_specific_fields_are_not_common_schema_requirements() -> None:
    result = validate_design_intent_draft(_draft("capacitor"))

    assert "kelvin" not in result["document"]
    assert result["ok"] is True


def test_unknown_schema_version_is_refused_without_migration() -> None:
    draft = _draft()
    draft["schema_version"] = 2

    with pytest.raises(AnalysisError) as caught:
        validate_design_intent_draft(draft)

    assert caught.value.code == "UNSUPPORTED_WORKFLOW_SCHEMA_VERSION"
    assert caught.value.details["implicit_migration_allowed"] is False


def test_implicit_shared_net_is_rejected() -> None:
    draft = _draft("capacitor")
    draft["terminal_net_pad_map"][1]["net"] = draft["terminal_net_pad_map"][0]["net"]

    with pytest.raises(AnalysisError) as caught:
        validate_design_intent_draft(draft)

    assert caught.value.code == "IMPLICIT_DESIGN_INTENT_NET_SHARING"


def test_design_measurement_requirement_rejects_unknown_terminal() -> None:
    draft = _draft("resistor")
    draft["measurement_requirements"]["stimuli"][0]["terminal"] = "UNKNOWN"

    with pytest.raises(AnalysisError) as caught:
        validate_design_intent_draft(draft)

    assert caught.value.code == "MEASUREMENT_REQUIREMENT_TERMINAL_UNKNOWN"


def test_reference_shape_validation_does_not_verify_approval() -> None:
    reference = {
        "schema_version": 1,
        "draft_sha256": HASH_A,
        "process_capability_sha256": HASH_B,
        "source_artifact_sha256s": {"layermap": HASH_C},
        "approval_scope": "planning_only",
        "output_classes": ["nonproduction_gds"],
        "signer_reference": "trusted-client:user-1",
        "scheme_id": "future-verifier-v1",
        "attestation_reference": "store://approval/1",
        "approved_at": "2026-08-30T00:00:00+09:00",
    }

    result = validate_approved_design_intent_reference(reference)

    assert result["reference_shape_valid"] is True
    assert result["approval_verified"] is False
    assert result["authorizes_planning"] is False
    assert result["authorizes_generation"] is False


def test_job_manifest_hash_chain_changes_with_parent() -> None:
    parent = {
        "schema_version": 1,
        "job_id": "job-1",
        "parent_manifest_sha256": None,
        "design_intent_sha256": HASH_B,
        "approved_intent_sha256": None,
        "process_capability_sha256": HASH_C,
        "stage": "intent_draft_complete",
        "evidence": _draft_evidence(),
        "normalized_inputs": {},
        "outputs": [],
        "fingerprints": {},
        "runtime": {"package": "0.5.0"},
        "warnings": [],
        "blockers": ["trusted approval missing"],
        "refusal_codes": [],
        "created_at": "2026-08-30T00:00:00+09:00",
        "completed_at": None,
        "atomic_promotion": {"promoted": False},
    }
    changed_parent = copy.deepcopy(parent)
    changed_parent["warnings"] = ["different immutable parent"]
    child = copy.deepcopy(parent)
    child["parent_manifest_sha256"] = canonical_sha256(parent)
    child["created_at"] = "2026-08-30T00:01:00+09:00"
    changed_child = copy.deepcopy(child)
    changed_child["parent_manifest_sha256"] = canonical_sha256(changed_parent)

    first = build_job_manifest(child, parent_manifest=parent)
    second = build_job_manifest(changed_child, parent_manifest=changed_parent)

    assert first["manifest_sha256"] != second["manifest_sha256"]
    assert first["content_addressed"] is True
    assert first["mutable_in_place"] is False
    assert first["production_ready"] is False


def test_job_manifest_rejects_unattained_stage() -> None:
    manifest = {
        "schema_version": 1,
        "job_id": "job-1",
        "parent_manifest_sha256": None,
        "design_intent_sha256": HASH_A,
        "approved_intent_sha256": None,
        "process_capability_sha256": HASH_B,
        "stage": "drawing_complete",
        "evidence": _draft_evidence(),
        "normalized_inputs": {},
        "outputs": [],
        "fingerprints": {},
        "runtime": {},
        "warnings": [],
        "blockers": [],
        "refusal_codes": [],
        "created_at": "2026-08-30T00:00:00+09:00",
        "completed_at": None,
        "atomic_promotion": {"promoted": False},
    }

    with pytest.raises(AnalysisError) as caught:
        build_job_manifest(manifest)

    assert caught.value.code == "JOB_MANIFEST_STAGE_NOT_ATTAINED"


def test_job_manifest_requires_exact_parent_snapshot() -> None:
    parent = {
        "schema_version": 1,
        "job_id": "job-1",
        "parent_manifest_sha256": None,
        "design_intent_sha256": HASH_A,
        "approved_intent_sha256": None,
        "process_capability_sha256": HASH_B,
        "stage": "intent_draft_complete",
        "evidence": _draft_evidence(),
        "normalized_inputs": {},
        "outputs": [],
        "fingerprints": {},
        "runtime": {},
        "warnings": [],
        "blockers": [],
        "refusal_codes": [],
        "created_at": "2026-08-30T00:00:00+09:00",
        "completed_at": None,
        "atomic_promotion": {"promoted": False},
    }
    child = copy.deepcopy(parent)
    child["parent_manifest_sha256"] = canonical_sha256(parent)
    child["job_id"] = "different-job"

    with pytest.raises(AnalysisError) as caught:
        build_job_manifest(child, parent_manifest=parent)

    assert caught.value.code == "JOB_MANIFEST_IDENTITY_DRIFT"


def test_job_manifest_rejects_missing_parent_snapshot() -> None:
    parent = {
        "schema_version": 1,
        "job_id": "job-1",
        "parent_manifest_sha256": None,
        "design_intent_sha256": HASH_A,
        "approved_intent_sha256": None,
        "process_capability_sha256": HASH_B,
        "stage": "intent_draft_complete",
        "evidence": _draft_evidence(),
        "normalized_inputs": {},
        "outputs": [],
        "fingerprints": {},
        "runtime": {},
        "warnings": [],
        "blockers": [],
        "refusal_codes": [],
        "created_at": "2026-08-30T00:00:00+09:00",
        "completed_at": None,
        "atomic_promotion": {"promoted": False},
    }
    child = copy.deepcopy(parent)
    child["parent_manifest_sha256"] = canonical_sha256(parent)

    with pytest.raises(AnalysisError) as caught:
        build_job_manifest(child)

    assert caught.value.code == "JOB_MANIFEST_PARENT_REQUIRED"


def test_job_manifest_rejects_skipped_parent_transition() -> None:
    parent = _job_manifest()
    child = copy.deepcopy(parent)
    child["parent_manifest_sha256"] = canonical_sha256(parent)
    child["approved_intent_sha256"] = HASH_C
    child["stage"] = "plan_complete"
    child["evidence"].update(
        {
            "approval_backend_trusted": True,
            "approval_verified": True,
            "plan_fingerprint_verified": True,
            "routing_plan_complete": True,
        }
    )

    with pytest.raises(AnalysisError) as caught:
        build_job_manifest(child, parent_manifest=parent)

    assert caught.value.code == "INVALID_JOB_MANIFEST_TRANSITION"


def test_job_manifest_rejects_output_without_content_hash() -> None:
    manifest = _job_manifest()
    manifest["outputs"] = [
        {"role": "layout", "content_sha256": "not-a-hash", "reference": "out.gds"}
    ]

    with pytest.raises(AnalysisError) as caught:
        build_job_manifest(manifest)

    assert caught.value.code == "INVALID_CONTENT_HASH"


def test_measurement_manifest_binds_generic_measurement_to_layout_hash() -> None:
    intent = _draft("capacitor")
    intent["measurement_requirements"]["observables"][0].update(
        {"quantity": "capacitance", "unit": "F"}
    )
    intent["measurement_requirements"]["stimuli"][0].update(
        {
            "source_mode": "ac_voltage",
            "program": {"kind": "ac_amplitude", "amplitude": 0.03, "unit": "V"},
            "compliance": {"quantity": "current", "limit": 0.001, "unit": "A"},
            "polarity": "bipolar",
            "frequency_hz": 1000.0,
        }
    )
    intent["measurement_requirements"]["timing"] = {
        "settling_s": 0.1,
        "integration": {"mode": "instrument_default"},
        "hold_s": 0.0,
        "delay_s": 0.0,
    }
    intent["measurement_requirements"]["environment"] = {"temperature_c": 25.0}
    intent["measurement_requirements"]["safety_envelope"] = {
        "limits": {"max_voltage_v": 1.0},
        "source_reference": "user-confirmed:test",
        "em_current_density_evidence": None,
    }
    intent_hash = validate_design_intent_draft(intent)["canonical_sha256"]
    manifest = {
        "schema_version": 1,
        "design_intent_sha256": intent_hash,
        "generated_layout_sha256": HASH_B,
        "dut_pin_map": [
            {
                "dut_id": "D1",
                "terminal": "HI",
                "net": "D1_HI",
                "pad": 1,
                "probe_pin": "P1",
                "instrument_channel": "LCR.HI",
                "electrical_role": "force_sense",
            },
            {
                "dut_id": "D1",
                "terminal": "LO",
                "net": "D1_LO",
                "pad": 2,
                "probe_pin": "P2",
                "instrument_channel": "LCR.LO",
                "electrical_role": "common",
            },
        ],
        "electrical_topology": {"type": "direct_2t", "connections": [], "guards": []},
        "stimuli": [
            {
                "stimulus_id": "ac1",
                "requirement_kind": "stimulus",
                "requirement_mode": "voltage_sweep",
                "target": {"dut_id": "D1", "terminal": "HI"},
                "source_mode": "ac_voltage",
                "program": {"kind": "ac_amplitude", "amplitude": 0.03, "unit": "V"},
                "compliance": {"quantity": "current", "limit": 0.001, "unit": "A"},
                "polarity": "bipolar",
                "frequency_hz": 1000.0,
            }
        ],
        "observables": [
            {
                "label": "capacitance",
                "requirement_mode": "measure",
                "quantity": "capacitance",
                "unit": "F",
                "source": {"dut_id": "D1", "terminal": "LO"},
            }
        ],
        "timing": {
            "settling_s": 0.1,
            "integration": {"mode": "instrument_default"},
            "hold_s": 0.0,
            "delay_s": 0.0,
        },
        "environment": {"temperature_c": 25.0},
        "safety_envelope": {
            "limits": {"max_voltage_v": 1.0},
            "source_reference": "user-confirmed:test",
            "em_current_density_evidence": None,
        },
        "calibration_and_deembedding": {
            "required": False,
            "calibration_plane": "probe_tip",
            "reference_duts": [],
        },
    }

    result = validate_measurement_manifest(manifest, design_intent=intent)

    assert result["schema_valid"] is True
    assert result["intent_binding_verified"] is True
    assert result["measurement_manifest_verified"] is False
    assert result["generated_layout_sha256"] == HASH_B
    assert result["instrument_commands_generated"] is False
    assert result["production_ready"] is False


@pytest.mark.parametrize("family", ["transistor", "resistor", "capacitor"])
def test_measurement_manifest_schema_covers_all_phase1_families(family: str) -> None:
    intent = _draft(family)
    result = validate_measurement_manifest(
        _measurement_manifest_for_intent(intent),
        design_intent=intent,
    )

    assert result["schema_valid"] is True
    assert result["intent_binding_verified"] is True


def test_measurement_manifest_rejects_missing_or_surplus_intent_semantics() -> None:
    intent = _draft("resistor")
    manifest = _measurement_manifest_for_intent(intent)
    manifest["observables"].append(
        {
            **copy.deepcopy(manifest["observables"][0]),
            "label": "unrequested-extra",
        }
    )

    with pytest.raises(AnalysisError) as caught:
        validate_measurement_manifest(manifest, design_intent=intent)

    assert caught.value.code == "MEASUREMENT_REQUIREMENT_COVERAGE_MISMATCH"


def test_measurement_manifest_rejects_declared_quantity_or_unit_drift() -> None:
    intent = _draft("resistor")
    manifest = _measurement_manifest_for_intent(intent)
    manifest["observables"][0]["unit"] = "V"

    with pytest.raises(AnalysisError) as caught:
        validate_measurement_manifest(manifest, design_intent=intent)

    assert caught.value.code == "MEASUREMENT_REQUIREMENT_SEMANTIC_MISMATCH"


def test_measurement_manifest_rejects_self_declared_mode_with_different_program() -> None:
    intent = _draft("resistor")
    manifest = _measurement_manifest_for_intent(intent)
    manifest["stimuli"][0].update(
        {
            "source_mode": "current",
            "program": {"kind": "dc_value", "value": 0.001, "unit": "A"},
        }
    )

    with pytest.raises(AnalysisError) as caught:
        validate_measurement_manifest(manifest, design_intent=intent)

    assert caught.value.code == "MEASUREMENT_EXECUTION_INTENT_MISMATCH"


def test_measurement_manifest_cannot_relax_approved_safety() -> None:
    intent = _draft("resistor")
    manifest = _measurement_manifest_for_intent(intent)
    manifest["safety_envelope"]["limits"]["max_voltage_v"] = 10.0

    with pytest.raises(AnalysisError) as caught:
        validate_measurement_manifest(manifest, design_intent=intent)

    assert caught.value.code == "MEASUREMENT_SAFETY_INTENT_MISMATCH"


def test_measurement_manifest_cannot_drift_approved_timing() -> None:
    intent = _draft("resistor")
    manifest = _measurement_manifest_for_intent(intent)
    manifest["timing"]["settling_s"] = 1.0

    with pytest.raises(AnalysisError) as caught:
        validate_measurement_manifest(manifest, design_intent=intent)

    assert caught.value.code == "MEASUREMENT_TIMING_INTENT_MISMATCH"


def test_measurement_manifest_balances_duplicate_terminal_requirements_by_multiplicity() -> None:
    intent = _draft("resistor")
    duplicate = copy.deepcopy(intent["measurement_requirements"]["stimuli"][0])
    duplicate.update({"quantity": "voltage", "unit": "V"})
    intent["measurement_requirements"]["stimuli"].append(duplicate)
    manifest = _measurement_manifest_for_intent(intent)
    second = copy.deepcopy(manifest["stimuli"][0])
    second.update(
        {
            "stimulus_id": "s2",
            "requirement_quantity": "voltage",
            "requirement_unit": "V",
        }
    )
    manifest["stimuli"].append(second)

    result = validate_measurement_manifest(manifest, design_intent=intent)

    assert result["intent_binding_verified"] is True


def test_measurement_manifest_rejects_one_channel_shared_by_different_pads() -> None:
    intent = _draft("capacitor")
    manifest = _measurement_manifest_for_intent(intent)
    manifest["dut_pin_map"][1]["instrument_channel"] = manifest["dut_pin_map"][0][
        "instrument_channel"
    ]

    with pytest.raises(AnalysisError) as caught:
        validate_measurement_manifest(manifest, design_intent=intent)

    assert caught.value.code == "DUPLICATE_MEASUREMENT_ACCESS_CHANNEL"


def test_measurement_manifest_rejects_stimulus_above_declared_safety_limit() -> None:
    intent = _draft("resistor")
    intent["measurement_requirements"]["stimuli"][0]["program"] = {
        "kind": "dc_value",
        "value": 1.1,
        "unit": "V",
    }
    with pytest.raises(AnalysisError) as caught:
        validate_design_intent_draft(intent)

    assert caught.value.code == "DESIGN_INTENT_SAFETY_LIMIT_EXCEEDED"


def test_measurement_manifest_rejects_terminal_pad_drift() -> None:
    intent = _draft("capacitor")
    intent_hash = validate_design_intent_draft(intent)["canonical_sha256"]
    manifest = {
        "schema_version": 1,
        "design_intent_sha256": intent_hash,
        "generated_layout_sha256": HASH_B,
        "dut_pin_map": [
            {
                "dut_id": "D1",
                "terminal": terminal,
                "net": f"D1_{terminal}",
                "pad": 25 if terminal == "HI" else 2,
                "probe_pin": f"P{index}",
                "instrument_channel": f"CH{index}",
                "electrical_role": role,
            }
            for index, (terminal, role) in enumerate(
                [("HI", "force_sense"), ("LO", "common")], start=1
            )
        ],
        "electrical_topology": {"type": "direct_2t", "connections": [], "guards": []},
        "stimuli": [{
            "stimulus_id": "s1",
            "requirement_kind": "stimulus",
            "requirement_mode": "voltage_sweep",
            "target": {"dut_id": "D1", "terminal": "HI"},
            "source_mode": "voltage",
            "program": {"kind": "dc_value", "value": 0.1, "unit": "V"},
            "compliance": {"quantity": "current", "limit": 0.001, "unit": "A"},
            "polarity": "positive",
            "frequency_hz": None,
        }],
        "observables": [{
            "label": "i",
            "requirement_mode": "measure",
            "quantity": "response",
            "unit": "A",
            "source": {"dut_id": "D1", "terminal": "LO"},
        }],
        "timing": {"settling_s": 0, "integration": {}, "hold_s": 0, "delay_s": 0},
        "environment": {},
        "safety_envelope": {
            "limits": {},
            "source_reference": "user:test",
            "em_current_density_evidence": None,
        },
        "calibration_and_deembedding": {
            "required": False,
            "calibration_plane": "probe_tip",
            "reference_duts": [],
        },
    }

    with pytest.raises(AnalysisError) as caught:
        validate_measurement_manifest(manifest, design_intent=intent)

    assert caught.value.code == "MEASUREMENT_PIN_INTENT_MISMATCH"


def test_measurement_manifest_requires_calibration_reference_when_enabled() -> None:
    intent = _draft("resistor")
    manifest = _measurement_manifest_for_intent(intent)
    manifest["calibration_and_deembedding"]["required"] = True

    with pytest.raises(AnalysisError) as caught:
        validate_measurement_manifest(manifest, design_intent=intent)

    assert caught.value.code == "CALIBRATION_REFERENCE_REQUIRED"


def test_measurement_manifest_rejects_negative_timing() -> None:
    intent = _draft("transistor")
    manifest = _measurement_manifest_for_intent(intent)
    manifest["timing"]["settling_s"] = -0.1

    with pytest.raises(AnalysisError) as caught:
        validate_measurement_manifest(manifest, design_intent=intent)

    assert caught.value.code == "INVALID_MEASUREMENT_TIMING"


def test_measurement_manifest_rejects_unknown_stimulus_terminal() -> None:
    intent = _draft("capacitor")
    manifest = _measurement_manifest_for_intent(intent)
    manifest["stimuli"][0]["target"]["terminal"] = "UNKNOWN"

    with pytest.raises(AnalysisError) as caught:
        validate_measurement_manifest(manifest, design_intent=intent)

    assert caught.value.code == "MEASUREMENT_STIMULUS_TERMINAL_UNKNOWN"


def test_shared_pad_measurement_requires_inactive_terminal_policy() -> None:
    intent = _shared_pad_intent()
    manifest = _shared_pad_measurement()

    with pytest.raises(AnalysisError) as exc_info:
        validate_measurement_manifest(manifest, design_intent=intent)

    assert exc_info.value.code == "SHARED_PAD_INACTIVE_TERMINAL_POLICY_REQUIRED"
    assert exc_info.value.details["drawing_blocked"] is False


def test_shared_pad_serial_policy_covers_every_inactive_terminal() -> None:
    intent = _shared_pad_intent()
    manifest = _shared_pad_measurement()
    terminals = [
        terminal["name"] for terminal in intent["terminal_contracts"][1]["terminals"]
    ]
    manifest["electrical_topology"]["inactive_terminal_policy"] = {
        "execution_mode": "serial",
        "active_dut_ids": ["D1"],
        "inactive_terminal_states": [
            {
                "dut_id": "D2",
                "terminal": terminal,
                "state": "follow_shared_pad",
                "reference": f"terminal:D1:{terminal}",
            }
            for terminal in terminals
        ],
    }

    result = validate_measurement_manifest(manifest, design_intent=intent)

    assert result["inactive_terminal_policy_complete"] is True
    assert result["measurement_manifest_verified"] is False


def test_shared_pad_policy_rejects_incomplete_inactive_terminal_coverage() -> None:
    intent = _shared_pad_intent()
    manifest = _shared_pad_measurement()
    manifest["electrical_topology"]["inactive_terminal_policy"] = {
        "execution_mode": "serial",
        "active_dut_ids": ["D1"],
        "inactive_terminal_states": [
            {
                "dut_id": "D2",
                "terminal": "G",
                "state": "follow_shared_pad",
                "reference": "terminal:D1:G",
            }
        ],
    }

    with pytest.raises(AnalysisError) as exc_info:
        validate_measurement_manifest(manifest, design_intent=intent)

    assert exc_info.value.code == "INACTIVE_TERMINAL_POLICY_INCOMPLETE"


def test_shared_pad_policy_rejects_independent_state_on_active_pad() -> None:
    intent = _shared_pad_intent()
    manifest = _shared_pad_measurement()
    terminals = [
        terminal["name"] for terminal in intent["terminal_contracts"][1]["terminals"]
    ]
    manifest["electrical_topology"]["inactive_terminal_policy"] = {
        "execution_mode": "serial",
        "active_dut_ids": ["D1"],
        "inactive_terminal_states": [
            {"dut_id": "D2", "terminal": terminal, "state": "float"}
            for terminal in terminals
        ],
    }

    with pytest.raises(AnalysisError) as exc_info:
        validate_measurement_manifest(manifest, design_intent=intent)

    assert exc_info.value.code == "INACTIVE_SHARED_PAD_STATE_CONFLICT"


def test_shared_pad_policy_rejects_conflicting_active_stimuli() -> None:
    intent = _shared_pad_intent()
    intent["measurement_requirements"]["stimuli"].append(
        {
            **copy.deepcopy(intent["measurement_requirements"]["stimuli"][0]),
            "dut_id": "D2",
            "terminal": "G",
            "program": {"kind": "dc_value", "value": 0.2, "unit": "V"},
        }
    )
    manifest = _measurement_manifest_for_intent(intent)
    for pin in manifest["dut_pin_map"]:
        pin["probe_pin"] = f"P{pin['pad']}"
        pin["instrument_channel"] = f"CH{pin['pad']}"
    manifest["observables"][0]["source"] = {"dut_id": "D1", "terminal": "B"}
    manifest["electrical_topology"]["inactive_terminal_policy"] = {
        "execution_mode": "simultaneous",
        "active_dut_ids": ["D1", "D2"],
        "inactive_terminal_states": [],
    }
    second_stimulus = copy.deepcopy(manifest["stimuli"][0])
    second_stimulus["stimulus_id"] = "s2"
    second_stimulus["target"] = {"dut_id": "D2", "terminal": "G"}
    second_stimulus["program"] = {"kind": "dc_value", "value": 0.2, "unit": "V"}
    manifest["stimuli"].append(second_stimulus)

    with pytest.raises(AnalysisError) as exc_info:
        validate_measurement_manifest(manifest, design_intent=intent)

    assert exc_info.value.code == "ACTIVE_SHARED_PAD_STIMULUS_CONFLICT"


def test_shared_pad_policy_requires_force_value_and_unit() -> None:
    intent = _shared_pad_intent()
    manifest = _shared_pad_measurement()
    terminals = [
        terminal["name"] for terminal in intent["terminal_contracts"][1]["terminals"]
    ]
    manifest["electrical_topology"]["inactive_terminal_policy"] = {
        "execution_mode": "serial",
        "active_dut_ids": ["D1"],
        "inactive_terminal_states": [
            {"dut_id": "D2", "terminal": terminal, "state": "force"}
            for terminal in terminals
        ],
    }

    with pytest.raises(AnalysisError) as exc_info:
        validate_measurement_manifest(manifest, design_intent=intent)

    assert exc_info.value.code == "INACTIVE_FORCE_VALUE_REQUIRED"


def test_nonfinite_and_nonstring_json_values_are_refused() -> None:
    with pytest.raises(AnalysisError) as nonfinite:
        canonical_sha256({"value": float("nan")})
    assert nonfinite.value.code == "NONFINITE_CANONICAL_JSON_NUMBER"

    with pytest.raises(AnalysisError) as key_error:
        canonical_sha256({1: "silently coerced otherwise"})
    assert key_error.value.code == "NON_STRING_CANONICAL_JSON_KEY"
