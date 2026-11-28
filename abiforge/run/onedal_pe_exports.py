# SPDX-License-Identifier: Apache-2.0
"""Compare oneDAL Windows DLL export names and ordinals with objdump."""

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

from abiforge.checkers.abicheck import pe_export_names


def audit(path, reader):
    output = subprocess.check_output([reader, "-p", path], text=True, errors="replace")
    addresses = set()
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
                addresses.add(int(entry[1]))
        elif section == "names":
            if not line.strip():
                break
            entry = re.match(r"\s*\[\s*(\d+)\] (?:\+base\[\s*\d+\]\s+\w+ )?(.+)$", line)
            if entry and base is not None:
                ordinal = base + int(entry[1])
                named.add(ordinal)
                expected[entry[2], ordinal] += 1
    if section != "names" or not addresses or not named <= addresses:
        raise ValueError(f"invalid COFF export tables in {path}")
    for ordinal in addresses - named:
        expected["", ordinal] += 1
    observed = Counter(pe_export_names(path))
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {
        "sha256": digest,
        "exports": expected.total(),
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
