# SPDX-License-Identifier: Apache-2.0
"""Build old oneDAL device consumers once and run both CPU OpenCL release images."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
from pathlib import Path


def run(old, new, compiler, out, selector="opencl:cpu", major=None, mpi=False):
    out.mkdir(parents=True, exist_ok=True)
    data = out / "data.csv"
    data.write_text("7,8\n")
    probes = Path(__file__).resolve().parents[2] / "research/onedal-sycl-usm"
    results = []
    clients = ["event", "buffer", "csr", "ownership", "dependency"]
    if mpi:
        clients.append("spmd")
    for name in clients:
        mpi_flags = []
        command = []
        if name == "spmd":
            command = ["mpiexec", "-n", "2"]
            for phase in ("compile", "link"):
                mpi_flags.extend(
                    shlex.split(
                        subprocess.check_output(
                            ["mpicxx", f"--showme:{phase}"], text=True
                        )
                    )
                )

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
                *mpi_flags,
                "-o",
                client,
            ],
            check=True,
        )
        for prefix in (old, new):
            env = {
                **os.environ,
                "ONEAPI_DEVICE_SELECTOR": selector,
                "LD_LIBRARY_PATH": os.pathsep.join(
                    [str(prefix / "lib"), os.environ.get("LD_LIBRARY_PATH", "")]
                ),
            }
            linked = subprocess.check_output(["ldd", client], env=env, text=True)
            providers = {}
            for line in linked.splitlines():
                name_so, sep, target = line.strip().partition(" => ")
                if sep and name_so.startswith("libonedal"):
                    path = Path(target.rsplit(" (", 1)[0])
                    if not path.is_file() or not path.resolve().is_relative_to(
                        (prefix / "lib").resolve()
                    ):
                        raise ValueError(
                            f"{client}: {name_so} resolved outside {prefix}: {target}"
                        )
                    providers[name_so] = str(path.resolve())
            if not providers:
                raise ValueError(f"{client}: no oneDAL providers in loader trace")
            result = subprocess.run(
                [*command, client, data], env=env, capture_output=True, text=True
            )
            matches = result.returncode == 0
            if name == "event":
                matches &= set(result.stdout.splitlines()) == {
                    f"{allocation} 256/256"
                    for allocation in ("shared", "host", "device")
                }
            if name == "dependency":
                matches &= result.stdout.strip() == "42"
            results.append(
                dict(
                    client=name,
                    package=prefix.name,
                    device_selector=selector,
                    providers=providers,
                    exit=result.returncode,
                    stdout=result.stdout.strip(),
                    stderr=result.stderr.strip(),
                    matches=matches,
                )
            )
        if major is not None:
            # The major image must stand alone. Inherited paths can contain
            # OLD/lib and silently supply the withdrawn SONAME.
            env = {**os.environ, "LC_ALL": "C", "LD_LIBRARY_PATH": str(major / "lib")}
            env.pop("LD_PRELOAD", None)
            run_major = subprocess.run(
                [client, data], env=env, capture_output=True, text=True
            )
            missing = next(
                (
                    word.rstrip(":")
                    for word in run_major.stderr.split()
                    if word.startswith("libonedal") and ".so.3" in word
                ),
                None,
            )
            results.append(
                dict(
                    client=name,
                    package=major.name,
                    device_selector=selector,
                    exit=run_major.returncode,
                    missing_soname=missing,
                    matches=run_major.returncode == 127 and missing is not None,
                )
            )
    (out / "receipt.json").write_text(json.dumps(results, indent=2) + "\n")
    return all(r["matches"] for r in results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("old", "new", "compiler", "out"):
        parser.add_argument(arg, type=Path)
    parser.add_argument(
        "--selector",
        choices=("opencl:cpu", "opencl:gpu", "level_zero:gpu"),
        default="opencl:cpu",
    )
    parser.add_argument(
        "--major",
        type=Path,
        help="also check the frozen client against a standalone major release",
    )
    parser.add_argument(
        "--mpi",
        action="store_true",
        help="also replay the two-rank Open MPI communicator client",
    )
    args = parser.parse_args()
    raise SystemExit(
        not run(
            args.old.resolve(),
            args.new.resolve(),
            args.compiler.resolve(),
            args.out.resolve(),
            args.selector,
            args.major.resolve() if args.major else None,
            mpi=args.mpi,
        )
    )
