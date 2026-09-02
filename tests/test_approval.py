from __future__ import annotations

import copy
from datetime import datetime

import pytest

from klayout_mcp.approval import (
    approval_verifier_contract,
    verify_design_intent_approval,
)
from klayout_mcp.errors import AnalysisError
from klayout_mcp.workflow_manifest import canonical_sha256, validate_design_intent_draft


CAPABILITY_HASH = "a" * 64
SOURCE_HASH = "b" * 64
RECEIPT_HASH = "c" * 64
CHECKED_AT = "2026-08-31T12:00:00+09:00"


def _draft() -> dict:
    return {
        "schema_version": 1,
        "intent_id": "approval-test",
        "units": "um",
        "process": {
            "profile": "example",
            "version": "1",
            "capability_sha256": CAPABILITY_HASH,
        },
        "frame": {
            "width_um": 2000,
            "height_um": 54,
            "origin_um": [0, 0],
            "allowed_boundary_um": [0, 0, 2000, 54],
        },
        "pads": {
            "count": 4,
            "rows": 1,
            "outline_um": [40, 40],
            "numbering": "left_to_right",
            "reserved_roles": {},
            "pitch_um": 80,
        },
        "devices": [
            {
                "dut_id": "R1",
                "family": "resistor",
                "device_type": "metal",
                "measurement_type": "kelvin_4t",
                "parameters": {"width_um": 0.1, "length_um": 1.0},
                "doe": {},
                "placement_constraints": {},
            }
        ],
        "terminal_contracts": [
            {
                "dut_id": "R1",
                "terminals": [
                    {"name": name, "electrical_role": role}
                    for name, role in (
                        ("F+", "force"),
                        ("F-", "force"),
                        ("S+", "sense"),
                        ("S-", "sense"),
                    )
                ],
            }
        ],
        "terminal_net_pad_map": [
            {
                "dut_id": "R1",
                "terminal": terminal,
                "net": terminal,
                "pad": index,
                "shared_net_explicit": False,
            }
            for index, terminal in enumerate(("F+", "F-", "S+", "S-"), start=1)
        ],
        "measurement_requirements": {
            "stimuli": [{
                "dut_id": "R1",
                "terminal": "F+",
                "mode": "current",
                "source_mode": "current",
                "program": {"kind": "dc_value", "value": 0.001, "unit": "A"},
                "compliance": {"quantity": "voltage", "limit": 1.0, "unit": "V"},
                "polarity": "positive",
                "frequency_hz": None,
            }],
            "observables": [
                {
                    "dut_id": "R1",
                    "terminal": "S+",
                    "mode": "voltage",
                    "quantity": "voltage",
                    "unit": "V",
                }
            ],
            "biases": [],
            "timing": {"settling_s": 0.1, "integration": {}, "hold_s": 0, "delay_s": 0},
            "environment": {},
            "safety_envelope": {
                "limits": {"max_abs_current_a": 0.01, "max_abs_voltage_v": 1.0},
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
            "internal_checks": ["fresh_reload"],
            "external_evidence_required": [],
        },
        "output_policy": {
            "format": "gds",
            "top_cell": "TEG",
            "new_output_required": True,
        },
        "unresolved_questions": [],
    }


def _reference(draft: dict) -> dict:
    return {
        "schema_version": 1,
        "draft_sha256": validate_design_intent_draft(draft)["canonical_sha256"],
        "process_capability_sha256": CAPABILITY_HASH,
        "source_artifact_sha256s": {"layermap": SOURCE_HASH},
        "approval_scope": "planning_and_generation",
        "output_classes": ["nonproduction_gds"],
        "signer_reference": "trusted-store:user-1",
        "scheme_id": "test-attestation-v1",
        "attestation_reference": "trusted-store://approval/exact",
        "approved_at": "2026-08-31T10:00:00+09:00",
        "expires_at": "2026-09-01T10:00:00+09:00",
        "revocation_id": "approval-exact",
    }


class ExactTestVerifier:
    backend_id = "test-only-exact-verifier"
    trusted = True

    def verify(self, **kwargs):
        draft = kwargs["draft_document"]
        reference = kwargs["approval_reference"]
        if reference["attestation_reference"] != "trusted-store://approval/exact":
            return {"verified": False}
        return {
            "verified": True,
            "backend_id": self.backend_id,
            "draft_sha256": canonical_sha256(draft),
            "process_capability_sha256": draft["process"]["capability_sha256"],
            "approval_reference_sha256": canonical_sha256(reference),
            "approval_scope": kwargs["required_scope"],
            "output_class": kwargs["output_class"],
            "signature_or_attestation_verified": True,
            "revocation_checked": True,
            "not_revoked": True,
            "verified_at": kwargs["checked_at"],
            "verification_receipt_sha256": RECEIPT_HASH,
        }


def _verify(draft: dict, reference: dict, verifier=None) -> dict:
    return verify_design_intent_approval(
        design_intent_draft=draft,
        approval_reference=reference,
        required_scope="planning_and_generation",
        output_class="nonproduction_gds",
        verifier=verifier,
        clock=lambda: datetime.fromisoformat(CHECKED_AT),
    )


def test_default_contract_is_fail_closed_and_cannot_mint() -> None:
    contract = approval_verifier_contract()

    assert contract["backend_configured"] is False
    assert contract["default_behavior"] == "fail_closed"
    assert contract["mcp_can_mint_approval"] is False
    assert contract["model_arguments_can_select_or_construct_verifier"] is False


def test_missing_backend_fails_closed() -> None:
    draft = _draft()

    with pytest.raises(AnalysisError) as caught:
        _verify(draft, _reference(draft), verifier=None)

    assert caught.value.code == "APPROVAL_BACKEND_UNAVAILABLE"
    assert caught.value.details["production_ready"] is False


def test_caller_supplied_approval_boolean_is_not_a_valid_reference() -> None:
    draft = _draft()
    reference = _reference(draft)
    reference["approval_verified"] = True

    with pytest.raises(AnalysisError) as caught:
        _verify(draft, reference, verifier=None)

    assert caught.value.code == "WORKFLOW_SCHEMA_MISMATCH"


def test_exact_trusted_verifier_approves_only_bound_intent() -> None:
    draft = _draft()
    result = _verify(draft, _reference(draft), verifier=ExactTestVerifier())

    assert result["intent_approved"] is True
    assert result["approval_scope_allows_planning"] is True
    assert result["approval_scope_allows_generation"] is True
    assert result["authorizes_planning"] is False
    assert result["authorizes_generation"] is False
    assert result["evidence_ladder"]["highest_attained_state"] == "intent_approved"
    assert result["production_ready"] is False


def test_draft_mutation_after_approval_is_rejected() -> None:
    original = _draft()
    reference = _reference(original)
    changed = copy.deepcopy(original)
    changed["terminal_net_pad_map"][0]["pad"] = 4
    changed["terminal_net_pad_map"][3]["pad"] = 1

    with pytest.raises(AnalysisError) as caught:
        _verify(changed, reference, verifier=ExactTestVerifier())

    assert caught.value.code == "APPROVAL_REFERENCE_BINDING_MISMATCH"
    assert "draft_sha256" in caught.value.details["mismatches"]


def test_process_capability_substitution_is_rejected() -> None:
    draft = _draft()
    reference = _reference(draft)
    reference["process_capability_sha256"] = "d" * 64

    with pytest.raises(AnalysisError) as caught:
        _verify(draft, reference, verifier=ExactTestVerifier())

    assert caught.value.code == "APPROVAL_REFERENCE_BINDING_MISMATCH"
    assert "process_capability_sha256" in caught.value.details["mismatches"]


def test_output_scope_expansion_is_rejected() -> None:
    draft = _draft()
    reference = _reference(draft)
    reference["output_classes"] = ["plan_only"]

    with pytest.raises(AnalysisError) as caught:
        _verify(draft, reference, verifier=ExactTestVerifier())

    assert caught.value.code == "APPROVAL_REFERENCE_BINDING_MISMATCH"
    assert "output_class" in caught.value.details["mismatches"]


def test_fake_attestation_is_rejected_by_trusted_backend() -> None:
    draft = _draft()
    reference = _reference(draft)
    reference["attestation_reference"] = "model-writable://fake"

    with pytest.raises(AnalysisError) as caught:
        _verify(draft, reference, verifier=ExactTestVerifier())

    assert caught.value.code == "APPROVAL_VERIFICATION_FAILED"


def test_expired_approval_is_rejected_before_backend_call() -> None:
    draft = _draft()
    reference = _reference(draft)
    reference["expires_at"] = "2026-08-31T11:00:00+09:00"

    with pytest.raises(AnalysisError) as caught:
        _verify(draft, reference, verifier=ExactTestVerifier())

    assert caught.value.code == "APPROVAL_EXPIRED"


def test_naive_or_failed_host_clock_fails_closed() -> None:
    draft = _draft()
    reference = _reference(draft)

    with pytest.raises(AnalysisError) as naive:
        verify_design_intent_approval(
            design_intent_draft=draft,
            approval_reference=reference,
            required_scope="planning_and_generation",
            output_class="nonproduction_gds",
            verifier=ExactTestVerifier(),
            clock=lambda: datetime(2026, 8, 31, 12, 0, 0),
        )
    assert naive.value.code == "INVALID_APPROVAL_CLOCK"

    def broken_clock():
        raise RuntimeError("clock unavailable")

    with pytest.raises(AnalysisError) as failed:
        verify_design_intent_approval(
            design_intent_draft=draft,
            approval_reference=reference,
            required_scope="planning_and_generation",
            output_class="nonproduction_gds",
            verifier=ExactTestVerifier(),
            clock=broken_clock,
        )
    assert failed.value.code == "APPROVAL_CLOCK_FAILED"


def test_untrusted_backend_flag_cannot_approve() -> None:
    class UntrustedVerifier(ExactTestVerifier):
        trusted = False

    draft = _draft()
    with pytest.raises(AnalysisError) as caught:
        _verify(draft, _reference(draft), verifier=UntrustedVerifier())

    assert caught.value.code == "UNTRUSTED_APPROVAL_BACKEND"


def test_backend_receipt_must_match_exact_reference_hash() -> None:
    class StaleReceiptVerifier(ExactTestVerifier):
        def verify(self, **kwargs):
            receipt = dict(super().verify(**kwargs))
            receipt["approval_reference_sha256"] = "e" * 64
            return receipt

    draft = _draft()
    with pytest.raises(AnalysisError) as caught:
        _verify(draft, _reference(draft), verifier=StaleReceiptVerifier())

    assert caught.value.code == "APPROVAL_VERIFICATION_FAILED"
    assert "approval_reference_sha256" in caught.value.details["mismatches"]


@pytest.mark.parametrize(
    ("mutation", "expected_detail"),
    [
        ("missing_revocation", "revocation_checked"),
        ("false_signature", "signature_or_attestation_verified"),
    ],
)
def test_incomplete_or_false_receipt_assertion_fails_closed(
    mutation: str,
    expected_detail: str,
) -> None:
    class IncompleteReceiptVerifier(ExactTestVerifier):
        def verify(self, **kwargs):
            receipt = dict(super().verify(**kwargs))
            if mutation == "missing_revocation":
                receipt.pop("revocation_checked")
            else:
                receipt["signature_or_attestation_verified"] = False
            return receipt

    draft = _draft()
    with pytest.raises(AnalysisError) as caught:
        _verify(draft, _reference(draft), verifier=IncompleteReceiptVerifier())

    assert caught.value.code == "APPROVAL_VERIFICATION_FAILED"
    details = caught.value.details
    assert (
        expected_detail in details["missing_or_invalid_fields"]
        or expected_detail in details["mismatches"]
    )
