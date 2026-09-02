import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.pcellizer_contract import (
    build_selection_manifest,
    build_source_layout_identity,
    normalize_occurrence_path,
)
from klayout_mcp.pcellizer_snapshot import (
    create_pcellizer_snapshot_package,
    inspect_pcellizer_snapshot_package,
    recover_pcellizer_snapshot_source,
)
from klayout_mcp.workflow_manifest import canonical_json_bytes, canonical_sha256


HASH_A = "a" * 64
HASH_B = "b" * 64


def _capture(source_path: Path) -> dict:
    source = build_source_layout_identity(
        str(source_path), top_cell="TOP", dbu_um=0.001
    )
    occurrence = normalize_occurrence_path(top_cell="TOP", segments=[])
    manifest = build_selection_manifest(
        source=source,
        occurrence_path=occurrence,
        layer=10,
        datatype=0,
        shape_fingerprint_sha256=HASH_A,
        neighborhood_fingerprint_sha256=HASH_B,
        edge_dbu=[[0, 0], [100, 0]],
    )
    capture = {
        "schema_version": 1,
        "kind": "PCellizerParameterCapture",
        "source": source,
        "ruler": {"ruler_dbu": [[0, 0], [100, 0]]},
        "selected_shapes": [],
        "endpoint_manifests": [{"endpoint_index": 0, "manifest": manifest}],
        "scope": "current_occurrence",
        "selection_mode": "explicit_shapes_and_ruler",
        "edge_snap": "exact_dbu",
        "source_layout_modified": False,
        "flattening_performed": False,
        "production_ready": False,
    }
    capture["parameter_capture_sha256"] = canonical_sha256(capture)
    return capture


