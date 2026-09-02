"""Explicit host assembly and deployment diagnostics for the persistent facade."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
import re
import tomllib
from typing import Any, Callable, Mapping

from .approval import ApprovalVerifier
from .errors import AnalysisError
from .external_evidence import ExternalEvidenceAdapterRegistry, SignoffPolicy
from .technology_registry import TechnologyAdapterRegistry
from .verification_runner import (
    ExternalVerificationRunnerRegistry,
    execute_external_verification,
)
from .workflow_store import (
    ProcessCapabilityProvider,
    TegWorkflowFacade,
    WorkflowEngineRegistry,
    WorkflowJobStore,
)


FORBIDDEN_DEPLOYMENT_KEYS = frozenset(
    {"module", "module_path", "import", "import_path", "class", "class_name"}
)
SECRET_KEY_PATTERN = re.compile(
    r"(?:password|secret|token|credential|license_key|api_key)", re.IGNORECASE
)
SAFE_COMPONENT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
COMPONENT_NAMES = (
    "process_provider",
    "approval_verifier",
    "engine_registry",
    "technology_registry",
    "external_evidence_registry",
    "external_verification_runner_registry",
    "signoff_policy",
    "pad_macro_registry",
)
HOST_COMPONENT_ENTRYPOINT_GROUP = "klayout_mcp.host_components"


def _fail(code: str, message: str, *, details: Mapping[str, Any], next_action: str) -> None:
    raise AnalysisError(
        code=code,
        message=message,
        details={"stage": "host_startup", **dict(details)},
        next_action=next_action,
    )


def _walk_keys(value: Any, *, path: str = "") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            found.append((child_path, child))
            found.extend(_walk_keys(child, path=child_path))
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            found.extend(_walk_keys(child, path=f"{path}[{index}]"))
    return found


def load_deployment_toml(path: str | Path) -> dict[str, Any]:
    """Load host-owned TOML while rejecting secrets and dynamic import directives."""

    source = Path(path).expanduser().resolve()
    try:
        with source.open("rb") as handle:
            document = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        _fail(
            "DEPLOYMENT_CONFIG_READ_FAILED",
            "The host deployment TOML could not be read.",
            details={"field": "deployment_path", "value": str(source), "error_type": type(exc).__name__},
            next_action="Provide a readable, valid host-controlled deployment.toml.",
        )
    if document.get("schema_version") != 1:
        _fail(
            "DEPLOYMENT_CONFIG_SCHEMA_UNSUPPORTED",
            "deployment.toml schema_version must be 1.",
            details={"field": "schema_version", "value": document.get("schema_version")},
            next_action="Set schema_version = 1 and validate the deployment again.",
        )
    for field_path, value in _walk_keys(document):
        key = field_path.rsplit(".", 1)[-1].split("[", 1)[0]
        if key in FORBIDDEN_DEPLOYMENT_KEYS:
            _fail(
                "DEPLOYMENT_DYNAMIC_IMPORT_FORBIDDEN",
                "Deployment configuration cannot provide Python module, class, or import paths.",
                details={"field": field_path, "value": value},
                next_action="Select a host-installed component by its allowlisted stable ID.",
            )
        if SECRET_KEY_PATTERN.search(key):
            _fail(
                "DEPLOYMENT_SECRET_IN_CONFIG_FORBIDDEN",
                "Secrets and license credentials cannot be stored in deployment.toml.",
                details={"field": field_path, "received_type": type(value).__name__},
                next_action="Move the credential to the host secret provider and keep only its non-secret reference ID.",
            )
    return document


@dataclass(frozen=True, slots=True)
class HostComponents:
    store: WorkflowJobStore
    process_provider: ProcessCapabilityProvider | None
    approval_verifier: ApprovalVerifier | None
    engine_registry: WorkflowEngineRegistry
    technology_registry: TechnologyAdapterRegistry
    external_evidence_registry: ExternalEvidenceAdapterRegistry | None = None
    external_verification_runner_registry: ExternalVerificationRunnerRegistry | None = None
    signoff_policy: SignoffPolicy | None = None
    pad_macro_registry: Any = None
    external_report_root: Path | None = None
    output_class: str = "nonproduction_gds"
    production_mode: bool = True

    def build_facade(self) -> TegWorkflowFacade:
        return TegWorkflowFacade(
            store=self.store,
            process_provider=self.process_provider,
            approval_verifier=self.approval_verifier,
            engine_registry=self.engine_registry,
            external_evidence_registry=self.external_evidence_registry,
            external_report_root=self.external_report_root,
            signoff_policy=self.signoff_policy,
            technology_registry=self.technology_registry,
            output_class=self.output_class,
            production_mode=self.production_mode,
        )

    def doctor(self, *, active_output_probe: bool = False) -> dict[str, Any]:
        output = self.store.publication_status(active_probe=active_output_probe)
        approval_ready = self.approval_verifier is not None
        provider_ready = self.process_provider is not None
        evidence_ready = self.external_evidence_registry is not None
        runner_readiness = (
            {
                "configured": False,
                "runner_ids_by_kind": {"drc": [], "lvs": [], "pex": []},
                "model_can_register_or_import_runner": False,
            }
            if self.external_verification_runner_registry is None
            else self.external_verification_runner_registry.readiness()
        )
        profiles = []
        for engine in self.engine_registry.readiness():
            blockers = []
            if not provider_ready:
                blockers.append("PROCESS_CAPABILITY_PROVIDER_UNAVAILABLE")
            if not approval_ready:
                blockers.append("APPROVAL_VERIFIER_UNAVAILABLE")
            if not output["supported_filesystem"]:
                blockers.append("UNSUPPORTED_PUBLICATION_FILESYSTEM")
            profiles.append(
                {
                    **engine,
                    "stages": {
                        "intake": provider_ready,
                        "plan": provider_ready
                        and approval_ready
                        and engine["planning_engine_configured"],
                        "generate": provider_ready
                        and approval_ready
                        and engine["generation_engine_configured"]
                        and output["supported_filesystem"],
                        "drc": evidence_ready
                        and bool(runner_readiness["runner_ids_by_kind"]["drc"]),
                        "lvs": evidence_ready
                        and bool(runner_readiness["runner_ids_by_kind"]["lvs"]),
                        "pex": evidence_ready
                        and bool(runner_readiness["runner_ids_by_kind"]["pex"]),
                    },
                    "blockers": blockers,
                }
            )
        blockers = []
        if not profiles:
            blockers.append("WORKFLOW_ENGINE_REGISTRY_EMPTY")
        if not provider_ready:
            blockers.append("PROCESS_CAPABILITY_PROVIDER_UNAVAILABLE")
        if not approval_ready:
            blockers.append("APPROVAL_VERIFIER_UNAVAILABLE")
        if not output["supported_filesystem"]:
            blockers.append("UNSUPPORTED_PUBLICATION_FILESYSTEM")
        return {
            "ok": not blockers and all(not profile["blockers"] for profile in profiles),
            "blockers": blockers,
            "output_publication": output,
            "approval_verifier_configured": approval_ready,
            "process_provider_configured": provider_ready,
            "technology_registry": self.technology_registry.contract(),
            "external_verification_runners": runner_readiness,
            "profile_readiness": profiles,
            "production_mode": self.production_mode,
        }

    def run_external_verification(
        self,
        *,
        runner_id: str,
        kind: str,
        generated_layout_path: str | Path,
        timeout_seconds: float,
        resource_limits: Mapping[str, int],
    ) -> dict[str, Any]:
        """Run one installed verifier without accepting executable/deck paths."""

        if self.external_verification_runner_registry is None:
            _fail(
                "VERIFICATION_RUNNER_REGISTRY_UNAVAILABLE",
                "No external verification runner registry is configured.",
                details={"field": "components.external_verification_runner_registry"},
                next_action="Configure an installed, allowlisted runner registry component.",
            )
        if self.external_report_root is None:
            _fail(
                "EXTERNAL_REPORT_ROOT_UNAVAILABLE",
                "No host-controlled external report root is configured.",
                details={"field": "paths.external_report_root"},
                next_action="Configure a host-controlled report directory.",
            )
        return execute_external_verification(
            registry=self.external_verification_runner_registry,
            runner_id=runner_id,
            kind=kind,
            generated_layout_path=generated_layout_path,
            report_root=self.external_report_root,
            timeout_seconds=timeout_seconds,
            resource_limits=resource_limits,
        )


ComponentFactory = Callable[[Mapping[str, Any]], Any]


def discover_installed_component_factories(
    config: Mapping[str, Any],
    *,
    entrypoint_records: Any = None,
) -> dict[str, dict[str, ComponentFactory]]:
    """Load only selected, allowlisted factories from installed entry points."""

    components = config.get("components", {})
    security = config.get("security", {})
    if not isinstance(components, Mapping) or not isinstance(security, Mapping):
        _fail(
            "DEPLOYMENT_CONFIG_SECTION_INVALID",
            "components and security must be TOML tables.",
            details={"field": "deployment"},
            next_action="Use the documented deployment.toml table structure.",
        )
    allowed = security.get("allowed_component_ids", [])
    if not isinstance(allowed, list) or not all(isinstance(item, str) for item in allowed):
        _fail(
            "DEPLOYMENT_ALLOWLIST_INVALID",
            "security.allowed_component_ids must be an array of stable IDs.",
            details={"field": "security.allowed_component_ids"},
            next_action="List every permitted installed component ID explicitly.",
        )
    selected = {
        (role, component_id)
        for role, component_id in components.items()
        if role in COMPONENT_NAMES and isinstance(component_id, str)
    }
    records = (
        metadata.entry_points(group=HOST_COMPONENT_ENTRYPOINT_GROUP)
        if entrypoint_records is None
        else entrypoint_records
    )
    discovered: dict[str, dict[str, ComponentFactory]] = {}
    for record in records:
        name = getattr(record, "name", "")
        if ":" not in name:
            continue
        role, component_id = name.split(":", 1)
        if (role, component_id) not in selected or component_id not in allowed:
            continue
        if role not in COMPONENT_NAMES or not SAFE_COMPONENT_ID.fullmatch(component_id):
            _fail(
                "DEPLOYMENT_ENTRYPOINT_IDENTITY_INVALID",
                "A selected installed component entry point has an invalid role or ID.",
                details={"entrypoint_name": name},
                next_action="Correct the installed package entry-point metadata.",
            )
        if component_id in discovered.get(role, {}):
            _fail(
                "DEPLOYMENT_ENTRYPOINT_DUPLICATE",
                "More than one installed package claims the selected component ID.",
                details={"component_role": role, "component_id": component_id},
                next_action="Remove the duplicate installed package.",
            )
        loaded = record.load()
        if not callable(loaded):
            _fail(
                "DEPLOYMENT_ENTRYPOINT_NOT_FACTORY",
                "The selected installed component entry point is not callable.",
                details={"component_role": role, "component_id": component_id},
                next_action="Install a package exposing a callable component factory.",
            )
        discovered.setdefault(role, {})[component_id] = loaded
    return discovered


def build_host_components_from_config(
    config: Mapping[str, Any],
    *,
    installed_factories: Mapping[str, Mapping[str, ComponentFactory]],
) -> HostComponents:
    """Assemble only host-installed, explicitly allowlisted component IDs."""

    components = config.get("components", {})
    security = config.get("security", {})
    paths = config.get("paths", {})
    if not isinstance(components, Mapping) or not isinstance(security, Mapping) or not isinstance(paths, Mapping):
        _fail(
            "DEPLOYMENT_CONFIG_SECTION_INVALID",
            "components, security, and paths must be TOML tables.",
            details={"field": "deployment"},
            next_action="Use the documented deployment.toml table structure.",
        )
    allowlist = security.get("allowed_component_ids", [])
    if not isinstance(allowlist, list) or not all(isinstance(item, str) for item in allowlist):
        _fail(
            "DEPLOYMENT_ALLOWLIST_INVALID",
            "security.allowed_component_ids must be an array of stable IDs.",
            details={"field": "security.allowed_component_ids", "value": allowlist},
            next_action="List every permitted installed component ID explicitly.",
        )
    allowed = set(allowlist)
    selected: dict[str, Any] = {}
    for component_name in COMPONENT_NAMES:
        component_id = components.get(component_name)
        if component_id is None:
            selected[component_name] = None
            continue
        if not isinstance(component_id, str) or component_id not in allowed:
            _fail(
                "DEPLOYMENT_COMPONENT_NOT_ALLOWLISTED",
                "A selected host component ID is not explicitly allowlisted.",
                details={"field": f"components.{component_name}", "value": component_id, "allowed": sorted(allowed)},
                next_action="Add the installed stable ID to the host allowlist or select an already allowed ID.",
            )
        factory = installed_factories.get(component_name, {}).get(component_id)
        if factory is None:
            _fail(
                "DEPLOYMENT_COMPONENT_NOT_INSTALLED",
                "An allowlisted component ID is not installed for this component role.",
                details={"field": f"components.{component_name}", "value": component_id},
                next_action="Install the signed component package or correct the selected stable ID.",
            )
        selected[component_name] = factory(config)
    if not isinstance(selected["engine_registry"], WorkflowEngineRegistry):
        selected["engine_registry"] = WorkflowEngineRegistry()
    if not isinstance(selected["technology_registry"], TechnologyAdapterRegistry):
        selected["technology_registry"] = TechnologyAdapterRegistry(paths.get("technology_registry_root"))
    workflow_root = paths.get("workflow_root")
    output_root = paths.get("output_root")
    if not isinstance(workflow_root, str) or not isinstance(output_root, str):
        _fail(
            "DEPLOYMENT_WORKFLOW_PATH_REQUIRED",
            "paths.workflow_root and paths.output_root are required.",
            details={"field": "paths", "missing": [name for name, value in (("workflow_root", workflow_root), ("output_root", output_root)) if not isinstance(value, str)]},
            next_action="Configure host-controlled workflow and final output directories.",
        )
    return HostComponents(
        store=WorkflowJobStore(workflow_root, output_root=output_root),
        process_provider=selected["process_provider"],
        approval_verifier=selected["approval_verifier"],
        engine_registry=selected["engine_registry"],
        technology_registry=selected["technology_registry"],
        external_evidence_registry=selected["external_evidence_registry"],
        external_verification_runner_registry=selected[
            "external_verification_runner_registry"
        ],
        signoff_policy=selected["signoff_policy"],
        pad_macro_registry=selected["pad_macro_registry"],
        external_report_root=(
            None
            if paths.get("external_report_root") is None
            else Path(paths["external_report_root"]).expanduser().resolve()
        ),
        output_class=str(config.get("output_class", "nonproduction_gds")),
        production_mode=bool(config.get("production_mode", True)),
    )


def build_host_components_from_toml(path: str | Path) -> HostComponents:
    """Build a deployment using installed allowlisted entry-point factories."""

    config = load_deployment_toml(path)
    installed = discover_installed_component_factories(config)
    return build_host_components_from_config(config, installed_factories=installed)
