# SPDX-License-Identifier: Apache-2.0
"""Link one strong ELF reference per observed CPU frontend symbol and release."""

import argparse
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from .onedal_export_surface import validate_release_labels
from .onedal_selector_relocations import image


def audit(matrix, prefixes, work):
    validate_release_labels(prefixes)
    if set(matrix) != {prefix.name for prefix in prefixes}:
        raise ValueError("load matrix and package releases differ")
    names = set()
    absent = {prefix.name: set() for prefix in prefixes}
    for campaigns in matrix.values():
        for client in campaigns.values():
            names.update(name for _, name in client["direct_onedal_bindings"])
            for prefix in prefixes:
                absent[prefix.name].update(
                    client["missing_frontend_imports_by_candidate"][prefix.name]
                )
    if not names:
        raise ValueError("empty direct-binding inventory")
    providers = {p.name: image(p, "libonedal") for p in prefixes}
    rows = {release: {} for release in providers}
    obj = work / "reference.o"
    for name in sorted(names):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"unsupported symbol spelling: {name}")
        source = f"extern void {name}(void);\nvoid (*reference)(void) = {name};\n"
        subprocess.run(
            ["gcc", "-x", "c", "-fPIC", "-c", "-", "-o", obj],
            input=source,
            text=True,
            check=True,
        )
        for release, provider in providers.items():
            result = subprocess.run(
                [
                    "ld",
                    "-shared",
                    "-z",
                    "defs",
                    "--no-demangle",
                    "-o",
                    work / "reference.so",
                    obj,
                    provider,
                ],
                env={**os.environ, "LC_ALL": "C"},
                capture_output=True,
                text=True,
            )
            if name in absent[release]:
                if (
                    result.returncode == 0
                    or f"undefined reference to `{name}'" not in result.stderr
                ):
                    raise ValueError(
                        f"expected unresolved {name} in {provider}: {result.stderr}"
                    )
            elif result.returncode:
                raise ValueError(
                    f"unexpected link failure for {name} in {provider}: {result.stderr}"
                )
            rows[release][name] = result.returncode
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("matrix", type=Path)
    parser.add_argument("prefixes", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        report = audit(json.loads(args.matrix.read_text()), args.prefixes, Path(tmp))
    args.out.write_text(json.dumps(report, indent=2) + "\n")
