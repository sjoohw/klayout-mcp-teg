"""Deterministic CSV/Excel-paste split-table planning for PCellizer."""

from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
import hashlib
import io
import os
from pathlib import Path
import re
from typing import Any, Mapping

from .errors import AnalysisError
from .pcellizer_recipe import validate_pcellizer_single_shape_recipe
from .workflow_manifest import canonical_sha256, immutable_json_copy


BATCH_PLAN_SCHEMA_VERSION = 1
BATCH_PLAN_KIND = "PCellizerSplitBatchPlan"
SPLIT_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
MAX_TABLE_BYTES = 4 * 1024 * 1024


def _fail(code: str, message: str, **details: Any) -> None:
    raise AnalysisError(
        code=code,
        message=message,
        details={**details, "production_ready": False},
        next_action="Correct the split table and run the read-only batch planner again.",
    )


def _stable_table_bytes(table_path: str) -> tuple[bytes, str]:
    path = Path(table_path).expanduser().resolve()
    if not path.is_file():
        _fail("PCELLIZER_SPLIT_TABLE_NOT_FOUND", "Split table file does not exist.", table_path=str(path))
    if path.suffix.lower() not in {".csv", ".tsv", ".txt"}:
        _fail(
            "UNSUPPORTED_PCELLIZER_SPLIT_TABLE_FORMAT",
            "Use CSV, TSV, text, or paste the table copied from Excel.",
            suffix=path.suffix.lower(),
        )
    try:
        before = path.stat()
        data = path.read_bytes()
        after = path.stat()
    except OSError as exc:
        _fail(
            "PCELLIZER_SPLIT_TABLE_READ_FAILED",
            "Split table could not be read.",
            error_type=type(exc).__name__,
        )
    if (
        before.st_size != after.st_size
        or before.st_mtime_ns != after.st_mtime_ns
        or len(data) != before.st_size
    ):
        _fail("PCELLIZER_SPLIT_TABLE_CHANGED", "Split table changed while it was being read.")
    return data, str(path)


def _table_input(*, table_path: str | None, table_text: str | None) -> tuple[str, dict[str, Any]]:
    if (table_path is None) == (table_text is None):
        _fail(
            "PCELLIZER_SPLIT_TABLE_INPUT_EXCLUSIVE",
            "Provide exactly one of table_path or table_text.",
        )
    if table_path is not None:
        raw, path = _stable_table_bytes(table_path)
        source_kind = "file"
        source_reference = path
    else:
        if not isinstance(table_text, str) or not table_text.strip():
            _fail("PCELLIZER_SPLIT_TABLE_EMPTY", "Pasted split table is empty.")
        raw = table_text.encode("utf-8")
        source_kind = "excel_paste_or_text"
        source_reference = None
    if len(raw) > MAX_TABLE_BYTES:
        _fail(
            "PCELLIZER_SPLIT_TABLE_TOO_LARGE",
            "Split table exceeds the bounded parser input size.",
            max_bytes=MAX_TABLE_BYTES,
            actual_bytes=len(raw),
        )
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        _fail(
            "PCELLIZER_SPLIT_TABLE_ENCODING",
            "Split table must be UTF-8 or UTF-8 with BOM.",
            error_type=type(exc).__name__,
        )
    return text, {
        "kind": source_kind,
        "reference": source_reference,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw),
    }


def _delimiter(text: str) -> str:
    header = next((line for line in text.splitlines() if line.strip()), "")
    if "\t" in header:
        return "\t"
    counts = {delimiter: header.count(delimiter) for delimiter in (",", ";", "|")}
    maximum = max(counts.values(), default=0)
    if maximum == 0:
        return ","
    winners = [item for item, count in counts.items() if count == maximum and count > 0]
    if len(winners) != 1:
        _fail(
            "PCELLIZER_SPLIT_TABLE_DELIMITER",
            "Could not determine one unambiguous delimiter from the header.",
            delimiter_counts=counts,
        )
    return winners[0]


