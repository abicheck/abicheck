# SPDX-License-Identifier: Apache-2.0
"""Check live address ownership of unversioned oneDAL exports on Linux."""

import argparse
import json
import os
import subprocess
import tempfile
from pathlib import Path

from .onedal_export_surface import surface, validate_release_labels


def audit(prefix, runtime, lookup):
    lib = prefix.resolve() / "lib"
    result = {}
    for provider, names in surface(prefix).items():
        if any("@" in name for name in names):
            raise ValueError(f"versioned lookup requires dlvsym: {provider}")
        major = provider.rsplit(".so.", 1)[1]
        parameters = "parameters_dpc" if "_dpc." in provider else "parameters"
        group = [
            lib / f"libonedal_{part}.so.{major}"
            for part in ("thread", "core", parameters)
        ]
        env = {
            **os.environ,
            "LD_PRELOAD": ":".join(map(str, group)),
            "LD_LIBRARY_PATH": os.pathsep.join([str(lib), str(runtime.resolve())]),
        }
        subprocess.run(
            [lookup, lib / provider],
            env=env,
            input="".join(name + "\n" for name in sorted(names)),
            text=True,
            check=True,
        )
        result[provider] = len(names)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--release",
        nargs=2,
        type=Path,
        metavar=("PREFIX", "RUNTIME_LIB"),
        required=True,
        action="append",
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    validate_release_labels(p for p, _ in args.release)
    with tempfile.TemporaryDirectory() as tmp:
        lookup = Path(tmp) / "lookup"
        source = (
            Path(__file__).resolve().parents[1] / "fixtures/onedal/export_lookup.cpp"
        )
        subprocess.run(["g++", "-std=c++17", source, "-ldl", "-o", lookup], check=True)
        result = {p.name: audit(p, runtime, lookup) for p, runtime in args.release}
    args.out.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
