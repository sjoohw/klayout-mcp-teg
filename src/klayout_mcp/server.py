"""STDIO MCP server."""

from __future__ import annotations

import asyncio
from importlib.metadata import version as package_version
import json
import os
from pathlib import Path
import platform
import re
from typing import Annotated, Any, Mapping

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from . import __version__
from .approval import approval_verifier_contract
from .assembly import plan_teg_dut_sequence as plan_dut_sequence
from .assembly_service import assemble_teg_service
from .drc_guardrails import DesignRuleConfig, verify_dut_design_rules
from .design_contract import (
    DEVICE_SPECIFIC_W_L,
    confirm_dimension_semantics,
    layout_contract_status,
    validate_orthogonal_m1_shapes,
)
from .device_doe import plan_phase1_device_doe as build_phase1_device_doe
from .dut_geometry import DutParameters, build_dut_geometry, describe_dut_pcell_contract
from .dut_corpus import (
    build_technology_adapter_candidate as build_adapter_candidate,
    onboard_dut_corpus as onboard_labeled_dut_corpus,
    resolve_corpus_variations as resolve_labeled_corpus,
    score_reproduced_corpus as score_labeled_corpus,
)
from .drawing_service import draw_manhattan_layout_service
from .evidence_state import evidence_ladder_contract
from .errors import AnalysisError
from .external_evidence import external_evidence_contract
from .geometry import Box
from .host_factory import (
    HostComponents,
    build_host_components_from_toml,
    load_deployment_toml,
)
from .klayout_adapter import create_layout_snapshot, run_klayout_worker
from .kelvin_service import (
    compare_kelvin_layouts_service,
    generate_kelvin_m1_teg_service,
    plan_kelvin_m1_routing_service,
)
from .kelvin_workflow import (
    KelvinM1GenerationEngine,
    KelvinM1PlanningEngine,
    SLN001_KELVIN_PROCESS_CAPABILITY,
)
from .layout_service import compare_layouts_service, inspect_layout_service
from .layermap import load_layermap
from .mesh_routing import (
    synthesize_maximum_contact_array as compile_maximum_contact_array,
    synthesize_staged_mesh_segment as compile_staged_mesh_segment,
)
from .mcp_protocol import ADDITIVE_WRITE, READ_ONLY, McpToolResult, protocol_tool
from .organization_presets import load_organization_preset
from .pad_macro import (
    compose_pad_macro_overlay as compose_immutable_pad_overlay,
    create_pad_macro_artifact,
)
from .padset import PadDetectionConfig, analyze_pad_boxes as analyze_boxes
from .padset_service import analyze_padset_snapshot
from .pcell_library import generate_pcell_python_source
from .pcellizer_service import inventory_pcellizer_hierarchy_service
from .pcellizer_snapshot import (
    create_pcellizer_snapshot_package,
    inspect_pcellizer_snapshot_package,
    recover_pcellizer_snapshot_source,
)
from .pcellizer_intent import (
    build_pcellizer_parameter_intent as build_pcellizer_intent,
)
from .pcellizer_recipe import (
    compile_pcellizer_single_shape_recipe as compile_single_shape_pcellizer_recipe,
)
from .pcellizer_batch import plan_pcellizer_split_batch as build_pcellizer_split_batch
from .pcellizer_batch_service import (
    generate_pcellizer_split_batch_service,
    inspect_pcellizer_batch_package,
)
from .pcellizer_process_intake import plan_pcellizer_process_inputs as build_pcellizer_process_inputs
from .phase1_primitives import (
    plan_metal_resistor_primitive as build_metal_resistor_primitive,
    plan_mom_capacitor_primitive as build_mom_capacitor_primitive,
)
from .phase1_layout import compose_phase1_direct_layout
from .phase1_routing import plan_phase1_terminal_routes as build_phase1_terminal_routes
from .phase1_service import generate_phase1_direct_teg_service
from .phase1_workflow import guide_phase1_direct_workflow as build_phase1_workflow_guide
from .profiles import DEFAULT_TEG_PROFILE
from .process_capability import (
    describe_builtin_process_capability,
    pdk_profile_input_contract,
    validate_process_capability,
)
from .reference_library import ReferenceLibrary, reference_library_contract
from .reference_service import (
    default_reference_library_root,
    register_reference_layout_service,
)
from .selection import plan_transistor_array as plan_array
from .selection import select_routed_units as select_units
from .sample_service import inspect_sample_dut_service
from .style_service import extract_layout_style_service
from .teg_planning import plan_teg_measurement_request
from .technology_registry import TechnologyAdapterRegistry
from .verification_runner import external_verification_runner_contract
from .transistor_context import plan_single_transistor_context as build_single_transistor_context
from .workflow_manifest import canonical_sha256, workflow_document_contract
from .workflow_types import (
    ApprovalReferenceInput,
    DesignIntentDraftInput,
)
from .workflow_store import (
    MappingProcessCapabilityProvider,
    TegWorkflowFacade,
    WorkflowEngineRegistry,
    WorkflowJobStore,
    workflow_store_contract,
)


# Backward-compatible injection seam retained while implementation lives in padset_service.
_analyze_padset_snapshot = analyze_padset_snapshot
_teg_workflow_facade_instance: TegWorkflowFacade | None = None
_host_components_instance: HostComponents | None = None
_active_tool_mode = "expert"

_TOOL_MODE_ALLOWLISTS: dict[str, frozenset[str] | None] = {
    "expert": None,
    "facade": frozenset(
        {
            "server_status",
            "host_doctor",
            "teg_intake",
            "teg_status",
            "teg_plan",
            "teg_generate",
            "teg_verify",
        }
    ),
    "drawing": frozenset(
        {
            "server_status",
            "draw_manhattan_layout",
            "inspect_layout",
            "extract_layout_style",
            "compare_layouts",
            "plan_staged_mesh_segment",
            "plan_maximum_contact_array",
        }
    ),
    "onboarding": frozenset(
        {
            "server_status",
            "host_doctor",
            "register_pad_macro",
            "compose_registered_pad_macro",
            "onboard_transistor_corpus",
            "resolve_transistor_corpus",
            "score_transistor_adapter",
            "build_transistor_adapter_candidate",
            "register_transistor_adapter_candidate",
        }
    ),
}


def _default_workflow_roots() -> tuple[Path, Path]:
    deployment_path = os.environ.get("KLAYOUT_MCP_DEPLOYMENT_TOML")
    if deployment_path:
        deployment = load_deployment_toml(deployment_path)
        paths = deployment.get("paths", {})
        workflow_root = paths.get("workflow_root")
        output_root = paths.get("output_root")
        if not isinstance(workflow_root, str) or not isinstance(output_root, str):
            raise AnalysisError(
                code="DEPLOYMENT_WORKFLOW_PATH_REQUIRED",
                message="paths.workflow_root and paths.output_root are required.",
                details={
                    "field": "paths",
                    "stage": "host_startup",
                    "missing": [
                        name
                        for name, value in (
                            ("workflow_root", workflow_root),
                            ("output_root", output_root),
                        )
                        if not isinstance(value, str)
                    ],
                },
                next_action=(
                    "Configure host-controlled workflow and final output directories."
                ),
            )
        return Path(workflow_root), Path(output_root)

    project_root = Path(__file__).resolve().parents[2]
    workflow_root = Path(
        os.environ.get(
            "KLAYOUT_MCP_WORKFLOW_ROOT",
            str(project_root / "output" / "workflow-jobs"),
        )
    )
    workflow_output_root = Path(
        os.environ.get(
            "KLAYOUT_MCP_WORKFLOW_OUTPUT_ROOT",
            str(project_root / "output" / "workflow-final"),
        )
    )
    return workflow_root, workflow_output_root


def _onboarding_roots() -> dict[str, Path]:
    workflow_root, output_root = _default_workflow_roots()
    return {
        "pad_macros": workflow_root / "onboarding" / "pad-macros",
        "pad_outputs": output_root / "onboarding-pad-overlays",
        "corpora": workflow_root / "onboarding" / "dut-corpora",
        "resolutions": workflow_root / "onboarding" / "corpus-resolutions",
        "scorecards": workflow_root / "onboarding" / "adapter-scorecards",
        "adapters": workflow_root / "onboarding" / "adapter-candidates",
    }


def _content_package(root: Path, digest: str) -> Path:
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise AnalysisError(
            code="INVALID_CONTENT_PACKAGE_HASH",
            message="Artifact package reference must be a lowercase SHA-256 digest.",
            details={"field": "package_sha256", "value": digest, "stage": "onboarding"},
            next_action="Use the exact package hash returned by the preceding onboarding tool.",
        )
    return root / digest


def _default_host_components() -> HostComponents:
    """Build explicit stock host components; approval remains fail-closed."""

    global _host_components_instance
    if _host_components_instance is not None:
        return _host_components_instance
    deployment_path = os.environ.get("KLAYOUT_MCP_DEPLOYMENT_TOML")
    if deployment_path:
        _host_components_instance = build_host_components_from_toml(deployment_path)
        return _host_components_instance
    project_root = Path(__file__).resolve().parents[2]
    workflow_root, workflow_output_root = _default_workflow_roots()
    provider = MappingProcessCapabilityProvider(
        {
            (
                "sln001_kelvin_reference_demo",
                "golden-v15-2026-08-25",
            ): SLN001_KELVIN_PROCESS_CAPABILITY,
        },
        provider_id="bundled-process-capabilities-v1",
    )
    registry = WorkflowEngineRegistry()
    golden = (
        project_root
        / "artifacts"
        / "SLN001_kelvin_m1"
        / "SLN001_kelvin_m1_aligned_force_pad_joint_v15.gds"
    )
    registry.register(
        process_profile="sln001_kelvin_reference_demo",
        planning_engine=KelvinM1PlanningEngine(),
        generation_engine=(
            KelvinM1GenerationEngine(
                template_gds_path=golden,
                reference_gds_path=golden,
            )
            if golden.is_file()
            else None
        ),
    )
    _host_components_instance = HostComponents(
        store=WorkflowJobStore(
            workflow_root,
            output_root=workflow_output_root,
        ),
        process_provider=provider,
        approval_verifier=None,
        engine_registry=registry,
        technology_registry=TechnologyAdapterRegistry(
            workflow_root / "technology-registry"
        ),
        production_mode=True,
        output_class="nonproduction_gds",
    )
    return _host_components_instance


def _default_teg_workflow_facade() -> TegWorkflowFacade:
    """Build the local persistent facade from explicit host components."""

    global _teg_workflow_facade_instance
    if _teg_workflow_facade_instance is not None:
        return _teg_workflow_facade_instance
    _teg_workflow_facade_instance = _default_host_components().build_facade()
    return _teg_workflow_facade_instance


