"""Pad-budget validation for explicit direct-measurement terminal mappings."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable, Mapping

from .errors import AnalysisError


def analyze_direct_pad_budget(
    assignments: Iterable[Mapping[str, Any]],
    *,
    pad_count: int,
    reserved_pad_indices: Iterable[int] = (),
    terminal_contracts: Iterable[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate explicit DUT-terminal/net/Pad records without inventing net sharing."""

    reserved = list(reserved_pad_indices)
    if any(isinstance(value, bool) or not isinstance(value, int) for value in reserved):
        raise AnalysisError(
            code="INVALID_RESERVED_PAD_INDEX",
            message="Every reserved Pad index must be an integer.",
            details={"reserved_pad_indices": reserved},
            next_action="Use unique integer Pad indices within the declared Pad count.",
        )
    if len(set(reserved)) != len(reserved) or any(
        value < 1 or value > pad_count for value in reserved
    ):
        raise AnalysisError(
            code="INVALID_RESERVED_PAD_INDEX",
            message="Reserved Pad indices must be unique and inside the Pad range.",
            details={"reserved_pad_indices": reserved, "pad_count": pad_count},
            next_action="Use unique indices from 1 through pad_count.",
        )

    records = list(assignments)
    if not records:
        raise AnalysisError(
            code="DIRECT_TERMINAL_ASSIGNMENTS_REQUIRED",
            message="Direct-measurement Pad budgeting requires explicit terminal assignments.",
            details={"required_fields": ["dut", "family", "terminal", "net", "pad"]},
            next_action="Provide one assignment record for every measurable DUT terminal.",
        )

    normalized: list[dict[str, Any]] = []
    seen_terminals: set[tuple[str, str]] = set()
    pad_to_nets: dict[int, set[str]] = defaultdict(set)
    pad_to_terminals: dict[int, list[dict[str, str]]] = defaultdict(list)
    net_to_pads: dict[str, set[int]] = defaultdict(set)
    net_to_terminals: dict[str, list[dict[str, str]]] = defaultdict(list)
    reserved_set = set(reserved)

    for index, record in enumerate(records, start=1):
        if not isinstance(record, Mapping):
            raise AnalysisError(
                code="INVALID_DIRECT_TERMINAL_ASSIGNMENT",
                message="Every terminal assignment must be an object.",
                details={"record_index": index, "record": record},
                next_action="Provide dut, family, terminal, net, and pad fields.",
            )
        strings: dict[str, str] = {}
        for field in ("dut", "family", "terminal", "net"):
            value = record.get(field)
            if not isinstance(value, str) or not value.strip():
                raise AnalysisError(
                    code="INVALID_DIRECT_TERMINAL_ASSIGNMENT",
                    message=f"Terminal assignment field {field} must be a non-empty string.",
                    details={"record_index": index, "field": field, "value": value},
                    next_action="Provide dut, family, terminal, net, and pad explicitly.",
                )
            strings[field] = value.strip()
        pad = record.get("pad")
        if isinstance(pad, bool) or not isinstance(pad, int) or pad < 1 or pad > pad_count:
            raise AnalysisError(
                code="INVALID_DIRECT_TERMINAL_PAD",
                message="A terminal assignment references an invalid Pad index.",
                details={"record_index": index, "pad": pad, "pad_count": pad_count},
                next_action="Assign each terminal to a Pad from 1 through pad_count.",
            )
        if pad in reserved_set:
            raise AnalysisError(
                code="RESERVED_PAD_ASSIGNED",
                message="A direct-measurement terminal uses a reserved Pad.",
                details={"record_index": index, "pad": pad},
                next_action="Move the terminal or remove that Pad from the reserved set.",
            )
        terminal_key = (strings["dut"], strings["terminal"])
        if terminal_key in seen_terminals:
            raise AnalysisError(
                code="DUPLICATE_DUT_TERMINAL_ASSIGNMENT",
                message="A DUT terminal is assigned more than once.",
                details={"dut": terminal_key[0], "terminal": terminal_key[1]},
                next_action="Keep exactly one explicit assignment per DUT terminal.",
            )
        seen_terminals.add(terminal_key)
        terminal_ref = {
            "dut": strings["dut"],
            "family": strings["family"],
            "terminal": strings["terminal"],
        }
        pad_to_nets[pad].add(strings["net"])
        pad_to_terminals[pad].append(terminal_ref)
        net_to_pads[strings["net"]].add(pad)
        net_to_terminals[strings["net"]].append(terminal_ref)
        normalized.append({**strings, "pad": pad})

    pad_conflicts = {
        str(pad): sorted(nets)
        for pad, nets in sorted(pad_to_nets.items())
        if len(nets) > 1
    }
    if pad_conflicts:
        raise AnalysisError(
            code="DIRECT_PAD_NET_CONFLICT",
            message="One Pad cannot directly measure multiple electrically distinct nets.",
            details={"pad_to_conflicting_nets": pad_conflicts},
            next_action="Assign each distinct net to a different Pad or explicitly merge the net names.",
        )

    normalized_contracts: list[dict[str, Any]] = []
    if terminal_contracts is not None:
        contract_by_dut: dict[str, dict[str, Any]] = {}
        for index, contract in enumerate(terminal_contracts, start=1):
            if not isinstance(contract, Mapping):
                raise AnalysisError(
                    code="INVALID_DUT_TERMINAL_CONTRACT",
                    message="Every DUT terminal contract must be an object.",
                    details={"contract_index": index, "contract": contract},
                    next_action="Provide dut, family, measurement, and required_terminals.",
                )
            dut = contract.get("dut")
            family = contract.get("family")
            measurement = contract.get("measurement")
            required = contract.get("required_terminals")
            if any(
                not isinstance(value, str) or not value.strip()
                for value in (dut, family, measurement)
            ) or not isinstance(required, (list, tuple)):
                raise AnalysisError(
                    code="INVALID_DUT_TERMINAL_CONTRACT",
                    message="A DUT terminal contract is incomplete.",
                    details={"contract_index": index, "contract": dict(contract)},
                    next_action="Provide non-empty dut/family/measurement and required_terminals.",
                )
            required_names = list(required)
            if (
                not required_names
                or any(not isinstance(name, str) or not name.strip() for name in required_names)
                or len(set(required_names)) != len(required_names)
            ):
                raise AnalysisError(
                    code="INVALID_DUT_TERMINAL_CONTRACT",
                    message="required_terminals must be a non-empty unique string list.",
                    details={"contract_index": index, "required_terminals": required_names},
                    next_action="List every required DUT terminal exactly once.",
                )
            dut_name = str(dut).strip()
            if dut_name in contract_by_dut:
                raise AnalysisError(
                    code="DUPLICATE_DUT_TERMINAL_CONTRACT",
                    message="A DUT has more than one terminal contract.",
                    details={"dut": dut_name},
                    next_action="Merge the definitions into one explicit DUT contract.",
                )
            normalized_contract = {
                "dut": dut_name,
                "family": str(family).strip(),
                "measurement": str(measurement).strip(),
                "required_terminals": [str(name).strip() for name in required_names],
            }
            contract_by_dut[dut_name] = normalized_contract
            normalized_contracts.append(normalized_contract)

        actual_by_dut: dict[str, dict[str, Any]] = {}
        for record in normalized:
            entry = actual_by_dut.setdefault(
                record["dut"], {"family": record["family"], "terminals": set()}
            )
            if entry["family"] != record["family"]:
                raise AnalysisError(
                    code="DUT_FAMILY_MISMATCH",
                    message="One DUT is assigned to multiple device families.",
                    details={"dut": record["dut"]},
                    next_action="Use one process/device family for each DUT identifier.",
                )
            entry["terminals"].add(record["terminal"])

        uncontracted = sorted(set(actual_by_dut).difference(contract_by_dut))
        missing_duts = sorted(set(contract_by_dut).difference(actual_by_dut))
        terminal_mismatches: dict[str, Any] = {}
        for dut, contract in contract_by_dut.items():
            if dut not in actual_by_dut:
                continue
            actual = actual_by_dut[dut]
            required_set = set(contract["required_terminals"])
            if actual["family"] != contract["family"] or actual["terminals"] != required_set:
                terminal_mismatches[dut] = {
                    "contract_family": contract["family"],
                    "assignment_family": actual["family"],
                    "missing_terminals": sorted(required_set.difference(actual["terminals"])),
                    "unexpected_terminals": sorted(actual["terminals"].difference(required_set)),
                }
        if uncontracted or missing_duts or terminal_mismatches:
            raise AnalysisError(
                code="DIRECT_TERMINAL_CONTRACT_MISMATCH",
                message="Direct terminal assignments do not exactly satisfy the DUT contracts.",
                details={
                    "uncontracted_duts": uncontracted,
                    "contracts_without_assignments": missing_duts,
                    "terminal_mismatches": terminal_mismatches,
                },
                next_action="Make every DUT family and required terminal exactly match its assignments.",
            )

    used_pads = sorted(pad_to_nets)
    available_pads = [
        pad for pad in range(1, pad_count + 1) if pad not in reserved_set
    ]
    unused_pads = [pad for pad in available_pads if pad not in pad_to_nets]
    return {
        "status": "fits",
        "pad_count": pad_count,
        "reserved_pad_indices": sorted(reserved),
        "available_pad_count": len(available_pads),
        "used_pad_count": len(used_pads),
        "unused_pad_count": len(unused_pads),
        "used_pad_indices": used_pads,
        "unused_pad_indices": unused_pads,
        "terminal_count": len(normalized),
        "dut_count": len({record["dut"] for record in normalized}),
        "device_families": sorted({record["family"] for record in normalized}),
        "distinct_net_count": len(net_to_pads),
        "assignments": normalized,
        "terminal_contracts": normalized_contracts,
        "terminal_contracts_verified": terminal_contracts is not None,
        "pad_to_net": {
            str(pad): next(iter(nets)) for pad, nets in sorted(pad_to_nets.items())
        },
        "pad_to_terminals": {
            str(pad): terminals for pad, terminals in sorted(pad_to_terminals.items())
        },
        "shared_nets": {
            net: terminals
            for net, terminals in sorted(net_to_terminals.items())
            if len(terminals) > 1
        },
        "multi_pad_nets": {
            net: sorted(pads)
            for net, pads in sorted(net_to_pads.items())
            if len(pads) > 1
        },
        "implicit_terminal_sharing": False,
    }
