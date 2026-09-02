"""Content-addressed reference layouts and reference-backed DRC precedents."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Mapping, Sequence

from .errors import AnalysisError
from .file_publication import (
    OutputAlreadyExistsError,
    publication_staging_prefix,
    publish_new_directory,
    publish_new_file,
)
from .pcellizer_contract import normalize_occurrence_path
from .workflow_manifest import canonical_json_bytes, canonical_sha256, immutable_json_copy


SCHEMA_VERSION = 1
SOURCE_SUFFIXES = {".gds", ".oas"}
SAFE_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
CONCERNS = (
    "transistor_context",
    "contact_array",
    "routing_mesh",
    "routing_transition",
    "pad_joint",
    "device_geometry",
    "other",
)
USAGE_MODES = ("normal_style", "reference_precedent")
SEVERITY_POLICIES = ("same_or_less_severe", "same_error_type")


def _fail(code: str, message: str, *, next_action: str, **details: Any) -> None:
    raise AnalysisError(
        code=code,
        message=message,
        details={**details, "production_ready": False},
        next_action=next_action,
    )


def _token(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not SAFE_TOKEN.fullmatch(value):
        _fail(
            "INVALID_REFERENCE_TOKEN",
            f"{field} must be a short filesystem-safe token.",
            next_action=f"Provide {field} using letters, digits, period, underscore, or hyphen.",
            field=field,
            value=value,
        )
    return value


def _optional_text(value: Any, *, field: str, maximum: int = 2000) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        _fail(
            "INVALID_REFERENCE_TEXT",
            f"{field} must be non-empty text no longer than {maximum} characters.",
            next_action=f"Provide a concise {field} or omit it.",
            field=field,
        )
    return value.strip()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        _fail(
            "REFERENCE_FILE_READ_FAILED",
            "A reference artifact could not be read.",
            next_action="Check that the reference library and GDS are readable.",
            path=str(path),
            error_type=type(exc).__name__,
        )
    return digest.hexdigest()


def _read_json(path: Path, *, code: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail(
            code,
            "A reference-library document could not be decoded.",
            next_action="Restore the content-addressed document or register the reference again.",
            path=str(path),
            error_type=type(exc).__name__,
        )
    if not isinstance(value, dict):
        _fail(
            code,
            "A reference-library document must be one JSON object.",
            next_action="Restore the content-addressed document or register the reference again.",
            path=str(path),
        )
    return value


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_json_bytes(value)
    handle, temporary_name = tempfile.mkstemp(
        prefix=publication_staging_prefix("reference-json"), suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            publish_new_file(temporary_name, path)
        except OutputAlreadyExistsError:
            try:
                existing = path.read_bytes()
            except OSError as exc:
                _fail(
                    "REFERENCE_DOCUMENT_READ_FAILED",
                    "A concurrently published reference document could not be read.",
                    next_action="Check the reference-library filesystem and retry.",
                    path=str(path),
                    error_type=type(exc).__name__,
                )
            if existing != data:
                _fail(
                    "REFERENCE_DOCUMENT_CONFLICT",
                    "A concurrent writer published different reference document content.",
                    next_action="Create a new immutable view or decision instead of replacing the winner.",
                    path=str(path),
                )
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def _bbox(value: Any, *, field: str) -> list[float]:
    if (
        isinstance(value, (str, bytes, bytearray))
        or not isinstance(value, Sequence)
        or len(value) != 4
    ):
        _fail(
            "INVALID_REFERENCE_ROI",
            f"{field} must contain x1, y1, x2, y2 in microns.",
            next_action="Select one finite positive-area rectangle in KLayout.",
            field=field,
            value=value,
        )
    result: list[float] = []
    for coordinate in value:
        if (
            isinstance(coordinate, bool)
            or not isinstance(coordinate, (int, float))
            or not math.isfinite(float(coordinate))
        ):
            _fail(
                "INVALID_REFERENCE_ROI",
                f"{field} coordinates must be finite numbers.",
                next_action="Select one finite positive-area rectangle in KLayout.",
                field=field,
                value=value,
            )
        result.append(float(coordinate))
    if result[2] <= result[0] or result[3] <= result[1]:
        _fail(
            "INVALID_REFERENCE_ROI",
            f"{field} must have positive width and height.",
            next_action="Select one finite positive-area rectangle in KLayout.",
            field=field,
            value=result,
        )
    return result


def _layer_tokens(value: Any, *, field: str) -> list[str]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        _fail(
            "INVALID_REFERENCE_LAYERS",
            f"{field} must be an array of semantic roles or layer/datatype tokens.",
            next_action="Use tokens such as m1 or 10/0 from the confirmed layermap.",
            field=field,
        )
    tokens = sorted({_token(item, field=f"{field}[]") for item in value})
    if not tokens:
        _fail(
            "REFERENCE_LAYERS_REQUIRED",
            "At least one relevant layer is required.",
            next_action="Select the layers that make the reference motif readable.",
            field=field,
        )
    return tokens


def _normalize_style_descriptors(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        _fail(
            "INVALID_REFERENCE_STYLE",
            "style_descriptors must be an array.",
            next_action="Provide explicit style descriptors or an empty array.",
        )
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(value):
        if not isinstance(raw, Mapping):
            _fail(
                "INVALID_REFERENCE_STYLE",
                "Each style descriptor must be one object.",
                next_action="Provide an id, category, description, and JSON parameters.",
                index=index,
            )
        descriptor = {
            "descriptor_id": _token(raw.get("descriptor_id"), field="descriptor_id"),
            "category": _token(raw.get("category"), field="category"),
            "description": _optional_text(raw.get("description"), field="description"),
            "parameters": immutable_json_copy(raw.get("parameters", {})),
        }
        normalized.append(descriptor)
    normalized.sort(key=lambda item: item["descriptor_id"])
    if len({item["descriptor_id"] for item in normalized}) != len(normalized):
        _fail(
            "DUPLICATE_REFERENCE_DESCRIPTOR",
            "Style descriptor ids must be unique within one reference view.",
            next_action="Rename duplicate descriptor ids.",
        )
    return normalized


def _normalize_marker_template(value: Mapping[str, Any], *, index: int) -> dict[str, Any]:
    template_id = _token(value.get("template_id"), field="template_id")
    context_signature = value.get("context_signature")
    if (
        not isinstance(context_signature, str)
        or not re.fullmatch(r"[0-9a-f]{64}", context_signature)
    ):
        _fail(
            "INVALID_REFERENCE_CONTEXT_SIGNATURE",
            "A marker template requires a lowercase SHA-256 structural context signature.",
            next_action="Use a deterministic geometry/context extractor to fingerprint the motif.",
            template_index=index,
        )
    deviation = value.get("max_deviation_um")
    if deviation is not None and (
        isinstance(deviation, bool)
        or not isinstance(deviation, (int, float))
        or not math.isfinite(float(deviation))
        or float(deviation) < 0
    ):
        _fail(
            "INVALID_REFERENCE_DEVIATION",
            "max_deviation_um must be finite and nonnegative when supplied.",
            next_action="Normalize the DRC marker into a nonnegative deviation magnitude.",
            template_id=template_id,
        )
    rule_id = value.get("rule_id")
    if rule_id is not None:
        rule_id = _token(rule_id, field="rule_id")
    return {
        "template_id": template_id,
        "rule_id": rule_id,
        "violation_type": _token(value.get("violation_type"), field="violation_type"),
        "layer_tokens": _layer_tokens(value.get("layer_tokens"), field="layer_tokens"),
        "context_signature": context_signature,
        "max_deviation_um": None if deviation is None else float(deviation),
        "reference_bbox_um": _bbox(value.get("reference_bbox_um"), field="reference_bbox_um"),
        "description": _optional_text(value.get("description"), field="description"),
    }


def _normalize_marker_templates(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        _fail(
            "INVALID_REFERENCE_MARKERS",
            "accepted_marker_templates must be an array.",
            next_action="Provide normalized reference DRC marker templates or an empty array.",
        )
    result = [
        _normalize_marker_template(raw, index=index)
        if isinstance(raw, Mapping)
        else _normalize_marker_template({}, index=index)
        for index, raw in enumerate(value)
    ]
    result.sort(key=lambda item: item["template_id"])
    if len({item["template_id"] for item in result}) != len(result):
        _fail(
            "DUPLICATE_REFERENCE_MARKER_TEMPLATE",
            "Marker template ids must be unique.",
            next_action="Rename duplicate template ids.",
        )
    return result


def _normalize_inventory(inventory: Mapping[str, Any]) -> dict[str, Any]:
    if inventory.get("ok") is not True or not isinstance(inventory.get("layout"), Mapping):
        _fail(
            "REFERENCE_LAYOUT_INVENTORY_FAILED",
            "KLayout did not return a successful reference inventory.",
            next_action="Open the GDS in KLayout, choose an explicit top cell, and register again.",
            inventory=immutable_json_copy(inventory),
        )
    layout = inventory["layout"]
    layers = []
    for item in inventory.get("layers", []):
        if isinstance(item, Mapping) and item.get("used") is True:
            layers.append(
                {
                    "layer": int(item["layer"]),
                    "datatype": int(item["datatype"]),
                    "mapped_roles": sorted(str(role) for role in item.get("mapped_roles", [])),
                }
            )
    layers.sort(key=lambda item: (item["layer"], item["datatype"]))
    return {
        "top_cell": str(layout["top_cell"]),
        "top_cells": sorted(str(item) for item in layout.get("top_cells", [])),
        "dbu_um": float(layout["dbu_um"]),
        "klayout_version": str(layout.get("klayout_version", "unknown")),
        "top_bbox_um": layout.get("top_bbox_um"),
        "cell_count": int(layout["cell_count"]),
        "used_layers": layers,
        "input_layout_modified": inventory.get("input_layout_modified") is True,
        "layout_read_count": int(inventory.get("layout_read_count", 0)),
    }


class ReferenceLibrary:
    """Local immutable reference store. No method edits source GDS geometry."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()

    def _find_document(self, folder: str, identifier: str, filename: str) -> Path:
        _token(identifier, field="identifier")
        matches = list(self.root.glob(f"*/{folder}/{identifier}/{filename}"))
        if len(matches) != 1:
            _fail(
                "REFERENCE_DOCUMENT_NOT_FOUND",
                "The requested reference-library document was not found uniquely.",
                next_action="List references or views and use an exact identifier.",
                identifier=identifier,
                match_count=len(matches),
            )
        return matches[0]

    @staticmethod
    def _verify_hashed_document(document: dict[str, Any], hash_field: str) -> dict[str, Any]:
        recorded = document.pop(hash_field, None)
        expected = canonical_sha256(document)
        if recorded != expected:
            _fail(
                "REFERENCE_DOCUMENT_HASH_MISMATCH",
                "A reference-library document changed after creation.",
                next_action="Restore the document or register and confirm a new revision.",
                expected_sha256=expected,
                actual_sha256=recorded,
            )
        document[hash_field] = recorded
        return document

    def register(
        self,
        *,
        source_layout_path: str,
        provenance_source_path: str | None = None,
        process_node: str,
        inventory: Mapping[str, Any],
        process_option: str = "default",
        process_revision: str = "unspecified",
        profile_name: str | None = None,
        profile_version: str | None = None,
        layermap_sha256: str | None = None,
        purpose_tags: Sequence[str] = (),
        description: str | None = None,
    ) -> dict[str, Any]:
        node = _token(process_node, field="process_node")
        option = _token(process_option, field="process_option")
        revision = _token(process_revision, field="process_revision")
        source = Path(source_layout_path).expanduser().resolve()
        suffix = source.suffix.lower()
        if not source.is_file() or suffix not in SOURCE_SUFFIXES:
            _fail(
                "REFERENCE_LAYOUT_NOT_FOUND",
                "Reference source must be an existing GDS or OASIS file.",
                next_action="Provide an existing .gds or .oas reference layout.",
                source_layout_path=str(source),
            )
        before = source.stat()
        source_hash = _file_sha256(source)
        after = source.stat()
        if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
            _fail(
                "REFERENCE_CHANGED_DURING_REGISTRATION",
                "The reference layout changed while it was being registered.",
                next_action="Stop other writers and register the stable reference again.",
                source_layout_path=str(source),
            )
        if layermap_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", layermap_sha256):
            _fail(
                "INVALID_REFERENCE_LAYERMAP_HASH",
                "layermap_sha256 must be a lowercase SHA-256 digest.",
                next_action="Hash the exact layermap used to interpret this reference.",
                layermap_sha256=layermap_sha256,
            )
        identity = {
            "process_node": node,
            "process_option": option,
            "process_revision": revision,
            "layout_sha256": source_hash,
        }
        reference_id = f"ref-{canonical_sha256(identity)[:24]}"
        final_dir = self.root / node / "assets" / reference_id
        manifest_path = final_dir / "asset.json"
        if manifest_path.is_file():
            return self.load_asset(reference_id)

        normalized_inventory = _normalize_inventory(inventory)
        if normalized_inventory["input_layout_modified"]:
            _fail(
                "REFERENCE_INVENTORY_MODIFIED_INPUT",
                "Reference inventory claims the source layout was modified.",
                next_action="Re-run read-only KLayout inspection.",
            )
        tags = sorted({_token(item, field="purpose_tags[]") for item in purpose_tags})
        asset_core = {
            "schema_version": SCHEMA_VERSION,
            "kind": "ReferenceAsset",
            "reference_id": reference_id,
            "process": {
                "node": node,
                "option": option,
                "revision": revision,
                "profile_name": _optional_text(profile_name, field="profile_name", maximum=128),
                "profile_version": _optional_text(profile_version, field="profile_version", maximum=128),
                "layermap_sha256": layermap_sha256,
            },
            "source": {
                "original_path": str(
                    Path(provenance_source_path).expanduser().resolve()
                    if provenance_source_path is not None
                    else source
                ),
                "layout_sha256": source_hash,
                "size_bytes": before.st_size,
                "format": suffix[1:],
                "stored_filename": f"source{suffix}",
                "mutable_original_is_runtime_dependency": False,
            },
            "inventory": normalized_inventory,
            "purpose_tags": tags,
            "description": _optional_text(description, field="description"),
            "source_geometry_modified": False,
            "flattening_performed": False,
        }
        manifest = {**asset_core, "asset_manifest_sha256": canonical_sha256(asset_core)}
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        staging_dir = Path(
            tempfile.mkdtemp(
                prefix=publication_staging_prefix("reference", directory=True),
                dir=final_dir.parent,
            )
        )
        try:
            shutil.copyfile(source, staging_dir / f"source{suffix}")
            if _file_sha256(staging_dir / f"source{suffix}") != source_hash:
                _fail(
                    "REFERENCE_SNAPSHOT_HASH_MISMATCH",
                    "The stored reference bytes differ from the registered source.",
                    next_action="Check the filesystem and register again.",
                )
            _atomic_json(staging_dir / "asset.json", manifest)
            try:
                publish_new_directory(staging_dir, final_dir)
            except OutputAlreadyExistsError:
                shutil.rmtree(staging_dir)
        except Exception:
            if staging_dir.exists():
                shutil.rmtree(staging_dir)
            raise
        return self.load_asset(reference_id)

    def load_asset(self, reference_id: str) -> dict[str, Any]:
        manifest_path = self._find_document("assets", reference_id, "asset.json")
        document = self._verify_hashed_document(
            _read_json(manifest_path, code="INVALID_REFERENCE_ASSET"),
            "asset_manifest_sha256",
        )
        source_path = manifest_path.parent / document["source"]["stored_filename"]
        actual_hash = _file_sha256(source_path)
        if actual_hash != document["source"]["layout_sha256"]:
            _fail(
                "REFERENCE_ASSET_HASH_MISMATCH",
                "Stored reference GDS bytes changed after registration.",
                next_action="Restore the content-addressed asset or register a new reference revision.",
                reference_id=reference_id,
                expected_sha256=document["source"]["layout_sha256"],
                actual_sha256=actual_hash,
            )
        document["source"]["stored_path"] = str(source_path.resolve())
        return document

    def list_assets(self, *, process_node: str | None = None) -> dict[str, Any]:
        node = _token(process_node, field="process_node") if process_node else None
        pattern = f"{node}/assets/*/asset.json" if node else "*/assets/*/asset.json"
        assets = []
        for path in sorted(self.root.glob(pattern)):
            assets.append(self.load_asset(path.parent.name))
        return {
            "ok": True,
            "library_root": str(self.root),
            "process_node": node,
            "reference_count": len(assets),
            "references": assets,
        }

    def prepare_view(
        self,
        *,
        reference_id: str,
        concern: str,
        usage_mode: str,
        roi_bbox_um: Sequence[float],
        relevant_layers: Sequence[str],
        view_bbox_um: Sequence[float] | None = None,
        occurrence_segments: Sequence[Mapping[str, Any]] = (),
        device_family: str | None = None,
        terminal_role: str | None = None,
        style_descriptors: Sequence[Mapping[str, Any]] = (),
        accepted_marker_templates: Sequence[Mapping[str, Any]] = (),
        severity_policy: str = "same_or_less_severe",
    ) -> dict[str, Any]:
        asset = self.load_asset(reference_id)
        if concern not in CONCERNS:
            _fail(
                "UNSUPPORTED_REFERENCE_CONCERN",
                "Reference concern is not supported by schema v1.",
                next_action=f"Use one of: {', '.join(CONCERNS)}.",
                concern=concern,
            )
        if usage_mode not in USAGE_MODES:
            _fail(
                "UNSUPPORTED_REFERENCE_USAGE_MODE",
                "Reference usage mode must be normal_style or reference_precedent.",
                next_action="Choose whether rules remain dominant or the confirmed reference precedent may accept matching markers.",
                usage_mode=usage_mode,
            )
        if severity_policy not in SEVERITY_POLICIES:
            _fail(
                "UNSUPPORTED_REFERENCE_SEVERITY_POLICY",
                "Reference marker severity policy is unsupported.",
                next_action=f"Use one of: {', '.join(SEVERITY_POLICIES)}.",
                severity_policy=severity_policy,
            )
        templates = _normalize_marker_templates(accepted_marker_templates)
        if usage_mode == "normal_style" and templates:
            _fail(
                "REFERENCE_PRECEDENT_MODE_REQUIRED",
                "Normal drawing style cannot carry accepted DRC marker templates.",
                next_action="Select reference_precedent or remove marker templates.",
            )
        if severity_policy == "same_or_less_severe" and any(
            item["max_deviation_um"] is None for item in templates
        ):
            _fail(
                "REFERENCE_DEVIATION_REQUIRED",
                "same_or_less_severe requires max_deviation_um for every marker template.",
                next_action="Normalize each reference marker deviation or use same_error_type explicitly.",
            )
        occurrence = normalize_occurrence_path(
            top_cell=asset["inventory"]["top_cell"], segments=occurrence_segments
        )
        local_roi = _bbox(roi_bbox_um, field="roi_bbox_um")
        if view_bbox_um is None and occurrence["depth"]:
            _fail(
                "REFERENCE_VIEW_BBOX_REQUIRED",
                "A nested occurrence requires its ROI transformed into top-view coordinates.",
                next_action="Capture view_bbox_um from the exact KLayout occurrence; do not infer hierarchy transforms in the LLM.",
                occurrence_id=occurrence["occurrence_id"],
            )
        display_bbox = (
            local_roi
            if view_bbox_um is None
            else _bbox(view_bbox_um, field="view_bbox_um")
        )
        view_core = {
            "schema_version": SCHEMA_VERSION,
            "kind": "ReferenceView",
            "reference_id": reference_id,
            "process": immutable_json_copy(asset["process"]),
            "layout_sha256": asset["source"]["layout_sha256"],
            "stored_layout_path": asset["source"]["stored_path"],
            "top_cell": asset["inventory"]["top_cell"],
            "occurrence_path": occurrence,
            "roi_bbox_um": local_roi,
            "view_bbox_um": display_bbox,
            "relevant_layers": _layer_tokens(relevant_layers, field="relevant_layers"),
            "concern": concern,
            "device_family": _optional_text(device_family, field="device_family", maximum=128),
            "terminal_role": _optional_text(terminal_role, field="terminal_role", maximum=128),
            "usage_mode": usage_mode,
            "severity_policy": severity_policy,
            "style_descriptors": _normalize_style_descriptors(style_descriptors),
            "accepted_marker_templates": templates,
            "reference_marker_count_is_acceptance_limit": False,
            "direct_klayout_inspection_required": True,
            "user_confirmed": False,
        }
        view_id = f"view-{canonical_sha256(view_core)[:24]}"
        view_core["view_id"] = view_id
        manifest = {**view_core, "view_manifest_sha256": canonical_sha256(view_core)}
        view_dir = self.root / asset["process"]["node"] / "views" / view_id
        _atomic_json(view_dir / "view.json", manifest)
        return {
            "ok": True,
            "view": manifest,
            "view_manifest_path": str((view_dir / "view.json").resolve()),
            "confirmation_path": str((view_dir / "confirmation.json").resolve()),
            "next_action": "Open the view manifest in the KLayout Reference Navigator, inspect the actual GDS, and confirm it there.",
            "production_ready": False,
        }

    def load_view(self, view_id: str) -> dict[str, Any]:
        path = self._find_document("views", view_id, "view.json")
        view = self._verify_hashed_document(
            _read_json(path, code="INVALID_REFERENCE_VIEW"),
            "view_manifest_sha256",
        )
        asset = self.load_asset(view["reference_id"])
        if asset["source"]["layout_sha256"] != view["layout_sha256"]:
            _fail(
                "STALE_REFERENCE_VIEW",
                "Reference view no longer identifies the stored reference asset.",
                next_action="Prepare and inspect a new reference view.",
                view_id=view_id,
            )
        view["stored_layout_path"] = asset["source"]["stored_path"]
        view["view_manifest_path"] = str(path.resolve())
        return view

    def record_gui_confirmation(
        self, *, view_id: str, justification: str | None = None
    ) -> dict[str, Any]:
        view = self.load_view(view_id)
        reason = justification or "Confirmed after direct inspection of the reference GDS in KLayout."
        confirmation_core = {
            "schema_version": SCHEMA_VERSION,
            "kind": "ReferenceGuiConfirmation",
            "view_id": view_id,
            "view_manifest_sha256": view["view_manifest_sha256"],
            "reference_id": view["reference_id"],
            "layout_sha256": view["layout_sha256"],
            "decision": view["usage_mode"],
            "justification": _optional_text(reason, field="justification"),
            "local_gui_user_action": True,
            "identity_attested_by_trusted_host": False,
        }
        confirmation = {
            **confirmation_core,
            "confirmation_sha256": canonical_sha256(confirmation_core),
        }
        path = Path(view["view_manifest_path"]).parent / "confirmation.json"
        if path.exists():
            existing = self._verify_hashed_document(
                _read_json(path, code="INVALID_REFERENCE_CONFIRMATION"),
                "confirmation_sha256",
            )
            if existing != confirmation:
                _fail(
                    "REFERENCE_CONFIRMATION_ALREADY_EXISTS",
                    "This immutable view already has a different confirmation.",
                    next_action="Prepare a new reference view for a changed decision or note.",
                    view_id=view_id,
                )
            return existing
        _atomic_json(path, confirmation)
        return confirmation

    def confirm_view(self, *, view_id: str) -> dict[str, Any]:
        view = self.load_view(view_id)
        confirmation_path = Path(view["view_manifest_path"]).parent / "confirmation.json"
        if not confirmation_path.is_file():
            _fail(
                "REFERENCE_GUI_CONFIRMATION_REQUIRED",
                "The reference has not been confirmed from the KLayout navigator.",
                next_action="Open the actual reference GDS in KLayout and click Confirm reference.",
                view_id=view_id,
            )
        confirmation = self._verify_hashed_document(
            _read_json(confirmation_path, code="INVALID_REFERENCE_CONFIRMATION"),
            "confirmation_sha256",
        )
        expected = {
            "view_id": view_id,
            "view_manifest_sha256": view["view_manifest_sha256"],
            "reference_id": view["reference_id"],
            "layout_sha256": view["layout_sha256"],
            "decision": view["usage_mode"],
            "local_gui_user_action": True,
        }
        if any(confirmation.get(key) != value for key, value in expected.items()):
            _fail(
                "REFERENCE_CONFIRMATION_BINDING_MISMATCH",
                "GUI confirmation does not bind the current immutable reference view.",
                next_action="Re-open and confirm the exact current view in KLayout.",
                view_id=view_id,
            )
        selection_core = {
            "schema_version": SCHEMA_VERSION,
            "kind": "ConfirmedReferenceSelection",
            "selection_id": f"selection-{view_id[5:]}",
            "view": immutable_json_copy(view),
            "confirmation": immutable_json_copy(confirmation),
            "approval_state": "confirmed",
            "reference_marker_count_is_acceptance_limit": False,
            "matching_markers_may_repeat_across_similar_instances": True,
            "trusted_identity_attestation_configured": False,
            "production_ready": False,
        }
        selection = {
            **selection_core,
            "selection_sha256": canonical_sha256(selection_core),
        }
        node = view["process"]["node"]
        selection_path = self.root / node / "selections" / selection["selection_id"] / "selection.json"
        _atomic_json(selection_path, selection)
        return {
            "ok": True,
            "selection": selection,
            "selection_path": str(selection_path.resolve()),
            "next_action": "Pass this exact selection_id when consulting reference style or classifying DRC markers.",
        }

    def load_selection(self, selection_id: str) -> dict[str, Any]:
        path = self._find_document("selections", selection_id, "selection.json")
        return self._verify_hashed_document(
            _read_json(path, code="INVALID_REFERENCE_SELECTION"),
            "selection_sha256",
        )

    def consult(self, *, selection_id: str) -> dict[str, Any]:
        selection = self.load_selection(selection_id)
        view = selection["view"]
        return {
            "ok": True,
            "selection_id": selection_id,
            "reference_id": view["reference_id"],
            "process": view["process"],
            "concern": view["concern"],
            "device_family": view["device_family"],
            "terminal_role": view["terminal_role"],
            "usage_mode": view["usage_mode"],
            "style_descriptors": view["style_descriptors"],
            "accepted_marker_templates": view["accepted_marker_templates"],
            "reference_citation": {
                "layout_sha256": view["layout_sha256"],
                "top_cell": view["top_cell"],
                "occurrence_id": view["occurrence_path"]["occurrence_id"],
                "roi_bbox_um": view["roi_bbox_um"],
                "view_manifest_sha256": view["view_manifest_sha256"],
            },
            "reference_marker_count_is_acceptance_limit": False,
            "production_ready": False,
        }

    def classify_markers(
        self,
        *,
        selection_id: str,
        candidate_markers: Sequence[Mapping[str, Any]],
        deviation_tolerance_um: float = 0.0,
    ) -> dict[str, Any]:
        if (
            isinstance(deviation_tolerance_um, bool)
            or not isinstance(deviation_tolerance_um, (int, float))
            or not math.isfinite(float(deviation_tolerance_um))
            or float(deviation_tolerance_um) < 0
        ):
            _fail(
                "INVALID_REFERENCE_DEVIATION_TOLERANCE",
                "deviation_tolerance_um must be finite and nonnegative.",
                next_action="Use zero or a DBU-derived tolerance.",
            )
        selection = self.load_selection(selection_id)
        view = selection["view"]
        if view["usage_mode"] != "reference_precedent":
            _fail(
                "REFERENCE_PRECEDENT_SELECTION_REQUIRED",
                "DRC marker classification requires a confirmed reference_precedent selection.",
                next_action="Confirm the reference in reference_precedent mode.",
                selection_id=selection_id,
            )
        if isinstance(candidate_markers, (str, bytes, bytearray)) or not isinstance(candidate_markers, Sequence):
            _fail(
                "INVALID_CANDIDATE_DRC_MARKERS",
                "candidate_markers must be an array.",
                next_action="Provide normalized DRC markers from one generated layout.",
            )
        templates = view["accepted_marker_templates"]
        classifications = []
        accepted = 0
        rejected = 0
        for index, raw in enumerate(candidate_markers):
            if not isinstance(raw, Mapping):
                _fail(
                    "INVALID_CANDIDATE_DRC_MARKER",
                    "Each candidate DRC marker must be one object.",
                    next_action="Normalize the marker schema before classification.",
                    marker_index=index,
                )
            marker_id = _token(raw.get("marker_id"), field="marker_id")
            marker = {
                "marker_id": marker_id,
                "process_node": _token(raw.get("process_node"), field="process_node"),
                "process_option": _token(raw.get("process_option"), field="process_option"),
                "process_revision": _token(raw.get("process_revision"), field="process_revision"),
                "profile_name": _optional_text(raw.get("profile_name"), field="profile_name", maximum=128),
                "profile_version": _optional_text(raw.get("profile_version"), field="profile_version", maximum=128),
                "layermap_sha256": raw.get("layermap_sha256"),
                "concern": raw.get("concern"),
                "rule_id": raw.get("rule_id"),
                "violation_type": _token(raw.get("violation_type"), field="violation_type"),
                "layer_tokens": _layer_tokens(raw.get("layer_tokens"), field="layer_tokens"),
                "context_signature": raw.get("context_signature"),
                "deviation_um": raw.get("deviation_um"),
                "bbox_um": _bbox(raw.get("bbox_um"), field="bbox_um"),
            }
            if marker["rule_id"] is not None:
                marker["rule_id"] = _token(marker["rule_id"], field="rule_id")
            if marker["layermap_sha256"] is not None and not re.fullmatch(
                r"[0-9a-f]{64}", str(marker["layermap_sha256"])
            ):
                _fail(
                    "INVALID_REFERENCE_LAYERMAP_HASH",
                    "Candidate marker layermap_sha256 must be a lowercase SHA-256 digest.",
                    next_action="Bind candidate markers to the exact layermap used by the DRC adapter.",
                    marker_id=marker_id,
                )
            if not isinstance(marker["context_signature"], str) or not re.fullmatch(
                r"[0-9a-f]{64}", marker["context_signature"]
            ):
                _fail(
                    "INVALID_REFERENCE_CONTEXT_SIGNATURE",
                    "Candidate marker requires a deterministic structural context signature.",
                    next_action="Fingerprint the local marker motif before classification.",
                    marker_id=marker_id,
                )
            deviation = marker["deviation_um"]
            if deviation is not None and (
                isinstance(deviation, bool)
                or not isinstance(deviation, (int, float))
                or not math.isfinite(float(deviation))
                or float(deviation) < 0
            ):
                _fail(
                    "INVALID_REFERENCE_DEVIATION",
                    "Candidate deviation_um must be finite and nonnegative.",
                    next_action="Normalize the DRC marker deviation magnitude.",
                    marker_id=marker_id,
                )
            marker["deviation_um"] = None if deviation is None else float(deviation)
            reasons = []
            matches = []
            expected_process = view["process"]
            if any(
                marker[field] != expected_process[expected_field]
                for field, expected_field in (
                    ("process_node", "node"),
                    ("process_option", "option"),
                    ("process_revision", "revision"),
                    ("profile_name", "profile_name"),
                    ("profile_version", "profile_version"),
                    ("layermap_sha256", "layermap_sha256"),
                )
            ):
                reasons.append("process_identity_mismatch")
            if marker["concern"] != view["concern"]:
                reasons.append("concern_mismatch")
            if not reasons:
                for template in templates:
                    if template["violation_type"] != marker["violation_type"]:
                        continue
                    if template["layer_tokens"] != marker["layer_tokens"]:
                        continue
                    if template["context_signature"] != marker["context_signature"]:
                        continue
                    if template["rule_id"] is not None and template["rule_id"] != marker["rule_id"]:
                        continue
                    if view["severity_policy"] == "same_or_less_severe":
                        if marker["deviation_um"] is None:
                            continue
                        if marker["deviation_um"] > template["max_deviation_um"] + float(deviation_tolerance_um):
                            continue
                    matches.append(template["template_id"])
            if matches:
                status = "REF_ACCEPTED"
                accepted += 1
            else:
                status = "REVIEW_NEEDED"
                rejected += 1
                if not reasons:
                    reasons.append("no_matching_reference_motif")
            classifications.append(
                {
                    "marker": marker,
                    "classification": status,
                    "matched_template_ids": matches,
                    "reasons": reasons,
                }
            )
        overall = (
            "CLEAN"
            if not classifications
            else "REVIEW_NEEDED"
            if rejected
            else "REF_ACCEPTED"
        )
        return {
            "ok": True,
            "classification": overall,
            "summary": {
                "candidate_marker_count": len(classifications),
                "ref_accepted_count": accepted,
                "review_needed_count": rejected,
                "reference_template_count": len(templates),
                "marker_count_growth_allowed": True,
            },
            "markers": classifications,
            "drc_clean": not classifications,
            "reference_exceptions_present": accepted > 0,
            "unmatched_marker_present": rejected > 0,
            "drawing_blocked": False,
            "advisory_only_until_process_adapter_validated": True,
            "reference_marker_count_is_acceptance_limit": False,
            "trusted_drc_adapter_required_for_production_claim": True,
            "production_ready": False,
        }


