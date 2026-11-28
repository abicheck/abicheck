# SPDX-License-Identifier: Apache-2.0
"""Measure frontend references introduced by including a public algorithm header."""

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

from .onedal_export_surface import symbol_names, validate_release_labels
from .onedal_selector_relocations import image

HEADERS = (
    "covariance",
    "decision_forest",
    "linear_regression",
    "logistic_regression",
    "pca",
)


def run(prefixes, work, compiler="g++", optimization="0", all_headers=False):
    validate_release_labels(prefixes)
    providers = {sdk: symbol_names(image(sdk, "libonedal")) for sdk in prefixes}
    result = {}
    for sdk in prefixes:
        rows = {}
        headers = HEADERS
        if all_headers:
            root = sdk / "include/oneapi/dal/algo"
            headers = sorted(p.stem for p in root.glob("*.hpp"))
            if not headers:
                raise ValueError(f"no algorithm headers found in {root}")
        for header in ("", *headers):
            include = f"#include <oneapi/dal/algo/{header}.hpp>\n" if header else ""
            obj = work / "client.o"
            compiled = subprocess.run(
                [
                    compiler,
                    f"-O{optimization}",
                    "-std=c++17",
                    f"-I{sdk}/include",
                    f"-I{sdk}/include/dal",
                    "-x",
                    "c++",
                    "-c",
                    "-",
                    "-o",
                    obj,
                ],
                input=include + "int main() { return 0; }\n",
                text=True,
                capture_output=True,
            )
            if compiled.returncode:
                if not all_headers or not header:
                    raise ValueError(
                        f"failed to compile {header!r} in {sdk}: {compiled.stderr}"
                    )
                rows[header] = {
                    "compile_exit": compiled.returncode,
                    "diagnostic": compiled.stderr,
                }
                continue
            output = subprocess.check_output(
                ["nm", "-P", "--undefined-only", obj], text=True
            )
            strong = {
                line.split()[0]
                for line in output.splitlines()
                if line.split()[1] == "U"
            }
            required = strong & providers[sdk]
            rows[header or "empty"] = {
                "frontend_references": sorted(required),
                "missing_by_release": {
                    p.name: sorted(required - names) for p, names in providers.items()
                },
            }
        result[sdk.name] = rows
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefixes", nargs="+", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--compiler", default="g++")
    parser.add_argument("--optimization", choices=("0", "2"), default="0")
    parser.add_argument("--all-headers", action="store_true")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        report = run(
            args.prefixes, Path(tmp), args.compiler, args.optimization, args.all_headers
        )
    args.out.write_text(json.dumps(report, indent=2) + "\n")
