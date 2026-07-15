#!/usr/bin/env python3
"""Synchronize public JSON Schema assets from the package into MkDocs docs.

The package copy is the source of truth.  MkDocs publishes ``docs/schemas/v1``
verbatim at stable, versioned URLs matching each schema's ``$id``.
"""
from __future__ import annotations

import argparse
import filecmp
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "abicheck" / "schemas"
DESTINATION = ROOT / "docs" / "schemas" / "v1"


def schema_files() -> list[Path]:
    return sorted(SOURCE.glob("*.schema.json"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if published copies are stale")
    args = parser.parse_args()
    stale = [source.name for source in schema_files() if not (DESTINATION / source.name).is_file()
             or not filecmp.cmp(source, DESTINATION / source.name, shallow=False)]
    if args.check:
        if stale:
            print("Published schema copies are stale: " + ", ".join(stale))
            return 1
        print("Published schema copies are current.")
        return 0
    DESTINATION.mkdir(parents=True, exist_ok=True)
    for source in schema_files():
        shutil.copyfile(source, DESTINATION / source.name)
    print(f"Published {len(schema_files())} schema file(s) to {DESTINATION.relative_to(ROOT)}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
