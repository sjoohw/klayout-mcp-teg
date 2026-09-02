"""Host-controlled persistent state for resumable, content-addressed TEG jobs."""

from __future__ import annotations

import json
import hashlib
import os
import re
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Protocol, runtime_checkable

from .approval import (
    ApprovalVerifier,
    require_host_approval_verifier,
    verify_design_intent_approval,
)
from .errors import AnalysisError
from .evidence_state import evaluate_evidence_ladder
from .file_publication import (
    OutputAlreadyExistsError,
    publication_root_doctor,
    publication_staging_prefix,
    publish_new_file,
    scavenge_stale_publication_entries,
)
from .external_evidence import (
    ExternalEvidenceAdapterRegistry,
    SignoffPolicy,
    evaluate_signoff_policy,
    verify_external_report,
)
from .process_capability import validate_process_capability
from .technology_registry import TechnologyAdapterRegistry
from .validation_report import ClarificationQuestion, ValidationReport
from .workflow_manifest import (
    SHA256_PATTERN,
    build_job_manifest,
    canonical_json_bytes,
    canonical_sha256,
    immutable_json_copy,
    validate_approved_design_intent_reference,
    validate_design_intent_draft,
    validate_measurement_manifest,
)


JOB_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,95}$")
WINDOWS_RESERVED_JOB_IDS = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{index}" for index in range(1, 10)),
    *(f"lpt{index}" for index in range(1, 10)),
}
OUTPUT_FORMATS = {"gds": ".gds", "oas": ".oas"}
WORKFLOW_REFERENCE_KINDS = {
    "plan": "plan",
    "generation_result": "generation_result",
    "measurement": "measurement_manifest",
    "external-evidence": "external_evidence",
    "signoff-policy": "signoff_policy_result",
}
WORKFLOW_REFERENCE_PATTERN = re.compile(
    r"^workflow://(?P<namespace>[a-z][a-z0-9_-]*)/(?P<sha256>[0-9a-f]{64})$"
)


def _fail(code: str, message: str, *, details: Mapping[str, Any]) -> None:
    raise AnalysisError(
        code=code,
        message=message,
        details={**dict(details), "production_ready": False},
        next_action="Restore the exact content-addressed workflow inputs and retry.",
    )


def _utc_now(clock: Callable[[], datetime] | None = None) -> datetime:
    try:
        value = (clock or (lambda: datetime.now(timezone.utc)))()
    except Exception as exc:
        _fail(
            "WORKFLOW_CLOCK_FAILED",
            "The host workflow clock failed closed.",
            details={"error_type": type(exc).__name__},
        )
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail(
            "INVALID_WORKFLOW_CLOCK",
            "The host workflow clock must return a timezone-aware datetime.",
            details={"received_type": type(value).__name__},
        )
    return value.astimezone(timezone.utc)


def _job_id(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not JOB_ID_PATTERN.fullmatch(value)
        or value.casefold() in WINDOWS_RESERVED_JOB_IDS
    ):
        _fail(
            "INVALID_WORKFLOW_JOB_ID",
            (
                "job_id must be a lowercase filesystem-safe identifier and cannot "
                "use a reserved Windows device name."
            ),
            details={"job_id": value},
        )
    return value


def _sha256(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        _fail(
            "INVALID_WORKFLOW_STORE_HASH",
            f"{field} must be a lowercase SHA-256 digest.",
            details={"field": field, "value": value},
        )
    return value


def _resolved_inside(root: Path, candidate: Path, *, field: str) -> Path:
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve()

    def comparable(value: Path) -> str:
        text = str(value)
        if os.name == "nt":
            # Path.resolve may add the Win32 extended-length prefix only after a
            # concurrently-created path crosses MAX_PATH. Normalize both sides
            # before containment comparison without weakening realpath checks.
            if text.startswith("\\\\?\\UNC\\"):
                text = "\\\\" + text[8:]
            elif text.startswith("\\\\?\\"):
                text = text[4:]
        return os.path.normcase(os.path.normpath(text))

    root_comparable = comparable(resolved_root)
    candidate_comparable = comparable(resolved_candidate)
    try:
        common = os.path.commonpath([root_comparable, candidate_comparable])
    except ValueError:
        common = ""
    if common != root_comparable:
        _fail(
            "WORKFLOW_PATH_OUTSIDE_HOST_ROOT",
            "A workflow path escaped its host-controlled root.",
            details={
                "field": field,
                "host_root": str(resolved_root),
                "candidate": str(resolved_candidate),
            },
        )
    return resolved_candidate


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with _native_io_path(path).open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        _fail(
            "WORKFLOW_FILE_HASH_FAILED",
            "A workflow artifact could not be read for SHA-256 verification.",
            details={"path": str(path), "error_type": type(exc).__name__},
        )
    return digest.hexdigest()


def _native_io_path(path: Path) -> Path:
    """Return a Windows extended-length spelling for host-controlled absolute I/O."""

    if os.name != "nt":
        return path
    text = os.path.abspath(str(path))
    if text.startswith("\\\\?\\"):
        return Path(text)
    if text.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + text[2:])
    return Path("\\\\?\\" + text)


@runtime_checkable
class ProcessCapabilityProvider(Protocol):
    """Host-selected source for the active process capability snapshot."""

    provider_id: str
    trusted: bool

    def load(self, *, profile: str, version: str) -> Mapping[str, Any]: ...


class MappingProcessCapabilityProvider:
    """Immutable host mapping for bundled or deployment-approved capabilities."""

    trusted = True

    def __init__(
        self,
        profiles: Mapping[tuple[str, str], Mapping[str, Any]],
        *,
        provider_id: str = "host-process-capability-registry",
    ) -> None:
        if not isinstance(provider_id, str) or not provider_id.strip():
            _fail(
                "INVALID_PROCESS_CAPABILITY_PROVIDER",
                "A process provider requires a stable host identity.",
                details={"provider_id": provider_id},
            )
        self.provider_id = provider_id.strip()
        self._profiles = {
            (profile, version): immutable_json_copy(capability)
            for (profile, version), capability in profiles.items()
        }

    def load(self, *, profile: str, version: str) -> Mapping[str, Any]:
        capability = self._profiles.get((profile, version))
        if capability is None:
            _fail(
                "PROCESS_CAPABILITY_NOT_REGISTERED",
                "The exact process profile/version is not registered on this host.",
                details={
                    "profile": profile,
                    "version": version,
                    "registered": [
                        {"profile": key[0], "version": key[1]}
                        for key in sorted(self._profiles)
                    ],
                },
            )
        return immutable_json_copy(capability)


