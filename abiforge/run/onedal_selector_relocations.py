# SPDX-License-Identifier: Apache-2.0
"""Compare parameter selector exports with named dynamic relocations in the ELF frontends."""

import argparse
import json
import re
import subprocess
from pathlib import Path

from abiforge.checkers.abicheck import elf_release_surface

from .onedal_covariance_parameters import CAMPAIGNS
from .onedal_export_surface import symbol_names, validate_release_labels


def image(prefix, stem):
    paths = {p.resolve() for p in (prefix / "lib").glob(f"{stem}.so.*")}
    if len(paths) != 1:
        raise ValueError(f"expected one {stem} image in {prefix}")
    return paths.pop()


def cpu_selectors(prefix):
    exports = symbol_names(image(prefix, "libonedal_parameters"))
    selectors = set()
    for family, (pattern, count) in CAMPAIGNS.items():
        names = {
            name for name in exports if re.search(pattern, name) and "EEclE" in name
        }
        if len(names) != count or names & selectors:
            raise ValueError(
                f"inconsistent selector inventory for {family} in {prefix}"
            )
        selectors.update(names)
    return selectors


def audit(prefix, device="cpu"):
    if device == "cpu":
        selectors = cpu_selectors(prefix)
    elif device == "gpu":
        exports = symbol_names(image(prefix, "libonedal_parameters_dpc"))
        selectors = {s for s in exports if "_parameters_gpuI" in s and "EEclE" in s}
        if not selectors:
            raise ValueError(f"missing GPU parameter selectors in {prefix}")
    else:
        raise ValueError(f"unknown selector device: {device}")
    result = {}
    for frontend in ("libonedal", "libonedal_dpc"):
        path = image(prefix, frontend)
        output = subprocess.check_output(["readelf", "-rDW", path], text=True)
        relocated = {}
        for line in output.splitlines():
            fields = line.split()
            if len(fields) >= 5 and fields[4] in selectors:
                relocated.setdefault(fields[4], set()).add(fields[2])
        imported = {entry[0] for entry in elf_release_surface(path).imports}
        if imported & selectors != relocated.keys():
            raise ValueError(
                f"{device} selector imports differ from named relocations: {path}"
            )
        result[path.name] = {
            "relocation_types_by_selector": {
                s: sorted(relocated[s]) for s in sorted(relocated)
            },
            "selectors_without_named_relocation": sorted(selectors - relocated.keys()),
        }
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefixes", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--device", choices=("cpu", "gpu"), default="cpu")
    args = parser.parse_args()
    validate_release_labels(args.prefixes)
    report = {prefix.name: audit(prefix, args.device) for prefix in args.prefixes}
    args.out.write_text(json.dumps(report, indent=2) + "\n")