def _parameter_header(parameter_name: str, header: str) -> str | None:
    compact = re.sub(r"[\s_]+", "", header).casefold().replace("(", "[").replace(")", "]")
    normalized_name = re.sub(r"[\s_]+", "", parameter_name).casefold()
    forms = {
        normalized_name: "um",
        f"{normalized_name}um": "um",
        f"{normalized_name}[um]": "um",
        f"{normalized_name}nm": "nm",
        f"{normalized_name}[nm]": "nm",
    }
    return forms.get(compact)


def _safe_output_filename(value: str, *, split_id: str) -> str:
    name = value.strip() if value.strip() else f"{split_id}.gds"
    if Path(name).name != name or "/" in name or "\\" in name:
        _fail("UNSAFE_PCELLIZER_OUTPUT_FILENAME", "Output filename must be a basename only.", value=value)
    if not name.lower().endswith(".gds"):
        _fail("UNSUPPORTED_PCELLIZER_BATCH_OUTPUT", "MVP batch output filenames must end in .gds.", value=value)
    primary_stem = name.split(".", 1)[0]
    if (
        not primary_stem
        or primary_stem.upper() in WINDOWS_RESERVED_NAMES
        or any(character in name for character in '<>:"|?*')
        or name.endswith((" ", "."))
        or len(name) > 120
    ):
        _fail("UNSAFE_PCELLIZER_OUTPUT_FILENAME", "Output filename is not portable or filesystem-safe.", value=value)
    return name


def _parameter_value_dbu(raw: str, *, unit: str, parameter: Mapping[str, Any]) -> int:
    try:
        value = Decimal(raw.strip())
    except InvalidOperation:
        _fail("INVALID_PCELLIZER_SPLIT_VALUE", "Parameter value must be a decimal number.", value=raw)
    if not value.is_finite() or value <= 0:
        _fail("INVALID_PCELLIZER_SPLIT_VALUE", "Parameter value must be finite and positive.", value=raw)
    value_um = value / Decimal(1000) if unit == "nm" else value
    dbu = Decimal(str(parameter["dbu_um"]))
    ratio = value_um / dbu
    if ratio != ratio.to_integral_value():
        _fail("PCELLIZER_SPLIT_VALUE_OFF_DBU", "Parameter value is not DBU-aligned.", value=raw, unit=unit)
    value_dbu = int(ratio)
    minimum = int(parameter["min_dbu"])
    maximum = int(parameter["max_dbu"])
    step = int(parameter["step_dbu"])
    grid = int(parameter["manufacturing_grid_dbu"])
    if value_dbu < minimum or value_dbu > maximum:
        _fail(
            "PCELLIZER_SPLIT_VALUE_OUT_OF_RANGE",
            "Parameter value lies outside the confirmed intent bounds.",
            value_dbu=value_dbu,
            min_dbu=minimum,
            max_dbu=maximum,
        )
    if (value_dbu - minimum) % step != 0 or value_dbu % grid != 0:
        _fail(
            "PCELLIZER_SPLIT_VALUE_OFF_LATTICE",
            "Parameter value is not on the confirmed step/manufacturing-grid lattice.",
            value_dbu=value_dbu,
            step_dbu=step,
            manufacturing_grid_dbu=grid,
        )
    return value_dbu


