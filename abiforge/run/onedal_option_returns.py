# SPDX-License-Identifier: Apache-2.0
"""Run the SDK's named result-option getters from one client compiled against OLD."""

import argparse
import csv
import os
import re
import subprocess
from pathlib import Path

from .onedal_release_matrix import obligations


def run(old, minor, major, out):
    rows = sorted(
        (
            r
            for r in obligations()
            if r["kind"] == "by_value_return"
            and r["detail"] in ("result_option_id", "optional_result_id")
        ),
        key=lambda r: r["symbol"],
    )
    out.mkdir(parents=True, exist_ok=True)
    demangled = subprocess.check_output(
        ["c++filt"], input="".join(r["symbol"] + "\n" for r in rows), text=True
    ).splitlines()
    source = "".join(f'#include "{h}"\n' for h in sorted({r["header"] for r in rows}))
    source += "#include <cstdio>\nbool modern_returns();\nint main() {\n"
    for expression in demangled:
        if not re.fullmatch(r"oneapi::dal::[\w:]+::get_\w+_id\(\)", expression):
            raise ValueError(f"unexpected option getter: {expression}")
        source += f'  std::printf("%llu\\n", static_cast<unsigned long long>({expression}.get_mask()));\n'
    src = out / "client.cpp"
    src.write_text(source + "return modern_returns() ? 0 : 65; }\n")
    client = out / "client"
    providers = sorted({p for r in rows for p in r["providers"].split(",")})
    subprocess.run(
        [
            "g++",
            "-std=c++17",
            f"-I{old}/include",
            src,
            Path(__file__).resolve().parents[1] / "fixtures/onedal/modern_returns.cpp",
            "-Wl,--no-as-needed",
            *(old / "lib" / p for p in providers),
            old / "lib/libonedal_parameters.so.3",
            old / "lib/libonedal_core.so.3",
            old / "lib/libonedal_thread.so.3",
            f"-Wl,-rpath-link,{old}/lib",
            "-o",
            client,
        ],
        check=True,
    )
    imported = {
        line.split()[0].split("@", 1)[0]
        for line in subprocess.check_output(
            ["nm", "-D", "-u", "-P", client], text=True
        ).splitlines()
    }
    expected = {r["symbol"] for r in rows} | {
        r["symbol"]
        for r in obligations()
        if r["kind"] == "by_value_return"
        and r["detail"] in ("heterogen_table", "std::exception_ptr")
    }
    if not expected <= imported:
        raise ValueError("compiler elided an expected option getter")
    results = []
    for prefix in (old, minor, major):
        env = {
            **os.environ,
            "LD_LIBRARY_PATH": str(prefix / "lib"),
            "LD_BIND_NOW": "1",
            "LC_ALL": "C",
        }
        env.pop("LD_PRELOAD", None)
        p = subprocess.run([client], env=env, capture_output=True, text=True)
        if prefix == major:
            if (
                p.returncode != 127
                or "cannot open shared object file" not in p.stderr
                or "libonedal" not in p.stderr
            ):
                raise ValueError(f"unexpected major loader result: {p.stderr}")
            continue
        p.check_returncode()
        masks = [int(s) for s in p.stdout.splitlines()]
        if len(masks) != len(rows) or any(n <= 0 or n & (n - 1) for n in masks):
            raise ValueError(f"invalid option masks in {prefix}")
        results.append(masks)
    if results[0] != results[1]:
        raise ValueError("released option mask changed")
    with (out / "option-returns.csv").open("w", newline="") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(["symbol", old.name, minor.name])
        writer.writerows(zip((r["symbol"] for r in rows), *results, strict=True))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("old", "minor", "major", "out"):
        parser.add_argument(arg, type=Path)
    args = parser.parse_args()
    run(args.old, args.minor, args.major, args.out)
