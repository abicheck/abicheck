# SPDX-License-Identifier: Apache-2.0
"""Link independent old-client relocations to names removed from the real DPC DSO."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .onedal_export_surface import symbol_names

DPC = "libonedal_dpc.so.3"


def run(old, new, out):
    out.mkdir(parents=True, exist_ok=True)
    baseline = symbol_names(old / "lib" / DPC)
    candidate = symbol_names(new / "lib" / DPC)
    rows = []
    for index, name in enumerate(sorted(baseline - candidate)):
        if not name.isidentifier() or not name.isascii():
            raise ValueError(f"unsupported assembler symbol: {name}")
        source = out / f"ref{index}.c"
        source.write_text(
            f'extern void target(void) asm("{name}");\n'
            "void (*volatile ref)(void) = target;\nint main(void) {return ref == 0;}\n"
        )
        client = out / f"ref{index}"
        providers = [
            DPC,
            "libonedal_parameters_dpc.so.3",
            "libonedal_core.so.3",
            "libonedal_thread.so.3",
        ]
        subprocess.run(
            [
                "gcc",
                "-fPIC",
                "-pie",
                source,
                "-Wl,--no-as-needed",
                *(old / "lib" / p for p in providers),
                "-Wl,--allow-shlib-undefined",
                "-o",
                client,
            ],
            check=True,
        )
        if name not in symbol_names(client, imports=True):
            raise ValueError(f"missing client relocation: {name}")
        exits = {}
        for sdk in (old, new):
            env = {
                **os.environ,
                "LC_ALL": "C",
                "LD_LIBRARY_PATH": str(sdk / "lib")
                + os.pathsep
                + os.environ.get("LD_LIBRARY_PATH", ""),
            }
            trace = subprocess.check_output(["ldd", client], env=env, text=True)
            if any(f"{p} => {sdk / 'lib' / p} (" not in trace for p in providers):
                raise ValueError("unexpected release provider")
            result = subprocess.run([client], env=env, capture_output=True, text=True)
            if (
                sdk == old
                and result.returncode != 0
                or sdk == new
                and (
                    result.returncode != 127
                    or f"undefined symbol: {name}" not in result.stderr
                )
            ):
                raise ValueError(f"{sdk.name}: {result.stderr}")
            exits[sdk.name] = result.returncode
        rows.append({"symbol": name, "exits": exits})
    report = out / "compare.json"
    compared = subprocess.run(
        [
            sys.executable,
            "-m",
            "abicheck",
            "compare",
            old / "lib" / DPC,
            new / "lib" / DPC,
            "-o",
            f"json={report}",
        ]
    )
    data = json.loads(report.read_text())
    detected = {
        change["symbol"]
        for change in data["changes"]
        if change["kind"] in ("func_removed", "func_removed_elf_only")
    }
    if (
        compared.returncode != 4
        or data["verdict"] != "BREAKING"
        or detected != baseline - candidate
    ):
        raise ValueError("ELF removal findings differ from the frozen loader witnesses")
    for row in rows:
        row["detected_removal"] = row["symbol"] in detected
    (out / "receipt.json").write_text(json.dumps(rows, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("old", "new", "out"):
        parser.add_argument(arg, type=Path)
    args = parser.parse_args()
    run(args.old.resolve(), args.new.resolve(), args.out.resolve())
