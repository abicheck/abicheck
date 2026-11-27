# SPDX-License-Identifier: Apache-2.0
"""Replay every upstream Linux C++ CPU example against two installed oneDAL releases."""

import argparse
import csv
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path


def build(source, sdk, root, exe, tbb_include, tbb_library):
    legacy = "daal" in source.relative_to(root).parts
    headers = [sdk / "include", source.parents[1]]
    if legacy:
        headers += [sdk / "include/dal", source.parents[1] / "utils"]
    libraries = [
        sdk / "lib" / name
        for name in (
            "libonedal.so.3",
            "libonedal_parameters.so.3",
            "libonedal_core.so.3",
            "libonedal_thread.so.3",
        )
    ]
    if source.name == "jaccard_batch_app.cpp":
        if tbb_include:
            headers.append(tbb_include)
        libraries.append(tbb_library)
        if not tbb_library.startswith("-"):
            libraries.extend(
                ["-Xlinker", "-rpath", "-Xlinker", Path(tbb_library).resolve().parent]
            )
    subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-O1",
            *(f"-I{path}" for path in headers),
            source,
            "-Wl,--no-as-needed",
            *libraries,
            "-o",
            exe,
        ],
        check=True,
    )


def run(exe, sdk, cwd):
    result = subprocess.run(
        [exe],
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        timeout=90,
        env={**os.environ, "LD_LIBRARY_PATH": str(sdk / "lib"), "LD_BIND_NOW": "1"},
    )
    return hashlib.sha256(result.stdout).hexdigest()


def inventory(root):
    result = {}
    for family in ("oneapi", "daal"):
        directory = root / "examples" / family / "cpp/source"
        sources = sorted(directory.glob("*/*.cpp"))
        if not sources:
            raise ValueError(f"no C++ examples in {directory}")
        result[family] = sources
    return result


def measure(args, work, sources):
    for family in ("oneapi", "daal"):
        cpp = args.source / "examples" / family / "cpp"
        cwd = cpp if family == "daal" else args.source
        for source in sources[family]:
            exe = work / "client"
            build(
                source, args.old, args.source, exe, args.tbb_include, args.tbb_library
            )
            old, minor = run(exe, args.old, cwd), run(exe, args.minor, cwd)
            counts = ["", ""]
            native = ""
            if old != minor:
                counts = [
                    len({initial, run(exe, sdk, cwd), run(exe, sdk, cwd)})
                    for initial, sdk in ((old, args.old), (minor, args.minor))
                ]
                if counts == [1, 1]:
                    build(
                        source,
                        args.minor,
                        args.source,
                        exe,
                        args.tbb_include,
                        args.tbb_library,
                    )
                    native = run(exe, args.minor, cwd)
            yield [
                source.relative_to(args.source).as_posix(),
                0,
                0,
                old,
                minor,
                *counts,
                native,
            ]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source", "old", "minor"):
        parser.add_argument(name, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--tbb-include", type=Path)
    parser.add_argument("--tbb-library", default="-ltbb")
    args = parser.parse_args()
    args.source, args.old, args.minor = (
        p.resolve() for p in (args.source, args.old, args.minor)
    )
    sources = inventory(args.source)
    with tempfile.TemporaryDirectory() as tmp, args.out.open("w", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(
            [
                "source",
                "2025.9_exit",
                "2025.11_exit",
                "2025.9_stdout_sha256",
                "2025.11_stdout_sha256",
                "2025.9_distinct_outputs_in_three_runs",
                "2025.11_distinct_outputs_in_three_runs",
                "2025.11_native_rebuild_stdout_sha256",
            ]
        )
        writer.writerows(measure(args, Path(tmp), sources))
