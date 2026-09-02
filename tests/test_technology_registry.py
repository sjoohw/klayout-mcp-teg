from pathlib import Path

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.technology_registry import TechnologyAdapterRegistry


def _package(version: str = "1.0.0", *, compiler: str = "a" * 64):
    return {
        "schema_version": 1,
        "identity": {
            "technology": "tech-a",
            "pdk_revision": "r7",
            "adapter_kind": "transistor",
            "device_family": "finfet",
            "topology": "nmos-core",
            "package_version": version,
        },
        "process_capability_sha256": "b" * 64,
        "layermap_sha256": "c" * 64,
        "compiler_code_sha256": compiler,
        "supported_parameters": ["gate_length", "cpp", "nfin", "cell_height"],
    }


def test_registry_resolves_only_exact_identity_and_pins_hash(tmp_path: Path) -> None:
    registry = TechnologyAdapterRegistry(tmp_path)
    registered = registry.register_package(_package())

    resolved = registry.resolve(
        _package()["identity"],
        expected_package_sha256=registered["package_sha256"],
    )

    assert resolved["package_sha256"] == registered["package_sha256"]
    assert resolved["fallback_used"] is False
    assert list((tmp_path / "packages").glob("*.json"))


def test_registry_rejects_nearest_version_and_explains_candidate_difference() -> None:
    registry = TechnologyAdapterRegistry()
    registry.register_package(_package("1.0.0"))
    requested = _package("1.0.1")["identity"]

    with pytest.raises(AnalysisError) as caught:
        registry.resolve(requested)

    assert caught.value.code == "TECH_ADAPTER_EXACT_MATCH_NOT_FOUND"
    differences = caught.value.details["candidates"][0]["differences"]
    assert differences == {
        "package_version": {"requested": "1.0.1", "registered": "1.0.0"}
    }


def test_registry_rejects_wildcard_identity() -> None:
    registry = TechnologyAdapterRegistry()
    identity = _package()["identity"] | {"pdk_revision": "latest"}

    with pytest.raises(AnalysisError) as caught:
        registry.resolve(identity)

    assert caught.value.code == "TECH_ADAPTER_FALLBACK_FORBIDDEN"


def test_registry_exact_key_is_immutable() -> None:
    registry = TechnologyAdapterRegistry()
    registry.register_package(_package())

    with pytest.raises(AnalysisError) as caught:
        registry.register_package(_package(compiler="d" * 64))

    assert caught.value.code == "TECH_ADAPTER_EXACT_KEY_CONFLICT"


def test_revocation_is_append_only_and_blocks_resolution(tmp_path: Path) -> None:
    registry = TechnologyAdapterRegistry(tmp_path)
    registered = registry.register_package(_package())
    lifecycle = registry.append_lifecycle_record(
        package_sha256=registered["package_sha256"],
        action="revoked",
        reason="qualification evidence invalidated",
        recorded_at="2026-09-02T00:00:00Z",
        signer_reference="host-trust://review-board",
        signature_sha256="e" * 64,
    )

    with pytest.raises(AnalysisError) as caught:
        registry.resolve(_package()["identity"])

    assert caught.value.code == "TECH_ADAPTER_REVOKED"
    assert (tmp_path / "lifecycle" / f"{lifecycle['record_sha256']}.json").is_file()
    snapshot = registry.snapshot()
    assert snapshot["snapshot"]["lookup_policy"] == "exact_only_no_alias_or_fallback"
    assert (tmp_path / "snapshots" / f"{snapshot['snapshot_sha256']}.json").is_file()
