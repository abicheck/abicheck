#!/usr/bin/env python3
"""Verify built distribution metadata and source-side FAIR metadata assets."""

from __future__ import annotations

import tarfile
import tomllib
import zipfile
from email import message_from_bytes
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement

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
        metadata_name = next(
            (
                name
                for name in archive.namelist()
                if name.endswith(".dist-info/METADATA")
            ),
            None,
        )
        if metadata_name is None:
            fail("wheel is missing core METADATA")
        metadata = message_from_bytes(archive.read(metadata_name))
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
    # The official CastXML wheel is part of the standard install so pip users
    # get the L2 scanner by default. Keep its executable release pinned.
    requires_dist = metadata.get_all("Requires-Dist", [])
    castxml_requires = [r for r in requires_dist if "castxml" in r.lower()]
    try:
        parsed_castxml = [Requirement(requirement) for requirement in castxml_requires]
    except InvalidRequirement as exc:
        fail(f"wheel has invalid CastXML Requires-Dist metadata: {exc}")
    if (
        len(parsed_castxml) != 1
        or parsed_castxml[0].name.lower() != "castxml"
        or str(parsed_castxml[0].specifier) != "==0.8.0"
        or parsed_castxml[0].marker is not None
    ):
        fail(
            "wheel Requires-Dist must include exactly the pinned default "
            f"CastXML 0.8.0 dependency: {castxml_requires!r}"
        )
    project_urls: dict[str, str] = {}
    for value in metadata.get_all("Project-URL", []):
        try:
            label, url = value.split(", ", 1)
        except ValueError:
            fail(f"wheel contains malformed Project-URL metadata: {value!r}")
        project_urls[label] = url
    if project_urls != project["urls"]:
        fail("wheel Project-URL metadata does not agree with pyproject.toml")
    print("Distribution metadata checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
