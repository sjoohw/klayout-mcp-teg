from pathlib import Path

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.host_factory import (
    build_host_components_from_config,
    discover_installed_component_factories,
    load_deployment_toml,
)
from klayout_mcp.technology_registry import TechnologyAdapterRegistry
from klayout_mcp.workflow_store import WorkflowEngineRegistry
from klayout_mcp.verification_runner import ExternalVerificationRunnerRegistry


def _write_deployment(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_deployment_rejects_dynamic_import_and_secret_fields(tmp_path: Path) -> None:
    dynamic = _write_deployment(
        tmp_path / "dynamic.toml",
        'schema_version = 1\nmodule_path = "user.module"\n',
    )
    with pytest.raises(AnalysisError) as caught:
        load_deployment_toml(dynamic)
    assert caught.value.code == "DEPLOYMENT_DYNAMIC_IMPORT_FORBIDDEN"

    secret = _write_deployment(
        tmp_path / "secret.toml",
        'schema_version = 1\nlicense_token = "do-not-store"\n',
    )
    with pytest.raises(AnalysisError) as caught:
        load_deployment_toml(secret)
    assert caught.value.code == "DEPLOYMENT_SECRET_IN_CONFIG_FORBIDDEN"


def test_host_factory_uses_only_allowlisted_installed_ids(tmp_path: Path) -> None:
    config = {
        "schema_version": 1,
        "production_mode": True,
        "paths": {
            "workflow_root": str(tmp_path / "workflow"),
            "output_root": str(tmp_path / "output"),
        },
        "security": {"allowed_component_ids": ["engines-v1", "tech-v1"]},
        "components": {
            "engine_registry": "engines-v1",
            "technology_registry": "tech-v1",
        },
    }
    host = build_host_components_from_config(
        config,
        installed_factories={
            "engine_registry": {"engines-v1": lambda _: WorkflowEngineRegistry()},
            "technology_registry": {"tech-v1": lambda _: TechnologyAdapterRegistry()},
        },
    )

    doctor = host.doctor(active_output_probe=True)

    assert doctor["output_publication"]["supported_filesystem"] is True
    assert doctor["technology_registry"]["wildcard_or_alias_fallback"] is False
    assert doctor["external_verification_runners"]["configured"] is False


def test_host_doctor_separates_report_parser_from_execution_runner(tmp_path: Path) -> None:
    config = {
        "schema_version": 1,
        "paths": {
            "workflow_root": str(tmp_path / "workflow"),
            "output_root": str(tmp_path / "output"),
        },
        "security": {"allowed_component_ids": ["runner-registry-v1"]},
        "components": {
            "external_verification_runner_registry": "runner-registry-v1"
        },
    }
    host = build_host_components_from_config(
        config,
        installed_factories={
            "external_verification_runner_registry": {
                "runner-registry-v1": lambda _: ExternalVerificationRunnerRegistry()
            }
        },
    )

    doctor = host.doctor()

    assert doctor["external_verification_runners"]["configured"] is False
    assert doctor["external_verification_runners"]["runner_ids_by_kind"]["drc"] == []


def test_host_factory_rejects_allowlisted_but_uninstalled_component(tmp_path: Path) -> None:
    config = {
        "schema_version": 1,
        "paths": {
            "workflow_root": str(tmp_path / "workflow"),
            "output_root": str(tmp_path / "output"),
        },
        "security": {"allowed_component_ids": ["unknown-provider"]},
        "components": {"process_provider": "unknown-provider"},
    }

    with pytest.raises(AnalysisError) as caught:
        build_host_components_from_config(config, installed_factories={})

    assert caught.value.code == "DEPLOYMENT_COMPONENT_NOT_INSTALLED"


def test_discovery_loads_only_selected_allowlisted_entrypoint() -> None:
    loaded: list[str] = []

    class EntryPoint:
        def __init__(self, name: str) -> None:
            self.name = name

        def load(self):
            loaded.append(self.name)
            return lambda _: WorkflowEngineRegistry()

    config = {
        "components": {"engine_registry": "engine-v1"},
        "security": {"allowed_component_ids": ["engine-v1", "unused-v1"]},
    }

    factories = discover_installed_component_factories(
        config,
        entrypoint_records=[
            EntryPoint("engine_registry:engine-v1"),
            EntryPoint("approval_verifier:unused-v1"),
        ],
    )

    assert loaded == ["engine_registry:engine-v1"]
    assert set(factories) == {"engine_registry"}
    assert set(factories["engine_registry"]) == {"engine-v1"}
