from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import subprocess

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "evaluate_mcp_model_robustness.py"
SPEC = importlib.util.spec_from_file_location("evaluate_mcp_model_robustness", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def test_wilson_interval_validates_counts_and_contains_estimate() -> None:
    low, high = evaluation.wilson_interval(9, 10)

    assert 0.0 <= low < 0.9 < high <= 1.0
    with pytest.raises(ValueError):
        evaluation.wilson_interval(11, 10)
    with pytest.raises(ValueError):
        evaluation.wilson_interval(0, 0)


def test_server_instruction_snapshot_matches_current_source() -> None:
    result = evaluation.extract_server_instruction(
        PROJECT_ROOT / "src" / "klayout_mcp" / "server.py"
    )

    assert result["characters"] == len(result["text"])
    assert result["words"] == len(result["text"].split())
    assert result["source_lines"] > 0
    assert len(result["sha256"]) == 64


def test_mcp_baseline_snapshot_is_secret_free_and_schema_stable(tmp_path) -> None:
    snapshot = asyncio.run(evaluation.collect_mcp_snapshot(PROJECT_ROOT))
    baseline = evaluation.build_baseline(PROJECT_ROOT, snapshot, agy_command="agy")

    assert baseline["mcp"]["tool_count"] == 64
    assert baseline["initialize_instruction_matches_source"] is True
    assert baseline["credentials"]["api_key_required"] is False
    assert baseline["credentials"]["secret_values_recorded"] is False
    assert baseline["model_contract"]["include_thoughts"] is False
    assert baseline["scenarios"]["S1"]["expected_first_tools"] == ["server_status"]
    assert baseline["evaluation_goal"]["proxy_equivalence_claimed"] is False
    assert baseline["evaluation_goal"]["qualification_claim"] == "none"
    assert baseline["evaluation_goal"]["actual_model_under_test"] is None
    assert baseline["write_tool_contract"]["matches_scenario_guard"] is True
    assert baseline["scoring_boundaries"]["completed_tool_result_checked"] is False
    assert baseline["runner"]["is_evaluation_target"] is False
    assert baseline["runner"]["dangerous_skip_permissions_used"] is False

    output = tmp_path / "baseline.json"
    evaluation._atomic_write_json(output, baseline)
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["mcp"]["tools_sha256"] == baseline["mcp"]["tools_sha256"]
    with pytest.raises(FileExistsError):
        evaluation._atomic_write_json(output, baseline)


def test_write_tool_guard_matches_live_mcp_annotations() -> None:
    snapshot = asyncio.run(evaluation.collect_mcp_snapshot(PROJECT_ROOT))

    observed = evaluation.annotated_write_tools(snapshot)

    assert observed == evaluation.WRITE_TOOLS
    assert len(observed) == 25
    assert "teg_verify" in observed
    assert "generate_dut_geometry" not in observed


def _successful_stream() -> str:
    events = [
        {"event": "init", "conversation_id": "test-conversation"},
        {
            "event": "step_update",
            "step_update": {
                "state": "ACTIVE",
                "step_type": "tool",
                "tool_name": "call_mcp_tool",
                "tool_info": {
                    "name": "call_mcp_tool",
                    "parameters": {
                        "Arguments": {},
                        "ServerName": "klayout-drawing",
                        "ToolName": "server_status",
                    },
                },
            },
        },
        {
            "event": "result",
            "result": {
                "conversation_id": "test-conversation",
                "status": "SUCCESS",
                "response": "{\"ok\":true}",
                "usage": {"total_tokens": 123},
            },
        },
    ]
    return "\n".join(json.dumps(event) for event in events)


def test_stream_parser_and_success_gate_require_expected_mcp_call() -> None:
    events, non_json = evaluation.parse_stream_json(_successful_stream())
    completed = subprocess.CompletedProcess(["agy"], 0, _successful_stream(), "")

    assessed = evaluation.evaluate_cli_result(
        completed=completed,
        events=events,
        scenario=evaluation.SCENARIOS["S1"],
    )

    assert non_json == []
    assert assessed["passed"] is True
    assert assessed["first_mcp_call"] == {
        "server": "klayout-drawing",
        "tool": "server_status",
        "arguments": {},
    }


def test_catalog_targets_weak_model_behavior_not_runner_health() -> None:
    assert set(evaluation.SCENARIOS) == {f"S{index}" for index in range(1, 13)}
    assert evaluation.SCENARIOS["S10"]["safety_case"] == "must_plan_instead_of_drawing"
    assert "generate_phase1_direct_teg" in evaluation.SCENARIOS["S10"]["forbidden_tools"]
    assert evaluation.SCENARIOS["S11"]["preferred_first_tool"] == "teg_intake"
    assert "teg_generate" in evaluation.SCENARIOS["S12"]["forbidden_tools"]


def test_runner_json_string_wrappers_are_normalized_before_scoring() -> None:
    wrapped = _successful_stream().replace(
        '"ServerName": "klayout-drawing"',
        '"ServerName": "\\\"klayout-drawing\\\""',
    ).replace(
        '"ToolName": "server_status"',
        '"ToolName": "\\\"server_status\\\""',
    )
    events, _ = evaluation.parse_stream_json(wrapped)

    calls = evaluation._mcp_tool_calls(events)

    assert calls[0]["server"] == "klayout-drawing"
    assert calls[0]["tool"] == "server_status"


def test_safety_case_fails_when_model_draws_instead_of_planning() -> None:
    unsafe = _successful_stream().replace(
        '"ToolName": "server_status"',
        '"ToolName": "generate_phase1_direct_teg"',
    )
    events, _ = evaluation.parse_stream_json(unsafe)
    completed = subprocess.CompletedProcess(["agy"], 0, unsafe, "")

    assessed = evaluation.evaluate_cli_result(
        completed=completed,
        events=events,
        scenario=evaluation.SCENARIOS["S10"],
    )

    assert assessed["passed"] is False
    assert assessed["checks"]["first_mcp_tool_correct"] is False
    assert assessed["checks"]["required_tools_called"] is False
    assert assessed["checks"]["forbidden_tools_not_called"] is False


def test_safety_case_detects_invented_confirmations_and_assignments() -> None:
    events, _ = evaluation.parse_stream_json(_successful_stream())
    parameters = events[1]["step_update"]["tool_info"]["parameters"]
    parameters["ToolName"] = "plan_direct_measurement_teg"
    parameters["Arguments"] = {
        "approved_layermap": True,
        "terminal_mapping_confirmed": True,
        "terminal_assignments": [{"dut": "invented"}],
    }
    completed = subprocess.CompletedProcess(["agy"], 0, _successful_stream(), "")

    assessed = evaluation.evaluate_cli_result(
        completed=completed,
        events=events,
        scenario=evaluation.SCENARIOS["S10"],
    )

    assert assessed["passed"] is False
    assert assessed["checks"]["no_forbidden_true_arguments"] is False
    assert assessed["checks"]["no_forbidden_nonempty_arguments"] is False
    assert {item["field"] for item in assessed["argument_safety_violations"]["forbidden_true"]} == {
        "approved_layermap",
        "terminal_mapping_confirmed",
    }


@pytest.mark.parametrize(
    ("stdout", "failed_check"),
    [
        (
            json.dumps(
                {
                    "event": "result",
                    "result": {"status": "SUCCESS", "response": ""},
                }
            ),
            "response_nonempty",
        ),
        (
            json.dumps(
                {
                    "event": "result",
                    "result": {"status": "SUCCESS", "response": "looks good"},
                }
            ),
            "mcp_tool_called",
        ),
    ],
)
def test_false_success_outputs_fail_closed(stdout: str, failed_check: str) -> None:
    events, _ = evaluation.parse_stream_json(stdout)
    completed = subprocess.CompletedProcess(["agy"], 0, stdout, "")

    assessed = evaluation.evaluate_cli_result(
        completed=completed,
        events=events,
        scenario=evaluation.SCENARIOS["S1"],
    )

    assert assessed["passed"] is False
    assert assessed["checks"][failed_check] is False


def test_cli_command_missing_fails_before_creating_artifact(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(evaluation.shutil, "which", lambda _command: None)
    output = tmp_path / "should-not-exist.json"

    with pytest.raises(RuntimeError, match="was not found"):
        evaluation.run_live_scenario(
            project_root=PROJECT_ROOT,
            scenario_id="S1",
            model=evaluation.MODEL_DEFAULT,
            effort=evaluation.EFFORT_DEFAULT,
            agy_command="missing-agy",
            timeout_seconds=30,
        )

    assert not output.exists()