def plan_pcellizer_split_batch(
    *,
    recipe: Mapping[str, Any],
    table_path: str | None = None,
    table_text: str | None = None,
    max_rows: int = 1000,
) -> dict[str, Any]:
    """Parse one CSV/Excel-paste table and bind every row to one recipe."""

    validated_recipe = validate_pcellizer_single_shape_recipe(recipe)
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 1 or max_rows > 10000:
        _fail("INVALID_PCELLIZER_SPLIT_ROW_LIMIT", "max_rows must be between 1 and 10000.")
    text, source = _table_input(table_path=table_path, table_text=table_text)
    delimiter = _delimiter(text)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
    try:
        raw_header = next(reader)
    except (StopIteration, csv.Error):
        _fail("PCELLIZER_SPLIT_TABLE_EMPTY", "Split table has no header row.")
    header = [item.strip() for item in raw_header]
    normalized_header = [re.sub(r"\s+", "", item).casefold() for item in header]
    if (
        not header
        or any(not item for item in header)
        or len(set(normalized_header)) != len(header)
    ):
        _fail("INVALID_PCELLIZER_SPLIT_HEADER", "Headers must be non-empty and unique.", header=header)
    parameter = validated_recipe["parameter"]
    parameter_name = parameter["parameter_name"]
    parameter_columns = [
        (index, _parameter_header(parameter_name, name))
        for index, name in enumerate(header)
        if _parameter_header(parameter_name, name) is not None
    ]
    if len(parameter_columns) != 1:
        _fail(
            "PCELLIZER_SPLIT_PARAMETER_COLUMN_REQUIRED",
            "Table must contain exactly one explicit column for the compiled parameter.",
            parameter_name=parameter_name,
            accepted_headers=[
                parameter_name,
                f"{parameter_name}_um",
                f"{parameter_name}[um]",
                f"{parameter_name}_nm",
                f"{parameter_name}[nm]",
            ],
        )
    allowed = {"split_id", "output_filename"}
    unknown = [
        name
        for name, normalized in zip(header, normalized_header)
        if normalized not in allowed
        and not normalized.startswith("meta.")
        and _parameter_header(parameter_name, name) is None
    ]
    if unknown:
        _fail(
            "UNKNOWN_PCELLIZER_SPLIT_COLUMN",
            "Unknown columns are rejected; prefix annotations with meta.",
            unknown_columns=unknown,
        )
    parameter_index, unit = parameter_columns[0]
    split_index = normalized_header.index("split_id") if "split_id" in normalized_header else None
    filename_index = normalized_header.index("output_filename") if "output_filename" in normalized_header else None
    meta_indices = []
    for index, (name, normalized) in enumerate(zip(header, normalized_header)):
        if normalized.startswith("meta."):
            metadata_name = name.strip()[5:].strip()
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", metadata_name):
                _fail(
                    "INVALID_PCELLIZER_METADATA_HEADER",
                    "Metadata headers must be meta.<portable-name>.",
                    header=name,
                )
            meta_indices.append((index, metadata_name))
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    seen_split_ids: set[str] = set()
    seen_filenames: set[str] = set()
    try:
        for raw_row in reader:
            if not any(item.strip() for item in raw_row):
                continue
            row_number = reader.line_num
            if len(rows) + len(errors) >= max_rows:
                _fail(
                    "PCELLIZER_SPLIT_ROW_LIMIT_EXCEEDED",
                    "Split table exceeds the configured row limit.",
                    max_rows=max_rows,
                )
            if len(raw_row) != len(header):
                errors.append({"row_number": row_number, "code": "PCELLIZER_SPLIT_COLUMN_COUNT", "expected": len(header), "actual": len(raw_row)})
                continue
            try:
                split_id = raw_row[split_index].strip() if split_index is not None else f"split_{len(rows) + len(errors) + 1:03d}"
                if (
                    not SPLIT_ID_PATTERN.fullmatch(split_id)
                    or split_id.split(".", 1)[0].upper() in WINDOWS_RESERVED_NAMES
                ):
                    _fail("INVALID_PCELLIZER_SPLIT_ID", "split_id is not portable and filesystem-safe.", split_id=split_id)
                filename = _safe_output_filename(
                    raw_row[filename_index] if filename_index is not None else "",
                    split_id=split_id,
                )
                if split_id in seen_split_ids:
                    _fail("DUPLICATE_PCELLIZER_SPLIT_ID", "split_id must be unique.", split_id=split_id)
                filename_key = filename.casefold()
                if filename_key in seen_filenames:
                    _fail("DUPLICATE_PCELLIZER_OUTPUT_FILENAME", "Output filename must be unique case-insensitively.", output_filename=filename)
                value_dbu = _parameter_value_dbu(raw_row[parameter_index], unit=unit, parameter=parameter)
                variant_key = canonical_sha256({parameter_name: value_dbu})
                row = {
                    "row_number": row_number,
                    "split_id": split_id,
                    "output_filename": filename,
                    "parameters_dbu": {parameter_name: value_dbu},
                    "parameters_um": {parameter_name: float(Decimal(value_dbu) * Decimal(str(parameter["dbu_um"])))},
                    "parameters_um_decimal": {
                        parameter_name: format(
                            Decimal(value_dbu) * Decimal(str(parameter["dbu_um"])), "f"
                        )
                    },
                    "variant_key": variant_key,
                    "metadata": {name: raw_row[index].strip() for index, name in meta_indices},
                }
                rows.append(row)
                seen_split_ids.add(split_id)
                seen_filenames.add(filename_key)
            except AnalysisError as exc:
                errors.append({"row_number": row_number, "code": exc.code, "message": exc.message, "details": exc.details})
    except csv.Error as exc:
        _fail("INVALID_PCELLIZER_SPLIT_CSV", "Split table CSV/TSV syntax is invalid.", error=str(exc))
    if not rows and not errors:
        _fail("PCELLIZER_SPLIT_TABLE_NO_ROWS", "Split table contains no data rows.")
    if errors:
        _fail(
            "PCELLIZER_SPLIT_TABLE_INVALID_ROWS",
            "One or more split rows are invalid; no batch plan was created.",
            error_count=len(errors),
            row_errors=errors,
        )
    core = {
        "schema_version": BATCH_PLAN_SCHEMA_VERSION,
        "kind": BATCH_PLAN_KIND,
        "recipe_sha256": validated_recipe["pcellizer_recipe_sha256"],
        "snapshot_package_sha256": validated_recipe["snapshot_package_sha256"],
        "parameter_name": parameter_name,
        "table_source": source,
        "delimiter": "tab" if delimiter == "\t" else delimiter,
        "rows": rows,
        "summary": {
            "row_count": len(rows),
            "unique_variant_count": len({row["variant_key"] for row in rows}),
            "output_mode": "one_standalone_gds_per_row",
            "transaction_policy": "all_or_nothing",
        },
        "production_ready": False,
    }
    return {**core, "pcellizer_batch_plan_sha256": canonical_sha256(core)}


