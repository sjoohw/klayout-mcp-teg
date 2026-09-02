"""Assembly planner for a fixed 25-Pad TEG with selectable DUT sites."""

from __future__ import annotations

from dataclasses import dataclass, fields
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from .dut_geometry import DutParameters, build_dut_geometry
from .errors import AnalysisError
from .profiles import DEFAULT_TEG_PROFILE


DUT_SITE_COUNT = DEFAULT_TEG_PROFILE.dut_site_count


@dataclass(slots=True)
class TegAssemblyPlan:
    """Validated placement and parameter plan over 21 available DUT sites."""

    padset_path: str
    layermap_path: str
    teg_name: str
    output_gds_path: str
    export_static: bool
    dut_sweep: list[dict[str, Any]]
    total_sites: int = DUT_SITE_COUNT
    selected_sites: tuple[int, ...] = tuple(range(1, DUT_SITE_COUNT + 1))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "production_ready": False,
            "geometry_status": "conceptual_scaffold",
            "padset_path": self.padset_path,
            "layermap_path": self.layermap_path,
            "teg_name": self.teg_name,
            "output_gds_path": self.output_gds_path,
            "export_static": self.export_static,
            "total_sites": self.total_sites,
            "selected_sites": list(self.selected_sites),
            "assembled_sites": len(self.selected_sites),
            "dut_sweep_count": len(self.dut_sweep),
            "warning": (
                "This plan uses synthetic process geometry and is not a production mask."
            ),
        }


