"""Create-only publication helpers for verified local files."""

from __future__ import annotations

import ctypes
import errno
from functools import lru_cache
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
import uuid


SUPPORTED_LOCAL_FILESYSTEMS = frozenset({"ntfs", "ext4", "xfs"})
UNSUPPORTED_REMOTE_FILESYSTEMS = frozenset(
    {"nfs", "nfs4", "cifs", "smb", "smb2", "smb3", "fuse.sshfs"}
)
STAGING_NAME_PATTERN = re.compile(
    r"^\.klayout-stage-(?P<kind>file|dir)-[a-z0-9][a-z0-9_-]{0,31}-"
    r"(?P<pid>[0-9]+)-[A-Za-z0-9_.-]+$"
)


class OutputAlreadyExistsError(FileExistsError):
    """Raised when another writer already owns the requested final path."""


class UnsupportedPublicationFilesystemError(OSError):
    """Raised when create-only guarantees are unavailable on the target filesystem."""


def _unescape_mount_path(value: str) -> str:
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


@lru_cache(maxsize=64)
def _linux_filesystem_type(resolved_path: str) -> str:
    path = Path(resolved_path)
    probe = path if path.exists() else path.parent
    probe = probe.resolve()
    best_mount = ""
    best_type = "unknown"
    try:
        lines = Path("/proc/self/mountinfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return "unknown"
    for line in lines:
        fields = line.split()
        try:
            separator = fields.index("-")
            mount_point = _unescape_mount_path(fields[4])
            filesystem_type = fields[separator + 1].casefold()
        except (ValueError, IndexError):
            continue
        try:
            probe.relative_to(Path(mount_point))
        except ValueError:
            continue
        if len(mount_point) > len(best_mount):
            best_mount = mount_point
            best_type = filesystem_type
    return best_type


@lru_cache(maxsize=64)
def _windows_filesystem_type(resolved_path: str) -> str:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    volume_path = ctypes.create_unicode_buffer(261)
    if not kernel32.GetVolumePathNameW(str(resolved_path), volume_path, len(volume_path)):
        return "unknown"
    filesystem_name = ctypes.create_unicode_buffer(261)
    if not kernel32.GetVolumeInformationW(
        volume_path.value,
        None,
        0,
        None,
        None,
        None,
        filesystem_name,
        len(filesystem_name),
    ):
        return "unknown"
    return filesystem_name.value.casefold()


def publication_filesystem_type(path: str | Path) -> str:
    """Return the target filesystem type used by the publication safety contract."""

    resolved = Path(path).expanduser().resolve()
    if os.name == "nt":
        return _windows_filesystem_type(str(resolved))
    if sys.platform.startswith("linux"):
        return _linux_filesystem_type(str(resolved))
    return "unknown"


def require_supported_publication_root(path: str | Path) -> str:
    """Fail closed outside the explicitly qualified same-host filesystems."""

    filesystem_type = publication_filesystem_type(path)
    if filesystem_type not in SUPPORTED_LOCAL_FILESYSTEMS:
        scope = "remote" if filesystem_type in UNSUPPORTED_REMOTE_FILESYSTEMS else "unknown"
        raise UnsupportedPublicationFilesystemError(
            f"Unsupported {scope} publication filesystem: {filesystem_type}"
        )
    return filesystem_type


def publication_staging_prefix(kind: str, *, directory: bool = False) -> str:
    """Return the reserved owner-tagged prefix used for recoverable staging entries."""

    normalized = str(kind).strip().casefold().replace("_", "-")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", normalized):
        raise ValueError("Publication staging kind must be a short filesystem-safe token.")
    entry_kind = "dir" if directory else "file"
    return f".klayout-stage-{entry_kind}-{normalized}-{os.getpid()}-"


def scavenge_stale_publication_entries(
    root: str | Path,
    *,
    ttl_seconds: float,
    now: float | None = None,
) -> dict[str, object]:
    """Remove only old reserved staging entries within one supported output root."""

    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)) or ttl_seconds < 0:
        raise ValueError("ttl_seconds must be a non-negative number.")
    resolved_root = Path(root).expanduser().resolve()
    require_supported_publication_root(resolved_root)
    cutoff = float(time.time() if now is None else now) - float(ttl_seconds)
    removed_files = 0
    removed_directories = 0
    skipped_recent = 0
    skipped_unsafe = 0
    candidates = sorted(
        resolved_root.rglob(".klayout-stage-*"),
        key=lambda value: len(value.parts),
        reverse=True,
    )
    for candidate in candidates:
        if not STAGING_NAME_PATTERN.fullmatch(candidate.name) or candidate.is_symlink():
            skipped_unsafe += 1
            continue
        try:
            resolved_candidate = candidate.resolve()
            resolved_candidate.relative_to(resolved_root)
            modified = candidate.stat().st_mtime
        except (OSError, ValueError):
            skipped_unsafe += 1
            continue
        if modified > cutoff:
            skipped_recent += 1
            continue
        if candidate.is_dir():
            shutil.rmtree(candidate)
            removed_directories += 1
        elif candidate.is_file():
            candidate.unlink()
            removed_files += 1
        else:
            skipped_unsafe += 1
    return {
        "root": str(resolved_root),
        "ttl_seconds": float(ttl_seconds),
        "removed_file_count": removed_files,
        "removed_directory_count": removed_directories,
        "skipped_recent_count": skipped_recent,
        "skipped_unsafe_count": skipped_unsafe,
    }


