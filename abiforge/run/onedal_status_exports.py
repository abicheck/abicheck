# SPDX-License-Identifier: Apache-2.0
"""Verify L2 affected-export attribution using the SDK return-type projection and real ELF."""

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from abiforge.checkers.abicheck import record_return_exports

from .onedal_exports import symbols
from .onedal_release_matrix import PRODUCT, obligations


def sdk_member_declarations(names):
    # The Itanium names of these function template specializations encode their
    # return and parameter types. The SDK compiler type-checks every declaration.
    declarations = subprocess.check_output(["c++filt", *names], text=True).splitlines()
    return "".join(f"extern template {declaration};\n" for declaration in declarations)


def run(prefix, campaign, out, *, original_headers=False, backend="clang"):
    expected = sorted(
        {
            r["symbol"]
            for r in obligations()
            if r["kind"] == "by_value_return" and r["detail"] == "services::Status"
        }
    )
    members = []
    if original_headers:
        members = json.loads((PRODUCT / "status-member-templates.json").read_text())[
            "symbols"
        ]
    binary = prefix / "lib/libonedal_core.so.3"
    env = {**os.environ, "ABICHECK_AST_FRONTEND": backend}
    cli = [sys.executable, "-m", "abicheck"]
    # This projection tests return-slot attribution only. Executable clients
    # use the unmodified SDK declarations and exercise the actual call shapes.
    for name in ("old", "mutant"):
        d = out / name
        d.mkdir(parents=True, exist_ok=True)
        overlay = campaign / name
        api = d / "returns.hpp"
        if original_headers:
            include = d / "include"
            shutil.copytree(prefix / "include", include, dirs_exist_ok=True)
            relative = Path("dal/services/error_handling.h")
            shutil.copyfile(overlay / relative, include / relative)
            headers = sorted(
                {
                    r["header"]
                    for r in obligations()
                    if r["kind"] == "by_value_return"
                    and r["detail"] == "services::Status"
                }
            )
            api.write_text(
                '#include "daal.h"\n'
                + "".join(f'#include "{h}"\n' for h in headers)
                + sdk_member_declarations(members)
            )
            includes = [include / "dal", include]
        else:
            api.write_text(
                '#include "services/error_handling.h"\n'
                + "".join(
                    f'daal::services::interface1::Status projection_{i}() asm("{symbol}");\n'
                    for i, symbol in enumerate(expected)
                )
            )
            includes = [overlay / "dal", prefix / "include/dal", prefix / "include"]
        subprocess.run(
            [
                *cli,
                "dump",
                binary,
                "-H",
                api,
                *(arg for path in includes for arg in ("-I", path)),
                "-o",
                d / "snapshot.json.gz",
            ],
            check=True,
            env=env,
        )
    verify_snapshots(binary, out, expected, original_headers, members)


def verify_snapshots(binary, out, expected, original_headers, members):
    cli = [sys.executable, "-m", "abicheck"]
    expected_by_side = {}
    for name in ("old", "mutant"):
        expected_by_side[name] = set(expected)
        if original_headers:
            exports = symbols(binary)
            declared = record_return_exports(
                out / name / "snapshot.json.gz",
                exports,
                "daal::services::interface1::Status",
            )
            if set(members) - declared:
                raise ValueError(
                    f"{name}: missing compiler-witnessed member specializations"
                )
            if not expected_by_side[name] <= declared:
                raise ValueError(f"{name}: missing ledger Status declarations")
            expected_by_side[name] = declared
    receipt = {}
    observed = {}
    for left, right in (("old", "mutant"), ("mutant", "old"), ("old", "old")):
        path = out / f"{left}-{right}.json"
        result = subprocess.run(
            [
                *cli,
                "compare",
                out / left / "snapshot.json.gz",
                out / right / "snapshot.json.gz",
                "-o",
                f"json={path}",
            ],
        )
        report = json.loads(path.read_text())
        changes = [
            c
            for c in report["changes"]
            if c["kind"] == "struct_return_convention_changed"
            and c["symbol"] == "Status"
        ]
        if left == right:
            if result.returncode or report["verdict"] != "NO_CHANGE" or changes:
                raise ValueError("unchanged release projection diverged")
        elif (
            result.returncode != 4
            or len(changes) != 1
            or set(changes[0].get("affected_symbols", [])) != expected_by_side[left]
        ):
            raise ValueError(
                f"{left}-{right}: L2 affected exports differ from the SDK ledger"
            )
        observed[left, right] = (
            set(changes[0]["affected_symbols"]) if changes else set()
        )
        receipt[f"{left}-{right}"] = {
            "verdict": report["verdict"],
            "affected_exports": len(observed[left, right]),
        }
        if original_headers:
            receipt[f"{left}-{right}"]["ledger_exports"] = len(
                observed[left, right].intersection(expected)
            )
            if left != right:
                receipt[f"{left}-{right}"]["symbols_sha256"] = hashlib.sha256(
                    "\n".join(sorted(observed[left, right])).encode()
                ).hexdigest()
    write_status_rows(out, observed)
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")


def write_status_rows(out, observed):
    rows = list(obligations())
    virtuals = {r["key"] for r in rows if r["kind"] == "virtual_slot"}
    with (out / "status-impact.csv").open("w", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(
            ["symbol", "provider", "sdk_declaration", "forward", "reverse", "control"]
        )
        for r in sorted(rows, key=lambda row: row["symbol"]):
            if r["kind"] == "by_value_return" and r["detail"] == "services::Status":
                writer.writerow(
                    [
                        r["symbol"],
                        r["providers"],
                        "virtual" if r["key"] in virtuals else "nonvirtual",
                        *(
                            int(r["symbol"] in observed[pair])
                            for pair in (
                                ("old", "mutant"),
                                ("mutant", "old"),
                                ("old", "old"),
                            )
                        ),
                    ]
                )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("prefix", "campaign", "out"):
        parser.add_argument(arg, type=Path)
    parser.add_argument("--original-headers", action="store_true")
    parser.add_argument("--backend", choices=("clang", "castxml"), default="clang")
    args = parser.parse_args()
    run(
        args.prefix,
        args.campaign,
        args.out,
        original_headers=args.original_headers,
        backend=args.backend,
    )