mcp = FastMCP(
    "klayout-teg-mcp",
    instructions=(
        "General-purpose Manhattan layout drawing and PCell MCP. The fixed 25-Pad TEG, "
        "transistor, and Kelvin flows are optional domain profiles, not the server scope. "
        "Tool registration is not process readiness. Stock Phase 1 is nonproduction: transistor "
        "has no process adapter and Pad geometry is synthesized rather than imported. Its "
        "DUT-to-Pad polylines compile to bounded multi-rail meshes, but conceptual DUT tools "
        "cannot replace the missing transistor, pad-macro, or foundry adapters. "
        "If a generation request omits any process, rule, terminal, bias, obstacle, dimension, "
        "or output-path decision, call plan_direct_measurement_teg with only facts explicitly "
        "provided by the user. Never change a confirmation from false to true, never invent "
        "terminal assignments, and stop after returning its required questions; do not call a "
        "write tool, shell command, or downstream primitive in that turn. "
        "Use draw_manhattan_layout for create-only cell, box, text, orthogonal-instance, and "
        "boolean drawing from explicit DBU and layer contracts; same-target local writers have "
        "one no-clobber winner. "
        "For a resumable Phase 1 direct-measurement workflow, call "
        "guide_phase1_direct_workflow with the outputs already obtained; follow its single "
        "next_tool and next_action without skipping or silently mutating handoffs. "
        "When a user provides process-node reference GDS files, register them with "
        "register_reference_layout. The model may list and recommend candidates, but it must "
        "not confirm one. Prepare an exact concern-scoped view, let the user inspect the full "
        "immutable GDS in the KLayout Reference Navigator, then import that GUI confirmation "
        "with confirm_reference_view. Use only the returned exact selection_id. A confirmed "
        "reference_precedent may accept more DRC markers than the reference contains when each "
        "marker independently matches the same process node, concern, layers, violation type, "
        "structural context signature, and severity policy. Marker count alone is never an "
        "acceptance limit. Never extend a precedent to another concern or auto-accept an "
        "unmatched marker. Until a process-specific trusted marker adapter validates similarity, "
        "return unmatched markers as non-blocking REVIEW_NEEDED advice; do not stop drawing "
        "solely because an unvalidated similarity gate rejected a marker. Report REF_ACCEPTED "
        "separately from DRC-clean. "
        "For the host-integrated persistent workflow contract, start with teg_intake. Stock "
        "supports the bundled research-only Kelvin template/intake/status path and fails at "
        "teg_plan before planning because no trusted approval verifier is configured. If no complete draft "
        "exists, omit design_intent_draft and provide an exact template process/version and "
        "one device family; fill every returned required question before calling teg_intake "
        "again. Continue only with teg_plan, teg_generate, and teg_verify using the returned "
        "job_id. These calls never mint approval and remain fail-closed when the host has no "
        "trusted approval backend. "
        "Before drawing a direct-measurement transistor, resistor, or capacitor TEG, use "
        "plan_direct_measurement_teg to close the process, Pad budget, DUT terminal, bias, "
        "obstacle, and first-metal feasibility gates. A boolean confirmation without explicit "
        "terminal records is insufficient. Use plan_phase1_device_doe to expand only axes "
        "supported by the confirmed process profile; keep LDE axes distinct and do not infer "
        "unsupported SA/SB, WPE, STI, orientation, dummy, finger, contact, or guard-ring rules. "
        "Use plan_phase1_direct_teg_layout only after those gates pass; it must recheck exact "
        "DUT-terminal-to-Pad endpoints, non-target Pad/DUT crossings, and width-dependent "
        "first-metal spacing before producing a draw_manhattan_layout request. "
        "Padset and layermap are mandatory for production work, and current Phase 1 cannot "
        "satisfy the padset-preservation requirement. "
        "Use padset DBU. Do not infer production layers. Do not run DRC or LVS by default. "
        "Routing must be horizontal/vertical Manhattan geometry only; diagonal and arbitrary-"
        "angle routing are forbidden. Before interpreting width and length, ask the user to "
        "confirm their meanings and pass dimension_semantics. For generic or resistor geometry, "
        "width is transverse to current flow and length is longitudinal to current flow; this "
        "directional mapping imposes no width-versus-length numeric ordering. Never swap them. "
        "The following mesh/contact rules are target acceptance requirements, not claims about "
        "the current Phase 1 composer. For direct-measurement TEG terminal-to-Pad routing, "
        "forbid long single-rail fallback. "
        "Outside a bounded terminal transition, require process-rule-compliant parallel "
        "orthogonal rails, repeated cross-ties, a hole-bearing merged mesh, and multiple "
        "positive-area Pad landings for source, drain, gate, body, force, sense, and shared "
        "buses. Maximize the mesh envelope within confirmed boundaries and rules; a token "
        "narrow mesh is not acceptable when a wider mesh is feasible. Use aligned staged "
        "transitions and natural full-width orthogonal joints, preserving the intermediate "
        "mesh and changing only its interface where possible. For transistor source/drain "
        "terminals, place the maximum legal contact count after cut, enclosure, metal-spacing, "
        "and neighboring-terminal checks; the count must increase with available device width "
        "unless explicit constraints prove otherwise. If first-metal routing is infeasible, "
        "stop or request explicit multi-metal "
        "escalation; never silently fall back to a single rail. "
        "Use plan_staged_mesh_segment and plan_maximum_contact_array as the "
        "process-agnostic integer-DBU compilers for direct-measurement routing. "
        "The selected process profile supplies confirmed orthogonal corridors, ports, "
        "and numeric cut/enclosure/metal rules; it must not replace these generic "
        "compilers with a profile-local routing algorithm when this contract can "
        "represent the geometry. Treat geometry_maximized_not_pex_proven literally: "
        "maximum legal geometry is not proof of minimum extracted resistance. "
        "For Kelvin M1 line measurement, call plan_kelvin_m1_routing first and pass its exact "
        "confirmed specification to generate_kelvin_m1_teg. The dedicated planner, compiler, "
        "and fresh-reload verifier enforce measured-line isolation, separate force/sense "
        "access, staged one-sided mesh expansion, aligned full-width joints, dense Pad "
        "landings, and the selected profile's numeric dimensions. Do not reproduce or alter "
        "those profile-local coordinates from global instructions. "
        "Solid external trunks or sheets are forbidden. "
        "When no approved foundry spacing rule is available, prefer clear space at least as "
        "large as the wider of two adjacent metal lines; for equal 0.300 um lines use at least "
        "0.300 um clear space. Intentional electrical junctions are excluded from this spacing. "
        "Minimize routing parasitic resistance, but never claim an optimal structure without "
        "approved process data and extracted-RC comparison of feasible candidates. "
        "For inspect_layout and compare_layouts, pass the user-provided existing path directly "
        "to the MCP tool. Relative paths resolve from the MCP process working directory; do "
        "not search the filesystem or run a shell preflight because the tool snapshots and "
        "validates each input itself. "
        "Treat every bundled process profile as an explicitly selected nonproduction example, "
        "never as a global rule source. Obtain process identity, DBU/grid, semantic layermap, "
        "routing rules, device/terminal contract, and verification evidence from the selected "
        "PDK profile or the user before process-specific drawing. "
        "For a single-transistor measurement with no explicit context override, apply the "
        "organization transistor-context preset: fill the DUT window, select one device by "
        "default from the balanced central region at least 5 um inside the array edge, leave "
        "surrounding devices unrouted, share diffusion only between "
        "compatible neighbors, and use same_as_measured fill. standard_cell_like uses the "
        "repeating x sequence nmos/pmos/pmos/nmos, requires standard_cell_height_um, and does not share "
        "diffusion across N/P boundaries. Explicit jobs may select multiple measured devices."
    ),
)