def test_snapshot_recovers_exact_bytes_after_original_is_removed(tmp_path) -> None:
    source_path = tmp_path / "original.gds"
    source_bytes = b"standalone-hierarchical-layout-snapshot"
    source_path.write_bytes(source_bytes)
    result = create_pcellizer_snapshot_package(
        capture=_capture(source_path),
        package_root=str(tmp_path / "store"),
        session_id="dut-a",
    )
    package_dir = Path(result["package_dir"])
    source_path.unlink()

    recovered_path = tmp_path / "recovered.gds"
    recovered = recover_pcellizer_snapshot_source(
        package_dir=str(package_dir), output_path=str(recovered_path)
    )

    assert recovered_path.read_bytes() == source_bytes
    assert recovered["layout_sha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert recovered["source_runtime_dependency_used"] is False
    assert result["manifest"]["embedded_source"]["external_runtime_dependency"] is False
    assert result["manifest"]["flattening_performed"] is False


def test_snapshot_is_content_addressed_and_idempotent(tmp_path) -> None:
    source_path = tmp_path / "source.gds"
    source_path.write_bytes(b"same-layout")
    capture = _capture(source_path)

    first = create_pcellizer_snapshot_package(
        capture=capture, package_root=str(tmp_path / "store")
    )
    second = create_pcellizer_snapshot_package(
        capture=capture, package_root=str(tmp_path / "store")
    )

    assert first["package_dir"] == second["package_dir"]
    assert (
        first["manifest"]["snapshot_package_sha256"]
        == second["manifest"]["snapshot_package_sha256"]
    )


def test_snapshot_rejects_source_changed_after_capture(tmp_path) -> None:
    source_path = tmp_path / "source.gds"
    source_path.write_bytes(b"version-one")
    capture = _capture(source_path)
    source_path.write_bytes(b"version-two")

    with pytest.raises(AnalysisError) as error:
        create_pcellizer_snapshot_package(
            capture=capture, package_root=str(tmp_path / "store")
        )

    assert error.value.code == "STALE_PCELLIZER_SOURCE"


def test_snapshot_recovery_rejects_embedded_source_tampering(tmp_path) -> None:
    source_path = tmp_path / "source.oas"
    source_path.write_bytes(b"oas-snapshot")
    result = create_pcellizer_snapshot_package(
        capture=_capture(source_path), package_root=str(tmp_path / "store")
    )
    package_dir = Path(result["package_dir"])
    (package_dir / "source.oas").write_bytes(b"tampered")

    with pytest.raises(AnalysisError) as error:
        recover_pcellizer_snapshot_source(
            package_dir=str(package_dir), output_path=str(tmp_path / "recovered.oas")
        )

    assert error.value.code == "PCELLIZER_SNAPSHOT_ARTIFACT_HASH_MISMATCH"


def test_snapshot_revision_requires_existing_same_session_parent(tmp_path) -> None:
    source_path = tmp_path / "source.gds"
    source_path.write_bytes(b"revision-layout")
    capture = _capture(source_path)
    store = tmp_path / "store"
    first = create_pcellizer_snapshot_package(
        capture=capture, package_root=str(store), session_id="dut-session"
    )
    parent = first["manifest"]["snapshot_package_sha256"]

    second = create_pcellizer_snapshot_package(
        capture=capture,
        package_root=str(store),
        session_id="dut-session",
        parent_revision_sha256=parent,
    )

    assert second["manifest"]["parent_revision_sha256"] == parent
    assert second["manifest"]["snapshot_package_sha256"] != parent

    with pytest.raises(AnalysisError) as missing:
        create_pcellizer_snapshot_package(
            capture=capture,
            package_root=str(store),
            session_id="dut-session",
            parent_revision_sha256="f" * 64,
        )
    assert missing.value.code == "PCELLIZER_PARENT_REVISION_NOT_FOUND"

    with pytest.raises(AnalysisError) as wrong_session:
        create_pcellizer_snapshot_package(
            capture=capture,
            package_root=str(store),
            session_id="other-session",
            parent_revision_sha256=parent,
        )
    assert wrong_session.value.code == "PCELLIZER_PARENT_SESSION_MISMATCH"


def test_concurrent_same_hash_directory_collision_is_idempotent(
    tmp_path, monkeypatch
) -> None:
    source_path = tmp_path / "source.gds"
    source_path.write_bytes(b"concurrent-layout")
    capture = _capture(source_path)
    real_replace = os.replace

    def competing_replace(staging, final):
        shutil.copytree(staging, final)
        raise PermissionError("simulated Windows directory collision")

    monkeypatch.setattr("klayout_mcp.pcellizer_snapshot.os.replace", competing_replace)
    result = create_pcellizer_snapshot_package(
        capture=capture, package_root=str(tmp_path / "store")
    )
    monkeypatch.setattr("klayout_mcp.pcellizer_snapshot.os.replace", real_replace)

    assert result["ok"] is True
    assert Path(result["package_dir"]).is_dir()


def test_snapshot_inspection_rejects_hash_valid_path_traversal_manifest(tmp_path) -> None:
    source_path = tmp_path / "source.gds"
    source_path.write_bytes(b"safe-layout")
    result = create_pcellizer_snapshot_package(
        capture=_capture(source_path), package_root=str(tmp_path / "store")
    )
    manifest_path = Path(result["package_dir"]) / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.pop("snapshot_package_sha256")
    manifest["embedded_source"]["filename"] = "../../outside.gds"
    manifest["snapshot_package_sha256"] = canonical_sha256(manifest)
    manifest_path.write_bytes(canonical_json_bytes(manifest))

    with pytest.raises(AnalysisError) as error:
        inspect_pcellizer_snapshot_package(package_dir=result["package_dir"])

    assert error.value.code == "INVALID_PCELLIZER_SNAPSHOT_SCHEMA"


def test_snapshot_inspection_rejects_non_object_manifest(tmp_path) -> None:
    package = tmp_path / "bad-package"
    package.mkdir()
    (package / "manifest.json").write_text("[]", encoding="utf-8")
    (package / "capture.json").write_text("{}", encoding="utf-8")

    with pytest.raises(AnalysisError) as error:
        inspect_pcellizer_snapshot_package(package_dir=str(package))

    assert error.value.code == "INVALID_PCELLIZER_SNAPSHOT_SCHEMA"


def test_snapshot_rejects_modified_or_flattened_capture_state(tmp_path) -> None:
    source_path = tmp_path / "source.gds"
    source_path.write_bytes(b"capture-state")
    capture = _capture(source_path)
    capture.pop("parameter_capture_sha256")
    capture["flattening_performed"] = True
    capture["parameter_capture_sha256"] = canonical_sha256(capture)

    with pytest.raises(AnalysisError) as error:
        create_pcellizer_snapshot_package(
            capture=capture, package_root=str(tmp_path / "store")
        )

    assert error.value.code == "INVALID_PCELLIZER_CAPTURE_STATE"


def test_snapshot_recovery_rejects_format_mismatch_and_directory_target(tmp_path) -> None:
    source_path = tmp_path / "source.gds"
    source_path.write_bytes(b"recovery-format")
    result = create_pcellizer_snapshot_package(
        capture=_capture(source_path), package_root=str(tmp_path / "store")
    )

    with pytest.raises(AnalysisError) as suffix_error:
        recover_pcellizer_snapshot_source(
            package_dir=result["package_dir"], output_path=str(tmp_path / "wrong.oas")
        )
    assert suffix_error.value.code == "PCELLIZER_RECOVERY_FORMAT_MISMATCH"

    target_directory = tmp_path / "directory.gds"
    target_directory.mkdir()
    with pytest.raises(AnalysisError) as directory_error:
        recover_pcellizer_snapshot_source(
            package_dir=result["package_dir"], output_path=str(target_directory)
        )
    assert directory_error.value.code == "PCELLIZER_RECOVERY_TARGET_IS_DIRECTORY"
