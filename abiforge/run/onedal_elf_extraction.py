# SPDX-License-Identifier: Apache-2.0
"""Compare abicheck's ELF reader against binutils for every shipped oneDAL DSO."""

import argparse
import json
from pathlib import Path

from abicheck.elf_metadata import parse_elf_metadata

from .onedal_export_surface import surface


def audit(prefix):
    providers = surface(prefix)
    result = {}
    for path in sorted({p.resolve() for p in (prefix / "lib").glob("libonedal*.so*")}):
        metadata = parse_elf_metadata(path)
        expected = providers[metadata.soname]
        actual = set()
        for symbol in metadata.symbols:
            name = symbol.name
            if symbol.version:
                name += ("@@" if symbol.is_default else "@") + symbol.version
            actual.add(name)
        result[metadata.soname] = {
            "exports": len(expected),
            "missing": sorted(expected - actual),
            "unexpected": sorted(actual - expected),
        }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefix", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    reports = {prefix.name: audit(prefix) for prefix in args.prefix}
    args.out.write_text(json.dumps(reports, indent=2, sort_keys=True) + "\n")
    raise SystemExit(
        any(
            row["missing"] or row["unexpected"]
            for r in reports.values()
            for row in r.values()
        )
    )
