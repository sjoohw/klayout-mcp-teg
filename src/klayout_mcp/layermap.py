"""Strict layermap loading. Production layer numbers are never inferred."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from .errors import AnalysisError


@dataclass(frozen=True, slots=True)
class LayerSpec:
    layer: int
    datatype: int

    def to_dict(self) -> dict[str, int]:
        return {"layer": self.layer, "datatype": self.datatype}


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AnalysisError(
            code="INVALID_LAYERMAP",
            message=f"{field} must be a non-negative integer.",
            details={"field": field, "value": value},
            next_action="Use an integer GDS layer and datatype in the supplied layermap.",
        )
    return value


def _parse_layer_spec(name: str, value: object) -> LayerSpec:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        layer, datatype = value
    elif isinstance(value, Mapping):
        if "layer" not in value or "datatype" not in value:
            raise AnalysisError(
                code="INVALID_LAYERMAP",
                message=f"Layer '{name}' must define layer and datatype.",
                details={"name": name, "value": dict(value)},
                next_action="Use [layer, datatype] or {layer: N, datatype: D}.",
            )
        layer, datatype = value["layer"], value["datatype"]
    else:
        raise AnalysisError(
            code="INVALID_LAYERMAP",
            message=f"Layer '{name}' has an unsupported value.",
            details={"name": name, "value": value},
            next_action="Use [layer, datatype] or {layer: N, datatype: D}.",
        )
    return LayerSpec(
        _nonnegative_int(layer, f"layers.{name}.layer"),
        _nonnegative_int(datatype, f"layers.{name}.datatype"),
    )


def load_layermap(
    path: str | Path, *, require_m1: bool = True
) -> dict[str, LayerSpec]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise AnalysisError(
            code="LAYERMAP_NOT_FOUND",
            message="Layermap file does not exist.",
            details={"layermap_path": str(source)},
            next_action="Provide an existing YAML or JSON layermap path.",
        )
    try:
        payload: Any = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise AnalysisError(
            code="LAYERMAP_READ_FAILED",
            message="Layermap could not be read.",
            details={"layermap_path": str(source), "error": str(exc)},
            next_action="Fix the YAML/JSON syntax and UTF-8 encoding.",
        ) from exc

    if not isinstance(payload, Mapping) or not isinstance(payload.get("layers"), Mapping):
        raise AnalysisError(
            code="INVALID_LAYERMAP",
            message="Layermap must contain a layers mapping.",
            details={"layermap_path": str(source)},
            next_action="Add a top-level layers mapping with an explicit m1 entry.",
        )

    result: dict[str, LayerSpec] = {}
    original_names: dict[str, str] = {}
    for raw_name, value in payload["layers"].items():
        if not isinstance(raw_name, str) or not raw_name.strip():
            raise AnalysisError(
                code="INVALID_LAYERMAP",
                message="Layer names must be non-empty strings.",
                details={"name": raw_name},
                next_action="Use a non-empty string key for every layer entry.",
            )
        name = raw_name.strip().casefold()
        if name in result:
            raise AnalysisError(
                code="LAYERMAP_AMBIGUOUS",
                message="Layermap contains duplicate case-insensitive layer names.",
                details={"names": [original_names[name], raw_name]},
                next_action="Keep one unambiguous entry for each layer name.",
            )
        result[name] = _parse_layer_spec(raw_name, value)
        original_names[name] = raw_name

    if require_m1 and "m1" not in result:
        raise AnalysisError(
            code="M1_NOT_IN_LAYERMAP",
            message="Layermap has no explicit m1 entry.",
            details={"available_layers": sorted(result)},
            next_action="Add layers.m1 with the production GDS layer and datatype.",
        )
    return result
