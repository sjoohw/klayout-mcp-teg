from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import threading

import pytest

from klayout_mcp import file_publication
from klayout_mcp.file_publication import (
    OutputAlreadyExistsError,
    UnsupportedPublicationFilesystemError,
    publication_staging_prefix,
    publication_root_doctor,
    publish_new_directory,
    publish_new_file,
    require_supported_publication_root,
    scavenge_stale_publication_entries,
)


def test_publish_new_file_preserves_preexisting_target(tmp_path: Path) -> None:
    staged = tmp_path / ".staged.gds"
    final = tmp_path / "final.gds"
    staged.write_bytes(b"replacement")
    final.write_bytes(b"original")

    with pytest.raises(OutputAlreadyExistsError):
        publish_new_file(staged, final)

    assert final.read_bytes() == b"original"
    assert staged.read_bytes() == b"replacement"


def test_publish_new_file_has_exactly_one_concurrent_winner(tmp_path: Path) -> None:
    writers = 8
    staged_paths = []
    for index in range(writers):
        staged = tmp_path / f".staged-{index}.gds"
        staged.write_bytes(f"writer-{index}".encode())
        staged_paths.append(staged)
    final = tmp_path / "final.gds"
    barrier = threading.Barrier(writers)

    def publish(staged: Path) -> str:
        barrier.wait()
        try:
            publish_new_file(staged, final)
        except OutputAlreadyExistsError:
            return "already_exists"
        return "published"

    with ThreadPoolExecutor(max_workers=writers) as executor:
        outcomes = list(executor.map(publish, staged_paths))

    assert outcomes.count("published") == 1
    assert outcomes.count("already_exists") == writers - 1
    assert final.read_bytes() in {path.read_bytes() for path in staged_paths}


def test_publish_new_directory_preserves_preexisting_target(tmp_path: Path) -> None:
    staged = tmp_path / ".staged"
    final = tmp_path / "final"
    staged.mkdir()
    final.mkdir()
    (staged / "payload").write_bytes(b"replacement")
    (final / "payload").write_bytes(b"original")

    with pytest.raises(OutputAlreadyExistsError):
        publish_new_directory(staged, final)

    assert (final / "payload").read_bytes() == b"original"
    assert (staged / "payload").read_bytes() == b"replacement"


def test_publish_new_directory_has_exactly_one_concurrent_winner(tmp_path: Path) -> None:
    writers = 8
    staged_paths = []
    for index in range(writers):
        staged = tmp_path / f".staged-dir-{index}"
        staged.mkdir()
        (staged / "payload").write_bytes(f"writer-{index}".encode())
        staged_paths.append(staged)
    final = tmp_path / "final-dir"
    barrier = threading.Barrier(writers)

    def publish(staged: Path) -> str:
        barrier.wait()
        try:
            publish_new_directory(staged, final)
        except OutputAlreadyExistsError:
            return "already_exists"
        return "published"

    with ThreadPoolExecutor(max_workers=writers) as executor:
        outcomes = list(executor.map(publish, staged_paths))

    assert outcomes.count("published") == 1
    assert outcomes.count("already_exists") == writers - 1
    assert (final / "payload").read_bytes() in {
        f"writer-{index}".encode() for index in range(writers)
    }
    assert sum(path.exists() for path in staged_paths) == writers - 1


def test_publication_root_doctor_probes_file_and_directory_commit(tmp_path: Path) -> None:
    report = publication_root_doctor(tmp_path)

    assert report["supported_filesystem"] is True
    assert report["file_create_only_probe"] is True
    assert report["directory_create_only_probe"] is True
    assert report["blocker_code"] is None
    assert list(tmp_path.glob(".klayout-stage-dir-doctor-*")) == []


def test_publication_root_rejects_remote_filesystem(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(file_publication, "publication_filesystem_type", lambda _: "nfs")

    with pytest.raises(UnsupportedPublicationFilesystemError, match="remote"):
        require_supported_publication_root(tmp_path)

    report = publication_root_doctor(tmp_path)
    assert report["supported_filesystem"] is False
    assert report["blocker_code"] == "UNSUPPORTED_PUBLICATION_FILESYSTEM"
    assert report["file_create_only_probe"] is False
    assert report["directory_create_only_probe"] is False


def test_scavenger_removes_only_old_reserved_staging_entries(tmp_path: Path) -> None:
    old_file = tmp_path / f"{publication_staging_prefix('test')}old.gds"
    old_directory = tmp_path / f"{publication_staging_prefix('test', directory=True)}old"
    recent_file = tmp_path / f"{publication_staging_prefix('test')}recent.gds"
    unrelated = tmp_path / ".klayout-stage-file-not-valid"
    old_file.write_bytes(b"old")
    old_directory.mkdir()
    (old_directory / "payload").write_bytes(b"old")
    recent_file.write_bytes(b"recent")
    unrelated.write_bytes(b"user")
    os.utime(old_file, (1, 1))
    os.utime(old_directory, (1, 1))
    os.utime(recent_file, (950, 950))
    os.utime(unrelated, (1, 1))

    report = scavenge_stale_publication_entries(
        tmp_path,
        ttl_seconds=100,
        now=1000,
    )

    assert report["removed_file_count"] == 1
    assert report["removed_directory_count"] == 1
    assert report["skipped_recent_count"] == 1
    assert report["skipped_unsafe_count"] == 1
    assert not old_file.exists()
    assert not old_directory.exists()
    assert recent_file.read_bytes() == b"recent"
    assert unrelated.read_bytes() == b"user"