@protocol_tool(mcp, annotations=READ_ONLY)
def server_status() -> McpToolResult:
    """Return server capabilities that do not require KLayout."""

    status: McpToolResult = {
        "ok": True,
        "server": "klayout-teg-mcp",
        "version": __version__,
        "tool_surface": {
            "active_mode": _active_tool_mode,
            "available_modes": sorted(_TOOL_MODE_ALLOWLISTS),
            "configuration": "KLAYOUT_MCP_TOOL_MODE",
            "expert_is_default": False,
            "expert_is_readiness_claim": False,
            "mode_reduces_tools_not_common_instruction": True,
        },
        "capabilities_semantics": (
            "registered tools include planning, conceptual, nonproduction, and incomplete "
            "workflows; registration is not target-process readiness"
        ),
        "known_limitations": {
            "stock_classification": "generic_nonproduction_drawing_and_contract_framework",
            "transistor_primitive_adapter": "not_implemented",
            "conceptual_transistor_is_phase1_fallback": False,
            "phase1_padset_import_or_preservation": "not_implemented",
            "phase1_pad_geometry": "synthesized_from_frame_and_pad_count",
            "phase1_dut_to_pad_route_geometry": "bounded_polyline_compiled_to_multi_rail_mesh",
            "phase1_standalone_mesh_compiler_integrated": True,
            "phase1_global_search_budget": "global_node_and_wall_time_budget_enforced",
            "stock_adapter_qualification_policy_authority": "not_configured",
            "caller_selected_score_can_qualify_adapter_candidate": False,
            "stock_lifecycle_external_trust_anchor": "not_configured",
            "local_lifecycle_head_detects_writer_compromise": False,
            "same_target_concurrent_writers_supported": True,
            "generic_manhattan_same_target_no_clobber": True,
            "exact_gemma4_qualified": False,
        },
        "capabilities": [
            "analyze_pad_boxes",
            "analyze_padset",
            "render_boundary_overlay",
            "select_routed_units",
            "plan_transistor_array",
            "plan_single_transistor_context",
            "describe_dut_pcell",
            "generate_dut_geometry",
            "inspect_sample_dut",
            "plan_teg_dut_sequence",
            "verify_design_rules",
            "assemble_teg",
            "export_pcell_code",
            "plan_kelvin_m1_routing",
            "generate_kelvin_m1_teg",
            "compare_kelvin_layouts",
            "draw_manhattan_layout",
            "inspect_layout",
            "extract_layout_style",
            "inventory_pcellizer_hierarchy",
            "create_pcellizer_snapshot",
            "inspect_pcellizer_snapshot",
            "recover_pcellizer_snapshot",
            "plan_pcellizer_process_inputs",
            "define_pcellizer_parameter",
            "compile_pcellizer_recipe",
            "plan_pcellizer_split_table",
            "generate_pcellizer_split_batch",
            "inspect_pcellizer_batch",
            "compare_layouts",
            "plan_staged_mesh_segment",
            "plan_maximum_contact_array",
            "plan_direct_measurement_teg",
            "plan_phase1_device_doe",
            "describe_process_capability",
            "describe_pdk_profile_inputs",
            "describe_organization_preset",
            "validate_process_capability_profile",
            "plan_metal_resistor_primitive",
            "plan_mom_capacitor_primitive",
            "plan_phase1_terminal_routes",
            "guide_phase1_direct_workflow",
            "plan_phase1_direct_teg_layout",
            "generate_phase1_direct_teg",
            "host_doctor",
            "register_pad_macro",
            "compose_registered_pad_macro",
            "onboard_transistor_corpus",
            "resolve_transistor_corpus",
            "score_transistor_adapter",
            "build_transistor_adapter_candidate",
            "register_transistor_adapter_candidate",
            "teg_intake",
            "teg_status",
            "teg_plan",
            "teg_generate",
            "teg_verify",
            "register_reference_layout",
            "list_reference_layouts",
            "prepare_reference_view",
            "confirm_reference_view",
            "consult_reference_selection",
            "classify_reference_drc_markers",
        ],
        "klayout_adapter": "subprocess",
        "recommended_entrypoints": {
            "inspect_existing_layout": ["inspect_layout"],
            "extract_reference_style": ["extract_layout_style"],
            "compare_existing_layouts": ["compare_layouts"],
            "generic_nonproduction_drawing": ["draw_manhattan_layout"],
            "incomplete_direct_teg_request": ["plan_direct_measurement_teg"],
            "nonproduction_phase1_handoff_guide": ["guide_phase1_direct_workflow"],
            "parameterize_existing_gds": ["inventory_pcellizer_hierarchy"],
            "process_reference_library": ["register_reference_layout"],
            "stock_persistent_intake_or_status": ["teg_intake", "teg_status"],
            "configured_host_readiness": ["host_doctor"],
        },
        "runtime": {
            "python": platform.python_version(),
            "mcp_sdk": package_version("mcp"),
        },
        "klayout_support": {
            "minimum_version": "0.30.0",
            "validated_version": "0.30.10",
            "version_reported_by_layout_tools": True,
        },
        "layout_contract": layout_contract_status(),
        "evidence_ladder_contract": evidence_ladder_contract(),
        "workflow_document_contract": workflow_document_contract(),
        "approval_verifier_contract": approval_verifier_contract(
            backend_configured=False
        ),
        "workflow_store_contract": workflow_store_contract(),
        "external_evidence_contract": external_evidence_contract(),
        "external_verification_runner_contract": external_verification_runner_contract(),
        "reference_library_contract": reference_library_contract(),
        "persistent_facade": {
            "tools": [
                "host_doctor",
                "teg_intake",
                "teg_status",
                "teg_plan",
                "teg_generate",
                "teg_verify",
            ],
            "default_approval_backend_configured": False,
            "stock_execution_limit": (
                "stock teg_intake accepts the exact bundled research Kelvin-resistor "
                "profile and its Kelvin engines are registered; teg_plan will fail closed "
                "before planning because no trusted host verifier is configured. Other target-production "
                "profiles additionally require matching providers, engines, runners, and policy"
            ),
            "bundled_process_profiles": [
                {
                    "profile": "sln001_kelvin_reference_demo",
                    "version": "golden-v15-2026-08-25",
                    "classification": "research_only_nonproduction_resistor_demo",
                    "engine_status": "planning_registered_generation_requires_golden_and_host_approval",
                },
            ],
            "target_production_transistor_engine_configured": False,
            "external_report_normalization_contract_available": True,
            "drc_lvs_pex_execution_runner_configured": False,
        },
    }
    allowlist = _TOOL_MODE_ALLOWLISTS[_active_tool_mode]
    active_tools = (
        {"server_status", *status["capabilities"]}
        if allowlist is None
        else set(allowlist)
    )
    status["tool_surface"]["active_tools"] = sorted(active_tools)
    status["capabilities"] = [
        name for name in status["capabilities"] if name in active_tools
    ]
    status["recommended_entrypoints"] = {
        goal: available
        for goal, tools in status["recommended_entrypoints"].items()
        if (available := [name for name in tools if name in active_tools])
    }
    status["persistent_facade"]["tools"] = [
        name for name in status["persistent_facade"]["tools"] if name in active_tools
    ]
    return status


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def host_doctor(active_output_probe: bool = False) -> McpToolResult:
    """Report configured profile×stage readiness and optionally probe output publication."""

    try:
        return _default_host_components().doctor(
            active_output_probe=active_output_probe
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def register_pad_macro(
    source_layout_path: str,
    top_cell: str,
    access_layer: dict[str, int],
    instances: list[dict[str, Any]],
    expected_width_um: float = 40.0,
    expected_height_um: float = 40.0,
    expected_dbu_um: float | None = None,
    klayout_executable: str | None = None,
) -> McpToolResult:
    """Inspect and preserve one immutable black-box pad macro package."""

    try:
        return create_pad_macro_artifact(
            source_layout_path=source_layout_path,
            top_cell=top_cell,
            access_layer=access_layer,
            instances=instances,
            package_root=_onboarding_roots()["pad_macros"],
            expected_width_um=expected_width_um,
            expected_height_um=expected_height_um,
            expected_dbu_um=expected_dbu_um,
            klayout_executable=klayout_executable,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def compose_registered_pad_macro(
    pad_macro_sha256: str,
    output_name: str,
    operations: list[dict[str, Any]],
    output_top_cell: str = "TEG_PAD_MACRO_OVERLAY",
    klayout_executable: str | None = None,
) -> McpToolResult:
    """Compose immutable pad instances with separate DUT/routing boxes."""

    try:
        roots = _onboarding_roots()
        if Path(output_name).name != output_name or Path(output_name).suffix.lower() not in {".gds", ".oas"}:
            raise AnalysisError(
                code="INVALID_ONBOARDING_OUTPUT_NAME",
                message="output_name must be one new GDS/OAS basename.",
                details={"field": "output_name", "value": output_name, "stage": "pad_macro_compose"},
                next_action="Provide a new basename such as pad-overlay.gds.",
            )
        roots["pad_outputs"].mkdir(parents=True, exist_ok=True)
        return compose_immutable_pad_overlay(
            package_path=_content_package(roots["pad_macros"], pad_macro_sha256),
            output_path=str(roots["pad_outputs"] / output_name),
            operations=operations,
            output_top_cell=output_top_cell,
            klayout_executable=klayout_executable,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def onboard_transistor_corpus(
    source_layout_path: str,
    technology_identity: dict[str, Any],
    device_family: str,
    topology: str,
    parameter_schema: dict[str, dict[str, Any]],
    compiler_model_spec: dict[str, Any],
    dut_records: list[dict[str, Any]],
    layer_roles: dict[str, dict[str, int]],
    validation_dut_ids: list[str],
    expected_dbu_um: float | None = None,
    klayout_executable: str | None = None,
) -> McpToolResult:
    """Onboard a labeled multi-DUT corpus and return coverage/clarification gates."""

    try:
        return onboard_labeled_dut_corpus(
            source_layout_path=source_layout_path,
            technology_identity=technology_identity,
            device_family=device_family,
            topology=topology,
            parameter_schema=parameter_schema,
            compiler_model_spec=compiler_model_spec,
            dut_records=dut_records,
            layer_roles=layer_roles,
            validation_dut_ids=validation_dut_ids,
            package_root=_onboarding_roots()["corpora"],
            expected_dbu_um=expected_dbu_um,
            klayout_executable=klayout_executable,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def resolve_transistor_corpus(
    corpus_sha256: str,
    decisions: dict[str, str],
    resolved_by: str,
    resolved_at: str,
) -> McpToolResult:
    """Record explicit human choices for every same-parameter geometry variation."""

    try:
        roots = _onboarding_roots()
        return resolve_labeled_corpus(
            corpus_package_path=_content_package(roots["corpora"], corpus_sha256),
            decisions=decisions,
            resolution_root=roots["resolutions"],
            resolved_by=resolved_by,
            resolved_at=resolved_at,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def score_transistor_adapter(
    corpus_sha256: str,
    reproduced_layout_path: str,
    reproduced_cell_by_dut_id: dict[str, str],
    scoring_policy: dict[str, Any],
    compiler_identity: dict[str, Any],
    klayout_executable: str | None = None,
) -> McpToolResult:
    """Score a distinct stream; caller policy is diagnostic unless the host injects its qualification authority."""

    try:
        roots = _onboarding_roots()
        return score_labeled_corpus(
            corpus_package_path=_content_package(roots["corpora"], corpus_sha256),
            reproduced_layout_path=reproduced_layout_path,
            reproduced_cell_by_dut_id=reproduced_cell_by_dut_id,
            scoring_policy=scoring_policy,
            scorecard_root=roots["scorecards"],
            compiler_identity=compiler_identity,
            qualification_policy_authority=(
                _default_host_components().qualification_policy_authority
            ),
            klayout_executable=klayout_executable,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def build_transistor_adapter_candidate(
    corpus_sha256: str,
    resolution_sha256: str,
    scorecard_sha256: str,
    adapter_identity: dict[str, Any],
    compiler_code_sha256: str,
) -> McpToolResult:
    """Build a host-policy-approved immutable candidate that remains nonproduction."""

    try:
        roots = _onboarding_roots()
        return build_adapter_candidate(
            corpus_package_path=_content_package(roots["corpora"], corpus_sha256),
            resolution_package_path=_content_package(roots["resolutions"], resolution_sha256),
            scorecard_package_path=_content_package(roots["scorecards"], scorecard_sha256),
            adapter_identity=adapter_identity,
            compiler_code_sha256=compiler_code_sha256,
            adapter_root=roots["adapters"],
            qualification_policy_authority=(
                _default_host_components().qualification_policy_authority
            ),
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def register_transistor_adapter_candidate(candidate_sha256: str) -> McpToolResult:
    """Register an immutable candidate by exact identity/hash without qualifying it."""

    try:
        roots = _onboarding_roots()
        package_path = _content_package(roots["adapters"], candidate_sha256) / "package.json"
        try:
            package = json.loads(package_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AnalysisError(
                code="TECH_ADAPTER_CANDIDATE_NOT_FOUND",
                message="The exact candidate package is missing or unreadable.",
                details={"field": "candidate_sha256", "value": candidate_sha256, "error_type": type(exc).__name__, "stage": "adapter_registration"},
                next_action="Use the exact hash returned by build_transistor_adapter_candidate.",
            ) from exc
        if (
            not isinstance(package, dict)
            or package.get("schema_version") != 1
            or package.get("artifact_type") != "TechnologyAdapterPackage"
            or canonical_sha256(package) != candidate_sha256
        ):
            raise AnalysisError(
                code="TECH_ADAPTER_CANDIDATE_PACKAGE_INVALID",
                message="Candidate metadata does not match its requested content address or schema.",
                details={
                    "field": "candidate_sha256",
                    "expected": candidate_sha256,
                    "received": canonical_sha256(package) if isinstance(package, dict) else None,
                    "stage": "adapter_registration",
                },
                next_action="Restore the exact untouched candidate package returned by the builder.",
            )
        host = _default_host_components()
        registered = host.technology_registry.register_package(package)
        snapshot = host.technology_registry.snapshot()
        return {
            **registered,
            "registry_snapshot_sha256": snapshot["snapshot_sha256"],
            "qualified": False,
            "production_ready": False,
            "next_gate": "Attach reviewed lifecycle evidence, then complete foundry DRC/LVS/PEX qualification.",
        }
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def teg_intake(
    design_intent_draft: Annotated[
        DesignIntentDraftInput | None,
        Field(
            description=(
                "Complete DesignIntentDraft. Omit it to receive a schema-valid, unapproved "
                "template and required confirmation questions without creating a job."
            )
        ),
    ] = None,
    job_id: Annotated[
        str | None,
        Field(description="Optional stable job id; omit for a content-derived id."),
    ] = None,
    draft_id: Annotated[
        str | None,
        Field(description="Stable immutable draft stream ID for correction/resume."),
    ] = None,
    expected_draft_revision: Annotated[
        int | None,
        Field(description="Last observed immutable draft revision; stale updates fail closed."),
    ] = None,
    resume_token: Annotated[
        str | None,
        Field(description="Content-bound token returned by the previous intake revision."),
    ] = None,
    validate_only: Annotated[
        bool,
        Field(description="Validate without persisting a draft or creating a job."),
    ] = False,
    template_process_profile: Annotated[
        str | None,
        Field(description="Exact profile for template mode, never inferred."),
    ] = None,
    template_process_version: Annotated[
        str | None,
        Field(description="Exact profile version for template mode, never inferred."),
    ] = None,
    template_family: Annotated[
        str | None,
        Field(description="Template family: transistor, resistor, or capacitor."),
    ] = None,
) -> McpToolResult:
    """Start/resume-safe intake or return a no-write draft template and questions."""

    try:
        return _default_teg_workflow_facade().teg_intake(
            design_intent_draft=design_intent_draft,
            job_id=job_id,
            draft_id=draft_id,
            expected_draft_revision=expected_draft_revision,
            resume_token=resume_token,
            validate_only=validate_only,
            template_process_profile=template_process_profile,
            template_process_version=template_process_version,
            template_family=template_family,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def teg_status(job_id: str) -> McpToolResult:
    """Revalidate and report one exact persistent job head for safe resume."""

    try:
        workflow_root, workflow_output_root = _default_workflow_roots()
        read_only_facade = TegWorkflowFacade(
            store=WorkflowJobStore(
                workflow_root,
                output_root=workflow_output_root,
                initialize=False,
            ),
            process_provider=None,
            production_mode=True,
        )
        return read_only_facade.teg_status(job_id=job_id)
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def teg_plan(
    job_id: str,
    approval_reference: ApprovalReferenceInput,
) -> McpToolResult:
    """Reverify exact trusted approval and persist the deterministic plan chain."""

    try:
        return _default_teg_workflow_facade().teg_plan(
            job_id=job_id,
            approval_reference=approval_reference,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def teg_generate(
    job_id: str,
    approval_reference: ApprovalReferenceInput,
    output_name: Annotated[
        str,
        Field(description="New basename only, such as final.gds; paths are forbidden."),
    ],
) -> McpToolResult:
    """Generate one final layout under the host output root after fresh reapproval."""

    try:
        return _default_teg_workflow_facade().teg_generate(
            job_id=job_id,
            approval_reference=approval_reference,
            output_name=output_name,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def teg_verify(
    job_id: str,
    approval_reference: ApprovalReferenceInput,
    measurement_manifest: Annotated[
        dict[str, Any] | None,
        Field(
            description=(
                "Optional MeasurementManifest JSON object obtained from the intake/job "
                "contract. Server-side validation returns exact field paths and fixes. "
                "When supplied, teg_verify freshly hashes the final layout and persists "
                "the package only on an exact match."
            )
        ),
    ] = None,
    external_reports: Annotated[
        list[dict[str, Any]] | None,
        Field(
            description=(
                "Optional exact {adapter_id,report_name,kind} records selecting pre-existing "
                "reports. The MCP does not execute DRC/LVS/PEX. The host must have a trusted "
                "adapter registry/report root; binding never means production_ready."
            )
        ),
    ] = None,
) -> McpToolResult:
    """Freshly rehash output and optionally promote the measurement package."""

    try:
        return _default_teg_workflow_facade().teg_verify(
            job_id=job_id,
            approval_reference=approval_reference,
            measurement_manifest=measurement_manifest,
            external_reports=external_reports,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def plan_staged_mesh_segment(
    dbu_um: float,
    start_um: list[float],
    end_um: list[float],
    corridor_um: Annotated[
        list[float],
        Field(description="Confirmed obstacle-free orthogonal corridor [x1,y1,x2,y2]."),
    ],
    rail_width_um: float,
    rail_space_um: float,
    landing_span_um: Annotated[
        float,
        Field(description="Available transverse span at the originating terminal landing."),
    ],
    transition_guard_um: float = 0.0,
    cross_tie_pitch_um: float | None = None,
    final_tie_reserve_um: float | None = None,
    receiving_tie_present: bool = True,
    minimum_rail_count: int = 2,
    cell: str = "ROUTE",
    layer_role: str = "m1",
) -> McpToolResult:
    """Compile one straight orthogonal staged-mesh segment in exact integer DBU.

    The caller must supply a confirmed obstacle-free corridor and process rules.
    Rail occupancy is maximized only inside that supplied corridor under the
    declared width/space subset. This tool performs no obstacle discovery,
    bend/global-net routing, full-PDK legality check, or Phase 1 integration.
    It fails closed rather than returning a token single rail, and its geometry
    is not an extracted-resistance claim.
    """

    try:
        return compile_staged_mesh_segment(
            dbu_um=dbu_um,
            start_um=start_um,
            end_um=end_um,
            corridor_um=corridor_um,
            rail_width_um=rail_width_um,
            rail_space_um=rail_space_um,
            landing_span_um=landing_span_um,
            transition_guard_um=transition_guard_um,
            cross_tie_pitch_um=cross_tie_pitch_um,
            final_tie_reserve_um=final_tie_reserve_um,
            receiving_tie_present=receiving_tie_present,
            minimum_rail_count=minimum_rail_count,
            cell=cell,
            layer_role=layer_role,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def plan_maximum_contact_array(
    dbu_um: float,
    array_center_um: list[float],
    array_axis: str,
    available_width_um: float,
    contact_size_um: float,
    contact_space_um: float,
    active_enclosure_um: float,
    metal_enclosure_um: float,
    metal_space_um: float,
    alignment: str = "centered",
    neighbor_metal_near_edge_um: float | None = None,
    neighbor_side: str | None = None,
    neighbor_clearance_um: float | None = None,
    cell: str = "DUT",
    contact_layer_role: str = "contact",
    metal_layer_role: str = "m1",
) -> McpToolResult:
    """Pack a maximum 1-D contact count under only the supplied local constraints.

    This is not a transistor adapter, full-PDK legality check, or DRC result.
    """

    try:
        return compile_maximum_contact_array(
            dbu_um=dbu_um,
            array_center_um=array_center_um,
            array_axis=array_axis,
            available_width_um=available_width_um,
            contact_size_um=contact_size_um,
            contact_space_um=contact_space_um,
            active_enclosure_um=active_enclosure_um,
            metal_enclosure_um=metal_enclosure_um,
            metal_space_um=metal_space_um,
            alignment=alignment,
            neighbor_metal_near_edge_um=neighbor_metal_near_edge_um,
            neighbor_side=neighbor_side,
            neighbor_clearance_um=neighbor_clearance_um,
            cell=cell,
            contact_layer_role=contact_layer_role,
            metal_layer_role=metal_layer_role,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def plan_direct_measurement_teg(
    device_families: Annotated[
        list[str] | None,
        Field(
            description=(
                "Intake/planning families only: transistor, resistor, and/or capacitor. "
                "This tool draws nothing and supplies no stock transistor adapter."
            )
        ),
    ] = None,
    process_profile: Annotated[
        str | None,
        Field(description="Exact PDK/process profile name; never inferred."),
    ] = None,
    process_profile_version: Annotated[
        str | None,
        Field(description="Exact capability profile version; never inferred from its name."),
    ] = None,
    frame_width_um: float = 2000.0,
    frame_height_um: float = 54.0,
    pad_count: int = 25,
    pad_rows: int = 1,
    pad_width_um: float = 40.0,
    pad_height_um: float = 40.0,
    dut_count: int | None = None,
    measurement_mode: str = "direct",
    prefer_first_metal: bool = True,
    allow_additional_metals_if_unavoidable: bool = True,
    approved_layermap: Annotated[
        bool,
        Field(description="True only after the user provides or explicitly approves the layermap."),
    ] = False,
    approved_design_rules: Annotated[
        bool,
        Field(description="True only after the user provides or explicitly approves the rule source."),
    ] = False,
    terminal_mapping_confirmed: Annotated[
        bool,
        Field(description="True only with explicit user-confirmed terminal assignment records."),
    ] = False,
    measurement_bias_confirmed: Annotated[
        bool,
        Field(description="True only after the user confirms the electrical bias envelope."),
    ] = False,
    routing_obstacles_confirmed: Annotated[
        bool,
        Field(description="True only after the user confirms routing obstacles and keepouts."),
    ] = False,
    dimension_semantics: str | None = None,
    dimension_semantics_by_family: Annotated[
        dict[str, str] | None,
        Field(description="Explicit W/L semantics token keyed by each requested family."),
    ] = None,
    terminal_assignments: Annotated[
        list[dict[str, Any]] | None,
        Field(description="Explicit {dut, family, terminal, net, pad} records for Pad budgeting."),
    ] = None,
    reserved_pad_indices: Annotated[
        list[int] | None,
        Field(description="Pads intentionally unavailable to DUT measurement terminals."),
    ] = None,
    dut_terminal_contracts: Annotated[
        list[dict[str, Any]] | None,
        Field(description="Per-DUT {dut, family, measurement, required_terminals} contracts."),
    ] = None,
    routing_connections: Annotated[
        list[dict[str, Any]] | None,
        Field(
            description=(
                "Centerline-feasibility records with net/start_um/end_um/width_um/clear_space_um; "
                "not compiled mesh geometry."
            )
        ),
    ] = None,
    routing_obstacles_um: Annotated[
        list[list[float]] | None,
        Field(description="Confirmed first-metal keepout boxes [x1,y1,x2,y2] in microns."),
    ] = None,
    routing_boundary_um: Annotated[
        list[float] | None,
        Field(description="Optional route boundary; defaults to the declared TEG frame."),
    ] = None,
) -> McpToolResult:
    """Gate incomplete TEG requests; return questions and stop instead of inferring or drawing."""

    try:
        return plan_teg_measurement_request(
            device_families=device_families,
            process_profile=process_profile,
            process_profile_version=process_profile_version,
            frame_width_um=frame_width_um,
            frame_height_um=frame_height_um,
            pad_count=pad_count,
            pad_rows=pad_rows,
            pad_width_um=pad_width_um,
            pad_height_um=pad_height_um,
            dut_count=dut_count,
            measurement_mode=measurement_mode,
            prefer_first_metal=prefer_first_metal,
            allow_additional_metals_if_unavoidable=allow_additional_metals_if_unavoidable,
            approved_layermap=approved_layermap,
            approved_design_rules=approved_design_rules,
            terminal_mapping_confirmed=terminal_mapping_confirmed,
            measurement_bias_confirmed=measurement_bias_confirmed,
            routing_obstacles_confirmed=routing_obstacles_confirmed,
            dimension_semantics=dimension_semantics,
            dimension_semantics_by_family=dimension_semantics_by_family,
            terminal_assignments=terminal_assignments,
            reserved_pad_indices=reserved_pad_indices,
            dut_terminal_contracts=dut_terminal_contracts,
            routing_connections=routing_connections,
            routing_obstacles_um=routing_obstacles_um,
            routing_boundary_um=routing_boundary_um,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def plan_phase1_device_doe(
    process_profile: str,
    process_profile_version: str,
    family: str,
    device_type: str,
    measurement: str,
    required_terminals: list[str],
    supported_axes: list[str],
    baseline: dict[str, Any],
    sweeps: dict[str, list[Any]],
    process_profile_confirmed: bool = False,
    design_mode: str = "one_factor_at_a_time",
    replicates: int = 1,
    max_splits: int = 500,
) -> McpToolResult:
    """Expand a process-confirmed transistor/resistor/capacitor DOE without drawing."""

    try:
        return build_phase1_device_doe(
            process_profile=process_profile,
            process_profile_version=process_profile_version,
            process_profile_confirmed=process_profile_confirmed,
            family=family,
            device_type=device_type,
            measurement=measurement,
            required_terminals=required_terminals,
            supported_axes=supported_axes,
            baseline=baseline,
            sweeps=sweeps,
            design_mode=design_mode,
            replicates=replicates,
            max_splits=max_splits,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def describe_process_capability(
    profile_name: str,
) -> McpToolResult:
    """Report that target process profiles must be supplied through onboarding."""

    try:
        return describe_builtin_process_capability(profile_name)
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def describe_pdk_profile_inputs() -> McpToolResult:
    """List process-neutral PDK inputs and separate them from TEG/project choices."""

    return pdk_profile_input_contract()


@protocol_tool(mcp, annotations=READ_ONLY)
def describe_organization_preset(
    preset_path: str | None = None,
) -> McpToolResult:
    """Load fixed company terminal/measurement conventions, independent of the PDK."""

    try:
        return load_organization_preset(preset_path)
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def validate_process_capability_profile(
    profile: dict[str, Any],
) -> McpToolResult:
    """Validate an explicit external process capability profile without drawing."""

    try:
        return validate_process_capability(profile)
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def plan_metal_resistor_primitive(
    process_capability: dict[str, Any],
    device_name: str,
    layer_role: str,
    measurement: str,
    width_um: float,
    length_um: float,
    terminal_size_um: float,
    dimension_semantics: str,
) -> McpToolResult:
    """Plan a process-gated horizontal 2T or Kelvin-4T metal resistor DUT."""

    try:
        return build_metal_resistor_primitive(
            process_capability=process_capability,
            device_name=device_name,
            layer_role=layer_role,
            measurement=measurement,
            width_um=width_um,
            length_um=length_um,
            terminal_size_um=terminal_size_um,
            dimension_semantics=dimension_semantics,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def plan_mom_capacitor_primitive(
    process_capability: dict[str, Any],
    device_name: str,
    layer_role: str,
    finger_width_um: float,
    finger_space_um: float,
    finger_length_um: float,
    finger_count: int,
    bus_width_um: float,
) -> McpToolResult:
    """Plan a process-gated single-metal interdigitated MOM capacitor DUT."""

    try:
        return build_mom_capacitor_primitive(
            process_capability=process_capability,
            device_name=device_name,
            layer_role=layer_role,
            finger_width_um=finger_width_um,
            finger_space_um=finger_space_um,
            finger_length_um=finger_length_um,
            finger_count=finger_count,
            bus_width_um=bus_width_um,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def plan_phase1_terminal_routes(
    process_capability: dict[str, Any],
    primitive_instances: list[dict[str, Any]],
    terminal_assignments: list[dict[str, Any]],
    route_specs: list[dict[str, Any]],
    frame_width_um: float = 2000.0,
    frame_height_um: float = 54.0,
    pad_count: int = 25,
    pad_width_um: float = 40.0,
    pad_height_um: float = 40.0,
    extra_obstacles_um: list[list[float]] | None = None,
) -> McpToolResult:
    """Plan centerline feasibility to synthetic Pad centers before M1 search.

    Pad centers/boxes come from frame and count parameters. This tool does not
    inspect or preserve pad GDS/OAS and does not compile mesh geometry.
    """

    try:
        return build_phase1_terminal_routes(
            process_capability=process_capability,
            primitive_instances=primitive_instances,
            terminal_assignments=terminal_assignments,
            route_specs=route_specs,
            frame_width_um=frame_width_um,
            frame_height_um=frame_height_um,
            pad_count=pad_count,
            pad_width_um=pad_width_um,
            pad_height_um=pad_height_um,
            extra_obstacles_um=extra_obstacles_um or (),
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def guide_phase1_direct_workflow(
    process_capability: dict[str, Any] | None = None,
    intake_plan: dict[str, Any] | None = None,
    doe_plan: dict[str, Any] | None = None,
    doe_required: bool = False,
    primitive_instances: list[dict[str, Any]] | None = None,
    route_plan: dict[str, Any] | None = None,
    final_request_plan: dict[str, Any] | None = None,
    layout_plan: dict[str, Any] | None = None,
    generation_result: dict[str, Any] | None = None,
) -> McpToolResult:
    """Guide nonproduction Phase 1 handoffs and return one next tool/action.

    Stock transistor requests intentionally stop at primitive geometry without
    a process adapter; later stages synthesize Pads and use centerline routes.
    """

    try:
        return build_phase1_workflow_guide(
            process_capability=process_capability,
            intake_plan=intake_plan,
            doe_plan=doe_plan,
            doe_required=doe_required,
            primitive_instances=primitive_instances,
            route_plan=route_plan,
            final_request_plan=final_request_plan,
            layout_plan=layout_plan,
            generation_result=generation_result,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def plan_phase1_direct_teg_layout(
    output_layout_path: str,
    top_cell: str,
    process_capability: dict[str, Any],
    request_plan: dict[str, Any],
    primitive_instances: list[dict[str, Any]],
    pad_rail_width_um: float,
) -> McpToolResult:
    """Compose a nonproduction synthetic Phase 1 layout before writing.

    The composer creates first-metal PAD_MESH cells, accepts injected verified
    primitives, and compiles bounded route polylines into multi-rail meshes. It does not
    import/preserve a pad macro in this legacy Phase 1 entrypoint.
    """

    try:
        return compose_phase1_direct_layout(
            output_layout_path=output_layout_path,
            top_cell=top_cell,
            process_capability=process_capability,
            request_plan=request_plan,
            primitive_instances=primitive_instances,
            pad_rail_width_um=pad_rail_width_um,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def generate_phase1_direct_teg(
    output_layout_path: str,
    top_cell: str,
    process_capability: dict[str, Any],
    request_plan: dict[str, Any],
    primitive_instances: list[dict[str, Any]],
    pad_rail_width_um: float,
    confirm_nonproduction: Annotated[
        bool,
        Field(
            description=(
                "Acknowledges synthetic Pads, multi-rail DUT-to-Pad mesh routes, and the "
                "absence of a stock transistor adapter/real padset/mesh E2E."
            )
        ),
    ] = False,
    klayout_executable: str | None = None,
    timeout_seconds: float = 120.0,
) -> McpToolResult:
    """Generate/fresh-reload the nonproduction synthetic Phase 1 scaffold.

    This is not a real transistor, preserved padset, or long-route mesh TEG.
    """

    try:
        return generate_phase1_direct_teg_service(
            output_layout_path=output_layout_path,
            top_cell=top_cell,
            process_capability=process_capability,
            request_plan=request_plan,
            primitive_instances=primitive_instances,
            pad_rail_width_um=pad_rail_width_um,
            confirm_nonproduction=confirm_nonproduction,
            klayout_executable=klayout_executable,
            timeout_seconds=timeout_seconds,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def draw_manhattan_layout(
    output_layout_path: Annotated[
        str,
        Field(
            description=(
                "New .gds or .oas path. Existing targets are preserved. Concurrent local writers "
                "to the same target use create-only publication: exactly one wins and losers return "
                "OUTPUT_ALREADY_EXISTS without deleting or replacing the winner."
            )
        ),
    ],
    dbu_um: Annotated[
        float,
        Field(description="Microns per database unit; every coordinate must lie exactly on this grid."),
    ],
    top_cell: Annotated[str, Field(description="Declared single top-cell name.")],
    cells: Annotated[
        list[str],
        Field(description="Unique top and reusable child cell names."),
    ],
    layers: Annotated[
        list[dict[str, Any]],
        Field(description="Explicit unique {name, layer, datatype} records."),
    ],
    operations: Annotated[
        list[dict[str, Any]],
        Field(
            description=(
                "Ordered add_box, add_text, add_instance, and boolean operations. "
                "Coordinates use microns; rotations are restricted to 0/90/180/270 degrees."
            )
        ),
    ],
    reference_selection_ids: Annotated[
        list[str],
        Field(
            description=(
                "Optional exact user-confirmed reference selections consulted for this drawing. "
                "Each selection is revalidated and cited in the result."
            )
        ),
    ] = [],
    reference_library_root: str | None = None,
    confirm_nonproduction: Annotated[
        bool,
        Field(description="Acknowledges that no foundry DRC/LVS sign-off is implied."),
    ] = False,
    klayout_executable: str | None = None,
    timeout_seconds: float = 60.0,
) -> McpToolResult:
    """Create one deterministic Manhattan layout with local no-clobber publication."""

    try:
        reference_root = reference_library_root or str(default_reference_library_root())
        reference_citations = []
        seen_reference_selections: set[str] = set()
        for selection_id in reference_selection_ids:
            if selection_id in seen_reference_selections:
                raise AnalysisError(
                    code="DUPLICATE_REFERENCE_SELECTION",
                    message="A drawing request cannot cite the same reference selection twice.",
                    details={"selection_id": selection_id},
                    next_action="Deduplicate reference_selection_ids while preserving the intended concern bindings.",
                )
            seen_reference_selections.add(selection_id)
            consulted = ReferenceLibrary(reference_root).consult(selection_id=selection_id)
            reference_citations.append(
                {
                    "selection_id": selection_id,
                    "reference_id": consulted["reference_id"],
                    "concern": consulted["concern"],
                    "usage_mode": consulted["usage_mode"],
                    "citation": consulted["reference_citation"],
                }
            )
        return draw_manhattan_layout_service(
            output_layout_path=output_layout_path,
            dbu_um=dbu_um,
            top_cell=top_cell,
            cells=cells,
            layers=layers,
            operations=operations,
            confirm_nonproduction=confirm_nonproduction,
            reference_citations=reference_citations,
            klayout_executable=klayout_executable,
            timeout_seconds=timeout_seconds,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def plan_kelvin_m1_routing(
    dimension_semantics: Annotated[
        str | None,
        Field(
            description=(
                "Explicitly confirmed W/L meaning; Kelvin requires transverse width and "
                "longitudinal length."
            )
        ),
    ] = None,
    confirm_routing_contract: Annotated[
        bool,
        Field(
            description=(
                "Confirms horizontal measured line, S+/F+/F-/S- Pad roles, direct force "
                "routing, straight-up sense routing, and one-sided 1/2/4/6 mesh."
            )
        ),
    ] = False,
    splits: Annotated[
        list[dict[str, int]] | None,
        Field(
            description=(
                "Optional ordered six records with width_nm and length_nm. The set must be "
                "a complete Cartesian product of three distinct widths and two lengths."
            )
        ),
    ] = None,
    site_origins_um: Annotated[
        list[list[float]] | None,
        Field(description="Optional ordered six [x_um,y_um] SLN001 DUT origins."),
    ] = None,
) -> McpToolResult:
    """Plan the deterministic Kelvin routing profile without opening KLayout."""

    try:
        return plan_kelvin_m1_routing_service(
            dimension_semantics=dimension_semantics,
            confirm_routing_contract=confirm_routing_contract,
            splits=splits,
            site_origins_um=site_origins_um,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def generate_kelvin_m1_teg(
    template_gds_path: Annotated[
        str,
        Field(
            description=(
                "Read-only SLN001 padset GDS/OAS. A golden Kelvin GDS may be supplied; "
                "existing KELVIN_* top instances are stripped before deterministic rebuild."
            )
        ),
    ],
    output_gds_path: Annotated[
        str,
        Field(description="New provisional GDS path located below work_directory_path."),
    ],
    work_directory_path: Annotated[
        str,
        Field(
            description=(
                "Project output subdirectory used for snapshots, worker exchange files, "
                "unverified GDS, and the provisional result."
            )
        ),
    ],
    dimension_semantics: Annotated[
        str | None,
        Field(description="Explicitly confirmed transverse-width/longitudinal-length token."),
    ] = None,
    confirm_routing_contract: bool = False,
    splits: list[dict[str, int]] | None = None,
    site_origins_um: list[list[float]] | None = None,
    reference_gds_path: Annotated[
        str | None,
        Field(
            description=(
                "Optional golden GDS/OAS for fresh-reload semantic comparison before output promotion."
            )
        ),
    ] = None,
    reference_top_cell: str | None = None,
    top_cell: str | None = None,
    require_reference_equivalence: Annotated[
        bool,
        Field(
            description=(
                "When a reference is supplied, reject and remove the provisional output unless "
                "recursive geometry, text, bbox, layers, and M1 topology are equivalent."
            )
        ),
    ] = True,
    klayout_executable: str | None = None,
    timeout_seconds: float = 60.0,
) -> McpToolResult:
    """Rebuild the SLN001 six-split Kelvin M1 TEG using controlled routing rules."""

    try:
        return generate_kelvin_m1_teg_service(
            template_gds_path=template_gds_path,
            output_gds_path=output_gds_path,
            work_directory_path=work_directory_path,
            dimension_semantics=dimension_semantics,
            confirm_routing_contract=confirm_routing_contract,
            splits=splits,
            site_origins_um=site_origins_um,
            reference_gds_path=reference_gds_path,
            reference_top_cell=reference_top_cell,
            top_cell=top_cell,
            require_reference_equivalence=require_reference_equivalence,
            klayout_executable=klayout_executable,
            timeout_seconds=timeout_seconds,
            snapshot_factory=create_layout_snapshot,
            worker_runner=run_klayout_worker,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def compare_kelvin_layouts(
    candidate_gds_path: str,
    reference_gds_path: str,
    work_directory_path: Annotated[
        str,
        Field(description="Project output subdirectory for disposable comparison exchange files."),
    ],
    candidate_top_cell: str | None = None,
    reference_top_cell: str | None = None,
    m1_layer: int = 15,
    m1_datatype: int = 0,
    klayout_executable: str | None = None,
    timeout_seconds: float = 60.0,
) -> McpToolResult:
    """Compare fresh-loaded layouts by recursive geometry and Kelvin M1 topology."""

    try:
        return compare_kelvin_layouts_service(
            candidate_gds_path=candidate_gds_path,
            reference_gds_path=reference_gds_path,
            work_directory_path=work_directory_path,
            candidate_top_cell=candidate_top_cell,
            reference_top_cell=reference_top_cell,
            m1_layer=m1_layer,
            m1_datatype=m1_datatype,
            klayout_executable=klayout_executable,
            timeout_seconds=timeout_seconds,
            snapshot_factory=create_layout_snapshot,
            worker_runner=run_klayout_worker,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def export_pcell_code(
    layermap_path: Annotated[
        str,
        Field(
            description=(
                "Required layermap with explicit m1, active, poly, and contact layers."
            )
        ),
    ],
    dimension_semantics: Annotated[
        str | None,
        Field(
            description=(
                "User-confirmed W/L meaning. Use device_specific_w_l for this transistor "
                "PCell only after explicit confirmation; never infer it."
            )
        ),
    ] = None,
    output_script_path: Annotated[
        str | None,
        Field(description="Optional file path to save the generated Python PCell script."),
    ] = None,
    confirm_conceptual_export: Annotated[
        bool,
        Field(
            description=(
                "Must be true to acknowledge that the generated PCell is conceptual, "
                "non-production, and electrically unverified."
            )
        ),
    ] = False,
) -> McpToolResult:
    """Generate explicitly acknowledged conceptual Python PCell code for KLayout."""

    try:
        if not confirm_conceptual_export:
            raise AnalysisError(
                code="CONCEPTUAL_PCELL_EXPORT_REQUIRES_OPT_IN",
                message=(
                    "PCell source uses synthetic process geometry and is not a production "
                    "device."
                ),
                details={"production_ready": False},
                next_action=(
                    "Set confirm_conceptual_export=true only for disposable GUI and "
                    "automation testing."
                ),
            )
        confirmed_semantics = confirm_dimension_semantics(dimension_semantics)
        if confirmed_semantics != DEVICE_SPECIFIC_W_L:
            raise AnalysisError(
                code="PCELL_DIMENSION_SEMANTICS_MISMATCH",
                message="The exported transistor PCell uses device-specific electrical W/L.",
                details={"dimension_semantics": confirmed_semantics},
                next_action=(
                    "Confirm device-specific transistor W/L with the user, or use a separate "
                    "resistor geometry tool for transverse width and longitudinal length."
                ),
            )
        layermap_data = load_layermap(layermap_path)
        source_code = generate_pcell_python_source(layermap_data)

        if output_script_path:
            out_path = os.path.abspath(output_script_path)
            out_dir = os.path.dirname(out_path)
            try:
                if out_dir:
                    os.makedirs(out_dir, exist_ok=True)
                with open(out_path, "x", encoding="utf-8") as handle:
                    handle.write(source_code)
            except FileExistsError:
                raise AnalysisError(
                    code="OUTPUT_EXISTS",
                    message="PCell script output already exists.",
                    details={"output_script_path": out_path},
                    next_action=(
                        "Provide a new output_script_path. Existing files are not overwritten."
                    ),
                ) from None
            except OSError as exc:
                raise AnalysisError(
                    code="OUTPUT_WRITE_FAILED",
                    message="PCell script output could not be written.",
                    details={
                        "output_script_path": out_path,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                    next_action=(
                        "Provide a writable output path in an accessible directory."
                    ),
                ) from exc
            return {
                "ok": True,
                "production_ready": False,
                "geometry_status": "conceptual_scaffold",
                "electrical_connectivity_verified": False,
                "layout_contract": layout_contract_status(
                    dimension_semantics=confirmed_semantics
                ),
                "output_script_path": out_path,
                "code_length": len(source_code),
                "source_code": source_code,
                "warning": (
                    "Synthetic process geometry is for GUI and automation testing only. "
                    "Do not use this PCell as a fabrication mask."
                ),
            }

        return {
            "ok": True,
            "production_ready": False,
            "geometry_status": "conceptual_scaffold",
            "electrical_connectivity_verified": False,
            "layout_contract": layout_contract_status(
                dimension_semantics=confirmed_semantics
            ),
            "code_length": len(source_code),
            "source_code": source_code,
            "warning": (
                "Synthetic process geometry is for GUI and automation testing only. "
                "Do not use this PCell as a fabrication mask."
            ),
        }
    except AnalysisError as exc:
        return exc.to_result()



@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def assemble_teg(
    padset_path: Annotated[
        str,
        Field(description="Existing padset GDS/OAS path. The input file is read-only."),
    ],
    layermap_path: Annotated[
        str,
        Field(description="YAML/JSON layermap path containing layer definitions."),
    ],
    output_gds_path: Annotated[
        str,
        Field(description="Path where the final assembled TEG GDS will be written."),
    ],
    dut_sweep: Annotated[
        list[dict[str, Any]],
        Field(
            description=(
                "Sweep list containing either 1 common config or one config per selected "
                "DUT site. W/L meaning is not inferred and requires confirmation."
            )
        ),
    ],
    dut_site_indices: Annotated[
        list[int] | None,
        Field(
            description=(
                "Optional occupied DUT sites chosen from 1..21. The Pad count remains fixed "
                "at 25. Omit to assemble all 21 available sites."
            )
        ),
    ] = None,
    dimension_semantics: Annotated[
        str | None,
        Field(
            description=(
                "Exact user-confirmed W/L meaning: "
                "width_is_transverse_axis_length_is_longitudinal_axis "
                "or device_specific_w_l. Ask first; do not infer or auto-swap."
            )
        ),
    ] = None,
    teg_name: str = "TEG_DUT_ARRAY_V1",
    export_static: bool = True,
    confirm_conceptual_export: Annotated[
        bool,
        Field(
            description=(
                "Must be true to acknowledge that synthetic process geometry is "
                "non-production and electrically unverified."
            )
        ),
    ] = False,
    klayout_executable: str | None = None,
    timeout_seconds: float = 60.0,
) -> McpToolResult:
    """Export conceptual DUT sites on a fixed 25-Pad nonproduction scaffold.

    This conceptual assembly is not a fallback for the missing Phase 1 process adapter.
    """

    try:
        return assemble_teg_service(
            padset_path=padset_path,
            layermap_path=layermap_path,
            output_gds_path=output_gds_path,
            dut_sweep=dut_sweep,
            dut_site_indices=dut_site_indices,
            dimension_semantics=dimension_semantics,
            teg_name=teg_name,
            export_static=export_static,
            confirm_conceptual_export=confirm_conceptual_export,
            klayout_executable=klayout_executable,
            timeout_seconds=timeout_seconds,
            snapshot_factory=create_layout_snapshot,
            worker_runner=run_klayout_worker,
        )
    except AnalysisError as exc:
        return exc.to_result()



@protocol_tool(mcp, annotations=READ_ONLY)
def plan_teg_dut_sequence(
    dut_slots: Annotated[
        list[dict[str, Any]],
        Field(description="All 21 candidate DUT slots returned by fixed 25-Pad analysis."),
    ],
    site_parameter_sets: Annotated[
        list[dict[str, Any]],
        Field(
            description=(
                "Between 1 and 21 entries shaped as {site: N, parameters: {...}}. "
                "Each selected site number must appear once. W/L meaning requires explicit "
                "dimension_semantics confirmation."
            )
        ),
    ],
    dimension_semantics: Annotated[
        str | None,
        Field(
            description=(
                "Exact user-confirmed W/L meaning. Ask the user before setting this value."
            )
        ),
    ] = None,
    defaults: Annotated[
        dict[str, Any] | None,
        Field(description="Optional common DUT parameters applied before site overrides."),
    ] = None,
) -> McpToolResult:
    """Plan selected DUT sites over the fixed 25-Pad/21-candidate-site profile."""

    try:
        confirmed_semantics = confirm_dimension_semantics(dimension_semantics)
        result = plan_dut_sequence(dut_slots, site_parameter_sets, defaults)
        result["layout_contract"] = layout_contract_status(
            dimension_semantics=confirmed_semantics
        )
        return result
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def verify_design_rules(
    dut_geometry: Annotated[
        dict[str, object],
        Field(description="Generated DUT geometry dictionary containing shape boxes and terminals."),
    ],
    min_m1_width_um: float = 0.28,
    min_m1_space_um: float = 0.28,
    min_landing_overlap_um: float = 0.1,
    min_poly_width_um: float = 0.08,
    min_contact_size_um: float = 0.18,
    comparison_tolerance_um: float = 1e-9,
) -> McpToolResult:
    """Verify Key Design Rules (width, space, overlap, size) on DUT geometry."""

    try:
        m1_shapes = dut_geometry.get("m1_shapes_um")
        if isinstance(m1_shapes, list):
            validate_orthogonal_m1_shapes(m1_shapes)
        rules = DesignRuleConfig(
            min_m1_width_um=min_m1_width_um,
            min_m1_space_um=min_m1_space_um,
            min_landing_overlap_um=min_landing_overlap_um,
            min_poly_width_um=min_poly_width_um,
            min_contact_size_um=min_contact_size_um,
            comparison_tolerance_um=comparison_tolerance_um,
        )
        return verify_dut_design_rules(dut_geometry, rules)
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def describe_dut_pcell() -> McpToolResult:
    """Return the conceptual DUT scaffold contract, not a process PCell adapter."""

    return describe_dut_pcell_contract()



@protocol_tool(mcp, annotations=READ_ONLY)
def generate_dut_geometry(
    w_um: Annotated[
        float,
        Field(
            description=(
                "Width-like value in microns. Its axis/device meaning is undefined until "
                "the user explicitly confirms dimension_semantics."
            )
        ),
    ] = 1.0,
    l_um: Annotated[
        float,
        Field(
            description=(
                "Length-like value in microns. Its axis/device meaning is undefined until "
                "the user explicitly confirms dimension_semantics."
            )
        ),
    ] = 0.1,
    array_rows: int = 4,
    array_cols: int = 8,
    pitch_x_um: float = 2.0,
    pitch_y_um: float = 2.0,
    routed_device_count: int = 10,
    m1_width_um: float = 0.4,
    m1_overlap_um: float = 0.2,
    device_window_um: Annotated[
        list[float] | None,
        Field(description="DUT-local device window [x1,y1,x2,y2] in microns."),
    ] = None,
    routing_boundary_um: Annotated[
        list[float] | None,
        Field(description="DUT-local routing boundary [x1,y1,x2,y2] in microns."),
    ] = None,
    dimension_semantics: Annotated[
        str | None,
        Field(
            description=(
                "Exact user-confirmed W/L meaning. For generic/resistor geometry use "
                "width_is_transverse_axis_length_is_longitudinal_axis; ask first and "
                "never infer. This defines directions and imposes no numeric ordering."
            )
        ),
    ] = None,
) -> McpToolResult:
    """Generate a synthetic nonproduction DUT scaffold and terminal stubs.

    Synthetic contact/device dimensions are for contract and UI testing only;
    this result cannot satisfy the Phase 1 transistor-adapter requirement.
    """

    try:
        confirmed_semantics = confirm_dimension_semantics(dimension_semantics)
        dev_win = (
            Box.from_sequence(device_window_um)
            if device_window_um is not None
            else Box(-17.5, -20.0, 17.5, 20.0)
        )
        rout_bound = (
            Box.from_sequence(routing_boundary_um)
            if routing_boundary_um is not None
            else Box(-20.0, -20.0, 20.0, 20.0)
        )
        params = DutParameters(
            w_um=w_um,
            l_um=l_um,
            array_rows=array_rows,
            array_cols=array_cols,
            pitch_x_um=pitch_x_um,
            pitch_y_um=pitch_y_um,
            routed_device_count=routed_device_count,
            m1_width_um=m1_width_um,
            m1_overlap_um=m1_overlap_um,
            device_window_um=dev_win,
            routing_boundary_um=rout_bound,
        )
        result = build_dut_geometry(params)
        payload = result.to_dict()
        payload["layout_contract"] = layout_contract_status(
            dimension_semantics=confirmed_semantics
        )
        return payload
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def compare_layouts(
    candidate_layout_path: Annotated[
        str,
        Field(
            description=(
                "Existing candidate GDS/OAS path. Pass it directly without a shell/file-search "
                "preflight; relative paths resolve from the MCP process working directory."
            )
        ),
    ],
    reference_layout_path: Annotated[
        str,
        Field(
            description=(
                "Existing reference GDS/OAS path. Pass it directly without a shell/file-search "
                "preflight; relative paths resolve from the MCP process working directory."
            )
        ),
    ],
    candidate_top_cell: str | None = None,
    reference_top_cell: str | None = None,
    klayout_executable: str | None = None,
    timeout_seconds: float = 60.0,
) -> McpToolResult:
    """Compare two layouts by recursive geometry, text, bbox, DBU, and layer set."""

    try:
        return compare_layouts_service(
            candidate_layout_path=candidate_layout_path,
            reference_layout_path=reference_layout_path,
            candidate_top_cell=candidate_top_cell,
            reference_top_cell=reference_top_cell,
            klayout_executable=klayout_executable,
            timeout_seconds=timeout_seconds,
            snapshot_factory=create_layout_snapshot,
            worker_runner=run_klayout_worker,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def register_reference_layout(
    layout_path: Annotated[
        str,
        Field(description="User-provided GDS/OAS reference. The original is never modified."),
    ],
    process_node: Annotated[
        str,
        Field(description="Exact organization process-node token, for example LN14LPU."),
    ],
    process_option: str = "default",
    process_revision: str = "unspecified",
    top_cell: str | None = None,
    layermap_path: Annotated[
        str | None,
        Field(description="Optional explicit layermap used only for layer-role labels."),
    ] = None,
    profile_name: str | None = None,
    profile_version: str | None = None,
    purpose_tags: list[str] = [],
    description: str | None = None,
    library_root: Annotated[
        str | None,
        Field(description="Optional local reference-library root. Omit for the host default."),
    ] = None,
    klayout_executable: str | None = None,
    timeout_seconds: float = 60.0,
) -> McpToolResult:
    """Store a stable full reference GDS by process node and content hash."""

    try:
        return register_reference_layout_service(
            layout_path=layout_path,
            process_node=process_node,
            process_option=process_option,
            process_revision=process_revision,
            top_cell=top_cell,
            layermap_path=layermap_path,
            profile_name=profile_name,
            profile_version=profile_version,
            purpose_tags=purpose_tags,
            description=description,
            library_root=library_root,
            klayout_executable=klayout_executable,
            timeout_seconds=timeout_seconds,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def list_reference_layouts(
    process_node: str | None = None,
    library_root: str | None = None,
) -> McpToolResult:
    """List immutable reference GDS assets, optionally for one process node."""

    try:
        root = library_root or str(default_reference_library_root())
        return ReferenceLibrary(root).list_assets(process_node=process_node)
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def prepare_reference_view(
    reference_id: str,
    concern: Annotated[
        str,
        Field(description="Concern such as transistor_context, contact_array, routing_mesh, or pad_joint."),
    ],
    usage_mode: Annotated[
        str,
        Field(description="normal_style or reference_precedent; only the latter may accept matching DRC markers."),
    ],
    roi_bbox_um: Annotated[list[float], Field(description="Cell-view ROI [x1,y1,x2,y2] in microns.")],
    relevant_layers: Annotated[
        list[str],
        Field(description="Confirmed semantic roles or layer/datatype tokens to show in KLayout."),
    ],
    view_bbox_um: Annotated[
        list[float] | None,
        Field(description="For a nested occurrence, the same ROI transformed into top-view coordinates."),
    ] = None,
    occurrence_segments: list[dict[str, Any]] = [],
    device_family: str | None = None,
    terminal_role: str | None = None,
    style_descriptors: list[dict[str, Any]] = [],
    accepted_marker_templates: list[dict[str, Any]] = [],
    severity_policy: str = "same_or_less_severe",
    library_root: str | None = None,
) -> McpToolResult:
    """Create an immutable KLayout inspection request; this does not confirm it."""

    try:
        root = library_root or str(default_reference_library_root())
        return ReferenceLibrary(root).prepare_view(
            reference_id=reference_id,
            concern=concern,
            usage_mode=usage_mode,
            roi_bbox_um=roi_bbox_um,
            view_bbox_um=view_bbox_um,
            relevant_layers=relevant_layers,
            occurrence_segments=occurrence_segments,
            device_family=device_family,
            terminal_role=terminal_role,
            style_descriptors=style_descriptors,
            accepted_marker_templates=accepted_marker_templates,
            severity_policy=severity_policy,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def confirm_reference_view(
    view_id: str,
    library_root: str | None = None,
) -> McpToolResult:
    """Import a confirmation created by the user's KLayout Reference Navigator click."""

    try:
        root = library_root or str(default_reference_library_root())
        return ReferenceLibrary(root).confirm_view(view_id=view_id)
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def consult_reference_selection(
    selection_id: str,
    library_root: str | None = None,
) -> McpToolResult:
    """Return only one exact user-confirmed reference selection and its citation."""

    try:
        root = library_root or str(default_reference_library_root())
        return ReferenceLibrary(root).consult(selection_id=selection_id)
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def classify_reference_drc_markers(
    selection_id: str,
    candidate_markers: Annotated[
        list[dict[str, Any]],
        Field(description="Normalized candidate DRC markers with deterministic local context signatures."),
    ],
    deviation_tolerance_um: float = 0.0,
    library_root: str | None = None,
) -> McpToolResult:
    """Advisably classify markers as REF_ACCEPTED or REVIEW_NEEDED; never block drawing."""

    try:
        root = library_root or str(default_reference_library_root())
        return ReferenceLibrary(root).classify_markers(
            selection_id=selection_id,
            candidate_markers=candidate_markers,
            deviation_tolerance_um=deviation_tolerance_um,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def inspect_layout(
    layout_path: Annotated[
        str,
        Field(
            description=(
                "Existing GDS/OAS path. Pass it directly without a shell/file-search preflight; "
                "relative paths resolve from the MCP process working directory. The input is "
                "snapshotted, validated, and never modified."
            )
        ),
    ],
    top_cell: str | None = None,
    layermap_path: Annotated[
        str | None,
        Field(description="Optional YAML/JSON layermap for explicit role labels only."),
    ] = None,
    text_limit: int = 200,
    klayout_executable: str | None = None,
    timeout_seconds: float = 60.0,
) -> McpToolResult:
    """Return a domain-neutral layout, hierarchy, layer, shape, and text inventory."""

    try:
        return inspect_layout_service(
            layout_path=layout_path,
            top_cell=top_cell,
            layermap_path=layermap_path,
            text_limit=text_limit,
            klayout_executable=klayout_executable,
            timeout_seconds=timeout_seconds,
            snapshot_factory=create_layout_snapshot,
            worker_runner=run_klayout_worker,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def extract_layout_style(
    layout_path: Annotated[
        str,
        Field(description="Existing GDS/OAS used read-only as the style observation source."),
    ],
    top_cell: str | None = None,
    layermap_path: Annotated[
        str | None,
        Field(
            description=(
                "Optional explicit layermap. Semantic roles are reported only from this file; "
                "they are never guessed from GDS colors or geometry."
            )
        ),
    ] = None,
    histogram_limit: Annotated[
        int,
        Field(description="Maximum most-frequent observed box dimensions retained per axis."),
    ] = 24,
    output_profile_path: Annotated[
        str | None,
        Field(
            description=(
                "Optional new .json path for the extracted content-hashed profile. Existing targets "
                "are never replaced; same-target concurrent writers preserve the first winner."
            )
        ),
    ] = None,
    klayout_executable: str | None = None,
    timeout_seconds: float = 120.0,
) -> McpToolResult:
    """Extract style observations and optionally publish a create-only JSON profile."""

    try:
        return extract_layout_style_service(
            layout_path=layout_path,
            top_cell=top_cell,
            layermap_path=layermap_path,
            histogram_limit=histogram_limit,
            output_profile_path=output_profile_path,
            klayout_executable=klayout_executable,
            timeout_seconds=timeout_seconds,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def inventory_pcellizer_hierarchy(
    layout_path: Annotated[
        str,
        Field(
            description=(
                "Existing source GDS/OAS. It is snapshotted and never modified or flattened."
            )
        ),
    ],
    top_cell: str | None = None,
    max_occurrences: Annotated[
        int,
        Field(
            description=(
                "Fail-closed upper bound for expanded physical occurrences, including array members."
            )
        ),
    ] = 100000,
    klayout_executable: str | None = None,
    timeout_seconds: float = 60.0,
) -> McpToolResult:
    """Return PCellizer H0 occurrence paths, transforms, arrays, and authoring gates."""

    try:
        return inventory_pcellizer_hierarchy_service(
            layout_path=layout_path,
            top_cell=top_cell,
            max_occurrences=max_occurrences,
            klayout_executable=klayout_executable,
            timeout_seconds=timeout_seconds,
            snapshot_factory=create_layout_snapshot,
            worker_runner=run_klayout_worker,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def create_pcellizer_snapshot(
    capture: Annotated[
        dict[str, Any],
        Field(description="Exact PCellizerParameterCapture produced by the KLayout dock."),
    ],
    package_root: Annotated[
        str,
        Field(description="Host directory in which to create a content-addressed snapshot store."),
    ],
    session_id: str | None = None,
    parent_revision_sha256: str | None = None,
) -> McpToolResult:
    """Embed source bytes and capture metadata without flattening or runtime source dependency."""

    try:
        return create_pcellizer_snapshot_package(
            capture=capture,
            package_root=package_root,
            session_id=session_id,
            parent_revision_sha256=parent_revision_sha256,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def inspect_pcellizer_snapshot(
    package_dir: Annotated[
        str,
        Field(description="Existing content-addressed PCellizer snapshot package directory."),
    ],
) -> McpToolResult:
    """Validate the immutable Dock-to-MCP handoff before asking parameter questions."""

    try:
        return inspect_pcellizer_snapshot_package(package_dir=package_dir)
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def recover_pcellizer_snapshot(
    package_dir: Annotated[
        str,
        Field(description="Existing content-addressed PCellizer snapshot package directory."),
    ],
    output_path: Annotated[
        str,
        Field(description="New GDS/OAS path for exact embedded-source recovery."),
    ],
) -> McpToolResult:
    """Recover standalone embedded layout bytes after validating every package hash."""

    try:
        return recover_pcellizer_snapshot_source(
            package_dir=package_dir, output_path=output_path
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def define_pcellizer_parameter(
    snapshot_package_sha256: Annotated[
        str, Field(description="Exact hash returned by inspect_pcellizer_snapshot.")
    ],
    parameter_name: Annotated[
        str,
        Field(
            description=(
                "Portable recipe parameter key. The current workflow does not emit a "
                "reusable KLayout PCell declaration or library."
            )
        ),
    ],
    min_um: Annotated[float, Field(description="Confirmed minimum value in microns.")],
    nominal_um: Annotated[
        float, Field(description="Captured nominal ruler span in microns.")
    ],
    max_um: Annotated[float, Field(description="Confirmed maximum value in microns.")],
    step_um: Annotated[
        float, Field(description="Positive DBU- and manufacturing-grid-aligned increment.")
    ],
    dbu_um: Annotated[
        float, Field(description="Source layout database unit in microns.")
    ],
    manufacturing_grid_um: Annotated[
        float, Field(description="Explicit process manufacturing grid in microns.")
    ],
    dimension_semantics: Annotated[
        str,
        Field(description="User-confirmed transverse_width or longitudinal_length meaning."),
    ],
    anchor_policy: Annotated[
        str,
        Field(description="Explicit p1_fixed, p2_fixed, or center_fixed anchor."),
    ],
    dependency_policy: Annotated[
        str,
        Field(description="P1 supports fixed_unselected_geometry only."),
    ] = "fixed_unselected_geometry",
) -> McpToolResult:
    """Bind explicit sweep intent to one immutable snapshot without inferring W/L meaning."""

    try:
        return build_pcellizer_intent(
            snapshot_package_sha256=snapshot_package_sha256,
            parameter_name=parameter_name,
            min_um=min_um,
            nominal_um=nominal_um,
            max_um=max_um,
            step_um=step_um,
            dbu_um=dbu_um,
            manufacturing_grid_um=manufacturing_grid_um,
            dimension_semantics=dimension_semantics,
            anchor_policy=anchor_policy,
            dependency_policy=dependency_policy,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def compile_pcellizer_recipe(
    package_dir: Annotated[
        str,
        Field(description="Verified standalone PCellizer snapshot package directory."),
    ],
    parameter_intent: Annotated[
        dict[str, Any],
        Field(description="Canonical result of define_pcellizer_parameter."),
    ],
) -> McpToolResult:
    """Compile a deterministic non-flattening recipe for one direct selected box."""

    try:
        return compile_single_shape_pcellizer_recipe(
            package_dir=package_dir, parameter_intent=parameter_intent
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def plan_pcellizer_split_table(
    recipe: Annotated[
        dict[str, Any],
        Field(description="Canonical result of compile_pcellizer_recipe."),
    ],
    table_path: Annotated[
        str | None,
        Field(description="UTF-8 CSV/TSV/TXT path; mutually exclusive with table_text."),
    ] = None,
    table_text: Annotated[
        str | None,
        Field(description="CSV text or tab-delimited rows copied directly from Excel."),
    ] = None,
    max_rows: Annotated[
        int,
        Field(description="Fail-closed row limit from 1 to 10000."),
    ] = 1000,
) -> McpToolResult:
    """Validate one-to-many split rows without writing geometry."""

    try:
        return build_pcellizer_split_batch(
            recipe=recipe,
            table_path=table_path,
            table_text=table_text,
            max_rows=max_rows,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def generate_pcellizer_split_batch(
    package_dir: Annotated[
        str,
        Field(description="Verified standalone source snapshot package directory."),
    ],
    recipe: Annotated[
        dict[str, Any],
        Field(description="Snapshot-bound single-box PCellizer recipe."),
    ],
    batch_plan: Annotated[
        dict[str, Any],
        Field(description="Canonical result of plan_pcellizer_split_table."),
    ],
    output_root: Annotated[
        str,
        Field(description="Root under which a plan-addressed batch package is added."),
    ],
    klayout_executable: str | None = None,
    timeout_seconds: float = 180.0,
) -> McpToolResult:
    """Generate one static standalone GDS per row with fresh-reload verification.

    Verification covers file integrity and the requested direct-box dimension;
    it does not prove DRC/LVS/PEX or dependent-shape/device correctness.
    """

    try:
        return generate_pcellizer_split_batch_service(
            package_dir=package_dir,
            recipe=recipe,
            batch_plan=batch_plan,
            output_root=output_root,
            klayout_executable=klayout_executable,
            timeout_seconds=timeout_seconds,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def inspect_pcellizer_batch(
    batch_dir: Annotated[
        str,
        Field(description="Existing PCellizer batch package directory."),
    ],
) -> McpToolResult:
    """Rehash recipe, plan, manifest, and every generated GDS in a batch."""

    try:
        return inspect_pcellizer_batch_package(batch_dir=batch_dir)
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def plan_pcellizer_process_inputs(
    layermap_path: Annotated[
        str,
        Field(description="Explicit YAML/JSON semantic role to GDS layer/datatype mapping."),
    ],
    process_name: str | None = None,
    process_version: str | None = None,
    layout_dbu_um: float | None = None,
    manufacturing_grid_um: float | None = None,
    editable_layer_roles: list[str] | None = None,
    layer_rules: dict[str, dict[str, Any]] | None = None,
    modified_cut_layer_roles: list[str] | None = None,
    connectivity: list[dict[str, Any]] | None = None,
    enclosure_rules: dict[str, dict[str, Any]] | None = None,
) -> McpToolResult:
    """Load layer identity only and ask for rules/connectivity that cannot be inferred safely."""

    try:
        return build_pcellizer_process_inputs(
            layermap_path=layermap_path,
            process_name=process_name,
            process_version=process_version,
            layout_dbu_um=layout_dbu_um,
            manufacturing_grid_um=manufacturing_grid_um,
            editable_layer_roles=editable_layer_roles,
            layer_rules=layer_rules,
            modified_cut_layer_roles=modified_cut_layer_roles,
            connectivity=connectivity,
            enclosure_rules=enclosure_rules,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def inspect_sample_dut(
    sample_layout_path: Annotated[
        str,
        Field(description="Existing sample DUT GDS/OAS path. The input is read-only."),
    ],
    layermap_path: Annotated[
        str,
        Field(description="YAML/JSON layermap with explicit process layer roles."),
    ],
    sample_description: Annotated[
        str | None,
        Field(description="User explanation of device type, terminals, and sweep meaning."),
    ] = None,
    top_cell: str | None = None,
    klayout_executable: str | None = None,
    timeout_seconds: float = 60.0,
) -> McpToolResult:
    """Inventory a sample DUT before any PCell source or production geometry is generated."""

    try:
        return inspect_sample_dut_service(
            sample_layout_path=sample_layout_path,
            layermap_path=layermap_path,
            sample_description=sample_description,
            top_cell=top_cell,
            klayout_executable=klayout_executable,
            timeout_seconds=timeout_seconds,
            snapshot_factory=create_layout_snapshot,
            worker_runner=run_klayout_worker,
        )
    except AnalysisError as exc:
        return exc.to_result()



@protocol_tool(mcp, annotations=READ_ONLY)
def analyze_pad_boxes(
    boxes_um: Annotated[
        list[list[float]],
        Field(description="M1 pad candidate boxes as [x1,y1,x2,y2] in microns."),
    ],
    expected_pad_count: int = DEFAULT_TEG_PROFILE.expected_pad_count,
    source_drain_pad_count: int = DEFAULT_TEG_PROFILE.source_drain_pad_count,
    pad_size_um: float = DEFAULT_TEG_PROFILE.pad_width_um,
    pitch_um: float = DEFAULT_TEG_PROFILE.pitch_um,
    device_width_um: float = DEFAULT_TEG_PROFILE.device_width_um,
    device_height_um: float = DEFAULT_TEG_PROFILE.device_height_um,
    tolerance_um: float = DEFAULT_TEG_PROFILE.default_tolerance_um,
) -> McpToolResult:
    """Detect one horizontal pad row and derive Source/Drain DUT slot boundaries."""

    try:
        config = PadDetectionConfig(
            expected_pad_count=expected_pad_count,
            source_drain_pad_count=source_drain_pad_count,
            expected_pad_width_um=pad_size_um,
            expected_pad_height_um=pad_size_um,
            expected_pitch_um=pitch_um,
            size_tolerance_um=tolerance_um,
            alignment_tolerance_um=tolerance_um,
            pitch_tolerance_um=tolerance_um,
            device_width_um=device_width_um,
            device_height_um=device_height_um,
        )
        return analyze_boxes(boxes_um, config)
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def select_routed_units(
    unit_centers_um: Annotated[
        list[list[float]],
        Field(
            description=(
                "Transistor centers as [x_um,y_um] in stable array-index order. "
                "Returned indices are 1-based positions in this list."
            )
        ),
    ],
    device_window_um: Annotated[
        list[float],
        Field(description="DUT-local device window [x1,y1,x2,y2] in microns."),
    ],
    routed_device_count: Annotated[
        int,
        Field(description="Number of transistor units to connect for measurement."),
    ],
    edge_inset_um: Annotated[
        float,
        Field(description="Excluded distance from every device-window edge in microns."),
    ] = 5.0,
) -> McpToolResult:
    """Choose one reusable, deterministic routed-transistor index pattern."""

    try:
        return select_units(
            unit_centers_um,
            device_window_um,
            routed_device_count,
            edge_inset_um,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def plan_single_transistor_context(
    dut_window_um: list[float],
    device_footprint_um: list[float],
    pitch_x_um: float,
    measured_device_type: str,
    pitch_y_um: float | None = None,
    edge_margin_um: float = 0.0,
    fill_style: str | None = None,
    measured_device_count: int | None = None,
    measurement_edge_inset_um: float | None = None,
    standard_cell_height_um: float | None = None,
) -> McpToolResult:
    """Fill a DUT and choose balanced, inset transistor sites for measurement routing."""

    try:
        return build_single_transistor_context(
            dut_window_um=dut_window_um,
            device_footprint_um=device_footprint_um,
            pitch_x_um=pitch_x_um,
            pitch_y_um=pitch_y_um,
            measured_device_type=measured_device_type,
            edge_margin_um=edge_margin_um,
            fill_style=fill_style,
            measured_device_count=measured_device_count,
            measurement_edge_inset_um=measurement_edge_inset_um,
            standard_cell_height_um=standard_cell_height_um,
        )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=READ_ONLY)
def plan_transistor_array(
    array_rows: int,
    array_cols: int,
    pitch_x_um: float,
    pitch_y_um: float,
    routed_device_count: int,
    device_window_um: Annotated[
        list[float] | None,
        Field(description="DUT-local device window [x1,y1,x2,y2] in microns."),
    ] = None,
    edge_inset_um: float = 5.0,
) -> McpToolResult:
    """Plan one centered transistor grid and its reusable routed-unit pattern."""

    try:
        return plan_array(
            array_rows,
            array_cols,
            pitch_x_um,
            pitch_y_um,
            routed_device_count,
            (
                device_window_um
                if device_window_um is not None
                else [-17.5, -20.0, 17.5, 20.0]
            ),
            edge_inset_um,
        )
    except AnalysisError as exc:
        return exc.to_result()




@protocol_tool(mcp, annotations=READ_ONLY)
def analyze_padset(
    padset_path: Annotated[
        str,
        Field(description="Existing padset GDS/OAS path. The input file is read-only."),
    ],
    layermap_path: Annotated[
        str,
        Field(description="YAML/JSON layermap path containing an explicit layers.m1 entry."),
    ],
    top_cell: str | None = None,
    klayout_executable: str | None = None,
    expected_pad_count: int = DEFAULT_TEG_PROFILE.expected_pad_count,
    source_drain_pad_count: int = DEFAULT_TEG_PROFILE.source_drain_pad_count,
    pad_size_um: float = DEFAULT_TEG_PROFILE.pad_width_um,
    pitch_um: float = DEFAULT_TEG_PROFILE.pitch_um,
    device_width_um: float = DEFAULT_TEG_PROFILE.device_width_um,
    device_height_um: float = DEFAULT_TEG_PROFILE.device_height_um,
    landing_search_half_depth_um: Annotated[
        float,
        Field(description="Half-depth of each M1 boundary search band in microns."),
    ] = DEFAULT_TEG_PROFILE.default_landing_search_half_depth_um,
    tolerance_um: float = DEFAULT_TEG_PROFILE.default_tolerance_um,
    timeout_seconds: float = 60.0,
) -> McpToolResult:
    """Analyze one immutable copy of the padset input."""

    try:
        if timeout_seconds <= 0:
            raise AnalysisError(
                code="INVALID_TIMEOUT",
                message="timeout_seconds must be positive.",
                details={"timeout_seconds": timeout_seconds},
                next_action="Provide a timeout greater than zero seconds.",
            )
        if landing_search_half_depth_um <= 0:
            raise AnalysisError(
                code="INVALID_LANDING_SEARCH_DEPTH",
                message="landing_search_half_depth_um must be positive.",
                details={
                    "landing_search_half_depth_um": landing_search_half_depth_um,
                },
                next_action="Provide a positive M1 boundary search half-depth in microns.",
            )
        load_layermap(layermap_path)
        with create_layout_snapshot(padset_path) as snapshot:
            return _analyze_padset_snapshot(
                padset_path=str(snapshot.path),
                layermap_path=layermap_path,
                top_cell=top_cell,
                klayout_executable=klayout_executable,
                expected_pad_count=expected_pad_count,
                source_drain_pad_count=source_drain_pad_count,
                pad_size_um=pad_size_um,
                pitch_um=pitch_um,
                device_width_um=device_width_um,
                device_height_um=device_height_um,
                landing_search_half_depth_um=landing_search_half_depth_um,
                tolerance_um=tolerance_um,
                timeout_seconds=timeout_seconds,
                source_padset_path=str(snapshot.source_path),
                snapshot_sha256=snapshot.sha256,
                snapshot_size_bytes=snapshot.size_bytes,
                worker_runner=run_klayout_worker,
            )
    except AnalysisError as exc:
        return exc.to_result()


@protocol_tool(mcp, annotations=ADDITIVE_WRITE)
def render_boundary_overlay(
    padset_path: Annotated[
        str,
        Field(description="Existing padset GDS/OAS path. The input file is read-only."),
    ],
    layermap_path: Annotated[
        str,
        Field(description="YAML/JSON layermap path containing an explicit layers.m1 entry."),
    ],
    image_path: Annotated[
        str,
        Field(description="New PNG path for the debug-only boundary overlay."),
    ],
    top_cell: str | None = None,
    klayout_executable: str | None = None,
    expected_pad_count: int = DEFAULT_TEG_PROFILE.expected_pad_count,
    source_drain_pad_count: int = DEFAULT_TEG_PROFILE.source_drain_pad_count,
    pad_size_um: float = DEFAULT_TEG_PROFILE.pad_width_um,
    pitch_um: float = DEFAULT_TEG_PROFILE.pitch_um,
    device_width_um: float = DEFAULT_TEG_PROFILE.device_width_um,
    device_height_um: float = DEFAULT_TEG_PROFILE.device_height_um,
    landing_search_half_depth_um: float = (
        DEFAULT_TEG_PROFILE.default_landing_search_half_depth_um
    ),
    image_width: int = 1600,
    image_height: int = 600,
    tolerance_um: float = DEFAULT_TEG_PROFILE.default_tolerance_um,
    timeout_seconds: float = 60.0,
) -> McpToolResult:
    """Analyze a padset and render pads, slots, and landing states as view markers."""
    try:
        if timeout_seconds <= 0:
            raise AnalysisError(
                code="INVALID_TIMEOUT",
                message="timeout_seconds must be positive.",
                details={"timeout_seconds": timeout_seconds},
                next_action="Provide a timeout greater than zero seconds.",
            )
        if landing_search_half_depth_um <= 0:
            raise AnalysisError(
                code="INVALID_LANDING_SEARCH_DEPTH",
                message="landing_search_half_depth_um must be positive.",
                details={
                    "landing_search_half_depth_um": landing_search_half_depth_um,
                },
                next_action="Provide a positive M1 boundary search half-depth in microns.",
            )
        load_layermap(layermap_path)
        with create_layout_snapshot(padset_path) as snapshot:
            analysis = _analyze_padset_snapshot(
                padset_path=str(snapshot.path),
                layermap_path=layermap_path,
                top_cell=top_cell,
                klayout_executable=klayout_executable,
                expected_pad_count=expected_pad_count,
                source_drain_pad_count=source_drain_pad_count,
                pad_size_um=pad_size_um,
                pitch_um=pitch_um,
                device_width_um=device_width_um,
                device_height_um=device_height_um,
                landing_search_half_depth_um=landing_search_half_depth_um,
                tolerance_um=tolerance_um,
                timeout_seconds=timeout_seconds,
                source_padset_path=str(snapshot.source_path),
                snapshot_sha256=snapshot.sha256,
                snapshot_size_bytes=snapshot.size_bytes,
                image_path=image_path,
                image_width=image_width,
                image_height=image_height,
                worker_runner=run_klayout_worker,
            )
            if not analysis.get("ok"):
                return analysis
    except AnalysisError as exc:
        return exc.to_result()
    return {
        "ok": True,
        "padset": analysis["padset"],
        "pad_count": analysis["pad_count"],
        "dut_slot_count": analysis["dut_slot_count"],
        "layout_read_count": analysis["layout_read_count"],
        "unresolved_landings": analysis["m1_connectivity"]["unresolved_landings"],
        "overlay": analysis["overlay"],
    }


async def configure_tool_mode(mode: str) -> list[str]:
    """Narrow the public tool surface once, using only FastMCP public APIs."""

    global _active_tool_mode
    normalized = mode.strip().lower()
    if normalized not in _TOOL_MODE_ALLOWLISTS:
        raise ValueError(
            "KLAYOUT_MCP_TOOL_MODE must be one of: "
            + ", ".join(sorted(_TOOL_MODE_ALLOWLISTS))
        )
    if _active_tool_mode != "expert" and normalized != _active_tool_mode:
        raise RuntimeError(
            f"Tool mode is already narrowed to {_active_tool_mode!r}; restart the server "
            "process to select another mode."
        )
    allowlist = _TOOL_MODE_ALLOWLISTS[normalized]
    if allowlist is not None:
        registered = [tool.name for tool in await mcp.list_tools()]
        for tool_name in registered:
            if tool_name not in allowlist:
                mcp.remove_tool(tool_name)
    _active_tool_mode = normalized
    return [tool.name for tool in await mcp.list_tools()]


def main() -> None:
    deployment_configured = bool(os.environ.get("KLAYOUT_MCP_DEPLOYMENT_TOML"))
    if deployment_configured:
        _default_host_components()
    default_mode = "facade" if deployment_configured else "drawing"
    asyncio.run(configure_tool_mode(os.environ.get("KLAYOUT_MCP_TOOL_MODE", default_mode)))
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
