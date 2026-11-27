# SPDX-License-Identifier: Apache-2.0
"""Compare oneDAL Windows DLL export names and ordinals with llvm-readobj."""

import argparse
import hashlib
import json
import re
import subprocess
from collections import Counter
from pathlib import Path

from abiforge.checkers.abicheck import pe_export_names


def audit(path, reader):
    output = subprocess.check_output([reader, "--coff-exports", path], text=True)
    expected = Counter()
    for block in re.findall(r"^Export \{\n(.*?)^\}", output, re.MULTILINE | re.DOTALL):
        ordinal = re.search(r"^  Ordinal: (\d+)$", block, re.MULTILINE)
        name = re.search(r"^  Name: (.*)$", block, re.MULTILINE)
        if not ordinal or not name:
            raise ValueError(f"invalid COFF export record in {path}")
        expected[name[1], int(ordinal[1])] += 1
    if not expected:
        raise ValueError(f"no COFF export records in {path}")
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
    parser.add_argument("--reader", default="llvm-readobj")
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
