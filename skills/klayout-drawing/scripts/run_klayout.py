"""Run a Python script with the installed KLayout runtime on Windows or Linux."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys


def executable_candidates(explicit: str | None) -> list[Path]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("KLAYOUT_EXE"):
        candidates.append(Path(os.environ["KLAYOUT_EXE"]))
    if sys.platform == "win32":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            candidates.append(
                Path(local_app_data) / "Programs" / "KLayout" / "klayout_app.exe"
            )
        candidates.extend(
            [
                Path(r"C:\Program Files\KLayout\klayout_app.exe"),
                Path(r"C:\Program Files\KLayout\klayout.exe"),
            ]
        )
    for name in ("klayout_app.exe", "klayout.exe", "klayout_app", "klayout"):
        resolved = shutil.which(name)
        if resolved:
            candidates.append(Path(resolved))
    return candidates


def find_executable(explicit: str | None) -> Path:
    for candidate in executable_candidates(explicit):
        if candidate.is_file():
            return candidate.resolve()
    raise FileNotFoundError(
        "KLayout executable not found; set KLAYOUT_EXE or pass --klayout-executable"
    )


def parse_definition(value: str) -> str:
    name, separator, assigned = value.partition("=")
    if not separator or not name.isidentifier() or not assigned:
        raise argparse.ArgumentTypeError("definitions must use name=value")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("script", type=Path)
    parser.add_argument("--define", action="append", default=[], type=parse_definition)
    parser.add_argument("--hidden-view", action="store_true")
    parser.add_argument("--klayout-executable")
    args = parser.parse_args()

    script = args.script.resolve(strict=True)
    executable = find_executable(args.klayout_executable)
    command = [str(executable)]
    if args.hidden_view:
        command.extend(["-z", "-nc", "-rx", "-r", str(script)])
    else:
        command.extend(["-b", "-r", str(script)])
    for definition in args.define:
        command.extend(["-rd", definition])

    completed = subprocess.run(command, check=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
