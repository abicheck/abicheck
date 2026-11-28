# SPDX-License-Identifier: Apache-2.0
"""Compare pinned oneDAL win-64 import libraries with their runtime DLLs."""

import argparse
import hashlib
import json
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

from abiforge.checkers.abicheck import pe_declaration_names
from abiforge.run.onedal_pe_exports import named_imports, validate_release_receipt


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def link_consumer(archive, filename, imports):
    """Force archive extraction for each import and verify the resulting PE table."""
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        response = directory / "references.rsp"
        # GNU response files recognize backslash escapes and quoted arguments.
        arguments = []
        for name in imports:
            escaped = name.replace("\\", "\\\\").replace('"', '\\"')
            arguments.append(f'-u "__imp_{escaped}"')
        response.write_text("\n".join(arguments))
        image = directory / "consumer.dll"
        subprocess.run(
            [
                "ld",
                "-m",
                "i386pep",
                "--dll",
                "-e",
                "0",
                f"@{response}",
                "-o",
                image,
                archive,
            ],
            check=True,
        )
        output = subprocess.check_output(["objdump", "-p", image], text=True)
        linked = named_imports(output)
        if set(linked) != {filename} or Counter(linked[filename]) != imports:
            raise ValueError(f"linked import table differs: {archive}")
        return len(linked[filename])


def audit(root, receipt):
    validate_release_receipt(receipt)
    results = {}
    for key, recorded in sorted(receipt.items()):
        release, filename = key.split("/")
        if filename.startswith("onedal_thread."):
            continue  # dal-devel ships import libraries for CPU, core and DPC.
        dll = root / key
        if digest(dll) != recorded["sha256"]:
            raise ValueError(f"runtime DLL digest differs: {key}")
        stem, major, _ = filename.split(".")
        directory = root / release / "Library" / "lib"
        archive = directory / f"{stem}_dll.{major}.lib"
        archive_hash = digest(archive)
        if digest(directory / f"{stem}_dll.lib") != archive_hash:
            raise ValueError(f"unversioned import archive differs: {archive}")
        members = subprocess.check_output(["ar", "t", archive], text=True).splitlines()
        if set(members) != {filename}:
            raise ValueError(f"unexpected archive member names in {archive}")
        listing = subprocess.check_output(
            ["nm", "-g", "--defined-only", archive], text=True
        )
        imports = Counter()
        code_thunks = set()
        for line in listing.splitlines():
            fields = line.split(maxsplit=2)
            if len(fields) == 3 and fields[1] == "T":
                code_thunks.add(fields[2])
            if len(fields) == 3 and fields[2].startswith("__imp_"):
                imports[fields[2].removeprefix("__imp_")] += 1
        functions, variables = pe_declaration_names(dll)
        names = Counter(functions) + Counter(variables)
        expected_code = Counter(
            {name: imports[name] for name in imports.keys() & code_thunks}
        )
        expected_data = imports - expected_code
        if not names or not imports:
            raise ValueError(f"empty DLL or import library symbol inventory: {key}")
        results[key] = {
            "archive_sha256": archive_hash,
            "dll_sha256": recorded["sha256"],
            "imports": imports.total(),
            "code_imports": sum(imports[name] for name in imports.keys() & code_thunks),
            "imports_without_code_thunks": sum(
                imports[name] for name in imports.keys() - code_thunks
            ),
            "missing_function_declarations": sorted(
                (expected_code - Counter(functions)).elements()
            ),
            "missing_variable_declarations": sorted(
                (expected_data - Counter(variables)).elements()
            ),
            "linked_imports": link_consumer(archive, filename, imports),
            "missing_from_dll": sorted((imports - names).elements()),
            "missing_from_archive": sorted((names - imports).elements()),
        }
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    results = audit(args.root, json.loads(args.receipt.read_text()))
    args.out.write_text(json.dumps(results, indent=2) + "\n")
    raise SystemExit(
        any(
            r["missing_from_archive"]
            or r["missing_from_dll"]
            or r["missing_function_declarations"]
            or r["missing_variable_declarations"]
            for r in results.values()
        )
    )
