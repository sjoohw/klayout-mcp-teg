"""Immutable exact-match registry for qualified technology adapter packages."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import uuid
from typing import Any, Mapping, Protocol, runtime_checkable

from .errors import AnalysisError
from .file_publication import (
    OutputAlreadyExistsError,
    publication_staging_prefix,
    publish_new_file,
)
from .workflow_manifest import canonical_json_bytes, canonical_sha256, immutable_json_copy


SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
IDENTITY_FIELDS = (
    "technology",
    "pdk_revision",
    "adapter_kind",
    "device_family",
    "topology",
    "package_version",
)
LIFECYCLE_ACTIONS = frozenset({"qualified", "deprecated", "revoked"})


@runtime_checkable
class LifecycleTrustAnchor(Protocol):
    """Independent append/verify authority supplied by the host deployment."""

    anchor_id: str
    trusted: bool

    def append_head(
        self, *, package_sha256: str, sequence: int, record_sha256: str
    ) -> Mapping[str, Any]: ...

    def verify_head(
        self,
        *,
        package_sha256: str,
        sequence: int,
        record_sha256: str,
        anchor_receipt_sha256: str,
    ) -> Mapping[str, Any]: ...


def _fail(code: str, message: str, *, details: Mapping[str, Any], next_action: str) -> None:
    raise AnalysisError(
        code=code,
        message=message,
        details={"stage": "adapter_resolution", **dict(details)},
        next_action=next_action,
    )


def _identity_value(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(
            "TECH_ADAPTER_IDENTITY_FIELD_REQUIRED",
            f"{field} must be a non-empty exact adapter identity value.",
            details={"field": f"identity.{field}", "value": value},
            next_action=f"Provide the exact {field}; aliases and wildcard values are not accepted.",
        )
    normalized = value.strip()
    if "*" in normalized or normalized.casefold() in {"latest", "any", "default"}:
        _fail(
            "TECH_ADAPTER_FALLBACK_FORBIDDEN",
            f"{field} cannot use a wildcard or fallback selector.",
            details={"field": f"identity.{field}", "value": value},
            next_action=f"Select one exact registered {field} value.",
        )
    return normalized


@dataclass(frozen=True, order=True, slots=True)
class TechnologyAdapterKey:
    technology: str
    pdk_revision: str
    adapter_kind: str
    device_family: str
    topology: str
    package_version: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TechnologyAdapterKey":
        if not isinstance(value, Mapping):
            _fail(
                "TECH_ADAPTER_IDENTITY_REQUIRED",
                "Technology adapter identity must be an object.",
                details={"field": "identity", "received_type": type(value).__name__},
                next_action="Provide every exact technology adapter identity field.",
            )
        missing = sorted(set(IDENTITY_FIELDS).difference(value))
        unexpected = sorted(set(value).difference(IDENTITY_FIELDS))
        if missing or unexpected:
            _fail(
                "TECH_ADAPTER_IDENTITY_SCHEMA_MISMATCH",
                "Technology adapter identity does not match the exact-key schema.",
                details={"field": "identity", "missing": missing, "unexpected": unexpected},
                next_action="Add the missing exact identity fields and remove unsupported selectors.",
            )
        return cls(
            **{
                field: _identity_value(value[field], field=field)
                for field in IDENTITY_FIELDS
            }
        )

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class TechnologyAdapterRegistry:
    """Append-only package and lifecycle registry with no implicit fallback."""

    def __init__(
        self,
        root: str | Path | None = None,
        *,
        lifecycle_trust_anchor: LifecycleTrustAnchor | None = None,
    ) -> None:
        self.root = None if root is None else Path(root).expanduser().resolve()
        self.lifecycle_trust_anchor = lifecycle_trust_anchor
        if lifecycle_trust_anchor is not None and (
            not isinstance(lifecycle_trust_anchor, LifecycleTrustAnchor)
            or lifecycle_trust_anchor.trusted is not True
            or not isinstance(lifecycle_trust_anchor.anchor_id, str)
            or not lifecycle_trust_anchor.anchor_id.strip()
        ):
            _fail(
                "TECH_ADAPTER_LIFECYCLE_ANCHOR_UNTRUSTED",
                "The configured lifecycle trust anchor is not a trusted host authority.",
                details={"anchor_type": type(lifecycle_trust_anchor).__name__},
                next_action="Configure an independent trusted lifecycle anchor or run explicitly with local-head-only protection.",
            )
        self._packages: dict[TechnologyAdapterKey, tuple[str, dict[str, Any]]] = {}
        self._lifecycle: list[dict[str, Any]] = []
        if self.root is not None:
            (self.root / "packages").mkdir(parents=True, exist_ok=True)
            (self.root / "lifecycle").mkdir(parents=True, exist_ok=True)
            (self.root / "lifecycle_heads").mkdir(parents=True, exist_ok=True)
            (self.root / "snapshots").mkdir(parents=True, exist_ok=True)
            self._load_from_disk()

    @staticmethod
    def _read_persisted_document(path: Path) -> dict[str, Any]:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _fail(
                "TECH_REGISTRY_PERSISTED_DOCUMENT_INVALID",
                "A persisted technology registry document is unreadable.",
                details={"path": str(path), "error_type": type(exc).__name__},
                next_action="Quarantine the registry root and restore it from trusted evidence.",
            )
        if not isinstance(document, dict):
            _fail(
                "TECH_REGISTRY_PERSISTED_DOCUMENT_INVALID",
                "A persisted technology registry document is not a JSON object.",
                details={"path": str(path), "received_type": type(document).__name__},
                next_action="Quarantine the registry root and restore it from trusted evidence.",
            )
        actual = canonical_sha256(document)
        if not SHA256_PATTERN.fullmatch(path.stem) or actual != path.stem:
            _fail(
                "TECH_REGISTRY_PERSISTED_HASH_MISMATCH",
                "A persisted technology registry document no longer matches its filename hash.",
                details={"path": str(path), "expected": path.stem, "received": actual},
                next_action="Quarantine the registry root and restore the exact content-addressed document.",
            )
        return document

    @staticmethod
    def _validate_recorded_at(value: Any, *, path: str) -> str:
        if not isinstance(value, str) or not value.strip():
            _fail(
                "TECH_ADAPTER_LIFECYCLE_FIELD_REQUIRED",
                "A lifecycle record is missing recorded_at.",
                details={"path": path, "field": "recorded_at"},
                next_action="Provide a timezone-aware ISO-8601 lifecycle timestamp.",
            )
        normalized = value.strip()
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError:
            parsed = None
        if parsed is None or parsed.tzinfo is None:
            _fail(
                "TECH_ADAPTER_LIFECYCLE_TIMESTAMP_INVALID",
                "recorded_at must be a timezone-aware ISO-8601 timestamp.",
                details={"path": path, "field": "recorded_at", "value": value},
                next_action="Use an explicit UTC offset, for example 2026-09-02T10:00:00Z.",
            )
        return normalized

    @staticmethod
    def _order_lifecycle_chain(
        records: list[tuple[str, dict[str, Any]]], *, package_sha256: str
    ) -> list[tuple[str, dict[str, Any]]]:
        legacy = [(digest, record) for digest, record in records if record.get("schema_version") == 1]
        chained = [(digest, record) for digest, record in records if record.get("schema_version") == 2]
        unsupported = [record.get("schema_version") for _, record in records if record.get("schema_version") not in {1, 2}]
        if unsupported:
            _fail(
                "TECH_ADAPTER_LIFECYCLE_SCHEMA_UNSUPPORTED",
                "A persisted lifecycle record has an unsupported schema version.",
                details={"package_sha256": package_sha256, "schema_versions": unsupported},
                next_action="Restore schema_version 1 or 2 lifecycle evidence.",
            )
        if len(legacy) > 1:
            _fail(
                "TECH_ADAPTER_LIFECYCLE_ORDER_UNPROVABLE",
                "Multiple legacy lifecycle records have no durable append order.",
                details={"package_sha256": package_sha256, "record_sha256s": sorted(digest for digest, _ in legacy)},
                next_action="Migrate the legacy records into one reviewed sequence/hash chain before restart.",
            )
        by_sequence: dict[int, tuple[str, dict[str, Any]]] = {}
        for digest, record in chained:
            sequence = record.get("sequence")
            if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1 or sequence in by_sequence:
                _fail(
                    "TECH_ADAPTER_LIFECYCLE_SEQUENCE_INVALID",
                    "Lifecycle sequence must be a unique positive integer per package.",
                    details={"package_sha256": package_sha256, "sequence": sequence, "record_sha256": digest},
                    next_action="Restore the exact monotonic lifecycle chain.",
                )
            by_sequence[sequence] = (digest, record)

        ordered_pairs = list(legacy)
        expected_sequence = 2 if legacy else 1
        previous_digest = legacy[0][0] if legacy else None
        while by_sequence:
            entry = by_sequence.pop(expected_sequence, None)
            if entry is None:
                _fail(
                    "TECH_ADAPTER_LIFECYCLE_CHAIN_BROKEN",
                    "Lifecycle sequence contains a gap or does not start at the expected record.",
                    details={"package_sha256": package_sha256, "expected_sequence": expected_sequence, "remaining_sequences": sorted(by_sequence)},
                    next_action="Restore every record in the exact append chain.",
                )
            digest, record = entry
            if record.get("prev_record_sha256") != previous_digest:
                _fail(
                    "TECH_ADAPTER_LIFECYCLE_CHAIN_BROKEN",
                    "Lifecycle prev_record_sha256 does not match the preceding record.",
                    details={"package_sha256": package_sha256, "sequence": expected_sequence, "expected": previous_digest, "received": record.get("prev_record_sha256")},
                    next_action="Restore the exact append-only lifecycle chain.",
                )
            ordered_pairs.append((digest, record))
            previous_digest = digest
            expected_sequence += 1

        ordered = [record for _, record in ordered_pairs]
        revoked_indexes = [index for index, record in enumerate(ordered) if record.get("action") == "revoked"]
        if revoked_indexes and revoked_indexes != [len(ordered) - 1]:
            _fail(
                "TECH_ADAPTER_REVOKED_TERMINAL_STATE_VIOLATION",
                "A revoked lifecycle package has later records, which is forbidden.",
                details={"package_sha256": package_sha256, "revoked_sequence": revoked_indexes[0] + 1},
                next_action="Quarantine the invalid post-revocation lifecycle records.",
            )
        return ordered_pairs

    @staticmethod
    def _read_lifecycle_head(path: Path) -> dict[str, Any]:
        try:
            head = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _fail(
                "TECH_ADAPTER_LIFECYCLE_HEAD_INVALID",
                "A lifecycle trusted-head document is unreadable.",
                details={"path": str(path), "error_type": type(exc).__name__},
                next_action="Restore the trusted lifecycle head before starting the registry.",
            )
        base_fields = {
            "schema_version",
            "artifact_type",
            "package_sha256",
            "sequence",
            "record_sha256",
        }
        schema_version = head.get("schema_version") if isinstance(head, dict) else None
        expected_fields = (
            base_fields
            if schema_version == 1
            else base_fields | {"anchor_id", "anchor_receipt_sha256"}
        )
        if (
            not isinstance(head, dict)
            or set(head) != expected_fields
            or schema_version not in {1, 2}
            or head.get("artifact_type") != "TechnologyAdapterLifecycleHead"
            or not SHA256_PATTERN.fullmatch(str(head.get("package_sha256", "")))
            or isinstance(head.get("sequence"), bool)
            or not isinstance(head.get("sequence"), int)
            or head.get("sequence", 0) < 1
            or not SHA256_PATTERN.fullmatch(str(head.get("record_sha256", "")))
            or (
                schema_version == 2
                and (
                    not isinstance(head.get("anchor_id"), str)
                    or not head.get("anchor_id", "").strip()
                    or not SHA256_PATTERN.fullmatch(
                        str(head.get("anchor_receipt_sha256", ""))
                    )
                )
            )
        ):
            _fail(
                "TECH_ADAPTER_LIFECYCLE_HEAD_INVALID",
                "A lifecycle trusted-head document does not match its strict schema.",
                details={"path": str(path)},
                next_action="Restore the exact trusted lifecycle head before starting the registry.",
            )
        if path.stem != head["package_sha256"]:
            _fail(
                "TECH_ADAPTER_LIFECYCLE_HEAD_INVALID",
                "The lifecycle trusted-head filename does not match its package hash.",
                details={
                    "path": str(path),
                    "expected": path.stem,
                    "received": head["package_sha256"],
                },
                next_action="Restore the trusted head under the exact package SHA-256 filename.",
            )
        return head

    def _append_external_lifecycle_head(
        self, *, package_sha256: str, sequence: int, record_sha256: str
    ) -> dict[str, str] | None:
        anchor = self.lifecycle_trust_anchor
        if anchor is None:
            return None
        try:
            receipt = anchor.append_head(
                package_sha256=package_sha256,
                sequence=sequence,
                record_sha256=record_sha256,
            )
        except Exception as exc:
            _fail(
                "TECH_ADAPTER_LIFECYCLE_ANCHOR_FAILED",
                "The independent lifecycle anchor failed closed while appending a head.",
                details={"anchor_id": anchor.anchor_id, "error_type": type(exc).__name__},
                next_action="Restore the independent anchor before appending lifecycle state.",
            )
        expected = {
            "anchored": True,
            "anchor_id": anchor.anchor_id,
            "package_sha256": package_sha256,
            "sequence": sequence,
            "record_sha256": record_sha256,
        }
        mismatches = {
            name: {"expected": value, "received": receipt.get(name)}
            for name, value in expected.items()
            if not isinstance(receipt, Mapping) or receipt.get(name) != value
        }
        receipt_sha256 = receipt.get("anchor_receipt_sha256") if isinstance(receipt, Mapping) else None
        if mismatches or not SHA256_PATTERN.fullmatch(str(receipt_sha256 or "")):
            _fail(
                "TECH_ADAPTER_LIFECYCLE_ANCHOR_RECEIPT_INVALID",
                "The independent anchor did not bind the exact lifecycle head.",
                details={"anchor_id": anchor.anchor_id, "mismatches": mismatches},
                next_action="Do not update the local head; repair the independent anchor receipt.",
            )
        return {
            "anchor_id": anchor.anchor_id,
            "anchor_receipt_sha256": str(receipt_sha256),
        }

    def _verify_external_lifecycle_head(self, head: Mapping[str, Any]) -> None:
        anchor = self.lifecycle_trust_anchor
        if head.get("schema_version") == 1:
            if anchor is not None:
                _fail(
                    "TECH_ADAPTER_LIFECYCLE_HEAD_NOT_EXTERNALLY_ANCHORED",
                    "The configured independent anchor cannot verify a legacy local-only head.",
                    details={"package_sha256": head.get("package_sha256")},
                    next_action="Review and explicitly migrate the lifecycle chain into the independent anchor.",
                )
            return
        if anchor is None:
            _fail(
                "TECH_ADAPTER_LIFECYCLE_ANCHOR_UNAVAILABLE",
                "An externally anchored lifecycle head cannot be verified by this host.",
                details={"package_sha256": head.get("package_sha256"), "anchor_id": head.get("anchor_id")},
                next_action="Restore the configured independent lifecycle anchor before starting the registry.",
            )
        if head.get("anchor_id") != anchor.anchor_id:
            _fail(
                "TECH_ADAPTER_LIFECYCLE_ANCHOR_MISMATCH",
                "The local lifecycle head belongs to a different independent anchor.",
                details={"expected": anchor.anchor_id, "received": head.get("anchor_id")},
                next_action="Use the exact anchor that owns this registry root.",
            )
        try:
            result = anchor.verify_head(
                package_sha256=str(head["package_sha256"]),
                sequence=int(head["sequence"]),
                record_sha256=str(head["record_sha256"]),
                anchor_receipt_sha256=str(head["anchor_receipt_sha256"]),
            )
        except Exception as exc:
            _fail(
                "TECH_ADAPTER_LIFECYCLE_ANCHOR_FAILED",
                "The independent lifecycle anchor failed closed during startup verification.",
                details={"anchor_id": anchor.anchor_id, "error_type": type(exc).__name__},
                next_action="Restore anchor availability before starting the registry.",
            )
        expected = {
            "verified": True,
            "anchor_id": anchor.anchor_id,
            "package_sha256": head["package_sha256"],
            "sequence": head["sequence"],
            "record_sha256": head["record_sha256"],
            "anchor_receipt_sha256": head["anchor_receipt_sha256"],
        }
        mismatches = {
            name: {"expected": value, "received": result.get(name)}
            for name, value in expected.items()
            if not isinstance(result, Mapping) or result.get(name) != value
        }
        if mismatches:
            _fail(
                "TECH_ADAPTER_LIFECYCLE_EXTERNAL_ANCHOR_MISMATCH",
                "The independent anchor does not recognize this local lifecycle head as current.",
                details={"anchor_id": anchor.anchor_id, "mismatches": mismatches},
                next_action="Stop adapter use and restore the externally anchored current lifecycle state.",
            )

    def _write_lifecycle_head(
        self,
        *,
        package_sha256: str,
        sequence: int,
        record_sha256: str,
        external_receipt: Mapping[str, str] | None,
    ) -> None:
        assert self.root is not None
        head = {
            "schema_version": 2 if external_receipt is not None else 1,
            "artifact_type": "TechnologyAdapterLifecycleHead",
            "package_sha256": package_sha256,
            "sequence": sequence,
            "record_sha256": record_sha256,
        }
        if external_receipt is not None:
            head.update(external_receipt)
        directory = self.root / "lifecycle_heads"
        final = directory / f"{package_sha256}.json"
        temporary = directory / (
            f"{publication_staging_prefix('lifecycle-head')}{uuid.uuid4().hex}"
        )
        try:
            with temporary.open("xb") as handle:
                handle.write(canonical_json_bytes(head))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, final)
        finally:
            temporary.unlink(missing_ok=True)

    def _load_from_disk(self) -> None:
        assert self.root is not None
        for path in sorted((self.root / "packages").glob("*.json")):
            document = self._read_persisted_document(path)
            if document.get("schema_version") != 1:
                _fail(
                    "TECH_ADAPTER_PACKAGE_SCHEMA_UNSUPPORTED",
                    "A persisted technology adapter package has an unsupported schema version.",
                    details={"path": str(path), "value": document.get("schema_version")},
                    next_action="Restore a schema_version 1 package or migrate it explicitly.",
                )
            key = TechnologyAdapterKey.from_mapping(document.get("identity", {}))
            existing = self._packages.get(key)
            if existing is not None and existing[0] != path.stem:
                _fail(
                    "TECH_ADAPTER_EXACT_KEY_CONFLICT",
                    "Persisted packages contain two payloads for the same exact adapter key.",
                    details={
                        "identity": key.to_dict(),
                        "first": existing[0],
                        "second": path.stem,
                    },
                    next_action="Resolve the registry conflict using a new explicit package version.",
                )
            self._packages[key] = (path.stem, document)

        lifecycle_records: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        registered_hashes = {entry[0] for entry in self._packages.values()}
        lifecycle_heads: dict[str, dict[str, Any]] = {}
        for path in sorted((self.root / "lifecycle_heads").glob("*.json")):
            head = self._read_lifecycle_head(path)
            package_sha256 = head["package_sha256"]
            if package_sha256 not in registered_hashes:
                _fail(
                    "TECH_ADAPTER_LIFECYCLE_HEAD_PACKAGE_MISSING",
                    "A lifecycle trusted head refers to an unavailable package.",
                    details={"path": str(path), "package_sha256": package_sha256},
                    next_action="Restore the exact package referenced by the trusted head.",
                )
            lifecycle_heads[package_sha256] = head
        for path in sorted((self.root / "lifecycle").glob("*.json")):
            record = self._read_persisted_document(path)
            package_sha256 = record.get("package_sha256")
            if package_sha256 not in registered_hashes:
                _fail(
                    "TECH_ADAPTER_PACKAGE_NOT_REGISTERED",
                    "A persisted lifecycle record refers to an unavailable package.",
                    details={"path": str(path), "package_sha256": package_sha256},
                    next_action="Restore the referenced package before loading lifecycle evidence.",
                )
            if record.get("action") not in LIFECYCLE_ACTIONS:
                _fail(
                    "TECH_ADAPTER_LIFECYCLE_ACTION_INVALID",
                    "A persisted lifecycle record has an unsupported action.",
                    details={"path": str(path), "action": record.get("action")},
                    next_action="Restore a qualified, deprecated, or revoked lifecycle record.",
                )
            self._validate_recorded_at(record.get("recorded_at"), path=str(path))
            lifecycle_records.setdefault(package_sha256, []).append((path.stem, record))
        self._lifecycle = []
        for package_sha256 in sorted(lifecycle_records):
            ordered_pairs = self._order_lifecycle_chain(
                lifecycle_records[package_sha256],
                package_sha256=package_sha256,
            )
            head = lifecycle_heads.get(package_sha256)
            last_digest, last_record = ordered_pairs[-1]
            last_sequence = int(last_record.get("sequence", 1))
            if head is None:
                _fail(
                    "TECH_ADAPTER_LIFECYCLE_HEAD_MISSING",
                    "Persisted lifecycle evidence has no trusted final head.",
                    details={
                        "package_sha256": package_sha256,
                        "last_sequence": last_sequence,
                        "last_record_sha256": last_digest,
                    },
                    next_action="Migrate and anchor the reviewed lifecycle chain before starting the registry.",
                )
            if (
                head["sequence"] != last_sequence
                or head["record_sha256"] != last_digest
            ):
                _fail(
                    "TECH_ADAPTER_LIFECYCLE_HEAD_MISMATCH",
                    "The lifecycle chain no longer reaches its trusted final head.",
                    details={
                        "package_sha256": package_sha256,
                        "expected_sequence": head["sequence"],
                        "received_sequence": last_sequence,
                        "expected_record_sha256": head["record_sha256"],
                        "received_record_sha256": last_digest,
                    },
                    next_action="Stop adapter use and restore the missing or modified lifecycle record.",
                )
            self._verify_external_lifecycle_head(head)
            self._lifecycle.extend(record for _, record in ordered_pairs)
        heads_without_records = sorted(set(lifecycle_heads).difference(lifecycle_records))
        if heads_without_records:
            package_sha256 = heads_without_records[0]
            _fail(
                "TECH_ADAPTER_LIFECYCLE_HEAD_RECORD_MISSING",
                "A trusted lifecycle head exists but its lifecycle chain is missing.",
                details={
                    "package_sha256": package_sha256,
                    "head": lifecycle_heads[package_sha256],
                },
                next_action="Stop adapter use and restore the complete lifecycle chain.",
            )

    @staticmethod
    def _publish_document(directory: Path, digest: str, document: Mapping[str, Any]) -> None:
        payload = canonical_json_bytes(document)
        final = directory / f"{digest}.json"
        temporary = directory / (
            f"{publication_staging_prefix('tech-registry')}{uuid.uuid4().hex}"
        )
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
            try:
                publish_new_file(temporary, final)
            except OutputAlreadyExistsError:
                if final.read_bytes() != payload:
                    _fail(
                        "TECH_REGISTRY_CONTENT_ADDRESS_COLLISION",
                        "Existing registry content differs at the same digest.",
                        details={"path": str(final), "expected": digest},
                        next_action="Quarantine the registry root and restore it from trusted evidence.",
                    )
        finally:
            temporary.unlink(missing_ok=True)

    def register_package(self, package: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(package, Mapping):
            _fail(
                "TECH_ADAPTER_PACKAGE_INVALID",
                "Technology adapter package must be an object.",
                details={"field": "package", "received_type": type(package).__name__},
                next_action="Provide a versioned TechnologyAdapterPackage object.",
            )
        document = immutable_json_copy(package)
        if document.get("schema_version") != 1:
            _fail(
                "TECH_ADAPTER_PACKAGE_SCHEMA_UNSUPPORTED",
                "Technology adapter package schema_version must be 1.",
                details={"field": "package.schema_version", "value": document.get("schema_version")},
                next_action="Export the package using schema_version 1.",
            )
        key = TechnologyAdapterKey.from_mapping(document.get("identity", {}))
        package_sha256 = canonical_sha256(document)
        existing = self._packages.get(key)
        if existing is not None and existing[0] != package_sha256:
            _fail(
                "TECH_ADAPTER_EXACT_KEY_CONFLICT",
                "A different immutable package is already registered for this exact key.",
                details={
                    "field": "package.identity",
                    "identity": key.to_dict(),
                    "expected": existing[0],
                    "received": package_sha256,
                },
                next_action="Use a new explicit package_version or resolve the registry conflict.",
            )
        if self.root is not None:
            self._publish_document(self.root / "packages", package_sha256, document)
        self._packages[key] = (package_sha256, document)
        return {
            "ok": True,
            "identity": key.to_dict(),
            "package_sha256": package_sha256,
            "idempotent": existing is not None,
        }

    def append_lifecycle_record(
        self,
        *,
        package_sha256: str,
        action: str,
        reason: str,
        recorded_at: str,
        signer_reference: str,
        signature_sha256: str,
        qualification_receipt_sha256: str | None = None,
    ) -> dict[str, Any]:
        if package_sha256 not in {entry[0] for entry in self._packages.values()}:
            _fail(
                "TECH_ADAPTER_PACKAGE_NOT_REGISTERED",
                "Lifecycle record refers to an unregistered package hash.",
                details={"field": "package_sha256", "value": package_sha256},
                next_action="Register and verify the exact package before adding lifecycle evidence.",
            )
        if action not in LIFECYCLE_ACTIONS:
            _fail(
                "TECH_ADAPTER_LIFECYCLE_ACTION_INVALID",
                "Lifecycle action must be qualified, deprecated, or revoked.",
                details={"field": "action", "value": action, "allowed": sorted(LIFECYCLE_ACTIONS)},
                next_action="Choose one supported lifecycle action.",
            )
        package_lifecycle = [
            record for record in self._lifecycle if record["package_sha256"] == package_sha256
        ]
        if package_lifecycle and package_lifecycle[-1]["action"] == "revoked":
            _fail(
                "TECH_ADAPTER_REVOKED_TERMINAL_STATE",
                "Revocation is terminal for an exact adapter package.",
                details={"field": "action", "package_sha256": package_sha256, "value": action},
                next_action="Register a new explicitly versioned package instead of reviving a revoked package.",
            )
        for field_name, value in (
            ("reason", reason),
            ("recorded_at", recorded_at),
            ("signer_reference", signer_reference),
        ):
            if not isinstance(value, str) or not value.strip():
                _fail(
                    "TECH_ADAPTER_LIFECYCLE_FIELD_REQUIRED",
                    f"{field_name} is required for an append-only lifecycle record.",
                    details={"field": field_name, "value": value},
                    next_action=f"Provide the trusted {field_name} value.",
                )
        normalized_recorded_at = self._validate_recorded_at(
            recorded_at, path="append_lifecycle_record"
        )
        hashes = {"package_sha256": package_sha256, "signature_sha256": signature_sha256}
        if qualification_receipt_sha256 is not None:
            hashes["qualification_receipt_sha256"] = qualification_receipt_sha256
        invalid_hashes = sorted(
            field_name
            for field_name, value in hashes.items()
            if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value)
        )
        if invalid_hashes:
            _fail(
                "TECH_ADAPTER_LIFECYCLE_HASH_INVALID",
                "Lifecycle hash fields must be lowercase SHA-256 digests.",
                details={"field": invalid_hashes[0], "invalid_fields": invalid_hashes},
                next_action="Provide the exact signed evidence hashes.",
            )
        previous_digest = (
            canonical_sha256(package_lifecycle[-1]) if package_lifecycle else None
        )
        record = {
            "schema_version": 2,
            "package_sha256": package_sha256,
            "sequence": len(package_lifecycle) + 1,
            "prev_record_sha256": previous_digest,
            "action": action,
            "reason": reason.strip(),
            "recorded_at": normalized_recorded_at,
            "signer_reference": signer_reference.strip(),
            "signature_sha256": signature_sha256,
            "qualification_receipt_sha256": qualification_receipt_sha256,
        }
        record_sha256 = canonical_sha256(record)
        if self.root is not None:
            self._publish_document(self.root / "lifecycle", record_sha256, record)
            external_receipt = self._append_external_lifecycle_head(
                package_sha256=package_sha256,
                sequence=record["sequence"],
                record_sha256=record_sha256,
            )
            self._write_lifecycle_head(
                package_sha256=package_sha256,
                sequence=record["sequence"],
                record_sha256=record_sha256,
                external_receipt=external_receipt,
            )
        if not any(canonical_sha256(existing) == record_sha256 for existing in self._lifecycle):
            self._lifecycle.append(record)
            self._lifecycle.sort(
                key=lambda item: (
                    item["package_sha256"],
                    int(item.get("sequence", 1)),
                )
            )
        return {"ok": True, "record_sha256": record_sha256, "record": immutable_json_copy(record)}

    def resolve(
        self,
        identity: Mapping[str, Any],
        *,
        expected_package_sha256: str | None = None,
        allow_deprecated: bool = False,
    ) -> dict[str, Any]:
        key = TechnologyAdapterKey.from_mapping(identity)
        entry = self._packages.get(key)
        if entry is None:
            candidates = []
            for candidate in sorted(self._packages):
                differences = {
                    field: {"requested": getattr(key, field), "registered": getattr(candidate, field)}
                    for field in IDENTITY_FIELDS
                    if getattr(key, field) != getattr(candidate, field)
                }
                candidates.append({"identity": candidate.to_dict(), "differences": differences})
            _fail(
                "TECH_ADAPTER_EXACT_MATCH_NOT_FOUND",
                "No technology adapter package matches every requested identity field.",
                details={"field": "identity", "identity": key.to_dict(), "candidates": candidates},
                next_action="Select an exact listed key or register a separately versioned package.",
            )
        package_sha256, package = entry
        if expected_package_sha256 is not None and expected_package_sha256 != package_sha256:
            _fail(
                "TECH_ADAPTER_PACKAGE_HASH_DRIFT",
                "The exact adapter key now resolves to a different package hash than the pinned job.",
                details={
                    "field": "expected_package_sha256",
                    "expected": expected_package_sha256,
                    "received": package_sha256,
                    "identity": key.to_dict(),
                },
                next_action="Restore the pinned package or create and approve a new immutable job revision.",
            )
        lifecycle = [record for record in self._lifecycle if record["package_sha256"] == package_sha256]
        latest_action = lifecycle[-1]["action"] if lifecycle else None
        if latest_action == "revoked" or (latest_action == "deprecated" and not allow_deprecated):
            _fail(
                "TECH_ADAPTER_VERSION_STALE" if latest_action == "deprecated" else "TECH_ADAPTER_REVOKED",
                f"The exact technology adapter package is {latest_action}.",
                details={
                    "field": "identity.package_version",
                    "identity": key.to_dict(),
                    "package_sha256": package_sha256,
                    "lifecycle": lifecycle,
                },
                next_action="Select an explicitly qualified package version and create a new job revision.",
            )
        return {
            "ok": True,
            "identity": key.to_dict(),
            "package_sha256": package_sha256,
            "package": immutable_json_copy(package),
            "lifecycle": immutable_json_copy(lifecycle),
            "qualified": latest_action == "qualified",
            "fallback_used": False,
        }

    def snapshot(self) -> dict[str, Any]:
        snapshot = {
            "schema_version": 1,
            "entries": [
                {"identity": key.to_dict(), "package_sha256": value[0]}
                for key, value in sorted(self._packages.items())
            ],
            "lifecycle_record_sha256s": [canonical_sha256(record) for record in self._lifecycle],
            "lookup_policy": "exact_only_no_alias_or_fallback",
        }
        snapshot_sha256 = canonical_sha256(snapshot)
        if self.root is not None:
            self._publish_document(self.root / "snapshots", snapshot_sha256, snapshot)
        return {
            "snapshot": immutable_json_copy(snapshot),
            "snapshot_sha256": snapshot_sha256,
        }

    def contract(self) -> dict[str, Any]:
        return {
            "exact_identity_fields": list(IDENTITY_FIELDS),
            "wildcard_or_alias_fallback": False,
            "append_only_packages": True,
            "append_only_lifecycle": True,
            "lifecycle_trusted_head_required": self.root is not None,
            "external_lifecycle_anchor_configured": self.lifecycle_trust_anchor
            is not None,
            "writer_compromise_rollback_detection": self.lifecycle_trust_anchor
            is not None,
            "local_head_only_detects_writer_compromise": False,
            "registered_package_count": len(self._packages),
            "snapshot_content_addressed": True,
        }
