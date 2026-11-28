# SPDX-License-Identifier: Apache-2.0
"""Check Windows thread declarations using ELF symbols and MSVC decorations."""

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

from abiforge.checkers.abicheck import pe_declaration_names
from abiforge.run.onedal_pe_exports import export_entries, validate_release_receipt


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def audit(root, receipt, undname):
    validate_release_receipt(receipt)
    results = {}
    for key, row in sorted(receipt.items()):
        release, name = key.split("/")
        if not name.startswith("onedal_thread."):
            continue
        dll = root / key
        if digest(dll) != row["sha256"]:
            raise ValueError(f"Windows DLL digest differs: {key}")
        major = name.split(".")[1]
        elf = root / release / "lib" / f"libonedal_thread.so.{major}"
        listing = subprocess.check_output(
            ["readelf", "--dyn-syms", "--wide", elf], text=True
        )
        elf_functions = set()
        for line in listing.splitlines():
            fields = line.split()
            if len(fields) >= 8 and fields[3] == "FUNC" and fields[6] != "UND":
                elf_functions.add(fields[7].partition("@")[0])
        exports = export_entries(
            subprocess.check_output(["objdump", "-p", dll], text=True)
        )
        names = {symbol for symbol, _, _ in exports}
        if len(names) != row["exports"]:
            raise ValueError(f"incomplete Windows name inventory: {key}")
        corroborated = names & elf_functions
        functions, variables = pe_declaration_names(dll)
        if (
            not corroborated
            or corroborated - set(functions)
            or corroborated.intersection(variables)
        ):
            raise ValueError(f"thread function classification differs: {key}")
        decorated = sorted(names - corroborated)
        demangled = subprocess.check_output([undname, *decorated], text=True)
        signatures = {}
        tables = {}
        for block in demangled.strip().split("\n\n"):
            symbol, signature = block.splitlines()
            if signature.endswith("::`vftable'"):
                tables[symbol] = signature
            elif " __cdecl " in signature and signature.endswith((")", ") const")):
                signatures[symbol] = signature
            else:
                raise ValueError(f"unrecognized thread declaration: {signature}")
        if (
            set(signatures) | set(tables) != set(decorated)
            or corroborated | set(signatures) != set(functions)
            or set(tables) != set(variables)
        ):
            raise ValueError(f"thread declarations differ: {key}")
        results[key] = {
            "windows_sha256": row["sha256"],
            "linux_sha256": digest(elf),
            "same_name_elf_functions": sorted(corroborated),
            "msvc_functions": signatures,
            "msvc_vftables": tables,
        }
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--undname", default="llvm-undname")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    rows = audit(args.root, json.loads(args.receipt.read_text()), args.undname)
    args.out.write_text(json.dumps(rows, indent=2) + "\n")
