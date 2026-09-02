from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import threading

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.external_evidence import (
    ExternalEvidenceAdapterRegistry,
    JsonExternalEvidenceAdapter,
)
from klayout_mcp.klayout_adapter import find_klayout_executable
from klayout_mcp.kelvin_routing import DEFAULT_SITE_ORIGINS_UM, DEFAULT_SPLITS
from klayout_mcp.kelvin_workflow import (
    KelvinM1GenerationEngine,
    KelvinM1PlanningEngine,
    SLN001_KELVIN_PROCESS_CAPABILITY,
)
from klayout_mcp.process_capability import validate_process_capability
from klayout_mcp.technology_registry import TechnologyAdapterRegistry
from conftest import SYNTHETIC_PROCESS_CAPABILITY
from klayout_mcp.workflow_manifest import canonical_sha256, validate_design_intent_draft
from klayout_mcp.workflow_store import (
    TegWorkflowFacade,
    WorkflowEngineRegistry,
    WorkflowJobStore,
    load_live_process_capability,
    workflow_store_contract,
)


CHECKED_AT = "2026-08-31T12:00:00+09:00"
SOURCE_HASH = "b" * 64
RECEIPT_HASH = "c" * 64


class StaticProvider:
    provider_id = "host-static-process-store"
    trusted = True

    def __init__(self, capability=None):
        self.capability = copy.deepcopy(capability or SYNTHETIC_PROCESS_CAPABILITY)
        self.calls = 0

    def load(self, *, profile, version):
        self.calls += 1
        return copy.deepcopy(self.capability)


class ExactVerifier:
    backend_id = "host-exact-attestation"
    trusted = True

    def __init__(self):
        self.calls = 0

    def verify(self, **kwargs):
        self.calls += 1
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


class MockVerifier(ExactVerifier):
    backend_id = "mock-verifier"


class ExplicitlyNonproductionVerifier(ExactVerifier):
    backend_id = "local-review-authority"
    nonproduction_only = True


class DeterministicPlanner:
    engine_id = "deterministic-kelvin-planner"

    def __init__(self):
        self.calls = 0

    def plan(self, **kwargs):
        self.calls += 1
        plan = {
            "contract_version": 1,
            "profile": "kelvin_m1",
            "design_intent_sha256": canonical_sha256(kwargs["design_intent"]),
            "routes": [
                {"terminal": terminal, "pad": index, "layer_role": "m1"}
                for index, terminal in enumerate(("F+", "F-", "S+", "S-"), start=1)
            ],
        }
        return {
            "ok": True,
            "plan": plan,
            "plan_sha256": canonical_sha256(plan),
            "routing_plan_fingerprint_sha256": canonical_sha256(plan["routes"]),
        }


class DeterministicGenerator:
    engine_id = "deterministic-kelvin-generator"

    def __init__(self):
        self.calls = 0

    def generate(self, **kwargs):
        self.calls += 1
        payload = b"deterministic-gds-fixture"
        Path(kwargs["output_path"]).write_bytes(payload)
        return {
            "ok": True,
            "fresh_reload_verified": True,
            "drawing_fingerprint_verified": True,
            "drawing_fingerprint_sha256": canonical_sha256(
                {"bytes": payload.hex(), "plan": kwargs["plan"]}
            ),
            "connectivity_projection_verified": True,
        }


class ExactSignoffPolicy:
    policy_id = "host-exact-signoff-v1"
    policy_version = "1"
    trusted = True

    def __init__(self, required_evidence_kinds=("drc", "lvs", "pex")):
        self.calls = 0
        self.required_evidence_kinds = tuple(required_evidence_kinds)

    def approve(self, *, evidence_documents):
        self.calls += 1
        evidence_sha256s = sorted(
            canonical_sha256(item) for item in evidence_documents
        )
        receipt = {
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "evidence_sha256s": evidence_sha256s,
            "decision": "approved_layout_signoff_evidence",
        }
        return {
            "approved": True,
            "policy_id": self.policy_id,
            "policy_version": self.policy_version,
            "evidence_sha256s": evidence_sha256s,
            "external_evidence_is_mock": False,
            "violations_reviewed": True,
            "approval_reference": "trusted-store://signoff/exact",
            "receipt_document": receipt,
            "receipt_sha256": canonical_sha256(receipt),
        }


