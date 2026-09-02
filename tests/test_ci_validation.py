from __future__ import annotations

import importlib.util
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "write_ci_validation.py"
SPEC = importlib.util.spec_from_file_location("write_ci_validation", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
validation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validation)


def test_validation_record_uses_junit_and_lock_provenance(tmp_path, monkeypatch) -> None:
    junit = tmp_path / "pytest.xml"
    junit.write_text(
        '<testsuites><testsuite tests="7" failures="1" errors="0" skipped="2" time="1.25"/></testsuites>',
        encoding="utf-8",
    )
    monkeypatch.chdir(PROJECT_ROOT)
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.example")
    monkeypatch.setenv("GITHUB_REPOSITORY", "org/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "42")

    record = validation.build_record(
        junit=junit,
        commit="a" * 40,
        job="unit-ubuntu-3.11",
        klayout_version=None,
    )

    assert record["pytest"] == {
        "tests": 7,
        "failures": 1,
        "errors": 0,
        "skipped": 2,
        "time_seconds": 1.25,
        "passed": 4,
    }
    assert record["success"] is False
    assert len(record["dependency_provenance"]["uv_lock_sha256"]) == 64
    assert record["run_url"].endswith("/actions/runs/42")
