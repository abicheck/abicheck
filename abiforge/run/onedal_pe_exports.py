# SPDX-License-Identifier: Apache-2.0
"""Compare oneDAL Windows DLL export names, ordinals and forwarding destinations with objdump."""

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

from abiforge.checkers.abicheck import pe_loader_surface


def validate_release_receipt(receipt):
    """Require all four providers for each release in the pinned Windows campaign."""
    majors = {"2025.9.0": 3, "2025.11.0": 3, "2026.2.0": 4}
    providers = ("onedal", "onedal_core", "onedal_dpc", "onedal_thread")
    expected = {
        f"{release}/{provider}.{major}.dll"
        for release, major in majors.items()
        for provider in providers
    }
    missing = sorted(expected - receipt.keys())
    unexpected = sorted(receipt.keys() - expected)
    if missing or unexpected:
        raise ValueError(
            f"Windows release inventory differs: missing={missing}, unexpected={unexpected}"
        )
    for key, row in receipt.items():
        if row["missing"] or row["unexpected"]:
            raise ValueError(
                f"unresolved export mismatch in Windows release receipt: {key}"
            )


def named_imports(output):
    """Read the ordinary named imports printed by GNU objdump on the pinned DLLs."""
    imports = {}
    table = output.partition("The Import Tables (")[2].partition("The Export Tables (")[
        0
    ]
    for block in table.split("DLL Name: ")[1:]:
        provider, _, rows = block.partition("\n")
        symbols = imports.setdefault(provider.strip(), [])
        for row in rows.splitlines()[1:]:
            if not row.strip():
                break
            entry = re.fullmatch(
                r"\s*[0-9a-fA-F]+\s+<none>\s+[0-9a-fA-F]+\s+(\S+)\s*", row
            )
            if not entry:
                raise ValueError(f"unrecognized import in {provider}: {row}")
            symbols.append(entry[1])
    return imports


def export_entries(output):
    """Read export identities and forwarding destinations from GNU objdump."""
    addresses = {}
    expected = Counter()
    named = set()
    base = None
    section = None
    for line in output.splitlines():
        if line.startswith("Export Address Table -- Ordinal Base "):
            base = int(line.split()[-1])
            section = "addresses"
        elif line.startswith("[Ordinal/Name Pointer] Table"):
            section = "names"
        elif section == "addresses":
            entry = re.match(r"\s*\[\s*\d+\] \+base\[\s*(\d+)\] ([0-9a-fA-F]+) ", line)
            if entry and int(entry[2], 16):
                _, forwarded, target = line.partition("Forwarder RVA -- ")
                addresses[int(entry[1])] = target.strip() if forwarded else ""
        elif section == "names":
            if not line.strip():
                break
            entry = re.match(r"\s*\[\s*(\d+)\] (?:\+base\[\s*\d+\]\s+\w+ )?(.+)$", line)
            if entry and base is not None:
                ordinal = base + int(entry[1])
                named.add(ordinal)
                expected[entry[2], ordinal, addresses.get(ordinal, "")] += 1
    if section != "names" or not addresses or not named <= addresses.keys():
        raise ValueError("invalid COFF export tables")
    for ordinal in addresses.keys() - named:
        expected["", ordinal, addresses[ordinal]] += 1
    return expected


def audit(path, reader):
    output = subprocess.check_output([reader, "-p", path], text=True, errors="replace")
    expected = export_entries(output)
    exports, observed_imports = pe_loader_surface(path)
    observed = Counter(exports)
    imports = named_imports(output)
    if {dll: Counter(names) for dll, names in imports.items()} != {
        dll: Counter(names) for dll, names in observed_imports.items()
    }:
        raise ValueError(f"ordinary imports differ in {path}")
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "sha256": digest,
        "exports": expected.total(),
        "named_imports": {dll: len(names) for dll, names in imports.items()},
        "forwarded": sum(count for (_, _, target), count in expected.items() if target),
        "missing": sorted((expected - observed).elements()),
        "unexpected": sorted((observed - expected).elements()),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dll", nargs="+", type=Path)
    parser.add_argument("--reader", default="objdump")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    results = {}
    for dll in args.dll:
        key = f"{dll.parent.name}/{dll.name}"
        if key in results:
            raise ValueError(f"duplicate release/DLL key {key}")
        results[key] = audit(dll, args.reader)
    args.out.write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    raise SystemExit(
        any(row["missing"] or row["unexpected"] for row in results.values())
    )