def _draft(*, unresolved=None):
    capability = validate_process_capability(SYNTHETIC_PROCESS_CAPABILITY)
    capability_hash = canonical_sha256(capability)
    return {
        "schema_version": 1,
        "intent_id": "persistent-kelvin",
        "units": "um",
        "process": {
            "profile": "synthetic_test_process",
            "version": "test-v1",
            "capability_sha256": capability_hash,
        },
        "frame": {
            "width_um": 2000,
            "height_um": 54,
            "origin_um": [0, 0],
            "allowed_boundary_um": [0, 0, 2000, 54],
        },
        "pads": {
            "count": 25,
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
                "device_type": "example_resistor",
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
                    {"name": "F+", "electrical_role": "force"},
                    {"name": "F-", "electrical_role": "force"},
                    {"name": "S+", "electrical_role": "sense"},
                    {"name": "S-", "electrical_role": "sense"},
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
                "source_mode": "voltage",
                "program": {"kind": "dc_value", "value": 0.1, "unit": "V"},
                "compliance": {"quantity": "current", "limit": 0.001, "unit": "A"},
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
        "unresolved_questions": list(unresolved or []),
    }


def _reference(draft):
    return {
        "schema_version": 1,
        "draft_sha256": validate_design_intent_draft(draft)["canonical_sha256"],
        "process_capability_sha256": draft["process"]["capability_sha256"],
        "source_artifact_sha256s": {"layermap": SOURCE_HASH},
        "approval_scope": "planning_and_generation",
        "output_classes": ["nonproduction_gds"],
        "signer_reference": "trusted-store:user-1",
        "scheme_id": "host-attestation-v1",
        "attestation_reference": "trusted-store://approval/exact",
        "approved_at": "2026-08-31T10:00:00+09:00",
        "expires_at": "2026-09-01T10:00:00+09:00",
        "revocation_id": "approval-exact",
    }


def _measurement(draft, layout_sha256):
    roles = {
        (contract["dut_id"], terminal["name"]): terminal["electrical_role"]
        for contract in draft["terminal_contracts"]
        for terminal in contract["terminals"]
    }
    records = draft["terminal_net_pad_map"]
    stimulus_requirement = draft["measurement_requirements"]["stimuli"][0]
    observable_requirement = draft["measurement_requirements"]["observables"][0]
    return {
        "schema_version": 1,
        "design_intent_sha256": validate_design_intent_draft(draft)["canonical_sha256"],
        "generated_layout_sha256": layout_sha256,
        "dut_pin_map": [
            {
                **{key: record[key] for key in ("dut_id", "terminal", "net", "pad")},
                "probe_pin": f"P{record['pad']}",
                "instrument_channel": f"CH{record['pad']}",
                "electrical_role": roles[(record["dut_id"], record["terminal"])],
            }
            for record in records
        ],
        "electrical_topology": {"type": "direct", "connections": [], "guards": []},
        "stimuli": [{
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
        }],
        "observables": [{
            "label": "response",
            "requirement_mode": observable_requirement["mode"],
            "quantity": observable_requirement["quantity"],
            "unit": observable_requirement["unit"],
            "source": {
                "dut_id": observable_requirement["dut_id"],
                "terminal": observable_requirement["terminal"],
            },
        }],
        "timing": copy.deepcopy(draft["measurement_requirements"]["timing"]),
        "environment": copy.deepcopy(draft["measurement_requirements"]["environment"]),
        "safety_envelope": copy.deepcopy(
            draft["measurement_requirements"]["safety_envelope"]
        ),
        "calibration_and_deembedding": {
            "required": False,
            "calibration_plane": "probe_tip",
            "reference_duts": [],
        },
    }


def _six_split_kelvin_draft():
    draft = _draft()
    normalized_capability = validate_process_capability(
        SLN001_KELVIN_PROCESS_CAPABILITY
    )
    draft["process"] = {
        "profile": "sln001_kelvin_reference_demo",
        "version": "golden-v15-2026-08-25",
        "capability_sha256": canonical_sha256(normalized_capability),
    }
    devices = []
    contracts = []
    mappings = []
    for index, ((width_nm, length_nm), origin) in enumerate(
        zip(DEFAULT_SPLITS, DEFAULT_SITE_ORIGINS_UM), start=1
    ):
        dut_id = f"K{index}"
        devices.append(
            {
                "dut_id": dut_id,
                "family": "resistor",
                "device_type": "metal1_resistor",
                "measurement_type": "kelvin_4t",
                "parameters": {
                    "width_um": width_nm / 1000.0,
                    "length_um": length_nm / 1000.0,
                },
                "doe": {"split_index": index},
                "placement_constraints": {"origin_um": list(origin)},
            }
        )
        contracts.append(
            {
                "dut_id": dut_id,
                "terminals": [
                    {"name": "S+", "electrical_role": "sense"},
                    {"name": "F+", "electrical_role": "force"},
                    {"name": "F-", "electrical_role": "force"},
                    {"name": "S-", "electrical_role": "sense"},
                ],
            }
        )
        for offset, terminal in enumerate(("S+", "F+", "F-", "S-"), start=1):
            mappings.append(
                {
                    "dut_id": dut_id,
                    "terminal": terminal,
                    "net": f"{dut_id}_{terminal}",
                    "pad": (index - 1) * 4 + offset,
                    "shared_net_explicit": False,
                }
            )
    draft["devices"] = devices
    draft["terminal_contracts"] = contracts
    draft["terminal_net_pad_map"] = mappings
    draft["measurement_requirements"] = {
        "stimuli": [{
            "dut_id": "K1",
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
                "dut_id": "K1",
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
    }
    return draft


def _facade(
    tmp_path,
    *,
    provider=None,
    verifier=None,
    planner=None,
    generator=None,
    external_registry=None,
    external_report_root=None,
    signoff_policy=None,
    production_mode=False,
):
    store = WorkflowJobStore(tmp_path / "jobs", output_root=tmp_path / "safe-output")
    return TegWorkflowFacade(
        store=store,
        process_provider=provider or StaticProvider(),
        approval_verifier=verifier,
        planning_engine=planner,
        generation_engine=generator,
        external_evidence_registry=external_registry,
        external_report_root=external_report_root,
        signoff_policy=signoff_policy,
        production_mode=production_mode,
        clock=lambda: datetime.fromisoformat(CHECKED_AT),
    )


def test_store_contract_does_not_trust_persisted_approval_boolean():
    contract = workflow_store_contract()

    assert contract["approval_boolean_grants_authority"] is False
    assert contract["approval_reverified_for_every_privileged_action"] is True
    assert contract["live_process_capability_rehashed_for_every_privileged_action"] is True
    assert contract["content_document_publish_concurrent_no_clobber"] is True
    assert contract["final_output_publish_concurrent_no_clobber"] is True
    assert contract["incomplete_drafts_persisted"] is True
    assert contract["incomplete_drafts_are_immutable_revisions"] is True


def test_concurrent_content_publish_is_idempotent_for_same_payload(tmp_path):
    store = WorkflowJobStore(tmp_path / "jobs", output_root=tmp_path / "outputs")
    target = store.documents_root / "race" / "same.json"
    payload = b'{"same":true}\n'
    barrier = threading.Barrier(8)

    def publish() -> None:
        barrier.wait()
        store._atomic_write(target, payload, replace=False)

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: publish(), range(8)))

    assert target.read_bytes() == payload
    assert list(target.parent.glob(".klayout-stage-file-workflow-document-*")) == []


def test_concurrent_content_publish_rejects_different_payload_without_clobber(
    tmp_path,
):
    store = WorkflowJobStore(tmp_path / "jobs", output_root=tmp_path / "outputs")
    target = store.documents_root / "race" / "collision.json"
    payloads = (b'{"writer":1}\n', b'{"writer":2}\n')
    barrier = threading.Barrier(2)

    def publish(payload: bytes) -> str:
        barrier.wait()
        try:
            store._atomic_write(target, payload, replace=False)
        except AnalysisError as exc:
            assert exc.code == "WORKFLOW_CONTENT_ADDRESS_COLLISION"
            return "collision"
        return "published"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(publish, payloads))

    assert outcomes.count("published") == 1
    assert outcomes.count("collision") == 1
    assert target.read_bytes() in payloads
    assert list(target.parent.glob(".klayout-stage-file-workflow-document-*")) == []


def test_concurrent_final_promotion_preserves_one_different_content_winner(
    tmp_path,
):
    output_root = tmp_path / "outputs"
    store = WorkflowJobStore(tmp_path / "jobs", output_root=output_root)
    payloads = (b"writer-one", b"writer-two")
    staged_paths = []
    for index, payload in enumerate(payloads):
        staged = output_root / f"stage-{index}.gds"
        staged.write_bytes(payload)
        staged_paths.append(staged)
    final = output_root / "final.gds"
    barrier = threading.Barrier(2)

    def promote(index: int) -> str:
        barrier.wait()
        expected = hashlib.sha256(payloads[index]).hexdigest()
        try:
            store.promote_staged_output(
                staged_path=staged_paths[index],
                final_target=final,
                expected_sha256=expected,
            )
        except AnalysisError as exc:
            assert exc.code == "WORKFLOW_FINAL_PROMOTION_CONFLICT"
            return "conflict"
        return "published"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(promote, range(2)))

    assert outcomes.count("published") == 1
    assert outcomes.count("conflict") == 1
    assert final.read_bytes() in payloads
    assert list(output_root.glob(".klayout-stage-file-workflow-promote-*")) == []


