# SPDX-License-Identifier: Apache-2.0
"""Isolate ELF relocation failures for individual oneDAL C1/C2/D0/D1/D2 and data exports."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

OWNER = "_ZN4daal10algorithms10covariance10interface15Input"
VARIABLE = "_ZN4daal8services10interface16String19__DAAL_STR_MAX_SIZEE"
SONAME = "libonedal_core.so.3"


def run(prefix, out):
    out.mkdir(parents=True, exist_ok=True)
    real = prefix / "lib"
    names = {
        variant: OWNER + variant + "Ev" for variant in ("C1", "C2", "D0", "D1", "D2")
    }
    names["data"] = VARIABLE
    clients = {}
    # A relocation forces the loader to resolve the symbol without invoking
    # a C++ constructor on an object whose lifetime has not begun.
    for tag, symbol in names.items():
        source = out / f"{tag}.c"
        source.write_text(
            f'extern char target[] asm("{symbol}"); void *volatile ref = target; int main(void) {{return ref == 0;}}\n'
        )
        client = out / tag
        subprocess.run(
            [
                "gcc",
                "-fPIC",
                "-pie",
                source,
                real / SONAME,
                f"-Wl,-rpath-link,{real}",
                "-Wl,--allow-shlib-undefined",
                "-o",
                client,
            ],
            check=True,
        )
        clients[tag] = client
    receipt = {}
    for absent in ("control", *names):
        d = out / f"image-{absent}"
        d.mkdir(exist_ok=True)
        asm = d / "provider.S"
        lines = []
        for tag, symbol in names.items():
            if tag == absent:
                continue
            data = tag == "data"
            lines.extend(
                [
                    ".data" if data else ".text",
                    f".globl {symbol}",
                    f".type {symbol}, @" + ("object" if data else "function"),
                    f"{symbol}:",
                    ".long 4096" if data else "ret",
                    f".size {symbol}, .-{symbol}",
                ]
            )
        asm.write_text("\n".join([*lines, '.section .note.GNU-stack,"",@progbits', ""]))
        subprocess.run(
            [
                "gcc",
                "-shared",
                "-nostdlib",
                "-s",
                f"-Wl,-soname,{SONAME}",
                asm,
                "-o",
                d / SONAME,
            ],
            check=True,
        )
        outcomes = {}
        for tag, client in clients.items():
            proc = subprocess.run(
                [client],
                env={
                    **os.environ,
                    "LC_ALL": "C",
                    "LD_LIBRARY_PATH": str(d),
                    "LD_BIND_NOW": "1",
                },
                capture_output=True,
                text=True,
            )
            expected = 127 if tag == absent else 0
            if proc.returncode != expected or (
                expected and names[tag] not in proc.stderr
            ):
                raise ValueError(f"{absent}/{tag}: {proc.returncode}, {proc.stderr}")
            outcomes[tag] = proc.returncode
        report = d / "compare.json"
        cli = subprocess.run(
            [
                sys.executable,
                "-m",
                "abicheck",
                "compare",
                out / "image-control" / SONAME,
                d / SONAME,
                "-o",
                f"json={report}",
            ]
        )
        result = json.loads(report.read_text())
        losses = {
            c["symbol"]
            for c in result["changes"]
            if c["kind"]
            in (
                "func_removed",
                "func_removed_elf_only",
                "var_removed",
                "var_removed_elf_only",
            )
        }
        if losses != (
            {names[absent]} if absent != "control" else set()
        ) or cli.returncode != (4 if losses else 0):
            raise ValueError(
                f"{absent}: unexpected loss set {losses} or exit {cli.returncode}"
            )
        receipt[absent] = {
            "exits": outcomes,
            "checker": result["verdict"],
            "lost_exports": sorted(losses),
        }
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefix", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    run(args.prefix, args.out)
