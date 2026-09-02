"""Host-controlled execution contract for external DRC/LVS/PEX tools.

The MCP never accepts an executable, deck, runset, or license path from a model.
Those details belong to a host-installed runner.  This module only binds the
runner's immutable identity and preflight receipt to one freshly hashed layout,
then publishes the resulting report without replacing an existing file.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any, Mapping, Protocol, runtime_checkable

from .errors import AnalysisError
from .external_evidence import EVIDENCE_KINDS, NONPRODUCTION_MARKERS
from .file_publication import (
    OutputAlreadyExistsError,
    publication_staging_prefix,
    publish_new_file,
    require_supported_publication_root,
)
from .workflow_manifest import SHA256_PATTERN, canonical_sha256, immutable_json_copy


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
RESOURCE_FIELDS = frozenset({"cpu_seconds", "memory_mb", "process_count"})


def _fail(code: str, message: str, *, details: Mapping[str, Any], next_action: str) -> None:
    raise AnalysisError(
        code=code,
        message=message,
        details={"stage": "external_verification_execution", **dict(details)},
        next_action=next_action,
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        _fail(
            "VERIFICATION_LAYOUT_READ_FAILED",
            "The generated layout could not be read for external verification.",
            details={"field": "layout_path", "error_type": type(exc).__name__},
            next_action="Restore the immutable generated layout and retry the same job.",
        )
    return digest.hexdigest()


@runtime_checkable
class ExternalVerificationRunner(Protocol):
    """Interface implemented by a trusted host package, never by MCP input."""

    runner_id: str
    runner_version: str
    trusted: bool
    is_mock: bool
    supported_kinds: tuple[str, ...]

    def preflight(self, *, kind: str) -> Mapping[str, Any]: ...

    def run(
        self,
        *,
        kind: str,
        layout_path: str,
        layout_sha256: str,
        output_directory: str,
        invocation_sha256: str,
        timeout_seconds: float,
        resource_limits: Mapping[str, int],
    ) -> Mapping[str, Any]: ...


class ExternalVerificationRunnerRegistry:
    """Frozen-on-use registry of explicitly installed external runners."""

    def __init__(self, *, production_mode: bool = True) -> None:
        self.production_mode = production_mode
        self._runners: dict[str, ExternalVerificationRunner] = {}
        self._frozen = False

    def register(self, runner: ExternalVerificationRunner) -> None:
        if self._frozen:
            _fail(
                "VERIFICATION_RUNNER_REGISTRY_FROZEN",
                "The verification runner registry cannot change after execution begins.",
                details={},
                next_action="Restart the host after changing its signed runner configuration.",
            )
        if not isinstance(runner, ExternalVerificationRunner):
            _fail(
                "INVALID_VERIFICATION_RUNNER",
                "The installed component does not implement the verification runner contract.",
                details={"runner_type": type(runner).__name__},
                next_action="Install a runner that implements the documented host protocol.",
            )
        if not SAFE_ID.fullmatch(runner.runner_id) or not SAFE_ID.fullmatch(runner.runner_version):
            _fail(
                "INVALID_VERIFICATION_RUNNER_IDENTITY",
                "Runner ID and version must be stable filesystem-safe identifiers.",
                details={"runner_id": runner.runner_id, "runner_version": runner.runner_version},
                next_action="Correct the signed runner package identity.",
            )
        kinds = tuple(runner.supported_kinds)
        if not kinds or len(kinds) != len(set(kinds)) or any(kind not in EVIDENCE_KINDS for kind in kinds):
            _fail(
                "INVALID_VERIFICATION_RUNNER_KINDS",
                "A runner must declare a unique non-empty subset of drc, lvs, and pex.",
                details={"runner_id": runner.runner_id, "supported_kinds": list(kinds)},
                next_action="Correct the runner's supported_kinds declaration.",
            )
        identity = " ".join((runner.runner_id, type(runner).__name__, type(runner).__module__)).casefold()
        if self.production_mode and (
            runner.trusted is not True
            or runner.is_mock is True
            or any(marker in identity for marker in NONPRODUCTION_MARKERS)
        ):
            _fail(
                "NONPRODUCTION_VERIFICATION_RUNNER_FORBIDDEN",
                "Production mode accepts only trusted, non-test host runners.",
                details={"runner_id": runner.runner_id},
                next_action="Install and allowlist the trusted production runner package.",
            )
        if runner.runner_id in self._runners:
            _fail(
                "DUPLICATE_VERIFICATION_RUNNER",
                "A verification runner ID can be registered only once.",
                details={"runner_id": runner.runner_id},
                next_action="Remove the duplicate host registration.",
            )
        self._runners[runner.runner_id] = runner

    def resolve(self, runner_id: str, *, kind: str) -> ExternalVerificationRunner:
        self._frozen = True
        runner = self._runners.get(runner_id)
        if runner is None:
            _fail(
                "VERIFICATION_RUNNER_UNAVAILABLE",
                "The requested external verification runner is not installed on this host.",
                details={"runner_id": runner_id, "available_runner_ids": sorted(self._runners)},
                next_action="Select an installed runner ID or ask the CAD owner to configure one.",
            )
        if kind not in runner.supported_kinds:
            _fail(
                "VERIFICATION_KIND_UNSUPPORTED_BY_RUNNER",
                "The selected runner does not support the requested evidence kind.",
                details={"runner_id": runner_id, "kind": kind, "supported_kinds": list(runner.supported_kinds)},
                next_action="Select a runner that explicitly supports this evidence kind.",
            )
        return runner

    def readiness(self) -> dict[str, Any]:
        by_kind = {
            kind: sorted(
                runner.runner_id
                for runner in self._runners.values()
                if kind in runner.supported_kinds
            )
            for kind in EVIDENCE_KINDS
        }
        return {
            "configured": bool(self._runners),
            "production_mode": self.production_mode,
            "registry_frozen": self._frozen,
            "runner_ids": sorted(self._runners),
            "runner_ids_by_kind": by_kind,
            "model_can_register_or_import_runner": False,
        }


def _validate_preflight(value: Mapping[str, Any], *, runner_id: str, kind: str) -> dict[str, Any]:
    required = {
        "ok",
        "executable",
        "license_available",
        "deck",
        "runset",
        "execution_is_out_of_process",
        "timeout_enforced",
        "resource_limits_enforced",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        _fail(
            "VERIFICATION_PREFLIGHT_SCHEMA_INVALID",
            "Runner preflight did not return the exact required readiness receipt.",
            details={"runner_id": runner_id, "kind": kind, "required_fields": sorted(required)},
            next_action="Update the host runner to return the documented preflight schema.",
        )
    if not all(
        value.get(field) is True
        for field in (
            "ok",
            "license_available",
            "execution_is_out_of_process",
            "timeout_enforced",
            "resource_limits_enforced",
        )
    ):
        _fail(
            "VERIFICATION_PREFLIGHT_NOT_READY",
            "Executable, license, isolation, timeout, or resource-limit readiness is missing.",
            details={"runner_id": runner_id, "kind": kind, "preflight": immutable_json_copy(value)},
            next_action="Fix the listed host prerequisite and rerun preflight.",
        )
    normalized: dict[str, Any] = {}
    for field, required_keys in {
        "executable": {"name", "version", "sha256"},
        "deck": {"id", "version", "sha256"},
        "runset": {"id", "sha256"},
    }.items():
        item = value.get(field)
        if not isinstance(item, Mapping) or set(item) != required_keys:
            _fail(
                "VERIFICATION_PREFLIGHT_SCHEMA_INVALID",
                "Executable, deck, and runset identities must be exact and hash-bound.",
                details={"runner_id": runner_id, "field": field, "required_fields": sorted(required_keys)},
                next_action="Return the exact identity fields without paths or credentials.",
            )
        for key, child in item.items():
            if not isinstance(child, str) or not child:
                _fail(
                    "VERIFICATION_PREFLIGHT_IDENTITY_INVALID",
                    "A preflight identity value is missing or not a string.",
                    details={"runner_id": runner_id, "field": f"{field}.{key}"},
                    next_action="Provide every immutable tool/deck/runset identity value.",
                )
        if not SHA256_PATTERN.fullmatch(str(item["sha256"])):
            _fail(
                "VERIFICATION_PREFLIGHT_HASH_INVALID",
                "A preflight identity SHA-256 is malformed.",
                details={"runner_id": runner_id, "field": f"{field}.sha256"},
                next_action="Recompute and provide the lowercase SHA-256 digest.",
            )
        normalized[field] = dict(item)
    normalized.update(
        {
            "license_available": True,
            "execution_is_out_of_process": True,
            "timeout_enforced": True,
            "resource_limits_enforced": True,
        }
    )
    return normalized


def execute_external_verification(
    *,
    registry: ExternalVerificationRunnerRegistry,
    runner_id: str,
    kind: str,
    generated_layout_path: str | Path,
    report_root: str | Path,
    timeout_seconds: float,
    resource_limits: Mapping[str, int],
) -> dict[str, Any]:
    """Execute one host runner and publish one provenance-bound report."""

    if kind not in EVIDENCE_KINDS:
        _fail(
            "INVALID_EXTERNAL_VERIFICATION_KIND",
            "External verification kind must be drc, lvs, or pex.",
            details={"field": "kind", "value": kind},
            next_action="Choose one of: drc, lvs, pex.",
        )
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or not 1 <= timeout_seconds <= 86400:
        _fail(
            "INVALID_VERIFICATION_TIMEOUT",
            "timeout_seconds must be between 1 and 86400.",
            details={"field": "timeout_seconds", "value": timeout_seconds},
            next_action="Set an explicit bounded wall-time limit.",
        )
    if not isinstance(resource_limits, Mapping) or set(resource_limits) != RESOURCE_FIELDS:
        _fail(
            "INVALID_VERIFICATION_RESOURCE_LIMITS",
            "resource_limits must contain exactly cpu_seconds, memory_mb, and process_count.",
            details={"field": "resource_limits", "required_fields": sorted(RESOURCE_FIELDS)},
            next_action="Provide all three positive integer resource limits.",
        )
    if any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in resource_limits.values()):
        _fail(
            "INVALID_VERIFICATION_RESOURCE_LIMITS",
            "Every verification resource limit must be a positive integer.",
            details={"field": "resource_limits", "value": dict(resource_limits)},
            next_action="Use positive integer CPU, memory, and process limits.",
        )

    layout = Path(generated_layout_path).expanduser().resolve()
    if not layout.is_file() or layout.is_symlink():
        _fail(
            "VERIFICATION_LAYOUT_NOT_REGULAR_FILE",
            "The generated layout must be an existing regular file.",
            details={"field": "generated_layout_path"},
            next_action="Generate or restore the immutable final GDS/OAS file.",
        )
    root = Path(report_root).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    filesystem_type = require_supported_publication_root(root)
    layout_sha256 = _file_sha256(layout)
    runner = registry.resolve(runner_id, kind=kind)
    preflight = _validate_preflight(runner.preflight(kind=kind), runner_id=runner_id, kind=kind)
    invocation = {
        "schema_version": 1,
        "kind": kind,
        "runner": {"id": runner.runner_id, "version": runner.runner_version},
        "layout_sha256": layout_sha256,
        "executable": preflight["executable"],
        "deck": preflight["deck"],
        "runset": preflight["runset"],
        "timeout_seconds": float(timeout_seconds),
        "resource_limits": dict(sorted(resource_limits.items())),
    }
    invocation_sha256 = canonical_sha256(invocation)
    staging = Path(
        tempfile.mkdtemp(
            prefix=publication_staging_prefix("external-report", directory=True),
            dir=root,
        )
    ).resolve()
    try:
        result = runner.run(
            kind=kind,
            layout_path=str(layout),
            layout_sha256=layout_sha256,
            output_directory=str(staging),
            invocation_sha256=invocation_sha256,
            timeout_seconds=float(timeout_seconds),
            resource_limits=dict(resource_limits),
        )
        if not isinstance(result, Mapping) or set(result) != {"ok", "report_name"} or result.get("ok") is not True:
            _fail(
                "VERIFICATION_RUNNER_RESULT_INVALID",
                "The runner did not return one successful report-name result.",
                details={"runner_id": runner_id, "kind": kind},
                next_action="Inspect the host runner log and retry after correcting the reported failure.",
            )
        report_name = result.get("report_name")
        if not isinstance(report_name, str) or Path(report_name).name != report_name or not report_name.endswith(".json"):
            _fail(
                "VERIFICATION_REPORT_NAME_INVALID",
                "The runner report must be one JSON basename inside its staging directory.",
                details={"runner_id": runner_id, "kind": kind, "report_name": report_name},
                next_action="Update the host runner to return a safe JSON basename.",
            )
        staged_report = (staging / report_name).resolve()
        try:
            staged_report.relative_to(staging)
        except ValueError:
            _fail(
                "VERIFICATION_REPORT_ESCAPED_STAGING",
                "The runner report escaped its host-controlled staging directory.",
                details={"runner_id": runner_id, "kind": kind},
                next_action="Remove the unsafe runner package and inspect the host.",
            )
        if not staged_report.is_file() or staged_report.is_symlink():
            _fail(
                "VERIFICATION_REPORT_MISSING",
                "The runner did not create the declared regular report file.",
                details={"runner_id": runner_id, "kind": kind},
                next_action="Inspect the host runner log and regenerate the report.",
            )
        try:
            raw_report = json.loads(staged_report.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _fail(
                "VERIFICATION_REPORT_NOT_JSON",
                "The generated verification report is not valid UTF-8 JSON.",
                details={"runner_id": runner_id, "kind": kind, "error_type": type(exc).__name__},
                next_action="Correct the runner's report serializer.",
            )
        if not isinstance(raw_report, Mapping) or raw_report.get("kind") != kind:
            _fail(
                "VERIFICATION_REPORT_KIND_MISMATCH",
                "The generated report kind does not match the requested verification kind.",
                details={"expected": kind, "actual": None if not isinstance(raw_report, Mapping) else raw_report.get("kind")},
                next_action="Rerun the matching deck and report adapter.",
            )
        for field, expected in {
            "input_layout_sha256": layout_sha256,
            "deck_sha256": preflight["deck"]["sha256"],
            "invocation_sha256": invocation_sha256,
        }.items():
            if raw_report.get(field) != expected:
                _fail(
                    "VERIFICATION_REPORT_PROVENANCE_MISMATCH",
                    "The report does not bind the exact layout, deck, and invocation.",
                    details={"field": field, "expected": expected, "actual": raw_report.get(field)},
                    next_action="Discard the stale report and rerun verification for this exact layout.",
                )
        if _file_sha256(layout) != layout_sha256:
            _fail(
                "VERIFICATION_LAYOUT_CHANGED_DURING_RUN",
                "The generated layout changed while external verification was running.",
                details={"expected_sha256": layout_sha256},
                next_action="Restore the immutable layout and rerun all external evidence.",
            )
        report_sha256 = _file_sha256(staged_report)
        final_name = f"{kind}-{report_sha256}.json"
        final_path = root / final_name
        descriptor, sibling_name = tempfile.mkstemp(
            prefix=publication_staging_prefix("external-report"),
            dir=root,
        )
        os.close(descriptor)
        sibling_staging = Path(sibling_name).resolve()
        try:
            shutil.copyfile(staged_report, sibling_staging)
            try:
                publish_new_file(sibling_staging, final_path)
            except OutputAlreadyExistsError:
                if not final_path.is_file() or _file_sha256(final_path) != report_sha256:
                    _fail(
                        "VERIFICATION_REPORT_PUBLICATION_CONFLICT",
                        "An existing report path does not contain the same immutable report.",
                        details={"report_name": final_name},
                        next_action="Ask the host operator to inspect the report store integrity.",
                    )
        finally:
            sibling_staging.unlink(missing_ok=True)
        return {
            "ok": True,
            "kind": kind,
            "runner_id": runner.runner_id,
            "runner_version": runner.runner_version,
            "layout_sha256": layout_sha256,
            "invocation": invocation,
            "invocation_sha256": invocation_sha256,
            "report_name": final_name,
            "report_sha256": report_sha256,
            "report_root": str(root),
            "publication_filesystem": filesystem_type,
            "external_evidence_attached": False,
            "production_ready": False,
            "next_action": "Attach this report through teg_verify so its trusted parser and signoff policy can revalidate it.",
        }
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def external_verification_runner_contract() -> dict[str, Any]:
    return {
        "contract_version": 1,
        "runner_registry_host_controlled": True,
        "model_can_register_or_import_runner": False,
        "model_can_supply_executable_deck_runset_or_license_path": False,
        "preflight_requires_executable_license_deck_runset": True,
        "execution_requires_out_of_process_timeout_and_resource_limits": True,
        "layout_hashed_before_and_after_execution": True,
        "report_bound_to_layout_deck_and_invocation_hash": True,
        "report_publication_create_only": True,
        "execution_result_is_signoff": False,
        "stock_runner_configured": False,
    }