def test_concurrent_final_promotion_is_idempotent_for_same_content(tmp_path):
    output_root = tmp_path / "outputs"
    store = WorkflowJobStore(tmp_path / "jobs", output_root=output_root)
    payload = b"same-content"
    expected = hashlib.sha256(payload).hexdigest()
    staged_paths = []
    for index in range(2):
        staged = output_root / f"same-stage-{index}.gds"
        staged.write_bytes(payload)
        staged_paths.append(staged)
    final = output_root / "same-final.gds"
    barrier = threading.Barrier(2)

    def promote(staged: Path) -> Path:
        barrier.wait()
        return store.promote_staged_output(
            staged_path=staged,
            final_target=final,
            expected_sha256=expected,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(promote, staged_paths))

    assert results == [final, final]
    assert final.read_bytes() == payload
    assert list(output_root.glob(".klayout-stage-file-workflow-promote-*")) == []


@pytest.mark.parametrize("family", ["transistor", "resistor", "capacitor"])
def test_intake_without_draft_returns_schema_valid_unapproved_template(
    tmp_path, family
):
    facade = _facade(tmp_path)

    result = facade.teg_intake(
        template_process_profile="synthetic_test_process",
        template_process_version="test-v1",
        template_family=family,
    )

    validated = validate_design_intent_draft(result["template"])
    assert result["workflow_status"] == "template_returned_input_required"
    assert result["template_schema_valid"] is True
    assert validated["draft_complete"] is False
    assert result["template_persisted"] is False
    assert result["authorizes_planning"] is False
    assert not any((tmp_path / "jobs" / "documents").rglob("*.json"))


def test_intake_persists_content_addressed_root_and_survives_restart(tmp_path):
    draft = _draft()
    first = _facade(tmp_path)
    result = first.teg_intake(design_intent_draft=draft, job_id="restart-job")

    restarted_store = WorkflowJobStore(
        tmp_path / "jobs", output_root=tmp_path / "safe-output"
    )
    restarted = restarted_store.head("restart-job")

    assert result["workflow_status"] == "intent_draft_complete"
    assert restarted["manifest_sha256"] == result["manifest_sha256"]
    assert restarted["manifest"]["design_intent_sha256"] == result[
        "design_intent_sha256"
    ]


def test_status_revalidates_persisted_head_without_granting_authority(tmp_path):
    facade = _facade(tmp_path)
    intake = facade.teg_intake(
        design_intent_draft=_draft(),
        job_id="status-job",
    )

    result = facade.teg_status(job_id="status-job")

    assert result["manifest_sha256"] == intake["manifest_sha256"]
    assert result["stage"] == "intent_draft_complete"
    assert result["highest_attained_state"] == "intent_draft_complete"
    assert result["next_required_state"] == "intent_approved"
    assert result["manifest_ancestry_revalidated"] is True
    assert result["output_files_rehashed"] is True
    assert result["output_file_integrity_verified"] is True
    assert result["checked_output_files"] == []
    assert result["approval_reverified"] is False
    assert result["authorizes_planning"] is False
    assert result["production_ready"] is False


def test_status_rehashes_generated_output_and_rejects_tampering(tmp_path):
    draft = _draft()
    facade = _facade(
        tmp_path,
        verifier=ExactVerifier(),
        planner=DeterministicPlanner(),
        generator=DeterministicGenerator(),
    )
    facade.teg_intake(design_intent_draft=draft, job_id="status-output")
    facade.teg_plan(job_id="status-output", approval_reference=_reference(draft))
    generated = facade.teg_generate(
        job_id="status-output",
        approval_reference=_reference(draft),
        output_name="final.gds",
    )

    status = facade.teg_status(job_id="status-output")
    assert status["output_file_integrity_verified"] is True
    assert status["workflow_documents_verified"] is True
    assert status["checked_workflow_documents"][0]["document_kind"] == (
        "generation_result"
    )
    assert status["checked_output_files"] == [
        {
            "role": "generated_layout",
            "path": generated["output_path"],
            "sha256": generated["generated_layout_sha256"],
        }
    ]

    Path(generated["output_path"]).write_bytes(b"tampered-after-status")
    with pytest.raises(AnalysisError) as caught:
        facade.teg_status(job_id="status-output")
    assert caught.value.code == "WORKFLOW_STATUS_OUTPUT_INTEGRITY_FAILURE"


def test_status_rejects_missing_generated_output(tmp_path):
    draft = _draft()
    facade = _facade(
        tmp_path,
        verifier=ExactVerifier(),
        planner=DeterministicPlanner(),
        generator=DeterministicGenerator(),
    )
    facade.teg_intake(design_intent_draft=draft, job_id="status-missing")
    facade.teg_plan(job_id="status-missing", approval_reference=_reference(draft))
    generated = facade.teg_generate(
        job_id="status-missing",
        approval_reference=_reference(draft),
        output_name="final.gds",
    )
    Path(generated["output_path"]).unlink()

    with pytest.raises(AnalysisError) as caught:
        facade.teg_status(job_id="status-missing")
    assert caught.value.code == "WORKFLOW_STATUS_OUTPUT_MISSING"


def test_status_rejects_missing_referenced_workflow_document(tmp_path):
    draft = _draft()
    facade = _facade(
        tmp_path,
        verifier=ExactVerifier(),
        planner=DeterministicPlanner(),
        generator=DeterministicGenerator(),
    )
    facade.teg_intake(design_intent_draft=draft, job_id="status-document-missing")
    facade.teg_plan(
        job_id="status-document-missing", approval_reference=_reference(draft)
    )
    generated = facade.teg_generate(
        job_id="status-document-missing",
        approval_reference=_reference(draft),
        output_name="final.gds",
    )
    document_path = (
        facade.store.documents_root
        / "generation_result"
        / f"{generated['generation_result_sha256']}.json"
    )
    document_path.unlink()

    with pytest.raises(AnalysisError) as caught:
        facade.teg_status(job_id="status-document-missing")

    assert caught.value.code == "WORKFLOW_DOCUMENT_NOT_FOUND"


