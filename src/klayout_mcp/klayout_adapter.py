"""Host-side bridge to KLayout's bundled pya runtime."""

from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping

from .errors import AnalysisError


@dataclass(frozen=True)
class LayoutSnapshot:
    """One immutable file capture used across multiple KLayout worker passes."""

    source_path: Path
    path: Path
    sha256: str
    size_bytes: int


@contextmanager
def create_layout_snapshot(
    layout_path: str,
    *,
    purpose: str = "padset",
) -> Iterator[LayoutSnapshot]:
    """Copy one input layout once and reject a source change during capture."""

    profiles = {
        "padset": {
            "label": "Padset",
            "path_key": "padset_path",
            "not_found": "PADSET_NOT_FOUND",
            "changed": "PADSET_CHANGED_DURING_SNAPSHOT",
            "failed": "PADSET_SNAPSHOT_FAILED",
            "prefix": "klayout-teg-snapshot-",
        },
        "sample": {
            "label": "Sample layout",
            "path_key": "sample_layout_path",
            "not_found": "SAMPLE_LAYOUT_NOT_FOUND",
            "changed": "SAMPLE_LAYOUT_CHANGED_DURING_SNAPSHOT",
            "failed": "SAMPLE_LAYOUT_SNAPSHOT_FAILED",
            "prefix": "klayout-sample-snapshot-",
        },
    }
    if purpose not in profiles:
        raise AnalysisError(
            code="INVALID_SNAPSHOT_PURPOSE",
            message="Layout snapshot purpose is not supported.",
            details={"purpose": purpose},
            next_action="Use the supported 'padset' or 'sample' snapshot purpose.",
        )
    profile = profiles[purpose]

    source_path = Path(layout_path).expanduser().resolve()
    if not source_path.is_file():
        raise AnalysisError(
            code=profile["not_found"],
            message=f"{profile['label']} does not exist.",
            details={profile["path_key"]: str(source_path)},
            next_action="Provide an existing GDS or OAS path.",
        )

    with tempfile.TemporaryDirectory(prefix=profile["prefix"]) as temp_dir:
        try:
            before = source_path.stat()
            snapshot_path = Path(temp_dir) / source_path.name
            digest = hashlib.sha256()
            size_bytes = 0
            with source_path.open("rb") as source, snapshot_path.open("xb") as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
                    digest.update(chunk)
                    size_bytes += len(chunk)
            after = source_path.stat()
            if (
                before.st_size != after.st_size
                or before.st_mtime_ns != after.st_mtime_ns
                or size_bytes != before.st_size
            ):
                raise AnalysisError(
                    code=profile["changed"],
                    message=f"{profile['label']} changed while its snapshot was captured.",
                    details={
                        profile["path_key"]: str(source_path),
                        "size_before": before.st_size,
                        "size_after": after.st_size,
                        "mtime_ns_before": before.st_mtime_ns,
                        "mtime_ns_after": after.st_mtime_ns,
                    },
                    next_action="Stop other writers and retry with a stable padset file.",
                )
        except AnalysisError:
            raise
        except OSError as exc:
            raise AnalysisError(
                code=profile["failed"],
                message=f"{profile['label']} could not be captured for analysis.",
                details={profile["path_key"]: str(source_path), "error": str(exc)},
                next_action="Check file permissions and available temporary disk space.",
            ) from exc
        yield LayoutSnapshot(
            source_path=source_path,
            path=snapshot_path,
            sha256=digest.hexdigest(),
            size_bytes=size_bytes,
        )


def find_klayout_executable(explicit_path: str | None = None) -> Path:
    candidates: list[str] = []
    if explicit_path:
        candidates.append(explicit_path)
    if os.environ.get("KLAYOUT_EXE"):
        candidates.append(os.environ["KLAYOUT_EXE"])
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(str(Path(local_app_data) / "Programs" / "KLayout" / "klayout_app.exe"))
        candidates.extend(
            [
                r"C:\Program Files\KLayout\klayout_app.exe",
                r"C:\Program Files\KLayout\klayout.exe",
            ]
        )
    for command in ("klayout", "klayout_app.exe", "klayout.exe"):
        resolved = shutil.which(command)
        if resolved:
            candidates.append(resolved)

    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.is_file():
            return path.resolve()
    raise AnalysisError(
        code="KLAYOUT_NOT_FOUND",
        message="KLayout executable was not found.",
        details={"checked": candidates},
        next_action=(
            "Set KLAYOUT_EXE to the KLayout executable. "
            "In csh use: setenv KLAYOUT_EXE /path/to/klayout"
        ),
    )


def run_klayout_worker(
    request: Mapping[str, Any],
    *,
    executable_path: str | None = None,
    timeout_seconds: float = 60.0,
    hidden_view: bool = False,
) -> dict[str, Any]:
    executable = find_klayout_executable(executable_path)
    worker = Path(__file__).with_name("klayout_worker.py").resolve()
    with tempfile.TemporaryDirectory(prefix="klayout-teg-mcp-") as temp_dir:
        request_path = Path(temp_dir) / "request.json"
        response_path = Path(temp_dir) / "response.json"
        request_path.write_text(json.dumps(dict(request)), encoding="utf-8")
        mode_arguments = ["-z", "-nc", "-rx"] if hidden_view else ["-b"]
        command = [
            str(executable),
            *mode_arguments,
            "-r",
            str(worker),
            "-rd",
            f"request_path={request_path}",
            "-rd",
            f"response_path={response_path}",
        ]
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AnalysisError(
                code="KLAYOUT_TIMEOUT",
                message="KLayout worker timed out.",
                details={"timeout_seconds": timeout_seconds},
                next_action="Check the layout size or increase timeout_seconds.",
            ) from exc
        except OSError as exc:
            raise AnalysisError(
                code="KLAYOUT_START_FAILED",
                message="KLayout could not be started.",
                details={"executable": str(executable), "error": str(exc)},
                next_action="Check KLayout execute permissions and the configured executable path.",
            ) from exc
        if completed.returncode != 0:
            raise AnalysisError(
                code="KLAYOUT_EXECUTION_FAILED",
                message="KLayout worker exited with an error.",
                details={
                    "exit_code": completed.returncode,
                    "stdout": completed.stdout[-4000:],
                    "stderr": completed.stderr[-4000:],
                },
                next_action="Inspect KLayout output and verify the installed version.",
            )
        if not response_path.is_file():
            raise AnalysisError(
                code="KLAYOUT_RESPONSE_MISSING",
                message="KLayout worker did not write a response.",
                details={"stdout": completed.stdout[-4000:], "stderr": completed.stderr[-4000:]},
                next_action="Inspect KLayout output and worker compatibility.",
            )
        try:
            result = json.loads(response_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise AnalysisError(
                code="KLAYOUT_RESPONSE_INVALID",
                message="KLayout worker response is invalid.",
                details={"error": str(exc)},
                next_action="Inspect KLayout output and verify worker/version compatibility.",
            ) from exc
    if not isinstance(result, dict):
        raise AnalysisError(
            code="KLAYOUT_RESPONSE_INVALID",
            message="KLayout worker response must be an object.",
            next_action="Inspect the worker response and return one JSON object.",
        )
    return result
