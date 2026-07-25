#!/usr/bin/env python3
"""Verify built distribution metadata and source-side FAIR metadata assets."""

from __future__ import annotations

import tarfile
import zipfile
from email import message_from_bytes
from pathlib import Path

import tomllib

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
    # PyPI/pip-installed abicheck is the lightweight/core distribution: it must
    # never pull in CastXML transitively, on any extra. `pip install abicheck`
    # (or `abicheck[mcp]`/`[dev]`/`[docs]`/`[dist]`/`[validation]`) installing
    # CastXML would silently promote the legacy, unsupported PyPI `castxml`
    # distribution (last released 0.4.5 in 2018 — see castxml_policy.py) into
    # abicheck's default install path, contradicting the documented contract
    # that a real L2 scanner setup comes from conda-forge or an explicitly
    # managed CastXML/compiler install.
    requires_dist = metadata.get_all("Requires-Dist", [])
    castxml_requires = [r for r in requires_dist if "castxml" in r.lower()]
    if castxml_requires:
        fail(
            "wheel Requires-Dist pulls in castxml, which must never be a "
            f"pip-installed abicheck dependency: {castxml_requires!r}"
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
