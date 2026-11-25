# SPDX-License-Identifier: Apache-2.0
"""Observe the oneDAL Status hidden return pointer on SysV AMD64 with old callers."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

CLIENT = Path(__file__).resolve().parents[1] / "fixtures/onedal/status_return.cpp"


def run(prefix, out):
    out.mkdir(parents=True, exist_ok=True)
    source = prefix / "include/dal/services/error_handling.h"
    original = source.read_text()
    mutant = original
    for declaration in ("Status(const Status & other);", "~Status();"):
        if mutant.count(declaration) != 1:
            raise ValueError(f"expected exactly one {declaration}")
        mutant = mutant.replace(declaration, declaration[:-1] + " = default;")
    lib = prefix / "lib"
    flags = ["-std=c++17", f"-I{prefix}/include", f"-I{prefix}/include/dal"]
    client = out / "client"
    subprocess.run(
        [
            "g++",
            *flags,
            CLIENT,
            lib / "libonedal_core.so.3",
            lib / "libonedal_thread.so.3",
            f"-Wl,-rpath-link,{lib}",
            "-rdynamic",
            "-o",
            client,
        ],
        check=True,
    )
    env = {**os.environ, "LD_LIBRARY_PATH": str(lib), "ABICHECK_AST_FRONTEND": "clang"}
    env.pop("LD_PRELOAD", None)
    exits = {
        mode: {"release": subprocess.run([client, mode], env=env).returncode}
        for mode in ("compute", "table", "virtual")
    }
    cli = [sys.executable, "-m", "abicheck"]
    for name, header in (
        ("old", original),
        ("control", original + "\n"),
        ("mutant", mutant),
    ):
        d = out / name
        overlay = d / "dal/services"
        overlay.mkdir(parents=True, exist_ok=True)
        (overlay / "error_handling.h").write_text(header)
        so = d / "observer.so"
        subprocess.run(
            [
                "g++",
                "-I",
                d,
                "-I",
                d / "dal",
                *flags,
                "-DONEDAL_OBSERVER",
                "-shared",
                "-fPIC",
                "-s",
                CLIENT,
                "-o",
                so,
            ],
            check=True,
        )
        for mode in exits:
            exits[mode][name] = subprocess.run(
                [client, mode], env={**env, "LD_PRELOAD": str(so)}
            ).returncode
        # The adapter exports the product method names. Supply the SDK's
        # return type and two public call declarations without all of daal.h.
        api = d / "api.hpp"
        api.write_text(
            '#include "services/error_handling.h"\nnamespace dm = daal::services::interface1;\n'
            'dm::Status observe_table(void*) asm("_ZN4daal15data_management10interface112NumericTable23allocateBasicStatisticsEv");\n'
            'dm::Status observe_check(const void*,const void*,int) asm("_ZNK4daal10algorithms10covariance10interface15Input5checkEPKNS0_10interface19ParameterEi");\n'
            'dm::Status observe_compute(void*) asm("_ZN4daal10algorithms10interface113AlgorithmImplILNS_11ComputeModeE1EE14computeNoThrowEv");\n'
        )
        subprocess.run(
            [
                *cli,
                "dump",
                so,
                "-H",
                api,
                "-I",
                d,
                "-I",
                d / "dal",
                "-I",
                prefix / "include",
                "-I",
                prefix / "include/dal",
                "-o",
                d / "snapshot.json.gz",
            ],
            env=env,
            check=True,
        )
    checks = {}
    for name in ("control", "mutant"):
        d = out / name
        report = d / "compare.json"
        report.unlink(missing_ok=True)
        proc = subprocess.run(
            [
                *cli,
                "compare",
                out / "old/snapshot.json.gz",
                d / "snapshot.json.gz",
                "-o",
                f"json={report}",
            ],
            env=env,
        )
        data = json.loads(report.read_text())
        rows = [
            [c["old_value"], c["new_value"]]
            for c in data["changes"]
            if c["kind"] == "struct_return_convention_changed"
            and c["symbol"] == "Status"
        ]
        checks[name] = dict(
            exit=proc.returncode, verdict=data["verdict"], status_return=rows
        )
    receipt = {"client_exits": exits, "checker": checks}
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return (
        all(
            v == {"release": 0, "old": 0, "control": 0, "mutant": 42}
            for v in exits.values()
        )
        and checks["control"]
        == {"exit": 0, "verdict": "NO_CHANGE", "status_return": []}
        and checks["mutant"]
        == {
            "exit": 4,
            "verdict": "BREAKING",
            "status_return": [["memory (sret)", "regs(INTEGER)"]],
        }
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefix", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    raise SystemExit(not run(args.prefix.resolve(), args.out.resolve()))