def plan_teg_dut_sequence(
    dut_slots: Sequence[dict[str, Any]],
    site_parameter_sets: Sequence[dict[str, Any]],
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate fixed pad roles, landings, parameters, and reusable DUT variants."""

    if len(dut_slots) != DUT_SITE_COUNT:
        raise AnalysisError(
            code="DUT_SITE_COUNT_MISMATCH",
            message=f"Expected 21 DUT slots, but got {len(dut_slots)}.",
            details={"slot_count": len(dut_slots)},
            next_action="Provide a padset analysis result with exactly 21 DUT slots.",
        )


    slots_by_site: dict[int, dict[str, Any]] = {}
    for slot in dut_slots:
        if not isinstance(slot, Mapping):
            raise AnalysisError(
                code="INVALID_DUT_SLOT",
                message="Every DUT slot must be an object returned by padset analysis.",
                details={"slot": slot},
                next_action="Use the dut_slots returned by analyze_padset or analyze_pad_boxes.",
            )
        site_num = slot.get("site")
        if (
            not isinstance(site_num, int)
            or isinstance(site_num, bool)
            or not 1 <= site_num <= DUT_SITE_COUNT
        ):
            raise AnalysisError(
                code="INVALID_DUT_SLOT_SITE",
                message="Every DUT slot must have one integer site number from 1 through 21.",
                details={"site": site_num, "slot": dict(slot)},
                next_action="Regenerate dut_slots from the fixed 21-site padset profile.",
            )
        if site_num in slots_by_site:
            raise AnalysisError(
                code="DUPLICATE_DUT_SLOT_SITE",
                message=f"DUT slot site {site_num} appears more than once.",
                details={"site": site_num},
                next_action="Provide exactly one DUT slot for each site from 1 through 21.",
            )

        expected_mapping = {
            "source_pad": site_num,
            "drain_pad": site_num + 1,
            "gate_pad": (
                DEFAULT_TEG_PROFILE.odd_gate_pad
                if site_num % 2
                else DEFAULT_TEG_PROFILE.even_gate_pad
            ),
            "body_pad": DEFAULT_TEG_PROFILE.body_pad,
        }
        mismatches = {
            name: {"expected": expected, "actual": slot.get(name)}
            for name, expected in expected_mapping.items()
            if slot.get(name) != expected
        }
        if mismatches:
            raise AnalysisError(
                code="DUT_PAD_MAPPING_MISMATCH",
                message=f"DUT slot site {site_num} does not match the fixed pad-role contract.",
                details={"site": site_num, "mismatches": mismatches},
                next_action="Use unmodified dut_slots returned by padset analysis.",
            )

        raw_origin = slot.get("origin_um")
        if (
            isinstance(raw_origin, (str, bytes, bytearray))
            or not isinstance(raw_origin, Sequence)
            or len(raw_origin) != 2
        ):
            raise AnalysisError(
                code="INVALID_DUT_SLOT_ORIGIN",
                message=f"DUT slot site {site_num} must have a two-coordinate origin_um.",
                details={"site": site_num, "origin_um": raw_origin},
                next_action="Use the finite [x, y] origin_um returned by padset analysis.",
            )
        origin = list(raw_origin)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in origin
        ):
            raise AnalysisError(
                code="INVALID_DUT_SLOT_ORIGIN",
                message=f"DUT slot site {site_num} origin_um must contain finite numbers.",
                details={"site": site_num, "origin_um": origin},
                next_action="Replace boolean, text, NaN, or infinite origin coordinates with finite micron values.",
            )
        normalized_slot = dict(slot)
        normalized_slot["origin_um"] = [float(origin[0]), float(origin[1])]
        slots_by_site[site_num] = normalized_slot

    missing_slot_sites = sorted(
        set(range(1, DUT_SITE_COUNT + 1)).difference(slots_by_site)
    )
    if missing_slot_sites:
        raise AnalysisError(
            code="DUT_SLOT_SITE_SET_MISMATCH",
            message="DUT slots must contain every site number from 1 through 21.",
            details={"missing_sites": missing_slot_sites},
            next_action="Regenerate the complete 21-site padset analysis.",
        )

    site_entries = list(site_parameter_sets)
    if not site_entries or len(site_entries) > DUT_SITE_COUNT:
        raise AnalysisError(
            code="INVALID_SITE_PARAMETER_COUNT",
            message=(
                "Provide between 1 and 21 explicit DUT site parameter sets for the "
                "fixed 25-Pad profile."
            ),
            details={"entry_count": len(site_entries), "maximum_site_count": DUT_SITE_COUNT},
            next_action=(
                "Choose the occupied site numbers and provide one {site, parameters} "
                "entry for each selected DUT."
            ),
        )

    sites_seen: set[int] = set()
    entries_by_site: dict[int, dict[str, Any]] = {}
    if defaults is not None and not isinstance(defaults, Mapping):
        raise AnalysisError(
            code="INVALID_DUT_DEFAULTS",
            message="defaults must be a DUT parameter object.",
            details={"defaults": defaults},
            next_action="Provide defaults as a JSON/YAML object.",
        )
    base_defaults = dict(defaults or {})
    supported_parameters = {
        item.name for item in fields(DutParameters)
    } - {"device_window_um", "routing_boundary_um"}

    for entry in site_entries:
        if not isinstance(entry, Mapping):
            raise AnalysisError(
                code="INVALID_SITE_PARAMETER",
                message="Every site parameter entry must be an object.",
                details={"entry": entry},
                next_action="Use {site: N, parameters: {...}} for every site.",
            )
        site_num = entry.get("site")
        if (
            not isinstance(site_num, int)
            or isinstance(site_num, bool)
            or not 1 <= site_num <= DUT_SITE_COUNT
        ):
            raise AnalysisError(
                code="INVALID_SITE_NUMBER",
                message=f"Site number must be an integer between 1 and 21, got {site_num}.",
                details={"entry": dict(entry)},
                next_action="Choose a unique integer site number from 1 through 21.",
            )
        if site_num in sites_seen:
            raise AnalysisError(
                code="DUPLICATE_SITE_NUMBER",
                message=f"Duplicate site number {site_num} found.",
                details={"site": site_num},
                next_action="Keep exactly one parameter entry for each site.",
            )
        sites_seen.add(site_num)

        raw_parameters = entry.get("parameters", {})
        if not isinstance(raw_parameters, Mapping):
            raise AnalysisError(
                code="INVALID_SITE_PARAMETER",
                message=f"Site {site_num} parameters must be an object.",
                details={"site": site_num, "parameters": raw_parameters},
                next_action="Provide DUT parameters as a JSON/YAML object.",
            )
        params_dict = dict(base_defaults)
        params_dict.update(raw_parameters)
        unknown = sorted(set(params_dict).difference(supported_parameters))
        if unknown:
            raise AnalysisError(
                code="UNKNOWN_DUT_PARAMETER",
                message=f"Site {site_num} contains unsupported DUT parameter names.",
                details={
                    "site": site_num,
                    "unknown_parameters": unknown,
                    "supported_parameters": sorted(supported_parameters),
                },
                next_action="Use describe_dut_pcell for supported parameter names.",
            )
        try:
            params = DutParameters(**params_dict)
            params.validate()
            geometry = build_dut_geometry(params)
        except AnalysisError as exc:
            raise AnalysisError(
                code="INVALID_SITE_PARAMETER",
                message=f"Site {site_num} parameter validation failed: {exc.message}",
                details={
                    "site": site_num,
                    "cause_code": exc.code,
                    "parameters": params_dict,
                },
                next_action=exc.next_action,
            ) from exc
        entries_by_site[site_num] = {
            "parameters": params.to_dict(),
            "routed_indices": list(geometry.routed_indices),
            "topology": {
                "array_rows": params.array_rows,
                "array_cols": params.array_cols,
                "pitch_x_um": params.pitch_x_um,
                "pitch_y_um": params.pitch_y_um,
                "routed_device_count": params.routed_device_count,
            },
        }

    selected_sites = sorted(entries_by_site)
    reference_site = selected_sites[0]
    reference_topology = entries_by_site[reference_site]["topology"]
    topology_mismatches = [
        {
            "site": site_num,
            "topology": entries_by_site[site_num]["topology"],
        }
        for site_num in selected_sites[1:]
        if entries_by_site[site_num]["topology"] != reference_topology
        or entries_by_site[site_num]["routed_indices"]
        != entries_by_site[reference_site]["routed_indices"]
    ]
    if topology_mismatches:
        raise AnalysisError(
            code="DUT_TOPOLOGY_MISMATCH",
            message="All selected DUT sites must reuse one array topology and routed-index pattern.",
            details={
                "reference_site": reference_site,
                "reference_topology": reference_topology,
                "mismatches": topology_mismatches,
            },
            next_action=(
                "Keep array rows, columns, pitches, and routed_device_count identical "
                "across the selected DUT sites."
            ),
        )

    variant_ids: dict[str, str] = {}
    ordered_sites: list[dict[str, Any]] = []
    unresolved_landings: list[dict[str, Any]] = []
    for site_num in selected_sites:
        slot = slots_by_site[site_num]
        normalized_params = entries_by_site[site_num]["parameters"]
        parameter_key = json.dumps(normalized_params, sort_keys=True, separators=(",", ":"))
        if parameter_key not in variant_ids:
            variant_ids[parameter_key] = f"VARIANT_{len(variant_ids) + 1:03d}"

        landings = slot.get("landings")
        role_status: dict[str, str] = {}
        for role in ("source", "drain", "gate", "body"):
            if not isinstance(landings, Mapping):
                role_status[role] = "not_analyzed"
                continue
            landing = landings.get(role)
            role_status[role] = (
                str(landing.get("status", "unresolved"))
                if isinstance(landing, Mapping)
                else "unresolved"
            )
        unresolved_roles = [
            role for role, status in role_status.items() if status != "resolved"
        ]
        if unresolved_roles:
            unresolved_landings.append({"site": site_num, "roles": unresolved_roles})

        ordered_sites.append(
            {
                "site": site_num,
                "variant_id": variant_ids[parameter_key],
                "parameters": normalized_params,
                "routed_indices": entries_by_site[site_num]["routed_indices"],
                "transform": {
                    "type": "translation",
                    "origin_um": list(slot["origin_um"]),
                },
                "pad_mapping": {
                    name: slot[name]
                    for name in ("source_pad", "drain_pad", "gate_pad", "body_pad")
                },
                "landing_readiness": {
                    "all_resolved": not unresolved_roles,
                    "role_status": role_status,
                },
            }
        )

    return {
        "ok": True,
        "production_ready": False,
        "total_sites": DUT_SITE_COUNT,
        "available_site_count": DUT_SITE_COUNT,
        "selected_site_count": len(selected_sites),
        "selected_sites": selected_sites,
        "variant_count": len(variant_ids),
        "all_landings_resolved": not unresolved_landings,
        "unresolved_landings": unresolved_landings,
        "shared_topology": reference_topology,
        "shared_routed_indices": entries_by_site[reference_site]["routed_indices"],
        "site_plan": ordered_sites,
        "next_action": (
            "Resolve every reported S/D/G/B padset landing before production assembly."
            if unresolved_landings
            else "Use this deterministic plan only after sample-derived DUT geometry is verified."
        ),
    }


def plan_teg_assembly(
    padset_path: str,
    layermap_path: str,
    dut_sweep: Sequence[dict[str, Any]],
    output_gds_path: str,
    teg_name: str = "TEG_DUT_ARRAY_V1",
    export_static: bool = True,
    expected_site_count: int = DUT_SITE_COUNT,
    dut_site_indices: Sequence[int] | None = None,
) -> TegAssemblyPlan:
    """Validate a non-production, explicitly opted-in conceptual export plan."""

    if expected_site_count != DUT_SITE_COUNT:
        raise AnalysisError(
            code="INVALID_TEG_SITE_COUNT",
            message="The fixed TEG profile requires exactly 21 DUT sites.",
            details={"expected_site_count": expected_site_count},
            next_action="Use the fixed 21-site profile.",
        )

    padset = Path(padset_path).expanduser().resolve()
    layermap = Path(layermap_path).expanduser().resolve()
    output = Path(output_gds_path).expanduser().resolve()
    if not padset.is_file():
        raise AnalysisError(
            code="PADSET_NOT_FOUND",
            message="Padset layout does not exist.",
            details={"padset_path": str(padset)},
            next_action="Provide an existing GDS or OAS path.",
        )
    if not layermap.is_file():
        raise AnalysisError(
            code="LAYERMAP_NOT_FOUND",
            message="Layermap file does not exist.",
            details={"layermap_path": str(layermap)},
            next_action="Provide an existing YAML or JSON layermap path.",
        )
    if output.suffix.casefold() != ".gds":
        raise AnalysisError(
            code="UNSUPPORTED_ASSEMBLY_OUTPUT_FORMAT",
            message="Conceptual assembly currently writes GDS only.",
            details={"output_gds_path": str(output)},
            next_action="Use a new output path ending in .gds.",
        )
    if output in {padset, layermap}:
        raise AnalysisError(
            code="OUTPUT_CONFLICTS_WITH_INPUT",
            message="Assembly output must not overwrite the padset or layermap input.",
            details={
                "output_gds_path": str(output),
                "padset_path": str(padset),
                "layermap_path": str(layermap),
            },
            next_action="Choose a new output path separate from every input file.",
        )
    if output.exists():
        raise AnalysisError(
            code="OUTPUT_ALREADY_EXISTS",
            message="Assembly output already exists and will not be overwritten.",
            details={"output_gds_path": str(output)},
            next_action="Choose a new output path or move the existing artifact first.",
        )
    if not output.parent.is_dir():
        raise AnalysisError(
            code="OUTPUT_DIRECTORY_NOT_FOUND",
            message="Assembly output directory does not exist.",
            details={"output_directory": str(output.parent)},
            next_action="Create the output directory, then retry with a new GDS path.",
        )
    if not isinstance(teg_name, str) or not teg_name.strip():
        raise AnalysisError(
            code="INVALID_TEG_NAME",
            message="teg_name must contain visible text.",
            details={"teg_name": teg_name},
            next_action="Provide a non-empty TEG name.",
        )

    # 1. Keep the 25-Pad/21-candidate-site contract while allowing a selected subset.
    if dut_site_indices is None:
        selected_sites = list(range(1, expected_site_count + 1))
    else:
        selected_sites = list(dut_site_indices)
        invalid_sites = [
            site
            for site in selected_sites
            if isinstance(site, bool)
            or not isinstance(site, int)
            or not 1 <= site <= expected_site_count
        ]
        if not selected_sites or invalid_sites:
            raise AnalysisError(
                code="INVALID_DUT_SITE_SELECTION",
                message="DUT site selection must contain 1 to 21 valid site numbers.",
                details={"selected_sites": selected_sites, "invalid_sites": invalid_sites},
                next_action="Choose one or more integer sites between 1 and 21.",
            )
        if len(set(selected_sites)) != len(selected_sites):
            raise AnalysisError(
                code="DUPLICATE_DUT_SITE_SELECTION",
                message="DUT site selection contains duplicate site numbers.",
                details={"selected_sites": selected_sites},
                next_action="Keep each selected DUT site exactly once.",
            )
    # 2. Validate sweep table count for the selected sites.
    sweep_list = list(dut_sweep)
    if len(sweep_list) == 1:
        sweep_list = [dict(sweep_list[0]) for _ in selected_sites]
    elif len(sweep_list) != len(selected_sites):
        raise AnalysisError(
            code="SWEEP_COUNT_MISMATCH",
            message=(
                "DUT sweep table must provide either 1 common config or exactly one "
                "config per selected DUT site."
            ),
            details={
                "provided_count": len(sweep_list),
                "selected_site_count": len(selected_sites),
                "selected_sites": selected_sites,
            },
            next_action=(
                f"Provide 1 or {len(selected_sites)} parameter dictionaries for the "
                "selected sites."
            ),
        )

    # 3. Validate individual DUT parameter sets.
    supported_parameters = {
        item.name for item in fields(DutParameters)
    } - {"device_window_um", "routing_boundary_um"}
    validated_sweep: list[dict[str, Any]] = []
    for site_idx, item in zip(selected_sites, sweep_list):
        if not isinstance(item, dict):
            raise AnalysisError(
                code="INVALID_SITE_PARAMETER",
                message=f"Site {site_idx} parameter config must be an object.",
                details={"site": site_idx, "config": item},
                next_action="Provide one JSON/YAML parameter object per site.",
            )
        unknown = sorted(set(item).difference(supported_parameters))
        if unknown:
            raise AnalysisError(
                code="UNKNOWN_DUT_PARAMETER",
                message=f"Site {site_idx} contains unsupported parameter names.",
                details={
                    "site": site_idx,
                    "unknown_parameters": unknown,
                    "supported_parameters": sorted(supported_parameters),
                },
                next_action="Use describe_dut_pcell for supported parameter names.",
            )
        try:
            params = DutParameters(**item)
            params.validate()
            build_dut_geometry(params)
            validated_sweep.append({
                "site": site_idx,
                "cell_name": f"DUT_SITE_{site_idx:02d}",
                "parameters": params.to_dict(),
            })
        except AnalysisError as exc:
            raise AnalysisError(
                code="INVALID_SITE_PARAMETER",
                message=f"Site {site_idx} parameter validation failed: {exc}",
                details={
                    "site": site_idx,
                    "config": item,
                    "cause_code": exc.code,
                    "error": str(exc),
                },
                next_action=exc.next_action,
            ) from exc

    return TegAssemblyPlan(
        padset_path=str(padset),
        layermap_path=str(layermap),
        teg_name=teg_name,
        output_gds_path=str(output),
        export_static=export_static,
        dut_sweep=validated_sweep,
        total_sites=expected_site_count,
        selected_sites=tuple(selected_sites),
    )