def test_unresolved_questions_do_not_create_authorizable_job(tmp_path):
    result = _facade(tmp_path).teg_intake(
        design_intent_draft=_draft(unresolved=["confirm terminal orientation"]),
        job_id="blocked-job",
    )

    assert result["workflow_status"] == "input_required"
    assert result["job_created"] is False
    assert result["draft_persisted"] is True
    assert result["draft_revision"] == 1
    assert result["resume_token"]
    assert result["clarification_request"]["questions"][0]["question_id"].endswith("-q001")
    assert result["clarification_request"]["mutation_state"]["geometry_generation_started"] is False
    assert result["authorizes_planning"] is False
    assert not (tmp_path / "jobs" / "jobs" / "blocked-job" / "head.json").exists()
    assert not (tmp_path / "jobs" / "documents" / "design_intent").exists()


def test_pinned_technology_registry_snapshot_is_rechecked_before_plan(tmp_path):
    registry = TechnologyAdapterRegistry(tmp_path / "technology-registry")
    package = {
        "schema_version": 1,
        "identity": {
            "technology": "synthetic-tech",
            "pdk_revision": "test-v1",
            "adapter_kind": "transistor",
            "device_family": "planar",
            "topology": "example-nmos",
            "package_version": "1.0.0",
        },
        "status": "candidate_scored_not_foundry_qualified",
    }
    registered = registry.register_package(package)
    snapshot = registry.snapshot()
    draft = _draft()
    draft["technology_adapter"] = {
        "identity": package["identity"],
        "package_sha256": registered["package_sha256"],
        "registry_snapshot_sha256": snapshot["snapshot_sha256"],
    }
    facade = _facade(
        tmp_path,
        verifier=ExactVerifier(),
        planner=DeterministicPlanner(),
    )
    facade.technology_registry = registry
    intake = facade.teg_intake(design_intent_draft=draft, job_id="adapter-pin")
    assert intake["workflow_status"] == "intent_draft_complete"

    registry.append_lifecycle_record(
        package_sha256=registered["package_sha256"],
        action="revoked",
        reason="test revocation",
        recorded_at="2026-09-02T00:00:00Z",
        signer_reference="host-trust://test",
        signature_sha256="d" * 64,
    )

    with pytest.raises(AnalysisError) as caught:
        facade.teg_plan(job_id="adapter-pin", approval_reference=_reference(draft))

    assert caught.value.code == "TECH_ADAPTER_REGISTRY_SNAPSHOT_DRIFT"


def test_incomplete_draft_resumes_as_new_immutable_revision(tmp_path):
    facade = _facade(tmp_path)
    first = facade.teg_intake(
        design_intent_draft=_draft(unresolved=["confirm terminal orientation"]),
        draft_id="device-draft",
    )
    corrected = _draft()

    second = facade.teg_intake(
        design_intent_draft=corrected,
        job_id="resumed-job",
        draft_id="device-draft",
        expected_draft_revision=first["draft_revision"],
        resume_token=first["resume_token"],
    )

    assert second["workflow_status"] == "intent_draft_complete"
    assert second["draft_revision"] == 2
    original = facade.store.get_draft_revision(draft_id="device-draft", revision=1)
    latest = facade.store.get_draft_revision(draft_id="device-draft", revision=2)
    assert original["document"]["unresolved_questions"]
    assert latest["document"]["unresolved_questions"] == []


def test_validate_only_intake_does_not_persist_draft_or_job(tmp_path):
    facade = _facade(tmp_path)

    result = facade.teg_intake(
        design_intent_draft=_draft(unresolved=["confirm terminal orientation"]),
        draft_id="preflight-draft",
        validate_only=True,
    )

    assert result["workflow_status"] == "input_required"
    assert result["draft_persisted"] is False
    assert result["clarification_request"]["mutation_state"]["stage_appended"] is False
    assert not (tmp_path / "jobs" / "drafts" / "preflight-draft").exists()


def test_live_process_mutation_is_rejected(tmp_path):
    draft = _draft()
    changed = copy.deepcopy(SYNTHETIC_PROCESS_CAPABILITY)
    changed["routing_metals"][0]["min_space_um"] = 0.11

    with pytest.raises(AnalysisError) as caught:
        load_live_process_capability(
            design_intent_draft=draft,
            provider=StaticProvider(changed),
        )

    assert caught.value.code == "LIVE_PROCESS_CAPABILITY_MISMATCH"


def test_privileged_action_reverifies_after_restart_every_time(tmp_path):
    draft = _draft()
    verifier = ExactVerifier()
    first = _facade(tmp_path, verifier=verifier)
    first.teg_intake(design_intent_draft=draft, job_id="reauth-job")
    first.reverify_privileged_action(
        job_id="reauth-job",
        approval_reference=_reference(draft),
        required_scope="planning_and_generation",
        output_class="nonproduction_gds",
    )

    restarted = _facade(tmp_path, verifier=verifier)
    second = restarted.reverify_privileged_action(
        job_id="reauth-job",
        approval_reference=_reference(draft),
        required_scope="planning_and_generation",
        output_class="nonproduction_gds",
    )

    assert verifier.calls == 2
    assert second["authorization_decision_is_persisted"] is False


def test_production_mode_rejects_mock_verifier(tmp_path):
    draft = _draft()
    facade = _facade(
        tmp_path,
        verifier=MockVerifier(),
        production_mode=True,
    )
    facade.teg_intake(design_intent_draft=draft, job_id="production-job")

    with pytest.raises(AnalysisError) as caught:
        facade.reverify_privileged_action(
            job_id="production-job",
            approval_reference=_reference(draft),
            required_scope="planning_and_generation",
            output_class="nonproduction_gds",
        )

    assert caught.value.code == "NONPRODUCTION_APPROVAL_VERIFIER_FORBIDDEN"


def test_production_mode_rejects_explicit_nonproduction_verifier(tmp_path):
    draft = _draft()
    facade = _facade(
        tmp_path,
        verifier=ExplicitlyNonproductionVerifier(),
        production_mode=True,
    )
    facade.teg_intake(design_intent_draft=draft, job_id="production-explicit-job")

    with pytest.raises(AnalysisError) as caught:
        facade.reverify_privileged_action(
            job_id="production-explicit-job",
            approval_reference=_reference(draft),
            required_scope="planning_and_generation",
            output_class="nonproduction_gds",
        )

    assert caught.value.code == "NONPRODUCTION_APPROVAL_VERIFIER_FORBIDDEN"
    assert caught.value.details["explicitly_nonproduction"] is True


@pytest.mark.parametrize(
    "output_name",
    ["../escape.gds", "nested/escape.gds", "C:/escape.gds", "wrong.oas"],
)
def test_output_path_rejects_traversal_absolute_nested_and_wrong_format(
    tmp_path, output_name
):
    store = WorkflowJobStore(tmp_path / "jobs", output_root=tmp_path / "safe-output")

    with pytest.raises(AnalysisError) as caught:
        store.prepare_output_path(
            job_id="safe-job",
            output_name=output_name,
            output_format="gds",
        )

    assert caught.value.code == "INVALID_WORKFLOW_OUTPUT_NAME"


