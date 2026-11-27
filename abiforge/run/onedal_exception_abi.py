# SPDX-License-Identifier: Apache-2.0
"""Exercise cross-DSO exception handling and withdraw the frozen thrower's imports."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from .onedal_export_surface import symbol_names

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/onedal"
CPU = "libonedal.so.3"


def client_result(client, image, lib):
    env = {
        **os.environ,
        "LC_ALL": "C",
        "LD_BIND_NOW": "1",
        "LD_LIBRARY_PATH": os.pathsep.join(map(str, (image, lib))),
    }
    return subprocess.run([client], env=env, text=True, capture_output=True)


def run(old, minor, work):
    lib = old / "lib"
    thrower = work / "thrower.so"
    client = work / "client"
    cxx = ["g++", "-std=c++17", "-O1", "-I", old / "include"]
    subprocess.run(
        [
            *cxx,
            "-fPIC",
            "-shared",
            FIXTURES / "exception_provider.cpp",
            lib / CPU,
            "-Wl,-soname,thrower.so",
            "-o",
            thrower,
        ],
        check=True,
    )
    subprocess.run(
        [
            *cxx,
            FIXTURES / "exception_client.cpp",
            thrower,
            "-Wl,--no-as-needed",
            lib / CPU,
            lib / "libonedal_parameters.so.3",
            lib / "libonedal_core.so.3",
            lib / "libonedal_thread.so.3",
            "-Wl,--allow-shlib-undefined",
            "-o",
            client,
        ],
        check=True,
    )
    controls = {}
    for release in (old, minor):
        result = client_result(client, work, release / "lib")
        result.check_returncode()
        controls[release.name] = result.returncode
    imports = symbol_names(thrower, imports=True)
    targets = sorted(
        s for s in imports if s.startswith(("_ZTIN6oneapi3dal", "_ZTVN6oneapi3dal"))
    )
    if not targets or not set(targets) <= symbol_names(lib / CPU):
        raise ValueError("missing oneDAL exception imports or baseline exports")
    results = []
    for name in targets:
        mapping = work / "renames"
        mapping.write_text(f"{name} abiforge_absent\n")
        candidate = work / CPU
        subprocess.run(
            [
                "patchelf",
                "--rename-dynamic-symbols",
                mapping,
                "--output",
                candidate,
                lib / CPU,
            ],
            check=True,
        )
        result = client_result(client, work, lib)
        if result.returncode != 127 or f"undefined symbol: {name}" not in result.stderr:
            raise ValueError(
                f"unexpected loader result for {name}: {result.returncode}, {result.stderr}"
            )
        report = work / "report.json"
        check = subprocess.run(
            [
                sys.executable,
                "-m",
                "abicheck",
                "compare",
                lib / CPU,
                candidate,
                "-o",
                f"json={report}",
            ]
        )
        data = json.loads(report.read_bytes())
        kinds = sorted(
            {row["kind"] for row in data["changes"] if row["symbol"] == name}
        )
        if (
            check.returncode != 4
            or data["verdict"] != "BREAKING"
            or "var_removed_elf_only" not in kinds
        ):
            raise ValueError(f"withdrawal was not attributed to {name}: {kinds}")
        results.append(
            {
                "symbol": name,
                "loader_exit": result.returncode,
                "cli_exit": check.returncode,
                "verdict": data["verdict"],
                "kinds": kinds,
            }
        )
    return {"controls": controls, "withdrawals": results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old", type=Path)
    parser.add_argument("minor", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        receipt = run(args.old.resolve(), args.minor.resolve(), Path(tmp))
    args.out.write_text(json.dumps(receipt, indent=2) + "\n")
