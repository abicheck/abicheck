#!/usr/bin/env python3
"""Verify built distribution metadata and source-side FAIR metadata assets."""
from __future__ import annotations

import email
import tarfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def main() -> int:
    with (ROOT / "pyproject.toml").open("rb") as file:
        project = tomllib.load(file)["project"]
    sdists = sorted(DIST.glob("*.tar.gz"))
    wheels = sorted(DIST.glob("*.whl"))
    if len(sdists) != 1 or len(wheels) != 1:
        fail("expected exactly one sdist and one wheel in dist/")

    with tarfile.open(sdists[0]) as archive:
        names = archive.getnames()
    for asset in ("CITATION.cff", "codemeta.json", ".zenodo.json"):
        if not any(name.endswith("/" + asset) for name in names):
            fail(f"sdist is missing {asset}")

    with zipfile.ZipFile(wheels[0]) as archive:
        metadata_name = next((name for name in archive.namelist() if name.endswith(".dist-info/METADATA")), None)
        if metadata_name is None:
            fail("wheel is missing core METADATA")
        metadata = email.message_from_bytes(archive.read(metadata_name))
    checks = {
        "Name": project["name"],
        "Version": project["version"],
        "Summary": project["description"],
        "Requires-Python": project["requires-python"],
        "License": project["license"]["text"],
    }
    for field, expected in checks.items():
        if metadata.get(field) != expected:
            fail(f"wheel {field} is {metadata.get(field)!r}, expected {expected!r}")
    if metadata.get("Project-URL", "").split(", ", 1)[-1] != project["urls"]["Repository"]:
        fail("wheel Project-URL does not include the canonical repository URL")
    print("Distribution metadata checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
