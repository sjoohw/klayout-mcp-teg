from __future__ import annotations

import json
from pathlib import Path

import pytest

from klayout_mcp.errors import AnalysisError
from klayout_mcp.verification_runner import (
    ExternalVerificationRunnerRegistry,
    execute_external_verification,
)


class FixtureRunner:
    runner_id = "fixture-drc-runner"
    runner_version = "1.0"
    trusted = True
    is_mock = True
    supported_kinds = ("drc",)

    def preflight(self, *, kind: str):
        assert kind == "drc"
        return {
            "ok": True,
            "executable": {"name": "fixture-engine", "version": "1", "sha256": "a" * 64},
            "license_available": True,
            "deck": {"id": "fixture-deck", "version": "1", "sha256": "b" * 64},
            "runset": {"id": "fixture-runset", "sha256": "c" * 64},
            "execution_is_out_of_process": True,
            "timeout_enforced": True,
            "resource_limits_enforced": True,
        }

    def run(
        self,
        *,
        kind: str,
        layout_path: str,
        layout_sha256: str,
        output_directory: str,
        invocation_sha256: str,
        timeout_seconds: float,
        resource_limits,
    ):
        assert Path(layout_path).is_file()
        assert timeout_seconds == 30
        assert resource_limits["memory_mb"] == 512
        report = {
            "schema_version": 1,
            "kind": kind,
            "status": "passed",
            "engine": {"name": "fixture-engine", "version": "1"},
            "deck_sha256": "b" * 64,
            "input_layout_sha256": layout_sha256,
            "violation_count": 0,
            "mismatch_count": 0,
            "generated_at": "2026-09-02T00:00:00Z",
            "invocation_sha256": invocation_sha256,
        }
        Path(output_directory, "report.json").write_text(
            json.dumps(report, sort_keys=True), encoding="utf-8"
        )
        return {"ok": True, "report_name": "report.json"}


def test_runner_binds_and_publishes_report_without_signoff(tmp_path: Path) -> None:
    layout = tmp_path / "final.gds"
    layout.write_bytes(b"immutable-layout")
    registry = ExternalVerificationRunnerRegistry(production_mode=False)
    registry.register(FixtureRunner())

    result = execute_external_verification(
        registry=registry,
        runner_id="fixture-drc-runner",
        kind="drc",
        generated_layout_path=layout,
        report_root=tmp_path / "reports",
        timeout_seconds=30,
        resource_limits={"cpu_seconds": 20, "memory_mb": 512, "process_count": 2},
    )

    report = json.loads((tmp_path / "reports" / result["report_name"]).read_text())
    assert result["ok"] is True
    assert report["input_layout_sha256"] == result["layout_sha256"]
    assert report["invocation_sha256"] == result["invocation_sha256"]
    assert result["external_evidence_attached"] is False
    assert result["production_ready"] is False
    assert not list((tmp_path / "reports").glob(".klayout-stage-*"))


def test_production_registry_rejects_nonproduction_runner() -> None:
    registry = ExternalVerificationRunnerRegistry(production_mode=True)

    with pytest.raises(AnalysisError) as caught:
        registry.register(FixtureRunner())

    assert caught.value.code == "NONPRODUCTION_VERIFICATION_RUNNER_FORBIDDEN"


def test_report_provenance_mismatch_fails_closed(tmp_path: Path) -> None:
    class StaleRunner(FixtureRunner):
        runner_id = "stale-runner"

        def run(self, **kwargs):
            result = super().run(**kwargs)
            report_path = Path(kwargs["output_directory"], result["report_name"])
            report = json.loads(report_path.read_text())
            report["input_layout_sha256"] = "0" * 64
            report_path.write_text(json.dumps(report), encoding="utf-8")
            return result

    layout = tmp_path / "final.gds"
    layout.write_bytes(b"layout")
    registry = ExternalVerificationRunnerRegistry(production_mode=False)
    registry.register(StaleRunner())

    with pytest.raises(AnalysisError) as caught:
        execute_external_verification(
            registry=registry,
            runner_id="stale-runner",
            kind="drc",
            generated_layout_path=layout,
            report_root=tmp_path / "reports",
            timeout_seconds=30,
            resource_limits={"cpu_seconds": 20, "memory_mb": 512, "process_count": 2},
        )

    assert caught.value.code == "VERIFICATION_REPORT_PROVENANCE_MISMATCH"
    assert not list((tmp_path / "reports").glob(".klayout-stage-*"))
