# SPDX-License-Identifier: Apache-2.0
"""Execute frozen legacy collection, destructor and exported-data clients across releases."""

import argparse
import json
import os
import subprocess
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1] / "fixtures/onedal/release_client.cpp"


def run(old, minor, major, out):
    out.mkdir(parents=True, exist_ok=True)
    client = out / "release_client"
    lib = old / "lib"
    subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-fPIC",
            "-pie",
            f"-I{old}/include",
            f"-I{old}/include/dal",
            SOURCE,
            lib / "libonedal_core.so.3",
            lib / "libonedal_thread.so.3",
            f"-Wl,-rpath-link,{lib}",
            "-o",
            client,
        ],
        check=True,
    )
    adapter = out / "ownership.so"
    subprocess.run(
        [
            "g++",
            "-std=c++17",
            "-shared",
            "-fPIC",
            "-s",
            f"-I{old}/include",
            f"-I{old}/include/dal",
            SOURCE.with_name("collection_ownership.cpp"),
            "-ldl",
            "-o",
            adapter,
        ],
        check=True,
    )
    result = {}
    for prefix in (old, minor, major):
        env = {
            **os.environ,
            "LD_LIBRARY_PATH": str(prefix / "lib"),
            "LD_BIND_NOW": "1",
            "LC_ALL": "C",
        }
        env.pop("LD_PRELOAD", None)
        trace = subprocess.run(
            ["ldd", client], env=env, text=True, capture_output=True, check=True
        )
        providers = {}
        for line in trace.stdout.splitlines():
            soname, sep, location = line.strip().partition(" => ")
            if sep and soname.startswith("libonedal"):
                name = location.rsplit(" (", 1)[0]
                if name != "not found" and not Path(name).resolve().is_relative_to(
                    (prefix / "lib").resolve()
                ):
                    raise ValueError(f"foreign provider {name}")
                providers[soname] = name if name == "not found" else Path(name).name
        if "libonedal_core.so.3" not in providers:
            raise ValueError("missing frozen core library dependency")
        cases = {}
        for mode in ("collection", "key_value", "delete_base", "variable"):
            p = subprocess.run([client, mode], env=env, text=True, capture_output=True)
            if prefix == major:
                if (
                    p.returncode != 127
                    or "libonedal" not in p.stderr
                    or "not found" not in providers.values()
                ):
                    raise ValueError(
                        f"expected missing old SONAME in {prefix}: {p.stderr}"
                    )
            elif p.returncode or (mode == "variable" and int(p.stdout) <= 0):
                raise ValueError(f"{prefix}/{mode}: exit {p.returncode}: {p.stderr}")
            cases[mode] = {"exit": p.returncode, "stdout": p.stdout.strip()}
        if prefix != major:
            for mode, premature, retained in (
                ("collection", 1, 2),
                ("key_value", 4, 5),
            ):
                for mutation, expected in (
                    ("forward", 0),
                    ("borrow", premature),
                    ("extra_owner", retained),
                ):
                    rc = subprocess.run(
                        [client, mode],
                        env={
                            **env,
                            "LD_PRELOAD": str(adapter.resolve()),
                            "ONEDAL_OWNERSHIP": mutation,
                        },
                    ).returncode
                    if rc != expected:
                        raise ValueError(
                            f"{prefix}/{mode}/{mutation}: expected {expected}, got {rc}"
                        )
                    cases[mode + "_" + mutation] = {"exit": rc}
        result[prefix.name] = {"providers": providers, "cases": cases}
    if result[old.name]["cases"] != result[minor.name]["cases"]:
        raise ValueError("minor release changed frozen client observations")
    (out / "receipt.json").write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ("old", "minor", "major", "out"):
        parser.add_argument(arg, type=Path)
    args = parser.parse_args()
    run(args.old, args.minor, args.major, args.out)