def test_output_path_is_new_and_inside_host_root(tmp_path):
    store = WorkflowJobStore(tmp_path / "jobs", output_root=tmp_path / "safe-output")
    target = store.prepare_output_path(
        job_id="safe-job", output_name="final.gds", output_format="gds"
    )

    assert target == (tmp_path / "safe-output" / "safe-job" / "final.gds").resolve()
    target.write_bytes(b"existing")
    with pytest.raises(AnalysisError) as caught:
        store.prepare_output_path(
            job_id="safe-job", output_name="final.gds", output_format="gds"
        )
    assert caught.value.code == "WORKFLOW_OUTPUT_ALREADY_EXISTS"


@pytest.mark.parametrize("job_id", ["CON", "con", "Alpha", "alpha.beta", "alpha "])
def test_job_id_rejects_windows_aliases_and_noncanonical_spelling(tmp_path, job_id):
    store = WorkflowJobStore(tmp_path / "jobs", output_root=tmp_path / "safe-output")

    with pytest.raises(AnalysisError) as caught:
        store.prepare_output_path(
            job_id=job_id, output_name="final.gds", output_format="gds"
        )

    assert caught.value.code == "INVALID_WORKFLOW_JOB_ID"


def test_concurrent_job_creation_serializes_head_compare_and_swap(tmp_path):
    draft = _draft()
    first = _facade(tmp_path)
    second = _facade(tmp_path)

    def create(facade):
        try:
            facade.teg_intake(design_intent_draft=draft, job_id="concurrent-job")
            return "created"
        except AnalysisError as exc:
            return (exc.code, exc.details)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, (first, second)))

    assert results.count("created") == 1
    failure = next(item for item in results if item != "created")
    assert failure[0] == "WORKFLOW_JOB_HEAD_CONFLICT", failure
    assert first.store.head("concurrent-job")["manifest"]["stage"] == (
        "intent_draft_complete"
    )


def test_tampered_content_addressed_document_is_rejected(tmp_path):
    draft = _draft()
    facade = _facade(tmp_path)
    result = facade.teg_intake(design_intent_draft=draft, job_id="tamper-job")
    path = (
        tmp_path
        / "jobs"
        / "documents"
        / "design_intent"
        / f"{result['design_intent_sha256']}.json"
    )
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(AnalysisError) as caught:
        facade.store.get_document("design_intent", result["design_intent_sha256"])

    assert caught.value.code == "WORKFLOW_DOCUMENT_INTEGRITY_FAILURE"


def test_four_call_facade_plans_generates_and_freshly_verifies(tmp_path):
    draft = _draft()
    verifier = ExactVerifier()
    planner = DeterministicPlanner()
    generator = DeterministicGenerator()
    facade = _facade(
        tmp_path,
        verifier=verifier,
        planner=planner,
        generator=generator,
    )

    intake = facade.teg_intake(design_intent_draft=draft, job_id="four-call-job")
    planned = facade.teg_plan(
        job_id="four-call-job", approval_reference=_reference(draft)
    )
    generated = facade.teg_generate(
        job_id="four-call-job",
        approval_reference=_reference(draft),
        output_name="final.gds",
    )
    verified = facade.teg_verify(
        job_id="four-call-job", approval_reference=_reference(draft)
    )

    assert intake["workflow_status"] == "intent_draft_complete"
    assert planned["workflow_status"] == "plan_complete"
    assert generated["workflow_status"] == "connectivity_projected"
    assert verified["final_output_integrity_verified"] is True
    assert verified["generated_layout_sha256"] == generated["generated_layout_sha256"]
    assert verified["measurement_manifest_verified"] is False
    assert verifier.calls == 3
    assert planner.calls == 1
    assert generator.calls == 1


def test_plan_resume_reverifies_approval_without_rerunning_engine(tmp_path):
    draft = _draft()
    verifier = ExactVerifier()
    planner = DeterministicPlanner()
    first = _facade(tmp_path, verifier=verifier, planner=planner)
    first.teg_intake(design_intent_draft=draft, job_id="plan-resume")
    first_result = first.teg_plan(
        job_id="plan-resume", approval_reference=_reference(draft)
    )

    restarted = _facade(tmp_path, verifier=verifier, planner=planner)
    second_result = restarted.teg_plan(
        job_id="plan-resume", approval_reference=_reference(draft)
    )

    assert first_result["plan_sha256"] == second_result["plan_sha256"]
    assert second_result["resumed"] is True
    assert verifier.calls == 2
    assert planner.calls == 1


def test_generation_resumes_from_persisted_drawing_without_rerunning_engine(
    tmp_path, monkeypatch
):
    draft = _draft()
    verifier = ExactVerifier()
    generator = DeterministicGenerator()
    facade = _facade(
        tmp_path,
        verifier=verifier,
        planner=DeterministicPlanner(),
        generator=generator,
    )
    facade.teg_intake(design_intent_draft=draft, job_id="drawing-resume")
    facade.teg_plan(job_id="drawing-resume", approval_reference=_reference(draft))

    append_stage = facade._append_stage

    def interrupt_after_drawing(**kwargs):
        if kwargs["stage"] == "connectivity_projected":
            raise RuntimeError("simulated host interruption")
        return append_stage(**kwargs)

    monkeypatch.setattr(facade, "_append_stage", interrupt_after_drawing)
    with pytest.raises(RuntimeError, match="simulated host interruption"):
        facade.teg_generate(
            job_id="drawing-resume",
            approval_reference=_reference(draft),
            output_name="final.gds",
        )
    assert facade.store.head("drawing-resume")["manifest"]["stage"] == "drawing_complete"

    monkeypatch.setattr(facade, "_append_stage", append_stage)
    resumed = facade.teg_generate(
        job_id="drawing-resume",
        approval_reference=_reference(draft),
        output_name="final.gds",
    )

    assert resumed["workflow_status"] == "connectivity_projected"
    assert resumed["resumed"] is True
    assert generator.calls == 1
    assert facade.store.head("drawing-resume")["manifest"]["stage"] == (
        "connectivity_projected"
    )


