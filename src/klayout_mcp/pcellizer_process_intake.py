"""Layermap-driven, user-confirmed process inputs for PCellizer geometry."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

from .errors import AnalysisError
from .layermap import load_layermap
from .workflow_manifest import canonical_sha256


def _fail(code: str, message: str, **details: Any) -> None:
    raise AnalysisError(
        code=code,
        message=message,
        details={**details, "production_ready": False},
        next_action="Correct the layermap or provide the explicitly requested process value.",
    )


def _positive(value: Any, *, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) <= 0
    ):
        _fail(
            "INVALID_PCELLIZER_PROCESS_INPUT",
            f"{field} must be a finite positive number.",
            field=field,
            value=value,
        )
    return float(value)


def _optional_identity(value: Any, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        _fail(
            "INVALID_PCELLIZER_PROCESS_INPUT",
            f"{field} must be a non-empty string when provided.",
            field=field,
        )
    return value.strip()


def plan_pcellizer_process_inputs(
    *,
    layermap_path: str,
    process_name: str | None = None,
    process_version: str | None = None,
    layout_dbu_um: float | None = None,
    manufacturing_grid_um: float | None = None,
    editable_layer_roles: Sequence[str] | None = None,
    layer_rules: Mapping[str, Mapping[str, Any]] | None = None,
    modified_cut_layer_roles: Sequence[str] | None = None,
    connectivity: Sequence[Mapping[str, Any]] | None = None,
    enclosure_rules: Mapping[str, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a rule-profile draft without inferring connectivity or DRC facts."""

    path = Path(layermap_path).expanduser().resolve()
    layers = load_layermap(path, require_m1=False)
    layer_payload = {
        role: spec.to_dict() for role, spec in sorted(layers.items())
    }
    name = _optional_identity(process_name, field="process_name")
    version = _optional_identity(process_version, field="process_version")
    dbu = None if layout_dbu_um is None else _positive(layout_dbu_um, field="layout_dbu_um")
    grid = (
        None
        if manufacturing_grid_um is None
        else _positive(manufacturing_grid_um, field="manufacturing_grid_um")
    )
    if dbu is not None and grid is not None:
        ratio = grid / dbu
        if not math.isclose(ratio, round(ratio), rel_tol=0.0, abs_tol=1e-9):
            _fail(
                "PCELLIZER_GRID_NOT_ON_DBU",
                "manufacturing_grid_um must be an integer multiple of layout_dbu_um.",
                layout_dbu_um=dbu,
                manufacturing_grid_um=grid,
            )

    editable = [] if editable_layer_roles is None else list(editable_layer_roles)
    if any(not isinstance(role, str) or not role.strip() for role in editable):
        _fail(
            "INVALID_PCELLIZER_EDITABLE_LAYERS",
            "editable_layer_roles must contain non-empty layermap role names.",
        )
    editable = list(dict.fromkeys(role.strip().casefold() for role in editable))
    unknown_editable = sorted(set(editable).difference(layers))
    if unknown_editable:
        _fail(
            "PCELLIZER_EDITABLE_LAYER_NOT_MAPPED",
            "Every editable layer must exist in the supplied layermap.",
            unknown_layer_roles=unknown_editable,
        )

    supplied_rules = {} if layer_rules is None else dict(layer_rules)
    unknown_rules = sorted(set(supplied_rules).difference(layers))
    if unknown_rules:
        _fail(
            "PCELLIZER_RULE_LAYER_NOT_MAPPED",
            "Layer rules reference roles absent from the layermap.",
            unknown_layer_roles=unknown_rules,
        )
    normalized_rules: dict[str, dict[str, float]] = {}
    missing_questions: list[dict[str, Any]] = []
    for role in editable:
        raw = supplied_rules.get(role)
        if not isinstance(raw, Mapping):
            missing_questions.append(
                {
                    "id": f"layer_rules.{role}",
                    "question": (
                        f"Provide {role} min_width_um, min_space_um, and min_area_um2. "
                        "Provide project_max_width_um only when the project imposes one."
                    ),
                    "reason": "required_before_geometry_export",
                }
            )
            continue
        normalized = {
            "min_width_um": _positive(
                raw.get("min_width_um"), field=f"layer_rules.{role}.min_width_um"
            ),
            "min_space_um": _positive(
                raw.get("min_space_um"), field=f"layer_rules.{role}.min_space_um"
            ),
            "min_area_um2": _positive(
                raw.get("min_area_um2"), field=f"layer_rules.{role}.min_area_um2"
            ),
        }
        if (
            raw.get("project_max_width_um") is not None
            and raw.get("profile_max_width_um") is not None
        ):
            _fail(
                "PCELLIZER_DUPLICATE_PROJECT_MAX_WIDTH",
                "Use project_max_width_um; do not provide the legacy profile_max_width_um alias too.",
                layer_role=role,
            )
        raw_maximum = raw.get("project_max_width_um", raw.get("profile_max_width_um"))
        if raw_maximum is not None:
            normalized["project_max_width_um"] = _positive(
                raw_maximum,
                field=f"layer_rules.{role}.project_max_width_um",
            )
            if normalized["project_max_width_um"] < normalized["min_width_um"]:
                _fail(
                    "PCELLIZER_MAX_WIDTH_BELOW_MINIMUM",
                    "project_max_width_um cannot be smaller than min_width_um.",
                    layer_role=role,
                )
        normalized_rules[role] = normalized

    cut_roles = (
        [] if modified_cut_layer_roles is None else list(modified_cut_layer_roles)
    )
    if any(not isinstance(role, str) or not role.strip() for role in cut_roles):
        _fail(
            "INVALID_PCELLIZER_CUT_LAYERS",
            "modified_cut_layer_roles must contain non-empty layermap roles.",
        )
    cut_roles = list(dict.fromkeys(role.strip().casefold() for role in cut_roles))
    unknown_cuts = sorted(set(cut_roles).difference(layers))
    if unknown_cuts:
        _fail(
            "PCELLIZER_CUT_LAYER_NOT_MAPPED",
            "Every modified cut layer must exist in the supplied layermap.",
            unknown_layer_roles=unknown_cuts,
        )
    normalized_connectivity: list[dict[str, Any]] = []
    seen_cuts: set[str] = set()
    for index, item in enumerate([] if connectivity is None else connectivity):
        if not isinstance(item, Mapping):
            _fail(
                "INVALID_PCELLIZER_CONNECTIVITY",
                "Connectivity entries must be explicit objects.",
                index=index,
            )
        lower = str(item.get("lower_layer_role", "")).strip().casefold()
        cut = str(item.get("cut_layer_role", "")).strip().casefold()
        upper = str(item.get("upper_layer_role", "")).strip().casefold()
        if not lower or not cut or not upper or any(
            role not in layers for role in (lower, cut, upper)
        ):
            _fail(
                "INVALID_PCELLIZER_CONNECTIVITY",
                "Connectivity must explicitly name mapped lower, cut, and upper roles.",
                index=index,
            )
        if cut in seen_cuts:
            _fail(
                "AMBIGUOUS_PCELLIZER_CONNECTIVITY",
                "A modified cut role must have one explicit connectivity definition.",
                cut_layer_role=cut,
            )
        seen_cuts.add(cut)
        normalized_connectivity.append(
            {
                "lower_layer_role": lower,
                "cut_layer_role": cut,
                "upper_layer_role": upper,
                "source": "user_confirmed",
                "inferred_from_layer_name": False,
            }
        )
    for role in sorted(set(cut_roles).difference(seen_cuts)):
        missing_questions.append(
            {
                "id": f"connectivity.{role}",
                "question": (
                    f"Which mapped lower and upper layers does cut layer {role} connect? "
                    "Also provide enclosure rules before this cut geometry is modified."
                ),
                "reason": "connectivity_is_never_inferred_from_names",
            }
        )

    supplied_enclosures = {} if enclosure_rules is None else dict(enclosure_rules)
    unknown_enclosures = sorted(set(supplied_enclosures).difference(layers))
    if unknown_enclosures:
        _fail(
            "PCELLIZER_ENCLOSURE_LAYER_NOT_MAPPED",
            "Enclosure rules reference cut roles absent from the layermap.",
            unknown_layer_roles=unknown_enclosures,
        )
    normalized_enclosures: dict[str, dict[str, float]] = {}
    for role in cut_roles:
        raw = supplied_enclosures.get(role)
        if not isinstance(raw, Mapping):
            missing_questions.append(
                {
                    "id": f"enclosure_rules.{role}",
                    "question": (
                        f"Provide lower_enclosure_um and upper_enclosure_um for modified cut layer {role}."
                    ),
                    "reason": "required_before_cut_geometry_export",
                }
            )
            continue
        normalized_enclosures[role] = {
            "lower_enclosure_um": _positive(
                raw.get("lower_enclosure_um"),
                field=f"enclosure_rules.{role}.lower_enclosure_um",
            ),
            "upper_enclosure_um": _positive(
                raw.get("upper_enclosure_um"),
                field=f"enclosure_rules.{role}.upper_enclosure_um",
            ),
        }

    fixed_questions = [
        ("process_name", name, "Provide the process/profile name."),
        ("process_version", version, "Provide the exact process/profile version."),
        ("layout_dbu_um", dbu, "Confirm the source layout DBU in microns."),
        (
            "manufacturing_grid_um",
            grid,
            "Provide the manufacturing snap grid in microns; DBU alone does not prove it.",
        ),
        (
            "editable_layer_roles",
            editable if editable else None,
            "Select which layermap roles this PCell recipe may modify.",
        ),
    ]
    for identifier, value, question in fixed_questions:
        if value is None:
            missing_questions.insert(
                0,
                {
                    "id": identifier,
                    "question": question,
                    "reason": "required_before_geometry_export",
                },
            )

    layermap_bytes = path.read_bytes()
    draft = {
        "schema_version": 1,
        "kind": "PCellizerProcessInputDraft",
        "process": {"name": name, "version": version},
        "layermap": {
            "path": str(path),
            "sha256": hashlib.sha256(layermap_bytes).hexdigest(),
            "layers": layer_payload,
            "semantic_roles_user_supplied": True,
        },
        "layout_dbu_um": dbu,
        "manufacturing_grid_um": grid,
        "editable_layer_roles": editable,
        "layer_rules": normalized_rules,
        "modified_cut_layer_roles": cut_roles,
        "connectivity": normalized_connectivity,
        "enclosure_rules": normalized_enclosures,
        "connectivity_policy": {
            "infer_from_layer_names": False,
            "techfile_auto_import": False,
            "drc_auto_extract": False,
            "explicit_user_confirmation_required_for_modified_cuts": True,
        },
        "missing_questions": missing_questions,
        "status": "ready_for_geometry_export" if not missing_questions else "needs_user_input",
        "production_ready": False,
    }
    draft["process_input_draft_sha256"] = canonical_sha256(draft)
    return draft
