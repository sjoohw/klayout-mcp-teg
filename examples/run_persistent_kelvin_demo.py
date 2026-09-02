"""Run the host-integrated persistent Kelvin workflow as a nonproduction demo.

The approval verifier in this file is deliberately named and configured as a
test-only authority.  Production mode rejects it.  Real deployments must inject
their own externally backed verifier instead of copying this verifier.
"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from klayout_mcp.klayout_adapter import find_klayout_executable
from klayout_mcp.kelvin_routing import DEFAULT_SITE_ORIGINS_UM, DEFAULT_SPLITS
from klayout_mcp.kelvin_workflow import (
    KelvinM1GenerationEngine,
    KelvinM1PlanningEngine,
    SLN001_KELVIN_PROCESS_CAPABILITY,
)
from klayout_mcp.process_capability import validate_process_capability
from klayout_mcp.workflow_manifest import canonical_sha256
from klayout_mcp.workflow_store import (
    MappingProcessCapabilityProvider,
    TegWorkflowFacade,
    WorkflowEngineRegistry,
    WorkflowJobStore,
)


PROFILE = "sln001_kelvin_reference_demo"
PROFILE_VERSION = "golden-v15-2026-08-25"
CHECKED_AT = "2026-09-01T12:00:00+09:00"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class NonproductionTestApprovalVerifier:
    """Deterministic test authority; rejected whenever production_mode is true."""

    backend_id = "test-only-local-kelvin-approval"
    trusted = True
    nonproduction_only = True

    def verify(self, **kwargs: Any) -> Mapping[str, Any]:
        draft = kwargs["draft_document"]
        reference = kwargs["approval_reference"]
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
            "verification_receipt_sha256": canonical_sha256(
                {
                    "test_only": True,
                    "draft": canonical_sha256(draft),
                    "approval": canonical_sha256(reference),
                    "checked_at": kwargs["checked_at"],
                }
            ),
        }


def _complete_kelvin_draft(template: Mapping[str, Any]) -> dict[str, Any]:
    draft = json.loads(json.dumps(template))
    draft["intent_id"] = "nonproduction-persistent-kelvin-demo"
    devices: list[dict[str, Any]] = []
    contracts: list[dict[str, Any]] = []
    mappings: list[dict[str, Any]] = []
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
        "timing": {
            "settling_s": 0.1,
            "integration": {"mode": "instrument_default"},
            "hold_s": 0.0,
            "delay_s": 0.0,
        },
        "environment": {"status": "not_controlled_nonproduction_demo"},
        "safety_envelope": {
            "limits": {"max_abs_current_a": 0.01, "max_abs_voltage_v": 1.0},
            "source_reference": "test-only:local-demo",
            "em_current_density_evidence": None,
        },
    }
    draft["unresolved_questions"] = []
    return draft


def _approval_reference(draft: Mapping[str, Any], golden: Path) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "draft_sha256": canonical_sha256(draft),
        "process_capability_sha256": draft["process"]["capability_sha256"],
        "source_artifact_sha256s": {"kelvin_golden_gds": _file_sha256(golden)},
        "approval_scope": "planning_and_generation",
        "output_classes": ["nonproduction_gds"],
        "signer_reference": "test-only:local-demo",
        "scheme_id": "test-only-exact-binding-v1",
        "attestation_reference": "test-only://persistent-kelvin-demo",
        "approved_at": "2026-09-01T11:00:00+09:00",
        "revocation_id": "test-only-persistent-kelvin-demo",
    }


def _measurement_manifest(
    draft: Mapping[str, Any], layout_sha256: str
) -> dict[str, Any]:
    roles = {
        (contract["dut_id"], terminal["name"]): terminal["electrical_role"]
        for contract in draft["terminal_contracts"]
        for terminal in contract["terminals"]
    }
    return {
        "schema_version": 1,
        "design_intent_sha256": canonical_sha256(draft),
        "generated_layout_sha256": layout_sha256,
        "dut_pin_map": [
            {
                "dut_id": record["dut_id"],
                "terminal": record["terminal"],
                "net": record["net"],
                "pad": record["pad"],
                "probe_pin": f"P{record['pad']}",
                "instrument_channel": f"CH{record['pad']}",
                "electrical_role": roles[(record["dut_id"], record["terminal"])],
            }
            for record in draft["terminal_net_pad_map"]
        ],
        "electrical_topology": {"type": "direct", "connections": [], "guards": []},
        "stimuli": [
            {
                "stimulus_id": "kelvin-current",
                "requirement_kind": "stimulus",
                "requirement_mode": "current",
                "target": {"dut_id": "K1", "terminal": "F+"},
                "source_mode": "current",
                "program": {"kind": "dc_value", "value": 0.001, "unit": "A"},
                "compliance": {"quantity": "voltage", "limit": 1.0, "unit": "V"},
                "polarity": "positive",
                "frequency_hz": None,
            }
        ],
        "observables": [
            {
                "label": "kelvin_voltage",
                "requirement_mode": "voltage",
                "quantity": "voltage",
                "unit": "V",
                "source": {"dut_id": "K1", "terminal": "S+"},
            }
        ],
        "timing": {
            "settling_s": 0.1,
            "integration": {"mode": "instrument_default"},
            "hold_s": 0.0,
            "delay_s": 0.0,
        },
        "environment": {"status": "not_controlled_nonproduction_demo"},
        "safety_envelope": {
            "limits": {"max_abs_current_a": 0.01, "max_abs_voltage_v": 1.0},
            "source_reference": "test-only:local-demo",
            "em_current_density_evidence": None,
        },
        "calibration_and_deembedding": {
            "required": False,
            "calibration_plane": "probe_tip",
            "reference_duts": [],
        },
    }


def run_demo(*, project_root: Path, run_root: Path) -> dict[str, Any]:
    golden = (
        project_root
        / "artifacts"
        / "SLN001_kelvin_m1"
        / "SLN001_kelvin_m1_aligned_force_pad_joint_v15.gds"
    ).resolve()
    if not golden.is_file():
        raise FileNotFoundError(f"Kelvin golden GDS is missing: {golden}")
    executable = find_klayout_executable()
    run_root = run_root.resolve()
    provider = MappingProcessCapabilityProvider(
        {(PROFILE, PROFILE_VERSION): SLN001_KELVIN_PROCESS_CAPABILITY},
        provider_id="test-only-local-process-registry",
    )
    registry = WorkflowEngineRegistry()
    registry.register(
        process_profile=PROFILE,
        planning_engine=KelvinM1PlanningEngine(),
        generation_engine=KelvinM1GenerationEngine(
            template_gds_path=golden,
            reference_gds_path=golden,
            klayout_executable=str(executable),
        ),
    )
    facade = TegWorkflowFacade(
        store=WorkflowJobStore(
            run_root / "jobs",
            output_root=run_root / "final",
        ),
        process_provider=provider,
        approval_verifier=NonproductionTestApprovalVerifier(),
        engine_registry=registry,
        output_class="nonproduction_gds",
        production_mode=False,
        clock=lambda: datetime.fromisoformat(CHECKED_AT),
    )

    template_result = facade.teg_intake(
        template_process_profile=PROFILE,
        template_process_version=PROFILE_VERSION,
        template_family="resistor",
    )
    draft = _complete_kelvin_draft(template_result["template"])
    reference = _approval_reference(draft, golden)
    intake = facade.teg_intake(design_intent_draft=draft, job_id="kelvin-demo")
    planned = facade.teg_plan(job_id="kelvin-demo", approval_reference=reference)
    generated = facade.teg_generate(
        job_id="kelvin-demo",
        approval_reference=reference,
        output_name="kelvin-final.gds",
    )
    measurement = _measurement_manifest(draft, generated["generated_layout_sha256"])
    verified = facade.teg_verify(
        job_id="kelvin-demo",
        approval_reference=reference,
        measurement_manifest=measurement,
    )
    status = facade.teg_status(job_id="kelvin-demo")
    return {
        "ok": True,
        "demo_only": True,
        "production_ready": False,
        "template_status": template_result["workflow_status"],
        "four_call_statuses": [
            intake["workflow_status"],
            planned["workflow_status"],
            generated["workflow_status"],
            verified["workflow_status"],
        ],
        "job_id": status["job_id"],
        "final_output_path": generated["output_path"],
        "final_output_sha256": generated["generated_layout_sha256"],
        "fresh_reload_verified": generated["fresh_reload_verified"],
        "connectivity_projection_verified": generated[
            "connectivity_projection_verified"
        ],
        "measurement_layout_hash_match": verified[
            "measurement_layout_hash_match"
        ],
        "manifest_ancestry_revalidated": status[
            "manifest_ancestry_revalidated"
        ],
        "highest_attained_state": status["highest_attained_state"],
        "measurement_program_ready": False,
        "external_signoff_attached": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--run-root",
        type=Path,
        required=True,
        help="New or empty directory for this nonproduction demo run.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    arguments = parser.parse_args()
    print(
        json.dumps(
            run_demo(
                project_root=arguments.project_root,
                run_root=arguments.run_root,
            ),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