def test_generation_resumes_from_durable_stage_without_rerunning_engine(
    tmp_path, monkeypatch
):
    draft = _draft()
    generator = DeterministicGenerator()
    facade = _facade(
        tmp_path,
        verifier=ExactVerifier(),
        planner=DeterministicPlanner(),
        generator=generator,
    )
    facade.teg_intake(design_intent_draft=draft, job_id="staged-resume")
    facade.teg_plan(job_id="staged-resume", approval_reference=_reference(draft))
    append_stage = facade._append_stage

    def interrupt_after_staging(**kwargs):
        result = append_stage(**kwargs)
        if kwargs["stage"] == "generation_staged":
            raise RuntimeError("simulated interruption after durable staging")
        return result

    monkeypatch.setattr(facade, "_append_stage", interrupt_after_staging)
    with pytest.raises(RuntimeError, match="durable staging"):
        facade.teg_generate(
            job_id="staged-resume",
            approval_reference=_reference(draft),
            output_name="final.gds",
        )
    staged_head = facade.store.head("staged-resume")
    assert staged_head["manifest"]["stage"] == "generation_staged"
    staged_path = Path(
        next(
            item
            for item in staged_head["manifest"]["outputs"]
            if item["role"] == "staged_layout"
        )["reference"]
    )
    assert staged_path.is_file()

    monkeypatch.setattr(facade, "_append_stage", append_stage)
    resumed = facade.teg_generate(
        job_id="staged-resume",
        approval_reference=_reference(draft),
        output_name="final.gds",
    )

    assert resumed["workflow_status"] == "connectivity_projected"
    assert resumed["resumed"] is True
    assert generator.calls == 1
    assert Path(resumed["output_path"]).is_file()
    assert not staged_path.exists()


def test_generation_recovers_after_final_write_before_drawing_manifest(
    tmp_path, monkeypatch
):
    draft = _draft()
    generator = DeterministicGenerator()
    facade = _facade(
        tmp_path,
        verifier=ExactVerifier(),
        planner=DeterministicPlanner(),
        generator=generator,
    )
    facade.teg_intake(design_intent_draft=draft, job_id="promotion-resume")
    facade.teg_plan(job_id="promotion-resume", approval_reference=_reference(draft))
    append_stage = facade._append_stage

    def interrupt_before_drawing_manifest(**kwargs):
        if kwargs["stage"] == "drawing_complete":
            raise RuntimeError("simulated interruption after final promotion")
        return append_stage(**kwargs)

    monkeypatch.setattr(facade, "_append_stage", interrupt_before_drawing_manifest)
    with pytest.raises(RuntimeError, match="final promotion"):
        facade.teg_generate(
            job_id="promotion-resume",
            approval_reference=_reference(draft),
            output_name="final.gds",
        )
    head = facade.store.head("promotion-resume")
    assert head["manifest"]["stage"] == "generation_staged"
    final_path = Path(head["manifest"]["runtime"]["final_output_path"])
    assert final_path.is_file()

    monkeypatch.setattr(facade, "_append_stage", append_stage)
    resumed = facade.teg_generate(
        job_id="promotion-resume",
        approval_reference=_reference(draft),
        output_name="final.gds",
    )

    assert resumed["resumed"] is True
    assert generator.calls == 1
    assert Path(resumed["output_path"]) == final_path


def test_generation_resume_rejects_output_mutation(tmp_path, monkeypatch):
    draft = _draft()
    facade = _facade(
        tmp_path,
        verifier=ExactVerifier(),
        planner=DeterministicPlanner(),
        generator=DeterministicGenerator(),
    )
    facade.teg_intake(design_intent_draft=draft, job_id="drawing-resume-tamper")
    facade.teg_plan(
        job_id="drawing-resume-tamper", approval_reference=_reference(draft)
    )
    append_stage = facade._append_stage

    def interrupt_after_drawing(**kwargs):
        if kwargs["stage"] == "connectivity_projected":
            raise RuntimeError("simulated host interruption")
        return append_stage(**kwargs)

    monkeypatch.setattr(facade, "_append_stage", interrupt_after_drawing)
    with pytest.raises(RuntimeError):
        facade.teg_generate(
            job_id="drawing-resume-tamper",
            approval_reference=_reference(draft),
            output_name="final.gds",
        )
    output = next(
        item
        for item in facade.store.head("drawing-resume-tamper")["manifest"]["outputs"]
        if item["role"] == "generated_layout"
    )
    Path(output["reference"]).write_bytes(b"mutated-before-resume")
    monkeypatch.setattr(facade, "_append_stage", append_stage)

    with pytest.raises(AnalysisError) as caught:
        facade.teg_generate(
            job_id="drawing-resume-tamper",
            approval_reference=_reference(draft),
            output_name="final.gds",
        )

    assert caught.value.code == "WORKFLOW_DRAWING_RESUME_OUTPUT_INTEGRITY_FAILURE"


def test_final_output_mutation_is_rejected_on_verify(tmp_path):
    draft = _draft()
    facade = _facade(
        tmp_path,
        verifier=ExactVerifier(),
        planner=DeterministicPlanner(),
        generator=DeterministicGenerator(),
    )
    facade.teg_intake(design_intent_draft=draft, job_id="output-tamper")
    facade.teg_plan(job_id="output-tamper", approval_reference=_reference(draft))
    generated = facade.teg_generate(
        job_id="output-tamper",
        approval_reference=_reference(draft),
        output_name="final.gds",
    )
    Path(generated["output_path"]).write_bytes(b"tampered")

    with pytest.raises(AnalysisError) as caught:
        facade.teg_verify(
            job_id="output-tamper", approval_reference=_reference(draft)
        )

    assert caught.value.code == "WORKFLOW_FINAL_OUTPUT_INTEGRITY_FAILURE"


def test_generation_requires_generation_scope(tmp_path):
    draft = _draft()
    planning_reference = _reference(draft)
    planning_reference["approval_scope"] = "planning"
    facade = _facade(
        tmp_path,
        verifier=ExactVerifier(),
        planner=DeterministicPlanner(),
        generator=DeterministicGenerator(),
    )
    facade.teg_intake(design_intent_draft=draft, job_id="scope-job")
    facade.teg_plan(job_id="scope-job", approval_reference=planning_reference)

    with pytest.raises(AnalysisError) as caught:
        facade.teg_generate(
            job_id="scope-job",
            approval_reference=planning_reference,
            output_name="final.gds",
        )

    assert caught.value.code == "APPROVAL_SCOPE_DOES_NOT_ALLOW_GENERATION"