@runtime_checkable
class TegPlanningEngine(Protocol):
    """Host-selected planner hidden behind the small public facade."""

    engine_id: str

    def plan(
        self,
        *,
        design_intent: Mapping[str, Any],
        process_capability: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


@runtime_checkable
class TegGenerationEngine(Protocol):
    """Host-selected staged generator hidden behind the small public facade."""

    engine_id: str

    def generate(
        self,
        *,
        design_intent: Mapping[str, Any],
        process_capability: Mapping[str, Any],
        plan: Mapping[str, Any],
        output_path: str,
    ) -> Mapping[str, Any]: ...


class WorkflowEngineRegistry:
    """Host-built profile dispatch; MCP arguments cannot import or register engines."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[TegPlanningEngine, TegGenerationEngine | None]] = {}

    def register(
        self,
        *,
        process_profile: str,
        planning_engine: TegPlanningEngine,
        generation_engine: TegGenerationEngine | None = None,
    ) -> None:
        if (
            not isinstance(process_profile, str)
            or not process_profile.strip()
            or not isinstance(planning_engine, TegPlanningEngine)
            or (
                generation_engine is not None
                and not isinstance(generation_engine, TegGenerationEngine)
            )
        ):
            _fail(
                "INVALID_WORKFLOW_ENGINE_REGISTRATION",
                "A host registry entry needs one exact profile and compatible engines.",
                details={"process_profile": process_profile},
            )
        profile = process_profile.strip()
        if profile in self._entries:
            _fail(
                "DUPLICATE_WORKFLOW_ENGINE_REGISTRATION",
                "A process profile can have only one host-selected engine entry.",
                details={"process_profile": profile},
            )
        self._entries[profile] = (planning_engine, generation_engine)

    def resolve(
        self, *, process_profile: str
    ) -> tuple[TegPlanningEngine, TegGenerationEngine | None]:
        entry = self._entries.get(process_profile)
        if entry is None:
            _fail(
                "WORKFLOW_PROFILE_ENGINE_UNAVAILABLE",
                "No host-approved workflow engine is registered for this process profile.",
                details={
                    "process_profile": process_profile,
                    "registered_profiles": sorted(self._entries),
                },
            )
        return entry

    def contract(self) -> dict[str, Any]:
        return {
            "host_controlled": True,
            "model_can_register_or_import_engines": False,
            "registered_profiles": sorted(self._entries),
        }

    def readiness(self) -> list[dict[str, Any]]:
        """Return non-mutating profile engine readiness for host startup diagnostics."""

        return [
            {
                "process_profile": profile,
                "planning_engine_id": planning.engine_id,
                "planning_engine_configured": True,
                "generation_engine_id": (
                    None if generation is None else generation.engine_id
                ),
                "generation_engine_configured": generation is not None,
            }
            for profile, (planning, generation) in sorted(self._entries.items())
        ]


def load_live_process_capability(
    *,
    design_intent_draft: Mapping[str, Any],
    provider: ProcessCapabilityProvider | None,
) -> dict[str, Any]:
    """Resolve and hash the active capability instead of trusting a stored claim."""

    draft_result = validate_design_intent_draft(design_intent_draft)
    draft = draft_result["document"]
    process_ref = draft["process"]
    if provider is None:
        _fail(
            "PROCESS_CAPABILITY_PROVIDER_UNAVAILABLE",
            "No trusted live process capability provider is configured.",
            details={"provider_configured": False},
        )
    if (
        not isinstance(provider, ProcessCapabilityProvider)
        or getattr(provider, "trusted", None) is not True
        or not isinstance(getattr(provider, "provider_id", None), str)
        or not provider.provider_id.strip()
    ):
        _fail(
            "UNTRUSTED_PROCESS_CAPABILITY_PROVIDER",
            "The configured process capability provider is not trusted.",
            details={"provider_type": type(provider).__name__},
        )
    try:
        raw_capability = provider.load(
            profile=process_ref["profile"],
            version=process_ref["version"],
        )
    except Exception as exc:
        _fail(
            "PROCESS_CAPABILITY_PROVIDER_FAILED",
            "The live process capability provider failed closed.",
            details={
                "provider_id": provider.provider_id,
                "error_type": type(exc).__name__,
            },
        )
    if not isinstance(raw_capability, Mapping):
        _fail(
            "INVALID_LIVE_PROCESS_CAPABILITY",
            "The provider returned a non-object process capability.",
            details={"provider_id": provider.provider_id},
        )
    normalized = validate_process_capability(raw_capability)
    identity = normalized["process"]
    mismatches: dict[str, Any] = {}
    if identity["name"] != process_ref["profile"]:
        mismatches["profile"] = {
            "expected": process_ref["profile"],
            "actual": identity["name"],
        }
    if identity["version"] != process_ref["version"]:
        mismatches["version"] = {
            "expected": process_ref["version"],
            "actual": identity["version"],
        }
    live_hash = canonical_sha256(normalized)
    if live_hash != process_ref["capability_sha256"]:
        mismatches["capability_sha256"] = {
            "expected": process_ref["capability_sha256"],
            "actual": live_hash,
        }
    if mismatches:
        _fail(
            "LIVE_PROCESS_CAPABILITY_MISMATCH",
            "The active process capability differs from the approved design intent.",
            details={"provider_id": provider.provider_id, "mismatches": mismatches},
        )
    return {
        "ok": True,
        "provider_id": provider.provider_id,
        "capability": immutable_json_copy(normalized),
        "capability_sha256": live_hash,
        "process": dict(identity),
        "production_ready": normalized["production_ready"],
    }


def load_process_capability_by_identity(
    *,
    profile: str,
    version: str,
    provider: ProcessCapabilityProvider | None,
) -> dict[str, Any]:
    """Load a template capability by explicit identity without granting authority."""

    if not isinstance(profile, str) or not profile.strip() or not isinstance(version, str) or not version.strip():
        _fail(
            "PROCESS_CAPABILITY_IDENTITY_REQUIRED",
            "Template discovery requires an explicit process profile and version.",
            details={"profile": profile, "version": version},
        )
    if provider is None or not isinstance(provider, ProcessCapabilityProvider):
        _fail(
            "PROCESS_CAPABILITY_PROVIDER_UNAVAILABLE",
            "No trusted process capability provider is configured.",
            details={"provider_configured": False},
        )
    if getattr(provider, "trusted", None) is not True:
        _fail(
            "UNTRUSTED_PROCESS_CAPABILITY_PROVIDER",
            "The configured process capability provider is not trusted.",
            details={"provider_type": type(provider).__name__},
        )
    try:
        raw = provider.load(profile=profile.strip(), version=version.strip())
    except Exception as exc:
        _fail(
            "PROCESS_CAPABILITY_PROVIDER_FAILED",
            "The process capability provider failed during template discovery.",
            details={"error_type": type(exc).__name__},
        )
    if not isinstance(raw, Mapping):
        _fail(
            "INVALID_LIVE_PROCESS_CAPABILITY",
            "The provider returned a non-object process capability.",
            details={"provider_id": provider.provider_id},
        )
    normalized = validate_process_capability(raw)
    identity = normalized["process"]
    if identity["name"] != profile.strip() or identity["version"] != version.strip():
        _fail(
            "LIVE_PROCESS_CAPABILITY_MISMATCH",
            "The provider returned a different process identity.",
            details={"expected": [profile, version], "actual": dict(identity)},
        )
    return {
        "capability": immutable_json_copy(normalized),
        "capability_sha256": canonical_sha256(normalized),
        "provider_id": provider.provider_id,
    }


class WorkflowJobStore:
    """Append-only documents/manifests with one integrity-checked mutable head."""

    def __init__(
        self,
        root: str | Path,
        *,
        output_root: str | Path | None = None,
        initialize: bool = True,
    ):
        self.root = Path(root).expanduser().resolve()
        self.output_root = Path(output_root or (self.root / "outputs")).expanduser().resolve()
        self.documents_root = self.root / "documents"
        self.manifests_root = self.root / "manifests"
        self.jobs_root = self.root / "jobs"
        self.drafts_root = self.root / "drafts"
        self.locks_root = self.root / "locks"
        if initialize:
            for directory in (
                self.root,
                self.output_root,
                self.documents_root,
                self.manifests_root,
                self.jobs_root,
                self.drafts_root,
                self.locks_root,
            ):
                directory.mkdir(parents=True, exist_ok=True)
        _resolved_inside(self.root, self.documents_root, field="documents_root")
        _resolved_inside(self.root, self.manifests_root, field="manifests_root")
        _resolved_inside(self.root, self.jobs_root, field="jobs_root")
        _resolved_inside(self.root, self.drafts_root, field="drafts_root")
        _resolved_inside(self.root, self.locks_root, field="locks_root")

    def publication_status(self, *, active_probe: bool = False) -> dict[str, object]:
        """Report create-only publication readiness for the configured output root."""

        return publication_root_doctor(self.output_root, active_probe=active_probe)

    def scavenge_staging(self, *, ttl_seconds: float) -> dict[str, object]:
        """Remove only expired, owner-tagged publication staging entries."""

        return scavenge_stale_publication_entries(
            self.output_root,
            ttl_seconds=ttl_seconds,
        )

    @contextmanager
    def _job_lock(self, job_id: str) -> Iterator[None]:
        """Serialize one job's head update across threads and local processes."""

        safe_job_id = _job_id(job_id)
        lock_path = _resolved_inside(
            self.locks_root,
            self.locks_root / f"{safe_job_id}.lock",
            field="job_lock_path",
        )
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _read_json(path: Path) -> Any:
        try:
            return json.loads(_native_io_path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _fail(
                "WORKFLOW_STORE_READ_FAILED",
                "A persisted workflow object could not be read as JSON.",
                details={"path": str(path), "error_type": type(exc).__name__},
            )

    @staticmethod
    def _atomic_write(path: Path, payload: bytes, *, replace: bool) -> None:
        native_path = _native_io_path(path)
        native_path.parent.mkdir(parents=True, exist_ok=True)
        # Do not repeat a 64-character content hash in the temporary basename.
        # Keeping this name short avoids legacy Win32 path-length failures while
        # preserving same-directory atomic replacement on Windows and Linux.
        temporary = native_path.parent / (
            f"{publication_staging_prefix('workflow-document')}{uuid.uuid4().hex}"
        )
        try:
            with temporary.open("xb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            if replace:
                os.replace(temporary, native_path)
            else:
                try:
                    publish_new_file(temporary, native_path)
                except OutputAlreadyExistsError:
                    existing = native_path.read_bytes()
                    if existing != payload:
                        _fail(
                            "WORKFLOW_CONTENT_ADDRESS_COLLISION",
                            "Existing content-addressed storage differs at the same digest.",
                            details={"path": str(path)},
                        )
                if native_path.read_bytes() != payload:
                    _fail(
                        "WORKFLOW_STORE_WRITE_INTEGRITY_FAILURE",
                        "A content-addressed write failed its immediate readback check.",
                        details={"path": str(path)},
                    )
        finally:
            temporary.unlink(missing_ok=True)

    def put_document(self, kind: str, document: Mapping[str, Any]) -> str:
        if not isinstance(kind, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", kind):
            _fail(
                "INVALID_WORKFLOW_DOCUMENT_KIND",
                "Document kind must be a filesystem-safe identifier.",
                details={"kind": kind},
            )
        payload = canonical_json_bytes(document)
        digest = canonical_sha256(document)
        path = _resolved_inside(
            self.documents_root,
            self.documents_root / kind / f"{digest}.json",
            field="document_path",
        )
        self._atomic_write(path, payload, replace=False)
        return digest

    def get_document(self, kind: str, digest: str) -> dict[str, Any]:
        _sha256(digest, field="document_sha256")
        path = _resolved_inside(
            self.documents_root,
            self.documents_root / kind / f"{digest}.json",
            field="document_path",
        )
        if not _native_io_path(path).is_file():
            _fail(
                "WORKFLOW_DOCUMENT_NOT_FOUND",
                "The content-addressed workflow document was not found.",
                details={"kind": kind, "sha256": digest},
            )
        document = self._read_json(path)
        actual = canonical_sha256(document)
        if actual != digest:
            _fail(
                "WORKFLOW_DOCUMENT_INTEGRITY_FAILURE",
                "The persisted workflow document hash no longer matches its address.",
                details={"kind": kind, "expected": digest, "actual": actual},
            )
        return document

    def append_draft_revision(
        self,
        *,
        draft_id: str,
        document: Mapping[str, Any],
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """Append or idempotently reuse one immutable intake draft revision."""

        safe_draft_id = _job_id(draft_id)
        if expected_revision is not None and (
            isinstance(expected_revision, bool)
            or not isinstance(expected_revision, int)
            or expected_revision < 0
        ):
            _fail(
                "INVALID_DRAFT_REVISION",
                "expected_draft_revision must be a non-negative integer.",
                details={"field": "expected_draft_revision", "value": expected_revision},
            )
        draft_directory = _resolved_inside(
            self.drafts_root,
            self.drafts_root / safe_draft_id,
            field="draft_directory",
        )
        with self._job_lock(safe_draft_id):
            draft_directory.mkdir(parents=True, exist_ok=True)
            revision_paths = sorted(draft_directory.glob("[0-9][0-9][0-9][0-9][0-9][0-9]-*.json"))
            current_record = None if not revision_paths else self._read_json(revision_paths[-1])
            current_revision = 0 if current_record is None else current_record.get("revision")
            if not isinstance(current_revision, int) or current_revision < 0:
                _fail(
                    "DRAFT_REVISION_STORE_INTEGRITY_FAILURE",
                    "The latest persisted draft revision is malformed.",
                    details={"draft_id": safe_draft_id},
                )
            if expected_revision is not None and expected_revision != current_revision:
                _fail(
                    "DRAFT_REVISION_CONFLICT",
                    "The intake draft changed after the caller last read it.",
                    details={
                        "draft_id": safe_draft_id,
                        "field": "expected_draft_revision",
                        "expected": expected_revision,
                        "received": current_revision,
                    },
                )
            normalized = immutable_json_copy(document)
            document_sha256 = canonical_sha256(normalized)
            if current_record is not None and current_record.get("document_sha256") == document_sha256:
                return {
                    **current_record,
                    "record_sha256": canonical_sha256(current_record),
                    "resume_token": canonical_sha256(
                        {
                            "draft_id": safe_draft_id,
                            "revision": current_revision,
                            "record_sha256": canonical_sha256(current_record),
                        }
                    ),
                    "idempotent": True,
                }
            revision = current_revision + 1
            parent_record_sha256 = (
                None if current_record is None else canonical_sha256(current_record)
            )
            record = {
                "schema_version": 1,
                "draft_id": safe_draft_id,
                "revision": revision,
                "parent_record_sha256": parent_record_sha256,
                "document_sha256": document_sha256,
                "document": normalized,
            }
            record_sha256 = canonical_sha256(record)
            revision_path = draft_directory / f"{revision:06d}-{record_sha256}.json"
            self._atomic_write(
                revision_path,
                canonical_json_bytes(record),
                replace=False,
            )
            return {
                **record,
                "record_sha256": record_sha256,
                "resume_token": canonical_sha256(
                    {
                        "draft_id": safe_draft_id,
                        "revision": revision,
                        "record_sha256": record_sha256,
                    }
                ),
                "idempotent": False,
            }

    def get_draft_revision(
        self,
        *,
        draft_id: str,
        revision: int | None = None,
        resume_token: str | None = None,
    ) -> dict[str, Any]:
        """Load and hash-verify one immutable draft revision."""

        safe_draft_id = _job_id(draft_id)
        draft_directory = _resolved_inside(
            self.drafts_root,
            self.drafts_root / safe_draft_id,
            field="draft_directory",
        )
        paths = sorted(draft_directory.glob("[0-9][0-9][0-9][0-9][0-9][0-9]-*.json"))
        if revision is not None:
            paths = [path for path in paths if path.name.startswith(f"{revision:06d}-")]
        if not paths:
            _fail(
                "DRAFT_REVISION_NOT_FOUND",
                "The requested immutable intake draft revision was not found.",
                details={"draft_id": safe_draft_id, "revision": revision},
            )
        record = self._read_json(paths[-1])
        record_sha256 = canonical_sha256(record)
        if paths[-1].stem.split("-", 1)[-1] != record_sha256:
            _fail(
                "DRAFT_REVISION_STORE_INTEGRITY_FAILURE",
                "The persisted draft revision no longer matches its content address.",
                details={"draft_id": safe_draft_id, "revision": record.get("revision")},
            )
        expected_token = canonical_sha256(
            {
                "draft_id": safe_draft_id,
                "revision": record["revision"],
                "record_sha256": record_sha256,
            }
        )
        if resume_token is not None and resume_token != expected_token:
            _fail(
                "DRAFT_RESUME_TOKEN_MISMATCH",
                "The resume token does not identify this exact draft revision.",
                details={"draft_id": safe_draft_id, "revision": record["revision"]},
            )
        if canonical_sha256(record.get("document")) != record.get("document_sha256"):
            _fail(
                "DRAFT_REVISION_STORE_INTEGRITY_FAILURE",
                "The persisted draft document hash is invalid.",
                details={"draft_id": safe_draft_id, "revision": record.get("revision")},
            )
        return {
            **record,
            "record_sha256": record_sha256,
            "resume_token": expected_token,
        }

    def append_manifest(
        self,
        document: Mapping[str, Any],
        *,
        expected_parent_sha256: str | None,
    ) -> dict[str, Any]:
        job_id = _job_id(document.get("job_id"))
        with self._job_lock(job_id):
            current = self.head(job_id, required=False)
            current_hash = current["manifest_sha256"] if current is not None else None
            if current_hash != expected_parent_sha256:
                _fail(
                    "WORKFLOW_JOB_HEAD_CONFLICT",
                    "The job head changed or the requested parent is stale.",
                    details={
                        "job_id": job_id,
                        "expected_parent_sha256": expected_parent_sha256,
                        "actual_head_sha256": current_hash,
                    },
                )
            parent = current["manifest"] if current is not None else None
            result = build_job_manifest(document, parent_manifest=parent)
            digest = result["manifest_sha256"]
            manifest_path = _resolved_inside(
                self.manifests_root,
                self.manifests_root / f"{digest}.json",
                field="manifest_path",
            )
            self._atomic_write(
                manifest_path,
                canonical_json_bytes(result["manifest"]),
                replace=False,
            )
            pointer = {
                "schema_version": 1,
                "job_id": job_id,
                "manifest_sha256": digest,
            }
            pointer_path = _resolved_inside(
                self.jobs_root,
                self.jobs_root / job_id / "head.json",
                field="job_head_path",
            )
            self._atomic_write(pointer_path, canonical_json_bytes(pointer), replace=True)
            return result

    def head(self, job_id: str, *, required: bool = True) -> dict[str, Any] | None:
        safe_job_id = _job_id(job_id)
        pointer_path = _resolved_inside(
            self.jobs_root,
            self.jobs_root / safe_job_id / "head.json",
            field="job_head_path",
        )
        if not _native_io_path(pointer_path).is_file():
            if required:
                _fail(
                    "WORKFLOW_JOB_NOT_FOUND",
                    "No persisted workflow job exists for this identifier.",
                    details={"job_id": safe_job_id},
                )
            return None
        pointer = self._read_json(pointer_path)
        if (
            not isinstance(pointer, Mapping)
            or pointer.get("schema_version") != 1
            or pointer.get("job_id") != safe_job_id
        ):
            _fail(
                "WORKFLOW_JOB_HEAD_INTEGRITY_FAILURE",
                "The mutable job head pointer is malformed.",
                details={"job_id": safe_job_id},
            )
        digest = _sha256(pointer.get("manifest_sha256"), field="manifest_sha256")
        path = _resolved_inside(
            self.manifests_root,
            self.manifests_root / f"{digest}.json",
            field="manifest_path",
        )
        if not _native_io_path(path).is_file():
            _fail(
                "WORKFLOW_JOB_HEAD_INTEGRITY_FAILURE",
                "The job head references a missing manifest.",
                details={"job_id": safe_job_id, "manifest_sha256": digest},
            )
        manifest = self._validate_manifest_ancestry(
            digest,
            expected_job_id=safe_job_id,
            seen=set(),
        )
        return {"manifest_sha256": digest, "manifest": manifest}

    def _validate_manifest_ancestry(
        self,
        digest: str,
        *,
        expected_job_id: str,
        seen: set[str],
    ) -> dict[str, Any]:
        if digest in seen or len(seen) >= 256:
            _fail(
                "WORKFLOW_MANIFEST_ANCESTRY_INVALID",
                "The persisted manifest ancestry contains a cycle or exceeds its bound.",
                details={"job_id": expected_job_id, "manifest_sha256": digest},
            )
        seen.add(digest)
        manifest = self._load_manifest(digest)
        if manifest.get("job_id") != expected_job_id:
            _fail(
                "WORKFLOW_MANIFEST_ANCESTRY_INVALID",
                "A manifest ancestor belongs to another job.",
                details={
                    "expected_job_id": expected_job_id,
                    "actual_job_id": manifest.get("job_id"),
                    "manifest_sha256": digest,
                },
            )
        parent_hash = manifest["parent_manifest_sha256"]
        parent = (
            None
            if parent_hash is None
            else self._validate_manifest_ancestry(
                parent_hash,
                expected_job_id=expected_job_id,
                seen=seen,
            )
        )
        build_job_manifest(manifest, parent_manifest=parent)
        return manifest

    def _load_manifest(self, digest: str) -> dict[str, Any]:
        _sha256(digest, field="manifest_sha256")
        path = _resolved_inside(
            self.manifests_root,
            self.manifests_root / f"{digest}.json",
            field="manifest_path",
        )
        if not _native_io_path(path).is_file():
            _fail(
                "WORKFLOW_MANIFEST_NOT_FOUND",
                "A parent workflow manifest is missing.",
                details={"manifest_sha256": digest},
            )
        manifest = self._read_json(path)
        if canonical_sha256(manifest) != digest:
            _fail(
                "WORKFLOW_MANIFEST_INTEGRITY_FAILURE",
                "A parent workflow manifest failed content verification.",
                details={"manifest_sha256": digest},
            )
        return manifest

    def prepare_output_path(
        self,
        *,
        job_id: str,
        output_name: str,
        output_format: str,
    ) -> Path:
        safe_job_id = _job_id(job_id)
        if output_format not in OUTPUT_FORMATS:
            _fail(
                "UNSUPPORTED_WORKFLOW_OUTPUT_FORMAT",
                "Only the configured stream output formats are accepted.",
                details={"output_format": output_format, "allowed": sorted(OUTPUT_FORMATS)},
            )
        if (
            not isinstance(output_name, str)
            or not output_name
            or Path(output_name).name != output_name
            or output_name in {".", ".."}
            or not output_name.lower().endswith(OUTPUT_FORMATS[output_format])
        ):
            _fail(
                "INVALID_WORKFLOW_OUTPUT_NAME",
                "Output must be one filename with the approved stream extension.",
                details={"output_name": output_name, "output_format": output_format},
            )
        target = _resolved_inside(
            self.output_root,
            self.output_root / safe_job_id / output_name,
            field="output_path",
        )
        if _native_io_path(target).exists():
            _fail(
                "WORKFLOW_OUTPUT_ALREADY_EXISTS",
                (
                    "Generation requires a new output path. If a concurrent writer publishes "
                    "during generation, create-only promotion preserves that winner."
                ),
                details={"path": str(target)},
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def prepare_staging_path(self, *, final_target: Path) -> Path:
        """Allocate a unique same-directory stream path for durable promotion."""

        target = _resolved_inside(
            self.output_root,
            final_target,
            field="final_output_path",
        )
        staged = target.parent / f".stage-{uuid.uuid4().hex}{target.suffix.lower()}"
        return _resolved_inside(self.output_root, staged, field="staged_output_path")

    def promote_staged_output(
        self,
        *,
        staged_path: Path,
        final_target: Path,
        expected_sha256: str,
    ) -> Path:
        """Publish a verified sibling copy without replacing a concurrent winner."""

        expected = _sha256(expected_sha256, field="staged_layout_sha256")
        staged = _resolved_inside(
            self.output_root,
            staged_path,
            field="staged_output_path",
        )
        target = _resolved_inside(
            self.output_root,
            final_target,
            field="final_output_path",
        )
        if _native_io_path(target).is_file():
            actual = _file_sha256(target)
            if actual != expected:
                _fail(
                    "WORKFLOW_FINAL_PROMOTION_CONFLICT",
                    "The final output name already contains different content.",
                    details={
                        "path": str(target),
                        "expected_sha256": expected,
                        "actual_sha256": actual,
                    },
                )
            return target
        if not _native_io_path(staged).is_file():
            _fail(
                "WORKFLOW_STAGED_OUTPUT_MISSING",
                "The durable staged layout is missing before final promotion.",
                details={"path": str(staged), "expected_sha256": expected},
            )
        actual_staged = _file_sha256(staged)
        if actual_staged != expected:
            _fail(
                "WORKFLOW_STAGED_OUTPUT_INTEGRITY_FAILURE",
                "The durable staged layout changed before final promotion.",
                details={
                    "path": str(staged),
                    "expected_sha256": expected,
                    "actual_sha256": actual_staged,
                },
            )
        temporary = target.parent / (
            f"{publication_staging_prefix('workflow-promote')}{uuid.uuid4().hex}"
        )
        native_staged = _native_io_path(staged)
        native_temporary = _native_io_path(temporary)
        native_target = _native_io_path(target)
        try:
            with native_staged.open("rb") as source, native_temporary.open("xb") as destination:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    destination.write(block)
                destination.flush()
                os.fsync(destination.fileno())
            if _file_sha256(temporary) != expected:
                _fail(
                    "WORKFLOW_FINAL_PROMOTION_INTEGRITY_FAILURE",
                    "The final promotion copy failed its pre-commit hash check.",
                    details={"path": str(temporary)},
                )
            try:
                publish_new_file(native_temporary, native_target)
            except OutputAlreadyExistsError:
                if not native_target.is_file():
                    _fail(
                        "WORKFLOW_FINAL_PROMOTION_CONFLICT",
                        "The final output name is occupied by a non-file target.",
                        details={"path": str(target)},
                    )
                actual_target = _file_sha256(target)
                if actual_target != expected:
                    _fail(
                        "WORKFLOW_FINAL_PROMOTION_CONFLICT",
                        "A concurrent writer published different final output content.",
                        details={
                            "path": str(target),
                            "expected_sha256": expected,
                            "actual_sha256": actual_target,
                        },
                    )
        finally:
            native_temporary.unlink(missing_ok=True)
        if _file_sha256(target) != expected:
            _fail(
                "WORKFLOW_FINAL_PROMOTION_INTEGRITY_FAILURE",
                "The promoted final output failed immediate readback verification.",
                details={"path": str(target)},
            )
        return target


class TegWorkflowFacade:
    """Persistent security boundary used by the four high-level workflow calls."""

    def __init__(
        self,
        *,
        store: WorkflowJobStore,
        process_provider: ProcessCapabilityProvider | None,
        approval_verifier: ApprovalVerifier | None = None,
        planning_engine: TegPlanningEngine | None = None,
        generation_engine: TegGenerationEngine | None = None,
        engine_registry: WorkflowEngineRegistry | None = None,
        external_evidence_registry: ExternalEvidenceAdapterRegistry | None = None,
        external_report_root: str | Path | None = None,
        signoff_policy: SignoffPolicy | None = None,
        technology_registry: TechnologyAdapterRegistry | None = None,
        output_class: str = "nonproduction_gds",
        production_mode: bool = True,
        clock: Callable[[], datetime] | None = None,
    ):
        self.store = store
        self.process_provider = process_provider
        self.approval_verifier = approval_verifier
        self.planning_engine = planning_engine
        self.generation_engine = generation_engine
        self.engine_registry = engine_registry
        self.external_evidence_registry = external_evidence_registry
        self.signoff_policy = signoff_policy
        self.technology_registry = technology_registry
        self.external_report_root = (
            None if external_report_root is None else Path(external_report_root).resolve()
        )
        if not isinstance(output_class, str) or not output_class.strip():
            _fail(
                "INVALID_FACADE_OUTPUT_CLASS",
                "The host facade output class must be a non-empty string.",
                details={"output_class": output_class},
            )
        self.output_class = output_class.strip()
        self.production_mode = production_mode
        self.clock = clock

    def _resolve_technology_adapter(
        self, design_intent: Mapping[str, Any]
    ) -> dict[str, Any] | None:
        reference = design_intent.get("technology_adapter")
        transistor_requested = any(
            device.get("family") == "transistor"
            for device in design_intent.get("devices", [])
            if isinstance(device, Mapping)
        )
        if reference is None:
            if (
                transistor_requested
                and self.production_mode
                and self.output_class == "production_gds"
            ):
                _fail(
                    "TECH_ADAPTER_REFERENCE_REQUIRED",
                    "Production transistor intent must pin an exact technology adapter package and registry snapshot.",
                    details={"field": "technology_adapter", "stage": "adapter_resolution"},
                )
            return None
        if self.technology_registry is None:
            _fail(
                "TECH_ADAPTER_REGISTRY_UNAVAILABLE",
                "The host has no immutable technology adapter registry configured.",
                details={"field": "technology_adapter", "stage": "adapter_resolution"},
            )
        snapshot = self.technology_registry.snapshot()
        if snapshot["snapshot_sha256"] != reference["registry_snapshot_sha256"]:
            _fail(
                "TECH_ADAPTER_REGISTRY_SNAPSHOT_DRIFT",
                "The active registry snapshot differs from the snapshot pinned by the design intent.",
                details={
                    "field": "technology_adapter.registry_snapshot_sha256",
                    "expected": reference["registry_snapshot_sha256"],
                    "received": snapshot["snapshot_sha256"],
                    "stage": "adapter_resolution",
                },
            )
        resolved = self.technology_registry.resolve(
            reference["identity"],
            expected_package_sha256=reference["package_sha256"],
        )
        if (
            self.production_mode
            and self.output_class == "production_gds"
            and not resolved["qualified"]
        ):
            _fail(
                "TECH_ADAPTER_NOT_QUALIFIED",
                "Production generation requires an explicitly qualified active adapter package.",
                details={
                    "field": "technology_adapter.package_sha256",
                    "package_sha256": resolved["package_sha256"],
                    "stage": "adapter_resolution",
                },
            )
        return {
            "package_sha256": resolved["package_sha256"],
            "registry_snapshot_sha256": snapshot["snapshot_sha256"],
            "package": resolved["package"],
            "qualified": resolved["qualified"],
        }

    def teg_intake(
        self,
        *,
        design_intent_draft: Mapping[str, Any] | None = None,
        job_id: str | None = None,
        draft_id: str | None = None,
        expected_draft_revision: int | None = None,
        resume_token: str | None = None,
        validate_only: bool = False,
        template_process_profile: str | None = None,
        template_process_version: str | None = None,
        template_family: str | None = None,
    ) -> dict[str, Any]:
        if design_intent_draft is None:
            return self._intake_template(
                process_profile=template_process_profile,
                process_version=template_process_version,
                family=template_family,
            )
        draft_result = validate_design_intent_draft(design_intent_draft)
        draft = draft_result["document"]
        draft_hash = draft_result["canonical_sha256"]
        live = load_live_process_capability(
            design_intent_draft=draft,
            provider=self.process_provider,
        )
        technology_adapter = self._resolve_technology_adapter(draft)
        safe_draft_id = _job_id(draft_id or f"draft-{draft_hash[:20]}")
        if resume_token is not None:
            current_draft = self.store.get_draft_revision(
                draft_id=safe_draft_id,
                resume_token=resume_token,
            )
            if (
                expected_draft_revision is not None
                and current_draft["revision"] != expected_draft_revision
            ):
                _fail(
                    "DRAFT_REVISION_CONFLICT",
                    "The supplied revision and resume token identify different draft states.",
                    details={
                        "draft_id": safe_draft_id,
                        "expected": expected_draft_revision,
                        "received": current_draft["revision"],
                    },
                )
        draft_revision = None
        if not validate_only:
            draft_revision = self.store.append_draft_revision(
                draft_id=safe_draft_id,
                document=draft,
                expected_revision=expected_draft_revision,
            )
        if not draft_result["draft_complete"]:
            questions = tuple(
                ClarificationQuestion(
                    question_id=f"{safe_draft_id}-q{index:03d}",
                    question=str(question),
                    reason="Planning cannot safely infer this design decision.",
                    answer_schema={"type": "string", "minLength": 1},
                )
                for index, question in enumerate(draft["unresolved_questions"], start=1)
            )
            clarification = ValidationReport.build(
                summary=(
                    f"intake 단계에서 사용자 결정 {len(questions)}건이 필요하여 "
                    "geometry 생성을 시작하지 않았습니다."
                ),
                issues=[],
                questions=questions,
                draft_id=safe_draft_id,
                draft_revision=(None if draft_revision is None else draft_revision["revision"]),
                next_action="Answer the listed questions and resubmit the corrected full draft.",
                retry_stage="intake",
                resume_token=(None if draft_revision is None else draft_revision["resume_token"]),
                stage_appended=draft_revision is not None,
            ).to_dict()
            return {
                "ok": True,
                "workflow_status": "input_required",
                "job_created": False,
                "design_intent_sha256": draft_hash,
                "draft_id": safe_draft_id,
                "draft_revision": None if draft_revision is None else draft_revision["revision"],
                "draft_persisted": draft_revision is not None,
                "resume_token": None if draft_revision is None else draft_revision["resume_token"],
                "validate_only": validate_only,
                "unresolved_questions": list(draft["unresolved_questions"]),
                "clarification_request": clarification,
                "authorizes_planning": False,
                "authorizes_generation": False,
                "production_ready": False,
            }
        if validate_only:
            return {
                "ok": True,
                "workflow_status": "preflight_complete",
                "job_created": False,
                "design_intent_sha256": draft_hash,
                "process_capability_sha256": live["capability_sha256"],
                "draft_id": safe_draft_id,
                "draft_persisted": False,
                "validate_only": True,
                "authorizes_planning": False,
                "authorizes_generation": False,
                "production_ready": False,
            }
        stored_draft_hash = self.store.put_document("design_intent", draft)
        self.store.put_document("process_capability", live["capability"])
        if technology_adapter is not None:
            self.store.put_document(
                "technology_adapter_package", technology_adapter["package"]
            )
        safe_job_id = _job_id(job_id or f"teg-{draft_hash[:20]}")
        created_at = _utc_now(self.clock).isoformat()
        manifest = {
            "schema_version": 1,
            "job_id": safe_job_id,
            "parent_manifest_sha256": None,
            "design_intent_sha256": draft_hash,
            "approved_intent_sha256": None,
            "process_capability_sha256": live["capability_sha256"],
            "stage": "intent_draft_complete",
            "evidence": {
                "draft_schema_valid": True,
                "unresolved_questions_zero": True,
            },
            "normalized_inputs": {
                "design_intent_sha256": draft_hash,
                "process_capability_sha256": live["capability_sha256"],
                **(
                    {}
                    if technology_adapter is None
                    else {
                        "technology_adapter_package_sha256": technology_adapter[
                            "package_sha256"
                        ],
                        "technology_adapter_registry_snapshot_sha256": technology_adapter[
                            "registry_snapshot_sha256"
                        ],
                    }
                ),
            },
            "outputs": [],
            "fingerprints": (
                {}
                if technology_adapter is None
                else {
                    "technology_adapter_package": technology_adapter["package_sha256"],
                    "technology_adapter_registry_snapshot": technology_adapter[
                        "registry_snapshot_sha256"
                    ],
                }
            ),
            "runtime": {"process_provider_id": live["provider_id"]},
            "warnings": [],
            "blockers": ["trusted approval must be reverified before planning"],
            "refusal_codes": [],
            "created_at": created_at,
            "completed_at": None,
            "atomic_promotion": {"promoted": False},
        }
        appended = self.store.append_manifest(manifest, expected_parent_sha256=None)
        return {
            "ok": True,
            "workflow_status": "intent_draft_complete",
            "job_id": safe_job_id,
            "manifest_sha256": appended["manifest_sha256"],
            "design_intent_sha256": draft_hash,
            "process_capability_sha256": live["capability_sha256"],
            "draft_id": safe_draft_id,
            "draft_revision": draft_revision["revision"],
            "resume_token": draft_revision["resume_token"],
            "draft_persisted": True,
            "authorizes_planning": False,
            "authorizes_generation": False,
            "production_ready": False,
        }

    def teg_status(self, *, job_id: str) -> dict[str, Any]:
        """Read and revalidate one exact persisted job without granting authority."""

        current = self.store.head(job_id)
        manifest = current["manifest"]
        checked_outputs: list[dict[str, Any]] = []
        checked_documents: list[dict[str, Any]] = []
        for index, output in enumerate(manifest["outputs"]):
            reference = output["reference"]
            if reference.startswith("workflow://"):
                match = WORKFLOW_REFERENCE_PATTERN.fullmatch(reference)
                if match is None:
                    _fail(
                        "WORKFLOW_STATUS_DOCUMENT_REFERENCE_INVALID",
                        "A persisted workflow document reference is malformed.",
                        details={"index": index, "reference": reference},
                    )
                namespace = match.group("namespace")
                document_kind = WORKFLOW_REFERENCE_KINDS.get(namespace)
                reference_sha256 = match.group("sha256")
                if document_kind is None or reference_sha256 != output["content_sha256"]:
                    _fail(
                        "WORKFLOW_STATUS_DOCUMENT_REFERENCE_INVALID",
                        "Workflow document namespace or content hash does not match its output record.",
                        details={
                            "index": index,
                            "namespace": namespace,
                            "reference_sha256": reference_sha256,
                            "content_sha256": output["content_sha256"],
                        },
                    )
                self.store.get_document(document_kind, reference_sha256)
                checked_documents.append(
                    {
                        "role": output["role"],
                        "document_kind": document_kind,
                        "sha256": reference_sha256,
                    }
                )
                continue
            target = _resolved_inside(
                self.store.output_root,
                Path(reference),
                field=f"outputs[{index}].reference",
            )
            if not _native_io_path(target).is_file():
                _fail(
                    "WORKFLOW_STATUS_OUTPUT_MISSING",
                    "A persisted workflow output is missing or is not a regular file.",
                    details={
                        "job_id": manifest["job_id"],
                        "role": output["role"],
                        "path": str(target),
                    },
                )
            actual_sha256 = _file_sha256(target)
            if actual_sha256 != output["content_sha256"]:
                _fail(
                    "WORKFLOW_STATUS_OUTPUT_INTEGRITY_FAILURE",
                    "A persisted workflow output no longer matches its recorded SHA-256.",
                    details={
                        "job_id": manifest["job_id"],
                        "role": output["role"],
                        "path": str(target),
                        "expected_sha256": output["content_sha256"],
                        "actual_sha256": actual_sha256,
                    },
                )
            checked_outputs.append(
                {
                    "role": output["role"],
                    "path": str(target),
                    "sha256": actual_sha256,
                }
            )
        evidence = evaluate_evidence_ladder(manifest["evidence"])
        return {
            "ok": True,
            "job_id": manifest["job_id"],
            "manifest_sha256": current["manifest_sha256"],
            "stage": manifest["stage"],
            "highest_attained_state": evidence["highest_attained_state"],
            "next_required_state": evidence["next_required_state"],
            "production_ready": evidence["production_ready"],
            "outputs": immutable_json_copy(manifest["outputs"]),
            "warnings": list(manifest["warnings"]),
            "blockers": list(manifest["blockers"]),
            "refusal_codes": list(manifest["refusal_codes"]),
            "created_at": manifest["created_at"],
            "completed_at": manifest["completed_at"],
            "manifest_ancestry_revalidated": True,
            "output_files_rehashed": True,
            "output_file_integrity_verified": True,
            "checked_output_files": checked_outputs,
            "external_stream_files_verified": True,
            "workflow_documents_verified": True,
            "checked_workflow_documents": checked_documents,
            "approval_reverified": False,
            "authorizes_planning": False,
            "authorizes_generation": False,
        }

    def _intake_template(
        self,
        *,
        process_profile: str | None,
        process_version: str | None,
        family: str | None,
    ) -> dict[str, Any]:
        if family not in {"transistor", "resistor", "capacitor"}:
            _fail(
                "WORKFLOW_TEMPLATE_FAMILY_REQUIRED",
                "Choose one Phase 1 family for the intake template.",
                details={
                    "family": family,
                    "allowed_families": ["transistor", "resistor", "capacitor"],
                },
            )
        live = load_process_capability_by_identity(
            profile=process_profile,
            version=process_version,
            provider=self.process_provider,
        )
        capability = live["capability"]
        candidates = [
            (name, device)
            for name, device in capability["devices"].items()
            if device["family"] == family
        ]
        if not candidates:
            _fail(
                "WORKFLOW_TEMPLATE_FAMILY_UNSUPPORTED",
                "The selected process capability has no device for this family.",
                details={
                    "process_profile": process_profile,
                    "family": family,
                    "supported_families": capability["device_families"],
                },
            )
        device_name, device = candidates[0]
        terminals = list(device["terminals"])
        draft = {
            "schema_version": 1,
            "intent_id": f"draft-{family}-replace-with-stable-id",
            "units": "um",
            "process": {
                "profile": process_profile,
                "version": process_version,
                "capability_sha256": live["capability_sha256"],
            },
            "frame": {
                "width_um": 2000.0,
                "height_um": 54.0,
                "origin_um": [0.0, 0.0],
                "allowed_boundary_um": [0.0, 0.0, 2000.0, 54.0],
            },
            "pads": {
                "count": 25,
                "rows": 1,
                "outline_um": [40.0, 40.0],
                "numbering": "left_to_right",
                "reserved_roles": {},
                "pitch_um": 80.0,
            },
            "devices": [
                {
                    "dut_id": "D1",
                    "family": family,
                    "device_type": device_name,
                    "measurement_type": device["measurements"][0],
                    "parameters": {},
                    "doe": {},
                    "placement_constraints": {},
                }
            ],
            "terminal_contracts": [
                {
                    "dut_id": "D1",
                    "terminals": [
                        {"name": terminal, "electrical_role": "unconfirmed"}
                        for terminal in terminals
                    ],
                }
            ],
            "terminal_net_pad_map": [
                {
                    "dut_id": "D1",
                    "terminal": terminal,
                    "net": f"D1_{terminal}",
                    "pad": index,
                    "shared_net_explicit": False,
                }
                for index, terminal in enumerate(terminals, start=1)
            ],
            "measurement_requirements": {
                "stimuli": [],
                "observables": [],
                "biases": [],
                "timing": {
                    "settling_s": 0.0,
                    "integration": {"mode": "unconfirmed"},
                    "hold_s": 0.0,
                    "delay_s": 0.0,
                },
                "environment": {"status": "unconfirmed"},
                "safety_envelope": {
                    "limits": {},
                    "source_reference": "unconfirmed",
                    "em_current_density_evidence": None,
                },
            },
            "routing_policy": {
                "manhattan_only": True,
                "prefer_first_metal": True,
                "allowed_layer_roles": [capability["first_metal_role"]],
                "escalation_policy": "user_approval_required",
            },
            "verification_policy": {
                "internal_checks": ["fresh_reload", "projected_connectivity"],
                "external_evidence_required": [],
            },
            "output_policy": {
                "format": "gds",
                "top_cell": "TEG",
                "new_output_required": True,
            },
            "unresolved_questions": [
                "confirm_frame_and_pad_topology",
                "confirm_dut_count_and_device_parameters",
                "confirm_width_length_semantics",
                "confirm_terminal_roles_nets_and_pad_assignments",
                "confirm_measurement_stimuli_biases_observables_and_safety",
                "confirm_placement_and_routing_obstacles",
                "confirm_output_policy",
            ],
        }
        validated = validate_design_intent_draft(draft)
        return {
            "ok": True,
            "workflow_status": "template_returned_input_required",
            "template": validated["document"],
            "template_sha256": validated["canonical_sha256"],
            "template_schema_valid": True,
            "template_is_approved_intent": False,
            "template_persisted": False,
            "required_questions": list(draft["unresolved_questions"]),
            "authorizes_planning": False,
            "authorizes_generation": False,
            "production_ready": False,
        }

    def _profile_engines(
        self, *, process_profile: str
    ) -> tuple[TegPlanningEngine | None, TegGenerationEngine | None]:
        if self.engine_registry is not None:
            return self.engine_registry.resolve(process_profile=process_profile)
        return self.planning_engine, self.generation_engine

    def reverify_privileged_action(
        self,
        *,
        job_id: str,
        approval_reference: Mapping[str, Any],
        required_scope: str,
        output_class: str,
    ) -> dict[str, Any]:
        """Re-read canonical source docs and re-run trust checks for every action."""

        head = self.store.head(job_id)
        manifest = head["manifest"]
        draft = self.store.get_document(
            "design_intent", manifest["design_intent_sha256"]
        )
        live = load_live_process_capability(
            design_intent_draft=draft,
            provider=self.process_provider,
        )
        technology_adapter = self._resolve_technology_adapter(draft)
        if technology_adapter is not None:
            stored_adapter = self.store.get_document(
                "technology_adapter_package",
                technology_adapter["package_sha256"],
            )
            if canonical_sha256(stored_adapter) != technology_adapter["package_sha256"]:
                _fail(
                    "TECH_ADAPTER_PERSISTED_PACKAGE_DRIFT",
                    "The persisted job adapter package differs from the active exact registry entry.",
                    details={
                        "job_id": job_id,
                        "package_sha256": technology_adapter["package_sha256"],
                        "stage": "adapter_resolution",
                    },
                )
        if live["capability_sha256"] != manifest["process_capability_sha256"]:
            _fail(
                "WORKFLOW_JOB_PROCESS_DRIFT",
                "The persisted job and active process capability no longer match.",
                details={"job_id": job_id},
            )
        verifier = require_host_approval_verifier(
            self.approval_verifier,
            production_mode=self.production_mode,
        )
        approval = verify_design_intent_approval(
            design_intent_draft=draft,
            approval_reference=approval_reference,
            required_scope=required_scope,
            output_class=output_class,
            verifier=verifier,
            clock=self.clock,
        )
        validated_reference = validate_approved_design_intent_reference(
            approval_reference
        )
        reference_hash = validated_reference["canonical_sha256"]
        if reference_hash != approval["approved_intent_reference_sha256"]:
            _fail(
                "WORKFLOW_APPROVAL_REFERENCE_DRIFT",
                "The stored approval reference differs from the verified reference.",
                details={"job_id": job_id},
            )
        return {
            "ok": True,
            "job_id": job_id,
            "head_manifest_sha256": head["manifest_sha256"],
            "design_intent": draft,
            "live_process_capability": live["capability"],
            "technology_adapter": technology_adapter,
            "approval_reference_sha256": reference_hash,
            "approval_verification_receipt_sha256": approval[
                "verification_receipt_sha256"
            ],
            "verified_at": approval["verified_at"],
            "required_scope": required_scope,
            "output_class": output_class,
            "authorization_decision_is_persisted": False,
            "production_ready": False,
        }

    @staticmethod
    def _require_engine(engine: Any, protocol: type, *, stage: str) -> Any:
        if engine is None or not isinstance(engine, protocol):
            _fail(
                "WORKFLOW_ENGINE_UNAVAILABLE",
                "The host has no compatible engine configured for this workflow stage.",
                details={"stage": stage, "engine_type": type(engine).__name__},
            )
        engine_id = getattr(engine, "engine_id", None)
        if not isinstance(engine_id, str) or not engine_id.strip():
            _fail(
                "INVALID_WORKFLOW_ENGINE",
                "The configured workflow engine lacks a stable host identity.",
                details={"stage": stage, "engine_type": type(engine).__name__},
            )
        return engine

    def _append_stage(
        self,
        *,
        parent: Mapping[str, Any],
        parent_sha256: str,
        stage: str,
        approved_intent_sha256: str,
        evidence: Mapping[str, Any],
        normalized_inputs: Mapping[str, Any],
        outputs: list[Mapping[str, Any]],
        fingerprints: Mapping[str, str],
        runtime: Mapping[str, Any],
        blockers: list[str] | None = None,
        completed: bool = False,
    ) -> dict[str, Any]:
        now = _utc_now(self.clock).isoformat()
        document = {
            "schema_version": 1,
            "job_id": parent["job_id"],
            "parent_manifest_sha256": parent_sha256,
            "design_intent_sha256": parent["design_intent_sha256"],
            "approved_intent_sha256": approved_intent_sha256,
            "process_capability_sha256": parent["process_capability_sha256"],
            "stage": stage,
            "evidence": immutable_json_copy(evidence),
            "normalized_inputs": immutable_json_copy(normalized_inputs),
            "outputs": immutable_json_copy(outputs),
            "fingerprints": immutable_json_copy(fingerprints),
            "runtime": immutable_json_copy(runtime),
            "warnings": [],
            "blockers": list(blockers or []),
            "refusal_codes": [],
            "created_at": now,
            "completed_at": now if completed else None,
            "atomic_promotion": {"promoted": completed},
        }
        return self.store.append_manifest(
            document,
            expected_parent_sha256=parent_sha256,
        )

    @staticmethod
    def _approval_scope(reference: Mapping[str, Any], *, generation: bool) -> str:
        validated = validate_approved_design_intent_reference(reference)["document"]
        scope = validated["approval_scope"]
        if generation and scope != "planning_and_generation":
            _fail(
                "APPROVAL_SCOPE_DOES_NOT_ALLOW_GENERATION",
                "This approval permits planning only, not layout generation.",
                details={"approval_scope": scope},
            )
        return scope

    def teg_plan(
        self,
        *,
        job_id: str,
        approval_reference: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Reverify approval, create a deterministic plan, and persist two stages."""

        scope = self._approval_scope(approval_reference, generation=False)
        context = self.reverify_privileged_action(
            job_id=job_id,
            approval_reference=approval_reference,
            required_scope=scope,
            output_class=self.output_class,
        )
        current = self.store.head(job_id)
        manifest = current["manifest"]
        if manifest["stage"] == "plan_complete":
            plan_output = next(
                (item for item in manifest["outputs"] if item["role"] == "plan"),
                None,
            )
            if plan_output is None:
                _fail(
                    "WORKFLOW_PLAN_OUTPUT_MISSING",
                    "A plan-complete manifest has no content-addressed plan output.",
                    details={"job_id": job_id},
                )
            plan = self.store.get_document("plan", plan_output["content_sha256"])
            return {
                "ok": True,
                "workflow_status": "plan_complete",
                "job_id": job_id,
                "manifest_sha256": current["manifest_sha256"],
                "plan_sha256": plan_output["content_sha256"],
                "plan": plan,
                "approval_reverified": True,
                "resumed": True,
                "production_ready": False,
            }
        if manifest["stage"] not in {"intent_draft_complete", "intent_approved"}:
            _fail(
                "WORKFLOW_STAGE_NOT_PLANNABLE",
                "The current job stage cannot be planned.",
                details={"job_id": job_id, "stage": manifest["stage"]},
            )

        evidence = dict(manifest["evidence"])
        evidence.update(
            {
                "approval_backend_trusted": True,
                "approval_verified": True,
            }
        )
        if manifest["stage"] == "intent_draft_complete":
            approval_stage = self._append_stage(
                parent=manifest,
                parent_sha256=current["manifest_sha256"],
                stage="intent_approved",
                approved_intent_sha256=context["approval_reference_sha256"],
                evidence=evidence,
                normalized_inputs={
                    "design_intent_sha256": manifest["design_intent_sha256"],
                    "process_capability_sha256": manifest[
                        "process_capability_sha256"
                    ],
                    "approval_reference_sha256": context[
                        "approval_reference_sha256"
                    ],
                },
                outputs=[],
                fingerprints={
                    "approval_verification_receipt": context[
                        "approval_verification_receipt_sha256"
                    ]
                },
                runtime={"approval_verified_at": context["verified_at"]},
                blockers=["plan has not been created"],
            )
            current = {
                "manifest_sha256": approval_stage["manifest_sha256"],
                "manifest": approval_stage["manifest"],
            }
            manifest = current["manifest"]
        elif manifest["approved_intent_sha256"] != context["approval_reference_sha256"]:
            _fail(
                "WORKFLOW_APPROVAL_IDENTITY_DRIFT",
                "The job approval reference changed after the approval stage.",
                details={"job_id": job_id},
            )

        planning_engine, _generation_engine = self._profile_engines(
            process_profile=context["design_intent"]["process"]["profile"]
        )
        engine = self._require_engine(planning_engine, TegPlanningEngine, stage="plan")
        try:
            raw_result = engine.plan(
                design_intent=context["design_intent"],
                process_capability=context["live_process_capability"],
            )
        except AnalysisError:
            raise
        except Exception as exc:
            _fail(
                "WORKFLOW_PLANNING_ENGINE_FAILED",
                "The configured planning engine failed closed.",
                details={"engine_id": engine.engine_id, "error_type": type(exc).__name__},
            )
        if not isinstance(raw_result, Mapping) or raw_result.get("ok") is not True:
            _fail(
                "WORKFLOW_PLAN_NOT_COMPLETE",
                "The planning engine did not return a complete deterministic plan.",
                details={"engine_id": engine.engine_id},
            )
        plan = raw_result.get("plan")
        if not isinstance(plan, Mapping):
            _fail(
                "INVALID_WORKFLOW_PLAN",
                "The planning engine returned no plan document.",
                details={"engine_id": engine.engine_id},
            )
        plan_snapshot = immutable_json_copy(plan)
        plan_sha256 = canonical_sha256(plan_snapshot)
        if raw_result.get("plan_sha256") not in (None, plan_sha256):
            _fail(
                "WORKFLOW_PLAN_HASH_MISMATCH",
                "The planning engine plan hash differs from the canonical plan.",
                details={
                    "engine_id": engine.engine_id,
                    "expected": plan_sha256,
                    "actual": raw_result.get("plan_sha256"),
                },
            )
        route_fingerprint = raw_result.get("routing_plan_fingerprint_sha256")
        if not isinstance(route_fingerprint, str) or not SHA256_PATTERN.fullmatch(
            route_fingerprint
        ):
            _fail(
                "WORKFLOW_ROUTE_FINGERPRINT_REQUIRED",
                "A complete plan requires a deterministic routing fingerprint.",
                details={"engine_id": engine.engine_id},
            )
        stored_plan_hash = self.store.put_document("plan", plan_snapshot)
        plan_stage = self._append_stage(
            parent=manifest,
            parent_sha256=current["manifest_sha256"],
            stage="plan_complete",
            approved_intent_sha256=context["approval_reference_sha256"],
            evidence={
                **evidence,
                "plan_fingerprint_verified": True,
                "routing_plan_complete": True,
            },
            normalized_inputs={
                "design_intent_sha256": manifest["design_intent_sha256"],
                "process_capability_sha256": manifest["process_capability_sha256"],
                "approval_reference_sha256": context["approval_reference_sha256"],
            },
            outputs=[
                {
                    "role": "plan",
                    "content_sha256": stored_plan_hash,
                    "reference": f"workflow://plan/{stored_plan_hash}",
                }
            ],
            fingerprints={
                "plan": plan_sha256,
                "routing_plan": route_fingerprint,
            },
            runtime={"planning_engine_id": engine.engine_id},
            blockers=["layout has not been generated"],
        )
        return {
            "ok": True,
            "workflow_status": "plan_complete",
            "job_id": job_id,
            "manifest_sha256": plan_stage["manifest_sha256"],
            "plan_sha256": stored_plan_hash,
            "routing_plan_fingerprint_sha256": route_fingerprint,
            "plan": plan_snapshot,
            "approval_reverified": True,
            "resumed": False,
            "production_ready": False,
        }

    def teg_generate(
        self,
        *,
        job_id: str,
        approval_reference: Mapping[str, Any],
        output_name: str,
    ) -> dict[str, Any]:
        """Reverify scope and emit through create-only staged promotion."""

        scope = self._approval_scope(approval_reference, generation=True)
        context = self.reverify_privileged_action(
            job_id=job_id,
            approval_reference=approval_reference,
            required_scope=scope,
            output_class=self.output_class,
        )
        current = self.store.head(job_id)
        manifest = current["manifest"]
        if manifest["stage"] == "generation_staged":
            if manifest["approved_intent_sha256"] != context["approval_reference_sha256"]:
                _fail(
                    "WORKFLOW_APPROVAL_IDENTITY_DRIFT",
                    "The generation approval differs from the persisted staging approval.",
                    details={"job_id": job_id},
                )
            staged_record = next(
                (item for item in manifest["outputs"] if item["role"] == "staged_layout"),
                None,
            )
            result_record = next(
                (item for item in manifest["outputs"] if item["role"] == "generation_result"),
                None,
            )
            if staged_record is None or result_record is None:
                _fail(
                    "WORKFLOW_STAGED_RESUME_EVIDENCE_MISSING",
                    "The persisted generation stage lacks layout or result evidence.",
                    details={"job_id": job_id},
                )
            staged_path = _resolved_inside(
                self.store.output_root,
                Path(staged_record["reference"]),
                field="staged_layout_reference",
            )
            target_value = manifest["runtime"].get("final_output_path")
            if not isinstance(target_value, str):
                _fail(
                    "WORKFLOW_STAGED_RESUME_EVIDENCE_MISSING",
                    "The generation stage lacks its exact final output path.",
                    details={"job_id": job_id},
                )
            target = _resolved_inside(
                self.store.output_root,
                Path(target_value),
                field="final_output_path",
            )
            if target.name != output_name:
                _fail(
                    "WORKFLOW_DRAWING_RESUME_OUTPUT_NAME_MISMATCH",
                    "Resume must use the exact persisted output filename.",
                    details={"expected": target.name, "actual": output_name},
                )
            if _native_io_path(staged_path).is_file():
                staged_actual = _file_sha256(staged_path)
                if staged_actual != staged_record["content_sha256"]:
                    _fail(
                        "WORKFLOW_STAGED_OUTPUT_INTEGRITY_FAILURE",
                        "The staged layout changed before generation resume.",
                        details={
                            "expected_sha256": staged_record["content_sha256"],
                            "actual_sha256": staged_actual,
                        },
                    )
            elif not _native_io_path(target).is_file():
                _fail(
                    "WORKFLOW_STAGED_OUTPUT_MISSING",
                    "Neither the staged layout nor its promoted final file exists.",
                    details={"staged_path": str(staged_path), "final_path": str(target)},
                )
            result_document = self.store.get_document(
                "generation_result", result_record["content_sha256"]
            )
            drawing_fingerprint = result_document.get("drawing_fingerprint_sha256")
            if (
                any(
                    result_document.get(field) is not True
                    for field in (
                        "fresh_reload_verified",
                        "drawing_fingerprint_verified",
                        "connectivity_projection_verified",
                    )
                )
                or drawing_fingerprint != manifest["fingerprints"].get("drawing")
            ):
                _fail(
                    "WORKFLOW_STAGED_RESUME_EVIDENCE_INVALID",
                    "The persisted generation result cannot support safe promotion.",
                    details={"generation_result_sha256": result_record["content_sha256"]},
                )
            self.store.promote_staged_output(
                staged_path=staged_path,
                final_target=target,
                expected_sha256=staged_record["content_sha256"],
            )
            file_sha256 = _file_sha256(target)
            output_record = {
                "role": "generated_layout",
                "content_sha256": file_sha256,
                "reference": str(target),
            }
            drawing_stage = self._append_stage(
                parent=manifest,
                parent_sha256=current["manifest_sha256"],
                stage="drawing_complete",
                approved_intent_sha256=context["approval_reference_sha256"],
                evidence={
                    **manifest["evidence"],
                    "fresh_reload_verified": True,
                    "drawing_fingerprint_verified": True,
                },
                normalized_inputs=manifest["normalized_inputs"],
                outputs=[output_record, result_record],
                fingerprints={
                    **manifest["fingerprints"],
                    "generated_layout_file": file_sha256,
                },
                runtime={
                    **manifest["runtime"],
                    "resumed_from_generation_staged": True,
                    "atomic_final_promotion_verified": True,
                },
                blockers=["measurement package has not been verified"],
            )
            _native_io_path(staged_path).unlink(missing_ok=True)
            connectivity_stage = self._append_stage(
                parent=drawing_stage["manifest"],
                parent_sha256=drawing_stage["manifest_sha256"],
                stage="connectivity_projected",
                approved_intent_sha256=context["approval_reference_sha256"],
                evidence={
                    **drawing_stage["manifest"]["evidence"],
                    "connectivity_projection_verified": True,
                },
                normalized_inputs=drawing_stage["manifest"]["normalized_inputs"],
                outputs=drawing_stage["manifest"]["outputs"],
                fingerprints=drawing_stage["manifest"]["fingerprints"],
                runtime={
                    **drawing_stage["manifest"]["runtime"],
                    "connectivity_projection": "internal_not_lvs",
                },
                blockers=["measurement package has not been verified"],
                completed=True,
            )
            return {
                "ok": True,
                "workflow_status": "connectivity_projected",
                "job_id": job_id,
                "manifest_sha256": connectivity_stage["manifest_sha256"],
                "output_path": str(target),
                "generated_layout_sha256": file_sha256,
                "generation_result_sha256": result_record["content_sha256"],
                "fresh_reload_verified": True,
                "connectivity_projection_verified": True,
                "connectivity_projection_is_lvs": False,
                "approval_reverified": True,
                "resumed": True,
                "production_ready": False,
            }
        if manifest["stage"] in {"drawing_complete", "connectivity_projected"}:
            if manifest["approved_intent_sha256"] != context["approval_reference_sha256"]:
                _fail(
                    "WORKFLOW_APPROVAL_IDENTITY_DRIFT",
                    "The generation approval differs from the persisted drawing approval.",
                    details={"job_id": job_id},
                )
            output_record = next(
                (item for item in manifest["outputs"] if item["role"] == "generated_layout"),
                None,
            )
            result_record = next(
                (item for item in manifest["outputs"] if item["role"] == "generation_result"),
                None,
            )
            if output_record is None or result_record is None:
                _fail(
                    "WORKFLOW_DRAWING_RESUME_EVIDENCE_MISSING",
                    "The persisted drawing lacks its layout or generation-result evidence.",
                    details={"job_id": job_id, "stage": manifest["stage"]},
                )
            target = _resolved_inside(
                self.store.output_root,
                Path(output_record["reference"]),
                field="generated_layout_reference",
            )
            if target.name != output_name:
                _fail(
                    "WORKFLOW_DRAWING_RESUME_OUTPUT_NAME_MISMATCH",
                    "Resume must use the exact persisted output filename.",
                    details={"expected": target.name, "actual": output_name},
                )
            if not _native_io_path(target).is_file():
                _fail(
                    "WORKFLOW_DRAWING_RESUME_OUTPUT_MISSING",
                    "The persisted drawing output is missing.",
                    details={"path": str(target)},
                )
            file_sha256 = _file_sha256(target)
            if file_sha256 != output_record["content_sha256"]:
                _fail(
                    "WORKFLOW_DRAWING_RESUME_OUTPUT_INTEGRITY_FAILURE",
                    "The persisted drawing output changed before resume.",
                    details={
                        "expected_sha256": output_record["content_sha256"],
                        "actual_sha256": file_sha256,
                    },
                )
            stale_stage_value = manifest["runtime"].get("staged_output_path")
            if isinstance(stale_stage_value, str):
                stale_stage = _resolved_inside(
                    self.store.output_root,
                    Path(stale_stage_value),
                    field="staged_output_path",
                )
                if (
                    _native_io_path(stale_stage).is_file()
                    and stale_stage.name.startswith(".stage-")
                    and _file_sha256(stale_stage) == file_sha256
                ):
                    _native_io_path(stale_stage).unlink(missing_ok=True)
            expected_result_reference = (
                f"workflow://generation_result/{result_record['content_sha256']}"
            )
            if result_record["reference"] != expected_result_reference:
                _fail(
                    "WORKFLOW_DRAWING_RESUME_EVIDENCE_MISSING",
                    "The generation-result reference is malformed.",
                    details={"reference": result_record["reference"]},
                )
            result_document = self.store.get_document(
                "generation_result", result_record["content_sha256"]
            )
            drawing_fingerprint = result_document.get("drawing_fingerprint_sha256")
            if (
                any(
                    result_document.get(field) is not True
                    for field in (
                        "fresh_reload_verified",
                        "drawing_fingerprint_verified",
                        "connectivity_projection_verified",
                    )
                )
                or drawing_fingerprint != manifest["fingerprints"].get("drawing")
            ):
                _fail(
                    "WORKFLOW_DRAWING_RESUME_EVIDENCE_INVALID",
                    "The persisted generation result cannot support safe resume.",
                    details={"generation_result_sha256": result_record["content_sha256"]},
                )
            if manifest["stage"] == "drawing_complete":
                connectivity_stage = self._append_stage(
                    parent=manifest,
                    parent_sha256=current["manifest_sha256"],
                    stage="connectivity_projected",
                    approved_intent_sha256=context["approval_reference_sha256"],
                    evidence={
                        **manifest["evidence"],
                        "connectivity_projection_verified": True,
                    },
                    normalized_inputs=manifest["normalized_inputs"],
                    outputs=manifest["outputs"],
                    fingerprints=manifest["fingerprints"],
                    runtime={
                        **manifest["runtime"],
                        "connectivity_projection": "internal_not_lvs",
                        "resumed_from_drawing_complete": True,
                    },
                    blockers=["measurement package has not been verified"],
                    completed=True,
                )
                final_manifest_sha256 = connectivity_stage["manifest_sha256"]
            else:
                final_manifest_sha256 = current["manifest_sha256"]
            return {
                "ok": True,
                "workflow_status": "connectivity_projected",
                "job_id": job_id,
                "manifest_sha256": final_manifest_sha256,
                "output_path": str(target),
                "generated_layout_sha256": file_sha256,
                "generation_result_sha256": result_record["content_sha256"],
                "fresh_reload_verified": True,
                "connectivity_projection_verified": True,
                "connectivity_projection_is_lvs": False,
                "approval_reverified": True,
                "resumed": True,
                "production_ready": False,
            }
        if manifest["stage"] != "plan_complete":
            _fail(
                "WORKFLOW_STAGE_NOT_GENERATABLE",
                "Generation requires the exact persisted plan-complete stage.",
                details={"job_id": job_id, "stage": manifest["stage"]},
            )
        if manifest["approved_intent_sha256"] != context["approval_reference_sha256"]:
            _fail(
                "WORKFLOW_APPROVAL_IDENTITY_DRIFT",
                "The generation approval differs from the plan approval.",
                details={"job_id": job_id},
            )
        plan_output = next(
            (item for item in manifest["outputs"] if item["role"] == "plan"), None
        )
        if plan_output is None:
            _fail(
                "WORKFLOW_PLAN_OUTPUT_MISSING",
                "The plan-complete stage has no persisted plan.",
                details={"job_id": job_id},
            )
        plan = self.store.get_document("plan", plan_output["content_sha256"])
        output_format = context["design_intent"]["output_policy"]["format"]
        target = self.store.prepare_output_path(
            job_id=job_id,
            output_name=output_name,
            output_format=output_format,
        )
        staged_path = self.store.prepare_staging_path(final_target=target)
        _planning_engine, generation_engine = self._profile_engines(
            process_profile=context["design_intent"]["process"]["profile"]
        )
        engine = self._require_engine(
            generation_engine, TegGenerationEngine, stage="generate"
        )
        try:
            raw_result = engine.generate(
                design_intent=context["design_intent"],
                process_capability=context["live_process_capability"],
                plan=plan,
                output_path=str(staged_path),
            )
        except AnalysisError:
            raise
        except Exception as exc:
            _fail(
                "WORKFLOW_GENERATION_ENGINE_FAILED",
                "The configured generation engine failed closed.",
                details={"engine_id": engine.engine_id, "error_type": type(exc).__name__},
            )
        if not isinstance(raw_result, Mapping) or raw_result.get("ok") is not True:
            _fail(
                "WORKFLOW_GENERATION_NOT_VERIFIED",
                "The generation engine did not return a verified result.",
                details={"engine_id": engine.engine_id},
            )
        if not _native_io_path(staged_path).is_file():
            _fail(
                "WORKFLOW_GENERATED_FILE_MISSING",
                "The generation engine reported success without its staged output file.",
                details={"engine_id": engine.engine_id, "path": str(staged_path)},
            )
        file_sha256 = _file_sha256(staged_path)
        required_flags = (
            "fresh_reload_verified",
            "drawing_fingerprint_verified",
            "connectivity_projection_verified",
        )
        missing_flags = [field for field in required_flags if raw_result.get(field) is not True]
        drawing_fingerprint = raw_result.get("drawing_fingerprint_sha256")
        if (
            missing_flags
            or not isinstance(drawing_fingerprint, str)
            or not SHA256_PATTERN.fullmatch(drawing_fingerprint)
        ):
            _fail(
                "WORKFLOW_GENERATION_NOT_VERIFIED",
                "Fresh reload, drawing fingerprint, and connectivity projection are mandatory.",
                details={
                    "engine_id": engine.engine_id,
                    "missing_true_flags": missing_flags,
                    "drawing_fingerprint_valid": isinstance(drawing_fingerprint, str)
                    and bool(SHA256_PATTERN.fullmatch(drawing_fingerprint)),
                    "unverified_file_preserved": str(staged_path),
                },
            )
        staged_record = {
            "role": "staged_layout",
            "content_sha256": file_sha256,
            "reference": str(staged_path),
        }
        result_document = immutable_json_copy(dict(raw_result))
        result_sha256 = self.store.put_document("generation_result", result_document)
        result_record = {
            "role": "generation_result",
            "content_sha256": result_sha256,
            "reference": f"workflow://generation_result/{result_sha256}",
        }
        try:
            staged_stage = self._append_stage(
                parent=manifest,
                parent_sha256=current["manifest_sha256"],
                stage="generation_staged",
                approved_intent_sha256=context["approval_reference_sha256"],
                evidence={
                    **manifest["evidence"],
                    "staged_layout_hash_verified": True,
                    "generation_result_persisted": True,
                },
                normalized_inputs={
                    **manifest["normalized_inputs"],
                    "plan_sha256": plan_output["content_sha256"],
                },
                outputs=[staged_record, result_record],
                fingerprints={
                    **manifest["fingerprints"],
                    "drawing": drawing_fingerprint,
                    "staged_layout_file": file_sha256,
                },
                runtime={
                    "generation_engine_id": engine.engine_id,
                    "generation_result_sha256": result_sha256,
                    "staged_output_path": str(staged_path),
                    "final_output_path": str(target),
                    "final_output_name": output_name,
                    "output_format": output_format,
                },
                blockers=["staged layout has not been promoted to its final output name"],
            )
        except AnalysisError as exc:
            if exc.code == "WORKFLOW_JOB_HEAD_CONFLICT":
                _native_io_path(staged_path).unlink(missing_ok=True)
            raise
        self.store.promote_staged_output(
            staged_path=staged_path,
            final_target=target,
            expected_sha256=file_sha256,
        )
        output_record = {
            "role": "generated_layout",
            "content_sha256": file_sha256,
            "reference": str(target),
        }
        drawing_stage = self._append_stage(
            parent=staged_stage["manifest"],
            parent_sha256=staged_stage["manifest_sha256"],
            stage="drawing_complete",
            approved_intent_sha256=context["approval_reference_sha256"],
            evidence={
                **staged_stage["manifest"]["evidence"],
                "fresh_reload_verified": True,
                "drawing_fingerprint_verified": True,
            },
            normalized_inputs=staged_stage["manifest"]["normalized_inputs"],
            outputs=[output_record, result_record],
            fingerprints={
                **staged_stage["manifest"]["fingerprints"],
                "generated_layout_file": file_sha256,
            },
            runtime={
                **staged_stage["manifest"]["runtime"],
                "atomic_final_promotion_verified": True,
            },
            blockers=["measurement package has not been verified"],
        )
        _native_io_path(staged_path).unlink(missing_ok=True)
        connectivity_stage = self._append_stage(
            parent=drawing_stage["manifest"],
            parent_sha256=drawing_stage["manifest_sha256"],
            stage="connectivity_projected",
            approved_intent_sha256=context["approval_reference_sha256"],
            evidence={
                **drawing_stage["manifest"]["evidence"],
                "connectivity_projection_verified": True,
            },
            normalized_inputs=drawing_stage["manifest"]["normalized_inputs"],
            outputs=[output_record, result_record],
            fingerprints=drawing_stage["manifest"]["fingerprints"],
            runtime={
                "generation_engine_id": engine.engine_id,
                "connectivity_projection": "internal_not_lvs",
            },
            blockers=["measurement package has not been verified"],
            completed=True,
        )
        return {
            "ok": True,
            "workflow_status": "connectivity_projected",
            "job_id": job_id,
            "manifest_sha256": connectivity_stage["manifest_sha256"],
            "output_path": str(target),
            "generated_layout_sha256": file_sha256,
            "generation_result_sha256": result_sha256,
            "fresh_reload_verified": True,
            "connectivity_projection_verified": True,
            "connectivity_projection_is_lvs": False,
            "approval_reverified": True,
            "resumed": False,
            "production_ready": False,
        }

    def teg_verify(
        self,
        *,
        job_id: str,
        approval_reference: Mapping[str, Any],
        measurement_manifest: Mapping[str, Any] | None = None,
        external_reports: list[Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Freshly rehash final output and optionally bind exact measurement meaning."""

        scope = self._approval_scope(approval_reference, generation=False)
        privileged_context = self.reverify_privileged_action(
            job_id=job_id,
            approval_reference=approval_reference,
            required_scope=scope,
            output_class=self.output_class,
        )
        current = self.store.head(job_id)
        manifest = current["manifest"]
        output = next(
            (item for item in manifest["outputs"] if item["role"] == "generated_layout"),
            None,
        )
        if output is None:
            _fail(
                "WORKFLOW_FINAL_OUTPUT_MISSING",
                "The current job has no generated final layout to verify.",
                details={"job_id": job_id, "stage": manifest["stage"]},
            )
        target = _resolved_inside(
            self.store.output_root,
            Path(output["reference"]),
            field="generated_layout_reference",
        )
        if not _native_io_path(target).is_file():
            _fail(
                "WORKFLOW_FINAL_OUTPUT_MISSING",
                "The persisted final layout is missing.",
                details={"job_id": job_id, "path": str(target)},
            )
        actual = _file_sha256(target)
        if actual != output["content_sha256"]:
            _fail(
                "WORKFLOW_FINAL_OUTPUT_INTEGRITY_FAILURE",
                "The final layout changed after staged generation promotion.",
                details={
                    "job_id": job_id,
                    "expected": output["content_sha256"],
                    "actual": actual,
                },
            )
        base_result = {
            "ok": True,
            "workflow_status": manifest["stage"],
            "job_id": job_id,
            "manifest_sha256": current["manifest_sha256"],
            "generated_layout_sha256": actual,
            "final_output_integrity_verified": True,
            "measurement_manifest_verified": False,
            "connectivity_projection_is_lvs": False,
            "approval_reverified": True,
            "production_ready": False,
            "next_gate": "bind a MeasurementManifest to this freshly hashed layout",
        }
        if measurement_manifest is not None and external_reports:
            _fail(
                "WORKFLOW_EVIDENCE_STAGE_AMBIGUOUS",
                "Bind the measurement manifest and external reports in separate teg_verify calls.",
                details={"job_id": job_id},
            )
        if measurement_manifest is None and not external_reports:
            return base_result
        if external_reports:
            return self._attach_external_reports(
                job_id=job_id,
                current=current,
                manifest=manifest,
                draft=self.store.get_document(
                    "design_intent", manifest["design_intent_sha256"]
                ),
                generated_layout_path=target,
                external_reports=external_reports,
                base_result=base_result,
            )
        if manifest["stage"] not in {
            "connectivity_projected",
            "measurement_package_complete",
        }:
            _fail(
                "WORKFLOW_STAGE_NOT_MEASUREMENT_BINDABLE",
                "Measurement binding requires a connectivity-projected final layout.",
                details={"job_id": job_id, "stage": manifest["stage"]},
            )
        draft = self.store.get_document(
            "design_intent", manifest["design_intent_sha256"]
        )
        validated = validate_measurement_manifest(
            measurement_manifest,
            design_intent=draft,
        )
        measurement = validated["document"]
        if measurement["generated_layout_sha256"] != actual:
            _fail(
                "MEASUREMENT_LAYOUT_HASH_MISMATCH",
                "The MeasurementManifest is stale or references another layout file.",
                details={
                    "job_id": job_id,
                    "expected": actual,
                    "actual": measurement["generated_layout_sha256"],
                },
            )
        measurement_sha256 = canonical_sha256(measurement)
        if manifest["stage"] == "measurement_package_complete":
            persisted = next(
                (
                    item
                    for item in manifest["outputs"]
                    if item["role"] == "measurement_manifest"
                ),
                None,
            )
            if persisted is None or persisted["content_sha256"] != measurement_sha256:
                _fail(
                    "WORKFLOW_MEASUREMENT_IDENTITY_DRIFT",
                    "A completed job cannot silently replace its measurement manifest.",
                    details={
                        "job_id": job_id,
                        "persisted_sha256": (
                            None if persisted is None else persisted["content_sha256"]
                        ),
                        "received_sha256": measurement_sha256,
                    },
                )
            return {
                **base_result,
                "workflow_status": "measurement_package_complete",
                "measurement_manifest_sha256": measurement_sha256,
                "measurement_manifest_verified": True,
                "measurement_layout_hash_match": True,
                "resumed": True,
                "next_gate": "attach provenance-matched external verification evidence",
            }
        stored_measurement_hash = self.store.put_document(
            "measurement_manifest", measurement
        )
        evidence = {
            **manifest["evidence"],
            "measurement_manifest_verified": True,
            "measurement_layout_hash_match": True,
        }
        outputs = [
            *manifest["outputs"],
            {
                "role": "measurement_manifest",
                "content_sha256": stored_measurement_hash,
                "reference": f"workflow://measurement/{stored_measurement_hash}",
            },
        ]
        measurement_stage = self._append_stage(
            parent=manifest,
            parent_sha256=current["manifest_sha256"],
            stage="measurement_package_complete",
            approved_intent_sha256=manifest["approved_intent_sha256"],
            evidence=evidence,
            normalized_inputs={
                **manifest["normalized_inputs"],
                "generated_layout_sha256": actual,
                "measurement_manifest_sha256": stored_measurement_hash,
            },
            outputs=outputs,
            fingerprints={
                **manifest["fingerprints"],
                "measurement_manifest": stored_measurement_hash,
            },
            runtime={"layout_hash_recomputed_during_measurement_binding": True},
            blockers=["external DRC/LVS/PEX evidence has not been attached"],
            completed=True,
        )
        return {
            **base_result,
            "workflow_status": "measurement_package_complete",
            "manifest_sha256": measurement_stage["manifest_sha256"],
            "measurement_manifest_sha256": stored_measurement_hash,
            "measurement_manifest_verified": True,
            "measurement_layout_hash_match": True,
            "resumed": False,
            "next_gate": "attach provenance-matched external verification evidence",
        }

    def _attach_external_reports(
        self,
        *,
        job_id: str,
        current: Mapping[str, Any],
        manifest: Mapping[str, Any],
        draft: Mapping[str, Any],
        generated_layout_path: Path,
        external_reports: list[Mapping[str, Any]],
        base_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        if manifest["stage"] not in {
            "measurement_package_complete",
            "external_evidence_attached",
            "signoff_evidence_approved",
        }:
            _fail(
                "WORKFLOW_STAGE_NOT_EXTERNAL_EVIDENCE_BINDABLE",
                "External evidence requires a completed measurement package.",
                details={"job_id": job_id, "stage": manifest["stage"]},
            )
        if self.external_evidence_registry is None or self.external_report_root is None:
            _fail(
                "EXTERNAL_EVIDENCE_HOST_NOT_CONFIGURED",
                "This host has no trusted external evidence registry and report root.",
                details={"job_id": job_id},
            )
        normalized_requests: list[dict[str, str]] = []
        seen_kinds: set[str] = set()
        for index, raw in enumerate(external_reports):
            if not isinstance(raw, Mapping) or set(raw) != {
                "adapter_id",
                "report_name",
                "kind",
            }:
                _fail(
                    "INVALID_EXTERNAL_REPORT_REQUEST",
                    "Each external report request needs exactly adapter_id, report_name, and kind.",
                    details={"index": index},
                )
            if raw["kind"] in seen_kinds:
                _fail(
                    "DUPLICATE_EXTERNAL_EVIDENCE_KIND",
                    "Provide one external report per evidence kind.",
                    details={"kind": raw["kind"]},
                )
            seen_kinds.add(raw["kind"])
            normalized_requests.append(
                {field: str(raw[field]) for field in ("adapter_id", "report_name", "kind")}
            )
        required = set(draft["verification_policy"]["external_evidence_required"])
        if seen_kinds != required:
            _fail(
                "EXTERNAL_EVIDENCE_SET_MISMATCH",
                "External reports must exactly cover the design intent evidence policy.",
                details={
                    "required": sorted(required),
                    "provided": sorted(seen_kinds),
                },
            )
        verified = [
            verify_external_report(
                adapter_registry=self.external_evidence_registry,
                adapter_id=request["adapter_id"],
                report_root=self.external_report_root,
                report_name=request["report_name"],
                generated_layout_path=generated_layout_path,
                expected_kind=request["kind"],
            )
            for request in sorted(normalized_requests, key=lambda item: item["kind"])
        ]
        if not verified or not all(
            item["external_evidence_provenance_verified"] is True for item in verified
        ):
            _fail(
                "EXTERNAL_EVIDENCE_PROVENANCE_UNVERIFIED",
                "Mock, untrusted, or incomplete evidence cannot advance the job state.",
                details={"job_id": job_id},
            )
        evidence_hashes = {
            item["evidence"]["kind"]: self.store.put_document(
                "external_evidence", item["evidence"]
            )
            for item in verified
        }
        if manifest["stage"] in {
            "external_evidence_attached",
            "signoff_evidence_approved",
        }:
            persisted = {
                item["role"].removeprefix("external_evidence_"): item["content_sha256"]
                for item in manifest["outputs"]
                if item["role"].startswith("external_evidence_")
            }
            if persisted != evidence_hashes:
                _fail(
                    "WORKFLOW_EXTERNAL_EVIDENCE_IDENTITY_DRIFT",
                    "Attached external evidence cannot be silently replaced.",
                    details={"persisted": persisted, "received": evidence_hashes},
                )
            if self.signoff_policy is not None:
                return self._apply_signoff_policy(
                    job_id=job_id,
                    current=current,
                    manifest=manifest,
                    verified=verified,
                    evidence_hashes=evidence_hashes,
                    base_result=base_result,
                )
            return {
                **base_result,
                "workflow_status": "external_evidence_attached",
                "external_evidence_sha256s": evidence_hashes,
                "external_evidence_provenance_verified": True,
                "resumed": True,
                "next_gate": "trusted organizational signoff policy approval is unavailable",
            }
        outputs = [
            *manifest["outputs"],
            *[
                {
                    "role": f"external_evidence_{kind}",
                    "content_sha256": digest,
                    "reference": f"workflow://external-evidence/{digest}",
                }
                for kind, digest in sorted(evidence_hashes.items())
            ],
        ]
        stage = self._append_stage(
            parent=manifest,
            parent_sha256=current["manifest_sha256"],
            stage="external_evidence_attached",
            approved_intent_sha256=manifest["approved_intent_sha256"],
            evidence={
                **manifest["evidence"],
                "external_evidence_provenance_verified": True,
                "external_evidence_is_mock": False,
            },
            normalized_inputs={
                **manifest["normalized_inputs"],
                "external_evidence_sha256s": evidence_hashes,
            },
            outputs=outputs,
            fingerprints={
                **manifest["fingerprints"],
                **{
                    f"external_evidence_{kind}": digest
                    for kind, digest in evidence_hashes.items()
                },
            },
            runtime={"external_evidence_report_files_rehashed": True},
            blockers=["trusted organizational signoff policy approval is unavailable"],
            completed=True,
        )
        if self.signoff_policy is not None:
            return self._apply_signoff_policy(
                job_id=job_id,
                current={
                    "manifest": stage["manifest"],
                    "manifest_sha256": stage["manifest_sha256"],
                },
                manifest=stage["manifest"],
                verified=verified,
                evidence_hashes=evidence_hashes,
                base_result=base_result,
            )
        return {
            **base_result,
            "workflow_status": "external_evidence_attached",
            "manifest_sha256": stage["manifest_sha256"],
            "external_evidence_sha256s": evidence_hashes,
            "external_evidence_provenance_verified": True,
            "external_evidence_is_mock": False,
            "resumed": False,
            "next_gate": "trusted organizational signoff policy approval is unavailable",
        }

    def _apply_signoff_policy(
        self,
        *,
        job_id: str,
        current: Mapping[str, Any],
        manifest: Mapping[str, Any],
        verified: list[Mapping[str, Any]],
        evidence_hashes: Mapping[str, str],
        base_result: Mapping[str, Any],
    ) -> dict[str, Any]:
        decision = evaluate_signoff_policy(
            verified_evidence=verified,
            policy=self.signoff_policy,
        )
        decision_sha256 = self.store.put_document("signoff_policy_result", decision)
        if manifest["stage"] == "signoff_evidence_approved":
            persisted = next(
                (
                    item
                    for item in manifest["outputs"]
                    if item["role"] == "signoff_policy_result"
                ),
                None,
            )
            if persisted is None or persisted["content_sha256"] != decision_sha256:
                _fail(
                    "WORKFLOW_SIGNOFF_POLICY_IDENTITY_DRIFT",
                    "A completed signoff cannot silently replace its policy decision.",
                    details={
                        "persisted_sha256": (
                            None if persisted is None else persisted["content_sha256"]
                        ),
                        "received_sha256": decision_sha256,
                    },
                )
            return {
                **base_result,
                "workflow_status": "signoff_evidence_approved",
                "external_evidence_sha256s": dict(evidence_hashes),
                "external_evidence_provenance_verified": True,
                "signoff_policy_id": decision["policy_id"],
                "signoff_policy_result_sha256": decision_sha256,
                "signoff_policy_approved": True,
                "resumed": True,
                "layout_signoff_evidence_approved": True,
                "production_ready": False,
                "next_gate": "organization-specific release workflow outside this facade",
            }
        outputs = [
            *manifest["outputs"],
            {
                "role": "signoff_policy_result",
                "content_sha256": decision_sha256,
                "reference": f"workflow://signoff-policy/{decision_sha256}",
            },
        ]
        stage = self._append_stage(
            parent=manifest,
            parent_sha256=current["manifest_sha256"],
            stage="signoff_evidence_approved",
            approved_intent_sha256=manifest["approved_intent_sha256"],
            evidence={
                **manifest["evidence"],
                "external_evidence_is_mock": False,
                "signoff_approval_reference_present": True,
                "signoff_policy_approved": True,
            },
            normalized_inputs={
                **manifest["normalized_inputs"],
                "signoff_policy_id": decision["policy_id"],
                "signoff_policy_result_sha256": decision_sha256,
            },
            outputs=outputs,
            fingerprints={
                **manifest["fingerprints"],
                "signoff_policy_result": decision_sha256,
                "signoff_policy_receipt": decision["receipt_sha256"],
            },
            runtime={
                **manifest["runtime"],
                "signoff_policy_id": decision["policy_id"],
            },
            blockers=[],
            completed=True,
        )
        return {
            **base_result,
            "workflow_status": "signoff_evidence_approved",
            "manifest_sha256": stage["manifest_sha256"],
            "external_evidence_sha256s": dict(evidence_hashes),
            "external_evidence_provenance_verified": True,
            "signoff_policy_id": decision["policy_id"],
            "signoff_policy_result_sha256": decision_sha256,
            "signoff_policy_approved": True,
            "resumed": False,
            "layout_signoff_evidence_approved": True,
            "production_ready": False,
            "next_gate": "organization-specific release workflow outside this facade",
        }


def workflow_store_contract() -> dict[str, Any]:
    return {
        "contract_version": 2,
        "documents_content_addressed": True,
        "manifests_append_only": True,
        "per_job_manifest_append_serialized_across_local_processes": True,
        "job_ids_lowercase_windows_safe": True,
        "full_manifest_ancestry_revalidated": True,
        "mutable_head_integrity_checked": True,
        "approval_boolean_grants_authority": False,
        "approval_reverified_for_every_privileged_action": True,
        "live_process_capability_rehashed_for_every_privileged_action": True,
        "output_root_host_controlled": True,
        "absolute_or_parent_traversal_output_allowed": False,
        "production_test_mock_verifiers_allowed": False,
        "workflow_engines_host_registered_by_process_profile": True,
        "model_can_register_or_import_engines": False,
        "incomplete_drafts_persisted": True,
        "incomplete_drafts_are_immutable_revisions": True,
        "draft_resume_token_content_bound": True,
        "validate_only_does_not_persist": True,
        "validation_errors_include_actionable_report": True,
        "technology_adapter_exact_package_and_snapshot_pinned": True,
        "measurement_manifest_requires_fresh_layout_file_hash": True,
        "measurement_execution_semantics_bound_to_approved_intent": True,
        "measurement_safety_envelope_cannot_be_relaxed_by_manifest": True,
        "measurement_package_is_signoff": False,
        "external_report_and_layout_hashes_recomputed": True,
        "external_evidence_requires_host_adapter_registry": True,
        "external_evidence_is_signoff": False,
        "signoff_policy_host_injected": True,
        "signoff_policy_default": "fail_closed_unavailable",
        "drc_lvs_pex_universally_required": False,
        "signoff_requires_exact_host_policy_evidence_set": True,
        "signoff_state_means_production_ready": False,
        "generation_uses_durable_staging_before_final_promotion": True,
        "content_document_publish_concurrent_no_clobber": True,
        "final_output_publish_concurrent_no_clobber": True,
        "generation_resume_after_final_write_without_engine_rerun": True,
        "status_rehashes_workflow_documents": True,
        "model_can_register_or_import_signoff_policy": False,
    }