def reference_library_contract() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "storage": "content_addressed_full_gds_per_process_node",
        "supported_formats": sorted(suffix[1:] for suffix in SOURCE_SUFFIXES),
        "concerns": list(CONCERNS),
        "usage_modes": list(USAGE_MODES),
        "selection_requires_direct_klayout_gui_confirmation": True,
        "reference_may_precede_design_rule_when_mode": "reference_precedent",
        "marker_acceptance": "advisory_exact_context_signature_per_marker",
        "reference_marker_count_is_acceptance_limit": False,
        "matching_repetitions_allowed": True,
        "unmatched_markers_block_drawing": False,
        "flattening_performed": False,
        "reference_proves_electrical_correctness": False,
        "reference_proves_production_approval": False,
    }


def load_reference_view_manifest(view_manifest_path: str | Path) -> dict[str, Any]:
    """Load one view through its owning library, preserving hash verification."""

    path = Path(view_manifest_path).expanduser().resolve()
    if path.name != "view.json" or len(path.parents) < 4:
        _fail(
            "INVALID_REFERENCE_VIEW_PATH",
            "Reference Navigator requires a library view.json manifest.",
            next_action="Choose <library>/<node>/views/<view-id>/view.json.",
            view_manifest_path=str(path),
        )
    view_id = path.parent.name
    library = ReferenceLibrary(path.parents[3])
    view = library.load_view(view_id)
    if Path(view["view_manifest_path"]).resolve() != path:
        _fail(
            "REFERENCE_VIEW_PATH_MISMATCH",
            "The selected path does not match the content-addressed view id.",
            next_action="Choose the exact view.json returned by prepare_reference_view.",
            view_manifest_path=str(path),
        )
    return view


def record_reference_gui_confirmation(
    *, view_manifest_path: str | Path, justification: str | None = None
) -> dict[str, Any]:
    """GUI-only helper: record the user's direct KLayout inspection click."""

    view = load_reference_view_manifest(view_manifest_path)
    path = Path(view["view_manifest_path"])
    return ReferenceLibrary(path.parents[3]).record_gui_confirmation(
        view_id=view["view_id"], justification=justification
    )