def test_measurement_manifest_promotes_exact_fresh_layout_and_resumes(tmp_path):
    draft = _draft()
    facade = _facade(
        tmp_path,
        verifier=ExactVerifier(),
        planner=DeterministicPlanner(),
        generator=DeterministicGenerator(),
    )
    facade.teg_intake(design_intent_draft=draft, job_id="measurement-job")
    facade.teg_plan(job_id="measurement-job", approval_reference=_reference(draft))
    generated = facade.teg_generate(
        job_id="measurement-job",
        approval_reference=_reference(draft),
        output_name="final.gds",
    )
    measurement = _measurement(draft, generated["generated_layout_sha256"])

    first = facade.teg_verify(
        job_id="measurement-job",
        approval_reference=_reference(draft),
        measurement_manifest=measurement,
    )
    restarted = _facade(tmp_path, verifier=ExactVerifier())
    second = restarted.teg_verify(
        job_id="measurement-job",
        approval_reference=_reference(draft),
        measurement_manifest=measurement,
    )

    assert first["workflow_status"] == "measurement_package_complete"
    assert first["measurement_layout_hash_match"] is True
    assert first["resumed"] is False
    assert second["measurement_manifest_sha256"] == first["measurement_manifest_sha256"]
    assert second["resumed"] is True
    assert restarted.store.head("measurement-job")["manifest"]["stage"] == (
        "measurement_package_complete"
    )


def test_measurement_manifest_with_stale_layout_hash_is_rejected(tmp_path):
    draft = _draft()
    facade = _facade(
        tmp_path,
        verifier=ExactVerifier(),
        planner=DeterministicPlanner(),
        generator=DeterministicGenerator(),
    )
    facade.teg_intake(design_intent_draft=draft, job_id="stale-measurement")
    facade.teg_plan(job_id="stale-measurement", approval_reference=_reference(draft))
    facade.teg_generate(
        job_id="stale-measurement",
        approval_reference=_reference(draft),
        output_name="final.gds",
    )

    with pytest.raises(AnalysisError) as caught:
        facade.teg_verify(
            job_id="stale-measurement",
            approval_reference=_reference(draft),
            measurement_manifest=_measurement(draft, "d" * 64),
        )

    assert caught.value.code == "MEASUREMENT_LAYOUT_HASH_MISMATCH"
    assert facade.store.head("stale-measurement")["manifest"]["stage"] == (
        "connectivity_projected"
    )


def test_trusted_external_report_advances_only_after_measurement_package(tmp_path):
    draft = _draft()
    approved_profile = copy.deepcopy(SYNTHETIC_PROCESS_CAPABILITY)
    approved_profile["process"]["evidence_status"] = "approved"
    approved_profile["verification"] = {
        "drc": "approved",
        "lvs": "approved",
        "pex": "approved",
    }
    draft["process"]["capability_sha256"] = canonical_sha256(
        validate_process_capability(approved_profile)
    )
    draft["verification_policy"]["external_evidence_required"] = ["drc"]
    report_root = tmp_path / "reports"
    report_root.mkdir()
    registry = ExternalEvidenceAdapterRegistry(production_mode=False)
    registry.register(
        JsonExternalEvidenceAdapter(
            adapter_id="fixture-json-adapter",
            adapter_version="1",
            trusted=True,
        )
    )
    facade = _facade(
        tmp_path,
        provider=StaticProvider(approved_profile),
        verifier=ExactVerifier(),
        planner=DeterministicPlanner(),
        generator=DeterministicGenerator(),
        external_registry=registry,
        external_report_root=report_root,
    )
    facade.teg_intake(design_intent_draft=draft, job_id="external-job")
    facade.teg_plan(job_id="external-job", approval_reference=_reference(draft))
    generated = facade.teg_generate(
        job_id="external-job",
        approval_reference=_reference(draft),
        output_name="final.gds",
    )
    facade.teg_verify(
        job_id="external-job",
        approval_reference=_reference(draft),
        measurement_manifest=_measurement(draft, generated["generated_layout_sha256"]),
    )
    report = {
        "schema_version": 1,
        "kind": "drc",
        "status": "passed",
        "engine": {"name": "fixture-drc", "version": "1"},
        "deck_sha256": "a" * 64,
        "input_layout_sha256": generated["generated_layout_sha256"],
        "violation_count": 0,
        "mismatch_count": 0,
        "generated_at": "2026-08-31T00:00:00Z",
        "invocation_sha256": "b" * 64,
    }
    (report_root / "drc.json").write_text(json.dumps(report), encoding="utf-8")

    result = facade.teg_verify(
        job_id="external-job",
        approval_reference=_reference(draft),
        external_reports=[
            {
                "adapter_id": "fixture-json-adapter",
                "report_name": "drc.json",
                "kind": "drc",
            }
        ],
    )

    assert result["workflow_status"] == "external_evidence_attached"
    assert result["external_evidence_provenance_verified"] is True
    assert result["external_evidence_is_mock"] is False
    assert result["production_ready"] is False
    assert facade.store.head("external-job")["manifest"]["stage"] == (
        "external_evidence_attached"
    )

    policy = ExactSignoffPolicy(("drc",))
    facade.signoff_policy = policy
    signed = facade.teg_verify(
        job_id="external-job",
        approval_reference=_reference(draft),
        external_reports=[
            {
                "adapter_id": "fixture-json-adapter",
                "report_name": "drc.json",
                "kind": "drc",
            }
        ],
    )

    assert signed["layout_signoff_evidence_approved"] is True
    assert signed["production_ready"] is False
    assert policy.calls == 1


def test_current_layout_drc_lvs_pex_can_approve_layout_signoff_not_production(tmp_path):
    draft = _draft()
    approved_profile = copy.deepcopy(SYNTHETIC_PROCESS_CAPABILITY)
    approved_profile["process"]["evidence_status"] = "approved"
    approved_profile["verification"] = {
        "drc": "approved",
        "lvs": "approved",
        "pex": "approved",
    }
    draft["process"]["capability_sha256"] = canonical_sha256(
        validate_process_capability(approved_profile)
    )
    draft["verification_policy"]["external_evidence_required"] = [
        "drc",
        "lvs",
        "pex",
    ]
    report_root = tmp_path / "reports"
    report_root.mkdir()
    registry = ExternalEvidenceAdapterRegistry(production_mode=False)
    registry.register(
        JsonExternalEvidenceAdapter(
            adapter_id="fixture-json-adapter",
            adapter_version="1",
            trusted=True,
        )
    )
    policy = ExactSignoffPolicy()
    facade = _facade(
        tmp_path,
        provider=StaticProvider(approved_profile),
        verifier=ExactVerifier(),
        planner=DeterministicPlanner(),
        generator=DeterministicGenerator(),
        external_registry=registry,
        external_report_root=report_root,
        signoff_policy=policy,
    )
    facade.teg_intake(design_intent_draft=draft, job_id="full-signoff-job")
    facade.teg_plan(job_id="full-signoff-job", approval_reference=_reference(draft))
    generated = facade.teg_generate(
        job_id="full-signoff-job",
        approval_reference=_reference(draft),
        output_name="final.gds",
    )
    facade.teg_verify(
        job_id="full-signoff-job",
        approval_reference=_reference(draft),
        measurement_manifest=_measurement(draft, generated["generated_layout_sha256"]),
    )
    for kind in ("drc", "lvs", "pex"):
        report = {
            "schema_version": 1,
            "kind": kind,
            "status": "passed",
            "engine": {"name": f"fixture-{kind}", "version": "1"},
            "deck_sha256": "a" * 64,
            "input_layout_sha256": generated["generated_layout_sha256"],
            "violation_count": 0,
            "mismatch_count": 0,
            "generated_at": "2026-08-31T00:00:00Z",
            "invocation_sha256": "b" * 64,
        }
        (report_root / f"{kind}.json").write_text(
            json.dumps(report), encoding="utf-8"
        )

    result = facade.teg_verify(
        job_id="full-signoff-job",
        approval_reference=_reference(draft),
        external_reports=[
            {
                "adapter_id": "fixture-json-adapter",
                "report_name": f"{kind}.json",
                "kind": kind,
            }
            for kind in ("drc", "lvs", "pex")
        ],
    )

    assert result["workflow_status"] == "signoff_evidence_approved"
    assert result["layout_signoff_evidence_approved"] is True
    assert result["production_ready"] is False
    assert policy.calls == 1


