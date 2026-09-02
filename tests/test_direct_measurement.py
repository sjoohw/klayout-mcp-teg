import pytest

from klayout_mcp.direct_measurement import analyze_direct_pad_budget
from klayout_mcp.errors import AnalysisError


def _nmos_assignments() -> list[dict[str, object]]:
    return [
        {"dut": "M1", "family": "transistor", "terminal": "G", "net": "G1", "pad": 1},
        {"dut": "M1", "family": "transistor", "terminal": "D", "net": "D1", "pad": 2},
        {"dut": "M1", "family": "transistor", "terminal": "S", "net": "S1", "pad": 3},
        {"dut": "M1", "family": "transistor", "terminal": "B", "net": "BODY", "pad": 25},
        {"dut": "M2", "family": "transistor", "terminal": "G", "net": "G2", "pad": 4},
        {"dut": "M2", "family": "transistor", "terminal": "D", "net": "D2", "pad": 5},
        {"dut": "M2", "family": "transistor", "terminal": "S", "net": "S2", "pad": 6},
        {"dut": "M2", "family": "transistor", "terminal": "B", "net": "BODY", "pad": 25},
    ]


def _nmos_contracts() -> list[dict[str, object]]:
    return [
        {
            "dut": dut,
            "family": "transistor",
            "measurement": "dc_4t",
            "required_terminals": ["G", "D", "S", "B"],
        }
        for dut in ("M1", "M2")
    ]


def test_budget_reports_explicit_shared_body_without_inventing_sharing() -> None:
    result = analyze_direct_pad_budget(
        _nmos_assignments(), pad_count=25, terminal_contracts=_nmos_contracts()
    )

    assert result["status"] == "fits"
    assert result["terminal_count"] == 8
    assert result["distinct_net_count"] == 7
    assert result["used_pad_count"] == 7
    assert len(result["shared_nets"]["BODY"]) == 2
    assert result["implicit_terminal_sharing"] is False
    assert result["terminal_contracts_verified"] is True


def test_missing_body_terminal_fails_the_explicit_dut_contract() -> None:
    records = [
        record
        for record in _nmos_assignments()
        if not (record["dut"] == "M2" and record["terminal"] == "B")
    ]

    with pytest.raises(AnalysisError) as caught:
        analyze_direct_pad_budget(
            records, pad_count=25, terminal_contracts=_nmos_contracts()
        )

    assert caught.value.code == "DIRECT_TERMINAL_CONTRACT_MISMATCH"
    assert caught.value.details["terminal_mismatches"]["M2"]["missing_terminals"] == ["B"]


def test_one_pad_cannot_carry_two_distinct_direct_nets() -> None:
    records = _nmos_assignments()
    records[4]["pad"] = 1

    with pytest.raises(AnalysisError) as caught:
        analyze_direct_pad_budget(records, pad_count=25)

    assert caught.value.code == "DIRECT_PAD_NET_CONFLICT"
    assert caught.value.details["pad_to_conflicting_nets"] == {"1": ["G1", "G2"]}


def test_reserved_pad_cannot_be_assigned() -> None:
    with pytest.raises(AnalysisError) as caught:
        analyze_direct_pad_budget(
            _nmos_assignments(), pad_count=25, reserved_pad_indices=[25]
        )

    assert caught.value.code == "RESERVED_PAD_ASSIGNED"


def test_duplicate_dut_terminal_is_rejected_even_if_net_matches() -> None:
    records = _nmos_assignments()
    records.append(dict(records[0]))

    with pytest.raises(AnalysisError) as caught:
        analyze_direct_pad_budget(records, pad_count=25)

    assert caught.value.code == "DUPLICATE_DUT_TERMINAL_ASSIGNMENT"
