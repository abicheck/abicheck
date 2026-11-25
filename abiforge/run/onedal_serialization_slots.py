# SPDX-License-Identifier: Apache-2.0
"""Compile a frozen secondary-base caller against the installed SerializationIface."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "fixtures/onedal/serialization_slots.cpp"

HEADER = Path("data_management/data/data_serialize.h")
SER = "virtual services::Status serializeImpl(interface1::InputDataArchive * archive) = 0;"
DES = "virtual services::Status deserializeImpl(const interface1::OutputDataArchive * archive) = 0;"


def run(prefix, out):
    original = (prefix / "include/dal" / HEADER).read_text()
    if original.count(SER) != 1 or original.count(DES) != 1:
        raise ValueError("ambiguous SerializationIface declaration")
    variants = {
        "old": original,
        "control": original + "\n",
        "swap": original.replace(SER, "PLACEHOLDER")
        .replace(DES, SER)
        .replace("PLACEHOLDER", DES),
        "remove": original.replace(SER, ""),
    }
    src = SOURCE
    lib = prefix / "lib"
    flags = ["-std=c++17", f"-I{prefix}/include/dal", f"-I{prefix}/include"]
    env = {**os.environ, "ABICHECK_AST_FRONTEND": "clang"}
    cli = [sys.executable, "-m", "abicheck"]
    receipt = {}
    for variant, header in variants.items():
        d = out / variant
        api = d / HEADER
        api.parent.mkdir(parents=True, exist_ok=True)
        api.write_text(header)
        subprocess.run(
            [
                "g++",
                "-shared",
                "-fPIC",
                "-s",
                "-DPROVIDER",
                *(["-DOMIT_SERIALIZE"] if variant == "remove" else []),
                "-I",
                d,
                *flags,
                src,
                lib / "libonedal_core.so.3",
                "-o",
                d / "libslots.so",
            ],
            check=True,
        )
        if variant == "old":
            subprocess.run(
                [
                    "g++",
                    *flags,
                    src,
                    "-L",
                    d,
                    "-lslots",
                    lib / "libonedal_core.so.3",
                    lib / "libonedal_thread.so.3",
                    f"-Wl,-rpath-link,{lib}",
                    "-o",
                    out / "client",
                ],
                check=True,
            )
        exits = [
            subprocess.run(
                [out / "client", *args], env={**env, "LD_LIBRARY_PATH": f"{d}:{lib}"}
            ).returncode
            for args in ([[]] if variant == "remove" else [[], ["deserialize"]])
        ]
        if (
            exits
            != {"old": [21, 22], "control": [21, 22], "swap": [22, 21], "remove": [22]}[
                variant
            ]
        ):
            raise ValueError(f"{variant}: unexpected dispatch {exits}")
        subprocess.run(
            [
                *cli,
                "dump",
                d / "libslots.so",
                "-H",
                api,
                "-I",
                d,
                "-I",
                prefix / "include/dal",
                "-I",
                prefix / "include",
                "-o",
                d / "snapshot.json.gz",
            ],
            env=env,
            check=True,
        )
        receipt[variant] = {"method_exits": exits}
        if variant != "old":
            report = d / "report.json"
            p = subprocess.run(
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
            kinds = sorted(
                {
                    c["kind"]
                    for c in data["changes"]
                    if "SerializationIface"
                    in str((c["symbol"], c.get("description", "")))
                }
            )
            receipt[variant].update(verdict=data["verdict"], kinds=kinds)
            if p.returncode != (0 if variant == "control" else 4) or (
                variant != "control" and "type_vtable_changed" not in kinds
            ):
                raise ValueError(
                    f"{variant}: unexpected checker result {receipt[variant]}"
                )
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prefix", type=Path)
    parser.add_argument("out", type=Path)
    args = parser.parse_args()
    run(args.prefix, args.out)