def _fsync_file(path: Path) -> None:
    # Windows requires a writable descriptor for ``os.fsync`` even though the
    # publication helper does not modify the completed payload.
    with path.open("rb+") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def publish_new_file(staged_path: str | Path, final_path: str | Path) -> Path:
    """Publish a completed sibling file without replacing an existing target.

    ``os.link`` is the commit primitive: on supported same-host local filesystems it
    atomically creates the final directory entry or raises when that name already
    exists. The caller retains ownership of the staged path and must clean it up.
    """

    staged = Path(staged_path).resolve()
    final = Path(final_path).resolve()
    if staged.parent != final.parent:
        raise ValueError("Create-only publication requires a same-directory staged file.")
    if not staged.is_file():
        raise FileNotFoundError(f"Staged publication file does not exist: {staged}")
    require_supported_publication_root(final.parent)

    _fsync_file(staged)
    try:
        os.link(staged, final)
    except FileExistsError as exc:
        raise OutputAlreadyExistsError(str(final)) from exc
    _fsync_directory(final.parent)
    return final


def _fsync_tree(root: Path) -> None:
    entries = list(root.rglob("*"))
    for entry in entries:
        if entry.is_symlink():
            raise ValueError("Directory publication does not permit symbolic links.")
        if entry.is_file():
            _fsync_file(entry)
    for directory in sorted(
        (entry for entry in entries if entry.is_dir()),
        key=lambda value: len(value.parts),
        reverse=True,
    ):
        _fsync_directory(directory)
    _fsync_directory(root)


def _rename_directory_no_replace(staged: Path, final: Path) -> None:
    if os.name == "nt":
        try:
            os.rename(staged, final)
        except FileExistsError as exc:
            raise OutputAlreadyExistsError(str(final)) from exc
        except OSError as exc:
            if final.exists():
                raise OutputAlreadyExistsError(str(final)) from exc
            raise
        return
    if not sys.platform.startswith("linux"):
        raise UnsupportedPublicationFilesystemError(
            "Create-only directory publication is implemented only for Windows and Linux."
        )
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise UnsupportedPublicationFilesystemError(
            "Linux renameat2(RENAME_NOREPLACE) is unavailable."
        )
    renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    renameat2.restype = ctypes.c_int
    result = renameat2(
        -100,
        os.fsencode(staged),
        -100,
        os.fsencode(final),
        1,
    )
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise OutputAlreadyExistsError(str(final))
    raise OSError(error_number, os.strerror(error_number), str(final))


def publish_new_directory(staged_path: str | Path, final_path: str | Path) -> Path:
    """Atomically publish a complete sibling directory without replacing a winner.

    Successful publication consumes the staged directory. A collision leaves the caller's
    staged directory untouched so the caller can verify the winner before cleanup.
    """

    staged = Path(staged_path).resolve()
    final = Path(final_path).resolve()
    if staged.parent != final.parent:
        raise ValueError("Create-only publication requires a same-directory staged directory.")
    if not staged.is_dir():
        raise FileNotFoundError(f"Staged publication directory does not exist: {staged}")
    require_supported_publication_root(final.parent)
    _fsync_tree(staged)
    _rename_directory_no_replace(staged, final)
    _fsync_directory(final.parent)
    return final


def publication_root_doctor(path: str | Path, *, active_probe: bool = True) -> dict[str, object]:
    """Report and optionally probe the exact create-only primitives used by writers."""

    root = Path(path).expanduser().resolve()
    filesystem_type = publication_filesystem_type(root)
    supported = filesystem_type in SUPPORTED_LOCAL_FILESYSTEMS
    report: dict[str, object] = {
        "root": str(root),
        "filesystem_type": filesystem_type,
        "supported_filesystem": supported,
        "same_host_local_required": True,
        "file_create_only_probe": False,
        "directory_create_only_probe": False,
        "blocker_code": None if supported else "UNSUPPORTED_PUBLICATION_FILESYSTEM",
    }
    if not active_probe or not supported:
        return report
    root.mkdir(parents=True, exist_ok=True)
    probe_root = Path(
        tempfile.mkdtemp(prefix=publication_staging_prefix("doctor", directory=True), dir=root)
    )
    try:
        staged_file = probe_root / f".staged-{uuid.uuid4().hex}"
        final_file = probe_root / "file.final"
        staged_file.write_bytes(b"publication-doctor")
        publish_new_file(staged_file, final_file)
        report["file_create_only_probe"] = final_file.read_bytes() == b"publication-doctor"
        staged_file.unlink(missing_ok=True)

        staged_directory = probe_root / f".directory-{uuid.uuid4().hex}"
        final_directory = probe_root / "directory.final"
        staged_directory.mkdir()
        (staged_directory / "payload").write_bytes(b"publication-doctor")
        publish_new_directory(staged_directory, final_directory)
        report["directory_create_only_probe"] = (
            final_directory.joinpath("payload").read_bytes() == b"publication-doctor"
        )
    finally:
        shutil.rmtree(probe_root, ignore_errors=True)
    return report
