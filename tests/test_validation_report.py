from klayout_mcp.errors import AnalysisError
from klayout_mcp.validation_report import ActionableIssue, ValidationReport


def test_analysis_error_includes_actionable_validation_report() -> None:
    error = AnalysisError(
        code="INPUT_VALUE_OUT_OF_RANGE",
        message="Gate length must be positive.",
        details={
            "stage": "intake",
            "field": "devices[2].parameters.gate_length_nm",
            "value": -1,
            "minimum": 1,
            "unit": "nm",
            "dut_id": "dut-3",
        },
        next_action="Set gate_length_nm to a qualified positive value.",
    )

    report = error.to_result()["validation_report"]

    assert report["retry_stage"] == "intake"
    assert report["mutation_state"] == {
        "source_modified": False,
        "stage_appended": False,
        "geometry_generation_started": False,
        "final_output_promoted": False,
    }
    issue = report["issues"][0]
    assert issue["field_path"] == "/devices/2/parameters/gate_length_nm"
    assert issue["received"] == {
        "redacted": False,
        "value": -1,
        "received_type": "int",
    }
    assert issue["expected"] == {"minimum": 1, "unit": "nm"}
    assert issue["object_identity"] == {"dut_id": "dut-3"}


def test_validation_report_sorts_deduplicates_and_truncates() -> None:
    issues = [
        ActionableIssue(
            code="B",
            category="semantic",
            severity="error",
            stage="intake",
            message="second",
            field_path="/z",
        ),
        ActionableIssue(
            code="A",
            category="schema",
            severity="blocker",
            stage="intake",
            message="first",
            field_path="/a",
        ),
        ActionableIssue(
            code="A",
            category="schema",
            severity="blocker",
            stage="intake",
            message="first",
            field_path="/a",
        ),
    ]

    report = ValidationReport.build(
        summary="two issues",
        issues=issues,
        max_embedded_issues=1,
    ).to_dict()

    assert report["total_issue_count"] == 2
    assert report["issues_truncated"] is True
    assert report["issues"][0]["code"] == "A"


def test_secret_received_value_is_redacted() -> None:
    result = AnalysisError(
        code="INPUT_FIELD_INVALID",
        message="Credential is invalid.",
        details={"field": "deployment.license_token", "value": "top-secret"},
    ).to_result()

    received = result["validation_report"]["issues"][0]["received"]
    assert received == {"redacted": True, "received_type": "str", "length": 10}
