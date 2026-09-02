from klayout_mcp.errors import AnalysisError


def test_analysis_error_initializes_base_exception_args() -> None:
    error = AnalysisError(code="TEST_ERROR", message="Actionable failure")

    assert error.args == ("Actionable failure",)
    assert str(error) == "Actionable failure"


def test_analysis_error_embeds_copyable_fix_payload_in_details() -> None:
    error = AnalysisError(
        code="TEST_ERROR",
        message="Actionable failure",
        details={"field": "profile_name"},
        example_fix_payload={"profile_name": "target_process"},
    )

    assert error.to_result()["details"]["example_fix_payload"] == {
        "profile_name": "target_process"
    }
