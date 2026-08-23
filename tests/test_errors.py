from klayout_mcp.errors import AnalysisError


def test_analysis_error_initializes_base_exception_args() -> None:
    error = AnalysisError(code="TEST_ERROR", message="Actionable failure")

    assert error.args == ("Actionable failure",)
    assert str(error) == "Actionable failure"
