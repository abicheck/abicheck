# SPDX-License-Identifier: Apache-2.0
"""Build old oneDAL device consumers once and run both CPU OpenCL release images."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path


def run(old, new, compiler, out):
    out.mkdir(parents=True, exist_ok=True)
    data = out / "data.csv"
    data.write_text("7,8\n")
    probes = Path(__file__).resolve().parents[2] / "research/onedal-sycl-usm"
    results = []
    for name in ("event", "buffer", "csr", "ownership"):
        client = out / name
        subprocess.run(
            [
                compiler,
                "-std=c++17",
                "-fsycl",
                "-DONEDAL_DATA_PARALLEL",
                f"-I{old}/include",
                probes / f"{name}_consumer.cpp",
                f"-L{old}/lib",
                "-Wl,--no-as-needed",
                *(
                    f"-l:libonedal{x}.so.3"
                    for x in ("_dpc", "_parameters_dpc", "_core", "_thread")
                ),
                "-o",
                client,
            ],
            check=True,
        )
        for prefix in (old, new):
            env = {
                **os.environ,
                "ONEAPI_DEVICE_SELECTOR": "opencl:cpu",
                "LD_LIBRARY_PATH": os.pathsep.join(
                    [str(prefix / "lib"), os.environ.get("LD_LIBRARY_PATH", "")]
                ),
            }
            result = subprocess.run(
                [client, data], env=env, capture_output=True, text=True
            )
            matches = result.returncode == 0
            if name == "event":
                matches &= set(result.stdout.splitlines()) == {
                    f"{allocation} 256/256"
                    for allocation in ("shared", "host", "device")
                }
            results.append(
                dict(
                    client=name,
                    package=prefix.name,
                    exit=result.returncode,
                    stdout=result.stdout.strip(),
                    stderr=result.stderr.strip(),
                    matches=matches,
                )
            )
    (out / "receipt.json").write_text(json.dumps(results, indent=2) + "\n")
    return all(r["matches"] for r in results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("old", "new", "compiler", "out"):
        parser.add_argument(arg, type=Path)
    args = parser.parse_args()
    raise SystemExit(
        not run(
            args.old.resolve(),
            args.new.resolve(),
            args.compiler.resolve(),
            args.out.resolve(),
        )
    )
