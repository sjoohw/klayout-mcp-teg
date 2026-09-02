"""Public nested input types for the persistent TEG workflow MCP tools.

The semantic validators in :mod:`workflow_manifest` remain authoritative.  These
types exist so MCP clients can discover the document shape before making a call
instead of receiving an unstructured ``dict[str, Any]`` schema.
"""

from __future__ import annotations

from typing import Any, Literal

from typing_extensions import NotRequired, TypedDict


class ProcessReferenceInput(TypedDict):
    profile: str
    version: str
    capability_sha256: str


class TechnologyAdapterIdentityInput(TypedDict):
    technology: str
    pdk_revision: str
    adapter_kind: str
    device_family: str
    topology: str
    package_version: str


class TechnologyAdapterReferenceInput(TypedDict):
    identity: TechnologyAdapterIdentityInput
    package_sha256: str
    registry_snapshot_sha256: str


class FrameInput(TypedDict):
    width_um: float
    height_um: float
    origin_um: list[float]
    allowed_boundary_um: list[float]


class PadsInput(TypedDict):
    count: int
    rows: int
    outline_um: list[float]
    numbering: str
    reserved_roles: dict[str, Any]
    pitch_um: NotRequired[float]
    explicit_bboxes_um: NotRequired[list[list[float]]]


class DeviceInput(TypedDict):
    dut_id: str
    family: Literal["transistor", "resistor", "capacitor"]
    device_type: str
    measurement_type: str
    parameters: dict[str, Any]
    doe: dict[str, Any]
    placement_constraints: dict[str, Any]


class TerminalInput(TypedDict):
    name: str
    electrical_role: str


class TerminalContractInput(TypedDict):
    dut_id: str
    terminals: list[TerminalInput]


class TerminalNetPadInput(TypedDict):
    dut_id: str
    terminal: str
    net: str
    pad: int
    shared_net_explicit: bool


class DcValueProgramInput(TypedDict):
    kind: Literal["dc_value"]
    value: float
    unit: str


class LinearSweepProgramInput(TypedDict):
    kind: Literal["linear_sweep"]
    start: float
    stop: float
    step: float
    direction: Literal["ascending", "descending"]
    unit: str


class AcAmplitudeProgramInput(TypedDict):
    kind: Literal["ac_amplitude"]
    amplitude: float
    unit: str


StimulusProgramInput = (
    DcValueProgramInput | LinearSweepProgramInput | AcAmplitudeProgramInput
)


class MeasurementComplianceInput(TypedDict):
    quantity: str
    limit: float
    unit: str


class MeasurementRequirementRecordInput(TypedDict):
    dut_id: str
    terminal: str
    mode: str
    quantity: NotRequired[str]
    unit: NotRequired[str]


class StimulusRequirementRecordInput(MeasurementRequirementRecordInput):
    source_mode: str
    program: StimulusProgramInput
    compliance: MeasurementComplianceInput
    polarity: str
    frequency_hz: float | None


class MeasurementTimingInput(TypedDict):
    settling_s: float
    integration: dict[str, Any]
    hold_s: float
    delay_s: float


class MeasurementSafetyEnvelopeInput(TypedDict):
    limits: dict[str, float]
    source_reference: str
    em_current_density_evidence: dict[str, Any] | None


class MeasurementRequirementsInput(TypedDict):
    stimuli: list[StimulusRequirementRecordInput]
    observables: list[MeasurementRequirementRecordInput]
    biases: list[StimulusRequirementRecordInput]
    timing: MeasurementTimingInput
    environment: dict[str, Any]
    safety_envelope: MeasurementSafetyEnvelopeInput


class RoutingPolicyInput(TypedDict):
    manhattan_only: Literal[True]
    prefer_first_metal: bool
    allowed_layer_roles: list[str]
    escalation_policy: str


class VerificationPolicyInput(TypedDict):
    internal_checks: list[str]
    external_evidence_required: list[str]


class OutputPolicyInput(TypedDict):
    format: str
    top_cell: str
    new_output_required: Literal[True]


class DesignIntentDraftInput(TypedDict):
    schema_version: Literal[1]
    intent_id: str
    units: Literal["um"]
    process: ProcessReferenceInput
    frame: FrameInput
    pads: PadsInput
    devices: list[DeviceInput]
    terminal_contracts: list[TerminalContractInput]
    terminal_net_pad_map: list[TerminalNetPadInput]
    measurement_requirements: MeasurementRequirementsInput
    routing_policy: RoutingPolicyInput
    verification_policy: VerificationPolicyInput
    output_policy: OutputPolicyInput
    unresolved_questions: list[str]
    technology_adapter: NotRequired[TechnologyAdapterReferenceInput]


class ApprovalReferenceInput(TypedDict):
    schema_version: Literal[1]
    draft_sha256: str
    process_capability_sha256: str
    source_artifact_sha256s: dict[str, str]
    approval_scope: str
    output_classes: list[str]
    signer_reference: str
    scheme_id: str
    attestation_reference: str
    approved_at: str
    expires_at: NotRequired[str]
    revocation_id: NotRequired[str]


class DutPinInput(TypedDict):
    dut_id: str
    terminal: str
    net: str
    pad: int
    probe_pin: str
    instrument_channel: str
    electrical_role: str


class InactiveTerminalStateInput(TypedDict):
    dut_id: str
    terminal: str
    state: Literal["force", "float", "ground", "guard", "follow_shared_pad"]
    value: NotRequired[float]
    unit: NotRequired[str]
    reference: NotRequired[str]


class InactiveTerminalPolicyInput(TypedDict):
    execution_mode: Literal["serial", "simultaneous"]
    active_dut_ids: list[str]
    inactive_terminal_states: list[InactiveTerminalStateInput]


class ElectricalTopologyInput(TypedDict):
    type: str
    connections: list[dict[str, Any]]
    guards: list[dict[str, Any]]
    inactive_terminal_policy: NotRequired[InactiveTerminalPolicyInput]


class TerminalReferenceInput(TypedDict):
    dut_id: str
    terminal: str


class ComplianceInput(TypedDict):
    quantity: str
    limit: float
    unit: str


class StimulusInput(TypedDict):
    stimulus_id: str
    requirement_kind: Literal["stimulus", "bias"]
    requirement_mode: str
    requirement_quantity: NotRequired[str]
    requirement_unit: NotRequired[str]
    target: TerminalReferenceInput
    source_mode: str
    program: StimulusProgramInput
    compliance: ComplianceInput
    polarity: str
    frequency_hz: float | None


class ObservableInput(TypedDict):
    label: str
    requirement_mode: str
    quantity: str
    unit: str
    source: TerminalReferenceInput


TimingInput = MeasurementTimingInput


SafetyEnvelopeInput = MeasurementSafetyEnvelopeInput


class CalibrationAndDeembeddingInput(TypedDict):
    required: bool
    calibration_plane: str
    reference_duts: list[Any]


class ExporterInput(TypedDict):
    name: str
    version: str
    output_sha256: str


class MeasurementManifestInput(TypedDict):
    schema_version: Literal[1]
    design_intent_sha256: str
    generated_layout_sha256: str
    dut_pin_map: list[DutPinInput]
    electrical_topology: ElectricalTopologyInput
    stimuli: list[StimulusInput]
    observables: list[ObservableInput]
    timing: TimingInput
    environment: dict[str, Any]
    safety_envelope: SafetyEnvelopeInput
    calibration_and_deembedding: CalibrationAndDeembeddingInput
    exporter: NotRequired[ExporterInput]


__all__ = [
    "ApprovalReferenceInput",
    "DesignIntentDraftInput",
    "MeasurementManifestInput",
]
