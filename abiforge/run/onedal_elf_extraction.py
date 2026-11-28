# SPDX-License-Identifier: Apache-2.0
"""Compare abicheck's ELF reader against binutils for every shipped oneDAL DSO."""

import argparse
import hashlib
import json
import os
import re
import subprocess
from collections import Counter
from pathlib import Path

from abiforge.checkers.abicheck import elf_release_surface

from .onedal_export_surface import surface, validate_release_labels


def version_requirements(path):
    """Read the dependency-specific version sets independently with GNU readelf."""
    output = subprocess.check_output(
        ["readelf", "-VW", path], text=True, env={**os.environ, "LC_ALL": "C"}
    )
    result = {}
    indices = {}
    for block in output.split("Version needs section ")[1:]:
        header, _, rows = block.partition("\n")
        count = re.search(r"contains (\d+) entr", header)
        dependencies = re.split(r"\bFile: (\S+)\s+Cnt: (\d+)", rows.split("\n\n")[0])
        if not count or len(dependencies) != 1 + 3 * int(count[1]):
            raise ValueError(f"incomplete version needs table: {path}")
        for library, size, body in zip(
            dependencies[1::3], dependencies[2::3], dependencies[3::3], strict=True
        ):
            entries = re.findall(r"\bName: (\S+)\s+Flags:.*?Version: (\d+)", body)
            versions = [version for version, _ in entries]
            for version, index in entries:
                if index in indices:
                    raise ValueError(f"duplicate version index {index}: {path}")
                indices[index] = library, version
            if len(versions) != int(size) or library in result:
                raise ValueError(
                    f"incomplete or duplicate version requirements: {path} {library}"
                )
            result[library] = sorted(versions)
    return result, indices


def dynamic_symbols(path, indices):
    output = subprocess.check_output(
        ["readelf", "--dyn-syms", "--wide", path],
        text=True,
        env={**os.environ, "LC_ALL": "C"},
    )
    exports = Counter()
    imports = Counter()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 8 or not fields[0].removesuffix(":").isdigit():
            continue
        name = fields[7]
        if fields[6] != "UND":
            if fields[4] != "LOCAL":
                exports[
                    name,
                    fields[3].lower(),
                    fields[4].lower(),
                    fields[5].lower(),
                    int(fields[2], 0),
                ] += 1
            continue
        version = provider = ""
        if len(fields) == 9:
            index = re.fullmatch(r"\((\d+)\)", fields[8])
            if index is None or index[1] not in indices:
                raise ValueError(f"unknown version index: {line}")
            provider, version = indices[index[1]]
            name, separator, label = name.rpartition("@")
            if not separator or label != version:
                raise ValueError(f"version index and name disagree: {line}")
        elif len(fields) != 8:
            raise ValueError(f"unrecognized dynamic import: {line}")
        imports[name, fields[4].lower(), version, provider] += 1
    return exports, imports


def loader_tags(path):
    output = subprocess.check_output(
        ["objdump", "-p", path], text=True, env={**os.environ, "LC_ALL": "C"}
    )
    tags = {"NEEDED": [], "SONAME": [], "RPATH": [], "RUNPATH": []}
    for line in output.splitlines():
        fields = line.split(None, 1)
        if fields and fields[0] in tags:
            tags[fields[0]].append(fields[1] if len(fields) == 2 else "")
    return tags


def audit(prefix):
    providers = surface(prefix)
    result = {}
    for path in sorted({p.resolve() for p in (prefix / "lib").glob("libonedal*.so*")}):
        actual = elf_release_surface(path)
        tags = loader_tags(path)
        if (
            tags["SONAME"] != [actual.soname]
            or tags["NEEDED"] != actual.needed
            or tags["RPATH"] != ([actual.rpath] if actual.rpath else [])
            or tags["RUNPATH"] != ([actual.runpath] if actual.runpath else [])
        ):
            raise ValueError(f"dynamic loader tags differ: {path}")
        required, indices = version_requirements(path)
        expected_exports, expected_imports = dynamic_symbols(path, indices)
        if expected_exports != Counter(actual.exports):
            raise ValueError(
                f"export type, binding, visibility, size or name differs: {path}"
            )
        actual_names = {entry[0] for entry in actual.exports}
        types = Counter()
        for (_, kind, _, _, _), count in expected_exports.items():
            types[kind] += count
        if expected_imports != Counter(actual.imports):
            raise ValueError(
                f"import names, bindings or version providers differ: {path}"
            )
        if required != {
            lib: sorted(values) for lib, values in actual.versions_required.items()
        }:
            raise ValueError(f"version requirements differ: {path}")
        expected = providers[actual.soname]
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        result[actual.soname] = {
            "sha256": digest,
            "loader_tags": tags,
            "exports": len(expected),
            "export_types": dict(types),
            "imports": expected_imports.total(),
            "versioned_imports": sum(
                count
                for (_, _, version, _), count in expected_imports.items()
                if version
            ),
            "versions_required": required,
            "missing": sorted(expected - actual_names),
            "unexpected": sorted(actual_names - expected),
        }
    return result


def verify_inventory(reports, receipt):
    """Verify the pinned release labels, SONAMEs and exact artifact bytes."""

    def members(data):
        return {
            (release, soname): row["sha256"]
            for release, libraries in data.items()
            for soname, row in libraries.items()
        }

    expected = members(receipt)
    actual = members(reports)
    if (
        not expected
        or actual.keys() != expected.keys()
        or reports.keys() != receipt.keys()
    ):
        raise ValueError(
            f"release inventory differs: missing={sorted(expected.keys() - actual.keys())}, "
            f"unexpected={sorted(actual.keys() - expected.keys())}"
        )
    for key, digest in expected.items():
        if actual[key] != digest:
            raise ValueError(f"release digest differs: {key}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefix", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument(
        "--verify-inventory", type=Path, help="Pinned extraction receipt"
    )
    args = parser.parse_args()
    validate_release_labels(args.prefix)
    reports = {prefix.name: audit(prefix) for prefix in args.prefix}
    if args.verify_inventory:
        verify_inventory(reports, json.loads(args.verify_inventory.read_text()))
    args.out.write_text(json.dumps(reports, indent=2, sort_keys=True) + "\n")
    raise SystemExit(
        any(
            row["missing"] or row["unexpected"]
            for r in reports.values()
            for row in r.values()
        )
    )
