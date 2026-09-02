"""Write one CI validation record from pytest JUnit XML and explicit provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import xml.etree.ElementTree as ET


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _junit_counts(path: Path) -> dict[str, int | float]:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    result: dict[str, int | float] = {
        "tests": 0,
        "failures": 0,
        "errors": 0,
        "skipped": 0,
        "time_seconds": 0.0,
    }
    for suite in suites:
        result["tests"] += int(suite.attrib.get("tests", 0))
        result["failures"] += int(suite.attrib.get("failures", 0))
        result["errors"] += int(suite.attrib.get("errors", 0))
        result["skipped"] += int(suite.attrib.get("skipped", 0))
        result["time_seconds"] += float(suite.attrib.get("time", 0.0))
    result["time_seconds"] = round(float(result["time_seconds"]), 6)
    result["passed"] = int(result["tests"]) - int(result["failures"]) - int(result["errors"]) - int(result["skipped"])
    return result


def build_record(*, junit: Path, commit: str, job: str, klayout_version: str | None) -> dict:
    counts = _junit_counts(junit)
    return {
        "schema_version": 1,
        "commit_sha": commit,
        "job": job,
        "run_url": (
            f"{os.environ.get('GITHUB_SERVER_URL')}/{os.environ.get('GITHUB_REPOSITORY')}"
            f"/actions/runs/{os.environ.get('GITHUB_RUN_ID')}"
            if os.environ.get("GITHUB_RUN_ID")
            else None
        ),
        "runtime": {
            "os": platform.platform(),
            "python": platform.python_version(),
            "klayout": klayout_version,
        },
        "dependency_provenance": {
            "uv_lock_sha256": _sha256(Path("uv.lock")),
            "frozen_lock_required": True,
        },
        "pytest": counts,
        "success": counts["failures"] == 0 and counts["errors"] == 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--junit", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--klayout-version")
    args = parser.parse_args()
    record = build_record(
        junit=args.junit,
        commit=args.commit,
        job=args.job,
        klayout_version=args.klayout_version,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")


if __name__ == "__main__":
    main()