def validate_pcellizer_split_batch_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Verify a table plan before granting additive batch generation."""

    if not isinstance(plan, Mapping):
        _fail("INVALID_PCELLIZER_BATCH_PLAN", "Batch plan must be an object.")
    document = immutable_json_copy(plan)
    recorded_hash = document.pop("pcellizer_batch_plan_sha256", None)
    if not isinstance(recorded_hash, str) or len(recorded_hash) != 64:
        _fail("INVALID_PCELLIZER_BATCH_PLAN_HASH", "Batch plan requires a SHA-256 identity.")
    expected_hash = canonical_sha256(document)
    if recorded_hash != expected_hash:
        _fail(
            "PCELLIZER_BATCH_PLAN_HASH_MISMATCH",
            "Batch plan content changed after table validation.",
            expected_sha256=expected_hash,
            actual_sha256=recorded_hash,
        )
    required = {
        "schema_version",
        "kind",
        "recipe_sha256",
        "snapshot_package_sha256",
        "parameter_name",
        "table_source",
        "delimiter",
        "rows",
        "summary",
        "production_ready",
    }
    rows = document.get("rows")
    summary = document.get("summary")
    if (
        set(document) != required
        or document.get("schema_version") != BATCH_PLAN_SCHEMA_VERSION
        or document.get("kind") != BATCH_PLAN_KIND
        or document.get("production_ready") is not False
        or not isinstance(rows, list)
        or not rows
        or not isinstance(summary, Mapping)
        or summary.get("row_count") != len(rows)
        or summary.get("output_mode") != "one_standalone_gds_per_row"
        or summary.get("transaction_policy") != "all_or_nothing"
    ):
        _fail(
            "INVALID_PCELLIZER_BATCH_PLAN_SCHEMA",
            "Batch plan is not the supported non-destructive per-row GDS contract.",
        )
    filenames = [row.get("output_filename") for row in rows if isinstance(row, Mapping)]
    if len(filenames) != len(rows) or len({str(name).casefold() for name in filenames}) != len(rows):
        _fail(
            "INVALID_PCELLIZER_BATCH_PLAN_SCHEMA",
            "Batch plan rows require unique output filenames.",
        )
    document["pcellizer_batch_plan_sha256"] = recorded_hash
    return immutable_json_copy(document)