def test_kelvin_profile_engine_plans_exact_six_split_contract():
    draft = _six_split_kelvin_draft()
    capability = validate_process_capability(SLN001_KELVIN_PROCESS_CAPABILITY)

    result = KelvinM1PlanningEngine().plan(
        design_intent=draft,
        process_capability=capability,
    )

    assert result["ok"] is True
    assert result["plan"]["profile"] == "sln001_kelvin_m1_six_split_v1"
    assert [
        (item["width_nm"], item["length_nm"])
        for item in result["plan"]["splits"]
    ] == list(DEFAULT_SPLITS)


def test_real_kelvin_four_call_facade_reproduces_golden_xor_zero(tmp_path):
    try:
        executable = find_klayout_executable()
    except Exception:
        pytest.skip("KLayout executable is not installed")
    reference = (
        Path(__file__).resolve().parents[1]
        / "artifacts"
        / "SLN001_kelvin_m1"
        / "SLN001_kelvin_m1_aligned_force_pad_joint_v15.gds"
    )
    if not reference.is_file():
        pytest.skip("Golden Kelvin reference is not present")
    draft = _six_split_kelvin_draft()
    facade = _facade(
        tmp_path,
        provider=StaticProvider(SLN001_KELVIN_PROCESS_CAPABILITY),
        verifier=ExactVerifier(),
        planner=KelvinM1PlanningEngine(),
        generator=KelvinM1GenerationEngine(
            template_gds_path=reference,
            reference_gds_path=reference,
            klayout_executable=str(executable),
        ),
    )

    facade.teg_intake(design_intent_draft=draft, job_id="kelvin-real")
    planned = facade.teg_plan(
        job_id="kelvin-real", approval_reference=_reference(draft)
    )
    generated = facade.teg_generate(
        job_id="kelvin-real",
        approval_reference=_reference(draft),
        output_name="kelvin-final.gds",
    )
    verified = facade.teg_verify(
        job_id="kelvin-real", approval_reference=_reference(draft)
    )

    assert planned["workflow_status"] == "plan_complete"
    assert generated["fresh_reload_verified"] is True
    assert generated["connectivity_projection_verified"] is True
    assert Path(generated["output_path"]).is_file()
    assert verified["generated_layout_sha256"] == generated["generated_layout_sha256"]


def test_host_registry_dispatches_exact_profile_without_model_registration(tmp_path):
    draft = _six_split_kelvin_draft()
    registry = WorkflowEngineRegistry()
    planner = KelvinM1PlanningEngine()
    registry.register(
        process_profile="sln001_kelvin_reference_demo",
        planning_engine=planner,
    )
    store = WorkflowJobStore(tmp_path / "jobs", output_root=tmp_path / "safe-output")
    facade = TegWorkflowFacade(
        store=store,
        process_provider=StaticProvider(SLN001_KELVIN_PROCESS_CAPABILITY),
        approval_verifier=ExactVerifier(),
        engine_registry=registry,
        production_mode=False,
        clock=lambda: datetime.fromisoformat(CHECKED_AT),
    )

    facade.teg_intake(design_intent_draft=draft, job_id="registry-job")
    result = facade.teg_plan(
        job_id="registry-job", approval_reference=_reference(draft)
    )

    assert result["workflow_status"] == "plan_complete"
    assert registry.contract()["model_can_register_or_import_engines"] is False
    assert registry.contract()["registered_profiles"] == [
        "sln001_kelvin_reference_demo"
    ]


def test_tampered_non_head_ancestor_is_rejected(tmp_path):
    draft = _draft()
    facade = _facade(
        tmp_path,
        verifier=ExactVerifier(),
        planner=DeterministicPlanner(),
        generator=DeterministicGenerator(),
    )
    facade.teg_intake(design_intent_draft=draft, job_id="ancestor-tamper")
    facade.teg_plan(job_id="ancestor-tamper", approval_reference=_reference(draft))
    facade.teg_generate(
        job_id="ancestor-tamper",
        approval_reference=_reference(draft),
        output_name="final.gds",
    )
    head = facade.store.head("ancestor-tamper")
    ancestor_hash = head["manifest"]["parent_manifest_sha256"]
    while True:
        ancestor_path = tmp_path / "jobs" / "manifests" / f"{ancestor_hash}.json"
        ancestor = facade.store._read_json(ancestor_path)
        if ancestor["parent_manifest_sha256"] is None:
            break
        ancestor_hash = ancestor["parent_manifest_sha256"]
    ancestor_path.write_text("{}", encoding="utf-8")

    with pytest.raises(AnalysisError) as caught:
        facade.store.head("ancestor-tamper")

    assert caught.value.code == "WORKFLOW_MANIFEST_INTEGRITY_FAILURE"


def test_workflow_store_exposes_output_publication_doctor_and_scavenger(tmp_path):
    store = WorkflowJobStore(tmp_path / "jobs", output_root=tmp_path / "safe-output")

    status = store.publication_status(active_probe=True)

    assert status["supported_filesystem"] is True
    assert status["file_create_only_probe"] is True
    assert status["directory_create_only_probe"] is True

    stale = store.output_root / ".klayout-stage-file-workflow-999-dead"
    unrelated = store.output_root / "user-file"
    stale.write_bytes(b"stale")
    unrelated.write_bytes(b"keep")
    os.utime(stale, (1, 1))

    report = store.scavenge_staging(ttl_seconds=1)

    assert report["removed_file_count"] == 1
    assert not stale.exists()
    assert unrelated.read_bytes() == b"keep"
