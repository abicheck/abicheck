# SPDX-License-Identifier: Apache-2.0
"""Project concrete SDK SharedPtr return types into independently compiled caller/provider probes."""

import argparse
import csv
import os
import subprocess
from collections import Counter
from pathlib import Path

from .onedal_release_matrix import PRODUCT


def run(old, minor, out):
    with (PRODUCT / "return-lowering.csv").open() as f:
        types = Counter(
            r["sdk_type"]
            for r in csv.DictReader(f)
            if r["sdk_type"].startswith("daal::services::interface1::SharedPtr<")
        )
    out.mkdir(parents=True, exist_ok=True)
    (out / "return_types.inc").write_text(
        "".join(f"PROBE({i}, {t})\n" for i, t in enumerate(sorted(types)))
    )
    src = (
        Path(__file__).resolve().parents[1] / "fixtures/onedal/shared_return_types.cpp"
    )
    client = out / "client"
    results = []
    for prefix in (old, minor):
        d = out / prefix.name
        d.mkdir(exist_ok=True)
        flags = [
            "g++",
            "-std=c++17",
            "-Wno-deprecated-declarations",
            "-I",
            out,
            f"-I{prefix}/include",
            f"-I{prefix}/include/dal",
        ]
        libs = [
            prefix / "lib/libonedal_core.so.3",
            prefix / "lib/libonedal_thread.so.3",
            f"-Wl,-rpath-link,{prefix}/lib",
        ]
        subprocess.run(
            [
                *flags,
                "-DPROVIDER",
                "-shared",
                "-fPIC",
                "-s",
                src,
                *libs,
                "-o",
                d / "libprobe.so",
            ],
            check=True,
        )
        if prefix == old:
            subprocess.run(
                [*flags, src, "-L", d, "-lprobe", *libs, "-o", client], check=True
            )
        env = {**os.environ, "LD_LIBRARY_PATH": f"{d}:{prefix}/lib"}
        env.pop("LD_PRELOAD", None)
        for i, t in enumerate(sorted(types)):
            exits = [
                subprocess.run([client, str(i), str(mode)], env=env).returncode
                for mode in range(3)
            ]
            if exits != [0, 11, 12]:
                raise ValueError(f"{prefix}/{t}: {exits}")
            results.append([prefix.name, t, types[t], *exits])
    with (out / "shared-return-types.csv").open("w", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(
            [
                "release",
                "sdk_type",
                "return_symbols",
                "control",
                "borrow",
                "extra_owner",
            ]
        )
        writer.writerows(results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("old", "minor", "out"):
        parser.add_argument(arg, type=Path)
    args = parser.parse_args()
    run(args.old, args.minor, args.out)
