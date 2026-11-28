# SPDX-License-Identifier: Apache-2.0
"""Check packaged Windows oneDAL imports against each pinned provider release."""

import argparse
import hashlib
import json
from pathlib import Path

from abiforge.checkers.abicheck import pe_loader_surface
from abiforge.run.onedal_pe_exports import validate_release_receipt


def bindings(root, receipt):
    validate_release_receipt(receipt)
    exports = {}
    imports = {}
    for key, row in receipt.items():
        release, dll = key.split("/")
        path = root / release / dll
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != row["sha256"]:
                raise ValueError(f"release DLL digest differs: {key}")
        symbols, dependencies = pe_loader_surface(path)
        exports.setdefault(release, {})[dll.lower()] = {name for name, _, _ in symbols}
        imports[key] = dependencies

    records = []
    for key, dependencies in sorted(imports.items()):
        for dll, names in sorted(dependencies.items()):
            if not dll.lower().startswith("onedal"):
                continue
            for release, providers in sorted(exports.items()):
                available = providers.get(dll.lower())
                records.append(
                    {
                        "consumer": key,
                        "provider": dll,
                        "provider_release": release,
                        "imports": len(names),
                        "provider_present": available is not None,
                        "missing_symbols": (
                            sorted(name for name in names if name not in available)
                            if available is not None
                            else None
                        ),
                    }
                )
    return records


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    rows = bindings(args.root, json.loads(args.receipt.read_text()))
    args.out.write_text(json.dumps(rows, indent=2) + "\n")
