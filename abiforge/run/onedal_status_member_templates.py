# SPDX-License-Identifier: Apache-2.0
"""Check the actual emitted calls to exported oneDAL Status member specializations."""

import argparse
import json
import re
import subprocess
from pathlib import Path

from .onedal_exports import symbols

SOURCE = (
    Path(__file__).resolve().parents[1] / "fixtures/onedal/status_member_templates.cpp"
)
CALL = re.compile(r"\b(?:call|invoke) (void|ptr) @([^ (]+)\((.*)")


def lowering(ir):
    result = {}
    for line in ir.splitlines():
        call = CALL.search(line)
        if not call:
            continue
        ret, symbol, operands = call.groups()
        if not (
            "HomogenNumericTable" in symbol
            and any(
                method in symbol
                for method in (
                    "getTBlockI",
                    "getTFeatureI",
                    "releaseTBlockI",
                    "releaseTFeatureI",
                )
            )
            or ("kernel_function" in symbol or "engines" in symbol)
            and "allocateIfE" in symbol
        ):
            continue
        if (
            ret == "void"
            and 'sret(%"class.daal::services::interface1::Status")'
            in operands.split(",")[0]
        ):
            result[symbol] = "sret"
        elif ret == "ptr":
            result[symbol] = "register"
        else:
            raise ValueError(f"undecided call: {line}")
    return result


def run(prefix, minor, campaign, out):
    out.mkdir(parents=True, exist_ok=True)
    sides = {}
    for variant in ("old", "mutant"):
        ir = out / f"{variant}.ll"
        subprocess.run(
            [
                "clang++",
                "-std=c++17",
                "-O0",
                "-fno-access-control",
                "-Wno-deprecated-declarations",
                "-I",
                campaign / variant / "dal",
                "-I",
                prefix / "include/dal",
                "-I",
                prefix / "include",
                "-S",
                "-emit-llvm",
                SOURCE,
                "-o",
                ir,
            ],
            check=True,
        )
        sides[variant] = lowering(ir.read_text())
    names = set(sides["old"])
    if len(names) != 38 or names != set(sides["mutant"]):
        raise ValueError("unexpected specialization set")
    for sdk in (prefix, minor):
        if names - symbols(sdk / "lib/libonedal_core.so.3"):
            raise ValueError(f"{sdk}: missing specialization export")
    if set(sides["old"].values()) != {"sret"} or set(sides["mutant"].values()) != {
        "register"
    }:
        raise ValueError("unexpected call lowering")
    receipt = {
        "old": "sret",
        "mutant": "register",
        "providers": [prefix.name, minor.name],
        "symbols": sorted(names),
    }
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("prefix", "minor", "campaign", "out"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    run(args.prefix, args.minor, args.campaign, args.out)
