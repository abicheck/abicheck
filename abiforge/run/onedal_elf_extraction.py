# SPDX-License-Identifier: Apache-2.0
"""Compare abicheck's ELF reader against binutils for every shipped oneDAL DSO."""

import argparse
import json
from pathlib import Path

from abiforge.checkers.abicheck import elf_export_names

from .onedal_export_surface import surface, validate_release_labels


def audit(prefix):
    providers = surface(prefix)
    result = {}
    for path in sorted({p.resolve() for p in (prefix / "lib").glob("libonedal*.so*")}):
        soname, actual = elf_export_names(path)
        expected = providers[soname]
        result[soname] = {
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
    validate_release_labels(args.prefix)
    reports = {prefix.name: audit(prefix) for prefix in args.prefix}
    args.out.write_text(json.dumps(reports, indent=2, sort_keys=True) + "\n")
    raise SystemExit(
        any(
            row["missing"] or row["unexpected"]
            for r in reports.values()
            for row in r.values()
        )
    )
